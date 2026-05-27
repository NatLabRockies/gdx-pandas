import logging
import os

import gdxpds.gdx
import gdxpds.special

try:
    from gams.core import gdx as gdxcc
except ImportError:
    import gdxcc
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def value_column_index(sym, gams_value_type):
    for i, val in enumerate(sym.value_cols):
        if val[1] == gams_value_type.value:
            break
    return len(sym.dims) + i


def test_roundtrip_just_special_values(run_dir, roundtrip_one_gdx):
    outdir = os.path.join(run_dir, "special_values")
    if not os.path.exists(outdir):
        os.mkdir(outdir)
    # create gdx file containing all special values
    with gdxpds.gdx.GdxFile(engine="gdxcc") as f:
        H = f._engine_impl.handle  # raw gdxcc escape hatch (GdxFile.H removed)
        df = pd.DataFrame(
            [
                ["sv" + str(i + 1), gdxpds.special.SPECIAL_VALUES[i]]
                for i in range(gdxcc.GMS_SVIDX_MAX - 2)
            ],
            columns=["sv", "Value"],
        )
        logger.info(f"Special values are:\n{df}")

        # save this directly as a GdxSymbol
        filename = os.path.join(outdir, "direct_write_special_values.gdx")
        ret = gdxcc.gdxOpenWrite(H, filename, "gdxpds")
        if not ret:
            raise gdxpds.gdx.GdxError(
                H,
                f"Could not open {repr(filename)} for writing. Consider cloning this file (.clone()) before trying to write",
            )
        # write the universal set
        f.universal_set.write()
        if not gdxcc.gdxDataWriteStrStart(
            H, "special_values", "", 1, gdxpds.gdx.GamsDataType.Parameter.value, 0
        ):
            raise gdxpds.gdx.GdxError(H, "Could not start writing data for symbol special_values")
        # set domain information
        if not gdxcc.gdxSymbolSetDomainX(H, 1, [df.columns[0]]):
            raise gdxpds.gdx.GdxError(H, "Could not set domain information for special_values.")
        values = gdxcc.doubleArray(gdxcc.GMS_VAL_MAX)
        for row in df.itertuples(index=False, name=None):
            dims = [str(x) for x in row[:1]]
            vals = row[1:]
            for _col_name, col_ind in gdxpds.gdx.GAMS_VALUE_COLS_MAP[
                gdxpds.gdx.GamsDataType.Parameter
            ]:
                values[col_ind] = float(vals[col_ind])
            gdxcc.gdxDataWriteStr(H, dims, values)
        gdxcc.gdxDataWriteDone(H)
        gdxcc.gdxClose(H)

    # general test for expected values
    def check_special_values(gdx_file):
        df = gdx_file["special_values"].dataframe
        for i, val in enumerate(df["Value"].values):
            assert gdxpds.special.pd_val_equal(val, gdxpds.special.NUMPY_SPECIAL_VALUES[i])

    # now roundtrip it gdx-only
    with gdxpds.gdx.GdxFile(lazy_load=False) as f:
        f.read(filename)
        check_special_values(f)
        with f.clone() as g:
            rt_filename = os.path.join(outdir, "roundtripped.gdx")
            g.write(rt_filename)
    with gdxpds.gdx.GdxFile(lazy_load=False) as g:
        g.read(filename)
        check_special_values(g)

    # now roundtrip it through csv
    roundtripped_gdx = roundtrip_one_gdx(filename, "roundtrip_just_special_values")
    with gdxpds.gdx.GdxFile(lazy_load=False) as h:
        h.read(roundtripped_gdx)
        check_special_values(h)


