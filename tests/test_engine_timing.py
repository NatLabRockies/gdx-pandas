"""Document the read/write speed difference between the gdxcc and gams_transfer
engines across the in-tree fixtures (sub-3 KB up to ~1.9 MB).

These are not pass/fail performance gates -- timings are machine-dependent. Each
test records its measurements; conftest's ``pytest_terminal_summary`` renders a
size-sorted table plus a clear-winner / switchover note at the end of the run.
The only assertion is that both engines actually ran (a engine that errors on a
fixture fails here rather than silently dropping out of the comparison).

Skipped when gams.transfer is unavailable.
"""

import glob
import os
import time

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


def _min_time(fn, repeats=_REPEATS):
    best = float("inf")
    for _ in range(repeats):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best


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
