"""The position a folder keeps when no window is looking at it.

`intent.py` owns what the three positions mean and every sentence they say. This module owns the one
place the answer to "what was this folder told to be" is *kept*, so that a terminal process which never
opens a window can be refused by the same fact the window obeys.

It used to live inside a window's own preference block. That block is rebuilt from a list of named keys
by whichever surface saved last, so the desktop window's next save deleted the web window's map of
per-folder positions — and a folder with no stored row is granted on **Change**. Choosing Read-only for
a folder and then opening the other window once was enough to put that folder back on the writable
path, silently. A promise one surface can delete by opening is not a promise, so the declaration moved
out to a file with one purpose and one writer.

Nothing here decides policy. It answers the question and records who answered it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import intent
from .engine import atomic_json, project_key

FILE = ".agent-modes.json"
# Written into the file as a code and spoken by `intent.source`, so the same row reads right in either
# language.
WEB, DESKTOP, TERMINAL, SAVED = "web", "gui", "cli", "saved"

# (mtime_ns, size) -> the parsed block, the same idiom the session cache uses. A write gate asks this on
# every snapshot, and a snapshot goes out on every streamed log line, so it must not re-read the disk.
_cache: dict[str, tuple[tuple, dict]] = {}


def path(app_dir) -> Path:
    return Path(app_dir) / FILE


def _kept(value) -> str:
    """The position this row states, defaulting to the one that writes nothing.

    A file edited by hand, a value from a version that used other words, a row whose mode is a number:
    each of those used to answer "nothing was declared", which is the direction that lets a write
    through. Over-sealing costs the operator one deliberate click to lift it; under-sealing costs a file.
    """
    text = str(value or "").strip().lower()
    return text if text in intent.MODES else intent.READ


def _rows(app_dir) -> dict:
    """Every declared position, keyed by project key. A file nobody can read answers as nothing was
    ever declared, which is the direction that keeps the tool working on a folder it cannot see."""
    file = path(app_dir)
    stamp = os.path.normcase(str(file))
    try:
        info = file.stat()
    except OSError:
        _cache.pop(stamp, None)
        return {}
    key = (info.st_mtime_ns, info.st_size)
    cached = _cache.get(stamp)
    if cached and cached[0] == key:
        return cached[1]
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    rows: dict = {}
    stored = raw.get("modes") if isinstance(raw, dict) else None
    if isinstance(stored, dict):
        for name, row in stored.items():
            # A row that exists but states no readable position is read as sealed, not as absent.
            value = row.get("mode") if isinstance(row, dict) else row
            blank = {} if not isinstance(row, dict) else row
            rows[str(name)] = {"mode": _kept(value),
                               "by": str(blank.get("by", ""))[:40],
                               "at": str(blank.get("at", ""))[:32],
                               "path": str(blank.get("path", ""))[:400]}
    _cache[stamp] = (key, rows)
    return rows


def _write(app_dir, rows: dict) -> None:
    atomic_json(path(app_dir), {"modes": rows})
    _cache.pop(os.path.normcase(str(path(app_dir))), None)


def folder_key(folder) -> str:
    """The project key for a folder, or "" for no folder. `project_key` resolves an empty path to the
    current directory, and a declaration must never land on whatever folder the process happened to be
    started in."""
    return project_key(folder) if str(folder or "").strip() else ""


def mode_for(app_dir, folder) -> str:
    """The position this folder was told to keep, or "" when nothing was ever said about it."""
    key = folder_key(folder)
    if not key:
        return ""
    row = _rows(app_dir).get(key)
    return row["mode"] if row else ""


def sealed(app_dir, folder) -> bool:
    return mode_for(app_dir, folder) == intent.READ


def row_for(app_dir, folder) -> dict:
    key = folder_key(folder)
    return dict(_rows(app_dir).get(key) or {})


def refusal(app_dir, folder, what: str = "Apply", *, arabic: bool = False, badge: str = "") -> str:
    """The line a write gate prints, chosen by *where the promise came from*.

    A window holding the position itself gets the badge's own refusal — the operator knows who set it,
    they just did. A folder carrying a declaration the window never made gets the sentence naming the
    surface and the minute, because "refused" without a who reads like a bug and sends the operator
    looking for a switch that is not in this window.
    """
    row = row_for(app_dir, folder)
    if row.get("mode") == intent.READ and not intent.read_only(badge):
        return intent.declared(by=row.get("by", ""), at=row.get("at", ""), arabic=arabic,
                               project=Path(folder).name if str(folder or "").strip() else "")
    return intent.no_write(what, arabic=arabic)


def declare(app_dir, folder, mode, by="") -> dict:
    """Record the position for this folder and return the row that was written."""
    key = folder_key(folder)
    if not key:
        return {}
    rows = dict(_rows(app_dir))
    # The key is normalised for matching — on Windows that means lower-cased — so the row also carries
    # the path as the file system spells it, because a listing is read by a person.
    row = {"mode": _kept(mode), "by": str(by or "")[:40], "at": time.strftime("%Y-%m-%d %H:%M"),
           "path": str(Path(folder).resolve())[:400]}
    rows[key] = row
    _write(app_dir, rows)
    return row


def forget(app_dir, folder) -> None:
    """Stop declaring anything: the folder goes back to how a new folder is opened."""
    key = folder_key(folder)
    rows = dict(_rows(app_dir))
    if key in rows:
        rows.pop(key)
        _write(app_dir, rows)


def listed(app_dir) -> list[dict]:
    """Every declaration, newest first, for a person who wants to know what is sealed."""
    return sorted(({**row, "folder": key} for key, row in _rows(app_dir).items()),
                  key=lambda row: row["at"], reverse=True)
