import os
import shutil
import subprocess

import pytest

import gdxpds.gdx


def pytest_addoption(parser):
    parser.addoption(
        "--no-clean-up",
        action="store_true",
        default=False,
        help="Pass this option to leave test outputs in place",
    )


@pytest.fixture(scope="session")
def clean_up(request):
    return not request.config.getoption("--no-clean-up")


# Rows appended by tests/test_engine_timing.py; rendered once after the run by
# pytest_terminal_summary below. Each row: dict(fixture, size_kb, op, gdxcc,
# gams_transfer, ratio) where ratio = gdxcc / gams_transfer (>1 = transfer faster).
_ENGINE_TIMINGS = []

# Rows appended by tests/test_engine_timing.py::test_synthetic_write_memory.
# Each row: dict(engine, rows, gdx_mb, peak_mb, ratio, seconds) where ratio is
# peak Python memory / GDX on-disk size (v3.1.0 target per wrap-up plan: <= 3x).
_ENGINE_MEMORY = []


@pytest.fixture(scope="session")
def engine_timings():
    return _ENGINE_TIMINGS


@pytest.fixture(scope="session")
def engine_memory():
    return _ENGINE_MEMORY


def _crossover_note(rows, op):
    """Describe the gdxcc<->gams_transfer winner across sizes for one op.

    Rows are sorted by size; returns "clear winner" text if one engine wins at
    every size, else the size band where gams_transfer overtakes gdxcc.
    """
    op_rows = sorted((r for r in rows if r["op"] == op), key=lambda r: r["size_kb"])
    if not op_rows:
        return ""
    wins = [r["ratio"] >= 1.0 for r in op_rows]  # True = transfer faster
    if all(wins):
        return f"{op}: gams_transfer faster at every size tested."
    if not any(wins):
        return f"{op}: gdxcc faster at every size tested (transfer overhead never amortized)."
    first = next(i for i, w in enumerate(wins) if w)
    # A clean single crossover is all-gdxcc-faster (False) up to `first`, then
    # all-transfer-faster (True) at and above it. Anything else -- transfer
    # already winning at the smallest size (first == 0), or wins that flip back
    # and forth (noise / non-monotonic timings) -- has no single switchover band
    # to report, so describe it as mixed rather than invent a bogus band.
    if first == 0 or not all(wins[first:]):
        return f"{op}: mixed results across sizes; no single switchover band (see table above)."
    below = op_rows[first - 1]
    above = op_rows[first]
    return (
        f"{op}: switchover between {below['size_kb']:.1f} KB ({below['fixture']}, gdxcc faster) "
        f"and {above['size_kb']:.1f} KB ({above['fixture']}, transfer faster)."
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    rows = _ENGINE_TIMINGS
    if not rows:
        return
    tr = terminalreporter
    tr.write_sep("=", "engine timing (gdxcc vs gams_transfer)")
    tr.write_line(
        "min seconds over repeated runs; ratio = gdxcc / gams_transfer (>1 = transfer faster)"
    )
    header = f"{'fixture':32s} {'size_KB':>9s} {'op':>5s} {'gdxcc':>9s} {'xfer':>9s} {'ratio':>7s}"
    tr.write_line(header)
    tr.write_line("-" * len(header))
    for r in sorted(rows, key=lambda r: (r["size_kb"], r["op"])):
        tr.write_line(
            f"{r['fixture'][:32]:32s} {r['size_kb']:9.1f} {r['op']:>5s} "
            f"{r['gdxcc']:9.4f} {r['gams_transfer']:9.4f} {r['ratio']:7.2f}"
        )
    for op in ("read", "write"):
        note = _crossover_note(rows, op)
        if note:
            tr.write_line(note)

    mem_rows = _ENGINE_MEMORY
    if mem_rows:
        tr.write_sep("=", "synthetic-write memory (peak Python memory via tracemalloc)")
        tr.write_line(
            "peak Python memory during to_gdx; ratio = peak_MB / gdx_MB "
            "(v3.1.0 target: <= 3x; pre-opt baseline is several x above)"
        )
        header = (
            f"{'engine':16s} {'rows':>10s} {'gdx_MB':>9s} "
            f"{'peak_MB':>10s} {'ratio':>7s} {'seconds':>9s}"
        )
        tr.write_line(header)
        tr.write_line("-" * len(header))
        for r in sorted(mem_rows, key=lambda r: r["engine"]):
            tr.write_line(
                f"{r['engine']:16s} {r['rows']:10,d} {r['gdx_mb']:9.2f} "
                f"{r['peak_mb']:10.2f} {r['ratio']:7.2f} {r['seconds']:9.3f}"
            )


@pytest.fixture(scope="session")
def base_dir():
    return os.path.dirname(__file__)


@pytest.fixture(scope="session")
def data_dir(base_dir):
    return os.path.join(base_dir, "data")


@pytest.fixture(scope="session")
def run_dir(base_dir):
    return os.path.join(base_dir, "output")


@pytest.fixture(scope="session", autouse=True)
def manage_rundir(request, clean_up, run_dir):
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
    os.mkdir(run_dir)

    def finalize_rundir():
        if os.path.exists(run_dir) and clean_up:
            shutil.rmtree(run_dir)

    request.addfinalizer(finalize_rundir)


@pytest.fixture
def roundtrip_one_gdx(data_dir, run_dir):
    """Factory: returns a callable(filename, dirname) -> roundtripped_gdx_path.

    CLI scripts are invoked by entry-point name. pip places `csv_to_gdx` and
    `gdx_to_csv` on PATH after `pip install -e .`. Using subprocess (rather
    than direct in-process calls) keeps each round-trip in a fresh process.
    """

    def _roundtrip(filename, dirname):
        gdx_file = os.path.join(data_dir, filename)
        # Pin gdxcc for the in-process metadata checks below: only gdxcc reports a
        # symbol's num_records before its data are loaded. (The CLI conversions
        # invoked via subprocess still use the default engine.)
        with gdxpds.gdx.GdxFile(engine="gdxcc") as gdx:
            gdx.read(gdx_file)
            num_records = {}
            total_records = 0
            for symbol in gdx:
                num_records[symbol.name] = symbol.num_records
                total_records += num_records[symbol.name]
            assert total_records > 0

        out_dir = os.path.join(run_dir, dirname, os.path.splitext(filename)[0])
        if not os.path.exists(os.path.dirname(out_dir)):
            os.mkdir(os.path.dirname(out_dir))
        subprocess.run(["gdx_to_csv", "-i", gdx_file, "-o", out_dir], check=True)

        txt_file = os.path.join(out_dir, "csvs.txt")
        with open(txt_file, "w") as f:
            for p, _dirs, files in os.walk(out_dir):
                for file in files:
                    if os.path.splitext(file)[1] == ".csv":
                        f.write(f"{os.path.join(p, file)}\n")
                break
        roundtripped_gdx = os.path.join(out_dir, "output.gdx")
        subprocess.run(["csv_to_gdx", "-i", txt_file, "-o", roundtripped_gdx], check=True)

        with gdxpds.gdx.GdxFile(lazy_load=True, engine="gdxcc") as gdx:
            gdx.read(roundtripped_gdx)
            for symbol_name, records in num_records.items():
                if records > 0:
                    assert symbol_name in gdx, f"Expected {symbol_name} in {roundtripped_gdx}."
                    assert gdx[symbol_name].num_records == records, (
                        f"Expected {symbol_name} in {roundtripped_gdx} to have {records} records, but has {gdx[symbol_name].num_records}."
                    )
        with gdxpds.gdx.GdxFile(lazy_load=False) as gdx:
            gdx.read(roundtripped_gdx)
            for symbol_name, records in num_records.items():
                if records > 0:
                    assert symbol_name in gdx, f"Expected {symbol_name} in {roundtripped_gdx}."
                    assert gdx[symbol_name].num_records == records, (
                        f"Expected {symbol_name} in {roundtripped_gdx} to have {records} records, but has {gdx[symbol_name].num_records}."
                    )

        return roundtripped_gdx

    return _roundtrip
