"""What a granted folder looks like: the picker that chooses one, and the drawer that measures it.

Two promises shape this file.

**A blank measurement is a measurement, not a failure.** `context_use` and `toolchain` answer for a
folder that may not be on this disk right now, and the drawer has to tell "this project has nothing"
apart from "the window could not look". Both return the same keys either way, with zeroes where
nothing was read — an absent key would render as a different claim.

**Nothing here decides.** Choosing a folder, binding a chat to it and removing it are the
controller's, because they write `self.projects` / `self.branch` under its lock. This module walks a
path it is handed. `listing` is the one function that reads a path the browser named, and it only
ever reads.
"""
from __future__ import annotations

import ctypes
import os
import re
import shlex
from pathlib import Path

from .. import ignore, runner, symbols
from ..chat import context_block, context_use as measure_context
from ..config import Settings
from ..errors import PolicyError
from ..workspace import Workspace


def hidden(path: Path) -> bool:
    """The picker's own rule, older than the shared one: a dot folder is not where the project is.

    `ignore.picker_dir` decides the rest beside it. This stays separate because it is a *navigation*
    choice — the file-system gate that protects `.git` and the credentials lives in `ignore`, and a
    folder being tedious to browse is not the same reason to refuse it.
    """
    return path.name.startswith(".")


def drive_roots() -> list[str]:
    """Every mounted root, so the folder picker can leave the drive it started on."""
    if os.name != "nt":
        return ["/"]
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except (OSError, AttributeError):
        mask = 1
    return [letter for letter in
            (chr(ord("A") + index) + ":\\" for index in range(26) if mask & (1 << index))
            if Path(letter).is_dir()]


def listing(path: str, want_files=None) -> dict:
    """One directory level, for the in-page folder browser (no native dialogs in a browser).

    The path comes from the browser, so it is only ever read; an entry that cannot be opened is
    skipped rather than reported, because a listing that refuses to answer over one unreadable folder
    is a picker the user cannot drive.
    """
    root = Path(path or Path.home())
    if not root.is_dir():
        root = Path.home()
    dirs, files = [], []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        try:
            if entry.is_dir():
                if not hidden(entry) and not ignore.picker_dir(entry.name):
                    dirs.append(entry)
            elif want_files:
                want_set = {str(s).lower() if str(s).startswith(".") else "." + str(s).lower()
                            for s in want_files}
                if entry.suffix.lower() in want_set:
                    files.append(entry)
        except OSError:
            continue
    # A drive root has no parent, so without the drive list the picker is a dead end on C: and
    # a project on another disk cannot be reached at all.
    return {"path": str(root), "parent": str(root.parent) if root.parent != root else None,
            "roots": drive_roots(),
            "dirs": [{"name": p.name, "path": str(p)} for p in dirs[:400]],
            "files": [{"name": p.name, "path": str(p)} for p in files[:400]]}


def initials_for(name: str) -> str:
    """A two-letter mark that survives near-identical folder names.

    ``demo2`` and ``demo_repo`` both start with "de", which made the sidebar avatars
    indistinguishable; segment boundaries and a trailing digit separate them.
    """
    parts = [part for part in re.split(r"[\s._\-]+", name) if part]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).casefold()
    word = parts[0] if parts else "?"
    digit = next((char for char in word[1:] if char.isdigit()), "")
    return (word[0] + (digit or (word[1] if len(word) > 1 else ""))).casefold()


def context_use(path: Path, notes: str, chat: dict | None) -> dict:
    """Map, conversation and what is left, against the configured context budget.

    `chat` is the conversation that reads this folder — or None, which is both "this is another
    project's drawer" and "this chat is bound to nothing", and spends none of this folder's budget.
    """
    settings = Settings()
    blank = {"system": 0, "context": 0, "turns": 0, "kept": 0, "used": len(notes),
             "budget": settings.context_chars, "remaining": settings.context_chars,
             "est_tokens": 0, "map": 0, "notes": len(notes), "files": 0, "bound": False}
    if not path.is_dir():
        return blank
    try:
        repo = Workspace(path)
        text = repo.repo_map()
        files = len(repo.files(limit=symbols.MAX_FILES))
    except (PolicyError, OSError):
        return blank
    context = context_block(text, notes) if text or notes else ""
    use = measure_context(chat, settings, context)
    return {**use, "map": len(text), "notes": len(notes), "files": files, "bound": bool(chat)}


def toolchain(path: Path, *, exists: bool, selected_hint: str | None,
              request_timeout: int) -> dict:
    """What this folder can run, and which command the drawer would pick.

    `selected_hint` is the recipe chosen for the branch in front of the user, or None for any other
    folder's drawer — which can only report what would be picked by default, never what the window is
    standing on.
    """
    if not exists:
        return {"detected": [], "selected": "", "timeout": 0, "proof": "",
                "request_timeout": request_timeout}
    detected = runner.detect(path)
    selected = selected_hint or (detected[0] if detected else "")
    entry = runner.RECIPES.get(selected) or {}
    writes_report = bool(entry.get("reports") or entry.get("junit_arg"))
    return {"detected": [{"name": name, "label": runner.RECIPES[name]["label"],
                          "command": shlex.join(runner.RECIPES[name]["command"])}
                         for name in detected],
            "selected": selected,
            "timeout": runner.timeout_for(selected) if selected else 0,
            "proof": (entry.get("proof_source", "JUnit XML report") if writes_report
                      else "the command's own summary line") if selected else "",
            "request_timeout": request_timeout}
