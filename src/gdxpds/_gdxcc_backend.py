"""gdxcc implementation of :class:`gdxpds._backend.GdxBackend`.

Holds the gdxcc-specific I/O logic, implementing the backend of the
:mod:`gdxpds.gdx` interface + data model as mediated by :mod:`gdxpds._backend`.
Implements the full backend contract: metadata read
(:meth:`GdxccBackend.open_read`), record read (:meth:`GdxccBackend.load_symbols`),
write (:meth:`GdxccBackend.write_file` and the per-symbol
:meth:`GdxccBackend.write_symbol`), plus ownership and teardown of the GDX handle.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from numbers import Number
from typing import TYPE_CHECKING

import gdxpds.special as special
from gdxpds._backend import GdxBackend
from gdxpds.gdx import (
    DomainError,
    GamsDataType,
    GamsEquationType,
    GamsVariableType,
    GdxError,
    GdxSymbol,
)
from gdxpds.tools import Error, _GdxHandle, load_gdxcc

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

    Owns the GDX handle: a :class:`~gdxpds.tools._GdxHandle` created in
    :meth:`__init__` and freed in :meth:`close`. The handle is exposed via
    :attr:`handle` (reached as ``gdx_file._backend_impl.handle``), and
    :class:`~gdxpds.gdx.GdxFile` schedules :meth:`close` via ``weakref.finalize``.
    """

    def __init__(self, gams_dir: str | None = None, gams_dir_source: str | None = None) -> None:
        self.gams_dir = gams_dir
        self.gams_dir_source = gams_dir_source
        # Idempotent: first call binds the library + populates SPECIAL_VALUES;
        # subsequent calls validate gams_dir and warn on mismatch.
        load_gdxcc(gams_dir=gams_dir)
        # _GdxHandle validates the create (raising GamsLoadError on failure, and
        # deleting the wrapper without an unsafe gdxFree). This backend owns the
        # handle; GdxFile's weakref.finalize calls close() to free+delete it.
        self._handle = _GdxHandle(gdxcc, gams_dir, gams_dir_source)

    @property
    def handle(self) -> object | None:
        """The SWIG-bound GDX handle pointer, or None once closed."""
        return self._handle.H if self._handle is not None else None

    def close(self) -> None:
        # Run-once: drop the reference first, then free+delete via _GdxHandle
        # (itself idempotent). After this, handle returns None.
        h = self._handle
        if h is not None:
            self._handle = None
            h.close()

    def open_read(self, gdx_file: GdxFile, filename: str | os.PathLike[str]) -> None:
        H = self.handle
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

        # Self-heal strict-domain refs and alias parents whose target appeared at
        # a higher GDX index than the dependent symbol (malformed but readable).
        # No-op for well-formed files -- each in-line attempt already succeeded.
        for symbol in gdx_file:
            symbol.resolve_domain()
            symbol.resolve_aliased_with()

    def _make_symbol(self, gdx_file: GdxFile, name: str, data_type, dims, index: int) -> GdxSymbol:
        """Construct a GdxSymbol and populate its extended gdxcc metadata."""
        H = self.handle
        symbol = GdxSymbol(name, data_type, dims=dims, file=gdx_file, index=index)
        ret, records, userinfo, description = gdxcc.gdxSymbolInfoX(H, index)
        if ret != 1:
            raise GdxError(H, f"Unable to get extended symbol information for {name}")
        symbol._num_records = records
        if symbol.data_type == GamsDataType.Variable:
            symbol.variable_type = GamsVariableType(userinfo)
        elif symbol.data_type == GamsDataType.Equation:
            symbol.equation_type = GamsEquationType(userinfo)
        elif symbol.data_type == GamsDataType.Alias:
            # For an alias, gdxSymbolInfoX's userinfo is the GDX index of the
            # aliased Set (0 = the universe set "*"). Record the parent name and
            # resolve it to a same-file ref (open_read self-heals forward refs).
            pret, parent_name, _pdims, _pdtype = gdxcc.gdxSymbolInfo(H, userinfo)
            if pret == 1:
                symbol._aliased_with_name = parent_name
                symbol.resolve_aliased_with()
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
    ) -> None:
        if symbols is None:
            symbols = list(gdx_file)
        for symbol in symbols:
            if symbol.loaded:
                continue
            if not symbol.index:
                raise Error(f"Cannot load {symbol!r} because there is no symbol index")
            self._load_one(gdx_file, symbol)

    def _load_one(self, gdx_file: GdxFile, symbol: GdxSymbol) -> None:
        H = self.handle
        _ret, records = gdxcc.gdxDataReadStrStart(H, symbol.index)

        def reader():
            for _i in range(records):
                yield gdxcc.gdxDataReadStr(H)

        vc = symbol.value_cols  # local for speed in the comprehensions below
        if symbol.data_type in (GamsDataType.Set, GamsDataType.Alias):
            # A Set/Alias value is its element text ("" when none); membership is
            # row presence. An Alias reads like the Set it aliases. The stored
            # value is the index into the element-text table.
            data = [
                elements
                + [gdxcc.gdxGetElemText(H, int(values[col_ind]))[1] for _col_name, col_ind in vc]
                for _ret, elements, values, _afdim in reader()
            ]
            symbol.dataframe = data
        else:
            data = [
                elements + [values[col_ind] for _col_name, col_ind in vc]
                for _ret, elements, values, _afdim in reader()
            ]
            symbol.dataframe = data
            symbol.dataframe = special.convert_gdx_to_np_svs(symbol.dataframe, symbol.num_dims)
        symbol._loaded = True

    def write_file(self, gdx_file: GdxFile, filename: str | os.PathLike[str]) -> None:
        # only write if all symbols loaded
        for symbol in gdx_file:
            if not symbol.loaded:
                raise Error("All symbols must be loaded before this file can be written.")

        H = self.handle
        ret = gdxcc.gdxOpenWrite(H, str(filename), "gdxpds")
        if not ret:
            raise GdxError(
                H,
                f"Could not open {filename!r} for writing. "
                "Consider cloning this file (.clone()) before trying to write.",
            )
        gdx_file._filename = filename

        # write the universal set
        self.write_symbol(gdx_file, gdx_file.universal_set)

        # Build the {name: position} map once so each symbol's strict-domain
        # eligibility check is O(1) per parent rather than O(N).
        name_positions = {name: i for i, name in enumerate(gdx_file._symbols.keys())}

        for i, symbol in enumerate(gdx_file, start=1):
            try:
                self.write_symbol(gdx_file, symbol, index=i, name_positions=name_positions)
            except Exception:
                logger.error(f"Unable to write {symbol} to {filename}")
                raise

        gdxcc.gdxClose(H)

    def write_symbol(
        self,
        gdx_file: GdxFile,
        symbol: GdxSymbol,
        index: int | None = None,
        name_positions: dict | None = None,
    ) -> None:
        if not symbol.loaded:
            raise Error(f"Cannot write unloaded symbol {symbol.name!r}.")
        H = self.handle

        if symbol.data_type in (GamsDataType.Set, GamsDataType.Alias):
            symbol._fixup_set_value()

        if index is not None:
            symbol._index = index

        if symbol.index == 0:
            # universal set
            gdxcc.gdxUELRegisterRawStart(H)
            gdxcc.gdxUELRegisterRaw(H, symbol.name)
            gdxcc.gdxUELRegisterDone(H)
            return

        if symbol.data_type == GamsDataType.Alias:
            # An alias carries no records of its own; it is registered against its
            # parent Set, which must already be written (no relaxed fallback).
            parent = symbol.aliased_with_name
            if parent is None:
                raise DomainError(
                    f"Cannot write alias {symbol.name!r}: no parent Set (aliased_with) is set."
                )
            if not gdxcc.gdxAddAlias(H, parent, symbol.name):
                raise GdxError(
                    H,
                    f"Could not add alias {symbol.name!r} -> {parent!r} "
                    "(is the parent Set written before the alias?)",
                )
            return

        # write the data
        userinfo = 0
        if symbol.variable_type is not None:
            userinfo = symbol.variable_type.value
        elif symbol.equation_type is not None:
            userinfo = symbol.equation_type.value
        if not gdxcc.gdxDataWriteStrStart(
            H, symbol.name, symbol.description, symbol.num_dims, symbol.data_type.value, userinfo
        ):
            raise GdxError(H, f"Could not start writing data for symbol {repr(symbol.name)}")
        # set domain information: prefer strict gdxSymbolSetDomain when every
        # entry of symbol._domain either is None ('*') or refers to a parent
        # already written to this file; otherwise fall back to relaxed
        # gdxSymbolSetDomainX. Decision is per-symbol because GDX itself only
        # supports per-symbol strict/relaxed.
        if symbol.num_dims > 0:
            domain = symbol._domain if symbol._strict_domain_writeable(name_positions) else None
            if domain is not None:
                names = [d.name if d is not None else "*" for d in domain]
                if not gdxcc.gdxSymbolSetDomain(H, names):
                    raise GdxError(
                        H,
                        f"Could not set strict domain information for {repr(symbol.name)}. "
                        f"Domains are {repr(names)}",
                    )
            elif symbol.index:
                if not gdxcc.gdxSymbolSetDomainX(H, symbol.index, symbol.dims):
                    raise GdxError(
                        H,
                        f"Could not set domain information for {repr(symbol.name)}. "
                        f"Domains are {repr(symbol.dims)}",
                    )
            else:
                logger.info("Not writing domain information because symbol index is unknown.")
        values = gdxcc.doubleArray(gdxcc.GMS_VAL_MAX)
        # make sure index is clean -- needed for merging in convert_np_to_gdx_svs
        symbol.dataframe = symbol.dataframe.reset_index(drop=True)

        if symbol.data_type in (GamsDataType.Set, GamsDataType.Alias):
            # Each row is a member; the value column is its element text ("" = no
            # text). Non-empty text is registered with gdxAddSetText and the row
            # stores the returned table index (0 means no text).
            for row in symbol.dataframe.itertuples(index=False, name=None):
                dims = [str(x) for x in row[: symbol.num_dims]]
                vals = row[symbol.num_dims :]
                for _col_name, col_ind in symbol.value_cols:
                    text = vals[col_ind]
                    node = 0
                    if isinstance(text, str) and text != "":
                        rc, node = gdxcc.gdxAddSetText(H, text)
                        if not rc:
                            raise GdxError(
                                H, f"Could not add set text {text!r} for {symbol.name!r}"
                            )
                    values[col_ind] = float(node)
                gdxcc.gdxDataWriteStr(H, dims, values)
        else:
            to_write = special.convert_np_to_gdx_svs(symbol.dataframe, symbol.num_dims)
            undef = special.SPECIAL_VALUES[0]  # GDX UNDEF magic float
            for row in to_write.itertuples(index=False, name=None):
                dims = [str(x) for x in row[: symbol.num_dims]]
                vals = row[symbol.num_dims :]
                for _col_name, col_ind in symbol.value_cols:
                    v = vals[col_ind]
                    try:
                        if v is None:
                            # gdxpds canonical UNDEF -> a genuine GDX UNDEF, which
                            # reads back as None (convert_np_to_gdx_svs can't key None).
                            values[col_ind] = undef
                        elif isinstance(v, Number):
                            values[col_ind] = float(v)
                        else:
                            values[col_ind] = 0.0
                    except Exception:
                        raise Error(f"Unable to set element {col_ind} from {vals}.")
                gdxcc.gdxDataWriteStr(H, dims, values)
        gdxcc.gdxDataWriteDone(H)
