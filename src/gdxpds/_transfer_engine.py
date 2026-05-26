"""gams.transfer implementation of :class:`gdxpds._engine.GdxEngine` (read + write).

Read: ``open_read`` builds the symbol metadata from a ``gams.transfer`` Container
(records-free), and ``load_symbols`` reads records (bulk or targeted) and
translates each symbol into the gdxpds DataFrame shape so the result matches the
gdxcc engine. Write: ``write_file`` builds a Container from the gdxpds symbols
(the inverse translation) and writes it, including Sets, aliases, and element text.

``gams.transfer`` is imported at module load, but this module is itself imported
lazily by :func:`gdxpds._engine.make_engine`, so ``import gdxpds`` stays free
of the gams.transfer import cost unless the engine is actually selected.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import gams.transfer as gt
import numpy as np
import pandas as pd

from gdxpds._engine import GdxEngine
from gdxpds.gdx import (
    DomainError,
    GamsDataType,
    GamsEquationType,
    GamsVariableType,
    GdxSymbol,
    TransferError,
)
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
    # either, so leaving it unmapped keeps the two engines consistent.
}

# Inverse maps for the write path (gdxpds enum -> gams.transfer .type string).
_VAR_TYPE_STR = {member: s for s, member in _VAR_TYPE.items()}
_EQU_TYPE_STR = {member: s for s, member in _EQU_TYPE.items()}


def _np_to_transfer_specials(records: pd.DataFrame, value_cols: list[str]) -> None:
    """In place, map gdxpds canonical special values to gams.transfer encodings.

    Mostly the inverse of :func:`_convert_transfer_specials`: machine eps -> EPS
    (gt's ``-0.0``); NaN -> NA (gt's NA sentinel); +/-inf already match. Genuine
    0.0 is left alone (only eps maps to EPS).

    GDX UNDEF is gdxpds' canonical ``None`` (see
    :data:`special.NUMPY_SPECIAL_VALUES`), only possible in an object column. It
    maps to a genuine ``gt.SpecialValues.UNDEF``, which reads back as ``None`` --
    distinct from NA (``np.nan``), which the plain float64 coercion would otherwise
    produce.
    """
    eps = NUMPY_SPECIAL_VALUES[-1]
    for col in value_cols:
        col_data = records[col]
        # float64 columns can't hold a Python None, so only object columns need
        # the (per-element) None check; skip it otherwise to keep the hot path fast.
        if col_data.dtype == object:
            is_none = col_data.map(lambda v: v is None).to_numpy(dtype=bool)
        else:
            is_none = np.zeros(len(col_data), dtype=bool)
        arr = col_data.to_numpy(dtype="float64", copy=True)
        is_eps = np.abs(arr - eps) < eps
        is_nan = np.isnan(arr) & ~is_none  # genuine NaN (NA), not a coerced None
        arr[is_nan] = gt.SpecialValues.NA
        arr[is_eps] = gt.SpecialValues.EPS
        arr[is_none] = gt.SpecialValues.UNDEF  # GDX UNDEF, reads back as None
        records[col] = arr


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
    raise TransferError(f"Unsupported gams.transfer symbol type {type(gt_sym).__name__!r}.")


def _dims_of(gt_sym) -> list[str]:
    # gt_sym.domain is a list of '*' strings and/or gt.Set references.
    return [d if isinstance(d, str) else d.name for d in gt_sym.domain]


def _convert_transfer_specials(values: pd.DataFrame) -> pd.DataFrame:
    """Map gams.transfer special-value encodings to the gdxpds canonical form.

    Mirrors the gdxcc engine's :func:`special.convert_gdx_to_np_svs` exactly:
    EPS (gt's ``-0.0``) -> machine eps; NA (gt's NA sentinel) -> ``np.nan``;
    UNDEF (gt's plain NaN) -> ``None``; +/-inf already match. Genuine ``0.0`` is
    left alone (only negative zero is EPS).

    UNDEF is kept distinct from NA, matching the gdxcc engine: gdxcc maps GDX
    UNDEF -> ``None`` and GDX NA -> ``np.nan`` (see
    :data:`special.GDX_TO_NP_SVS`). A column carrying any UNDEF therefore comes
    back as object dtype (so ``None`` survives), matching gdxcc; a column with no
    UNDEF stays ``float64``.
    """
    eps = NUMPY_SPECIAL_VALUES[-1]
    out = values.copy()
    for col in out.columns:
        arr = out[col].to_numpy(dtype="float64", copy=True)
        is_eps = np.asarray(gt.SpecialValues.isEps(arr))
        is_na = np.asarray(gt.SpecialValues.isNA(arr))
        is_undef = np.asarray(gt.SpecialValues.isUndef(arr))
        arr[is_na | is_undef] = np.nan
        arr[is_eps] = eps
        if is_undef.any():
            # UNDEF -> None (gdxcc parity), forcing object dtype like gdxcc does.
            obj = arr.astype(object)
            obj[is_undef] = None
            out[col] = obj
        else:
            out[col] = arr
    return out


class TransferEngine(GdxEngine):
    """Reads and writes GDX via ``gams.transfer``, translating to/from the gdxpds shape.

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
        for symbol in gdx_file:
            if not symbol.loaded:
                raise Error("All symbols must be loaded before this file can be written.")

        container = gt.Container(system_directory=self.gams_dir)
        # {name: position} for the per-symbol strict-domain eligibility check,
        # mirroring the gdxcc write path.
        name_positions = {name: i for i, name in enumerate(gdx_file._symbols.keys())}
        for symbol in gdx_file:
            self._add_symbol(container, symbol, name_positions)
        try:
            # eps_to_zero defaults True, which silently drops EPS to 0.0; keep EPS
            # so it round-trips like the gdxcc path.
            container.write(str(filename), eps_to_zero=False)
        except Exception as e:
            raise TransferError(f"gams.transfer failed to write {filename!r}: {e}") from e
        gdx_file._filename = filename

    def _gt_domain(self, container, symbol: GdxSymbol, name_positions: dict):
        """Domain spec for a gt symbol, mirroring the gdxcc strict/relaxed choice.

        Strict (a same-file parent that precedes this symbol) -> the gt.Set refs
        already in the container; otherwise the dim-name strings (relaxed / '*').
        """
        if symbol.num_dims == 0:
            return []
        if symbol._strict_domain_writeable(name_positions):
            return [container.data[d.name] if d is not None else "*" for d in symbol._domain]
        return list(symbol.dims)

    def _add_symbol(self, container, symbol: GdxSymbol, name_positions: dict) -> None:
        data_type = symbol.data_type
        if data_type == GamsDataType.Alias:
            # An alias carries no records of its own; it points at its parent Set,
            # which must already be in the container (no relaxed fallback).
            parent = symbol.aliased_with_name
            if parent is None:
                raise DomainError(
                    f"Cannot write alias {symbol.name!r}: no parent Set (aliased_with) is set."
                )
            universe = (
                symbol.file.universal_set.name
                if symbol.file is not None and symbol.file.universal_set is not None
                else "*"
            )
            if parent == universe:
                gt.UniverseAlias(container, symbol.name)
            elif parent in container.data:
                gt.Alias(container, symbol.name, container.data[parent])
            else:
                raise DomainError(
                    f"Cannot write alias {symbol.name!r} -> {parent!r}: the parent Set is not "
                    "in this file or has not been written before the alias."
                )
            return

        num_dims = symbol.num_dims
        domain = self._gt_domain(container, symbol, name_positions)
        description = symbol.description or ""
        # Domain columns are matched positionally by gams.transfer, so give them
        # unique throwaway names (dodging duplicate '*' labels); value columns
        # are matched by name.
        dim_names = [f"_d{i}" for i in range(num_dims)]

        if data_type == GamsDataType.Set:
            records = symbol.dataframe.iloc[:, :num_dims].copy()
            records.columns = dim_names
            # The value column is the element text ("" = a member with no text).
            records["element_text"] = symbol.dataframe.iloc[:, num_dims].astype(str).to_numpy()
            gt.Set(container, symbol.name, domain=domain, description=description, records=records)
            return

        # Parameter / Variable / Equation. gams.transfer's value-column names are
        # the gdxpds value_col_names lowercased (Value -> value, Level -> level,
        # ...); value_col_names derives from GamsValueType, the same source the
        # gdxcc engine uses, so there is no second hard-coded list to keep in sync.
        value_cols = [name.lower() for name in symbol.value_col_names]
        records = symbol.dataframe.copy()
        records.columns = dim_names + value_cols
        _np_to_transfer_specials(records, value_cols)
        if data_type == GamsDataType.Parameter:
            gt.Parameter(
                container, symbol.name, domain=domain, description=description, records=records
            )
        elif data_type == GamsDataType.Variable:
            vt = symbol.variable_type
            gt.Variable(
                container,
                symbol.name,
                _VAR_TYPE_STR.get(vt, "free") if vt is not None else "free",
                domain=domain,
                description=description,
                records=records,
            )
        else:  # Equation
            et = symbol.equation_type
            gt.Equation(
                container,
                symbol.name,
                _EQU_TYPE_STR.get(et, "eq") if et is not None else "eq",
                domain=domain,
                description=description,
                records=records,
            )

    def open_read(self, gdx_file: GdxFile, filename: str | os.PathLike[str]) -> None:
        # Metadata only: keeps list_symbols / get_data_types cheap. Records are
        # read lazily on load (gams.transfer can't add records to an existing
        # container, so loads use a separate records=True read).
        container = gt.Container(system_directory=self.gams_dir)
        try:
            container.read(str(filename), records=False)
        except Exception as e:
            raise TransferError(f"gams.transfer failed to open {filename!r}: {e}") from e
        gdx_file._filename = filename
        # gams.transfer exposes neither the GDX file version/producer nor a
        # pre-load record count, so those stay at their defaults (None / 0).

        for index, (name, gt_sym) in enumerate(container.data.items(), start=1):
            try:
                gdx_file.append(self._make_symbol(gdx_file, name, gt_sym, index))
            except Exception as e:
                logger.error(f"Unable to initialize GdxSymbol {name!r}, because {e}. SKIPPING.")

        # Self-heal strict-domain refs and alias parents (target appearing after
        # the dependent symbol).
        for symbol in gdx_file:
            symbol.resolve_domain()
            symbol.resolve_aliased_with()

    def _make_symbol(self, gdx_file: GdxFile, name: str, gt_sym, index: int) -> GdxSymbol:
        data_type = _data_type_of(gt_sym)
        dims = _dims_of(gt_sym)
        symbol = GdxSymbol(name, data_type, dims=dims, file=gdx_file, index=index)
        symbol.description = getattr(gt_sym, "description", "") or ""
        if data_type == GamsDataType.Variable:
            symbol.variable_type = _VAR_TYPE.get(gt_sym.type, GamsVariableType.Free)
        elif data_type == GamsDataType.Equation:
            symbol.equation_type = _EQU_TYPE.get(gt_sym.type, GamsEquationType.Equality)
        elif data_type == GamsDataType.Alias:
            # gt.Alias.alias_with is the parent gt.Set; gt.UniverseAlias.alias_with
            # is the string "*". Record the parent name and resolve to a same-file ref.
            parent = getattr(gt_sym, "alias_with", None)
            parent_name = parent if isinstance(parent, str) else getattr(parent, "name", None)
            if parent_name is not None:
                symbol._aliased_with_name = parent_name
                symbol.resolve_aliased_with()
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
    ) -> None:
        if symbols is None:
            # Bulk: the full container is read once and cached.
            targets = [s for s in gdx_file if not s.loaded]
            container = self._records_container(gdx_file) if targets else None
        else:
            # Targeted: read just the requested symbols' records. gams.transfer
            # requires an alias's parent Set to be present in the same read, so
            # pull those in too (the universe parent "*" is implicit, not a symbol).
            targets = [s for s in symbols if not s.loaded]
            read_names = {s.name for s in targets}
            universe = gdx_file.universal_set.name if gdx_file.universal_set is not None else "*"
            for s in targets:
                parent = s.aliased_with_name
                if s.data_type == GamsDataType.Alias and parent and parent != universe:
                    read_names.add(parent)
            container = self._read_records(gdx_file, list(read_names)) if targets else None
        for symbol in targets:
            self._translate(container, symbol)

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
            raise TransferError(
                f"gams.transfer failed to read records from {gdx_file.filename!r}: {e}"
            ) from e

    def _translate(self, container, symbol: GdxSymbol) -> None:
        gt_sym = container.data[symbol.name]
        records = gt_sym.records
        out_cols = symbol.dims + symbol.value_col_names
        if records is None or len(records) == 0:
            symbol.dataframe = pd.DataFrame([], columns=out_cols)
            symbol._loaded = True
            return

        num_dims = symbol.num_dims
        # Domain columns, decategorized to plain strings (gdxcc yields object/str,
        # gams.transfer yields ordered categoricals).
        dim_data = records.iloc[:, :num_dims].astype(str).reset_index(drop=True)

        # A Set/Alias value is its element text ("" = no text); membership is row
        # presence. An Alias delegates its .records to the parent Set, so the same
        # path applies. A universe alias (alias of '*') has only the member column
        # and no element-text column, so its members all carry empty text.
        if symbol.data_type in (GamsDataType.Set, GamsDataType.Alias):
            if records.shape[1] > num_dims:
                text = records.iloc[:, num_dims].astype(str)
            else:
                text = pd.Series([""] * len(records))
            value_data = text.reset_index(drop=True).to_frame()
        else:
            value_data = _convert_transfer_specials(
                records.iloc[:, num_dims:].reset_index(drop=True)
            )

        df = pd.concat([dim_data, value_data], axis=1)
        df.columns = out_cols
        symbol.dataframe = df
        symbol._loaded = True
