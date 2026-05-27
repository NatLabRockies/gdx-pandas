"""Quick profiling probe to localize the remaining per-row overhead in the
gdxcc engine's write loop. Compares per-row work decomposed into:

  total = gdxcc.gdxDataWriteStr() + everything else

so we can tell how much of the gdxpds gap to raw is "we're doing extra Python"
vs "we're calling the same C function slower".
"""

import cProfile
import io
import os
import pstats
import tempfile
import time

import numpy as np
import pandas as pd

from gdxpds import to_gdx

N = 500_000


def build_param():
    rng = np.random.default_rng(0)
    n_i, n_j, n_k, n_l, n_m = 500, 100, 10, 10, 5
    idx = np.arange(N)
    return pd.DataFrame(
        {
            "i": np.array([f"i{i}" for i in range(n_i)], dtype=object)[idx % n_i],
            "j": np.array([f"j{i}" for i in range(n_j)], dtype=object)[(idx // n_i) % n_j],
            "k": np.array([f"k{i}" for i in range(n_k)], dtype=object)[
                (idx // (n_i * n_j)) % n_k
            ],
            "l": np.array([f"l{i}" for i in range(n_l)], dtype=object)[
                (idx // (n_i * n_j * n_k)) % n_l
            ],
            "m": np.array([f"m{i}" for i in range(n_m)], dtype=object)[
                (idx // (n_i * n_j * n_k * n_l)) % n_m
            ],
            "Value": rng.standard_normal(N),
        }
    )


def main():
    df = build_param()
    with tempfile.TemporaryDirectory() as d:
        # Warm up
        to_gdx(
            {"warm": pd.DataFrame({"i": ["a"], "Value": [1.0]})},
            os.path.join(d, "warm.gdx"),
            engine="gdxcc",
        )

        # Profile
        out = os.path.join(d, "synth.gdx")
        pr = cProfile.Profile()
        t = time.perf_counter()
        pr.enable()
        to_gdx({"p": df}, out, engine="gdxcc")
        pr.disable()
        elapsed = time.perf_counter() - t

        print(f"\nTotal time: {elapsed:.3f}s")
        s = io.StringIO()
        pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(25)
        print(s.getvalue())


if __name__ == "__main__":
    main()
