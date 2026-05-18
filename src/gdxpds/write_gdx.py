import logging
from numbers import Number

# gdxpds needs to be imported before pandas to try to avoid library conflict on 
# Linux that causes a segmentation fault.
from gdxpds.tools import Error
from gdxpds.gdx import GdxFile, GdxSymbol, GAMS_VALUE_COLS_MAP, GamsDataType

import pandas as pd

logger = logging.getLogger(__name__)

class Translator(object):
    def __init__(self,dataframes,gams_dir=None):
        self.dataframes = dataframes
        self.__gams_dir=None

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
    def gams_dir(self):
        return self.__gams_dir

    @gams_dir.setter
    def gams_dir(self, value):
        self.__gams_dir = value

    @property
    def gdx(self):
        if self.__gdx is None:
            self.__gdx = GdxFile(gams_dir=self.__gams_dir)
            for symbol_name, df in self.dataframes.items():
                self.__add_symbol_to_gdx(symbol_name, df)
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


def to_gdx(dataframes,path=None,gams_dir=None):
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

    Returns
    -------
    :py:class:`gdxpds.gdx.GdxFile`
    """
    translator = Translator(dataframes,gams_dir=gams_dir)
    if path is not None:
        translator.save_gdx(path)
    return translator.gdx

