"""Phase A read parity: the gams_transfer backend must produce DataFrames
identical to the gdxcc oracle for every in-tree GDX fixture, with and without
set text, plus a controlled special-value round-trip. Skipped when
gams.transfer is unavailable."""

import glob
import os
import tempfile
from ctypes import c_bool

import numpy as np
import pandas as pd
import pytest

import gdxpds
from gdxpds import to_dataframes, to_gdx
from gdxpds.gdx import GamsDataType, GdxFile

pytestmark = pytest.mark.skipif(not gdxpds.HAVE_GAMS_TRANSFER, reason="gams.transfer not available")

# Computed at import (collection) time because @parametrize needs the values
# before the conftest ``data_dir`` fixture is available. Test bodies use the
# ``data_dir`` fixture (repo convention); this constant just feeds parametrize.
FIXTURES = sorted(
    os.path.basename(p) for p in glob.glob(os.path.join(os.path.dirname(__file__), "data", "*.gdx"))
)


def _normalize(df):
    """c_bool cells -> plain bool so DataFrames compare by value (NaNs are
    treated as equal by assert_frame_equal). Iterates by position to handle the
    duplicate '*' column labels that multi-dim universe symbols carry."""
    df = df.copy()
    for i in range(df.shape[1]):
        s = df.iloc[:, i]
        if s.map(lambda v: isinstance(v, c_bool)).any():
            df.isetitem(i, s.map(lambda v: bool(v) if isinstance(v, c_bool) else v))
    return df


def _assert_same(a, b):
    # Same symbols in the same order (both backends read in GDX order), then
    # identical contents per symbol.
    assert list(a) == list(b)
    for name in a:
        pd.testing.assert_frame_equal(_normalize(a[name]), _normalize(b[name]), check_dtype=True)


@pytest.mark.parametrize("fixture", FIXTURES)
@pytest.mark.parametrize("load_set_text", [False, True])
def test_read_parity(data_dir, fixture, load_set_text):
    path = os.path.join(data_dir, fixture)
    a = to_dataframes(path, backend="gdxcc", load_set_text=load_set_text)
    b = to_dataframes(path, backend="gams_transfer", load_set_text=load_set_text)
    _assert_same(a, b)


def test_read_parity_special_values():
    # Write a Parameter carrying each producible special value via the gdxcc
    # write path, then assert both backends read it back identically.
    eps = np.finfo(float).eps
    df = pd.DataFrame(
        {"i": ["a", "b", "c", "d", "e", "f"], "Value": [np.nan, np.inf, -np.inf, eps, 0.0, 1.5]}
    )
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "specials.gdx")
        to_gdx({"p": df}, out)
        a = to_dataframes(out, backend="gdxcc")
        b = to_dataframes(out, backend="gams_transfer")
        _assert_same(a, b)


def test_read_parity_undef():
    # gdxcc distinguishes GDX UNDEF (-> None) from GDX NA (-> np.nan), yielding an
    # object-dtype value column when any UNDEF is present (see special.GDX_TO_NP_SVS).
    # gdxcc's *write* path can't emit a genuine UNDEF (None collapses to 0.0), so
    # build the fixture straight through gams.transfer to get a real UNDEF on disk,
    # then assert both backends read it back identically (object col, None vs nan).
    import gams.transfer as gt

    from gdxpds.tools import GamsDirFinder

    gdir = GamsDirFinder().gams_dir
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "undef.gdx")
        c = gt.Container(system_directory=gdir)
        recs = pd.DataFrame(
            {
                "i": ["na", "undef", "one"],
                "value": [gt.SpecialValues.NA, gt.SpecialValues.UNDEF, 1.0],
            }
        )
        gt.Parameter(c, "p", domain=["*"], records=recs)
        c.write(out)

        a = to_dataframes(out, backend="gdxcc")
        b = to_dataframes(out, backend="gams_transfer")
        # Sanity: the oracle really did produce the None/nan distinction in object dtype.
        assert a["p"]["Value"].dtype == object
        assert a["p"]["Value"].iloc[1] is None
        _assert_same(a, b)


def test_read_parity_symbol_subset(data_dir):
    # The subset path (targeted read on gams.transfer) matches gdxcc.
    path = os.path.join(data_dir, "symbol_types_fixture.gdx")
    names = list(to_dataframes(path, backend="gdxcc"))[:2]
    a = to_dataframes(path, backend="gdxcc", symbols=names)
    b = to_dataframes(path, backend="gams_transfer", symbols=names)
    assert list(a) == names and list(b) == names
    _assert_same(a, b)


