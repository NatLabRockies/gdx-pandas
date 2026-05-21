"""Backend selection and the ``GdxBackend`` interface.

gdxpds can move data between GDX files and pandas DataFrames through more than
one engine. This module owns the engine-agnostic pieces:

- the public :class:`Backend` enum and the :data:`DEFAULT_BACKEND` constant,
- the :class:`GdxBackend` ABC that each engine implements, and
- :func:`make_backend`, which constructs the concrete backend.

The ABC is being built up incrementally (Phase 0 of the gams.transfer work).
Today it covers the read-record primitive :meth:`GdxBackend.load_symbols`
(with :meth:`~GdxBackend.load_file` / :meth:`~GdxBackend.load_symbol` as
conveniences). Metadata reading, writing, and handle teardown move behind this
ABC in later steps.
"""

from __future__ import annotations

import abc
import os
from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING

from gdxpds.tools import Error

if TYPE_CHECKING:
    from gdxpds.gdx import GdxFile, GdxSymbol


class Backend(StrEnum):
    """Selectable engine for GDX <-> DataFrame I/O.

    A :class:`~enum.StrEnum` so callers may pass either the member
    (``Backend.GDXCC``) or its string value (``"gdxcc"``), and so the
    ``GDXPDS_BACKEND`` env var maps straight onto it.
    """

    GDXCC = "gdxcc"
    GAMS_TRANSFER = "gams_transfer"


#: The backend used when none is explicitly requested. v3.0.0 will flip this.
DEFAULT_BACKEND = Backend.GDXCC


class GdxBackend(abc.ABC):
    """Interface implemented by each GDX I/O engine.

    The single abstract read primitive is :meth:`load_symbols`; the
    :meth:`load_file` (all symbols) and :meth:`load_symbol` (one symbol)
    conveniences are defined in terms of it.
    """

    @abc.abstractmethod
    def open_read(self, gdx_file: GdxFile, filename: str | os.PathLike[str]) -> None:
        """Open ``filename`` for reading and populate ``gdx_file``'s metadata.

        Sets ``gdx_file``'s ``_filename``/``_version``/``_producer``, builds its
        ``universal_set`` and the ``GdxSymbol`` collection (with extended
        per-symbol metadata and resolved domains), but does **not** load records.
        """

    @abc.abstractmethod
    def load_symbols(
        self,
        gdx_file: GdxFile,
        symbols: Sequence[GdxSymbol] | None = None,
        *,
        load_set_text: bool = False,
    ) -> None:
        """Load records into the given ``symbols`` of ``gdx_file``.

        ``symbols is None`` loads every symbol; otherwise only the given
        :class:`~gdxpds.gdx.GdxSymbol` objects (the data model owns name lookup,
        so callers pass objects, not names). Already-loaded symbols are skipped.
        """

    def load_file(self, gdx_file: GdxFile, *, load_set_text: bool = False) -> None:
        """Eagerly load every symbol's records."""
        self.load_symbols(gdx_file, None, load_set_text=load_set_text)

    def load_symbol(self, symbol: GdxSymbol, *, load_set_text: bool = False) -> None:
        """Load the records of a single symbol."""
        self.load_symbols(symbol.file, [symbol], load_set_text=load_set_text)

    @property
    def handle(self) -> object | None:
        """Native engine handle, if any.

        The gdxcc backend returns its GDX pointer; backends without one (e.g.
        gams.transfer) return ``None``. Surfaced through ``GdxFile.H``.
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
        """Write a single symbol (legacy per-symbol path).

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


def make_backend(
    kind: Backend = DEFAULT_BACKEND,
    gams_dir: str | None = None,
    gams_dir_source: str | None = None,
) -> GdxBackend:
    """Construct the concrete backend for ``kind``.

    The implementation module is imported lazily so importing :mod:`gdxpds.gdx`
    does not pull in every backend.
    """
    if kind == Backend.GDXCC:
        from gdxpds._gdxcc_backend import GdxccBackend

        return GdxccBackend(gams_dir, gams_dir_source)
    raise Error(f"Backend {kind!r} is not available.")
