"""Step 1 coverage: the Backend enum, capability flag, backend resolution, and
the to_dataframes(symbols=...) subset feature. The gams.transfer backend itself
is not yet constructible (Phase A), so these exercise selection/plumbing on the
default gdxcc backend."""

import os
from ctypes import c_bool

import pandas as pd
import pytest

import gdxpds
from gdxpds import (
    Backend,
    BackendError,
    SymbolNotFoundError,
    get_data_types,
    get_subset_relationships,
    list_symbols,
    to_dataframe,
    to_dataframes,
    to_gdx,
)
from gdxpds._backend import resolve_backend
from gdxpds.tools import Error


def _normalize(df):
    """Map ctypes c_bool cells to plain bool so DataFrames compare by value.

    Set ``Value`` columns hold distinct c_bool *objects* that never compare
    equal element-wise, which trips assert_frame_equal even on identical data.
    """
    df = df.copy()
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, c_bool)).any():
            df[col] = df[col].map(lambda v: bool(v) if isinstance(v, c_bool) else v)
    return df


def test_have_gams_transfer_is_bool():
    assert isinstance(gdxpds.HAVE_GAMS_TRANSFER, bool)


def test_new_exceptions_subclass_error():
    # Non-breaking: existing ``except Error`` still catches the specific types.
    assert issubclass(BackendError, Error)
    assert issubclass(SymbolNotFoundError, Error)


def test_resolve_backend_default(monkeypatch):
    monkeypatch.delenv("GDXPDS_BACKEND", raising=False)
    assert resolve_backend(None) is Backend.GDXCC


def test_resolve_backend_env(monkeypatch):
    monkeypatch.setenv("GDXPDS_BACKEND", "gdxcc")
    assert resolve_backend(None) is Backend.GDXCC


def test_resolve_backend_kwarg_beats_env(monkeypatch):
    # An (unsatisfiable-here) env value must not override an explicit kwarg.
    monkeypatch.setenv("GDXPDS_BACKEND", "bogus")
    assert resolve_backend("gdxcc") is Backend.GDXCC
    assert resolve_backend(Backend.GDXCC) is Backend.GDXCC


def test_resolve_backend_unknown_raises(monkeypatch):
    monkeypatch.delenv("GDXPDS_BACKEND", raising=False)
    with pytest.raises(BackendError):
        resolve_backend("bogus")


def test_resolve_backend_unknown_env_raises(monkeypatch):
    monkeypatch.setenv("GDXPDS_BACKEND", "bogus")
    with pytest.raises(BackendError):
        resolve_backend(None)


def test_resolve_backend_gams_transfer_capability_gated():
    # Allowed when importable; raises (no silent fallback) when not.
    if gdxpds.HAVE_GAMS_TRANSFER:
        assert resolve_backend("gams_transfer") is Backend.GAMS_TRANSFER
    else:
        with pytest.raises(BackendError):
            resolve_backend("gams_transfer")


def test_info_mentions_transfer_and_default_backend():
    report = gdxpds.info()
    assert "gams.transfer" in report
    assert "Default backend" in report


def test_to_dataframes_explicit_gdxcc(data_dir):
    # Explicitly selecting the default backend is a no-op vs leaving it None.
    gdx_file = os.path.join(data_dir, "set_text_fixture.gdx")
    explicit = to_dataframes(gdx_file, backend="gdxcc")
    implicit = to_dataframes(gdx_file)
    assert set(explicit) == set(implicit)


def test_to_dataframes_bogus_backend_raises(data_dir):
    gdx_file = os.path.join(data_dir, "set_text_fixture.gdx")
    with pytest.raises(BackendError):
        to_dataframes(gdx_file, backend="bogus")


def test_to_dataframes_symbols_subset(data_dir):
    gdx_file = os.path.join(data_dir, "symbol_types_fixture.gdx")
    full = to_dataframes(gdx_file)
    all_names = list(full)
    assert len(all_names) >= 2

    subset = all_names[:2]
    dfs = to_dataframes(gdx_file, symbols=subset)
    # exactly those keys, in the requested order
    assert list(dfs) == subset
    # and equal to the full-read result restricted to them
    for name in subset:
        pd.testing.assert_frame_equal(_normalize(dfs[name]), _normalize(full[name]))


def test_to_dataframes_symbols_empty_and_unknown(data_dir):
    gdx_file = os.path.join(data_dir, "symbol_types_fixture.gdx")
    assert to_dataframes(gdx_file, symbols=[]) == {}
    with pytest.raises(SymbolNotFoundError):
        to_dataframes(gdx_file, symbols=["definitely_not_a_symbol"])


# Every read entry point that gained a backend= kwarg, exercised so a bogus
# value reaches resolve_backend (proving the kwarg threads all the way through).
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda p: to_dataframes(p, backend="bogus"), id="to_dataframes"),
        pytest.param(lambda p: to_dataframe(p, "x", backend="bogus"), id="to_dataframe"),
        pytest.param(lambda p: list_symbols(p, backend="bogus"), id="list_symbols"),
        pytest.param(lambda p: get_data_types(p, backend="bogus"), id="get_data_types"),
        pytest.param(
            lambda p: get_subset_relationships(p, backend="bogus"),
            id="get_subset_relationships",
        ),
    ],
)
def test_read_helpers_thread_backend_kwarg(call, data_dir):
    gdx_file = os.path.join(data_dir, "set_text_fixture.gdx")
    with pytest.raises(BackendError):
        call(gdx_file)


def test_to_gdx_threads_backend_kwarg(data_dir, run_dir):
    dfs = to_dataframes(os.path.join(data_dir, "set_text_fixture.gdx"))
    # explicit default backend writes successfully
    out = os.path.join(run_dir, "backend_kwarg_ok.gdx")
    to_gdx(dfs, out, backend="gdxcc")
    assert os.path.exists(out)
    # bogus backend reaches resolve_backend on the write path
    with pytest.raises(BackendError):
        to_gdx(dfs, os.path.join(run_dir, "backend_kwarg_bogus.gdx"), backend="bogus")
