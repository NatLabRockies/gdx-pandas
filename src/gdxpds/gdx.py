"""
Backend functionality for reading and writing GDX files.
The GdxFile and GdxSymbol classes are full-featured interfaces
for going between the GDX format and pandas DataFrames,
including translation between GDX and numpy special values.
"""

from __future__ import annotations

import copy
import logging
import os
import weakref
from collections import OrderedDict, defaultdict
from collections.abc import MutableSequence, Sequence
from ctypes import c_bool
from enum import Enum
from numbers import Number

import numpy as np
import pandas as pd

import gdxpds.special as special

# Re-exported from gdxpds.special for backward compatibility.
from gdxpds.special import (
    NUMPY_SPECIAL_VALUES,  # noqa: F401
    convert_gdx_to_np_svs,  # noqa: F401
    convert_np_to_gdx_svs,  # noqa: F401
    gdx_isnan,  # noqa: F401
    gdx_val_equal,  # noqa: F401
    is_np_eps,  # noqa: F401
    is_np_sv,  # noqa: F401
)
from gdxpds.tools import Error, NeedsGamsDir, _GdxHandle, load_gdxcc

# gdxcc bindings: modern (shipped inside gamsapi) is preferred; the standalone
# legacy PyPI package is the fallback.
try:
    from gams.core import gdx as gdxcc
except ImportError:
    import gdxcc

# Optional Windows-only fast path for loading GAMS parameters.
try:
    import gdx2py

    HAVE_GDX2PY = True
except ImportError:
    HAVE_GDX2PY = False

logger = logging.getLogger(__name__)


def _stable_topological_sort(names, parents_of):
    """
    Stable topological sort.

    Parameters
    ----------
    names : sequence of str
        The items to sort, in their original order. Original position is
        used to break ties between ready items.
    parents_of : dict of str to iterable of str
        For each name, the names it depends on (must precede it).
        Self-references and parent names not in ``names`` are ignored.

    Returns
    -------
    (list of str or None, dict of str to set of str, or None)
        ``(ordered, cycle)``.

        - ``cycle`` is a ``{name: set_of_unresolved_parents}`` dict
          describing the names that couldn't be ordered when the input
          contains a cycle, else ``None``. Iterating it (e.g.
          ``sorted(cycle)``) yields the involved names.
        - ``ordered`` is the topologically sorted sequence when a reorder
          was actually needed. It is ``None`` when the input was already
          in dependency order (no reorder needed) or when a cycle was
          detected (callers should consult ``cycle`` and react).
    """
    name_to_pos = {n: i for i, n in enumerate(names)}
    remaining = {
        n: {p for p in parents_of.get(n, ()) if p in name_to_pos and p != n} for n in names
    }
    ordered = []
    while remaining:
        ready = sorted(
            (n for n in remaining if not remaining[n]),
            key=lambda n: name_to_pos[n],
        )
        if not ready:
            return None, remaining
        for name in ready:
            ordered.append(name)
            del remaining[name]
            for deps in remaining.values():
                deps.discard(name)
    if ordered == list(names):
        return None, None
    return ordered, None


def replace_df_column(df: pd.DataFrame, colname: str, new_col) -> None:
    """
    Utility function that replaces df[colname] with new_col. Special
    care is taken for the case when df has multiple columns named '*',
    since this causes pandas to crash.

    Parameters
    ----------
    df : pandas.DataFrame
        edited in place by this function
    colname : str
        name of column in df whose data is to be replaced
    new_col : vector, list, pandas.Series
        new column data for df[colname]
    """
    cols = df.columns
    tmpcols = [col if col != "*" else "aaa" for col in cols]
    df.columns = tmpcols
    df[colname] = new_col
    df.columns = cols
    return


class GdxError(Error):
    def __init__(self, H, msg):
        """
        Pulls information from gdxcc about the last encountered error and appends
        it to msg.

        Parameters
        ----------
        H : pointer or None
            SWIG binding pointer to a GDX object
        msg : str
            gdxpds error message

        Attributes
        ----------
        msg : str
            msg that is passed in with a gdxErrorStr appended
        """
        if H:
            msg += ". " + gdxcc.gdxErrorStr(H, gdxcc.gdxGetLastError(H))[1] + "."
        super().__init__(msg)


class DomainError(Error):
    """
    Raised for any invalid input to the dim/domain layer of a symbol:
    cycle in parent-child references, unknown parent name, wrong-length
    list against a fixed-dimension symbol, wrong outer type for ``dims``
    or ``domain``, or a malformed element (non-string in ``dims``; plain
    string passed to :py:attr:`GdxSymbol.domain` instead of a
    :py:class:`GdxSymbol` reference). Subclass of :class:`Error` so
    callers may continue catching the broader category if they wish.
    """


