from __future__ import annotations

import logging
import os
from collections import OrderedDict

import pandas as pd

from gdxpds.gdx import GamsDataType, GdxFile
from gdxpds.tools import Error

logger = logging.getLogger(__name__)


class Translator:
    def __init__(self, gdx_file, gams_dir=None, lazy_load=False):
        self.__gdx = GdxFile(gams_dir=gams_dir, lazy_load=lazy_load)
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
        self.__gdx = GdxFile(gams_dir=self.gdx.gams_dir, lazy_load=self.gdx.lazy_load)
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

    def dataframe(self, symbol_name, load_set_text=False):
        if symbol_name not in self.gdx:
            raise Error(f"No symbol named '{symbol_name}' in '{self.gdx_file}'.")
        if not self.gdx[symbol_name].loaded:
            self.gdx[symbol_name].load(load_set_text=load_set_text)
        # This was returning { symbol_name: dataframe }, which seems intuitively off.
        return self.gdx[symbol_name].dataframe.copy()

    def _get_dataframes(self, load_set_text=False):
        # One eager bulk load (set text is just a parameter), then collect a copy
        # of each symbol's dataframe in file order. Backends optimize load_all:
        # gdxcc loops per symbol; gams.transfer does a single bulk read.
        if self.__dataframes is None:
            self.__gdx.load_all(load_set_text=load_set_text)
            self.__dataframes = OrderedDict(
                (symbol.name, symbol.dataframe.copy()) for symbol in self.__gdx
            )
        return self.__dataframes


def to_dataframes(
    gdx_file: str | os.PathLike[str],
    gams_dir: str | os.PathLike[str] | None = None,
    load_set_text: bool = False,
) -> dict[str, pd.DataFrame]:
    """
    Primary interface for converting a GAMS GDX file to pandas DataFrames.

    Parameters
    ----------
    gdx_file : pathlib.Path or str
        Path to the GDX file to read
    gams_dir : None or pathlib.Path or str
        optional path to GAMS directory
    load_set_text : bool
        If True (default is False), then for every symbol that is a Set, loads
        the GDX Text field into the dataframe rather than a `c_bool`.

    Returns
    -------
    dict of str to pd.DataFrame
        Returns a dict of Pandas DataFrames, one item for each symbol in the GDX
        file, keyed with the symbol name.
    """
    return Translator(gdx_file, gams_dir=gams_dir, lazy_load=True)._get_dataframes(
        load_set_text=load_set_text
    )


def list_symbols(
    gdx_file: str | os.PathLike[str],
    gams_dir: str | os.PathLike[str] | None = None,
) -> list[str]:
    """
    Returns the list of symbols available in gdx_file.

    Parameters
    ----------
    gdx_file : pathlib.Path or str
        Path to the GDX file to read
    gams_dir : None or pathlib.Path or str
        optional path to GAMS directory

    Returns
    -------
    list of str
        List of symbol names
    """
    return Translator(gdx_file, gams_dir=gams_dir, lazy_load=True).symbols


def get_data_types(
    gdx_file: str | os.PathLike[str],
    gams_dir: str | os.PathLike[str] | None = None,
) -> dict[str, GamsDataType]:
    """
    Returns a dict of the symbols' :py:class:`GamsDataTypes <GamsDataType>`.

    Parameters
    ----------
    gdx_file : pathlib.Path or str
        Path to the GDX file to read
    gams_dir : None or pathlib.Path or str
        optional path to GAMS directory

    Returns
    -------
    dict of str to :py:class:GamsDataType`
        Map of symbol names to the corresponding :py:class:GamsDataType`
    """
    return Translator(gdx_file, gams_dir=gams_dir, lazy_load=True).data_types


def get_subset_relationships(
    gdx_file: str | os.PathLike[str],
    gams_dir: str | os.PathLike[str] | None = None,
) -> dict[str, list[str | None]]:
    """
    Returns the subset (domain) relationships recorded in ``gdx_file``, keyed by symbol name.

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

    Returns
    -------
    dict of str to list of (str or None)
        Map of symbol name to its domain. Pair this with :func:`to_dataframes` to recover the full
        file shape.
    """
    result = OrderedDict()
    gdx = GdxFile(gams_dir=gams_dir, lazy_load=True)
    gdx.read(gdx_file)
    for symbol in gdx:
        if symbol.domain is not None:
            result[symbol.name] = [d.name if d is not None else None for d in symbol.domain]
        else:
            result[symbol.name] = [None if d == "*" else d for d in symbol.dims]
    return result


def to_dataframe(
    gdx_file: str | os.PathLike[str],
    symbol_name: str,
    gams_dir: str | os.PathLike[str] | None = None,
    load_set_text: bool = False,
) -> pd.DataFrame:
    """
    Interface for getting the data for a single symbol

    Parameters
    ----------
    gdx_file : pathlib.Path or str
        Path to the GDX file to read
    symbol_name : str
        Name of the symbol whose data are to be read
    gams_dir : None or pathlib.Path or str
        optional path to GAMS directory
    load_set_text : bool
        If True (default is False) and symbol_name is a Set, loads the GDX Text
        field into the dataframe rather than a `c_bool`.

    Returns
    -------
    pd.DataFrame
        The data for symbol_name as a pandas DataFrame.
    """
    return Translator(gdx_file, gams_dir=gams_dir, lazy_load=True).dataframe(
        symbol_name, load_set_text=load_set_text
    )
