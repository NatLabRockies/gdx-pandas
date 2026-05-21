"""Generate tests/data/symbol_types_fixture.gdx.

A small, internally coherent reference GDX with one symbol of each writable
GamsDataType and known values, used by the read and round-trip tests in
tests/test_read.py and tests/test_write.py. Built through the gdxpds write path
(so it also exercises that path). Committed to the repo; only re-run this if the
schema or expected values change.

Usage (from repo root, with the venv active and $env:GAMS_DIR set):

    python dev\\build_symbol_types_fixture.py

Schema (known values, asserted by the tests):
  Set       t        : 1D root Set (wildcard domain), elements a, b, c, d, e
  Set       sub_t(t) : strict subset of t, elements a, c
  Parameter p(t)     : one special value per element of t
  Variable  v(sub_t) : Free, known Level/Marginal/Lower/Upper/Scale
  Equation  e(sub_t) : Equality, known Level/Marginal/Lower/Upper/Scale
"""

import os

import numpy as np
import pandas as pd

import gdxpds.gdx

OUT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tests", "data", "symbol_types_fixture.gdx")
)

# Known value-column values for v and e, keyed by column name so the builder is
# independent of value_col_names ordering. Two records each (one per sub_t member).
V_VALUES = {
    "Level": [1.0, 6.0],
    "Marginal": [2.0, 7.0],
    "Lower": [3.0, 8.0],
    "Upper": [4.0, 9.0],
    "Scale": [5.0, 10.0],
}
E_VALUES = {
    "Level": [11.0, 16.0],
    "Marginal": [12.0, 17.0],
    "Lower": [13.0, 18.0],
    "Upper": [14.0, 19.0],
    "Scale": [15.0, 20.0],
}


def main():
    eps = np.finfo(float).eps

    with gdxpds.gdx.GdxFile() as gdx:
        # Root Set with a wildcard domain (domain_type == NONE on read).
        gdx.append(gdxpds.gdx.GdxSymbol("t", gdxpds.gdx.GamsDataType.Set, dims=["*"]))
        gdx[-1].dataframe = pd.DataFrame(
            [[x, True] for x in ["a", "b", "c", "d", "e"]], columns=["*", "Value"]
        )

        # Strict subset of t (domain_type == REGULAR on read).
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "sub_t", gdxpds.gdx.GamsDataType.Set, dims=["t"], domain=[gdx["t"]]
            )
        )
        gdx[-1].dataframe = pd.DataFrame([["a", True], ["c", True]], columns=["t", "Value"])

        # Parameter strictly over t: one special value per element. UNDEF/NA are
        # indistinguishable from NaN in pandas, so the tests assert the NaN-family
        # loosely and +Inf / -Inf / EPS exactly.
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "p", gdxpds.gdx.GamsDataType.Parameter, dims=["t"], domain=[gdx["t"]]
            )
        )
        gdx[-1].dataframe = pd.DataFrame(
            [
                ["a", 1.5],
                ["b", np.nan],
                ["c", np.inf],
                ["d", -np.inf],
                ["e", eps],
            ],
            columns=["t", "Value"],
        )

        # Variable (Free) strictly over sub_t, known values in all five columns.
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "v",
                gdxpds.gdx.GamsDataType.Variable,
                dims=["sub_t"],
                domain=[gdx["sub_t"]],
                variable_type=gdxpds.gdx.GamsVariableType.Free,
            )
        )
        v_df = pd.DataFrame({"sub_t": ["a", "c"]})
        for col in gdx[-1].value_col_names:
            v_df[col] = V_VALUES[col]
        gdx[-1].dataframe = v_df

        # Equation (Equality) strictly over sub_t, known values in all five columns.
        gdx.append(
            gdxpds.gdx.GdxSymbol(
                "e",
                gdxpds.gdx.GamsDataType.Equation,
                dims=["sub_t"],
                domain=[gdx["sub_t"]],
                equation_type=gdxpds.gdx.GamsEquationType.Equality,
            )
        )
        e_df = pd.DataFrame({"sub_t": ["a", "c"]})
        for col in gdx[-1].value_col_names:
            e_df[col] = E_VALUES[col]
        gdx[-1].dataframe = e_df

        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        gdx.write(OUT_PATH)

    print(f"Wrote {OUT_PATH} ({os.path.getsize(OUT_PATH)} bytes)")


if __name__ == "__main__":
    main()