class GdxFile(MutableSequence, NeedsGamsDir):
    def __init__(
        self, gams_dir: str | os.PathLike[str] | None = None, lazy_load: bool = True
    ) -> None:
        """
        Initializes a GdxFile object by connecting to GAMS and creating a pointer.

        Raises a :class:`gdxpds.tools.GamsLoadError` if GAMS cannot be located or
        loaded, or if the GDX object cannot be created.

        Parameters
        ----------
        gams_dir : None or str
        lazy_load : bool
            If True, :py:class:`GdxSymbol` data are not automatically loaded when the
            symbols are initially :py:meth:`read`. Individual data tables can only be
            accessed later after the corresponding calls to :py:meth:`GdxSymbol.load`.
            If False, all data are automatically loaded and the full GDX file is
            available in memory after the call to :py:meth:`read`.
        """
        self.lazy_load = lazy_load
        self._version = None
        self._producer = None
        self._filename = None
        self._symbols = OrderedDict()
        # Set before anything that can raise, so cleanup() is safe if create fails.
        self._H = None
        self._handle = None
        self._finalizer = None

        NeedsGamsDir.__init__(self, gams_dir=gams_dir)
        self._handle = self._create_gdx_object()
        self._H = self._handle.H
        self.universal_set = GdxSymbol("*", GamsDataType.Set, dims=1, file=None, index=0)
        self.universal_set._file = self

        # Free the gdx object + wrapper exactly once, at the first of: cleanup(),
        # garbage collection, or interpreter exit. The callback is the handle's own
        # close() -- a bound method of self._handle, not self -- so it never keeps
        # this GdxFile alive (which would defeat GC-time finalization) and stays
        # valid at interpreter shutdown (close() uses callables bound when the
        # handle was created, not module-global lookups).
        self._finalizer = weakref.finalize(self, self._handle.close)
        return

    def cleanup(self) -> None:
        if self._finalizer is not None:
            self._finalizer()  # runs handle.close() at most once
        self._H = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.cleanup()

    def clone(self) -> GdxFile:
        """
        Returns a new GdxFile containing clones of the GdxSymbols in this
        GdxFile. The clone will not be associated with a filename. The clone's
        GdxSymbols will not have indexes. The clone will be ready to write to
        a new file.

        Returns
        -------
        :py:class:`GdxFile`
        """
        result = GdxFile(gams_dir=self.gams_dir, lazy_load=False)
        for symbol in self:
            result.append(symbol.clone())
            result[-1]._file = result
        return result

    @property
    def empty(self):
        """
        Returns True if this GdxFile object does not contain any symbols.

        Returns
        -------
        bool
        """
        return len(self) == 0

    @property
    def H(self):
        """
        GDX object handle
        """
        return self._H

    @property
    def filename(self):
        """
        Filename this :py:class:`GdxFile` is associated with, if any

        Returns
        -------
        None or str
        """
        return self._filename

    @property
    def version(self):
        """
        GDX file version
        """
        return self._version

    @property
    def producer(self):
        """
        What program wrote the GDX file
        """
        return self._producer

    @property
    def num_elements(self):
        """
        Total number of records present in this file, summed over all symbols.

        Returns
        -------
        int
        """
        return sum([symbol.num_records for symbol in self])

    def read(self, filename: str | os.PathLike[str]) -> None:
        """
        Opens gdx file at filename and reads meta-data. If not self.lazy_load,
        also loads all symbols.

        Throws an Error if not self.empty.

        Throws a GdxError if any calls to gdxcc fail.

        Parameters
        ----------
        filename : pathlib.Path or str
        """
        if not self.empty:
            raise Error("GdxFile.read can only be used if the GdxFile is .empty")

        # open the file
        rc = gdxcc.gdxOpenRead(self.H, str(filename))
        if not rc[0]:
            raise GdxError(self.H, f"Could not open {filename!r}")
        self._filename = filename

        # read in meta-data ...
        # ... for the file
        ret, self._version, self._producer = gdxcc.gdxFileVersion(self.H)
        if ret != 1:
            raise GdxError(self.H, "Could not get file version")
        ret, symbol_count, element_count = gdxcc.gdxSystemInfo(self.H)
        logger.debug(
            f"Opening '{filename}' with {symbol_count} symbols and "
            f"{element_count} elements with lazy_load = {self.lazy_load}."
        )
        # ... for the symbols
        ret, name, dims, data_type = gdxcc.gdxSymbolInfo(self.H, 0)
        if ret != 1:
            raise GdxError(self.H, "Could not get symbol info for the universal set")
        self.universal_set = GdxSymbol(name, data_type, dims=dims, file=self, index=0)
        for i in range(symbol_count):
            index = i + 1
            ret, name, dims, data_type = gdxcc.gdxSymbolInfo(self.H, index)
            if ret != 1:
                raise GdxError(self.H, f"Could not get symbol info for symbol {index}")
            try:
                sym = GdxSymbol(name, data_type, dims=dims, file=self, index=index)
                self.append(sym)
            except Exception as e:
                logger.error(f"Unable to initialize GdxSymbol {name!r}, because {e}. SKIPPING.")

        # Self-heal strict-domain refs whose parent appeared at a higher
        # GDX index than the child (malformed but readable). No-op for
        # well-formed files — each symbol's in-line attempt already
        # succeeded.
        for symbol in self:
            symbol.resolve_domain()

        # read all symbols if not lazy_load
        if not self.lazy_load:
            for symbol in self:
                symbol.load()
        return

    def reorder_for_strict_domains(self):
        """
        Reorder ``self._symbols`` in place so every symbol with a strict (``GdxSymbol``-ref)
        ``domain`` follows all of its parents. Stable topological sort: symbols that don't
        reference each other keep their current relative order. No-ops on cycles (logs a warning
        and leaves the original order untouched).

        Strict-domain writes require the parent's ``gdxDataWriteDone`` to have completed before
        the child's write starts. Calling this before :py:meth:`write` is the easy way to satisfy
        that constraint when symbols were appended in an unordered way.
        """
        names = list(self._symbols.keys())
        parents_of = {}
        for s in self._symbols.values():
            ps = set()
            if s.domain is not None:
                for d in s.domain:
                    if d is None or d is s:
                        continue
                    ps.add(d.name)
            parents_of[s.name] = ps

        ordered, cycle = _stable_topological_sort(names, parents_of)
        if cycle is not None:
            logger.warning(
                "reorder_for_strict_domains: cyclic domain references "
                "detected; leaving symbol order untouched. Cycle "
                "involves: %s",
                sorted(cycle),
            )
            return
        if ordered is None:
            return
        self._symbols = OrderedDict((name, self._symbols[name]) for name in ordered)

    def write(self, filename: str | os.PathLike[str]) -> None:
        """
        Writes this :py:class:`GdxFile` to filename

        Parameters
        ----------
        filename : pathlib.Path or str
        """
        # only write if all symbols loaded
        for symbol in self:
            if not symbol.loaded:
                raise Error("All symbols must be loaded before this file can be written.")

        ret = gdxcc.gdxOpenWrite(self.H, str(filename), "gdxpds")
        if not ret:
            raise GdxError(
                self.H,
                f"Could not open {filename!r} for writing. "
                "Consider cloning this file (.clone()) before trying to write.",
            )
        self._filename = filename

        # write the universal set
        self.universal_set.write()

        # Build the {name: position} map once so each symbol's strict-domain
        # eligibility check is O(1) per parent rather than O(N).
        name_positions = {name: i for i, name in enumerate(self._symbols.keys())}

        for i, symbol in enumerate(self, start=1):
            try:
                symbol.write(index=i, name_positions=name_positions)
            except Exception:
                logger.error(f"Unable to write {symbol} to {filename}")
                raise

        gdxcc.gdxClose(self.H)

    def __repr__(self):
        return f"GdxFile(self,gams_dir={repr(self.gams_dir)},lazy_load={repr(self.lazy_load)})"

    def __str__(self):
        s = f"GdxFile containing {len(self)} symbols and {self.num_elements} elements."
        sep = " Symbols:\n  "
        for symbol in self:
            s += sep + str(symbol)
            sep = "\n  "
        return s

    def __getitem__(self, key):
        """
        Supports list-like indexing and symbol-based indexing

        Parameters
        ----------
        key : int or str
            If int, the index into the list of symbols. If str, the name of the symbol to
            be accessed.

        Returns
        -------
        :py:class:`GdxSymbol`
        """
        return self._symbols[self._name_key(key)]

    def __setitem__(self, key, value):
        """
        Supports overwriting or adding a :py:class:`GdxSymbol` via a list-like interface

        Parameters
        ----------
        key : int
            Must be an index into the list of symbols, within range(len(self)+1)
        value : :py:class:`GdxSymbol`
        """
        self._check_insert_setitem(key, value)
        value._file = self
        if key < len(self):
            self._symbols[self._name_key(key)] = value
            self._fixup_name_keys()
            return
        assert key == len(self)
        self._symbols[value.name] = value
        return

    def __delitem__(self, key):
        """
        Deletes a symbol from this :py:class:`GdxFile`'s collection

        Parameters
        ----------
        key : int or str
            If int, the index into the list of symbols. If str, the name of the symbol to
            be accessed.
        """
        del self._symbols[self._name_key(key)]
        return

    def __len__(self):
        """
        Number of :py:class:`GdxSymbol`s in this :py:class:`GdxFile`
        """
        return len(self._symbols)

    def insert(self, key: int, value: GdxSymbol) -> None:
        """
        Inserts value at position key

        Parameters
        ----------
        key : int
            Must be an index into the list of symbols, within range(len(self)+1)
        value : :py:class:`GdxSymbol`
        """
        self._check_insert_setitem(key, value)
        value._file = self
        if key == len(self) and value.name not in self._symbols:
            # We can safely append the symbol. This is fast (O(log(n)) complexity)
            self._symbols[value.name] = value
        else:
            # Need to insert inside the sequence. This is slow (O(n) complexity)
            data = [(symbol.name, symbol) for symbol in self]
            data.insert(key, (value.name, value))
            self._symbols = OrderedDict(data)
        return

    def __contains__(self, key):
        """
        Returns True if __getitem__ works with key.
        """
        try:
            self.__getitem__(key)
            return True
        except Exception:
            return False

    def keys(self) -> list[str]:
        """
        List of symbol names obtained by iterating through this :py:class:`GdxFile`

        Returns
        -------
        list of str
        """
        return [symbol.name for symbol in self]

    def _name_key(self, key):
        name_key = key
        if isinstance(key, int):
            name_key = list(self._symbols.keys())[key]
        return name_key

    def _check_insert_setitem(self, key, value):
        if not isinstance(value, GdxSymbol):
            raise Error(f"GdxFiles only contain GdxSymbols. GdxFile was given a {type(value)}.")
        if not isinstance(key, int):
            raise Error(
                "When adding or replacing GdxSymbols in GdxFiles, only integer, not name indices, may be used."
            )
        if key > len(self):
            raise Error(f"Invalid key, {key}")
        return

    def _fixup_name_keys(self):
        self._symbols = OrderedDict(
            [(symbol.name, symbol) for _cur_key, symbol in self._symbols.items()]
        )
        return

    def _create_gdx_object(self):
        # Idempotent: first call binds the library + populates SPECIAL_VALUES;
        # subsequent calls validate self.gams_dir and warn on mismatch.
        load_gdxcc(gams_dir=self.gams_dir)
        # _GdxHandle validates the create (raising GamsLoadError on failure, and
        # deleting the wrapper without an unsafe gdxFree). This GdxFile keeps the
        # handle; its weakref.finalize calls handle.close() to free+delete.
        return _GdxHandle(gdxcc, self.gams_dir, self.gams_dir_source)


