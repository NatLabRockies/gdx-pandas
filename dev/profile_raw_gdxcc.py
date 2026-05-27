"""Sibling of _profile_gdxcc_write.py for the raw gdxDataWriteStr baseline.
Lets us read off the per-row Python overhead added by gdxpds vs the minimum
required by the SWIG bindings."""

import cProfile
import io
import os
import pstats
import tempfile
import time

import numpy as np
import pandas as pd

from gdxpds.tools import GamsDirFinder

try:
    from gams.core import gdx as gdxcc
except ImportError:
    import gdxcc  # type: ignore[no-redef]

from gdxpds.tools import _GdxHandle

N = 500_000


def build_param():
    rng = np.random.default_rng(0)
    n_i, n_j, n_k, n_l, n_m = 500, 100, 10, 10, 5
    idx = np.arange(N)
    return pd.DataFrame(
        {
            "i": np.array([f"i{i}" for i in range(n_i)], dtype=object)[idx % n_i],
            "j": np.array([f"j{i}" for i in range(n_j)], dtype=object)[(idx // n_i) % n_j],
            "k": np.array([f"k{i}" for i in range(n_k)], dtype=object)[(idx // (n_i * n_j)) % n_k],
            "l": np.array([f"l{i}" for i in range(n_l)], dtype=object)[
                (idx // (n_i * n_j * n_k)) % n_l
            ],
            "m": np.array([f"m{i}" for i in range(n_m)], dtype=object)[
                (idx // (n_i * n_j * n_k * n_l)) % n_m
            ],
            "Value": rng.standard_normal(N),
        }
    )


def raw_write(df, out, gams_dir):
    num_dims = 5
    dim_lists = df.iloc[:, :num_dims].astype(str).to_numpy().tolist()
    value_arr = df.iloc[:, num_dims].to_numpy(dtype=np.float64, copy=True)
    n = len(df)
    with _GdxHandle(gdxcc, gams_dir, "raw") as h:
        H = h.H
        gdxcc.gdxOpenWrite(H, out, "raw")
        gdxcc.gdxDataWriteStrStart(H, "p", "", num_dims, 1, 0)
        values = gdxcc.doubleArray(gdxcc.GMS_VAL_MAX)
        for r in range(n):
            values[0] = value_arr[r]
            gdxcc.gdxDataWriteStr(H, dim_lists[r], values)
        gdxcc.gdxDataWriteDone(H)
        gdxcc.gdxClose(H)


def main():
    df = build_param()
    gams_dir = GamsDirFinder().gams_dir
    with tempfile.TemporaryDirectory() as d:
        raw_write(df.head(1), os.path.join(d, "warm.gdx"), gams_dir)  # warm-up

        out = os.path.join(d, "synth.gdx")
        pr = cProfile.Profile()
        t = time.perf_counter()
        pr.enable()
        raw_write(df, out, gams_dir)
        pr.disable()
        elapsed = time.perf_counter() - t

        print(f"\nTotal time: {elapsed:.3f}s")
        s = io.StringIO()
        pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(20)
        print(s.getvalue())


if __name__ == "__main__":
    main()
