import logging
import os

import pytest

import gdxpds.gdx
from gdxpds import to_dataframes, list_symbols, get_data_types

logger = logging.getLogger(__name__)

def test_read(base_dir):
    filename = 'all_generator_properties_input.gdx'
    gdx_file = os.path.join(base_dir,filename)
    with gdxpds.gdx.GdxFile() as f:
        f.read(gdx_file)
        for symbol in f:
            symbol.load()

def test_read_none():
    with pytest.raises(gdxpds.gdx.GdxError) as excinfo:
        to_dataframes(None)
    assert "Could not open None" in str(excinfo.value)

def test_read_path(base_dir):
    filename = 'all_generator_properties_input.gdx'
    from pathlib import Path
    gdx_file = Path(base_dir) / filename
    
    symbol_names = list_symbols(gdx_file)
    n = len(symbol_names) 
    assert isinstance(symbol_names[0], str)
    assert n == 7
    
    dfs = to_dataframes(gdx_file)
    assert len(dfs) == n

    # data frames are loaded in order
    for i, symbol_name in enumerate(dfs):
        assert symbol_names[i] == symbol_name

    dtypes = get_data_types(gdx_file)
    # this file is all parameters
    for val in dtypes.values():
        val == gdxpds.gdx.GamsDataType.Parameter

def test_unload(base_dir):
    filename = 'all_generator_properties_input.gdx'
    gdx_file = os.path.join(base_dir,filename)
    with gdxpds.gdx.GdxFile() as f:
        f.read(gdx_file)
        assert not f['startupfuel'].loaded
        assert f['startupfuel'].dataframe.empty

        f['startupfuel'].load()
        assert f['startupfuel'].loaded
        assert not f['startupfuel'].dataframe.empty
        assert 'CC' in f['startupfuel'].dataframe['*'].tolist()

        f['startupfuel'].unload()
        assert not f['startupfuel'].loaded
        assert f['startupfuel'].dataframe.empty
        
        f['startupfuel'].load()
        assert f['startupfuel'].loaded
        assert not f['startupfuel'].dataframe.empty
        assert 'CC' in f['startupfuel'].dataframe['*'].tolist()
