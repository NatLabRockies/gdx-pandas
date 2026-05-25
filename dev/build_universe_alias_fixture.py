"""Generate tests/data/universe_alias_fixture.gdx.

A 1D Set plus a *universe* alias (an alias of the universe set ``*``, as opposed
to a named Set). Built with the raw gdxcc bindings (gdxAddAlias against ``"*"``)
so the read tests stay independent of gdxpds's own write path. Committed to the
repo; only re-run this if the schema changes.

Usage (from repo root, with the venv active and $env:GAMS_DIR set):

    python dev\\build_universe_alias_fixture.py

Schema:
  Set   t : 1D, elements a / b / c
  Alias u : alias of the universe set '*'
"""

import os

import gdxpds.gdx

try:
    from gams.core import gdx as gdxcc
except ImportError:
    import gdxcc

OUT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tests", "data", "universe_alias_fixture.gdx")
)

ELEMENTS = ["a", "b", "c"]


def main():
    # Pin the gdxcc backend: this script drives raw gdxcc calls through the GDX
    # handle, which the gams.transfer backend does not have.
    with gdxpds.gdx.GdxFile(backend="gdxcc") as f:
        H = f._backend_impl.handle
        if not gdxcc.gdxOpenWrite(H, OUT_PATH, "gdxpds"):
            raise gdxpds.gdx.GdxError(H, f"Could not open {OUT_PATH!r} for writing")
        f.universal_set.write()

        # Set t = {a, b, c} (registers the UELs).
        if not gdxcc.gdxDataWriteStrStart(
            H, "t", "a set", 1, gdxpds.gdx.GamsDataType.Set.value, 0
        ):
            raise gdxpds.gdx.GdxError(H, "Could not start writing data for symbol t")
        gdxcc.gdxSymbolSetDomainX(H, 1, ["*"])
        values = gdxcc.doubleArray(gdxcc.GMS_VAL_MAX)
        values[gdxcc.GMS_VAL_LEVEL] = 0.0
        for elem in ELEMENTS:
            gdxcc.gdxDataWriteStr(H, [elem], values)
        gdxcc.gdxDataWriteDone(H)

        # Universe alias u -> '*'.
        if not gdxcc.gdxAddAlias(H, "*", "u"):
            raise gdxpds.gdx.GdxError(H, "Could not add universe alias u -> '*'")

        gdxcc.gdxClose(H)

    print(f"Wrote {OUT_PATH} ({os.path.getsize(OUT_PATH)} bytes)")


if __name__ == "__main__":
    main()
