from collections import OrderedDict
import logging

# gdxpds needs to be imported before pandas to try to avoid library conflict on 
# Linux that causes a segmentation fault.
from gdxpds.tools import Error
from gdxpds.gdx import GdxFile, GamsDataType

logger = logging.getLogger(__name__)

class Translator(object):
    def __init__(self,gdx_file,gams_dir=None,lazy_load=False):
        self.__gdx = GdxFile(gams_dir=gams_dir,lazy_load=lazy_load)
        self.__gdx.read(gdx_file)
        self.__dataframes = None

    def __exit__(self, *args):
        self.__gdx.__exit__(self, *args)

    def __del__(self):
        self.__gdx.__del__()

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
    def gdx_file(self,value):
        self.__gdx.__del__()
        self.__gdx = GdxFile(gams_dir=self.gdx.gams_dir,lazy_load=self.gdx.lazy_load)
        self.__gdx.read(value)
        self.__dataframes = None

    @property
    def gdx(self):
        return self.__gdx

    @property
    def dataframes(self):
        if self.__dataframes is None:
            self.__dataframes = OrderedDict()
            for symbol in self.gdx:
                if not symbol.loaded:
                    symbol.load()
                self.__dataframes[symbol.name] = symbol.dataframe.copy()
        return self.__dataframes

    @property
    def symbols(self):
        return [symbol.name for symbol in self.gdx]
    
    @property
    def data_types(self):
        return {symbol.name: symbol.data_type for symbol in self.gdx}

    def dataframe(self, symbol_name, load_set_text=False):
        if not symbol_name in self.gdx:
            raise Error("No symbol named '{}' in '{}'.".format(symbol_name, self.gdx_file))
        if not self.gdx[symbol_name].loaded:
            self.gdx[symbol_name].load(load_set_text=load_set_text)
        # This was returning { symbol_name: dataframe }, which seems intuitively off.
        return self.gdx[symbol_name].dataframe.copy()
    
    def _get_dataframes(self, load_set_text=False):
        if self.__dataframes is None:
            self.__dataframes = OrderedDict()
            for symbol in self.__gdx:
                if not symbol.loaded:
                    symbol.load(load_set_text=load_set_text)
                self.__dataframes[symbol.name] = symbol.dataframe.copy()
        return self.__dataframes
    

def to_dataframes(gdx_file,gams_dir=None,load_set_text=False):
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
    if load_set_text:
        return Translator(gdx_file,gams_dir=gams_dir,lazy_load=True)._get_dataframes(load_set_text=load_set_text)
    return Translator(gdx_file,gams_dir=gams_dir).dataframes


def list_symbols(gdx_file,gams_dir=None):
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
    return Translator(gdx_file,gams_dir=gams_dir,lazy_load=True).symbols


def get_data_types(gdx_file,gams_dir=None):
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
    return Translator(gdx_file,gams_dir=gams_dir,lazy_load=True).data_types



def to_dataframe(gdx_file,symbol_name,gams_dir=None,old_interface=True,load_set_text=False):
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
    old_interface : bool
        Whether to use the old interface and return a dict, or the new interface, 
        and simply return a pd.DataFrame
    load_set_text : bool
        If True (default is False) and symbol_name is a Set, loads the GDX Text 
        field into the dataframe rather than a `c_bool`.
    
    Returns
    -------
    dict of str to pd.DataFrame OR pd.DataFrame
        If old_interface (the default), returns a dict with a single entry, 
        where the key is symbol_name and the value is the corresponding 
        pd.DataFrame. Otherwise (if not old_interface), returns just the 
        pd.DataFrame.
    """
    df = Translator(gdx_file,gams_dir=gams_dir,lazy_load=True).dataframe(
        symbol_name,
        load_set_text=load_set_text)
    return {symbol_name: df} if old_interface else df