class GamsDataType(Enum):
    Set = gdxcc.GMS_DT_SET
    Parameter = gdxcc.GMS_DT_PAR
    Variable = gdxcc.GMS_DT_VAR
    Equation = gdxcc.GMS_DT_EQU
    Alias = gdxcc.GMS_DT_ALIAS


class GamsVariableType(Enum):
    Unknown = gdxcc.GMS_VARTYPE_UNKNOWN
    Binary = gdxcc.GMS_VARTYPE_BINARY
    Integer = gdxcc.GMS_VARTYPE_INTEGER
    Positive = gdxcc.GMS_VARTYPE_POSITIVE
    Negative = gdxcc.GMS_VARTYPE_NEGATIVE
    Free = gdxcc.GMS_VARTYPE_FREE
    SOS1 = gdxcc.GMS_VARTYPE_SOS1
    SOS2 = gdxcc.GMS_VARTYPE_SOS2
    Semicont = gdxcc.GMS_VARTYPE_SEMICONT
    Semiint = gdxcc.GMS_VARTYPE_SEMIINT


class GamsEquationType(Enum):
    Equality = 53 + gdxcc.GMS_EQUTYPE_E
    GreaterThan = 53 + gdxcc.GMS_EQUTYPE_G
    LessThan = 53 + gdxcc.GMS_EQUTYPE_L
    NothingEnforced = 53 + gdxcc.GMS_EQUTYPE_N
    External = 53 + gdxcc.GMS_EQUTYPE_X
    Conic = 53 + gdxcc.GMS_EQUTYPE_C


class GamsDomainType(Enum):
    """
    Domain status of a :py:class:`GdxSymbol`. Member ``.value`` matches the
    :c:func:`gdxSymbolGetDomainX` return code (1, 2, or 3 — see
    ``gdxcc.h``); ``gdxcc`` does not expose these as symbolic constants,
    so the integers are written out explicitly here.

    - ``NONE`` (1): no domain information stored.
    - ``RELAXED`` (2): domain stored as plain string names (via
      :c:func:`gdxSymbolSetDomainX`), no validation.
    - ``REGULAR`` (3): strict domain (via :c:func:`gdxSymbolSetDomain`),
      each non-wildcard dimension references an existing Set/Alias.
    """

    NONE = 1
    RELAXED = 2
    REGULAR = 3


class GamsValueType(Enum):
    Level = gdxcc.GMS_VAL_LEVEL  # .l
    Marginal = gdxcc.GMS_VAL_MARGINAL  # .m
    Lower = gdxcc.GMS_VAL_LOWER  # .lo
    Upper = gdxcc.GMS_VAL_UPPER  # .ub
    Scale = gdxcc.GMS_VAL_SCALE  # .scale

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            for value_type in cls:
                if value_type.name == value:
                    return value_type
            if value == "Value":
                return GamsValueType(GamsValueType.Level)
        super()._missing_(value)


GAMS_VALUE_COLS_MAP = defaultdict(lambda: [("Value", GamsValueType.Level.value)])
"""
List of value columns provided for each :py:attr:`GamsValueType`
"""
GAMS_VALUE_COLS_MAP[GamsDataType.Variable] = [
    (value_type.name, value_type.value) for value_type in GamsValueType
]
GAMS_VALUE_COLS_MAP[GamsDataType.Equation] = GAMS_VALUE_COLS_MAP[GamsDataType.Variable]


GAMS_VALUE_DEFAULTS = {
    GamsValueType.Level: 0.0,
    GamsValueType.Marginal: 0.0,
    GamsValueType.Lower: -np.inf,
    GamsValueType.Upper: np.inf,
    GamsValueType.Scale: 1.0,
}
"""
Default values for each :py:class:`GamsValueType`
"""

