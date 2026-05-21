import copy
import gc
import logging
import os
from ctypes import c_bool

import numpy as np
import pandas as pd
import pytest

import gdxpds
import gdxpds.gdx
import gdxpds.special
from gdxpds.tools import Error

logger = logging.getLogger(__name__)


def test_from_scratch_sets(run_dir):
    outdir = os.path.join(run_dir, "from_scratch_sets")
    if not os.path.exists(outdir):
        os.mkdir(outdir)

    with gdxpds.gdx.GdxFile() as gdx:
        gdx.append(gdxpds.gdx.GdxSymbol("my_set", gdxpds.gdx.GamsDataType.Set, dims=["u"]))
        data = pd.DataFrame([["u" + str(i)] for i in range(1, 11)])
        data["Value"] = True
        gdx[-1].dataframe = data
        assert isinstance(gdx[-1].dataframe[gdx[-1].dataframe.columns[-1]].values[0], c_bool)
        gdx.append(gdxpds.gdx.GdxSymbol("my_other_set", gdxpds.gdx.GamsDataType.Set, dims=["u"]))
        data = pd.DataFrame([["u" + str(i)] for i in range(1, 11)], columns=["u"])
        data["Value"] = True
        gdx[-1].dataframe = pd.concat([gdx[-1].dataframe, data])
        gdx.write(os.path.join(outdir, "my_sets.gdx"))

    with gdxpds.gdx.GdxFile(lazy_load=False) as gdx:
        gdx.read(os.path.join(outdir, "my_sets.gdx"))
        for sym in gdx:
            assert sym.num_dims == 1
            assert sym.dims[0] == "u"
            assert sym.data_type == gdxpds.gdx.GamsDataType.Set
            assert sym.num_records == 10
            assert isinstance(sym.dataframe[sym.dataframe.columns[-1]].values[0], c_bool)


def test_unnamed_dimensions(run_dir):
    from pathlib import Path

    outdir = Path(run_dir) / "unnamed_dimensions"
    if not outdir.exists():
        outdir.mkdir()
    # create a gdx file with all symbol types and 4 dimensions named '*'
    cols = ["*"] * 4
    some_entries = pd.DataFrame(
        [
            ["tech_1", "year_2", "low", "h1"],
            ["tech_1", "year_2", "low", "h2"],
            ["tech_1", "year_2", "low", "h3"],
            ["tech_1", "year_2", "low", "h4"],
        ],
        columns=cols,
    )

    with gdxpds.gdx.GdxFile() as gdx:
        # Set
        gdx.append(gdxpds.gdx.GdxSymbol("star_set", gdxpds.gdx.GamsDataType.Set, dims=4))
        gdx[-1].dataframe = some_entries
        # Parmeter
        a_param = copy.deepcopy(some_entries)
        a_param["Value"] = [1.0, 2.0, 3.0, 4.0]
        gdx.append(gdxpds.gdx.GdxSymbol("star_param", gdxpds.gdx.GamsDataType.Parameter, dims=4))
        gdx[-1].dataframe = a_param
        # Test changing the parameter data
        a_param.iloc[:, 0] = "tech_2"
        gdx[-1].dataframe = pd.concat([gdx[-1].dataframe, a_param])
        # Variable
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "star_var",
                gdxpds.gdx.GamsDataType.Variable,
                dims=4,
                variable_type=gdxpds.gdx.GamsVariableType.Positive,
            )
        )
        a_var = copy.deepcopy(some_entries)
        for value_col_name in gdx[-1].value_col_names:
            a_var[value_col_name] = gdx[-1].get_value_col_default(value_col_name)
        gdx[-1].dataframe = a_var
        # Equation
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "star_eqn",
                gdxpds.gdx.GamsDataType.Equation,
                dims=4,
                equation_type=gdxpds.gdx.GamsEquationType.GreaterThan,
            )
        )
        an_eqn = copy.deepcopy(some_entries)
        for value_col_name in gdx[-1].value_col_names:
            an_eqn[value_col_name] = gdx[-1].get_value_col_default(value_col_name)
        gdx[-1].dataframe = an_eqn
        gdx.write(outdir / "star_symbols.gdx")

    with gdxpds.gdx.GdxFile(lazy_load=False) as gdx:
        gdx.read(outdir / "star_symbols.gdx")
        assert gdx["star_set"].num_dims == 4
        assert gdx["star_set"].data_type == gdxpds.gdx.GamsDataType.Set
        assert gdx["star_set"].variable_type is None
        assert gdx["star_set"].equation_type is None
        assert gdx["star_param"].num_dims == 4
        assert gdx["star_param"].data_type == gdxpds.gdx.GamsDataType.Parameter
        assert gdx["star_param"].variable_type is None
        assert gdx["star_param"].equation_type is None
        assert gdx["star_var"].num_dims == 4
        assert gdx["star_var"].data_type == gdxpds.gdx.GamsDataType.Variable
        assert gdx["star_var"].variable_type == gdxpds.gdx.GamsVariableType.Positive
        assert gdx["star_var"].equation_type is None
        assert gdx["star_eqn"].num_dims == 4
        assert gdx["star_eqn"].data_type == gdxpds.gdx.GamsDataType.Equation
        assert gdx["star_eqn"].variable_type is None
        assert gdx["star_eqn"].equation_type == gdxpds.gdx.GamsEquationType.GreaterThan


