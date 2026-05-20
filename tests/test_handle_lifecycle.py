"""Regression tests for GDX handle lifecycle (see src/gdxpds/tools.py _GdxHandle
and src/gdxpds/gdx.py GdxFile finalize).

The core safety property -- on a failed gdxCreateD we must delete the SWIG
wrapper but NOT call gdxFree (which segfaults on a failed create) -- is verified
deterministically with a fake gdxcc passed into _GdxHandle's constructor
(dependency injection, no monkeypatching, no GAMS needed). The remaining tests
exercise the real GdxFile teardown and skip when no GAMS is discoverable.
"""

import gc
import os
import subprocess
import sys

import pytest

import gdxpds
import gdxpds.gdx
import gdxpds.tools
from gdxpds.tools import GamsLoadError, _GdxHandle

# A path that exists but is not a GAMS install (matches tests/test_diagnostics.py).
NOT_GAMS_DIR = "C:\\Windows" if os.name == "nt" else "/tmp"


def _gams_discoverable():
    try:
        gdxpds.tools.GamsDirFinder().gams_dir
        return True
    except Exception:
        return False


requires_gams = pytest.mark.skipif(
    not _gams_discoverable(), reason="no GAMS installation discoverable"
)


# --------------------------------------------------------------------- _GdxHandle
# A fake binding that records the call sequence and lets each test choose the
# gdxCreateD return code. _check_gdx_create_rc treats code == 1 as success and,
# because we supply a message in the rc list, never reaches gdxErrorStr -- so the
# fake needs only these four functions plus GMS_SSSIZE.


class FakeGdxcc:
    GMS_SSSIZE = 256

    def __init__(self, create_rc):
        self._create_rc = create_rc
        self.calls = []

    def new_gdxHandle_tp(self):
        self.calls.append("new")
        return "H"  # sentinel handle

    def gdxCreateD(self, H, gams_dir, ssize):
        self.calls.append("create")
        return self._create_rc

    def gdxFree(self, H):
        self.calls.append("free")
        return 1

    def delete_gdxHandle_tp(self, H):
        self.calls.append("delete")


def test_gdxhandle_success_frees_then_deletes():
    fake = FakeGdxcc([1, ""])
    with _GdxHandle(fake, "/gams", "test"):
        pass
    assert fake.calls == ["new", "create", "free", "delete"]


def test_gdxhandle_failure_deletes_without_free():
    # The whole point: a failed create must delete the wrapper but never gdxFree.
    fake = FakeGdxcc([2, "boom"])
    with pytest.raises(GamsLoadError):
        _GdxHandle(fake, "/gams", "test")
    assert fake.calls == ["new", "create", "delete"]
    assert "free" not in fake.calls


def test_gdxhandle_close_is_idempotent():
    fake = FakeGdxcc([1, ""])
    h = _GdxHandle(fake, "/gams", "test")
    h.close()
    h.close()
    assert fake.calls == ["new", "create", "free", "delete"]
    assert fake.calls.count("free") == 1
    assert fake.calls.count("delete") == 1


def test_gdxhandle_not_freed_until_close():
    # The long-lived (GdxFile) pattern: the handle stays open after construction
    # and is only freed when its owner calls close() (from weakref.finalize).
    fake = FakeGdxcc([1, ""])
    h = _GdxHandle(fake, "/gams", "test")
    assert fake.calls == ["new", "create"]  # alive; nothing freed yet
    h.close()
    assert fake.calls == ["new", "create", "free", "delete"]


def test_needsgamsdir_records_source():
    # GdxFile passes self.gams_dir_source (not a "GdxFile" literal) to _GdxHandle,
    # so a create-failure error names the real discovery branch. Verify the source
    # is captured. No GAMS needed: an explicit dir is just path-cleaned.
    nd = gdxpds.tools.NeedsGamsDir(gams_dir=NOT_GAMS_DIR)
    assert nd.gams_dir_source == "explicit override"


# ------------------------------------------------------------------------ GdxFile


@requires_gams
def test_gdxfile_cleanup_is_idempotent():
    # "Idempotent" = a second/third cleanup() (and a later GC) has the same effect
    # as the first: the handle is freed exactly once, then later calls are safe
    # no-ops. weakref.finalize.alive is the observable proxy for "not yet freed",
    # so asserting the True -> False transition proves cleanup ran exactly once
    # (a bare "did not crash" check would also pass on a silent double-free).
    f = gdxpds.gdx.GdxFile()
    assert f._finalizer.alive  # handle live, not yet freed
    f.cleanup()
    assert not f._finalizer.alive  # freed, exactly once
    assert f._H is None
    f.cleanup()  # second call: safe no-op
    assert not f._finalizer.alive
    del f
    gc.collect()  # GC of an already-cleaned file must not re-free


@requires_gams
def test_gdxfile_context_manager_frees_on_exit():
    # The with-block must free the handle exactly once, at __exit__.
    with gdxpds.gdx.GdxFile() as f:
        assert f._finalizer.alive
    assert not f._finalizer.alive


@requires_gams
def test_gdxfile_freed_on_garbage_collection():
    # Core of the no-__del__ design: an un-cleaned GdxFile sits in a reference
    # cycle (universal_set._file = self), so it is reclaimed by *cyclic* GC; the
    # weakref.finalize must still fire then and free the handle.
    f = gdxpds.gdx.GdxFile()
    fin = f._finalizer  # holds a weak ref to f, so it does not keep f alive
    assert fin.alive
    del f
    gc.collect()
    assert not fin.alive


@requires_gams
def test_many_gdxfiles_exit_cleanly():
    # Build and drop many GdxFiles (some explicitly cleaned, some left to GC and
    # the at-exit finalizers) in a fresh process; a clean exit means no
    # double-free at teardown and no atexit pile-up. GAMS_DIR is inherited.
    code = (
        "import gc, gdxpds.gdx\n"
        "for _ in range(50):\n"
        "    g = gdxpds.gdx.GdxFile(); g.cleanup()\n"
        "fs = [gdxpds.gdx.GdxFile() for _ in range(50)]\n"
        "del fs; gc.collect()\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"non-clean exit:\n{result.stderr}"


def test_invalid_gams_dir_fails_cleanly():
    # A bogus GAMS_DIR must raise GamsLoadError (at the _require_gams_installation
    # pre-check, before gdxCreateD) and exit cleanly -- never segfault. Needs no
    # real GAMS install. Runs in a fresh interpreter so binding state is pristine.
    env = {**os.environ, "GAMS_DIR": NOT_GAMS_DIR}
    result = subprocess.run(
        [sys.executable, "-c", "import gdxpds.gdx; gdxpds.gdx.GdxFile()"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1, (
        f"expected a clean non-zero exit, got {result.returncode}\n{result.stderr}"
    )
    assert "GamsLoadError" in result.stderr or "not a GAMS installation" in result.stderr