GAMS_VARIABLE_DEFAULT_LOWER_UPPER_BOUNDS = {
    GamsVariableType.Unknown: (-np.inf, np.inf),
    GamsVariableType.Binary: (0.0, 1.0),
    GamsVariableType.Integer: (0.0, np.inf),
    GamsVariableType.Positive: (0.0, np.inf),
    GamsVariableType.Negative: (-np.inf, 0.0),
    GamsVariableType.Free: (-np.inf, np.inf),
    GamsVariableType.SOS1: (0.0, np.inf),
    GamsVariableType.SOS2: (0.0, np.inf),
    GamsVariableType.Semicont: (1.0, np.inf),
    GamsVariableType.Semiint: (1.0, np.inf),
}
"""
Default lower and upper bounds for each :py:class:`GamsVariableType`
"""


class GdxSymbol:
    def __init__(
        self,
        name: str,
        data_type: GamsDataType | int,
        dims: int | list[str] = 0,
        file: GdxFile | None = None,
        index: int | None = None,
        description: str | None = "",
        variable_type: GamsVariableType | int | None = None,
        equation_type: GamsEquationType | int | None = None,
        domain: Sequence[GdxSymbol | None] | None = None,
    ) -> None:
        """
        In-memory representation of a GAMS GDX Symbol

        Parameters
        ----------
        name : str
        data_type : :py:class:`GamsDataType`
        dims : int or list of str
            If dims is set to an int, then that number of dimensions will be created, each
            indicated with the wildcard name '*'. Otherwise, a list of strings is expected,
            each string being a dimension name.
        file : None or :py:class:`GdxFile`
            Users should not set file. File is set by, e.g., :py:meth:`GdxFile.read` and
            :py:meth:`GdxFile.append`.
        index : None or int
            Users should not set file. File is set by, e.g., :py:meth:`GdxFile.read` and
            :py:meth:`GdxFile.append`.
        description : str
            Human readable description for this :py:class:`GdxSymbol`
        variable_type : None or :py:class:`GamsVariableType`
            Only expected if data_type == :py:attr:`GamsDataType.Variable`
        equation_type : None or :py:class:`GamsEquationType`
            Only expected if data_type == :py:attr:`GamsDataType.Equation`
        domain : None or list/tuple of (:py:class:`GdxSymbol` or None)
            Strict (regular) domain references, one per dimension. ``None`` entries map to the
            GAMS wildcard (``'*'``). When supplied, this flags the symbol for strict
            :c:func:`gdxSymbolSetDomain` writes (subject to the parent existing in the file at
            write time; otherwise the symbol falls back to relaxed). Plain strings are not
            accepted here; use ``dims`` for string-only domains.
        """
        self._name = name
        self.description = description
        self._loaded = False
        self._data_type = GamsDataType(data_type)
        self._variable_type = None
        self.variable_type = variable_type
        self._equation_type = None
        self.equation_type = equation_type
        self._dataframe = None
        self._dims = None
        self._domain = None
        self._strict_on_disk = False
        self.dims = dims
        if domain is not None:
            self.domain = domain
        assert self._dataframe is not None
        self._file = file
        self._index = index

        # adding this flag to implement ability to load set text instead of boolean values
        self._fixup_set_vals = True

        if self.file is not None:
            # reading from file
            # get additional meta-data
            ret, records, userinfo, description = gdxcc.gdxSymbolInfoX(self.file.H, self.index)
            if ret != 1:
                raise GdxError(
                    self.file.H, f"Unable to get extended symbol information for {self.name}"
                )
            self._num_records = records
            if self.data_type == GamsDataType.Variable:
                self.variable_type = GamsVariableType(userinfo)
            elif self.data_type == GamsDataType.Equation:
                self.equation_type = GamsEquationType(userinfo)
            self.description = description
            if self.index > 0:
                ret, gdx_domain = gdxcc.gdxSymbolGetDomainX(self.file.H, self.index)
                if ret == 0:
                    raise GdxError(self.file.H, f"Unable to get domain information for {self.name}")
                assert len(gdx_domain) == len(self.dims), (
                    "Dimensional information read in from GDX should be consistent."
                )
                self.dims = gdx_domain
                if ret == 3:
                    # Stored via strict gdxSymbolSetDomain. Mark so a later retry can distinguish
                    # strict-but-unresolved from truly relaxed, then try to resolve names to same-file
                    # GdxSymbol refs. Symbols with lower indices are already in self.file._symbols at
                    # this point; for well-formed GDX, this succeeds. GdxFile.read() also retries at the
                    # end to self-heal symbols whose parents had higher indices (malformed files).
                    self._strict_on_disk = True
                    self.resolve_domain()
            else:
                # universal set
                assert self.index == 0
                self._loaded = True
            return

        # writing new symbol
        self._loaded = True
        return

    def clone(self) -> GdxSymbol:
        """
        Create a copy of this :py:class:`GdxSymbol`

        Returns
        -------
        :py:class:`GdxSymbol`
        """
        if not self.loaded:
            raise Error(f"Symbol {repr(self.name)} cannot be cloned because it is not yet loaded.")

        assert self.loaded
        # Pass _domain directly to the constructor: the domain setter copies the list, so the
        # clone has its own slot list pointing at the same parent GdxSymbols. Write-time name
        # lookup resolves against whatever file the clone ends up in.
        result = GdxSymbol(
            self.name,
            self.data_type,
            dims=self.dims,
            description=self.description,
            variable_type=self.variable_type,
            equation_type=self.equation_type,
            domain=self._domain,
        )
        result.dataframe = copy.deepcopy(self.dataframe)
        assert result.loaded
        return result

    @property
    def name(self):
        """
        Name of this :py:class:`GdxSymbol`

        Returns
        -------
        str
        """
        return self._name

    @name.setter
    def name(self, value):
        self._name = value
        if self.file is not None:
            self.file._fixup_name_keys()
        return

    @property
    def description(self) -> str:
        """
        Human-readable description for this :py:class:`GdxSymbol`. Never ``None``:
        a ``None`` assigned here (e.g. via the ``append_*`` helpers, which default
        ``description`` to ``None``) is stored as ``""`` so :py:meth:`__str__` and
        ``gdxDataWriteStrStart`` always get a string.

        Returns
        -------
        str
        """
        return self._description

    @description.setter
    def description(self, value: str | None) -> None:
        self._description = value if value is not None else ""

    @property
    def data_type(self):
        """
        GAMS data type of this :py:class:`GdxSymbol`

        Returns
        -------
        :py:class:`GamsDataType`
        """
        return self._data_type

    @data_type.setter
    def data_type(self, value):
        if not self.loaded or self.num_records > 0:
            raise Error(
                "Cannot change the data_type of a GdxSymbol that is yet to be read for file or contains records."
            )
        self._data_type = GamsDataType(value)
        self.variable_type = None
        self.equation_type = None
        self._init_dataframe()
        return

    @property
    def variable_type(self):
        """
        Only not none if :py:attr:`data_type` == :py:attr:`GamsDataType.Variable`

        Returns
        -------
        None or :py:attr:`GamsDataType.Variable`
        """
        return self._variable_type

    @variable_type.setter
    def variable_type(self, value):
        if self.data_type == GamsDataType.Variable:
            if value is None:
                # default to Free
                self._variable_type = GamsVariableType.Free
            else:
                try:
                    self._variable_type = GamsVariableType(value)
                except Exception:
                    if isinstance(self._variable_type, GamsVariableType):
                        logger.warning(f"Ignoring invalid GamsVariableType request '{value}'.")
                        return
                    logger.debug(f"Setting variable_type to {GamsVariableType.Free}.")
                    self._variable_type = GamsVariableType.Free
            return
        assert self.data_type != GamsDataType.Variable
        if value is not None:
            logger.warning("GdxSymbol is not a Variable, so setting variable_type to None")
        self._variable_type = None

    @property
    def equation_type(self):
        """
        Only not none if :py:attr:`data_type` == :py:attr:`GamsDataType.Equation`

        Returns
        -------
        None or :py:attr:`GamsDataType.Equation`
        """
        return self._equation_type

    @equation_type.setter
    def equation_type(self, value):
        if self.data_type == GamsDataType.Equation:
            if value is None:
                # default to Equality
                self._equation_type = GamsEquationType.Equality
            else:
                try:
                    self._equation_type = GamsEquationType(value)
                except Exception:
                    if isinstance(self._equation_type, GamsEquationType):
                        logger.warning(f"Ignoring invalid GamsEquationType request '{value}'.")
                        return
                    logger.debug(f"Setting equation_type to {GamsEquationType.Equality}.")
                    self._equation_type = GamsEquationType.Equality
            return
        assert self.data_type != GamsDataType.Equation
        if value is not None:
            logger.warning("GdxSymbol is not an Equation, so setting equation_type to None")
        self._equation_type = None

    @property
    def value_cols(self):
        """
        List of (name, GamsValueType.value) tuples that describe the
        value columns in the dataframe, that is, the columns that follow the
        self.dims columns.

        Returns
        -------
        list of (str, int)
        """
        return GAMS_VALUE_COLS_MAP[self.data_type]

    @property
    def value_col_names(self):
        """
        List of value column names, that is, the columns that follow the self.dims columns.

        Returns
        -------
        list of str
        """
        return [col_name for col_name, col_ind in self.value_cols]

    def get_value_col_default(self, value_col_name):
        if value_col_name not in self.value_col_names:
            raise Error(
                f"{value_col_name} is not one of the value columns for "
                f"this GdxSymbol, which is a {self.data_type}"
            )
        value_col = GamsValueType(value_col_name)
        if self.data_type == GamsDataType.Set:
            assert value_col == GamsValueType.Level
            return c_bool(True)
        if (self.data_type == GamsDataType.Variable) and (
            (value_col == GamsValueType.Lower) or (value_col == GamsValueType.Upper)
        ):
            lb_default, ub_default = GAMS_VARIABLE_DEFAULT_LOWER_UPPER_BOUNDS[self.variable_type]
            if value_col == GamsValueType.Lower:
                return lb_default
            else:
                assert value_col == GamsValueType.Upper
                return ub_default
        return GAMS_VALUE_DEFAULTS[value_col]

    @property
    def file(self):
        """
        :py:class:`GdxFile` file that contains this :py:class:`GdxSymbol`, if any

        Returns
        -------
        None or :py:class:`GdxFile`
        """
        return self._file

    @property
    def index(self):
        """
        Index of this :py:class:`GdxSymbol` in its :py:class:`GdxFile`, if any

        Returns
        -------
        None or int
        """
        return self._index

    @property
    def loaded(self):
        """
        Whether the data for this symbol has been loaded

        Returns
        -------
        bool
        """
        return self._loaded

    @property
    def full_typename(self):
        if self.data_type == GamsDataType.Parameter and self.dims == 0:
            return "Scalar"
        elif self.data_type == GamsDataType.Variable:
            return self.variable_type.name + " " + self.data_type.name
        return self.data_type.name

    @property
    def dims(self):
        """
        List of dimension names over which this symbol is defined. If the :py:class:`GdxSymbol` was
        constructed with dims set to an integer, all dimension names will be the wildcard '*'.

        Returns
        -------
        list of str
            length of list is equal to :py:attr:`num_dims`
        """
        return self._dims

    @dims.setter
    def dims(self, value):
        self._set_dims_internal(value)
        # Assigning dims by string drops any strict (GdxSymbol-ref) domain.
        self._domain = None

    def _set_dims_internal(self, value):
        """
        Shared implementation for the ``dims`` setter and the ``domain`` setter.
        Updates ``self._dims`` and either initializes the dataframe (no records
        yet) or reflows the dataframe columns in place. Does NOT touch
        ``self._domain``; callers are responsible for that. Raises
        :class:`DomainError` on bad shape/type input.
        """
        if (self._dims is not None) and (
            self.loaded and ((self.num_dims > 0) or (self.num_records > 0))
        ):
            if not isinstance(value, list) or len(value) != self.num_dims:
                raise DomainError(
                    f"Cannot set dims to {value}, because the number of "
                    f"dimensions has already been set to {self.num_dims}."
                )
        if isinstance(value, int):
            self._dims = ["*"] * value
            self._init_dataframe()
            return
        if not isinstance(value, list):
            raise DomainError(
                f"dims must be an int or a list. Was passed {value} of type {type(value)}."
            )
        for dim in value:
            if not isinstance(dim, str):
                raise DomainError(
                    f"Individual dimensions must be denoted by strings. Was passed {dim} as element of {value}."
                )
        assert (
            (self._dims is None)
            or (self.loaded and (self.num_dims == 0) and (self.num_records == 0))
            or (len(value) == self.num_dims)
        )
        self._dims = value
        if self.loaded and self.num_records > 0:
            self._dataframe.columns = self.dims + self.value_col_names
            return
        self._init_dataframe()

    @property
    def domain(self):
        """
        Strict (regular) domain references: one :py:class:`GdxSymbol` per dimension, or
        ``None`` per slot meaning the GAMS wildcard (``'*'``). The whole attribute is
        ``None`` when no strict refs are known.

        Setting ``domain`` rewrites :py:attr:`dims` to the stringified version (parent name
        per slot, ``'*'`` for ``None`` slots), reflowing DataFrame column headers in place.
        Setting ``dims`` instead clears ``domain``.

        Returns
        -------
        list of (:py:class:`GdxSymbol` or None), or None
        """
        return self._domain

    @domain.setter
    def domain(self, value):
        if value is None:
            self._domain = None
            return
        if not isinstance(value, (list, tuple)):
            raise DomainError(
                "domain must be a list or tuple of (GdxSymbol | None), or None. "
                f"Was passed {value} of type {type(value)}."
            )
        for d in value:
            if d is None:
                continue
            if isinstance(d, str):
                raise DomainError(
                    "domain entries must be GdxSymbol references or None. "
                    "Use the dims attribute for string-only domains. "
                    f"Was passed string {d!r} as element of {value}."
                )
            if not isinstance(d, GdxSymbol):
                raise DomainError(
                    "domain entries must be GdxSymbol references or None. "
                    f"Was passed {d} of type {type(d)} as element of {value}."
                )
        if (
            (self._dims is not None)
            and self.loaded
            and ((self.num_dims > 0) or (self.num_records > 0))
        ):
            if len(value) != self.num_dims:
                raise DomainError(
                    f"Cannot set domain to length {len(value)}, because the "
                    f"number of dimensions has already been set to {self.num_dims}."
                )
        stringified = [d.name if isinstance(d, GdxSymbol) else "*" for d in value]
        self._set_dims_internal(stringified)
        self._domain = list(value)

    @property
    def domain_type(self):
        """
        Derived domain status. See :py:class:`GamsDomainType` for the codes.

        Returns
        -------
        :py:class:`GamsDomainType`
        """
        if self._domain is not None and any(isinstance(d, GdxSymbol) for d in self._domain):
            return GamsDomainType.REGULAR
        if self._dims is not None and all(d == "*" for d in self._dims):
            return GamsDomainType.NONE
        return GamsDomainType.RELAXED

    def resolve_domain(self):
        """
        Attempt to populate :py:attr:`domain` with live :py:class:`GdxSymbol` references
        by looking up each entry in :py:attr:`dims` against ``self.file._symbols``. Idempotent;
        no-ops when ``domain`` is already set, when there's no associated file, or when the
        symbol's on-disk domain wasn't strict (``REGULAR``).

        Useful when:

        - A GDX file was read whose strict-domain parent has a higher index than the child
          (malformed; rare). The in-line resolution during read fails for that symbol; calling
          this method after read picks the parent up.
        - The user has manipulated ``self.file`` after reading (e.g. appended or replaced the
          parent symbol) and wants the ``REGULAR`` write path re-enabled.

        Returns
        -------
        bool
            True if ``domain`` was populated as a result of this call, False otherwise.
        """
        if self._domain is not None:
            return False
        if self.file is None or not self._strict_on_disk:
            return False
        if not self._dims:
            return False
        resolved = []
        for d in self._dims:
            if d == "*":
                resolved.append(None)
            elif d in self.file._symbols:
                resolved.append(self.file._symbols[d])
            else:
                logger.warning(
                    "resolve_domain: symbol %r references strict-domain "
                    "parent %r which is not in this file; leaving domain "
                    "unresolved (RELAXED on the next write).",
                    self.name,
                    d,
                )
                return False
        self._domain = resolved
        return True

    def _strict_domain_writeable(self, name_positions=None):
        """
        Internal: True iff this symbol's ``_domain`` can be written using strict
        :c:func:`gdxSymbolSetDomain` at this point in the file's write sequence. Requires every
        non-``None`` entry to name a parent that (a) exists in ``self.file._symbols`` and
        (b) precedes this symbol in insertion order (so the parent has already been
        ``gdxDataWriteDone``-ed and is in the GDX symbol table). Returns False for
        self-referential 1-dim sets.

        ``name_positions`` is an optional ``{name: insertion_position}`` map for
        ``self.file._symbols``. :py:meth:`GdxFile.write` builds it once and passes it
        through so eligibility checks stay O(1) per parent rather than O(N) across a
        whole-file write. When omitted (standalone calls) it is built on demand.
        """
        if self._domain is None:
            return False
        if self.file is None:
            return False
        if name_positions is None:
            name_positions = {name: i for i, name in enumerate(self.file._symbols.keys())}
        my_pos = name_positions.get(self.name)
        if my_pos is None:
            return False
        for d in self._domain:
            if d is None:
                continue
            if d is self:
                # Self-referential set: parent isn't yet in the GDX symbol
                # table when its own write starts.
                return False
            parent_pos = name_positions.get(d.name)
            if parent_pos is None:
                # Parent not in this file.
                return False
            if parent_pos >= my_pos:
                # Parent hasn't been written yet in this pass.
                return False
        return True

    @property
    def num_dims(self):
        """
        Number of dimensions over which this symbol is defined

        Returns
        -------
        int
        """
        return len(self.dims)

    @property
    def dataframe(self):
        """
        Data table for this symbol. Dim columns are followed by value columns, left to right.

        Returns
        -------
        pd.DataFrame
        """
        return self._dataframe

    @dataframe.setter
    def dataframe(self, data):
        try:
            # get data in common format and start dealing with dimensions
            if isinstance(data, pd.DataFrame):
                df = data.copy()
                has_col_names = True
            else:
                df = pd.DataFrame(data)
                has_col_names = False
                if df.empty:
                    # clarify dimensionality, as needed for loading empty GdxSymbols
                    df = pd.DataFrame(data, columns=self.dims + self.value_cols)

            # finish handling dimensions
            n = len(df.columns)
            if (self.num_dims > 0) or (self.num_records > 0):
                if not ((n == self.num_dims) or (n == self.num_dims + len(self.value_cols))):
                    raise Error(
                        f"Cannot set dataframe to {df.head()} because the number "
                        + f"of dimensions would change. This symbol has {self.num_dims} "
                        + f"dimensions, currently represented by {self.dims}."
                    )
                num_dims = self.num_dims
            else:
                # num_dims not explicitly established yet. in this case we must
                # assume value columns have been provided or dimensionality is 0
                num_dims = max(n - len(self.value_cols), 0)
                if (num_dims == 0) and (n < len(self.value_cols)):
                    raise Error(
                        f"Cannot set dataframe to {df.head()} because the number "
                        + f"of dimensions cannot be established consistent with {self}."
                    )
                if self.loaded and (num_dims > 0):
                    logger.warning(
                        f"Inferring {self.name} to have {num_dims} dimensions. "
                        + "Recommended practice is to explicitly set gdxpds.gdx.GdxSymbol dims in the constructor."
                    )

            replace_dims = True
            if has_col_names:
                dim_cols = list(df.columns)[:num_dims]
            elif self.num_dims == num_dims:
                dim_cols = self.dims
                replace_dims = False
            else:
                dim_cols = ["*"] * num_dims
            for col in dim_cols:
                if not isinstance(col, str):
                    replace_dims = False
                    logger.info(
                        f"Not using dataframe column names to set dimensions because {col} is not a string."
                    )
                    if num_dims != self.num_dims:
                        self.dims = num_dims
                    break
            if replace_dims and dim_cols != self._dims:
                # Going through the dims setter clears any strict (GdxSymbol-ref)
                # domain. Only do that when the names are actually changing.
                self.dims = dim_cols
            # all done establishing dimensions
            assert self.num_dims == num_dims

            # finalize the dataframe
            if n == self.num_dims:
                self._append_default_values(df)
            df.columns = self.dims + self.value_col_names
            self._dataframe = df
        except Exception:
            logger.error(
                f"Unable to set dataframe for {self} to\n{data}\n\nIn process dataframe: {self._dataframe}"
            )
            raise

        if self.data_type == GamsDataType.Set:
            self._fixup_set_value()
        return

    def _init_dataframe(self):
        self._dataframe = pd.DataFrame([], columns=self.dims + self.value_col_names)
        if self.data_type == GamsDataType.Set:
            colname = self._dataframe.columns[-1]
            replace_df_column(self._dataframe, colname, self._dataframe[colname].astype(c_bool))
        return

    def _append_default_values(self, df):
        assert len(df.columns) == self.num_dims
        logger.debug(f"Applying default values to create valid dataframe for '{self.name}'.")
        for value_col_name in self.value_col_names:
            df[value_col_name] = self.get_value_col_default(value_col_name)

    def _fixup_set_value(self):
        """
        Tricky to get boolean set values to come through right.
        isinstance(True,Number) == True and float(True) = 1, but
        isinstance(c_bool(True),Number) == False, and this keeps the default
        value of 0.0.

        Could just test for isinstance(,bool), but this fix has the added
        advantage of speaking the GDX bindings data type language, and also
        fills in any missing values, so users no longer need to actually specify
        self.dataframe['Value'] = True.
        """
        assert self.data_type == GamsDataType.Set

        colname = self._dataframe.columns[-1]
        assert colname == self.value_col_names[0], (
            f"Unexpected final column {colname!r} in Set dataframe"
        )
        if self._dataframe[colname].isnull().values.any():
            logger.warning(
                f"Filling null values in {self} with True. To be "
                f"filled:\n{self._dataframe[self._dataframe[colname].isnull()]}"
            )
            replace_df_column(self._dataframe, colname, self._dataframe[colname].fillna(value=True))
        if self._fixup_set_vals:
            replace_df_column(
                self._dataframe, colname, self._dataframe[colname].apply(lambda x: c_bool(x))
            )
        self._fixup_set_vals = True
        return

    @property
    def num_records(self):
        """
        Number of rows in the data table, per the DataFrame if :py:attr:`loaded`, or per GAMS.

        Returns
        -------
        int
        """
        if self.loaded:
            return len(self.dataframe.index)
        return self._num_records

    def __repr__(self):
        return f"GdxSymbol({repr(self.name)},{repr(self.data_type)},{repr(self.dims)},file={repr(self.file)},index={repr(self.index)},description={repr(self.description)},variable_type={repr(self.variable_type)},equation_type={repr(self.equation_type)})"

    def __str__(self):
        s = self.name
        s += ", " + self.description
        s += ", " + self.full_typename
        s += f", {self.num_records} records"
        s += f", {self.num_dims} dims {self.dims}"
        s += ", loaded" if self.loaded else ", not loaded"
        return s

    def load(self, load_set_text: bool = False) -> None:
        """
        Loads this :py:class:`GdxSymbol` from its :py:attr:`file`, thereby popluating
        :py:attr:`dataframe`.

        Parameters
        ----------
        load_set_text : bool
            If True (default is False) and this symbol is a :class:`GamsDataType.Set <GamsDataType>`,
            loads the GDX Text field into the :py:attr:`dataframe` rather than a `c_bool`.
        """
        if self.loaded:
            logger.info("Nothing to do. Symbol already loaded.")
            return
        if not self.file:
            raise Error(f"Cannot load {repr(self)} because there is no file pointer")
        if not self.index:
            raise Error(f"Cannot load {repr(self)} because there is no symbol index")

        if self.data_type == GamsDataType.Parameter and HAVE_GDX2PY:
            self.dataframe = gdx2py.par2list(self.file.filename, self.name)
            self._loaded = True
            return

        _ret, records = gdxcc.gdxDataReadStrStart(self.file.H, self.index)

        def reader():
            handle = self.file.H
            for i in range(records):
                yield gdxcc.gdxDataReadStr(handle)

        vc = self.value_cols  # do this for speed in the next line
        if load_set_text and (self.data_type == GamsDataType.Set):
            data = [
                elements
                + [
                    gdxcc.gdxGetElemText(self.file.H, int(values[col_ind]))[1]
                    for _col_name, col_ind in vc
                ]
                for _ret, elements, values, _afdim in reader()
            ]
            self._fixup_set_vals = False
        else:
            data = [
                elements + [values[col_ind] for col_name, col_ind in vc]
                for ret, elements, values, afdim in reader()
            ]
        self.dataframe = data
        if self.data_type not in (GamsDataType.Set, GamsDataType.Alias):
            self.dataframe = special.convert_gdx_to_np_svs(self.dataframe, self.num_dims)
        self._loaded = True
        return

    def unload(self) -> None:
        """
        Drops this :py:class:`GdxSymbol`'s :py:attr:`dataframe`
        """
        self.dataframe = None
        self._loaded = False

    def write(self, index: int | None = None, name_positions: dict | None = None) -> None:
        """
        Writes this :py:class:`GdxSymbol` to its :py:attr:`file`
        """
        if not self.loaded:
            raise Error(f"Cannot write unloaded symbol {self.name!r}.")
        if self.file is None:
            raise Error(f"Cannot write {self!r} because there is no file pointer")

        if self.data_type == GamsDataType.Set:
            self._fixup_set_value()

        if index is not None:
            self._index = index

        if self.index == 0:
            # universal set
            gdxcc.gdxUELRegisterRawStart(self.file.H)
            gdxcc.gdxUELRegisterRaw(self.file.H, self.name)
            gdxcc.gdxUELRegisterDone(self.file.H)
            return

        # write the data
        userinfo = 0
        if self.variable_type is not None:
            userinfo = self.variable_type.value
        elif self.equation_type is not None:
            userinfo = self.equation_type.value
        if not gdxcc.gdxDataWriteStrStart(
            self.file.H, self.name, self.description, self.num_dims, self.data_type.value, userinfo
        ):
            raise GdxError(
                self.file.H, f"Could not start writing data for symbol {repr(self.name)}"
            )
        # set domain information: prefer strict gdxSymbolSetDomain when every
        # entry of self._domain either is None ('*') or refers to a parent
        # already written to this file; otherwise fall back to relaxed
        # gdxSymbolSetDomainX. Decision is per-symbol because GDX itself only
        # supports per-symbol strict/relaxed.
        if self.num_dims > 0:
            domain = self._domain if self._strict_domain_writeable(name_positions) else None
            if domain is not None:
                names = [d.name if d is not None else "*" for d in domain]
                if not gdxcc.gdxSymbolSetDomain(self.file.H, names):
                    raise GdxError(
                        self.file.H,
                        f"Could not set strict domain information for {repr(self.name)}. "
                        f"Domains are {repr(names)}",
                    )
            elif self.index:
                if not gdxcc.gdxSymbolSetDomainX(self.file.H, self.index, self.dims):
                    raise GdxError(
                        self.file.H,
                        f"Could not set domain information for {repr(self.name)}. Domains are {repr(self.dims)}",
                    )
            else:
                logger.info("Not writing domain information because symbol index is unknown.")
        values = gdxcc.doubleArray(gdxcc.GMS_VAL_MAX)
        # make sure index is clean -- needed for merging in convert_np_to_gdx_svs
        self.dataframe = self.dataframe.reset_index(drop=True)
        # convert special numeric values if appropriate
        to_write = (
            self.dataframe.copy()
            if (self.data_type in (GamsDataType.Set, GamsDataType.Alias))
            else special.convert_np_to_gdx_svs(self.dataframe, self.num_dims)
        )
        # write each row
        for row in to_write.itertuples(index=False, name=None):
            dims = [str(x) for x in row[: self.num_dims]]
            vals = row[self.num_dims :]
            for _col_name, col_ind in self.value_cols:
                values[col_ind] = 0.0
                try:
                    if isinstance(vals[col_ind], Number):
                        values[col_ind] = float(vals[col_ind])
                except Exception:
                    raise Error(f"Unable to set element {col_ind} from {vals}.")
            gdxcc.gdxDataWriteStr(self.file.H, dims, values)
        gdxcc.gdxDataWriteDone(self.file.H)
        return


