import logging
from numbers import Number

from gdxpds.tools import Error
from gdxpds.gdx import (
    GdxFile,
    GdxSymbol,
    GAMS_VALUE_COLS_MAP,
    GamsDataType,
    DomainError,
    _stable_topological_sort,
)

import pandas as pd

logger = logging.getLogger(__name__)

class Translator(object):
    def __init__(self,dataframes,gams_dir=None,domains=None):
        self.dataframes = dataframes
        self.__domains = domains
        self.__gams_dir=gams_dir
        self.__gdx = None

    def __exit__(self, *args):
        if self.__gdx is not None:
            self.__gdx.__exit__(self, *args)

    def __del__(self):
        if self.__gdx is not None:
            self.__gdx.__del__()

    @property
    def dataframes(self):
        return self.__dataframes

    @dataframes.setter
    def dataframes(self,value):
        err_msg = "Expecting map of name, pandas.DataFrame pairs."
        try:
            for symbol_name, df in value.items():
                if not isinstance(symbol_name, str): raise Error(err_msg)
                if not isinstance(df, pd.DataFrame): raise Error(err_msg)
        except AttributeError: raise Error(err_msg)
        self.__dataframes = value
        self.__gdx = None

    @property
    def domains(self):
        return self.__domains

    @property
    def gams_dir(self):
        return self.__gams_dir

    @gams_dir.setter
    def gams_dir(self, value):
        self.__gams_dir = value

    @property
    def gdx(self):
        if self.__gdx is None:
            domains = self.__domains
            gdx_file = GdxFile(gams_dir=self.__gams_dir)
            dataframes = (
                self.__topo_sort_dataframes(self.dataframes, domains)
                if domains is not None
                else self.dataframes
            )
            self.__gdx = gdx_file
            for symbol_name, df in dataframes.items():
                self.__add_symbol_to_gdx(symbol_name, df)
            if domains is not None:
                self.__wire_domains(domains, gdx_file)
        return self.__gdx

    def save_gdx(self,path,gams_dir=None):
        if gams_dir is not None:
            self.__gams_dir=gams_dir
        self.gdx.write(path)

    def __add_symbol_to_gdx(self, symbol_name, df):
        data_type, num_dims = self.__infer_data_type(symbol_name,df)
        logger.info("Inferred data type of {} to be {}.".format(symbol_name,data_type.name))

        self.__gdx.append(GdxSymbol(symbol_name,data_type,dims=num_dims))
        self.__gdx[symbol_name].dataframe = df
        return

    @staticmethod
    def __topo_sort_dataframes(dataframes, domains):
        """
        Validate ``domains`` against ``dataframes`` and return a dict with the same items
        as ``dataframes`` reordered so every parent precedes its children. Stable: items
        with no mutual dependency keep their original relative order.

        Raises :class:`DomainError` for any structural problem: unknown child or parent
        name, wrong type for ``parents``, malformed entry (non-str non-None), or a cycle.
        """
        names = list(dataframes.keys())
        parents_of = {}
        for child_name, parents in domains.items():
            if child_name not in dataframes:
                raise DomainError(
                    f"to_gdx: domains key {child_name!r} is not in dataframes"
                )
            if not isinstance(parents, (list, tuple)):
                raise DomainError(
                    f"to_gdx: domains[{child_name!r}] must be a list or tuple, "
                    f"got {type(parents).__name__}"
                )
            ps = set()
            for p in parents:
                if p is None or p == child_name:
                    continue
                if not isinstance(p, str):
                    raise DomainError(
                        f"to_gdx: domains[{child_name!r}] entries must be "
                        f"str or None; got {type(p).__name__} {p!r}"
                    )
                if p not in dataframes:
                    raise DomainError(
                        f"to_gdx: domains[{child_name!r}] references unknown parent {p!r}"
                    )
                ps.add(p)
            parents_of[child_name] = ps

        ordered, cycle = _stable_topological_sort(names, parents_of)
        if cycle is not None:
            raise DomainError(f"to_gdx: cyclic domain references among {sorted(cycle)}")
        if ordered is None:
            return dataframes
        return {n: dataframes[n] for n in ordered}

    @staticmethod
    def __wire_domains(domains, gdx_file):
        """Resolve each ``domains`` entry against the materialized symbols and assign
        ``GdxSymbol.domain`` so subsequent writes take the strict
        :c:func:`gdxSymbolSetDomain` path. Length-vs-``num_dims`` is checked here
        because it needs the constructed symbol."""
        for child_name, parents in domains.items():
            child = gdx_file[child_name]
            if len(parents) != child.num_dims:
                raise DomainError(
                    f"to_gdx: domains[{child_name!r}] has length "
                    f"{len(parents)} but symbol has {child.num_dims} dims"
                )
            child.domain = [
                None if p is None else gdx_file[p] for p in parents
            ]

    def __infer_data_type(self,symbol_name,df):
        """
        Returns
        -------
        (gdxpds.GamsDataType, int)
            symbol type and number of dimensions implied by df
        """
        # See if structure implies that symbol_name may be a Variable or an Equation
        # If so, break tie based on naming convention--Variables start with upper case, 
        # equations start with lower case
        var_or_eqn = False        
        df_col_names = df.columns
        var_eqn_col_names = [col_name for col_name, col_ind in GAMS_VALUE_COLS_MAP[GamsDataType.Variable]]
        if len(df_col_names) >= len(var_eqn_col_names):
            # might be variable or equation
            var_or_eqn = True
            trunc_df_col_names = df_col_names[len(df_col_names) - len(var_eqn_col_names):]
            for i, df_col in enumerate(trunc_df_col_names):
                if df_col and (df_col.lower() != var_eqn_col_names[i].lower()):
                    var_or_eqn = False
                    break
            if var_or_eqn:
                num_dims = len(df_col_names) - len(var_eqn_col_names)
                if symbol_name[0].upper() == symbol_name[0]:
                    return GamsDataType.Variable, num_dims
                else:
                    return GamsDataType.Equation, num_dims

        # Parameter or set
        num_dims = len(df_col_names) - 1
        if len(df.index) > 0:
            if isinstance(df.iloc[0,-1],Number):
                return GamsDataType.Parameter, num_dims
        return GamsDataType.Set, num_dims


