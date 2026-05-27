__version__ = "3.1.0"

from gdxpds._engine import Engine, EngineError
from gdxpds.gdx import DomainError, GdxError, SymbolNotFoundError, TransferError
from gdxpds.read_gdx import (
    get_aliases,
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
    "TransferError",
    "DomainError",
    "GamsDirFinder",
    "Engine",
    "EngineError",
    "SymbolNotFoundError",
    "HAVE_GAMS_TRANSFER",
    "to_dataframes",
    "to_dataframe",
    "list_symbols",
    "get_data_types",
    "get_subset_relationships",
    "get_aliases",
    "to_gdx",
]


def __getattr__(name: str):
    # Expose HAVE_GAMS_TRANSFER lazily so ``import gdxpds`` does not pay the
    # gams.transfer import cost; the probe runs on first access.
    if name == "HAVE_GAMS_TRANSFER":
        from gdxpds.tools import _probe_gams_transfer

        return _probe_gams_transfer()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
