"""Document the read/write speed difference between the gdxcc and gams_transfer
engines across the in-tree fixtures (sub-3 KB up to ~1.9 MB), plus a synthetic
large-row Parameter that exercises the per-symbol allocation hotspots driving
issues #65 (memory) and #113 (perf).

Two scales:

- Default: 500K rows. Runs every ``pytest tests`` invocation; fast enough not
  to dominate the suite.
- Large: 5M rows, gated by the ``slow`` marker. Opt in with ``pytest -m slow``
  to confirm the default-scale ratios still hold at issue #65's order of
  magnitude (29M-row Parameters reported in 2019).

For both scales, every (engine, op) pair (gdxcc/gams_transfer, raw equivalents,
read and write) appears as one row in the synthetic-IO table rendered by
conftest's ``pytest_terminal_summary``. None of these are pass/fail gates --
timings are machine-dependent; the only assertion is that the engine actually
ran. The wrap-up plan's acceptance ratios (gdxcc write <= 1.3x raw_gdxcc;
gams_transfer write <= 1.5x raw_transfer) are read off the ``x raw`` column.

Skipped when gams.transfer is unavailable.
"""

import glob
import os
import time
import tracemalloc
from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest

import gdxpds
from gdxpds import to_dataframes, to_gdx

pytestmark = pytest.mark.skipif(not gdxpds.HAVE_GAMS_TRANSFER, reason="gams.transfer not available")

FIXTURES = sorted(
    os.path.basename(p) for p in glob.glob(os.path.join(os.path.dirname(__file__), "data", "*.gdx"))
)

# Repeats per measurement; the minimum is reported (least perturbed by noise).
# Small because this runs in the default suite and the largest fixture is ~1.9 MB.
_REPEATS = 3

# Default synthetic-fixture size. 500K rows is large enough to amplify per-symbol
# allocation pressure (the GDX is ~5-10 MB; Python peak runs an order of
# magnitude above that on the unoptimized write path) yet small enough to keep
# the default test run under a few seconds per engine. The slow-marked
# ``synthetic_param_large`` fixture below runs at 5M rows for scaling probes.
_SYNTH_ROWS = 500_000
_SYNTH_ROWS_LARGE = 5_000_000


def _min_time(fn, repeats=_REPEATS):
    best = float("inf")
    for _ in range(repeats):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best


def _peak_python_memory(fn):
    """Run ``fn`` under ``tracemalloc`` and return ``(result, peak_bytes)``.

    Captures Python-allocator memory, which is what numpy/pandas buffers and
    Python string objects flow through -- exactly the allocations that drove
    the 18 GB peak on a 0.4 GB GDX in issue #65. The GDX shared library's
    own C-level allocations are not tracked, but the per-symbol DataFrame
    copies are entirely Python-side, so this is the right measurement for
    what we are trying to reduce.

    NOTE: ``tracemalloc`` slows the workload by a factor that depends on the
    allocation pattern (a few percent to several x), so timings collected
    under it are NOT reliable. Run the timed call outside this helper and use
    this only when peak memory is what you want.
    """
    tracemalloc.start()
    try:
        result = fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, peak


