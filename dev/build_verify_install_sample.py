"""Generate src/gdxpds/_verify_install/sample.gdx.

The resulting sample.gdx is committed to the repo; this script only needs to
be re-run if the schema of the install verification test changes.

Usage (from repo root, with the venv active and $env:GAMS_DIR set):

    python dev\\build_verify_install_sample.py

Schema (chosen to exercise the distinct code paths gdxpds handles):
  Set       t : 1D, 3 elements
  Parameter p : 2D over t x t, 6 records: 1 normal value + 5 specials
  Variable  v : 1D over t, exercises the 5-value-column shape
"""
import os

# gdxpds before pandas, for the documented Linux shared-library ordering.
import gdxpds
import gdxpds.gdx

import numpy as np
import pandas as pd

OUT_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "..", "src", "gdxpds", "_verify_install", "sample.gdx",
))


def main():
    eps = np.finfo(float).eps

    with gdxpds.gdx.GdxFile() as gdx:
        gdx.append(gdxpds.gdx.GdxSymbol(
            "t", gdxpds.gdx.GamsDataType.Set, dims=["t"]))
        gdx[-1].dataframe = pd.DataFrame(
            [["a", True], ["b", True], ["c", True]],
            columns=["t", "Value"])

        gdx.append(gdxpds.gdx.GdxSymbol(
            "p", gdxpds.gdx.GamsDataType.Parameter, dims=["t1", "t2"]))
        gdx[-1].dataframe = pd.DataFrame([
            ["a", "a", 1.0],
            ["a", "b", None],
            ["b", "a", np.nan],
            ["b", "b", np.inf],
            ["c", "a", -np.inf],
            ["c", "b", eps],
        ], columns=["t1", "t2", "Value"])

        gdx.append(gdxpds.gdx.GdxSymbol(
            "v", gdxpds.gdx.GamsDataType.Variable, dims=["t"],
            variable_type=gdxpds.gdx.GamsVariableType.Free))
        v_df = pd.DataFrame([["a"], ["b"], ["c"]], columns=["t"])
        for col in gdx[-1].value_col_names:
            v_df[col] = gdx[-1].get_value_col_default(col)
        v_df["Level"] = [10.0, 20.0, 30.0]
        gdx[-1].dataframe = v_df

        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        gdx.write(OUT_PATH)

    print(f"Wrote {OUT_PATH} ({os.path.getsize(OUT_PATH)} bytes)")


if __name__ == "__main__":
    main()
