#!/usr/bin/env python3
"""Behavioral check: ``resolve_engine(gams_dir=X)`` must reflect ``X``, not the
cached default-discovered GAMS install.

Run from a venv with gdxpds installed. Each command-line argument is a GAMS
directory to probe against; the script asserts that
``resolve_engine(None, gams_dir=X) == GAMS_TRANSFER`` iff
``_probe_gams_transfer(X)`` is True for each ``X``. A clearly bogus dir is
appended as a control, so the check still catches the bug when every real GAMS
install happens to agree with the cached default.

Exit code: 0 on success, non-zero with a failure summary on mismatch. The
default (no args) is still useful -- it exercises the bogus-dir control.

Intended as a follow-on step in ``dev/run_test_matrix.sh`` to exercise the
``gams_dir`` parameter on ``resolve_engine`` across the matrix's installs.
"""

from __future__ import annotations

import sys
import tempfile

from gdxpds._engine import Engine, resolve_engine
from gdxpds.tools import _probe_gams_transfer

# A real, existing, non-GAMS directory: ``_probe_transfer_at`` accepts an
# existing path verbatim (GamsDirFinder only redirects to default discovery
# when the path doesn't exist), so a real-but-non-GAMS dir is what actually
# forces the probe to return False -- which it must, to act as a control.
BOGUS_DIR = tempfile.gettempdir()


def _expected(probe_result: bool) -> Engine:
    return Engine.GAMS_TRANSFER if probe_result else Engine.GDXCC


def check_one(label: str, gams_dir: str | None) -> tuple[bool, str]:
    probe = _probe_gams_transfer(gams_dir)
    resolved = resolve_engine(None, gams_dir=gams_dir)
    expected = _expected(probe)
    ok = resolved == expected
    detail = f"{label}: gams_dir={gams_dir!r} probe={probe} resolved={resolved.value} expected={expected.value}"
    return ok, detail


def main(argv: list[str]) -> int:
    cases: list[tuple[str, str | None]] = [("default", None)]
    for i, gd in enumerate(argv):
        cases.append((f"explicit[{i}]", gd))
    cases.append(("bogus-control", BOGUS_DIR))

    failures: list[str] = []
    for label, gd in cases:
        ok, detail = check_one(label, gd)
        marker = "OK" if ok else "FAIL"
        print(f"[engine-cross] {marker}  {detail}")
        if not ok:
            failures.append(detail)

    if failures:
        print(f"[engine-cross] {len(failures)} failure(s):")
        for f in failures:
            print(f"  {f}")
        return 1
    print("[engine-cross] all cases consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