def _build_synthetic_param(n_rows: int) -> pd.DataFrame:
    """Build an ``n_rows``-row 5-dim Parameter with unique ``(i, j, k, l, m)``
    tuples. Dim labels come from per-dim UEL pools indexed by mixed-base
    arithmetic on the row number, so every row has a unique tuple
    (gams.transfer rejects duplicate keys). Values are float64 from a seeded
    RNG so runs are repeatable.

    Pool sizes: 500 / 100 / 10 / 10 / 5 -> 2.5M distinct tuples, enough headroom
    for ``_SYNTH_ROWS`` (500K) and ``_SYNTH_ROWS_LARGE`` (5M); the latter
    consumes ``5_000_000 / (500*100*10*10*5) = 2`` -> not unique. Bump the
    leading dims for large.
    """
    n_i, n_j, n_k, n_l, n_m = (
        (5000, 100, 10, 10, 5) if n_rows > 2_500_000 else (500, 100, 10, 10, 5)
    )
    if n_rows > n_i * n_j * n_k * n_l * n_m:
        raise ValueError(f"_build_synthetic_param: pool too small for {n_rows} unique rows")

    rng = np.random.default_rng(0)
    indices = np.arange(n_rows)
    i_idx = indices % n_i
    j_idx = (indices // n_i) % n_j
    k_idx = (indices // (n_i * n_j)) % n_k
    l_idx = (indices // (n_i * n_j * n_k)) % n_l
    m_idx = (indices // (n_i * n_j * n_k * n_l)) % n_m

    def pool(prefix, n_vals):
        return np.array([f"{prefix}{i}" for i in range(n_vals)], dtype=object)

    return pd.DataFrame(
        {
            "i": pool("i", n_i)[i_idx],
            "j": pool("j", n_j)[j_idx],
            "k": pool("k", n_k)[k_idx],
            "l": pool("l", n_l)[l_idx],
            "m": pool("m", n_m)[m_idx],
            "Value": rng.standard_normal(n_rows),
        }
    )


@pytest.fixture(scope="session")
def synthetic_param():
    """500K-row 5-dim Parameter; runs every test session."""
    return _build_synthetic_param(_SYNTH_ROWS)


@pytest.fixture(scope="session")
def synthetic_param_large():
    """5M-row 5-dim Parameter; only built when slow tests are selected."""
    return _build_synthetic_param(_SYNTH_ROWS_LARGE)


@pytest.fixture(scope="session")
def synthetic_gdx(synthetic_param, tmp_path_factory):
    """Write ``synthetic_param`` once to a session-scoped GDX so read tests
    don't have to rebuild it per engine."""
    path = str(tmp_path_factory.mktemp("synth") / "synth.gdx")
    to_gdx({"p": synthetic_param}, path, engine="gdxcc")
    return path


@pytest.fixture(scope="session")
def synthetic_gdx_large(synthetic_param_large, tmp_path_factory):
    """5M-row session-scoped GDX for slow-marked read tests."""
    path = str(tmp_path_factory.mktemp("synth_large") / "synth_large.gdx")
    to_gdx({"p": synthetic_param_large}, path, engine="gdxcc")
    return path


@pytest.mark.parametrize("fixture", FIXTURES)
def test_engine_timing(data_dir, fixture, tmp_path, engine_timings):
    path = os.path.join(data_dir, fixture)
    size_kb = os.path.getsize(path) / 1024.0

    # Read (eager / bulk path -- the same one to_dataframes uses).
    read_g = _min_time(lambda: to_dataframes(path, engine="gdxcc"))
    read_t = _min_time(lambda: to_dataframes(path, engine="gams_transfer"))

    # Write: read once (untimed) to get DataFrames, then time each engine's write.
    dfs = to_dataframes(path, engine="gdxcc")
    write_g = _min_time(lambda: to_gdx(dfs, str(tmp_path / "g.gdx"), engine="gdxcc"))
    write_t = _min_time(lambda: to_gdx(dfs, str(tmp_path / "t.gdx"), engine="gams_transfer"))

    engine_timings.append(
        {
            "fixture": fixture,
            "size_kb": size_kb,
            "op": "read",
            "gdxcc": read_g,
            "gams_transfer": read_t,
            "ratio": read_g / read_t,
        }
    )
    engine_timings.append(
        {
            "fixture": fixture,
            "size_kb": size_kb,
            "op": "write",
            "gdxcc": write_g,
            "gams_transfer": write_t,
            "ratio": write_g / write_t,
        }
    )

    # Sanity only: both engines ran for both ops (no timing threshold).
    assert min(read_g, read_t, write_g, write_t) > 0


# -- Raw-engine baselines -----------------------------------------------------


def _raw_gdxcc_write(df: pd.DataFrame, num_dims: int, out_path: str, gams_dir: str) -> None:
    """Pre-cast, pre-stringified ``gdxDataWriteStr`` loop. Floor against which
    the gdxpds gdxcc engine is measured (wrap-up plan target: gdxpds write
    <= 1.3x this loop at 1M rows)."""
    try:
        from gams.core import gdx as gdxcc
    except ImportError:
        import gdxcc  # type: ignore[no-redef]

    from gdxpds.tools import _GdxHandle

    dim_lists = df.iloc[:, :num_dims].astype(str).to_numpy().tolist()
    value_arr = df.iloc[:, num_dims].to_numpy(dtype=np.float64, copy=True)
    n = len(df)

    with _GdxHandle(gdxcc, gams_dir, "raw-baseline") as h:
        H = h.H
        if not gdxcc.gdxOpenWrite(H, out_path, "raw-baseline"):
            raise RuntimeError("gdxOpenWrite failed in raw baseline")
        gdxcc.gdxDataWriteStrStart(H, "p", "", num_dims, 1, 0)  # 1 = GMS_DT_PAR
        values = gdxcc.doubleArray(gdxcc.GMS_VAL_MAX)
        for r in range(n):
            values[0] = value_arr[r]
            gdxcc.gdxDataWriteStr(H, dim_lists[r], values)
        gdxcc.gdxDataWriteDone(H)
        gdxcc.gdxClose(H)


def _raw_gdxcc_read(path: str, gams_dir: str) -> None:
    """Bare ``gdxDataReadStr`` loop pulling all records from the single
    Parameter at index 1 into Python lists. Floor for gdxpds gdxcc reads."""
    try:
        from gams.core import gdx as gdxcc
    except ImportError:
        import gdxcc  # type: ignore[no-redef]

    from gdxpds.tools import _GdxHandle

    with _GdxHandle(gdxcc, gams_dir, "raw-baseline") as h:
        H = h.H
        if not gdxcc.gdxOpenRead(H, path)[0]:
            raise RuntimeError("gdxOpenRead failed in raw baseline")
        # Symbol 1 is the Parameter (symbol 0 is the universal set).
        ret, _name, dims, _data_type = gdxcc.gdxSymbolInfo(H, 1)
        if ret != 1:
            raise RuntimeError("gdxSymbolInfo failed in raw baseline")
        ret, n_records = gdxcc.gdxDataReadStrStart(H, 1)
        if not ret:
            raise RuntimeError("gdxDataReadStrStart failed in raw baseline")
        dim_cols: list[list[str]] = [[] for _ in range(dims)]
        value_col: list[float] = []
        for _ in range(n_records):
            _ret, elems, vals, _afdim = gdxcc.gdxDataReadStr(H)
            for j in range(dims):
                dim_cols[j].append(elems[j])
            value_col.append(vals[0])
        gdxcc.gdxDataReadDone(H)
        gdxcc.gdxClose(H)


def _raw_transfer_write(df: pd.DataFrame, num_dims: int, out_path: str, gams_dir: str) -> None:
    """Minimum-effort ``gams.transfer`` write. Floor for gdxpds transfer
    engine writes."""
    import gams.transfer as gt

    new_cols = list(df.columns[:num_dims]) + ["value"]
    records = df.set_axis(new_cols, axis=1)
    container = gt.Container(system_directory=gams_dir)
    gt.Parameter(container, "p", domain=["*"] * num_dims, records=records)
    container.write(out_path, eps_to_zero=False)


def _raw_transfer_read(path: str, gams_dir: str) -> None:
    """Minimum-effort ``gams.transfer`` read. Floor for gdxpds transfer
    engine reads."""
    import gams.transfer as gt

    container = gt.Container(system_directory=gams_dir)
    container.read(path)


# -- Measurement helpers ------------------------------------------------------


def _record_io(
    *,
    engine: str,
    op: str,
    rows: int,
    out_for_size: str,
    time_fn: Callable[[], object],
    mem_fn: Callable[[], object],
    engine_memory: list,
) -> None:
    """Run a two-pass measurement: one ``time_fn`` outside tracemalloc for an
    honest wall-clock, one ``mem_fn`` inside tracemalloc for peak Python
    memory. Append one row to ``engine_memory``."""
    t = time.perf_counter()
    time_fn()
    elapsed = time.perf_counter() - t
    _, peak = _peak_python_memory(mem_fn)
    gdx_mb = os.path.getsize(out_for_size) / (1024 * 1024)
    peak_mb = peak / (1024 * 1024)
    engine_memory.append(
        {
            "engine": engine,
            "op": op,
            "rows": rows,
            "gdx_mb": gdx_mb,
            "peak_mb": peak_mb,
            "ratio": peak_mb / gdx_mb if gdx_mb > 0 else float("inf"),
            "seconds": elapsed,
        }
    )


# -- Default-scale tests (500K rows; run every session) ----------------------


@pytest.mark.parametrize("engine", ["gdxcc", "gams_transfer"])
def test_synthetic_write_memory(synthetic_param, tmp_path, engine, engine_memory):
    """Time + peak memory for writing the 500K-row Parameter through each
    gdxpds engine."""
    out_time = str(tmp_path / f"w_{engine}.time.gdx")
    out_mem = str(tmp_path / f"w_{engine}.mem.gdx")
    to_gdx(  # warm-up
        {"warm": pd.DataFrame({"i": ["a"], "Value": [1.0]})},
        str(tmp_path / f"warm_w_{engine}.gdx"),
        engine=engine,
    )
    _record_io(
        engine=engine,
        op="write",
        rows=len(synthetic_param),
        out_for_size=out_time,
        time_fn=lambda: to_gdx({"p": synthetic_param}, out_time, engine=engine),
        mem_fn=lambda: to_gdx({"p": synthetic_param}, out_mem, engine=engine),
        engine_memory=engine_memory,
    )


@pytest.mark.parametrize("engine", ["gdxcc", "gams_transfer"])
def test_synthetic_read_memory(synthetic_param, synthetic_gdx, engine, engine_memory):
    """Time + peak memory for reading the 500K-row Parameter back through each
    gdxpds engine."""
    to_dataframes(synthetic_gdx, engine=engine)  # warm-up
    _record_io(
        engine=engine,
        op="read",
        rows=len(synthetic_param),
        out_for_size=synthetic_gdx,
        time_fn=lambda: to_dataframes(synthetic_gdx, engine=engine),
        mem_fn=lambda: to_dataframes(synthetic_gdx, engine=engine),
        engine_memory=engine_memory,
    )


def test_raw_gdxcc_write_baseline(synthetic_param, tmp_path, engine_memory):
    """Raw-gdxcc write floor (500K rows)."""
    from gdxpds.tools import GamsDirFinder

    df = synthetic_param
    num_dims = len(df.columns) - 1
    gams_dir = GamsDirFinder().gams_dir
    _raw_gdxcc_write(  # warm-up
        df.head(1), num_dims, str(tmp_path / "warm_raw_w_gdxcc.gdx"), gams_dir
    )
    out_time = str(tmp_path / "raw_w_gdxcc.time.gdx")
    out_mem = str(tmp_path / "raw_w_gdxcc.mem.gdx")
    _record_io(
        engine="raw_gdxcc",
        op="write",
        rows=len(df),
        out_for_size=out_time,
        time_fn=lambda: _raw_gdxcc_write(df, num_dims, out_time, gams_dir),
        mem_fn=lambda: _raw_gdxcc_write(df, num_dims, out_mem, gams_dir),
        engine_memory=engine_memory,
    )


def test_raw_gdxcc_read_baseline(synthetic_param, synthetic_gdx, engine_memory):
    """Raw-gdxcc read floor (500K rows)."""
    from gdxpds.tools import GamsDirFinder

    gams_dir = GamsDirFinder().gams_dir
    _raw_gdxcc_read(synthetic_gdx, gams_dir)  # warm-up
    _record_io(
        engine="raw_gdxcc",
        op="read",
        rows=len(synthetic_param),
        out_for_size=synthetic_gdx,
        time_fn=lambda: _raw_gdxcc_read(synthetic_gdx, gams_dir),
        mem_fn=lambda: _raw_gdxcc_read(synthetic_gdx, gams_dir),
        engine_memory=engine_memory,
    )


def test_raw_transfer_write_baseline(synthetic_param, tmp_path, engine_memory):
    """Raw-transfer write floor (500K rows)."""
    from gdxpds.tools import GamsDirFinder

    df = synthetic_param
    num_dims = len(df.columns) - 1
    gams_dir = GamsDirFinder().gams_dir
    _raw_transfer_write(  # warm-up
        df.head(1), num_dims, str(tmp_path / "warm_raw_w_transfer.gdx"), gams_dir
    )
    out_time = str(tmp_path / "raw_w_transfer.time.gdx")
    out_mem = str(tmp_path / "raw_w_transfer.mem.gdx")
    _record_io(
        engine="raw_transfer",
        op="write",
        rows=len(df),
        out_for_size=out_time,
        time_fn=lambda: _raw_transfer_write(df, num_dims, out_time, gams_dir),
        mem_fn=lambda: _raw_transfer_write(df, num_dims, out_mem, gams_dir),
        engine_memory=engine_memory,
    )


def test_raw_transfer_read_baseline(synthetic_param, synthetic_gdx, engine_memory):
    """Raw-transfer read floor (500K rows)."""
    from gdxpds.tools import GamsDirFinder

    gams_dir = GamsDirFinder().gams_dir
    _raw_transfer_read(synthetic_gdx, gams_dir)  # warm-up
    _record_io(
        engine="raw_transfer",
        op="read",
        rows=len(synthetic_param),
        out_for_size=synthetic_gdx,
        time_fn=lambda: _raw_transfer_read(synthetic_gdx, gams_dir),
        mem_fn=lambda: _raw_transfer_read(synthetic_gdx, gams_dir),
        engine_memory=engine_memory,
    )


# -- Large-scale scaling probes (5M rows; slow-marked) -----------------------


@pytest.mark.slow
@pytest.mark.parametrize("engine", ["gdxcc", "gams_transfer"])
def test_synthetic_write_memory_large(synthetic_param_large, tmp_path, engine, engine_memory):
    """Scaling probe at 5M rows: confirms the default-scale ratios hold an
    order of magnitude up (approaching issue #65's 29M-row scale)."""
    out_time = str(tmp_path / f"w_{engine}_5m.time.gdx")
    out_mem = str(tmp_path / f"w_{engine}_5m.mem.gdx")
    to_gdx(  # warm-up
        {"warm": pd.DataFrame({"i": ["a"], "Value": [1.0]})},
        str(tmp_path / f"warm_w_{engine}_5m.gdx"),
        engine=engine,
    )
    _record_io(
        engine=engine,
        op="write",
        rows=len(synthetic_param_large),
        out_for_size=out_time,
        time_fn=lambda: to_gdx({"p": synthetic_param_large}, out_time, engine=engine),
        mem_fn=lambda: to_gdx({"p": synthetic_param_large}, out_mem, engine=engine),
        engine_memory=engine_memory,
    )


@pytest.mark.slow
@pytest.mark.parametrize("engine", ["gdxcc", "gams_transfer"])
def test_synthetic_read_memory_large(
    synthetic_param_large, synthetic_gdx_large, engine, engine_memory
):
    """5M-row read scaling probe."""
    to_dataframes(synthetic_gdx_large, engine=engine)  # warm-up
    _record_io(
        engine=engine,
        op="read",
        rows=len(synthetic_param_large),
        out_for_size=synthetic_gdx_large,
        time_fn=lambda: to_dataframes(synthetic_gdx_large, engine=engine),
        mem_fn=lambda: to_dataframes(synthetic_gdx_large, engine=engine),
        engine_memory=engine_memory,
    )


@pytest.mark.slow
def test_raw_gdxcc_write_baseline_large(synthetic_param_large, tmp_path, engine_memory):
    from gdxpds.tools import GamsDirFinder

    df = synthetic_param_large
    num_dims = len(df.columns) - 1
    gams_dir = GamsDirFinder().gams_dir
    _raw_gdxcc_write(df.head(1), num_dims, str(tmp_path / "warm_raw_w_gdxcc_5m.gdx"), gams_dir)
    out_time = str(tmp_path / "raw_w_gdxcc_5m.time.gdx")
    out_mem = str(tmp_path / "raw_w_gdxcc_5m.mem.gdx")
    _record_io(
        engine="raw_gdxcc",
        op="write",
        rows=len(df),
        out_for_size=out_time,
        time_fn=lambda: _raw_gdxcc_write(df, num_dims, out_time, gams_dir),
        mem_fn=lambda: _raw_gdxcc_write(df, num_dims, out_mem, gams_dir),
        engine_memory=engine_memory,
    )


@pytest.mark.slow
def test_raw_gdxcc_read_baseline_large(synthetic_param_large, synthetic_gdx_large, engine_memory):
    from gdxpds.tools import GamsDirFinder

    gams_dir = GamsDirFinder().gams_dir
    _raw_gdxcc_read(synthetic_gdx_large, gams_dir)
    _record_io(
        engine="raw_gdxcc",
        op="read",
        rows=len(synthetic_param_large),
        out_for_size=synthetic_gdx_large,
        time_fn=lambda: _raw_gdxcc_read(synthetic_gdx_large, gams_dir),
        mem_fn=lambda: _raw_gdxcc_read(synthetic_gdx_large, gams_dir),
        engine_memory=engine_memory,
    )


@pytest.mark.slow
def test_raw_transfer_write_baseline_large(synthetic_param_large, tmp_path, engine_memory):
    from gdxpds.tools import GamsDirFinder

    df = synthetic_param_large
    num_dims = len(df.columns) - 1
    gams_dir = GamsDirFinder().gams_dir
    _raw_transfer_write(
        df.head(1), num_dims, str(tmp_path / "warm_raw_w_transfer_5m.gdx"), gams_dir
    )
    out_time = str(tmp_path / "raw_w_transfer_5m.time.gdx")
    out_mem = str(tmp_path / "raw_w_transfer_5m.mem.gdx")
    _record_io(
        engine="raw_transfer",
        op="write",
        rows=len(df),
        out_for_size=out_time,
        time_fn=lambda: _raw_transfer_write(df, num_dims, out_time, gams_dir),
        mem_fn=lambda: _raw_transfer_write(df, num_dims, out_mem, gams_dir),
        engine_memory=engine_memory,
    )


@pytest.mark.slow
def test_raw_transfer_read_baseline_large(
    synthetic_param_large, synthetic_gdx_large, engine_memory
):
    from gdxpds.tools import GamsDirFinder

    gams_dir = GamsDirFinder().gams_dir
    _raw_transfer_read(synthetic_gdx_large, gams_dir)
    _record_io(
        engine="raw_transfer",
        op="read",
        rows=len(synthetic_param_large),
        out_for_size=synthetic_gdx_large,
        time_fn=lambda: _raw_transfer_read(synthetic_gdx_large, gams_dir),
        mem_fn=lambda: _raw_transfer_read(synthetic_gdx_large, gams_dir),
        engine_memory=engine_memory,
    )
