"""Document the read/write speed difference between the gdxcc and gams_transfer
engines across the in-tree fixtures (sub-3 KB up to ~1.9 MB), plus a synthetic
large-row Parameter that exercises the per-symbol allocation hotspots driving
issues #65 (memory) and #113 (perf).

These are not pass/fail performance gates -- timings are machine-dependent. Each
test records its measurements; conftest's ``pytest_terminal_summary`` renders a
size-sorted table plus a clear-winner / switchover note at the end of the run.
The only assertion is that both engines actually ran (an engine that errors on a
fixture fails here rather than silently dropping out of the comparison).

Skipped when gams.transfer is unavailable.
"""

import glob
import os
import time
import tracemalloc

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

# Synthetic-fixture size. 500K rows is large enough to amplify per-symbol
# allocation pressure (the GDX is ~5-10 MB; Python peak runs an order of
# magnitude above that on the unoptimized write path) yet small enough to keep
# the default test run under a few seconds per engine.
_SYNTH_ROWS = 500_000


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
    own C-level allocations are not tracked, but the unoptimized
    per-symbol DataFrame copies are entirely Python-side, so this is the
    right measurement for what we are trying to reduce.
    """
    tracemalloc.start()
    try:
        result = fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, peak


@pytest.fixture(scope="session")
def synthetic_param():
    """A ``_SYNTH_ROWS``-row 5-dim Parameter built programmatically.

    Dim labels are produced by treating the row index as a mixed-base integer
    over per-dim UEL pools, so every row has a unique ``(i, j, k, l, m)``
    tuple -- gams.transfer rejects duplicates, and a benchmark that gdxcc
    silently dedupes would compare apples to oranges. The string pools are
    shared (``np.ndarray`` of dtype object, indexed by integer arrays), so
    fixture build stays at one Python string per UEL slot rather than
    ``_SYNTH_ROWS`` distinct strings. Values are float64 from a seeded RNG.
    """
    rng = np.random.default_rng(0)

    # Per-dim UEL counts whose product (>= _SYNTH_ROWS) gives every row a unique
    # tuple. 500 * 100 * 10 * 10 * 5 = 2.5M >= 500K.
    n_i, n_j, n_k, n_l, n_m = 500, 100, 10, 10, 5

    indices = np.arange(_SYNTH_ROWS)
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
            "Value": rng.standard_normal(_SYNTH_ROWS),
        }
    )


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


@pytest.mark.parametrize("engine", ["gdxcc", "gams_transfer"])
def test_synthetic_write_memory(synthetic_param, tmp_path, engine, engine_memory):
    """Peak Python memory and elapsed time for writing the synthetic large-row
    Parameter on each engine. The peak is captured by ``tracemalloc`` around
    the ``to_gdx`` call, after a warm-up write to push first-time engine init
    out of the measured window.

    The acceptance target for v3.1.0 (per the wrap-up plan) is peak Python
    memory <= 3x the resulting GDX's on-disk size. Pre-optimization, the
    unoptimized write paths run several times above that; the test is here to
    record the number so the ratio can be tracked across commits.
    """
    out = str(tmp_path / f"synth_{engine}.gdx")

    # Warm-up: pay first-time engine init / gams.transfer import / GDX handle
    # bring-up costs outside the measured window.
    to_gdx(
        {"warm": pd.DataFrame({"i": ["a"], "Value": [1.0]})},
        str(tmp_path / f"warm_{engine}.gdx"),
        engine=engine,
    )

    t = time.perf_counter()
    _, peak = _peak_python_memory(lambda: to_gdx({"p": synthetic_param}, out, engine=engine))
    elapsed = time.perf_counter() - t

    gdx_mb = os.path.getsize(out) / (1024 * 1024)
    peak_mb = peak / (1024 * 1024)
    ratio = peak_mb / gdx_mb if gdx_mb > 0 else float("inf")

    engine_memory.append(
        {
            "engine": engine,
            "rows": len(synthetic_param),
            "gdx_mb": gdx_mb,
            "peak_mb": peak_mb,
            "ratio": ratio,
            "seconds": elapsed,
        }
    )

    # Sanity only: the engine ran and produced a file (no memory threshold).
    assert gdx_mb > 0
