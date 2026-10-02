"""Where a folder's answers to the policy table are kept.

`policy.py` owns what the classes mean and what the table answers. This module owns the one file that
remembers "this folder was told that a custom command is fine", so that a terminal process which never
opened a window is obeying the same fact the window obeys.

The place it lives is the whole design. `modes.py` learned this the hard way: a declaration kept inside
a window's own preference block got deleted by whichever surface saved last, and a folder with no stored
row went back to the writable path silently. A permission store has one worse version of that failure —
inside the approved folder — because the thing the tool is asked to approve is a change to files, and a
proposal that can edit the rules it is being checked against is not checked against anything. So this
file sits beside `.agent-modes.json`, outside every workspace, and the workspace gate that refuses
`.agent-*` paths inside a project is the same gate that keeps a proposal away from this one.

Nothing here decides anything: it answers what was declared, and records who declared it.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import policy
from .engine import atomic_json, project_key

FILE = ".agent-permissions.json"
# Written as a code and spoken by `labels`, the same idiom `modes.py` uses for who set a position.
WEB, DESKTOP, TERMINAL, SAVED = "web", "gui", "cli", "saved"

# (mtime_ns, size) -> the parsed block: a snapshot read asks this on every drawn row, and a snapshot goes
# out on every streamed log line, so it must not re-read the disk each time.
_cache: dict[str, tuple[tuple, dict]] = {}
_thread_locks: dict[str, threading.RLock] = {}
_thread_locks_guard = threading.Lock()


def path(app_dir) -> Path:
    return Path(app_dir) / FILE


@contextmanager
def _store_lock(app_dir):
    """Serialize permission read/modify/write cycles across threads and processes."""
    lock_path = Path(app_dir) / (FILE + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(lock_path.resolve()))
    with _thread_locks_guard:
        thread_lock = _thread_locks.setdefault(key, threading.RLock())
    with thread_lock:
        if os.name == "nt":
            import ctypes
            mutex_name = "Local\\AICodeEngineerPermissions_" + hashlib.sha256(
                key.encode("utf-8", errors="surrogatepass")).hexdigest()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
            kernel.CreateMutexW.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            kernel.WaitForSingleObject.restype = ctypes.c_uint32
            kernel.ReleaseMutex.argtypes = (ctypes.c_void_p,)
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            handle = kernel.CreateMutexW(None, False, mutex_name)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            result = kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
            if result not in (0, 0x80):
                kernel.CloseHandle(handle)
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                yield
            finally:
                kernel.ReleaseMutex(handle)
                kernel.CloseHandle(handle)
        else:
            import fcntl
            with lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _kept(value) -> str:
    """The verdict a stored value states, defaulting to the one that permits nothing.

    A hand-edited row saying `maybe`, a value from a version that used other words, a verdict written as
    a number: each of those answers DENY, because a permission file that cannot be read is not evidence
    of permission. The cost of over-refusing is one deliberate click; the cost of under-refusing is the
    file the rule was there to protect.
    """
    text = str(value or "").strip().lower()
    return text if text in policy.VERDICTS else policy.DENY


def _clean(actions) -> dict:
    """Only classes with a live policy decision point, and only readable verdicts.

    Legacy rows for classes now handled by a stronger, separate safeguard are ignored here. Keeping them
    out of the effective override set makes old settings harmless without treating them as applied rules.
    """
    rows: dict = {}
    if isinstance(actions, dict):
        for name, value in actions.items():
            key = str(name or "")
            if key in policy.CONFIGURABLE_ACTIONS:
                rows[key] = _kept(value)
    return rows


def _rows(app_dir) -> dict:
    """Every declared override, keyed by project key. A file nobody can read declares nothing."""
    file = path(app_dir)
    stamp = os.path.normcase(str(file))
    try:
        info = file.stat()
    except OSError:
        _cache.pop(stamp, None)
        return {}
    mark = (info.st_mtime_ns, info.st_size)
    cached = _cache.get(stamp)
    if cached and cached[0] == mark:
        return cached[1]
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    stored = raw.get("permissions") if isinstance(raw, dict) else None
    rows: dict = {}
    if isinstance(stored, dict):
        for name, row in stored.items():
            blank = {} if not isinstance(row, dict) else row
            # A row that exists but states no readable actions is read as refusing everything it was
            # ever asked about, not as permitting them: it was written for a reason someone cannot now
            # read, and the safe reading of an unreadable grant is no grant.
            rows[str(name)] = {"actions": _clean(blank.get("actions")),
                               "by": str(blank.get("by", ""))[:40],
                               "at": str(blank.get("at", ""))[:32],
                               "path": str(blank.get("path", ""))[:400]}
    _cache[stamp] = (mark, rows)
    return rows


def _write(app_dir, rows: dict) -> None:
    atomic_json(path(app_dir), {"permissions": rows})
    _cache.pop(os.path.normcase(str(path(app_dir))), None)


def folder_key(folder) -> str:
    """The project key for a folder, or "" for no folder — a rule must never land on whatever folder
    the process happened to be started in."""
    return project_key(folder) if str(folder or "").strip() else ""


def overrides(app_dir, folder) -> dict:
    """The action classes this folder was told about, and the verdict each was given."""
    key = folder_key(folder)
    if not key:
        return {}
    return dict(_rows(app_dir).get(key, {}).get("actions") or {})


def verdict(app_dir, folder, action: str) -> str:
    """The answer for this folder: its own declaration when it made one, the table otherwise.

    An override changes the table's answer for a class that has a live decision point. The table does not
    claim actor-specific restrictions: shell commands and network requests currently enter through
    operator-triggered controls, and any future task-originated action needs its own checked boundary.
    """
    name = str(action or "")
    return policy.decide(name, overrides(app_dir, folder).get(name, ""))


def declare(app_dir, folder, action: str, value: str, by: str = "") -> dict:
    """Record one verdict for one class in one folder, and return the row that was written.

    An action without a policy decision point is refused rather than stored as an override nobody will
    ever consult. Its safeguard is described by the interface instead.
    """
    key = folder_key(folder)
    name = str(action or "")
    if not key or name not in policy.CONFIGURABLE_ACTIONS:
        return {}
    with _store_lock(app_dir):
        rows = dict(_rows(app_dir))
        row = dict(rows.get(key) or {})
        actions = dict(row.get("actions") or {})
        actions[name] = _kept(value)
        rows[key] = {"actions": actions, "by": str(by or "")[:40],
                     "at": time.strftime("%Y-%m-%d %H:%M"),
                     "path": str(Path(folder).resolve())[:400]}
        _write(app_dir, rows)
        return rows[key]


def forget(app_dir, folder, action: str = "") -> None:
    """Drop one class's declaration, or the folder's whole set. What is left answers the table."""
    key = folder_key(folder)
    with _store_lock(app_dir):
        rows = dict(_rows(app_dir))
        if key not in rows:
            return
        name = str(action or "")
        if not name:
            rows.pop(key)
        elif name in policy.CONFIGURABLE_ACTIONS and name in rows[key].get("actions", {}):
            actions = dict(rows[key]["actions"])
            actions.pop(name, None)
            rows[key] = {**rows[key], "actions": actions}
            if not actions:
                rows.pop(key)
        else:
            return
        _write(app_dir, rows)


def listed(app_dir) -> list[dict]:
    """Every declared override, newest first, for a person who wants to know what was opened."""
    out = []
    for key, row in _rows(app_dir).items():
        for name, value in sorted(row["actions"].items()):
            out.append({"folder": key, "path": row.get("path", ""), "action": name,
                        "verdict": value, "by": row.get("by", ""), "at": row.get("at", "")})
    return sorted(out, key=lambda item: item["at"], reverse=True)
