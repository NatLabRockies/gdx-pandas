"""Generate tests/data/set_text_fixture.gdx.

A 1D Set whose elements carry GAMS explanatory *text*, used by the
load_set_text=True read test in tests/test_read.py. gdxpds has no API for
*writing* set element text, so this fixture is built with the raw gdxcc bindings
(the same low-level approach used in tests/test_specials.py). Committed to the
repo; only re-run this if the schema changes.

Usage (from repo root, with the venv active and $env:GAMS_DIR set):

    python dev\\build_set_text_fixture.py

Schema:
  Set st : 1D, elements a / b / c with explanatory text alpha / beta / gamma
"""

import os

import gdxpds.gdx

try:
    from gams.core import gdx as gdxcc
except ImportError:
    import gdxcc

OUT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tests", "data", "set_text_fixture.gdx")
)

# (element, explanatory text)
ELEMENTS = [("a", "alpha"), ("b", "beta"), ("c", "gamma")]


def main():
    # Creating a GdxFile binds the GAMS library, after which the raw gdxcc calls
    # below operate on its handle (f.H). Pin the gdxcc backend: f.H is None under
    # the gams.transfer backend, so GDXPDS_BACKEND must not redirect us.
    with gdxpds.gdx.GdxFile(backend="gdxcc") as f:
        if not gdxcc.gdxOpenWrite(f.H, OUT_PATH, "gdxpds"):
            raise gdxpds.gdx.GdxError(f.H, f"Could not open {OUT_PATH!r} for writing")
        f.universal_set.write()
        if not gdxcc.gdxDataWriteStrStart(
            f.H, "st", "set with element text", 1, gdxpds.gdx.GamsDataType.Set.value, 0
        ):
            raise gdxpds.gdx.GdxError(f.H, "Could not start writing data for symbol st")
        gdxcc.gdxSymbolSetDomainX(f.H, 1, ["*"])
        values = gdxcc.doubleArray(gdxcc.GMS_VAL_MAX)
        for elem, text in ELEMENTS:
            rc, node = gdxcc.gdxAddSetText(f.H, text)
            if not rc:
                raise gdxpds.gdx.GdxError(f.H, f"Could not add set text {text!r}")
            # A Set record's value is the index into the set-text table; gdxpds
            # surfaces it via gdxGetElemText when load_set_text=True.
            values[gdxcc.GMS_VAL_LEVEL] = float(node)
            gdxcc.gdxDataWriteStr(f.H, [elem], values)
        gdxcc.gdxDataWriteDone(f.H)
        gdxcc.gdxClose(f.H)

    print(f"Wrote {OUT_PATH} ({os.path.getsize(OUT_PATH)} bytes)")


if __name__ == "__main__":
    main()
