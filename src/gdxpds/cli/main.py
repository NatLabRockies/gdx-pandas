"""Top-level `gdxpds` CLI.

For now only one subcommand exists: `gdxpds test`, which verifies a fresh
installation against the local GAMS environment. If more subcommands are
added, split the dispatcher and per-command logic into separate modules.
"""
import argparse
import os
import sys
import tempfile
from importlib.resources import as_file, files

import gdxpds
import gdxpds.gdx
import gdxpds.tools

import numpy as np


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="gdxpds",
        description="gdx-pandas command-line utilities.",
    )
    parser.add_argument(
        "--version", action="version", version=f"gdxpds {gdxpds.__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    info_parser = subparsers.add_parser(
        "info",
        help="Print gdxpds environment info (Python, bindings, GAMS_DIR, load status).",
    )
    info_parser.add_argument(
        "-g", "--gams_dir", default=None,
        help="Probe this GAMS directory instead of the loaded / discovered one.")

    test_parser = subparsers.add_parser(
        "test",
        help="Verify the gdxpds installation against the local GAMS environment.",
    )
    test_parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print exception tracebacks on failure.")
    test_parser.add_argument(
        "-g", "--gams_dir", default=None,
        help="Use this GAMS directory for the verification run.")

    args = parser.parse_args(argv)
    if args.command == "info":
        print(gdxpds.info(gams_dir=args.gams_dir))
        return 0
    if args.command == "test":
        return _run_verify_install(args)
    raise AssertionError(f"unhandled CLI command: {args.command!r}")


def _run_verify_install(args) -> int:
    print(gdxpds.info(gams_dir=args.gams_dir))
    print()
    print("Verifying gdxpds installation...")
    failures = []

    gams_dir = _check_gams_install(args, failures)
    if gams_dir is None:
        return _verdict(failures)

    if not _check_bindings(args, failures):
        return _verdict(failures)

    with as_file(files("gdxpds._verify_install") / "sample.gdx") as sample_path:
        sample_path = str(sample_path)
        if not _check_read(sample_path, args, failures):
            return _verdict(failures)

        with tempfile.TemporaryDirectory() as tmp:
            roundtripped = os.path.join(tmp, "roundtrip.gdx")
            if _check_roundtrip(sample_path, roundtripped, args, failures):
                _check_specials(roundtripped, args, failures)

    return _verdict(failures)


def _check_gams_install(args, failures):
    try:
        finder = gdxpds.tools.GamsDirFinder(gams_dir=args.gams_dir)
        gdxpds.tools._require_gams_installation(finder)
        _ok(f"GAMS install found at {finder.gams_dir}")
        return finder.gams_dir
    except Exception as exc:
        _fail("Could not locate a GAMS installation", exc, args)
        _hint("Set $env:GAMS_DIR (PowerShell) or $GAMS_DIR (POSIX) to your "
              "GAMS install directory, put `gams` on PATH, or pass -g/--gams_dir.")
        failures.append("gams_install")
        return None


def _check_bindings(args, failures):
    source = gdxpds.tools._bindings_source
    if source is None:
        _fail("GDX bindings not loaded",
              RuntimeError("see info() output above for details"),
              args)
        _hint("Install `gamsapi` matched to your GAMS version: "
              "pip install gamsapi[transfer]==<your GAMS version>")
        failures.append("bindings")
        return False
    _ok(f"GDX bindings loaded: {source}")
    return True


def _check_read(sample_path, args, failures):
    try:
        with gdxpds.gdx.GdxFile(gams_dir=args.gams_dir, lazy_load=False) as gdx:
            gdx.read(sample_path)
            names = {s.name for s in gdx}
            assert names == {"t", "p", "v"}, f"unexpected symbols: {names}"
            assert gdx["p"].num_records == 6
        _ok(f"Read embedded sample.gdx ({sample_path})")
        return True
    except Exception as exc:
        _fail("Could not read embedded sample.gdx", exc, args)
        failures.append("read")
        return False


def _check_roundtrip(sample_path, out_path, args, failures):
    try:
        with gdxpds.gdx.GdxFile(gams_dir=args.gams_dir, lazy_load=False) as gdx:
            gdx.read(sample_path)
            with gdx.clone() as gdx2:
                gdx2.write(out_path)
        with gdxpds.gdx.GdxFile(gams_dir=args.gams_dir, lazy_load=False) as gdx:
            gdx.read(out_path)
            assert {s.name for s in gdx} == {"t", "p", "v"}
        _ok("Round-trip write->read preserves all symbols")
        return True
    except Exception as exc:
        _fail("Round-trip write->read failed", exc, args)
        failures.append("roundtrip")
        return False


def _check_specials(out_path, args, failures):
    try:
        with gdxpds.gdx.GdxFile(gams_dir=args.gams_dir, lazy_load=False) as gdx:
            gdx.read(out_path)
            values = gdx["p"].dataframe["Value"].tolist()

        assert any(v == 1.0 for v in values), "normal value 1.0 missing"
        assert any(v == np.inf for v in values), "+Inf missing"
        assert any(v == -np.inf for v in values), "-Inf missing"
        # NA and UNDEF both collapse to NaN under pandas.
        nan_count = sum(1 for v in values if v != v)
        assert nan_count >= 1, f"expected at least one NaN, found {nan_count}"
        _ok("Special values (+Inf, -Inf, NaN) survive round-trip")
    except Exception as exc:
        _fail("Special-value preservation failed", exc, args)
        failures.append("specials")


def _ok(msg):
    print(f"  [OK]   {msg}")


def _fail(msg, exc, args):
    print(f"  [FAIL] {msg}: {exc}", file=sys.stderr)
    if args.verbose:
        import traceback
        traceback.print_exception(exc, file=sys.stderr)


def _hint(msg):
    print(f"         hint: {msg}", file=sys.stderr)


def _verdict(failures) -> int:
    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("PASSED: gdxpds installation verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
