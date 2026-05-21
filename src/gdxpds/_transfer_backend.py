"""gams.transfer implementation of :class:`gdxpds._backend.GdxBackend` (read).

Phase A: the read fast path. ``open_read`` builds the symbol metadata from a
``gams.transfer`` Container (records-free), and ``load_symbols`` reads records
(bulk or targeted) and translates each symbol into the gdxpds DataFrame shape so
the result matches the gdxcc backend. ``write_file`` is not implemented yet
(Phase B) and inherits the ABC default that raises.

``gams.transfer`` is imported at module load, but this module is itself imported
lazily by :func:`gdxpds._backend.make_backend`, so ``import gdxpds`` stays free
of the gams.transfer import cost unless the backend is actually selected.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import gams.transfer as gt
import numpy as np
import pandas as pd

from gdxpds._backend import GdxBackend
from gdxpds.gdx import GamsDataType, GamsEquationType, GamsVariableType, GdxSymbol
from gdxpds.special import NUMPY_SPECIAL_VALUES
from gdxpds.tools import Error

if TYPE_CHECKING:
    import os

    from gdxpds.gdx import GdxFile

logger = logging.getLogger(__name__)

# gams.transfer .type strings -> gdxpds enum members. gt's subtype integers
# match GamsVariableType's values and (offset by GamsEquationType's +53) the
# equation enum, but mapping by the canonical strings is clearer and stable.
_VAR_TYPE = {
    "binary": GamsVariableType.Binary,
    "integer": GamsVariableType.Integer,
    "positive": GamsVariableType.Positive,
    "negative": GamsVariableType.Negative,
    "free": GamsVariableType.Free,
    "sos1": GamsVariableType.SOS1,
    "sos2": GamsVariableType.SOS2,
    "semicont": GamsVariableType.Semicont,
    "semiint": GamsVariableType.Semiint,
}
_EQU_TYPE = {
    "eq": GamsEquationType.Equality,
    "geq": GamsEquationType.GreaterThan,
    "leq": GamsEquationType.LessThan,
    "nonbinding": GamsEquationType.NothingEnforced,
    "external": GamsEquationType.External,
    # gt's 'boolean' (subtype 6) has no GamsEquationType; gdxcc can't model it
    # either, so leaving it unmapped keeps the two backends consistent.
}


def _data_type_of(gt_sym) -> GamsDataType:
    # UniverseAlias / Alias before Set (an alias is not a Set, but check the
    # narrower types first to be safe).
    if isinstance(gt_sym, (gt.Alias, gt.UniverseAlias)):
        return GamsDataType.Alias
    if isinstance(gt_sym, gt.Set):
        return GamsDataType.Set
    if isinstance(gt_sym, gt.Parameter):
        return GamsDataType.Parameter
    if isinstance(gt_sym, gt.Variable):
        return GamsDataType.Variable
    if isinstance(gt_sym, gt.Equation):
        return GamsDataType.Equation
    raise Error(f"Unsupported gams.transfer symbol type {type(gt_sym).__name__!r}.")


def _dims_of(gt_sym) -> list[str]:
    # gt_sym.domain is a list of '*' strings and/or gt.Set references.
    return [d if isinstance(d, str) else d.name for d in gt_sym.domain]


def _convert_transfer_specials(values: pd.DataFrame) -> pd.DataFrame:
    """Map gams.transfer special-value encodings to the gdxpds canonical form.

    EPS (gt's ``-0.0``) -> machine eps; NA and UNDEF (gt's special NaNs) ->
    plain ``np.nan``; +/-inf already match. Genuine ``0.0`` is left alone (only
    negative zero is EPS).
    """
    eps = NUMPY_SPECIAL_VALUES[-1]
    out = values.copy()
    for col in out.columns:
        arr = np.asarray(out[col].to_numpy(dtype="float64"))
        is_eps = np.asarray(gt.SpecialValues.isEps(arr))
        is_nan = np.asarray(gt.SpecialValues.isNA(arr)) | np.asarray(
            gt.SpecialValues.isUndef(arr)
        )
        arr = arr.copy()
        arr[is_nan] = np.nan
        arr[is_eps] = eps
        out[col] = arr
    return out


class TransferBackend(GdxBackend):
    """Reads GDX via ``gams.transfer`` and translates to the gdxpds shape.

    Holds no native handle (``handle`` stays ``None``); state is the cached
    Container, dropped in :meth:`close`.
    """

    def __init__(self, gams_dir: str | None = None, gams_dir_source: str | None = None) -> None:
        self.gams_dir = gams_dir
        self.gams_dir_source = gams_dir_source
        # Invariant: this holds only a *full* container (every symbol's records
        # read) or None -- read lazily on the first bulk load and reused.
        # Targeted (subset) reads use transient fresh containers and never
        # populate this, so returning it without re-reading is always safe.
        self._container = None

    def close(self) -> None:
        self._container = None

    def write_file(self, gdx_file: GdxFile, filename: str | os.PathLike[str]) -> None:
        raise NotImplementedError(
            "Writing via the gams_transfer backend is not yet implemented "
            "(planned for v2.1.0 Phase B); use backend='gdxcc' to write."
        )

    def open_read(self, gdx_file: GdxFile, filename: str | os.PathLike[str]) -> None:
        # Metadata only: keeps list_symbols / get_data_types cheap. Records are
        # read lazily on load (gams.transfer can't add records to an existing
        # container, so loads use a separate records=True read).
        container = gt.Container(system_directory=self.gams_dir)
        container.read(str(filename), records=False)
        gdx_file._filename = filename
        # gams.transfer exposes neither the GDX file version/producer nor a
        # pre-load record count, so those stay at their defaults (None / 0).

        for index, (name, gt_sym) in enumerate(container.data.items(), start=1):
            try:
                gdx_file.append(self._make_symbol(gdx_file, name, gt_sym, index))
            except Exception as e:
                logger.error(f"Unable to initialize GdxSymbol {name!r}, because {e}. SKIPPING.")

        # Self-heal strict-domain refs (parent appearing after the child).
        for symbol in gdx_file:
            symbol.resolve_domain()

    def _make_symbol(self, gdx_file: GdxFile, name: str, gt_sym, index: int) -> GdxSymbol:
        data_type = _data_type_of(gt_sym)
        dims = _dims_of(gt_sym)
        symbol = GdxSymbol(name, data_type, dims=dims, file=gdx_file, index=index)
        symbol.description = getattr(gt_sym, "description", "") or ""
        if data_type == GamsDataType.Variable:
            symbol.variable_type = _VAR_TYPE.get(gt_sym.type, GamsVariableType.Free)
        elif data_type == GamsDataType.Equation:
            symbol.equation_type = _EQU_TYPE.get(gt_sym.type, GamsEquationType.Equality)
        # A non-wildcard domain entry (a Set reference) means a strict/regular
        # domain; mark it and resolve names to same-file GdxSymbol refs.
        if any(not isinstance(d, str) for d in gt_sym.domain):
            symbol._strict_on_disk = True
            symbol.resolve_domain()
        return symbol

    def load_symbols(
        self,
        gdx_file: GdxFile,
        symbols: Sequence[GdxSymbol] | None = None,
        *,
        load_set_text: bool = False,
    ) -> None:
        if symbols is None:
            # Bulk: the full container is read once and cached.
            targets = [s for s in gdx_file if not s.loaded]
            container = self._records_container(gdx_file) if targets else None
        else:
            # Targeted: read just the requested symbols' records.
            targets = [s for s in symbols if not s.loaded]
            container = (
                self._read_records(gdx_file, [s.name for s in targets]) if targets else None
            )
        for symbol in targets:
            self._translate(container, symbol, load_set_text=load_set_text)

    def _records_container(self, gdx_file: GdxFile):
        if self._container is None:
            self._container = self._read_records(gdx_file)
        return self._container

    def _read_records(self, gdx_file: GdxFile, names: list[str] | None = None):
        # A fresh Container per records read: gams.transfer reads create symbols,
        # so records can't be added to the metadata-only container from open_read.
        try:
            container = gt.Container(system_directory=self.gams_dir)
            container.read(str(gdx_file.filename), records=True, symbols=names)
            return container
        except Exception as e:
            # On failure the targets stay unloaded, so a retry re-reads cleanly.
            raise Error(f"gams.transfer failed to read records from {gdx_file.filename!r}: {e}")

    def _translate(self, container, symbol: GdxSymbol, *, load_set_text: bool) -> None:
        gt_sym = container.data[symbol.name]
        records = gt_sym.records
        out_cols = symbol.dims + symbol.value_col_names
        if records is None or len(records) == 0:
            symbol.dataframe = pd.DataFrame([], columns=out_cols)
            symbol._loaded = True
            return

        if symbol.data_type == GamsDataType.Alias:
            # Aliases delegate .records to their parent Set; translating them to
            # the gdxpds shape needs a fixture to validate against (Phase A.4).
            raise NotImplementedError(
                "Alias read via the gams_transfer backend is not yet implemented."
            )

        num_dims = symbol.num_dims
        # Domain columns, decategorized to plain strings (gdxcc yields object/str,
        # gams.transfer yields ordered categoricals).
        dim_data = records.iloc[:, :num_dims].astype(str).reset_index(drop=True)

        if symbol.data_type == GamsDataType.Set:
            text = records.iloc[:, num_dims].astype(str).reset_index(drop=True)
            if load_set_text:
                value_data = text.to_frame()
                symbol._fixup_set_vals = False
            else:
                # Membership truthiness: element text present iff a non-zero value
                # was stored, i.e. c_bool(True); empty text -> c_bool(False).
                value_data = (text != "").to_frame()
        else:
            value_data = _convert_transfer_specials(
                records.iloc[:, num_dims:].reset_index(drop=True)
            )

        df = pd.concat([dim_data, value_data], axis=1)
        df.columns = out_cols
        symbol.dataframe = df
        symbol._loaded = True
