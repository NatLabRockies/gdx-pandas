import logging
import os
import subprocess as subp
import re
import sys

logger = logging.getLogger(__name__)


class Error(Exception):
    """
    Base class for all Exceptions raised by this package.
    """


class GamsLoadError(Error):
    """
    Raised when gdxpds cannot bring up the GAMS shared library at the
    resolved gams_dir (non-GAMS directory, gdxCreateD non-success return,
    version skew, missing shared library, etc.).
    """


def _require_gams_installation(finder):
    """Verify the resolved GAMS directory actually contains GAMS.

    Calling the SWIG-bound ``gdxCreateD`` against a directory that does not
    contain the GAMS shared library can crash the Python process on Windows
    (access violation). This pre-check raises :class:`GamsLoadError` first,
    naming the discovery branch that produced the bad directory.
    """
    gams_exe = "gams.exe" if os.name == "nt" else "gams"
    candidate = os.path.join(finder.gams_dir, gams_exe)
    if not os.path.exists(candidate):
        raise GamsLoadError(
            f"Resolved GAMS directory {finder.gams_dir!r} "
            f"[source: {finder.source}] does not contain {gams_exe!r}; "
            f"this is not a GAMS installation."
        )


def _check_gdx_create_rc(H, rc, gdxcc, gams_dir, source):
    """Raise :class:`GamsLoadError` if gdxCreateD failed.

    ``rc`` may be a bare int (older bindings) or a ``[code, msg]`` 2-list
    (modern ``gams.core.gdx``). Either way, ``code == 1`` is success.
    """
    if isinstance(rc, (list, tuple)):
        code = rc[0]
        msg = rc[1] if len(rc) > 1 else ''
    else:
        code = rc
        msg = ''
    if code == 1:
        return
    if not msg and hasattr(gdxcc, 'gdxErrorStr') and hasattr(gdxcc, 'gdxGetLastError'):
        try:
            msg = gdxcc.gdxErrorStr(None, gdxcc.gdxGetLastError(H))[1]
        except Exception:
            pass
    # Deliberately no gdxFree(H): on a failed gdxCreateD the library was never
    # loaded, so XFree is unbound and gdxFree(H) would segfault.
    raise GamsLoadError(
        f"gdxCreateD failed (rc={code}) for GAMS directory {gams_dir!r} "
        f"[source: {source}]. gdxcc reported: {msg!r}"
    )


