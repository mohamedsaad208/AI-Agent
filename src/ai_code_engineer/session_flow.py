"""The rules both windows apply to the same stored records.

Tk and the web window agree on nothing about presentation — one owns a `StringVar`, the other a
snapshot dict — and that is fine. What must not happen is for the two to disagree about *what a
record on disk means*, which is how this project has already had to undo drift: an Apply dialog that
warned about different files depending on which window was open, a plan ledger one window trusted and
the other re-read.

Every function here is a lookup with no side effect. A window keeps its own state — the ledger it has
open, the cache it holds, the row it draws — and calls these to decide what a file or a list says.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import planbook, repair, runner
from .errors import AgentError
from .workspace import Workspace


def read_cached(cache: dict, path: Path, load: Callable[[Path], dict]) -> dict | None:
    """One file, parsed at most once per change.

    The key is what the filesystem can state for free — `mtime_ns` and size — because a navigation
    list reads every session in the folder on each poll, and re-parsing a tree of them is the cost
    that made the sidebar lag. A file that cannot be read is `None` and *stays* `None` in the cache:
    a broken record is not going to heal between two polls a second apart, and re-reading it would
    turn one unreadable session into a repeated cost.
    """
    try:
        info = path.stat()
    except OSError:
        return None
    key = (info.st_mtime_ns, info.st_size)
    cached = cache.get(path)
    if cached and cached[0] == key:
        return cached[1]
    try:
        value = load(path)
    except (AgentError, OSError):
        value = None
    cache[path] = (key, value)
    return value


def ledger_for(plans: Path, session: dict) -> tuple[Path, dict] | None:
    """The plan ledger a stored session belongs to, or None when it is not a plan step.

    The sha check is the whole answer: a ledger opened against a plan whose text has since changed
    describes steps this session never implemented, so it is refused rather than trusted. The caller
    records the pair as the ledger it is standing on; this function opens nothing on its own.
    """
    reference, step_id = session.get("plan_reference"), session.get("plan_step")
    if not reference or step_id is None:
        return None
    try:
        path, book = planbook.open_book(plans, Workspace(Path(session["root"])), reference["path"])
    except (AgentError, OSError):
        return None
    if book.get("plan_sha256") != reference["sha256"]:
        return None
    return path, book


def attempt_rows(runs: Path, session: dict | None, chat_id: str) -> list:
    """The fix attempts the offer, the stop line and the exported report all read."""
    if not session:
        return []
    return repair.attempts_of(runs, session["root"], chat_id)


def attempt_history(runs: Path, session: dict | None, chat_id: str) -> list:
    """Every attempt in this chat and folder, oldest first.

    `repair` reads the record; a window only knows where its own chat lives.
    """
    if not session:
        return []
    return repair.round_history(runs, session["root"], chat_id)


def recipe_for(recipes: list[str], chosen_label: str) -> str | None:
    """The recipe key for the label on screen.

    A window stores the label because that is what a person chose, and the engine wants the key; the
    two are kept apart by this lookup rather than by a second field that can disagree with the first.
    A label that is no longer in the list answers None, which is what disables Run.
    """
    labels = [runner.RECIPES[name]["label"] for name in recipes]
    try:
        return recipes[labels.index(chosen_label)]
    except ValueError:
        return None


def label_for(targets: list[dict], target: str) -> str:
    """Which module of a multi-project folder a command runs in, as the row says it."""
    return next((row["label"] for row in targets if row["path"] == target), "")
