"""Per-project notes the user writes, so a decision survives the task that created it.

A small local model re-derives package names, Java versions and naming rules from scratch
on every task, and the user re-types them in the message box. This module keeps those
decisions in one file per project, *outside* the approved folder: a proposal can never
edit the notes it is being held to, which is the same reason the plan ledger lives outside.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile

from .errors import PolicyError

MAX_MEMORY = 4000
LABEL = ("Project notes the user wrote for this folder. These are the user's own standing "
         "instructions, not model output and not observed code. Follow them unless the "
         "current task overrides one; say so in the summary if a note blocks you:\n")


def key_for(root: str) -> str:
    """A name that is readable in the folder and unique per resolved project root.

    The same folder typed with different separators or case must land on one file, or a
    note about "never rename this package" would silently apply to only one spelling.
    """
    try:
        resolved = Path(root).expanduser().resolve()
    except OSError:
        resolved = Path(root)
    return re.sub(r"[^A-Za-z0-9._-]", "-", resolved.name) + "-" + \
        hashlib.sha256(str(resolved).casefold().encode("utf-8")).hexdigest()[:16]


def path_for(memory_dir: Path, root: str) -> Path:
    if not str(root).strip():
        raise PolicyError("Choose a project folder before editing its notes.")
    return Path(memory_dir) / (key_for(root) + ".md")


def read(memory_dir: Path, root: str) -> str:
    try:
        return path_for(memory_dir, root).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise PolicyError("Could not read the project notes: " + str(exc)[:160]) from None


def write(memory_dir: Path, root: str, text: str) -> Path:
    """Replace the notes, or clear the file when the box is emptied."""
    if len(text) > MAX_MEMORY:
        raise PolicyError(f"Project notes must stay within {MAX_MEMORY} characters "
                          f"(this is {len(text)}).")
    path = path_for(memory_dir, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.strip():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return path
    # Written through a temporary file in the same folder, so a crash mid-save cannot
    # leave half a note that the next task would still be told to obey.
    handle, staged = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(staged, path)
    except OSError as exc:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise PolicyError("Could not save the project notes: " + str(exc)[:160]) from None
    return path


def block(text: str) -> str:
    """The labelled context a planning turn receives, empty when there are no notes."""
    trimmed = (text or "").strip()
    return (LABEL + trimmed[:MAX_MEMORY]) if trimmed else ""


def record(session: dict, text: str) -> None:
    """Keep the delivered notes on the session for audit, outside the proposal hash."""
    session["memory"] = text.strip()[:MAX_MEMORY]
    session["memory_sha256"] = hashlib.sha256(session["memory"].encode("utf-8")).hexdigest()
