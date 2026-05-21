import logging
import os
from ctypes import c_bool

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
    with pytest.raises(gdxpds.gdx.GdxError) as excinfo:
        to_dataframes(None)
    assert "Could not open None" in str(excinfo.value)


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

        # Set t: a single boolean Value column; membership is conveyed by row
        # presence. gdxpds-written Sets store value 0.0 (see _fixup_set_value),
        # so the boolean reads False even though each element is a member.
        t = f["t"]
        assert t.data_type == gdxpds.gdx.GamsDataType.Set
        assert list(t.dataframe.columns) == ["*", "Value"]
        assert t.dataframe["*"].tolist() == ["a", "b", "c", "d", "e"]
        assert all(isinstance(v, c_bool) for v in t.dataframe["Value"])
        # ...and every value reads False: gdxpds-written Sets store 0.0, so the
        # boolean is the truthiness of that stored value, not "is a member".
        # Pin it so the alternative backend can't silently change it.
        assert all(not bool(v) for v in t.dataframe["Value"])

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
    """load_set_text=True surfaces GAMS element text in place of the membership
    boolean. See dev/build_set_text_fixture.py."""
    gdx_file = os.path.join(data_dir, "set_text_fixture.gdx")

    # Default: membership booleans (the raw-written values are non-zero, so True).
    default = to_dataframe(gdx_file, "st")
    assert default["*"].tolist() == ["a", "b", "c"]
    assert all(isinstance(v, c_bool) for v in default["Value"])
    # ...and they read True here: the raw-written text-node indices are non-zero,
    # in contrast to the all-False plain-membership Set above.
    assert all(bool(v) for v in default["Value"])

    # load_set_text=True: the explanatory text replaces the boolean Value.
    with_text = to_dataframe(gdx_file, "st", load_set_text=True)
    assert with_text["Value"].tolist() == ["alpha", "beta", "gamma"]