class _GdxHandle:
    """Own the new -> create -> (use) -> free -> delete lifecycle of one SWIG gdx handle.

    The SWIG gdx handle has two separable resources: the wrapper struct from
    ``new_gdxHandle_tp()`` (a plain ``calloc``) and the gdx object that
    ``gdxCreateD`` builds inside it. They tear down differently:

    - success: ``gdxFree`` (frees the object) then ``delete_gdxHandle_tp`` (frees the wrapper);
    - failure: ``delete_gdxHandle_tp`` ONLY -- on a failed ``gdxCreateD`` the library never
      loaded, so ``XFree`` is unbound and ``gdxFree`` would segfault.

    ``close()`` is idempotent (run-once: a second ``gdxFree`` is a double free). There is no
    ``__del__`` on purpose -- native cleanup never runs from a destructor (which would fire at
    interpreter teardown after module state is partially gone). Short-lived handles use ``with``;
    a long-lived owner (e.g. ``GdxFile``) keeps the instance and drives :meth:`close` from a
    ``weakref.finalize`` callback -- safe because ``close`` is a bound method that does not
    reference the owner and uses gdxcc callables bound at construction (not module globals).

    ``gdxcc`` is passed in because :mod:`gdxpds.tools` does not import a binding at module level.
    """
    def __init__(self, gdxcc, gams_dir, source):
        # Bind the callables now so close() does not look them up through module
        # globals during interpreter shutdown.
        self._free = gdxcc.gdxFree
        self._delete = gdxcc.delete_gdxHandle_tp
        self._created = self._closed = False
        self.H = None
        self.H = gdxcc.new_gdxHandle_tp()
        try:
            rc = gdxcc.gdxCreateD(self.H, gams_dir, gdxcc.GMS_SSSIZE)
            _check_gdx_create_rc(self.H, rc, gdxcc, gams_dir, source)  # raises on failure
            self._created = True
        except BaseException:
            self.close()   # _created is False -> delete-only, never gdxFree
            raise

    def close(self):
        if self._closed or self.H is None:
            return
        self._closed = True
        H, self.H = self.H, None
        if self._created:
            self._free(H)
        self._delete(H)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class GamsDirFinder(object):
    """
    Class for finding and accessing the system's GAMS directory. 

    The find function first looks for the 'GAMS_DIR' environment variable. If 
    that is unsuccessful, it next uses 'which gams' for POSIX systems, and the 
    default install location, 'C:/GAMS', for Windows systems. In the latter case
    it prefers the largest version number.
    
    You can always specify the GAMS directory directly, and this class will attempt 
    to clean up your input. (Even on Windows, the GAMS path must use '/' rather than 
    '\'.)
    """
    gams_dir_cache = None

    def __init__(self,gams_dir=None):
        self.__source = None
        self.gams_dir = gams_dir

    @property
    def source(self):
        """Label describing which discovery branch produced ``gams_dir``.

        Set whenever a non-None ``gams_dir`` is resolved. Useful for diagnosing
        "wrong GAMS directory got picked" reports. ``None`` if no GAMS directory
        could be resolved.
        """
        return self.__source

    @property
    def gams_dir(self):
        """The GAMS directory on this system."""
        if self.__gams_dir is None:
            raise RuntimeError("Unable to locate your GAMS directory.")
        return self.__gams_dir

    @gams_dir.setter
    def gams_dir(self, value):
        self.__gams_dir = None
        if isinstance(value, str):
            self.__gams_dir = self.__clean_gams_dir(value)
            if self.__gams_dir is not None:
                self.__source = "explicit override"
        elif value is not None:
            logger.warning(f"Unexpected gams_dir type {type(value)}. Ignoring "
                f"input {value!r} because it is not a str.")
        if self.__gams_dir is None:
            self.__gams_dir = self.__find_gams()
            
    def __find_gams_root_in(self, parent):
        """Search direct subdirectories of `parent` for a GAMS installation,
        identified by the presence of gams.exe. If multiple are found, prefer
        the one whose directory name parses as the largest version."""
        if not os.path.isdir(parent):
            return None
        roots = []
        try:
            for name in os.listdir(parent):
                d = os.path.join(parent, name)
                if os.path.isdir(d) and os.path.exists(os.path.join(d, 'gams.exe')):
                    roots.append(d)
        except OSError:
            return None
        if not roots:
            return None
        if len(roots) == 1:
            return roots[0]
        def _parse(d):
            name = os.path.basename(d)
            try:
                return tuple(int(p) for p in name.split('.'))
            except ValueError:
                return None
        valid = [(p, d) for p, d in ((_parse(d), d) for d in roots) if p is not None]
        if valid:
            pad = max(len(p) for p, _ in valid)
            padded = [(p + (0,) * (pad - len(p)), d) for p, d in valid]
            return max(padded)[1]
        return roots[0]

    def __clean_gams_dir(self,value):
        """
        Cleans up the path string.
        """
        if value is None:
            return None
        assert(isinstance(value, str))
        ret = os.path.realpath(value)
        if not os.path.exists(ret):
            return None
        ret = re.sub('\\\\','/',ret)
        return ret
        
    def __find_gams(self):
        """
        For all systems, the first place we examine is the GAMS_DIR environment
        variable, and the second is GAMSDIR.

        For Windows, the next step is to try 'where gams'. Then we look in the 
        default install location (C:/GAMS), preferring win64 to win32 and the 
        most recent version.
        
        For all others, the next step is 'which gams'.
        
        Returns
        -------
        str or None
            If not None, the return value is the found gams_dir
        """
        # check for environment variable
        ret = os.environ.get('GAMS_DIR')
        ret = self.__clean_gams_dir(ret)
        if ret is not None:
            self.__source = "GAMS_DIR env var"

        if ret is None:
            ret = os.environ.get('GAMSDIR')
            ret = self.__clean_gams_dir(ret)
            if ret is not None:
                self.__source = "GAMSDIR env var"

        if ret is None and os.name == 'nt':
            # windows systems
            try:
                ret = os.path.dirname(subp.check_output(['where', 'gams']).decode().split("\n")[0])
            except:
                ret = None
            ret = self.__clean_gams_dir(ret)
            if ret is not None:
                self.__source = "where gams"

        if ret is None and os.name == 'nt':
            # search in default installation location. Modern GAMS (v42+)
            # installs to C:\GAMS\<version>\; legacy installs went to
            # C:\GAMS\win64\<version>\. A GAMS root is identified by the
            # presence of gams.exe.
            ret = self.__find_gams_root_in(r'C:\GAMS')
            if ret is not None:
                self.__source = r"C:\GAMS default-location walk (modern layout)"
            else:
                ret = self.__find_gams_root_in(os.path.join(r'C:\GAMS', 'win64'))
                if ret is not None:
                    self.__source = r"C:\GAMS\win64 default-location walk (legacy layout)"
            ret = self.__clean_gams_dir(ret)

        if ret is None and os.name != 'nt':
            # posix systems
            try:
                ret = os.path.dirname(subp.check_output(['which', 'gams']).decode().split("\n")[0])
            except:
                ret = None
            ret = self.__clean_gams_dir(ret)
            if ret is not None:
                self.__source = "which gams"

        if ret is not None:
            GamsDirFinder.gams_dir_cache = ret

        if ret is None:
            logger.debug(f"Did not find GAMS directory. Using cached value {self.gams_dir_cache}.")
            ret = GamsDirFinder.gams_dir_cache
            if ret is not None:
                self.__source = "cached"

        return ret
        
