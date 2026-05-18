__version__ = "1.5.0"

import logging
import sys

logger = logging.getLogger(__name__)

from gdxpds.tools import (
    Error,
    GamsLoadError,
    GamsDirFinder,
    _require_gams_installation,
    _check_gdx_create_rc,
)
from gdxpds.special import load_specials

__all__ = [
    "__version__",
    "load_gdxcc",
    "info",
    "Error",
    "GamsLoadError",
    "GdxError",
    "GamsDirFinder",
    "to_dataframes",
    "to_dataframe",
    "list_symbols",
    "get_data_types",
    "to_gdx",
]

# Populated by the import-time bootstrap. Consumed by info().
_load_error = None
_bindings_source = None


def load_gdxcc(gams_dir=None):
    """
    Method to initialize GAMS, especially to load required libraries that can
    sometimes conflict with other packages.

    Parameters
    ----------
    gams_dir : None or str
        if not None, directory containing the GAMS executable
    """
    global _bindings_source
    if 'pandas' in sys.modules:
        logger.warning("Especially on Linux, gdxpds should be imported before " + \
                       "pandas to avoid a library conflict. Also make sure your " + \
                       "GAMS directory is listed in LD_LIBRARY_PATH.")
    try:
        from gams.core import gdx as gdxcc
        _bindings_source = "gams.core.gdx"
    except ImportError:
        import gdxcc
        _bindings_source = "gdxcc"
    from gdxpds.tools import GamsDirFinder
    finder = GamsDirFinder(gams_dir=gams_dir)
    _require_gams_installation(finder)
    H = gdxcc.new_gdxHandle_tp()
    rc = gdxcc.gdxCreateD(H, finder.gams_dir, gdxcc.GMS_SSSIZE)
    _check_gdx_create_rc(H, rc, gdxcc, finder.gams_dir, finder.source)
    gdxcc.gdxFree(H)
    load_specials(finder)
    return

try:
    load_gdxcc()
except Exception as e:
    _load_error = e
    from gdxpds.tools import GamsDirFinder
    gams_dir = None
    try:
        gams_dir = GamsDirFinder().gams_dir
    except: pass
    logger.warning(
        f"Unable to load gdxcc with default GAMS directory {gams_dir!r}: "
        f"{type(e).__name__}: {e}. "
        f"You may need to explicitly call gdxpds.load_gdxcc(gams_dir) "
        f"before importing pandas to avoid a library conflict. "
        f"Run `gdxpds info` for a full environment report."
    )


def info(gams_dir=None):
    """Return a human-readable environment report as a string.

    Includes the gdxpds version, Python info, which GDX bindings were
    selected at import time, the resolved GAMS directory plus the discovery
    branch that produced it, and whether ``load_gdxcc()`` succeeded.

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
    from gdxpds.tools import GamsDirFinder

    lines = ["gdxpds info", "-----------"]

    # gdxpds
    lines.append(f"gdxpds:        {__version__}")

    # Python
    py_version = ".".join(str(v) for v in sys.version_info[:3])
    lines.append(f"Python:        {py_version} ({sys.platform})")

    # Bindings: probe each independently so we report both if both are installed
    lines.append("Bindings:")
    for module_path, dist_name in [("gams.core.gdx", "gamsapi"), ("gdxcc", "gdxcc")]:
        try:
            mod = importlib.import_module(module_path)
            try:
                version = importlib.metadata.version(dist_name)
            except importlib.metadata.PackageNotFoundError:
                version = "(version unknown)"
            lines.append(f"  {module_path}: {dist_name} {version}")
        except Exception:
            lines.append(f"  {module_path}: not importable")
    selected = _bindings_source or "(not yet selected)"
    lines.append(f"  selected:    {selected}")

    # GAMS directory: fresh probe each call. Reports what subsequent gdxpds
    # operations would also see (they re-probe too).
    try:
        finder = GamsDirFinder(gams_dir=gams_dir)
        lines.append(f"GAMS_DIR:      {finder.gams_dir}")
        lines.append(f"  source:      {finder.source}")
    except RuntimeError:
        lines.append("GAMS_DIR:      (not found)")
    except Exception as e:
        lines.append(f"GAMS_DIR:      (probe failed: {type(e).__name__}: {e})")

    # Optional fast-path module
    try:
        gdx2py_spec = importlib.util.find_spec("gdx2py")
    except Exception:
        gdx2py_spec = None
    gdx2py_status = "importable" if gdx2py_spec else "not importable"
    lines.append(f"gdx2py:        {gdx2py_status}  (optional fast path)")

    # Linux load-order warning condition
    pandas_state = "yes" if "pandas" in sys.modules else "no"
    lines.append(f"pandas pre-imported: {pandas_state}")

    # Load failure, if any. Suppressed on the happy path -- the rest of the
    # report implies success when this line is absent.
    if _load_error is not None:
        lines.append("")
        lines.append(
            f"load_gdxcc FAILED: {type(_load_error).__name__}: {_load_error}"
        )

    return "\n".join(lines)


from gdxpds.read_gdx import to_dataframes, list_symbols, to_dataframe, get_data_types
from gdxpds.write_gdx import to_gdx
from gdxpds.gdx import GdxError
