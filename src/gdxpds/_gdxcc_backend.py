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
from gdxpds.gdx import GamsDataType
from gdxpds.tools import Error

# gdxcc bindings: modern (shipped inside gamsapi) is preferred; the standalone
# legacy PyPI package is the fallback.
try:
    from gams.core import gdx as gdxcc
except ImportError:
    import gdxcc

if TYPE_CHECKING:
    from gdxpds.gdx import GdxFile, GdxSymbol

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
