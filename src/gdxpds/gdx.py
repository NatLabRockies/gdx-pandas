"""
Engine functionality for reading and writing GDX files.
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
from enum import Enum

import numpy as np
import pandas as pd

from gdxpds._engine import Engine, make_engine, resolve_engine

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
from gdxpds.tools import Error, NeedsGamsDir

logger = logging.getLogger(__name__)

# Pandas >= 3 has Copy-on-Write always enabled, so a shallow copy of the
# caller's DataFrame in :meth:`GdxSymbol.dataframe.setter` is safe -- a later
# user-side mutation of the source frame triggers a copy and does not leak into
# the stored frame. On pandas < 3 (no CoW), the shallow copy shares column
# storage with the caller and the assignment-captures-a-snapshot contract
# requires a deep copy. Gating this saves an O(rows * num_dims) string-ref
# allocation per write on pandas 3 (~22 MB on a 500K-row Parameter), which
# dominates the gams_transfer engine's write-time peak vs raw_transfer.
_PANDAS_HAS_COW: bool = int(pd.__version__.split(".", 1)[0]) >= 3


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
            # Imported lazily: GdxError is only raised on the gdxcc path, where a
            # binding is necessarily present. Keeping it out of module scope lets
            # `import gdxpds` succeed with no binding installed.
            try:
                from gams.core import gdx as gdxcc
            except ImportError:
                import gdxcc
            msg += ". " + gdxcc.gdxErrorStr(H, gdxcc.gdxGetLastError(H))[1] + "."
        super().__init__(msg)


class TransferError(Error):
    """Raised when a ``gams.transfer`` read or write operation fails.

    The gams.transfer counterpart to :class:`GdxError`. There is no GDX handle or
    last-error registry behind gams.transfer, so the underlying exception is
    carried by chaining (``raise TransferError(...) from e``) rather than a
    handle-derived message. Subclass of :class:`Error`, so ``except Error`` still
    catches it.
    """


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


class SymbolNotFoundError(Error):
    """Raised when a requested symbol name is not present in a :class:`GdxFile`.

    Subclass of :class:`Error`, so ``except Error`` still catches it.
    """


class GdxFile(MutableSequence, NeedsGamsDir):
    def __init__(
        self,
        gams_dir: str | os.PathLike[str] | None = None,
        lazy_load: bool = True,
        engine: str | Engine | None = None,
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
        engine : None or str or :py:class:`gdxpds.Engine`
            Which I/O engine to use. ``None`` (default) resolves via the
            ``GDXPDS_ENGINE`` env var, then the default engine: ``gams.transfer``
            when usable, otherwise ``gdxcc``. Pass ``"gdxcc"`` / ``Engine.GDXCC``
            to pin the gdxcc engine.
        """
        self.lazy_load = lazy_load
        self._version = None
        self._producer = None
        self._filename = None
        self._symbols = OrderedDict()
        # Set before anything that can raise, so cleanup() is safe if create fails.
        self._finalizer = None
        self._engine_impl = None
        self._engine_kind = None

        NeedsGamsDir.__init__(self, gams_dir=gams_dir)
        # Build the I/O engine. For the gdxcc engine this binds the GDX library
        # and creates the handle, which the engine owns and frees in close().
        # `self.gams_dir` (resolved above) is threaded through to engine
        # selection so the gams.transfer probe runs against the caller's
        # actual install rather than the cached default-discovered one.
        self._engine_kind = resolve_engine(engine, gams_dir=self.gams_dir)
        self._engine_impl = make_engine(self._engine_kind, self.gams_dir, self.gams_dir_source)

        # Free the engine's native resources exactly once, at the first of:
        # cleanup(), garbage collection, or interpreter exit. The callback is the
        # engine's own close() -- a bound method of the engine, not self -- so
        # it never keeps this GdxFile alive (which would defeat GC-time
        # finalization) and stays valid at interpreter shutdown (close() uses
        # callables bound when the handle was created, not module-global lookups).
        self._finalizer = weakref.finalize(self, self._engine_impl.close)

        self.universal_set = GdxSymbol("*", GamsDataType.Set, dims=1, file=None, index=0)
        self.universal_set._file = self
        return

    def cleanup(self) -> None:
        if self._finalizer is not None:
            self._finalizer()  # runs engine.close() at most once

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
        result = GdxFile(gams_dir=self.gams_dir, lazy_load=False, engine=self._engine_kind)
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

        # The engine reads file + symbol metadata and builds the GdxSymbol
        # collection (records are not loaded here).
        self._engine_impl.open_read(self, filename)

        # read all symbols if not lazy_load
        if not self.lazy_load:
            self.load_all()
        return

    def load_all(self) -> None:
        """
        Eagerly load every symbol's records into its :py:attr:`GdxSymbol.dataframe`.

        Already-loaded symbols are skipped.
        """
        self._engine_impl.load_file(self)

    def load_symbols(self, names: Sequence[str]) -> None:
        """
        Eagerly load the records of the named symbols (a subset of the file).

        Resolves each name to its :py:class:`GdxSymbol` and loads via the engine
        (gams.transfer issues a single targeted read; gdxcc loops per symbol).
        Raises :class:`SymbolNotFoundError` for an unknown name; already-loaded
        symbols are skipped.
        """
        symbols = []
        for name in names:
            if name not in self:
                raise SymbolNotFoundError(f"No symbol named {name!r} in {self.filename!r}.")
            symbols.append(self[name])
        self._engine_impl.load_symbols(self, symbols)

    def reorder_for_strict_domains(self):
        """
        Reorder ``self._symbols`` in place so every symbol follows the symbols it depends
        on: each strict (``GdxSymbol``-ref) ``domain`` parent, and the parent Set of each
        :py:attr:`GamsDataType.Alias`. Stable topological sort: symbols that don't reference
        each other keep their current relative order. No-ops on cycles (logs a warning and
        leaves the original order untouched).

        Strict-domain and alias writes require the parent's ``gdxDataWriteDone`` (or, for an
        alias, the parent's registration) to have completed before the dependent symbol is
        written. Calling this before :py:meth:`write` is the easy way to satisfy that
        constraint when symbols were appended in an unordered way.
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
            # An alias must be written after the Set it aliases.
            if s.alias_of_name is not None and s.alias_of_name != s.name:
                ps.add(s.alias_of_name)
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
        self._engine_impl.write_file(self, filename)

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


# GAMS GDX type codes, hardcoded so `import gdxpds` needs no binding at module
# load. These mirror the gdxcc ``GMS_*`` constants; ``test_gms_constants_match_gdxcc``
# verifies the match whenever a binding is installed.
class GamsDataType(Enum):
    Set = 0  # GMS_DT_SET
    Parameter = 1  # GMS_DT_PAR
    Variable = 2  # GMS_DT_VAR
    Equation = 3  # GMS_DT_EQU
    Alias = 4  # GMS_DT_ALIAS


class GamsVariableType(Enum):
    Unknown = 0  # GMS_VARTYPE_UNKNOWN
    Binary = 1  # GMS_VARTYPE_BINARY
    Integer = 2  # GMS_VARTYPE_INTEGER
    Positive = 3  # GMS_VARTYPE_POSITIVE
    Negative = 4  # GMS_VARTYPE_NEGATIVE
    Free = 5  # GMS_VARTYPE_FREE
    SOS1 = 6  # GMS_VARTYPE_SOS1
    SOS2 = 7  # GMS_VARTYPE_SOS2
    Semicont = 8  # GMS_VARTYPE_SEMICONT
    Semiint = 9  # GMS_VARTYPE_SEMIINT


# Offset by 53 so the values don't collide with GamsVariableType's; the GMS_EQUTYPE_*
# codes themselves are 0..5.
class GamsEquationType(Enum):
    Equality = 53 + 0  # GMS_EQUTYPE_E
    GreaterThan = 53 + 1  # GMS_EQUTYPE_G
    LessThan = 53 + 2  # GMS_EQUTYPE_L
    NothingEnforced = 53 + 3  # GMS_EQUTYPE_N
    External = 53 + 4  # GMS_EQUTYPE_X
    Conic = 53 + 5  # GMS_EQUTYPE_C


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
    Level = 0  # GMS_VAL_LEVEL, .l
    Marginal = 1  # GMS_VAL_MARGINAL, .m
    Lower = 2  # GMS_VAL_LOWER, .lo
    Upper = 3  # GMS_VAL_UPPER, .ub
    Scale = 4  # GMS_VAL_SCALE, .scale

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
        alias_of: GdxSymbol | None = None,
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
        alias_of : None or :py:class:`GdxSymbol`
            Only for ``data_type == GamsDataType.Alias``: the parent Set this alias refers to,
            as a :py:class:`GdxSymbol` reference. The parent must exist in the same file at
            write time (an alias has no relaxed fallback). See :py:attr:`alias_of`.
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
        # Alias target: the parent Set, as a GdxSymbol ref (_alias_of) plus its
        # name (_alias_of_name), the latter surviving when the ref can't yet be
        # resolved (forward reference) or after a clone into another file.
        self._alias_of = None
        self._alias_of_name = None
        # Record count per GAMS; meaningful only before load (afterwards
        # num_records uses the dataframe). The engine's open_read overwrites
        # this for symbols read from a file.
        self._num_records = 0
        self.dims = dims
        if domain is not None:
            self.domain = domain
        if alias_of is not None:
            self.alias_of = alias_of
        assert self._dataframe is not None
        self._file = file
        self._index = index

        # A symbol constructed without a file is being built for writing and is
        # ready to use immediately. A symbol constructed with a file is being
        # read: the engine's open_read populates its extended metadata (record
        # count, variable/equation subtype, description, domain) and it stays
        # unloaded until its records are pulled.
        if self.file is None:
            self._loaded = True
        return

    def clone(self) -> GdxSymbol:
        """
        Create a copy of this :py:class:`GdxSymbol`.

        The clone is independent of any :py:class:`GdxFile` -- its ``file`` is
        ``None`` and it has no ``index``. Append it to a destination
        :py:class:`GdxFile` (e.g. via ``dest.append(cloned)``) before writing;
        for an Alias, also call ``resolve_alias_of()`` so the parent name is
        rebound to a same-file ref. :py:meth:`GdxFile.clone` does this wiring
        for every symbol it copies.

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
        #
        # For aliases, intentionally do NOT carry the live `alias_of` GdxSymbol ref --
        # it would still point at the *original* file's parent, which is wrong once the
        # clone is inserted elsewhere. Preserve only `alias_of_name`; the destination
        # file resolves it against its own symbols at write time.
        result = GdxSymbol(
            self.name,
            self.data_type,
            dims=self.dims,
            description=self.description,
            variable_type=self.variable_type,
            equation_type=self.equation_type,
            domain=self._domain,
        )
        result._alias_of_name = self.alias_of_name
        # An Alias has no records of its own -- its `.dataframe` is a view onto its
        # parent (see the dataframe getter). The view resolves after the clone is
        # inserted into a destination file and `resolve_alias_of()` rebinds the parent.
        if self.data_type != GamsDataType.Alias:
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
        if self.data_type in (GamsDataType.Set, GamsDataType.Alias):
            assert value_col == GamsValueType.Level
            # A Set/Alias value is its GAMS element text; "" means a member with
            # no text. Membership itself is conveyed by row presence.
            return ""
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
            if d.data_type not in (GamsDataType.Set, GamsDataType.Alias):
                # GDX's gdxSymbolSetDomain only accepts a Set or Alias-of-Set
                # in each domain slot, so the strict-write path would fail and
                # the engine would silently fall back to relaxed. Reject up front
                # to match alias_of's setter behavior (see #106). Both engines
                # accept Alias parents on write (verified by
                # tests/test_domain.py::test_domain_accepts_alias_parent).
                raise DomainError(
                    f"domain parent must be a Set (or an Alias-of-Set); "
                    f"{d.name!r} is a {d.data_type.name}."
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
    def alias_of(self):
        """
        For an :py:attr:`GamsDataType.Alias`, the parent symbol it refers to, as a
        :py:class:`GdxSymbol` reference; ``None`` for any other symbol type and for
        an alias whose parent could not be resolved against its file.

        The parent is typically a Set, but an Alias is also accepted (GDX itself
        supports chained aliases; the ``gdxcc`` engine preserves the chain on
        write, while ``gams_transfer`` flattens it to point at the root Set).

        Unlike :py:attr:`domain`, an alias has no relaxed fallback: the parent must
        exist in the same file when the alias is written, or the write raises
        :py:class:`DomainError`.

        Returns
        -------
        None or :py:class:`GdxSymbol`
        """
        return self._alias_of

    @alias_of.setter
    def alias_of(self, value):
        if value is None:
            self._alias_of = None
            self._alias_of_name = None
            return
        if self.data_type != GamsDataType.Alias:
            raise DomainError(
                f"alias_of may only be set on an Alias symbol; "
                f"{self.name!r} is a {self.data_type.name}."
            )
        if not isinstance(value, GdxSymbol):
            raise DomainError(
                "alias_of must be a GdxSymbol reference (the parent Set) or None. "
                f"Was passed {value!r} of type {type(value)}."
            )
        if value.data_type not in (GamsDataType.Set, GamsDataType.Alias):
            raise DomainError(
                f"alias_of parent must be a Set (or another Alias); "
                f"{value.name!r} is a {value.data_type.name}."
            )
        self._alias_of = value
        self._alias_of_name = value.name

    @property
    def alias_of_name(self):
        """Name of the parent Set this alias refers to (or ``None``). Survives when
        the :py:attr:`alias_of` reference is not yet resolved or after a clone
        into another file; the write path resolves the parent by this name."""
        if self._alias_of is not None:
            return self._alias_of.name
        return self._alias_of_name

    def resolve_alias_of(self):
        """
        Populate :py:attr:`alias_of` with a live :py:class:`GdxSymbol` reference by
        looking :py:attr:`alias_of_name` up in ``self.file``. Idempotent; no-ops when
        already resolved, when there is no file, or when no parent name is recorded.

        Mirrors :py:meth:`resolve_domain` for the forward-reference / post-read case
        (an alias whose parent has a higher GDX index than the alias itself). The
        universe set (``'*'``) resolves to the file's ``universal_set``.

        Returns
        -------
        bool
            True if ``alias_of`` was populated as a result of this call.
        """
        if self._alias_of is not None:
            return False
        if self.file is None or self._alias_of_name is None:
            return False
        name = self._alias_of_name
        if name in self.file._symbols:
            self._alias_of = self.file._symbols[name]
            return True
        if self.file.universal_set is not None and name == self.file.universal_set.name:
            self._alias_of = self.file.universal_set
            return True
        logger.warning(
            "resolve_alias_of: alias %r references parent %r which is not in "
            "this file; leaving it unresolved.",
            self.name,
            name,
        )
        return False

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

        For an :py:attr:`GamsDataType.Alias` with a resolved :py:attr:`alias_of`, the
        returned frame is the parent's own ``dataframe`` (a live view, not a copy) --
        an alias has no records of its own. Mutate the parent to change what an alias
        reads; direct assignment to an alias's ``dataframe`` raises.

        Returns
        -------
        pd.DataFrame
        """
        if self.data_type == GamsDataType.Alias and self._alias_of is not None:
            return self._alias_of.dataframe
        return self._dataframe

    @dataframe.setter
    def dataframe(self, data):
        if self.data_type == GamsDataType.Alias:
            raise Error(
                f"Cannot assign to {self.name!r}.dataframe: an Alias has no records "
                "of its own -- its dataframe is a read-only view of its parent "
                "(alias_of). Mutate the parent's dataframe instead."
            )
        try:
            # get data in common format and start dealing with dimensions
            if isinstance(data, pd.DataFrame):
                # Shallow copy on pandas 3 (CoW always on): the stored frame
                # stays independent of caller-side mutations because any write
                # to ``data`` triggers CoW. On pandas < 3 a shallow copy would
                # share column storage with the caller, breaking the
                # assignment-captures-a-snapshot contract -- fall back to deep.
                df = data.copy(deep=not _PANDAS_HAS_COW)
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

        if self.data_type in (GamsDataType.Set, GamsDataType.Alias):
            self._fixup_set_value()
        return

    def _init_dataframe(self):
        self._dataframe = pd.DataFrame([], columns=self.dims + self.value_col_names)
        if self.data_type in (GamsDataType.Set, GamsDataType.Alias):
            # A Set/Alias value column holds element-text strings ("" = no text).
            colname = self._dataframe.columns[-1]
            replace_df_column(self._dataframe, colname, self._dataframe[colname].astype(str))
        return

    def _append_default_values(self, df):
        assert len(df.columns) == self.num_dims
        logger.debug(f"Applying default values to create valid dataframe for '{self.name}'.")
        for value_col_name in self.value_col_names:
            df[value_col_name] = self.get_value_col_default(value_col_name)

    def _fixup_set_value(self):
        """
        Normalize a Set/Alias value column to its canonical element-text form: one
        string per row, where ``""`` denotes a member with no text. Membership is
        conveyed by row presence, so any non-string value -- a boolean or ``c_bool``
        membership flag, a missing value, etc. -- maps to ``""``; existing strings
        are kept verbatim. This lets callers build a Set from dimension columns
        alone, from booleans, or from text, and get a consistent representation.
        """
        assert self.data_type in (GamsDataType.Set, GamsDataType.Alias)

        colname = self._dataframe.columns[-1]
        assert colname == self.value_col_names[0], (
            f"Unexpected final column {colname!r} in Set dataframe"
        )
        replace_df_column(
            self._dataframe,
            colname,
            self._dataframe[colname].map(lambda v: v if isinstance(v, str) else ""),
        )
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

    def load(self) -> None:
        """
        Loads this :py:class:`GdxSymbol` from its :py:attr:`file`, thereby popluating
        :py:attr:`dataframe`.

        For an Alias, the parent's records are what the alias's ``dataframe`` view
        surfaces, so the parent is loaded first (if not already).
        """
        if self.loaded:
            logger.info("Nothing to do. Symbol already loaded.")
            return
        if not self.file:
            raise Error(f"Cannot load {repr(self)} because there is no file pointer")
        if self.data_type == GamsDataType.Alias and self._alias_of is not None:
            # The alias has no records of its own; ensure the parent (which owns
            # the data the alias's view will surface) is loaded too.
            self._alias_of.load()
        # The engine reads the records and populates this symbol's dataframe.
        # The "no symbol index" guard now lives in the gdxcc engine, which is
        # the path that needs it.
        self.file._engine_impl.load_symbol(self)
        return

    def unload(self) -> None:
        """
        Drops this :py:class:`GdxSymbol`'s :py:attr:`dataframe`. For an Alias the
        dataframe is a view onto the parent's, so unload only flips the loaded
        flag (the parent's data is untouched).
        """
        if self.data_type != GamsDataType.Alias:
            self.dataframe = None
        self._loaded = False

    def write(self, index: int | None = None, name_positions: dict | None = None) -> None:
        """
        Writes this :py:class:`GdxSymbol` to its :py:attr:`file`
        """
        if self.file is None:
            raise Error(f"Cannot write {self!r} because there is no file pointer")
        self.file._engine_impl.write_symbol(
            self.file, self, index=index, name_positions=name_positions
        )


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


def append_alias(
    gdx_file: GdxFile,
    alias_name: str,
    parent: GdxSymbol | str,
) -> GdxSymbol:
    """
    Convenience function that appends ``alias_name`` to ``gdx_file`` as a
    :class:`GamsDataType.Alias <GamsDataType>` of ``parent``.

    Parameters
    ----------
    gdx_file : :class:`GdxFile`
        file to which the new alias is to be added
    alias_name : str
        name of the alias to be added
    parent : :class:`GdxSymbol` or str
        the parent symbol the alias refers to, as a :class:`GdxSymbol` reference or
        the name of a symbol already in ``gdx_file``. The parent is typically a Set
        but an Alias is also accepted (GDX supports chained aliases; the gdxcc
        engine preserves the chain on write, gams_transfer flattens it to the
        root Set). An unknown name, a non-:class:`GdxSymbol` parent, or a parent
        that is neither a Set nor an Alias raises :class:`DomainError` (an alias
        has no relaxed fallback).

    Returns
    -------
    :class:`GdxSymbol`
        The freshly appended alias symbol.
    """
    if not isinstance(alias_name, str):
        raise DomainError(
            f"append_alias: alias_name must be a str; got {type(alias_name).__name__}"
        )
    if isinstance(parent, str):
        if parent not in gdx_file:
            raise DomainError(f"append_alias: parent {parent!r} is not in the file")
        parent = gdx_file[parent]
    if not isinstance(parent, GdxSymbol):
        raise DomainError(
            f"append_alias: parent must be a GdxSymbol or a symbol name; "
            f"got {type(parent).__name__}"
        )
    if parent.data_type not in (GamsDataType.Set, GamsDataType.Alias):
        raise DomainError(
            f"append_alias: parent {parent.name!r} is a {parent.data_type.name}; "
            "must be a Set or Alias"
        )
    gdx_file.append(GdxSymbol(alias_name, GamsDataType.Alias, dims=parent.dims, alias_of=parent))
    return gdx_file[-1]
