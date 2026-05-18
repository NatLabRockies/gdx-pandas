import os
import subprocess
import sys

import pytest

import gdxpds
import gdxpds.tools

# A path that exists but is not a GAMS install. Used to drive negative paths
# without needing to mock anything.
NOT_GAMS_DIR = "C:\\Windows" if os.name == "nt" else "/tmp"

# Labels that gdxpds.info() always emits on the happy path.
INFO_LABELS = ("gdxpds:", "Python:", "Bindings:", "GAMS_DIR:")


def run_cli(*args):
    """Invoke the gdxpds CLI in a subprocess, PATH-independent."""
    return subprocess.run(
        [sys.executable, "-m", "gdxpds.cli.main", *args],
        capture_output=True, text=True,
    )


# ---------------------------------------------------------------- gdxpds.info()

def test_info_returns_str_with_expected_fields():
    # load_gdxcc shows up only on failure, by design.
    report = gdxpds.info()
    assert isinstance(report, str)
    for label in INFO_LABELS:
        assert label in report, f"missing field {label!r} in info() output"


def test_info_never_raises():
    gdxpds.info()


def test_info_override_reflected_in_report():
    report = gdxpds.info(gams_dir=NOT_GAMS_DIR)
    assert NOT_GAMS_DIR.replace("\\", "/") in report
    assert "source:      explicit override" in report


# ----------------------------------------------------------------- GamsDirFinder

def test_gams_dir_finder_explicit_override_recorded():
    finder = gdxpds.tools.GamsDirFinder(gams_dir=NOT_GAMS_DIR)
    assert finder.source == "explicit override"


def test_gams_dir_finder_records_source_in_env():
    finder = gdxpds.tools.GamsDirFinder()
    try:
        finder.gams_dir
    except RuntimeError:
        pytest.skip("no GAMS installation discoverable in this environment")
    assert isinstance(finder.source, str) and finder.source


# -------------------------------------------------------------------- load_gdxcc

def test_load_gdxcc_raises_for_non_gams_dir():
    with pytest.raises(gdxpds.GamsLoadError, match=r"not a GAMS installation"):
        gdxpds.load_gdxcc(gams_dir=NOT_GAMS_DIR)


# ---------------------------------------------------------------- CLI surface

def test_cli_version_flag():
    result = run_cli("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == f"gdxpds {gdxpds.__version__}"


def test_cli_info_subcommand():
    result = run_cli("info")
    assert result.returncode == 0
    for label in INFO_LABELS:
        assert label in result.stdout


def test_cli_info_with_gams_dir_override():
    result = run_cli("info", "-g", NOT_GAMS_DIR)
    assert result.returncode == 0
    assert "source:      explicit override" in result.stdout


def test_cli_test_runs_against_local_gams():
    # Smoke test of the full `gdxpds test` happy path against the local
    # environment. Exercises argparse dispatch and the five internal checks.
    result = run_cli("test")
    assert result.returncode == 0, f"gdxpds test failed:\n{result.stdout}\n{result.stderr}"


def test_cli_test_prints_info_header_first():
    out = run_cli("test").stdout
    info_idx = out.index("gdxpds:")
    check_idx = next(
        (out.index(s) for s in ("[OK]", "[FAIL]", "PASSED", "FAILED") if s in out),
        len(out),
    )
    assert info_idx < check_idx, "info() header must precede the per-step check lines"


def test_cli_test_with_bad_gams_dir_fails_cleanly():
    # The -g/--gams_dir override should fail at _check_gams_install (rather
    # than crashing or spuriously passing) when pointed at a non-GAMS dir.
    result = run_cli("test", "-g", NOT_GAMS_DIR)
    assert result.returncode == 1
    assert "FAILED" in result.stdout
    assert "not a GAMS installation" in result.stderr