def test_setting_dataframes(run_dir):
    outdir = os.path.join(run_dir, "setting_dataframes")
    if not os.path.exists(outdir):
        os.mkdir(outdir)

    with gdxpds.gdx.GdxFile() as gdx:
        # reading is tested elsewhere. here go through the different ways to
        # set a dataframe.

        # start with WAYS THAT WORK:
        # 0 dims
        #     full dataframe
        gdx.append(gdxpds.gdx.GdxSymbol("sym_1", gdxpds.gdx.GamsDataType.Parameter))
        gdx[-1].dataframe = pd.DataFrame([[2.0]])
        assert list(gdx[-1].dataframe.columns) == ["Value"]
        #     edit initialized dataframe - Parameter
        gdx.append(gdxpds.gdx.GdxSymbol("sym_2", gdxpds.gdx.GamsDataType.Parameter))
        n = len(gdx[-1].dataframe.columns)
        gdx[-1].dataframe["Value"] = [5.0]  # list is required to specify number of rows to make
        assert n == len(gdx[-1].dataframe.columns)
        #     list of lists
        gdx.append(gdxpds.gdx.GdxSymbol("sym_3", gdxpds.gdx.GamsDataType.Variable))
        values = [3.0]
        for value_col_name in gdx[-1].value_col_names:
            if value_col_name == "Level":
                continue
            values.append(gdx[-1].get_value_col_default(value_col_name))
        gdx[-1].dataframe = [values]
        #     reset with empty list
        gdx.append(gdxpds.gdx.GdxSymbol("sym_4", gdxpds.gdx.GamsDataType.Parameter))
        gdx[-1].dataframe = pd.DataFrame([[1.0]])
        gdx[-1].dataframe = []
        assert gdx[-1].num_records == 0

        # > 0 dims - GdxSymbol initialized with dims=0
        #     full dataframe
        gdx.append(gdxpds.gdx.GdxSymbol("sym_5", gdxpds.gdx.GamsDataType.Parameter))
        gdx[-1].dataframe = pd.DataFrame(
            [["u1", "CC", 8727.2], ["u2", "CC", 7500.2], ["u3", "CT", 9258.0]],
            columns=["u", "q", "val"],
        )
        assert gdx[-1].num_dims == 2
        assert gdx[-1].num_records == 3
        #     full list of lists
        gdx.append(gdxpds.gdx.GdxSymbol("sym_6", gdxpds.gdx.GamsDataType.Parameter))
        gdx[-1].dataframe = [
            ["u1", "CC", 8727.2],
            ["u2", "CC", 7500.2],
            ["u3", "CT", 9258.0],
            ["u4", "Coal", 10100.0],
        ]
        assert gdx[-1].num_dims == 2
        assert gdx[-1].num_records == 4
        #     reset with empty list
        gdx.append(gdxpds.gdx.GdxSymbol("sym_7", gdxpds.gdx.GamsDataType.Parameter))
        gdx[-1].dataframe = gdx[-2].dataframe.copy()
        gdx[-1].dataframe = []
        assert gdx[-1].num_dims == 2
        assert gdx[-1].num_records == 0

        # > 0 dims - GdxSymbol initialized with dims=n
        #     dataframe of dims
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "sym_8",
                gdxpds.gdx.GamsDataType.Variable,
                dims=3,
                variable_type=gdxpds.gdx.GamsVariableType.Positive,
            )
        )
        gdx[-1].dataframe = pd.DataFrame(
            [["u0", "BES", "c2"], ["u0", "BES", "c1"], ["u1", "BES", "c2"]]
        )
        assert gdx[-1].num_dims == 3
        assert gdx[-1].dims == ["*", "*", "*"]
        assert len(gdx[-1].dataframe.columns) > 3
        gdx[-1].dataframe.loc[:, gdxpds.gdx.GamsValueType.Level.name] = 1.0
        gdx[-1].dataframe.loc[:, gdxpds.gdx.GamsValueType.Upper.name] = 10.0
        #     full dataframe
        gdx.append(gdxpds.gdx.GdxSymbol("sym_9", gdxpds.gdx.GamsDataType.Parameter, dims=3))
        gdx[-1].dataframe = pd.DataFrame(
            [["u0", "BES", "c2", 2.0], ["u0", "BES", "c1", 1.0], ["u1", "BES", "c2", 2.0]],
            columns=["u", "q", "c", "storage_duration_h"],
        )
        assert list(gdx[-1].dataframe.columns) == ["u", "q", "c", "Value"]
        #     list of lists containing dims only
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "sym_10",
                gdxpds.gdx.GamsDataType.Equation,
                dims=4,
                equation_type=gdxpds.gdx.GamsEquationType.LessThan,
            )
        )
        gdx[-1].dataframe = [
            ["u0", "PHES", "c0", "1"],
            ["u0", "PHES", "c0", "2"],
            ["u0", "PHES", "c0", "3"],
            ["u0", "PHES", "c0", "4"],
            ["u0", "PHES", "c0", "5"],
        ]
        gdx[-1].dataframe.loc[:, "Level"] = -15.0
        assert list(gdx[-1].dataframe.columns[: gdx[-1].num_dims]) == ["*"] * 4
        #     full list of lists
        gdx.append(gdxpds.gdx.GdxSymbol("sym_11", gdxpds.gdx.GamsDataType.Set, dims=2))
        gdx[-1].dataframe = [
            ["PV", "c0", True],
            ["CSP", "c0", False],
            ["CSP", "c1", False],
            ["Wind", "c0", True],
        ]
        assert gdx[-1].num_dims == 2
        #     reset with empty list
        gdx.append(gdxpds.gdx.GdxSymbol("sym_12", gdxpds.gdx.GamsDataType.Set, dims=2))
        gdx[-1].dataframe = gdx[-1].dataframe.copy()
        gdx[-1].dataframe = []
        assert gdx[-1].num_dims == 2
        assert gdx[-1].dims == ["*"] * 2
        assert gdx[-1].num_records == 0

        # > 0 dims - GdxSymbol initialized with dims=[list of actual dims]
        #     dataframe of dims
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "sym_13",
                gdxpds.gdx.GamsDataType.Variable,
                dims=["u", "q", "c"],
                variable_type=gdxpds.gdx.GamsVariableType.Positive,
            )
        )
        gdx[-1].dataframe = pd.DataFrame(
            [["u0", "BES", "c2"], ["u0", "BES", "c1"], ["u1", "BES", "c2"]]
        )
        assert gdx[-1].num_dims == 3
        assert gdx[-1].dims == ["u", "q", "c"]
        assert len(gdx[-1].dataframe.columns) > 3
        gdx[-1].dataframe[gdxpds.gdx.GamsValueType.Level.name] = 1.0
        gdx[-1].dataframe[gdxpds.gdx.GamsValueType.Upper.name] = 10.0
        #     full dataframe
        gdx.append(
            gdxpds.gdx.GdxSymbol("sym_14", gdxpds.gdx.GamsDataType.Parameter, dims=["u", "q", "c"])
        )
        gdx[-1].dataframe = pd.DataFrame(
            [["u0", "BES", "c2", 2.0], ["u0", "BES", "c1", 1.0], ["u1", "BES", "c2", 2.0]],
            columns=["u", "q", "c", "storage_duration_h"],
        )
        assert list(gdx[-1].dataframe.columns) == ["u", "q", "c", "Value"]
        #     list of lists containing dims only
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "sym_15",
                gdxpds.gdx.GamsDataType.Equation,
                dims=["u", "q", "c", "t"],
                equation_type=gdxpds.gdx.GamsEquationType.LessThan,
            )
        )
        gdx[-1].dataframe = [
            ["u0", "PHES", "c0", "1"],
            ["u0", "PHES", "c0", "2"],
            ["u0", "PHES", "c0", "3"],
            ["u0", "PHES", "c0", "4"],
            ["u0", "PHES", "c0", "5"],
        ]
        gdx[-1].dataframe["Level"] = -15.0
        assert list(gdx[-1].dataframe.columns[: gdx[-1].num_dims]) == ["u", "q", "c", "t"]
        #     full list of lists
        gdx.append(gdxpds.gdx.GdxSymbol("sym_16", gdxpds.gdx.GamsDataType.Set, dims=["q", "c"]))
        gdx[-1].dataframe = [
            ["PV", "c0", True],
            ["CSP", "c0", False],
            ["CSP", "c1", False],
            ["Wind", "c0", True],
        ]
        assert gdx[-1].num_dims == 2
        #     reset with empty list
        gdx.append(gdxpds.gdx.GdxSymbol("sym_17", gdxpds.gdx.GamsDataType.Set, dims=["q", "c"]))
        gdx[-1].dataframe = gdx["sym_11"].dataframe.copy()
        gdx[-1].dataframe = []
        assert gdx[-1].num_dims == 2
        assert list(gdx[-1].dataframe.columns[: gdx[-1].num_dims]) == ["*"] * 2
        assert gdx[-1].num_records == 0

        # And then document that some ways DO NOT WORK:
        # dims=0
        #     set value, then try to set different number of dimensions
        gdx.append(gdxpds.gdx.GdxSymbol("sym_18", gdxpds.gdx.GamsDataType.Parameter, dims=0))
        gdx[-1].dataframe = [[3]]
        with pytest.raises(Error) as excinfo:
            gdx[-1].dims = 3
        assert "Cannot set dims to 3" in str(excinfo.value)
        # dims > 0
        #     explicitly set dims to something else
        gdx.append(
            gdxpds.gdx.GdxSymbol("sym_19", gdxpds.gdx.GamsDataType.Parameter, dims=["g", "t"])
        )
        with pytest.raises(Exception) as excinfo:
            gdx[-1].dims = ["g", "t", "d"]
        assert "Cannot set dims" in str(excinfo.value)
        #     dataframe of different number of dims
        gdx.append(
            gdxpds.gdx.GdxSymbol("sym_20", gdxpds.gdx.GamsDataType.Variable, dims=["d", "t"])
        )
        gdx[-1].dataframe = [["d1", "1"], ["d1", "2"], ["d1", "3"]]
        tmp = gdx[-1].dataframe.copy()
        cols = list(tmp.columns)
        tmp["q"] = "PV"
        tmp = tmp[["q"] + cols]
        with pytest.raises(Exception) as _excinfo:
            gdx[-1].dataframe = tmp
        #     full dataframe of different number of dims
        gdx.append(gdxpds.gdx.GdxSymbol("sym_21", gdxpds.gdx.GamsDataType.Parameter, dims=6))
        assert gdx[-1].dims == ["*"] * 6
        with pytest.raises(Exception):
            gdx[-1].dataframe = pd.DataFrame([["1", 6.0], ["2", 7.0], ["3", -12.0]])
        #     list of lists of varying widths
        gdx.append(gdxpds.gdx.GdxSymbol("sym_22", gdxpds.gdx.GamsDataType.Parameter, dims=3))
        with pytest.raises(Exception):
            gdx[-1].dataframe = [[1]]
        with pytest.raises(Exception):
            gdx[-1].dataframe = [["1", 2.5], ["2", -30.0]]
        # TODO: Write test where parameter value ends up as set dimension--does
        # an exception get thrown upon writing to GDX?
        with pytest.raises(Exception):
            gdx[-1].dataframe = [["u1", "PV", "c0", "1", 2.5], ["u1", "PV", "c0", "2", -30.0]]

        gdx.write(os.path.join(outdir, "dataframe_set_tests.gdx"))

    with gdxpds.gdx.GdxFile(lazy_load=False) as gdx:
        gdx.read(os.path.join(outdir, "dataframe_set_tests.gdx"))
        assert gdx["sym_1"].num_records == 1
        assert gdx["sym_2"].num_records == 1
        assert gdx["sym_3"].num_records == 1
        assert gdx["sym_4"].num_records == 1  # GAMS defaults empty 0-dim parameter to 0
        assert gdx["sym_4"].dataframe["Value"].values[0] == 0.0
        assert gdx["sym_5"].dims == ["u", "q"]
        assert gdx["sym_5"].num_records == 3
        assert gdx["sym_5"].dataframe["Value"].values[1] == 7500.2
        assert gdx["sym_6"].num_records == 4
        assert gdx["sym_7"].num_records == 0
        assert gdx["sym_8"].dims == ["*"] * 3
        assert gdx["sym_8"].dataframe[gdxpds.gdx.GamsValueType.Upper.name].values[0] == 10.0
        assert gdx["sym_9"].num_records == 3
        assert gdx["sym_10"].num_dims == 4
        assert gdx["sym_11"].num_dims == 2
        assert gdx["sym_11"].num_records == 4
        # ETH@20181007 - Tried to test for some values being c_bool(False) in sym_11, but
        # c_bool(True) != c_bool(True), so that makes it hard to test such things.
        # Also, c_bool(False) appears to be interpreted as True in GDX. Ick and yikes.
        assert gdx["sym_12"].num_dims == 2
        assert gdx["sym_12"].num_records == 0
        assert gdx["sym_13"].dims == ["u", "q", "c"]
        assert gdx["sym_13"].dataframe[gdxpds.gdx.GamsValueType.Upper.name].values[0] == 10.0
        assert gdx["sym_14"].num_records == 3
        assert gdx["sym_15"].num_dims == 4
        assert gdx["sym_16"].num_dims == 2
        assert gdx["sym_16"].num_records == 4
        assert gdx["sym_17"].num_dims == 2
        assert gdx["sym_17"].num_records == 0