def test_roundtrip_special_values(data_dir, roundtrip_one_gdx):
    filename = "OptimalCSPConfig_Out.gdx"
    original_gdx = os.path.join(data_dir, filename)
    roundtripped_gdx = roundtrip_one_gdx(filename, "roundtrip_special_values")
    data = []
    for gdx_file in [original_gdx, roundtripped_gdx]:
        with gdxpds.gdx.GdxFile(lazy_load=False) as gdx:
            data.append([])
            gdx.read(gdx_file)
            sym = gdx["calculate_capacity_value"]
            assert sym.data_type == gdxpds.gdx.GamsDataType.Equation
            val = sym.dataframe.iloc[0, value_column_index(sym, gdxpds.gdx.GamsValueType.Marginal)]
            assert gdxpds.special.is_np_sv(val)
            data[-1].append(val)
            sym = gdx["CapacityValue"]
            assert sym.data_type == gdxpds.gdx.GamsDataType.Variable
            val = sym.dataframe.iloc[0, value_column_index(sym, gdxpds.gdx.GamsValueType.Upper)]
            assert gdxpds.special.is_np_sv(val)
            data[-1].append(val)
    data = list(zip(*data))
    for pt in data:
        for i in range(1, len(pt)):
            assert (pt[i] == pt[0]) or (np.isnan(pt[i]) and np.isnan(pt[0]))


def test_special_integrity():
    """
    Check that the special values line up
    """
    assert all(sv in gdxpds.special.GDX_TO_NP_SVS for sv in gdxpds.special.SPECIAL_VALUES)
    assert all(sv in gdxpds.special.NP_TO_GDX_SVS for sv in gdxpds.special.NUMPY_SPECIAL_VALUES)

    for val in gdxpds.special.SPECIAL_VALUES:
        assert gdxpds.special.NP_TO_GDX_SVS[gdxpds.special.GDX_TO_NP_SVS[val]] == val

    for val in gdxpds.special.NUMPY_SPECIAL_VALUES:
        # Can't use "==", as None != NaN
        assert gdxpds.special.pd_val_equal(
            gdxpds.special.GDX_TO_NP_SVS[gdxpds.special.NP_TO_GDX_SVS[val]], val
        )


def test_numpy_eps():
    # v4+: GAMS EPS encodes as np.finfo(float).tiny (smallest normal positive
    # float). Exact equality only: legitimate small floats like machine
    # epsilon, 1e-200, etc. must NOT match (#39).
    assert gdxpds.special.is_np_eps(np.finfo(float).tiny)
    assert not gdxpds.special.is_np_eps(0.0)
    assert not gdxpds.special.is_np_eps(2.0 * np.finfo(float).tiny)
    assert not gdxpds.special.is_np_eps(np.finfo(float).eps)
    assert not gdxpds.special.is_np_eps(1e-200)


def test_convert_np_to_gdx_svs_eps():
    test_df = pd.DataFrame(
        [["a", np.finfo(float).tiny], ["b", 0.0], ["c", 2.0 * np.finfo(float).tiny]],
        columns=["A", "Value"],
    )
    result_df = gdxpds.special.convert_np_to_gdx_svs(test_df, num_dims=1)
    expected_df = pd.Series([gdxpds.special.SPECIAL_VALUES[4], 0.0, 2.0 * np.finfo(float).tiny])
    assert result_df["Value"].equals(expected_df)


def test_small_float_is_not_eps():
    """Regression for #39: legitimate small floats survive the write/read
    round-trip as themselves, not as GAMS EPS."""
    test_df = pd.DataFrame(
        [["a", 1e-200], ["b", np.finfo(float).eps], ["c", 1e-100]],
        columns=["A", "Value"],
    )
    result_df = gdxpds.special.convert_np_to_gdx_svs(test_df, num_dims=1)
    # None of these should have been mapped to the GAMS EPS magic float
    # (SPECIAL_VALUES[4]); they pass through as ordinary small floats.
    assert (result_df["Value"].to_numpy() != gdxpds.special.SPECIAL_VALUES[4]).all()
    assert result_df["Value"].iloc[0] == 1e-200
    assert result_df["Value"].iloc[1] == np.finfo(float).eps
    assert result_df["Value"].iloc[2] == 1e-100
