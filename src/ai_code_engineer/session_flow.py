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


def resumable_runs(runs: Path, root: str, cache: dict, limit: int = 8) -> list[dict]:
    """Tasks in this folder that stopped before they reached a result, newest first.

    Only a run still in DISCOVERING counts. Every other state is a run that said something — a proposal,
    a verdict, a cancellation — and the files on disk already answer it. A run with no continuation
    written beside it is still listed, with turn 0, because the operator needs to know it is there even
    when there is nothing to carry on from. Showing the list is not the same as resuming it: nothing in
    this function starts anything, and no window may resume a task by itself.
    """
    from .engine import load_session, load_turns, turns_file

    def identity(value: str) -> str:
        try:
            return str(Path(value).expanduser().resolve()).casefold()
        except OSError:
            return ""

    wanted = identity(str(root or ""))
    if not wanted:
        return []
    try:
        files = sorted(runs.glob("*/session.json"), key=lambda path: path.stat().st_mtime,
                       reverse=True)
    except OSError:
        return []
    found: list[dict] = []
    for path in files[:200]:
        session = read_cached(cache, path, load_session)
        if not session or session.get("state") != "DISCOVERING":
            continue
        if identity(str(session.get("root") or "")) != wanted:
            continue
        stored = load_turns(turns_file(path)) or {}
        found.append({"run_id": str(session.get("id") or path.parent.name),
                      "task": str(session.get("task") or "")[:90],
                      "turn": int(stored.get("turn") or 0),
                      "plan_step": session.get("plan_step"),
                      "written": str(stored.get("written") or session.get("created") or "")})
        if len(found) >= limit:
            break
    return found
