import logging
import os

import numpy as np
import pandas as pd
import pytest

import gdxpds.gdx
import gdxpds.special
from gdxpds import get_data_types, list_symbols, to_dataframe, to_dataframes

logger = logging.getLogger(__name__)


def test_read(data_dir):
    filename = "all_generator_properties_input.gdx"
    gdx_file = os.path.join(data_dir, filename)
    with gdxpds.gdx.GdxFile() as f:
        f.read(gdx_file)
        for symbol in f:
            symbol.load()


def test_read_none():
    # The error type is engine-specific (GdxError vs TransferError), but both
    # subclass gdxpds.Error and name the offending path.
    with pytest.raises(gdxpds.Error) as excinfo:
        to_dataframes(None)
    assert "None" in str(excinfo.value)


def test_read_path(data_dir):
    filename = "all_generator_properties_input.gdx"
    from pathlib import Path

    gdx_file = Path(data_dir) / filename

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
        assert val == gdxpds.gdx.GamsDataType.Parameter


def test_unload(data_dir):
    filename = "all_generator_properties_input.gdx"
    gdx_file = os.path.join(data_dir, filename)
    with gdxpds.gdx.GdxFile() as f:
        f.read(gdx_file)
        assert not f["startupfuel"].loaded
        assert f["startupfuel"].dataframe.empty

        f["startupfuel"].load()
        assert f["startupfuel"].loaded
        assert not f["startupfuel"].dataframe.empty
        assert "CC" in f["startupfuel"].dataframe["*"].tolist()

        f["startupfuel"].unload()
        assert not f["startupfuel"].loaded
        assert f["startupfuel"].dataframe.empty

        f["startupfuel"].load()
        assert f["startupfuel"].loaded
        assert not f["startupfuel"].dataframe.empty
        assert "CC" in f["startupfuel"].dataframe["*"].tolist()


def test_symbol_types_read(data_dir):
    """Read symbol_types_fixture.gdx and pin the per-type column shapes and
    values. See dev/build_symbol_types_fixture.py for the contents."""
    gdx_file = os.path.join(data_dir, "symbol_types_fixture.gdx")
    with gdxpds.gdx.GdxFile(lazy_load=False) as f:
        f.read(gdx_file)

        # Set t: a single Value column holding element text; membership is
        # conveyed by row presence. These elements carry no text, so Value is "".
        t = f["t"]
        assert t.data_type == gdxpds.gdx.GamsDataType.Set
        assert list(t.dataframe.columns) == ["*", "Value"]
        assert t.dataframe["*"].tolist() == ["a", "b", "c", "d", "e"]
        assert t.dataframe["Value"].tolist() == ["", "", "", "", ""]

        # Strict subset of t.
        sub_t = f["sub_t"]
        assert sub_t.data_type == gdxpds.gdx.GamsDataType.Set
        assert sub_t.domain_type == gdxpds.gdx.GamsDomainType.REGULAR
        assert sub_t.domain == [t]
        assert sub_t.dataframe["t"].tolist() == ["a", "c"]

        # Parameter p: single Value column carrying the special values. UNDEF/NA
        # are indistinguishable from NaN in pandas, so the NaN-family is checked
        # loosely and +Inf / -Inf / EPS exactly.
        p = f["p"]
        assert p.data_type == gdxpds.gdx.GamsDataType.Parameter
        assert p.domain_type == gdxpds.gdx.GamsDomainType.REGULAR
        assert p.domain == [t]
        assert list(p.dataframe.columns) == ["t", "Value"]
        pv = dict(zip(p.dataframe["t"], p.dataframe["Value"]))
        assert pv["a"] == 1.5
        assert np.isnan(pv["b"])
        assert pv["c"] == np.inf
        assert pv["d"] == -np.inf
        assert gdxpds.special.is_np_eps(pv["e"])

        # Variable v (Free) over sub_t: the five value columns with known values.
        v = f["v"]
        assert v.data_type == gdxpds.gdx.GamsDataType.Variable
        assert v.variable_type == gdxpds.gdx.GamsVariableType.Free
        assert v.domain == [sub_t]
        assert list(v.dataframe.columns) == ["sub_t"] + [
            t[0] for t in gdxpds.gdx.GAMS_VALUE_COLS_MAP[gdxpds.gdx.GamsDataType.Variable]
        ]
        v_by_key = v.dataframe.set_index("sub_t")
        assert v_by_key.loc["a"].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]
        assert v_by_key.loc["c"].tolist() == [6.0, 7.0, 8.0, 9.0, 10.0]

        # Equation e (Equality) over sub_t: the five value columns with known values.
        e = f["e"]
        assert e.data_type == gdxpds.gdx.GamsDataType.Equation
        assert e.equation_type == gdxpds.gdx.GamsEquationType.Equality
        assert e.domain == [sub_t]
        e_by_key = e.dataframe.set_index("sub_t")
        assert e_by_key.loc["a"].tolist() == [11.0, 12.0, 13.0, 14.0, 15.0]
        assert e_by_key.loc["c"].tolist() == [16.0, 17.0, 18.0, 19.0, 20.0]


def test_to_dataframe_single_symbol(data_dir):
    """to_dataframe returns a plain DataFrame for a single symbol (as of v2.0.0,
    with the old_interface dict wrapper removed)."""
    gdx_file = os.path.join(data_dir, "symbol_types_fixture.gdx")
    df = to_dataframe(gdx_file, "p")
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["t", "Value"]
    assert df.loc[df["t"] == "a", "Value"].iloc[0] == 1.5


def test_set_element_text(data_dir):
    """A Set's Value column is its GAMS element text; membership is row presence.
    See dev/build_set_text_fixture.py."""
    gdx_file = os.path.join(data_dir, "set_text_fixture.gdx")

    df = to_dataframe(gdx_file, "st")
    assert df["*"].tolist() == ["a", "b", "c"]
    assert df["Value"].tolist() == ["alpha", "beta", "gamma"]


def test_set_element_text_to_dataframes(data_dir):
    """to_dataframes surfaces element text via the eager bulk-load path
    (load_all -> load_file -> load_symbols), the plural counterpart of
    test_set_element_text. See dev/build_set_text_fixture.py."""
    gdx_file = os.path.join(data_dir, "set_text_fixture.gdx")

    df = to_dataframes(gdx_file)["st"]
    assert df["Value"].tolist() == ["alpha", "beta", "gamma"]


def test_alias_reads_like_its_set(data_dir):
    """An Alias reads like the Set it aliases -- the same elements and element
    text -- while keeping its Alias data type and recording its parent in
    alias_of. Cross-engine equality is covered by test_engine_parity. See
    dev/build_alias_fixture.py."""
    gdx_file = os.path.join(data_dir, "alias_fixture.gdx")
    assert get_data_types(gdx_file)["at"] == gdxpds.gdx.GamsDataType.Alias

    t = to_dataframe(gdx_file, "t")
    at = to_dataframe(gdx_file, "at")
    assert list(at.columns) == list(t.columns)
    # The alias surfaces the same elements and element text as its set.
    assert at["*"].tolist() == t["*"].tolist()
    assert at["Value"].tolist() == t["Value"].tolist()

    # alias_of resolves to the parent Set GdxSymbol.
    with gdxpds.gdx.GdxFile(lazy_load=False) as f:
        f.read(gdx_file)
        assert f["at"].alias_of is f["t"]