def test_parameter_with_nulls(run_dir):
    outdir = os.path.join(run_dir, "parameter_with_nulls")
    if not os.path.exists(outdir):
        os.mkdir(outdir)

    with gdxpds.gdx.GdxFile() as gdx:
        gdx.append(gdxpds.gdx.GdxSymbol("has_nulls", gdxpds.gdx.GamsDataType.Parameter, dims=1))
        gdx[-1].dataframe = [["A", 1], ["B", None]]
        assert gdx[-1].dataframe["Value"].isnull().values.any()

        gdx.write(os.path.join(outdir, "parameter_with_nulls_test.gdx"))


def test_to_gdx_returned_handle_survives_translator_gc(run_dir):
    # Regression: to_gdx returns a GdxFile while the transient Translator
    # goes out of scope. The Translator must not free the returned object's
    # GDX handle on cleanup, or reusing the GdxFile would hit a freed handle.
    outdir = os.path.join(run_dir, "to_gdx_handle_lifetime")
    if not os.path.exists(outdir):
        os.mkdir(outdir)
    out1 = os.path.join(outdir, "first.gdx")
    out2 = os.path.join(outdir, "second.gdx")

    dataframes = {"a": pd.DataFrame([["a1", True], ["a2", True]], columns=["a", "Value"])}
    gdx = gdxpds.to_gdx(dataframes, out1)
    # Force the transient Translator (now unreferenced) to be collected so
    # any premature handle-free would have happened before we reuse gdx.
    gc.collect()

    # The handle must still be valid: a second write through the same object
    # would raise GdxError (or crash) if the handle had been freed.
    gdx.write(out2)

    with gdxpds.gdx.GdxFile(lazy_load=False) as check:
        check.read(out2)
        assert {s.name for s in check} == {"a"}
        assert check["a"].num_records == 2


