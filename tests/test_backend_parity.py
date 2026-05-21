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

pytestmark = pytest.mark.skipif(
    not gdxpds.HAVE_GAMS_TRANSFER, reason="gams.transfer not available"
)

# Computed at import (collection) time because @parametrize needs the values
# before the conftest ``data_dir`` fixture is available. Test bodies use the
# ``data_dir`` fixture (repo convention); this constant just feeds parametrize.
FIXTURES = sorted(
    os.path.basename(p)
    for p in glob.glob(os.path.join(os.path.dirname(__file__), "data", "*.gdx"))
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


def test_read_parity_symbol_subset(data_dir):
    # The subset path (targeted read on gams.transfer) matches gdxcc.
    path = os.path.join(data_dir, "symbol_types_fixture.gdx")
    names = list(to_dataframes(path, backend="gdxcc"))[:2]
    a = to_dataframes(path, backend="gdxcc", symbols=names)
    b = to_dataframes(path, backend="gams_transfer", symbols=names)
    assert list(a) == names and list(b) == names
    _assert_same(a, b)