# --- Phase B: write parity, over the full write x read backend matrix ---

_BACKENDS = ("gdxcc", "gams_transfer")


def _write_read_matrix(dfs, tmp_path, **kw):
    """Write ``dfs`` with each backend, then read each output back with each
    backend. Returns ``{(write_backend, read_backend): dataframes}``, exercising
    the full 2x2 matrix -- including the cross-engine combinations (notably
    gams_transfer-write -> gams_transfer-read, the fast path's real workflow,
    which reading-back-only-via-gdxcc would never touch)."""
    paths = {}
    for w in _BACKENDS:
        paths[w] = str(tmp_path / f"via_{w}.gdx")
        to_gdx(dfs, paths[w], backend=w, **kw)
    return {(w, r): to_dataframes(paths[w], backend=r) for w in _BACKENDS for r in _BACKENDS}


def _assert_matrix_consistent(matrix):
    # gdxcc-write + gdxcc-read is the oracle (the legacy round-trip); every other
    # (write, read) combination must reproduce it.
    oracle = matrix[("gdxcc", "gdxcc")]
    for dfs in matrix.values():
        _assert_same(oracle, dfs)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_write_parity(data_dir, fixture, tmp_path):
    dfs = to_dataframes(os.path.join(data_dir, fixture), backend="gdxcc")
    _assert_matrix_consistent(_write_read_matrix(dfs, tmp_path))


def test_write_parity_special_values(tmp_path):
    eps = np.finfo(float).eps
    dfs = {
        "p": pd.DataFrame(
            {"i": ["a", "b", "c", "d", "e"], "Value": [np.nan, np.inf, -np.inf, eps, 0.0]}
        ),
        "scalar": pd.DataFrame({"Value": [42.0]}),
    }
    _assert_matrix_consistent(_write_read_matrix(dfs, tmp_path))


def test_write_parity_undef(tmp_path):
    # A Python None in a value column is gdxpds' canonical GDX UNDEF. The gdxcc
    # oracle's write path can't emit UNDEF -- a None isn't a Number, so it falls
    # through to 0.0 -- and v2.1.0 holds strict parity, so the gams_transfer write
    # must also yield 0.0 (not NA). Keep the whole write x read matrix consistent.
    # (1.0 first so to_gdx infers a Parameter, not a Set.)
    dfs = {
        "p": pd.DataFrame(
            {"i": ["one", "undef", "na"], "Value": pd.Series([1.0, None, np.nan], dtype=object)}
        )
    }
    matrix = _write_read_matrix(dfs, tmp_path)
    _assert_matrix_consistent(matrix)
    for dfs_out in matrix.values():
        vals = list(dfs_out["p"]["Value"])
        assert vals[0] == 1.0
        # The point of this test is that the write path treats UNDEF and NA
        # *differently*: UNDEF (None) collapses to 0.0, while NA stays NaN -- and
        # NA must not itself collapse to None (which would mean it got confused
        # with UNDEF). The `is not None` guard makes that distinction explicit and
        # short-circuits before np.isnan (which would raise on None); pd.isna would
        # be too weak here, since pd.isna(None) is also True.
        assert vals[1] == 0.0
        assert vals[2] is not None and np.isnan(vals[2])


def test_write_parity_mixed_boolean_set(tmp_path):
    # R12 gate: gdxcc collapses every set element to 0.0 / c_bool(False) on write
    # (the membership-boolean wart), so a Set with mixed True/False must read back
    # all-False no matter which engine wrote *or* read it.
    dfs = {"s": pd.DataFrame({"i": ["a", "b", "c"], "Value": [True, False, True]})}
    matrix = _write_read_matrix(dfs, tmp_path)
    _assert_matrix_consistent(matrix)
    for dfs in matrix.values():
        assert [bool(v) for v in dfs["s"]["Value"]] == [False, False, False]


def test_write_alias_unsupported(data_dir, tmp_path):
    # to_gdx never infers an Alias, so the parity tests never reach the write
    # path's Alias branch. A GdxFile read from an alias-bearing GDX *does* carry
    # an Alias symbol; writing it via gams_transfer is explicitly unsupported in
    # v2.1.0 (use backend='gdxcc'). Lock in the NotImplementedError contract.
    f = GdxFile(lazy_load=False, backend="gams_transfer")
    f.read(os.path.join(data_dir, "alias_fixture.gdx"))
    assert any(s.data_type == GamsDataType.Alias for s in f)
    with pytest.raises(NotImplementedError):
        f.write(str(tmp_path / "out.gdx"))
