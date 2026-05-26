"""Generate tests/data/set_text_fixture.gdx.

A 1D Set whose elements carry GAMS explanatory *text*, used by the set-element-text
read tests in tests/test_read.py. Built with the raw gdxcc bindings (the same
low-level approach used in tests/test_specials.py) so the read tests stay independent
of gdxpds's own write path. Committed to the repo; only re-run this if the schema changes.

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
    # below operate on its GDX handle. Pin the gdxcc engine: the gams.transfer
    # engine has no handle, so GDXPDS_ENGINE must not redirect us.
    with gdxpds.gdx.GdxFile(engine="gdxcc") as f:
        H = f._engine_impl.handle
        if not gdxcc.gdxOpenWrite(H, OUT_PATH, "gdxpds"):
            raise gdxpds.gdx.GdxError(H, f"Could not open {OUT_PATH!r} for writing")
        f.universal_set.write()
        if not gdxcc.gdxDataWriteStrStart(
            H, "st", "set with element text", 1, gdxpds.gdx.GamsDataType.Set.value, 0
        ):
            raise gdxpds.gdx.GdxError(H, "Could not start writing data for symbol st")
        gdxcc.gdxSymbolSetDomainX(H, 1, ["*"])
        values = gdxcc.doubleArray(gdxcc.GMS_VAL_MAX)
        for elem, text in ELEMENTS:
            rc, node = gdxcc.gdxAddSetText(H, text)
            if not rc:
                raise gdxpds.gdx.GdxError(H, f"Could not add set text {text!r}")
            # A Set record's value is the index into the set-text table; gdxpds
            # surfaces it as the Set's element text on read.
            values[gdxcc.GMS_VAL_LEVEL] = float(node)
            gdxcc.gdxDataWriteStr(H, [elem], values)
        gdxcc.gdxDataWriteDone(H)
        gdxcc.gdxClose(H)

    print(f"Wrote {OUT_PATH} ({os.path.getsize(OUT_PATH)} bytes)")


if __name__ == "__main__":
    main()