class NeedsGamsDir(object):
    """Mix-in class that asserts that a GAMS directory is needed and provides the methods 
    required to find and access it."""

    def __init__(self,gams_dir=None):
        self.gams_dir = gams_dir
        
    @property
    def gams_dir(self):
        """
        The GAMS directory whose value has either been directly set or has been found using 
        the GamsDirFinder class.

        Returns
        -------
        str    
        """
        return self.__gams_dir
        
    @gams_dir.setter
    def gams_dir(self, value):
        finder = GamsDirFinder(value)
        self.__gams_dir = finder.gams_dir
        self.__gams_dir_source = finder.source

    @property
    def gams_dir_source(self):
        """Label for the discovery branch that produced :attr:`gams_dir`.

        Mirrors :attr:`GamsDirFinder.source`; lets GAMS-load errors name where
        the directory came from, consistently with the other handle-create sites.
        """
        return self.__gams_dir_source


# Process-global state for the bound GAMS library. Populated by the first
# successful load_gdxcc() call; consumed by info() and the mismatch warning.
_bindings_source = None
_loaded_gams_dir = None


def load_gdxcc(gams_dir=None):
    """Bind the GAMS library and initialize special-value conversion tables.

    Idempotent: safe to call repeatedly. The first successful call binds the
    GDX shared library at the resolved ``gams_dir`` and populates the
    module-level dicts in :mod:`gdxpds.special`. Subsequent calls early-return
    without re-binding; if the resolved ``gams_dir`` differs from the
    already-bound directory, a warning is emitted (one GAMS library bound per
    process). Directory validation runs only on the first, binding call, so a
    later call with an invalid ``gams_dir`` warns and returns rather than
    raising (the passed directory is ignored once a library is bound).

    Parameters
    ----------
    gams_dir : None or str
        If not None, directory containing the GAMS executable. If None, the
        directory is resolved by :class:`GamsDirFinder` (env vars, PATH,
        default-install walk).

    Raises
    ------
    GamsLoadError
        If the resolved directory does not contain GAMS, or if ``gdxCreateD``
        fails on the first bind.
    """
    global _bindings_source, _loaded_gams_dir
    finder = GamsDirFinder(gams_dir=gams_dir)
    if _loaded_gams_dir is not None:
        if finder.gams_dir != _loaded_gams_dir:
            logger.warning(
                f"gams_dir={finder.gams_dir!r} differs from the already-bound "
                f"directory {_loaded_gams_dir!r}; the passed gams_dir is "
                f"ignored (one GAMS library bound per process)."
            )
        return
    _require_gams_installation(finder)
    try:
        from gams.core import gdx as gdxcc
        _bindings_source = "gams.core.gdx"
    except ImportError:
        import gdxcc
        _bindings_source = "gdxcc"
    with _GdxHandle(gdxcc, finder.gams_dir, finder.source):
        pass
    # Deferred to break the tools <-> special import cycle.
    from gdxpds.special import load_specials
    load_specials(finder)
    _loaded_gams_dir = finder.gams_dir


