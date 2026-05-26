"""Engine selection and the ``GdxEngine`` interface.

gdxpds moves data between GDX files and pandas DataFrames through a selectable
I/O engine. This module owns the engine-agnostic pieces:

- :class:`Engine`, the engine enum, with :data:`DEFAULT_ENGINE`;
- :func:`resolve_engine` (explicit arg / ``GDXPDS_ENGINE`` env var / default)
  and :func:`make_engine`, which builds the chosen engine; and
- :class:`GdxEngine`, the ABC each engine implements; see its docstring for the
  read / write / handle / teardown contract.

The concrete engines live in :mod:`gdxpds._gdxcc_engine` (the legacy and default
engine) and :mod:`gdxpds._transfer_engine`.
"""

from __future__ import annotations

import abc
import os
from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, assert_never

from gdxpds.tools import Error

if TYPE_CHECKING:
    from gdxpds.gdx import GdxFile, GdxSymbol


class EngineError(Error):
    """Raised for an invalid or unavailable engine selection.

    Subclass of :class:`~gdxpds.tools.Error`, so ``except Error`` still catches
    it.
    """


class Engine(StrEnum):
    """Selectable engine for GDX <-> DataFrame I/O.

    A :class:`~enum.StrEnum` so callers may pass either the member
    (``Engine.GDXCC``) or its string value (``"gdxcc"``), and so the
    ``GDXPDS_ENGINE`` env var maps straight onto it.
    """

    GDXCC = "gdxcc"
    GAMS_TRANSFER = "gams_transfer"


#: The engine used when none is explicitly requested: gams.transfer when it is
#: usable, otherwise gdxcc (the fallback resolved in :func:`resolve_engine`).
DEFAULT_ENGINE = Engine.GAMS_TRANSFER