def test_write_known_value_columns(run_dir):
    """Build a Parameter (with specials), a Variable, and an Equation from
    scratch with known, non-default values in every value column; write, read
    back, and assert the exact values and types survive. (Existing tests only
    wrote default value-column values.)"""
    eps = np.finfo(float).eps
    outdir = os.path.join(run_dir, "known_value_columns")
    if not os.path.exists(outdir):
        os.mkdir(outdir)
    out = os.path.join(outdir, "known_values.gdx")

    with gdxpds.gdx.GdxFile() as gdx:
        gdx.append(gdxpds.gdx.GdxSymbol("p", gdxpds.gdx.GamsDataType.Parameter, dims=["t"]))
        gdx[-1].dataframe = pd.DataFrame(
            [["a", 1.5], ["b", np.nan], ["c", np.inf], ["d", -np.inf], ["e", eps]],
            columns=["t", "Value"],
        )
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "v",
                gdxpds.gdx.GamsDataType.Variable,
                dims=["t"],
                variable_type=gdxpds.gdx.GamsVariableType.Free,
            )
        )
        v_df = pd.DataFrame({"t": ["a", "b"]})
        for i, col in enumerate(gdx[-1].value_col_names):
            v_df[col] = [float(i + 1), float(i + 6)]
        gdx[-1].dataframe = v_df
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "e",
                gdxpds.gdx.GamsDataType.Equation,
                dims=["t"],
                equation_type=gdxpds.gdx.GamsEquationType.Equality,
            )
        )
        e_df = pd.DataFrame({"t": ["a", "b"]})
        for i, col in enumerate(gdx[-1].value_col_names):
            e_df[col] = [float(i + 11), float(i + 16)]
        gdx[-1].dataframe = e_df
        gdx.write(out)

    with gdxpds.gdx.GdxFile(lazy_load=False) as gdx:
        gdx.read(out)

        p = gdx["p"]
        pv = dict(zip(p.dataframe["t"], p.dataframe["Value"]))
        assert pv["a"] == 1.5
        assert np.isnan(pv["b"])
        assert pv["c"] == np.inf
        assert pv["d"] == -np.inf
        assert gdxpds.special.is_np_eps(pv["e"])

        v = gdx["v"]
        assert v.data_type == gdxpds.gdx.GamsDataType.Variable
        assert v.variable_type == gdxpds.gdx.GamsVariableType.Free
        v_by_key = v.dataframe.set_index("t")
        assert v_by_key.loc["a"].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]
        assert v_by_key.loc["b"].tolist() == [6.0, 7.0, 8.0, 9.0, 10.0]

        e = gdx["e"]
        assert e.data_type == gdxpds.gdx.GamsDataType.Equation
        assert e.equation_type == gdxpds.gdx.GamsEquationType.Equality
        e_by_key = e.dataframe.set_index("t")
        assert e_by_key.loc["a"].tolist() == [11.0, 12.0, 13.0, 14.0, 15.0]
        assert e_by_key.loc["b"].tolist() == [16.0, 17.0, 18.0, 19.0, 20.0]


