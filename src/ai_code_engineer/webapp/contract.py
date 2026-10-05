"""The seam between the UI server and whatever drives it.

The server knows nothing about planning, approvals or the file system: it only ever calls the
methods below and forwards whatever the controller emits. That keeps the front-end reviewable now
(against ``fake.FakeController``) while the real controller is still being extracted from the Tk
window.
"""
from __future__ import annotations

from typing import Callable, Iterable, Protocol


Sink = Callable[[dict], None]
"""Pushes one UI event to every connected browser."""


class Controller(Protocol):
    def snapshot(self) -> dict:
        """The whole UI state, serialisable, so a fresh client can paint in one round trip."""
        ...

    def action(self, type: str, payload: dict, emit: Sink) -> dict | None:
        """Run one user intent. Long work goes to a thread and reports through ``emit``."""
        ...

    def project_info(self, key: str) -> dict:
        """One granted folder, measured: its path, notes and size. Raises ``PolicyError`` for a
        key that was never granted — the server turns that into a 400 the browser can show."""
        ...

    def memory_panel(self, chat_id: str = "") -> dict:
        """The project's own memory, as one readout: both layers, the block they build and its cost.
        Asked for on the click rather than pushed with every snapshot, the same way ``project_info``
        is. Says ``{"available": False, "why": ...}`` when there is no folder to remember for."""
        ...

    def list_dir(self, path: str, want_files: Iterable[str] | None = None) -> dict:
        """One directory level, for the in-page folder browser (no native dialogs in a browser)."""
        ...

    def set_reply(self, request_id: str, reply: dict) -> None:
        """Deliver the answer to a ``confirm`` or ``pick`` event the controller asked for."""
