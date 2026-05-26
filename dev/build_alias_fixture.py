"""Generate tests/data/alias_fixture.gdx.

A 1D parent Set plus an Alias of it, used by the engine read-parity tests.
Built with the raw gdxcc bindings (gdxAddAlias) so the read tests stay independent
of gdxpds's own alias-write path, the same low-level approach used in
build_set_text_fixture.py. Committed to the repo; only re-run this if the schema changes.

Usage (from repo root, with the venv active and $env:GAMS_DIR set):

    python dev\\build_alias_fixture.py

Schema:
  Set   t  : 1D, elements a / b / c
  Alias at : alias of t
"""

import os

import gdxpds.gdx

try:
    from gams.core import gdx as gdxcc
except ImportError:
    import gdxcc

OUT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tests", "data", "alias_fixture.gdx")
)

ELEMENTS = ["a", "b", "c"]


def main():
    # Pin the gdxcc engine: this script drives raw gdxcc calls through the GDX
    # handle, which the gams.transfer engine does not have (so GDXPDS_ENGINE
    # must not be allowed to redirect us).
    with gdxpds.gdx.GdxFile(engine="gdxcc") as f:
        H = f._engine_impl.handle
        if not gdxcc.gdxOpenWrite(H, OUT_PATH, "gdxpds"):
            raise gdxpds.gdx.GdxError(H, f"Could not open {OUT_PATH!r} for writing")
        f.universal_set.write()

        # Parent set t = {a, b, c}.
        if not gdxcc.gdxDataWriteStrStart(
            H, "t", "parent set", 1, gdxpds.gdx.GamsDataType.Set.value, 0
        ):
            raise gdxpds.gdx.GdxError(H, "Could not start writing data for symbol t")
        gdxcc.gdxSymbolSetDomainX(H, 1, ["*"])
        values = gdxcc.doubleArray(gdxcc.GMS_VAL_MAX)
        values[gdxcc.GMS_VAL_LEVEL] = 0.0
        for elem in ELEMENTS:
            gdxcc.gdxDataWriteStr(H, [elem], values)
        gdxcc.gdxDataWriteDone(H)

        # Alias at -> t.
        if not gdxcc.gdxAddAlias(H, "t", "at"):
            raise gdxpds.gdx.GdxError(H, "Could not add alias at -> t")

        gdxcc.gdxClose(H)

    print(f"Wrote {OUT_PATH} ({os.path.getsize(OUT_PATH)} bytes)")


if __name__ == "__main__":
    main()