# ------------------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------------------


def append_set(
    gdx_file: GdxFile,
    set_name: str,
    df: pd.DataFrame,
    cols: list[str] | None = None,
    dim_names: list[str] | None = None,
    description: str | None = None,
    domain: Sequence[GdxSymbol | None] | None = None,
) -> GdxSymbol:
    """
    Convenience function that appends set_name to gdx_file as a
    :class:`GamsDataType.Set <GamsDataType>` :class:`GdxSymbol` using data in
    df.

    Parameters
    ----------
    gdx_file : :class:`GdxFile`
        file to which new :class:`GdxSymbol` is to be added
    set_name : str
        name of the :class:`GdxSymbol` to be added
    df : pandas.DataFrame
        dataframe or data that can be used to construct a dataframe containing
        the set data. assumes that all columns define dimensions (there is no
        'Value' column)
    cols : None or list of str
        if not None, these are the columns in df to be used for the set
        definition
    dim_names : None or list of str
        if provided, the columns of a copy of df (or of df[cols]) will be renamed
        to these names, because the dimension names are taken from the final
        dataframe, these will also be the dimension names
    description : None or str
        passed directly to :class:`GdxSymbol`
    domain : None or list/tuple of (:class:`GdxSymbol` or None)
        strict (regular) domain references, one per dimension. ``None`` slots
        mean the wildcard (``'*'``). When supplied, the new symbol is
        flagged for strict :c:func:`gdxSymbolSetDomain` writes.

    Returns
    -------
    :class:`GdxSymbol`
        The freshly appended symbol — useful for ``child = append_set(...,
        domain=[parent])`` chains.
    """
    # ensure df is DataFrame and not Series
    logger.debug(f"Defining set {set_name!r} based on:\n{df!r}")
    tmp = pd.DataFrame(df)
    # select down to data we actually want
    if cols is not None:
        tmp = tmp[cols]
    if dim_names is not None:
        if tmp.empty:
            tmp = pd.DataFrame([], columns=dim_names)
        else:
            tmp.columns = dim_names
    # define the symbol
    gdx_file.append(
        GdxSymbol(
            set_name,
            GamsDataType.Set,
            dims=list(tmp.columns),
            description=description,
            domain=domain,
        )
    )
    # define the data for the symbol
    gdx_file[-1].dataframe = tmp
    # debug description of what happened
    logger.debug(f"Added set {set_name!r} to {gdx_file!r} using processed data:\n{tmp!r}")
    return gdx_file[-1]


