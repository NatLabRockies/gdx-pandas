"""Coverage for the Engine enum, capability flag, engine resolution, and the
to_dataframes(symbols=...) subset feature."""

import os

import pandas as pd
import pytest

import gdxpds
from gdxpds import (
    DomainError,
    Engine,
    EngineError,
    SymbolNotFoundError,
    TransferError,
    get_data_types,
    get_subset_relationships,
    list_symbols,
    to_dataframe,
    to_dataframes,
    to_gdx,
)
from gdxpds._engine import resolve_engine
from gdxpds.tools import Error


def test_have_gams_transfer_is_bool():
    assert isinstance(gdxpds.HAVE_GAMS_TRANSFER, bool)


def test_new_exceptions_subclass_error():
    # Non-breaking: existing ``except Error`` still catches the specific types.
    assert issubclass(EngineError, Error)
    assert issubclass(SymbolNotFoundError, Error)
    assert issubclass(TransferError, Error)
    assert issubclass(DomainError, Error)


def test_transfer_error_on_bad_read(tmp_path):
    # A gams.transfer I/O failure surfaces as TransferError (an Error subclass).
    if not gdxpds.HAVE_GAMS_TRANSFER:
        pytest.skip("gams.transfer not available")
    missing = tmp_path / "does_not_exist.gdx"
    with pytest.raises(TransferError):
        to_dataframes(str(missing), engine="gams_transfer")


def test_resolve_engine_default(monkeypatch):
    # The default prefers gams.transfer when usable, falling back to gdxcc.
    monkeypatch.delenv("GDXPDS_ENGINE", raising=False)
    expected = Engine.GAMS_TRANSFER if gdxpds.HAVE_GAMS_TRANSFER else Engine.GDXCC
    assert resolve_engine(None) is expected


def test_resolve_engine_env(monkeypatch):
    monkeypatch.setenv("GDXPDS_ENGINE", "gdxcc")
    assert resolve_engine(None) is Engine.GDXCC


def test_resolve_engine_kwarg_beats_env(monkeypatch):
    # An (unsatisfiable-here) env value must not override an explicit kwarg.
    monkeypatch.setenv("GDXPDS_ENGINE", "bogus")
    assert resolve_engine("gdxcc") is Engine.GDXCC
    assert resolve_engine(Engine.GDXCC) is Engine.GDXCC


def test_resolve_engine_unknown_raises(monkeypatch):
    monkeypatch.delenv("GDXPDS_ENGINE", raising=False)
    with pytest.raises(EngineError):
        resolve_engine("bogus")


def test_resolve_engine_unknown_env_raises(monkeypatch):
    monkeypatch.setenv("GDXPDS_ENGINE", "bogus")
    with pytest.raises(EngineError):
        resolve_engine(None)


def test_resolve_engine_gams_transfer_capability_gated():
    # Allowed when importable; raises (no silent fallback) when not.
    if gdxpds.HAVE_GAMS_TRANSFER:
        assert resolve_engine("gams_transfer") is Engine.GAMS_TRANSFER
    else:
        with pytest.raises(EngineError):
            resolve_engine("gams_transfer")


def test_info_mentions_transfer_and_default_engine():
    report = gdxpds.info()
    assert "gams.transfer" in report
    assert "Default engine" in report


def test_to_dataframes_explicit_gdxcc(data_dir):
    # Pinning gdxcc reads the same symbols as the resolved default engine.
    gdx_file = os.path.join(data_dir, "set_text_fixture.gdx")
    explicit = to_dataframes(gdx_file, engine="gdxcc")
    implicit = to_dataframes(gdx_file)
    assert set(explicit) == set(implicit)


def test_to_dataframes_bogus_engine_raises(data_dir):
    gdx_file = os.path.join(data_dir, "set_text_fixture.gdx")
    with pytest.raises(EngineError):
        to_dataframes(gdx_file, engine="bogus")


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
        pd.testing.assert_frame_equal(dfs[name], full[name])


def test_to_dataframes_symbols_empty_and_unknown(data_dir):
    gdx_file = os.path.join(data_dir, "symbol_types_fixture.gdx")
    assert to_dataframes(gdx_file, symbols=[]) == {}
    with pytest.raises(SymbolNotFoundError):
        to_dataframes(gdx_file, symbols=["definitely_not_a_symbol"])


# Every read entry point that gained an engine= kwarg, exercised so a bogus
# value reaches resolve_engine (proving the kwarg threads all the way through).
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda p: to_dataframes(p, engine="bogus"), id="to_dataframes"),
        pytest.param(lambda p: to_dataframe(p, "x", engine="bogus"), id="to_dataframe"),
        pytest.param(lambda p: list_symbols(p, engine="bogus"), id="list_symbols"),
        pytest.param(lambda p: get_data_types(p, engine="bogus"), id="get_data_types"),
        pytest.param(
            lambda p: get_subset_relationships(p, engine="bogus"),
            id="get_subset_relationships",
        ),
    ],
)
def test_read_helpers_thread_engine_kwarg(call, data_dir):
    gdx_file = os.path.join(data_dir, "set_text_fixture.gdx")
    with pytest.raises(EngineError):
        call(gdx_file)


def test_to_gdx_threads_engine_kwarg(data_dir, run_dir):
    dfs = to_dataframes(os.path.join(data_dir, "set_text_fixture.gdx"))
    # explicit default engine writes successfully
    out = os.path.join(run_dir, "engine_kwarg_ok.gdx")
    to_gdx(dfs, out, engine="gdxcc")
    assert os.path.exists(out)
    # bogus engine reaches resolve_engine on the write path
    with pytest.raises(EngineError):
        to_gdx(dfs, os.path.join(run_dir, "engine_kwarg_bogus.gdx"), engine="bogus")