def to_gdx(dataframes,path=None,gams_dir=None,domains=None):
    """
    Creates a :py:class:`gdxpds.gdx.GdxFile` from dataframes and optionally writes it to path

    Parameters
    ----------
    dataframes : dict of str to pd.DataFrame
        symbol name to pd.DataFrame dict to be compiled into a single gdx file. Each DataFrame
        is assumed to represent a single set or parameter. The last column must be the parameter's
        value, or the set's listing of True/False, and must be labeled as (case insensitive)
        'value'.
    path : None or pathlib.Path or str
        If provided, the gdx file will be written to this path
    gams_dir : None or pathlib.Path or str
    domains : None or dict of str to (list or tuple) of (str or None)
        Optional subset/domain relationships, string-based. Each entry maps a child symbol's name
        to a list or tuple of its parent Set names, one per dimension, with ``None`` slots
        mapping to the GAMS wildcard (``'*'``). When provided, the resulting :class:`Translator`
        (1) topologically sorts ``dataframes`` so each parent precedes its children and (2)
        wires up strict :c:func:`gdxSymbolSetDomain` writes for each listed child. Any invalid
        input (unknown parent name, wrong type, wrong length, cyclic references) raises
        :class:`DomainError`.

    Returns
    -------
    :py:class:`gdxpds.gdx.GdxFile`
    """
    translator = Translator(dataframes, gams_dir=gams_dir, domains=domains)
    gdx = translator.gdx
    if path is not None:
        gdx.write(path)
    return gdx

