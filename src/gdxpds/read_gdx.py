from __future__ import annotations

import logging
import os
from collections import OrderedDict
from typing import TYPE_CHECKING

import pandas as pd

from gdxpds.gdx import GamsDataType, GdxFile, SymbolNotFoundError

if TYPE_CHECKING:
    from gdxpds._engine import Engine

logger = logging.getLogger(__name__)


class Translator:
    def __init__(self, gdx_file, gams_dir=None, lazy_load=False, engine=None):
        self.__gdx = GdxFile(gams_dir=gams_dir, lazy_load=lazy_load, engine=engine)
        self.__gdx.read(gdx_file)
        self.__dataframes = None

    def __exit__(self, *args):
        self.__gdx.__exit__(*args)

    @property
    def gams_dir(self):
        return self.gdx.gams_dir

    @gams_dir.setter
    def gams_dir(self, value):
        self.gdx.gams_dir = value

    @property
    def gdx_file(self):
        return self.gdx.filename

    @gdx_file.setter
    def gdx_file(self, value):
        self.__gdx.cleanup()
        self.__gdx = GdxFile(
            gams_dir=self.gdx.gams_dir,
            lazy_load=self.gdx.lazy_load,
            engine=self.gdx._engine_kind,
        )
        self.__gdx.read(value)
        self.__dataframes = None

    @property
    def gdx(self):
        return self.__gdx

    @property
    def dataframes(self):
        return self._get_dataframes()

    @property
    def symbols(self):
        return [symbol.name for symbol in self.gdx]

    @property
    def data_types(self):
        return {symbol.name: symbol.data_type for symbol in self.gdx}

    def dataframe(self, symbol_name):
        if symbol_name not in self.gdx:
            raise SymbolNotFoundError(f"No symbol named '{symbol_name}' in '{self.gdx_file}'.")
        if not self.gdx[symbol_name].loaded:
            self.gdx[symbol_name].load()
        # This was returning { symbol_name: dataframe }, which seems intuitively off.
        return self.gdx[symbol_name].dataframe.copy()

    def _get_dataframes(self, symbols=None):
        # One eager load, then collect a copy of each symbol's dataframe.
        # `symbols=None` loads/returns every symbol in file order; a list
        # loads/returns only those, in the given order. Engines optimize the
        # load: gdxcc loops per symbol; gams.transfer does a single (bulk or
        # targeted) read.
        if self.__dataframes is None:
            if symbols is None:
                self.__gdx.load_all()
                names = [symbol.name for symbol in self.__gdx]
            else:
                self.__gdx.load_symbols(symbols)
                names = list(symbols)
            self.__dataframes = OrderedDict(
                (name, self.__gdx[name].dataframe.copy()) for name in names
            )
        return self.__dataframes


