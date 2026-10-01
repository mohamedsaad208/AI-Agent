"""Per-project notes the user writes, so a decision survives the task that created it.

A small local model re-derives package names, Java versions and naming rules from scratch
on every task, and the user re-types them in the message box. This module keeps those
decisions in one file per project, *outside* the approved folder: a proposal can never
edit the notes it is being held to, which is the same reason the plan ledger lives outside.
"""
from __future__ import annotations

import hashlib
import json
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


# ----------------------------- Automatic Project Notes -----------------------------

def auto_notes_path(memory_dir: Path, root: str) -> Path:
    if not str(root).strip():
        raise PolicyError("Choose a project folder before reading its auto-notes.")
    return Path(memory_dir) / (key_for(root) + ".auto.json")


def read_auto_notes(memory_dir: Path, root: str) -> dict:
    default_notes = {
        "enabled": True,
        "updated_at": "",
        "purpose_and_stack": "",
        "components": [],
        "execution_commands": {},
        "implemented_changes": [],
        "verification_results": "untested",
        "remaining_issues": []
    }
    if not str(root).strip():
        return default_notes
    path = auto_notes_path(memory_dir, root)
    if not path.is_file():
        return default_notes
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {**default_notes, **data}
    except Exception:
        pass
    return default_notes


def write_auto_notes(memory_dir: Path, root: str, data: dict) -> Path:
    if not str(root).strip():
        raise PolicyError("Choose a project folder before saving auto-notes.")
    path = auto_notes_path(memory_dir, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Redact secrets in serialized data
    def _sanitize(val):
        if isinstance(val, str):
            from .redaction import redact
            return redact(val)
        if isinstance(val, list):
            return [_sanitize(x) for x in val]
        if isinstance(val, dict):
            return {k: _sanitize(v) for k, v in val.items()}
        return val

    clean_data = _sanitize(data)
    handle, staged = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(clean_data, stream, indent=2)
        os.replace(staged, path)
    except OSError as exc:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise PolicyError("Could not save automatic project notes: " + str(exc)[:160]) from None
    return path


def update_auto_notes_from_task(memory_dir: Path, root: str, task_info: dict) -> dict:
    """Incrementally updates project auto-notes after a task completes or verifies."""
    current = read_auto_notes(memory_dir, root)
    if not current.get("enabled", True):
        return current

    from datetime import datetime, timezone
    from .redaction import redact
    current["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Purpose and stack
    stack = task_info.get("stack")
    if stack and not current.get("purpose_and_stack"):
        current["purpose_and_stack"] = redact(str(stack))

    # Key components and files (keep bounded to 20 files max)
    new_files = [str(f) for f in task_info.get("files", []) if str(f).strip()]
    existing_comps = set(current.get("components", []))
    for f in new_files:
        existing_comps.add(redact(f))
    current["components"] = sorted(list(existing_comps))[:20]

    # Execution commands
    cmds = task_info.get("commands", {})
    if isinstance(cmds, dict):
        merged_cmds = dict(current.get("execution_commands", {}))
        merged_cmds.update({k: redact(str(v)) for k, v in cmds.items() if v})
        current["execution_commands"] = merged_cmds

    # Implemented changes (distinguish applied, verified, untested)
    change_title = task_info.get("title") or task_info.get("summary")
    status = task_info.get("status") or "applied"   # "applied", "verified", "untested"
    if change_title:
        clean_title = redact(str(change_title)[:120])
        changes = current.get("implemented_changes", [])
        # Replace if same title, else append
        filtered = [c for c in changes if c.get("title") != clean_title]
        filtered.append({
            "title": clean_title,
            "status": status,
            "time": current["updated_at"]
        })
        current["implemented_changes"] = filtered[-10:]

    # Verification status
    verif = task_info.get("verification")
    if verif:
        current["verification_results"] = redact(str(verif)[:200])

    # Remaining issues
    issues = task_info.get("issues")
    if isinstance(issues, list):
        current["remaining_issues"] = [redact(str(i)[:120]) for i in issues[:5]]

    write_auto_notes(memory_dir, root, current)
    return current


def auto_notes_context(memory_dir: Path, root: str, max_chars: int = 1500) -> str:
    """Concise bounded summary to ground agent in project context."""
    notes = read_auto_notes(memory_dir, root)
    if not notes.get("enabled", True):
        return ""
    parts = []
    if notes.get("purpose_and_stack"):
        parts.append(f"Stack/Purpose: {notes['purpose_and_stack']}")
    if notes.get("components"):
        parts.append(f"Key Files: {', '.join(notes['components'][:8])}")
    if notes.get("execution_commands"):
        cmd_strs = [f"{k}: {v}" for k, v in notes["execution_commands"].items()]
        parts.append(f"Known Commands: {', '.join(cmd_strs)}")
    if notes.get("verification_results"):
        parts.append(f"Verification: {notes['verification_results']}")
    if notes.get("implemented_changes"):
        recent = [f"{c['title']} ({c.get('status', 'applied')})" for c in notes["implemented_changes"][-3:]]
        parts.append(f"Recent Changes: {'; '.join(recent)}")

    if not parts:
        return ""
    text = "Auto-Observed Project Summary:\n" + "\n".join(f"- {p}" for p in parts)
    return text[:max_chars]
