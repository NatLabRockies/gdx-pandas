"""gdxcc implementation of :class:`gdxpds._backend.GdxBackend`.

Holds the gdxcc-specific I/O logic extracted from :mod:`gdxpds.gdx`, so that
``gdx.py`` is left as a backend-agnostic interface + data model. Built up
incrementally (Phase 0 of the gams.transfer work); currently implements the
record-read primitive :meth:`GdxccBackend.load_symbols`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import gdxpds.special as special
from gdxpds._backend import GdxBackend
from gdxpds.gdx import (
    GamsDataType,
    GamsEquationType,
    GamsVariableType,
    GdxError,
    GdxSymbol,
)
from gdxpds.tools import Error

# gdxcc bindings: modern (shipped inside gamsapi) is preferred; the standalone
# legacy PyPI package is the fallback.
try:
    from gams.core import gdx as gdxcc
except ImportError:
    import gdxcc

if TYPE_CHECKING:
    import os

    from gdxpds.gdx import GdxFile

logger = logging.getLogger(__name__)


class GdxccBackend(GdxBackend):
    """Reads/writes GDX via the SWIG-bound ``gdxcc`` calls.

    The GDX handle is currently still owned by the :class:`~gdxpds.gdx.GdxFile`
    (the backend reads it off ``gdx_file.H`` at call time); handle ownership
    moves here in a later Phase 0 step.
    """

    def __init__(self, gams_dir: str | None = None, gams_dir_source: str | None = None) -> None:
        self.gams_dir = gams_dir
        self.gams_dir_source = gams_dir_source

    def open_read(self, gdx_file: GdxFile, filename: str | os.PathLike[str]) -> None:
        H = gdx_file.H
        rc = gdxcc.gdxOpenRead(H, str(filename))
        if not rc[0]:
            raise GdxError(H, f"Could not open {filename!r}")
        gdx_file._filename = filename

        # file-level meta-data
        ret, gdx_file._version, gdx_file._producer = gdxcc.gdxFileVersion(H)
        if ret != 1:
            raise GdxError(H, "Could not get file version")
        ret, symbol_count, element_count = gdxcc.gdxSystemInfo(H)
        logger.debug(
            f"Opening '{filename}' with {symbol_count} symbols and "
            f"{element_count} elements with lazy_load = {gdx_file.lazy_load}."
        )

        # the universal set (index 0)
        ret, name, dims, data_type = gdxcc.gdxSymbolInfo(H, 0)
        if ret != 1:
            raise GdxError(H, "Could not get symbol info for the universal set")
        gdx_file.universal_set = self._make_symbol(gdx_file, name, data_type, dims, 0)

        # the symbols (indices 1..symbol_count)
        for i in range(symbol_count):
            index = i + 1
            ret, name, dims, data_type = gdxcc.gdxSymbolInfo(H, index)
            if ret != 1:
                raise GdxError(H, f"Could not get symbol info for symbol {index}")
            try:
                gdx_file.append(self._make_symbol(gdx_file, name, data_type, dims, index))
            except Exception as e:
                logger.error(f"Unable to initialize GdxSymbol {name!r}, because {e}. SKIPPING.")

        # Self-heal strict-domain refs whose parent appeared at a higher GDX
        # index than the child (malformed but readable). No-op for well-formed
        # files -- each symbol's in-line attempt already succeeded.
        for symbol in gdx_file:
            symbol.resolve_domain()

    def _make_symbol(
        self, gdx_file: GdxFile, name: str, data_type, dims, index: int
    ) -> GdxSymbol:
        """Construct a GdxSymbol and populate its extended gdxcc metadata.

        Mirrors the metadata read that used to live in ``GdxSymbol.__init__``;
        keeping it here leaves the constructor backend-agnostic.
        """
        H = gdx_file.H
        symbol = GdxSymbol(name, data_type, dims=dims, file=gdx_file, index=index)
        ret, records, userinfo, description = gdxcc.gdxSymbolInfoX(H, index)
        if ret != 1:
            raise GdxError(H, f"Unable to get extended symbol information for {name}")
        symbol._num_records = records
        if symbol.data_type == GamsDataType.Variable:
            symbol.variable_type = GamsVariableType(userinfo)
        elif symbol.data_type == GamsDataType.Equation:
            symbol.equation_type = GamsEquationType(userinfo)
        symbol.description = description
        if index > 0:
            ret, gdx_domain = gdxcc.gdxSymbolGetDomainX(H, index)
            if ret == 0:
                raise GdxError(H, f"Unable to get domain information for {name}")
            assert len(gdx_domain) == len(symbol.dims), (
                "Dimensional information read in from GDX should be consistent."
            )
            symbol.dims = gdx_domain
            if ret == 3:
                # Stored via strict gdxSymbolSetDomain. Mark so a later retry can
                # distinguish strict-but-unresolved from truly relaxed, then try
                # to resolve names to same-file GdxSymbol refs. Lower-index
                # parents are already appended; open_read retries at the end to
                # self-heal forward references (malformed files).
                symbol._strict_on_disk = True
                symbol.resolve_domain()
        else:
            # universal set
            assert index == 0
            symbol._loaded = True
        return symbol

    def load_symbols(
        self,
        gdx_file: GdxFile,
        symbols: Sequence[GdxSymbol] | None = None,
        *,
        load_set_text: bool = False,
    ) -> None:
        if symbols is None:
            symbols = list(gdx_file)
        for symbol in symbols:
            if symbol.loaded:
                continue
            if not symbol.index:
                raise Error(f"Cannot load {symbol!r} because there is no symbol index")
            self._load_one(gdx_file, symbol, load_set_text=load_set_text)

    def _load_one(self, gdx_file: GdxFile, symbol: GdxSymbol, *, load_set_text: bool) -> None:
        H = gdx_file.H
        _ret, records = gdxcc.gdxDataReadStrStart(H, symbol.index)

        def reader():
            for _i in range(records):
                yield gdxcc.gdxDataReadStr(H)

        vc = symbol.value_cols  # local for speed in the comprehensions below
        if load_set_text and (symbol.data_type == GamsDataType.Set):
            data = [
                elements
                + [gdxcc.gdxGetElemText(H, int(values[col_ind]))[1] for _col_name, col_ind in vc]
                for _ret, elements, values, _afdim in reader()
            ]
            # Element text loaded in place of membership booleans: tell the
            # dataframe setter not to coerce the value column back to c_bool.
            symbol._fixup_set_vals = False
        else:
            data = [
                elements + [values[col_ind] for _col_name, col_ind in vc]
                for _ret, elements, values, _afdim in reader()
            ]
        symbol.dataframe = data
        if symbol.data_type not in (GamsDataType.Set, GamsDataType.Alias):
            symbol.dataframe = special.convert_gdx_to_np_svs(symbol.dataframe, symbol.num_dims)
        symbol._loaded = True