def append_parameter(
    gdx_file: GdxFile,
    param_name: str,
    df: pd.DataFrame,
    cols: list[str] | None = None,
    dim_names: list[str] | None = None,
    description: str | None = None,
    domain: Sequence[GdxSymbol | None] | None = None,
) -> GdxSymbol:
    """
    Convenience function that appends param_name to gdx_file as a
    :class:`GamsDataType.Parameter <GamsDataType>` :class:`GdxSymbol` using
    data in df.

    Parameters
    ----------
    gdx_file : :class:`GdxFile`
        file to which new :class:`GdxSymbol` is to be added
    param_name : str
        name of the :class:`GdxSymbol` to be added
    df : pandas.DataFrame
        dataframe or data that can be used to construct a dataframe containing
        the parameter data. assumes that the last selected column is the 'Value'
        column
    cols : None or list of str
        if not None, these are the columns in df to be used for the parameter
        definition (dimension columns followed by value column)
    dim_names : None or list of str
        if provided, the columns of a copy of df (or of df[cols]) will be renamed
        to these names + ['Value']. because the dimension names are taken from
        the final dataframe, these will also be the dimension names
    description : None or str
        passed directly to :class:`GdxSymbol`
    domain : None or list/tuple of (:class:`GdxSymbol` or None)
        strict (regular) domain references, one per dimension. ``None`` slots
        mean the wildcard (``'*'``). When supplied, the new symbol is
        flagged for strict :c:func:`gdxSymbolSetDomain` writes.

    Returns
    -------
    :class:`GdxSymbol`
        The freshly appended symbol — useful for chaining further
        ``domain=[returned_param]`` references.
    """
    # pre-process the data
    logger.debug(f"Defining parameter {param_name!r} based on:\n{df!r}")
    tmp = pd.DataFrame(df)
    if cols is not None:
        tmp = tmp[cols]
    if dim_names is not None:
        if tmp.empty:
            tmp = pd.DataFrame([], columns=dim_names + ["Value"])
        else:
            tmp.columns = dim_names + ["Value"]
    # define the symbol
    gdx_file.append(
        GdxSymbol(
            param_name,
            GamsDataType.Parameter,
            dims=list(tmp.columns)[:-1],
            description=description,
            domain=domain,
        )
    )
    # define the data for the symbol
    gdx_file[-1].dataframe = tmp
    # debug descripton of what happened
    logger.debug(f"Added parameter {param_name!r} to {gdx_file!r} using processed data:\n{tmp!r}")
    return gdx_file[-1]