def info(gams_dir=None):
    """Return a human-readable environment report as a string.

    Includes the gdxpds version, Python info, which GDX bindings are available
    and which (if any) is currently bound, the resolved GAMS directory plus the
    discovery branch that produced it, and any error from the active load
    attempt.

    Calls :func:`load_gdxcc` internally inside a try/except so install issues
    surface in the report even before any GDX operation has run. The first
    such call binds the library if it isn't already bound; subsequent calls
    hit the idempotent fast-path.

    Parameters
    ----------
    gams_dir : None or str
        If ``None`` (default), the GAMS directory is resolved via a fresh
        ``GamsDirFinder()`` probe -- the same discovery any subsequent gdxpds
        operation would do. If a string, probes that directory instead.

    This function never raises. Probe failures show up as ``"(unknown)"`` or
    ``"not importable"`` in the corresponding field. The return value is a
    string suitable for printing or pasting into a bug report.
    """
    import importlib
    import importlib.metadata
    import importlib.util
    # Deferred to break the tools <-> __init__ import cycle.
    from gdxpds import __version__

    lines = ["gdxpds info", "-----------"]

    lines.append(f"gdxpds:        {__version__}")

    py_version = ".".join(str(v) for v in sys.version_info[:3])
    lines.append(f"Python:        {py_version} ({sys.platform})")

    lines.append("Bindings:")
    for module_path, dist_name in [("gams.core.gdx", "gamsapi"), ("gdxcc", "gdxcc")]:
        try:
            importlib.import_module(module_path)
            try:
                version = importlib.metadata.version(dist_name)
            except importlib.metadata.PackageNotFoundError:
                version = "(version unknown)"
            lines.append(f"  {module_path}: {dist_name} {version}")
        except Exception:
            lines.append(f"  {module_path}: not importable")

    load_failure = None
    try:
        load_gdxcc(gams_dir=gams_dir)
    except Exception as e:
        load_failure = e

    selected = _bindings_source or "(not yet selected)"
    lines.append(f"  selected:    {selected}")
    bound = _loaded_gams_dir or "(not yet bound)"
    lines.append(f"  bound dir:   {bound}")

    try:
        finder = GamsDirFinder(gams_dir=gams_dir)
        lines.append(f"GAMS_DIR:      {finder.gams_dir}")
        lines.append(f"  source:      {finder.source}")
    except RuntimeError:
        lines.append("GAMS_DIR:      (not found)")
    except Exception as e:
        lines.append(f"GAMS_DIR:      (probe failed: {type(e).__name__}: {e})")

    try:
        gdx2py_spec = importlib.util.find_spec("gdx2py")
    except Exception:
        gdx2py_spec = None
    gdx2py_status = "importable" if gdx2py_spec else "not importable"
    lines.append(f"gdx2py:        {gdx2py_status}  (optional fast path)")

    if load_failure is not None:
        lines.append("")
        lines.append(
            f"load_gdxcc FAILED: {type(load_failure).__name__}: {load_failure}"
        )

    return "\n".join(lines)