def test_symbol_types_round_trip(data_dir, run_dir):
    """Read the committed reference fixture, write it back out through the
    backend, re-read, and assert every symbol round-trips identically. This is
    the read+write parity check the future gams.transfer backend must also pass.
    See dev/build_symbol_types_fixture.py."""
    src = os.path.join(data_dir, "symbol_types_fixture.gdx")
    outdir = os.path.join(run_dir, "symbol_types_round_trip")
    if not os.path.exists(outdir):
        os.mkdir(outdir)
    rt = os.path.join(outdir, "round_trip.gdx")

    names = ["t", "sub_t", "p", "v", "e"]
    orig = {}
    with gdxpds.gdx.GdxFile(lazy_load=False) as f:
        f.read(src)
        for name in names:
            sym = f[name]
            orig[name] = (
                sym.data_type,
                sym.variable_type,
                sym.equation_type,
                sym.dataframe.reset_index(drop=True),
            )
        with f.clone() as g:
            g.write(rt)

    def cell_equal(x, y):
        if isinstance(x, c_bool) or isinstance(y, c_bool):
            return bool(x) == bool(y)
        if isinstance(x, float) or isinstance(y, float):
            return gdxpds.special.pd_val_equal(x, y)
        return x == y

    with gdxpds.gdx.GdxFile(lazy_load=False) as g:
        g.read(rt)
        for name in names:
            dt, vt, et, odf = orig[name]
            sym = g[name]
            assert sym.data_type == dt
            assert sym.variable_type == vt
            assert sym.equation_type == et
            rdf = sym.dataframe.reset_index(drop=True)
            assert list(rdf.columns) == list(odf.columns)
            assert len(rdf) == len(odf)
            for col in odf.columns:
                for x, y in zip(odf[col], rdf[col]):
                    assert cell_equal(x, y), f"{name}.{col}: {x!r} != {y!r}"
