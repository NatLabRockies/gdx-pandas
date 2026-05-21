__version__ = "2.0.0"

from gdxpds._backend import Backend, BackendError
from gdxpds.gdx import GdxError, SymbolNotFoundError
from gdxpds.read_gdx import (
    get_data_types,
    get_subset_relationships,
    list_symbols,
    to_dataframe,
    to_dataframes,
)
from gdxpds.tools import (
    Error,
    GamsDirFinder,
    GamsLoadError,
    info,
    load_gdxcc,
)
from gdxpds.write_gdx import to_gdx

__all__ = [
    "__version__",
    "load_gdxcc",
    "info",
    "Error",
    "GamsLoadError",
    "GdxError",
    "GamsDirFinder",
    "Backend",
    "BackendError",
    "SymbolNotFoundError",
    "HAVE_GAMS_TRANSFER",
    "to_dataframes",
    "to_dataframe",
    "list_symbols",
    "get_data_types",
    "get_subset_relationships",
    "to_gdx",
]


def __getattr__(name: str):
    # Expose HAVE_GAMS_TRANSFER lazily so ``import gdxpds`` does not pay the
    # gams.transfer import cost; the probe runs on first access.
    if name == "HAVE_GAMS_TRANSFER":
        from gdxpds.tools import _probe_gams_transfer

        return _probe_gams_transfer()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