class GdxEngine(abc.ABC):
    """Interface implemented by each GDX I/O engine.

    Abstract methods: :meth:`open_read` (metadata), :meth:`load_symbols`
    (records), :meth:`write_file`, and :meth:`close`. The :meth:`load_file` (all
    symbols) and :meth:`load_symbol` (one symbol) conveniences are defined in
    terms of :meth:`load_symbols`; :meth:`write_symbol` and :attr:`handle` have
    default implementations that engines override only if applicable.
    """

    @abc.abstractmethod
    def open_read(self, gdx_file: GdxFile, filename: str | os.PathLike[str]) -> None:
        """Open ``filename`` for reading and populate ``gdx_file``'s metadata.

        Sets ``gdx_file``'s ``_filename`` and, where the engine exposes them, its
        ``_version``/``_producer`` (the gams.transfer engine leaves those
        ``None``); builds its ``universal_set`` and the ``GdxSymbol`` collection
        (with extended per-symbol metadata and resolved domains), but does
        **not** load records.
        """

    @abc.abstractmethod
    def load_symbols(
        self,
        gdx_file: GdxFile,
        symbols: Sequence[GdxSymbol] | None = None,
    ) -> None:
        """Load records into the given ``symbols`` of ``gdx_file``.

        ``symbols is None`` loads every symbol; otherwise only the given
        :class:`~gdxpds.gdx.GdxSymbol` objects (the data model owns name lookup,
        so callers pass objects, not names). Already-loaded symbols are skipped.
        """

    def load_file(self, gdx_file: GdxFile) -> None:
        """Eagerly load every symbol's records."""
        self.load_symbols(gdx_file, None)

    def load_symbol(self, symbol: GdxSymbol) -> None:
        """Load the records of a single symbol."""
        self.load_symbols(symbol.file, [symbol])

    @staticmethod
    def _expand_alias_targets(symbols: Sequence[GdxSymbol]) -> list[GdxSymbol]:
        """Return ``symbols`` extended with the transitive ``alias_of`` parents
        of any aliases in the input, preserving order and de-duplicating.

        An Alias has no records of its own -- its ``dataframe`` is a view onto
        its parent's (see :class:`~gdxpds.gdx.GdxSymbol.dataframe`) -- so an
        engine asked to load an alias must also load the parent (and the
        parent's parent, for chained aliases). Engines call this from their
        ``load_symbols`` to build the actual target list.
        """
        # Late import: gdx.py imports this module, so a top-level import would cycle.
        from gdxpds.gdx import GamsDataType

        result = list(symbols)
        seen = {id(s) for s in result}
        for s in list(result):
            cur = s
            while cur.data_type == GamsDataType.Alias and cur._alias_of is not None:
                cur = cur._alias_of
                if id(cur) in seen:
                    break
                seen.add(id(cur))
                result.append(cur)
        return result

    @property
    def handle(self) -> object | None:
        """Native engine handle, if any.

        The gdxcc engine returns its GDX pointer; engines without one (e.g.
        gams.transfer) return ``None``. Reached as ``gdx_file._engine_impl.handle``
        for the rare case of driving raw ``gdxcc`` calls.
        """
        return None

    @abc.abstractmethod
    def write_file(self, gdx_file: GdxFile, filename: str | os.PathLike[str]) -> None:
        """Write every (loaded) symbol of ``gdx_file`` out to ``filename``."""

    def write_symbol(
        self,
        gdx_file: GdxFile,
        symbol: GdxSymbol,
        index: int | None = None,
        name_positions: dict | None = None,
    ) -> None:
        """Write a single symbol (single-symbol path).

        Only engines with a per-symbol write API implement this; others raise.
        Whole-file writes should go through :meth:`write_file`.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support per-symbol writes; "
            "use GdxFile.write() / gdxpds.to_gdx()."
        )

    @abc.abstractmethod
    def close(self) -> None:
        """Release any native resources (run-once, idempotent)."""


def resolve_engine(
    explicit: str | Engine | None,
    gams_dir: str | os.PathLike[str] | None = None,
) -> Engine:
    """Resolve which engine to use.

    Order: ``explicit`` value → ``GDXPDS_ENGINE`` env var → :data:`DEFAULT_ENGINE`.
    Strings are normalized to :class:`Engine` members. An unrecognized value
    raises :class:`EngineError`.

    The default (no explicit arg / env var) prefers ``GAMS_TRANSFER`` but falls
    back to ``GDXCC`` when gams.transfer is not usable, so gdxcc-only environments
    are unaffected. An *explicit* ``GAMS_TRANSFER`` request that can't be satisfied
    raises instead of falling back.

    ``gams_dir`` is threaded through to :func:`~gdxpds.tools._probe_gams_transfer`,
    so engine selection reflects the GAMS install the caller will actually use
    (the default-discovered install can differ from an explicit ``gams_dir=`` on
    :class:`~gdxpds.gdx.GdxFile` / :func:`~gdxpds.to_gdx` / :func:`~gdxpds.to_dataframes`).
    With ``gams_dir=None`` the probe uses the cached default-discovered directory.
    """
    # _probe_gams_transfer is the single source of truth for "is gams.transfer
    # usable here?". Resolve `engine` first (default / env / explicit), then call
    # it at most once -- and only if the resolved engine is GAMS_TRANSFER.
    raw = explicit if explicit is not None else os.environ.get("GDXPDS_ENGINE")
    if raw is None or raw == "":
        engine = DEFAULT_ENGINE
        explicit_request = False
    else:
        try:
            engine = Engine(raw)
        except ValueError:
            valid = ", ".join(repr(b.value) for b in Engine)
            raise EngineError(f"Unknown engine {raw!r}. Valid engines: {valid}.")
        explicit_request = True

    if engine is Engine.GAMS_TRANSFER:
        from gdxpds.tools import _probe_gams_transfer

        if not _probe_gams_transfer(gams_dir):
            if explicit_request:
                raise EngineError(
                    "Engine 'gams_transfer' requested but gams.transfer is not "
                    "usable here (not installed, or its gamsapi build cannot load "
                    "the active GAMS libraries). Install a gamsapi matching your "
                    "GAMS version."
                )
            # Quiet fallback: default-selected gams.transfer isn't usable, so
            # gdxcc-only environments stay unaffected by the new default.
            return Engine.GDXCC
    return engine


def make_engine(
    kind: Engine = DEFAULT_ENGINE,
    gams_dir: str | None = None,
    gams_dir_source: str | None = None,
) -> GdxEngine:
    """Construct the concrete engine for ``kind``.

    The implementation module is imported lazily so importing :mod:`gdxpds.gdx`
    does not pull in every engine.
    """
    if kind == Engine.GDXCC:
        from gdxpds._gdxcc_engine import GdxccEngine

        return GdxccEngine(gams_dir, gams_dir_source)
    if kind == Engine.GAMS_TRANSFER:
        from gdxpds._transfer_engine import TransferEngine

        return TransferEngine(gams_dir, gams_dir_source)
    # Exhaustive over Engine; guards against a future member added without a
    # branch here. User-facing validation of bad values lives in resolve_engine.
    assert_never(kind)