def to_dataframes(
    gdx_file: str | os.PathLike[str],
    gams_dir: str | os.PathLike[str] | None = None,
    engine: str | Engine | None = None,
    symbols: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Primary interface for converting a GAMS GDX file to pandas DataFrames.

    Parameters
    ----------
    gdx_file : pathlib.Path or str
        Path to the GDX file to read
    gams_dir : None or pathlib.Path or str
        optional path to GAMS directory
    engine : None or str or :py:class:`gdxpds.Engine`
        Which I/O engine to use. ``None`` (default) resolves via the
        ``GDXPDS_ENGINE`` env var, then the default engine (``gams.transfer``
        when usable, otherwise ``gdxcc``).
    symbols : None or list of str
        If None (default), every symbol is read. Otherwise only the named
        symbols are read and returned, in the given order; an unknown name
        raises :class:`~gdxpds.gdx.SymbolNotFoundError` and ``[]`` returns an
        empty dict.

    Returns
    -------
    dict of str to pd.DataFrame
        Returns a dict of Pandas DataFrames, one item for each requested symbol
        in the GDX file, keyed with the symbol name. For a Set or Alias, the
        value column holds the GAMS element text (``""`` for a member with no
        text); membership is conveyed by row presence.
    """
    return Translator(gdx_file, gams_dir=gams_dir, lazy_load=True, engine=engine)._get_dataframes(
        symbols=symbols
    )


def list_symbols(
    gdx_file: str | os.PathLike[str],
    gams_dir: str | os.PathLike[str] | None = None,
    engine: str | Engine | None = None,
) -> list[str]:
    """
    Returns the list of symbols available in gdx_file.

    Parameters
    ----------
    gdx_file : pathlib.Path or str
        Path to the GDX file to read
    gams_dir : None or pathlib.Path or str
        optional path to GAMS directory
    engine : None or str or :py:class:`gdxpds.Engine`
        Which I/O engine to use (default resolves via ``GDXPDS_ENGINE``).

    Returns
    -------
    list of str
        List of symbol names
    """
    return Translator(gdx_file, gams_dir=gams_dir, lazy_load=True, engine=engine).symbols


def get_data_types(
    gdx_file: str | os.PathLike[str],
    gams_dir: str | os.PathLike[str] | None = None,
    engine: str | Engine | None = None,
) -> dict[str, GamsDataType]:
    """
    Returns a dict of the symbols' :py:class:`GamsDataTypes <GamsDataType>`.

    Parameters
    ----------
    gdx_file : pathlib.Path or str
        Path to the GDX file to read
    gams_dir : None or pathlib.Path or str
        optional path to GAMS directory
    engine : None or str or :py:class:`gdxpds.Engine`
        Which I/O engine to use (default resolves via ``GDXPDS_ENGINE``).

    Returns
    -------
    dict of str to :py:class:`GamsDataType`
        Map of symbol names to the corresponding :py:class:`GamsDataType`
    """
    return Translator(gdx_file, gams_dir=gams_dir, lazy_load=True, engine=engine).data_types


def get_subset_relationships(
    gdx_file: str | os.PathLike[str],
    gams_dir: str | os.PathLike[str] | None = None,
    engine: str | Engine | None = None,
) -> dict[str, list[str | None]]:
    """
    Returns the domain relationships recorded in ``gdx_file``, keyed by symbol name.

    Any symbol type (Set, Parameter, Variable, Equation) can carry a domain -- a Set's
    domain is a *subset* relationship, a Parameter/Variable/Equation's is an *indexed-over*
    relationship -- and the parent named in each slot is a Set or Alias-of-Set.

    Outputs a dict that maps each symbol name to a list with one entry per dimension, giving the
    parent Set name recorded for that dimension. A dimension whose domain is the wildcard
    (``'*'``), or for which the GDX file records no domain information at all, comes through
    as ``None``. Every other dimension is reported by its recorded name verbatim, including the
    self-referential case where a (typically root) Set's dimension names the Set itself.

    The length of each list matches the symbol's number of dimensions, and names appear in
    dimension order. The output shape matches the ``domains=`` argument of :func:`to_gdx`, so a
    value read here can be fed straight back in (``None`` round-trips as the wildcard).

    Parameters
    ----------
    gdx_file : pathlib.Path or str
        Path to the GDX file to read
    gams_dir : None or pathlib.Path or str
        optional path to GAMS directory
    engine : None or str or :py:class:`gdxpds.Engine`
        Which I/O engine to use (default resolves via ``GDXPDS_ENGINE``).

    Returns
    -------
    dict of str to list of (str or None)
        Map of symbol name to its domain. Pair this with :func:`to_dataframes` to recover the full
        file shape.
    """
    result = OrderedDict()
    gdx = GdxFile(gams_dir=gams_dir, lazy_load=True, engine=engine)
    gdx.read(gdx_file)
    for symbol in gdx:
        if symbol.domain is not None:
            result[symbol.name] = [d.name if d is not None else None for d in symbol.domain]
        else:
            result[symbol.name] = [None if d == "*" else d for d in symbol.dims]
    return result


def get_aliases(
    gdx_file: str | os.PathLike[str],
    gams_dir: str | os.PathLike[str] | None = None,
    engine: str | Engine | None = None,
) -> dict[str, str]:
    """
    Returns the alias relationships recorded in ``gdx_file``, keyed by alias name.

    The output shape matches the ``aliases=`` argument of :func:`to_gdx`, so a value read
    here can be fed straight back in. Each entry maps an alias's name to the name of its
    parent symbol -- typically a Set, but a chained alias parent is possible (GDX permits
    a chain, and the ``gdxcc`` engine preserves it on disk; ``gams.transfer`` flattens
    to the root Set on write). The universe set (``'*'``) is reported verbatim for a
    universe alias.

    Symbols that are not aliases are not included in the output.

    Parameters
    ----------
    gdx_file : pathlib.Path or str
        Path to the GDX file to read
    gams_dir : None or pathlib.Path or str
        optional path to GAMS directory
    engine : None or str or :py:class:`gdxpds.Engine`
        Which I/O engine to use (default resolves via ``GDXPDS_ENGINE``).

    Returns
    -------
    dict of str to str
        Map of alias name to parent symbol name. Pair this with :func:`to_dataframes`
        and :func:`get_subset_relationships` to recover the full file shape.
    """
    result = OrderedDict()
    gdx = GdxFile(gams_dir=gams_dir, lazy_load=True, engine=engine)
    gdx.read(gdx_file)
    for symbol in gdx:
        if symbol.data_type == GamsDataType.Alias and symbol.alias_of_name is not None:
            result[symbol.name] = symbol.alias_of_name
    return result


def to_dataframe(
    gdx_file: str | os.PathLike[str],
    symbol_name: str,
    gams_dir: str | os.PathLike[str] | None = None,
    engine: str | Engine | None = None,
) -> pd.DataFrame:
    """
    Interface for getting the data for a single symbol

    Parameters
    ----------
    gdx_file : pathlib.Path or str
        Path to the GDX file to read
    symbol_name : str
        Name of the symbol whose data are to be read. An unknown name raises
        :class:`~gdxpds.gdx.SymbolNotFoundError`.
    gams_dir : None or pathlib.Path or str
        optional path to GAMS directory
    engine : None or str or :py:class:`gdxpds.Engine`
        Which I/O engine to use (default resolves via ``GDXPDS_ENGINE``).

    Returns
    -------
    pd.DataFrame
        The data for symbol_name as a pandas DataFrame. For a Set or Alias, the
        value column holds the GAMS element text (``""`` for a member with no
        text); membership is conveyed by row presence.
    """
    return Translator(gdx_file, gams_dir=gams_dir, lazy_load=True, engine=engine).dataframe(
        symbol_name
    )
