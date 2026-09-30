"""Ordered plan steps with a ledger, so a phase gates on real proof instead of memory.

An attached plan used to be reference text: the model was told to do "the phase the user
asked for", and the sequence lived in whoever typed the next request. This module makes
the order a fact on disk. A step is complete only when a command run proved that tests
executed and none failed, and the next step's task is generated from what is already done.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from .engine import atomic_json
from .errors import PolicyError
from . import memory as memory_store
from .workspace import Workspace

MAX_STEPS = 50
STEP_HEADING = re.compile(
    r"(?m)^[ \t]{0,3}#{1,6}[ \t]*(?:(?:phase|step|task|stage)s*[ \t]+)?\b(\d+)\b[^\w\r\n]*(?P<title>[^\r\n]*)\r?$", re.I)
LIST_STEP = re.compile(r"(?m)^(?P<indent> {0,3})(?P<number>\d+)[.)][ \t]+(?P<title>[^\r\n]+)\r?$")


def _outside_fences(text: str) -> str:
    """Mask fenced examples without changing offsets into the original plan."""
    out, fence = [], ""
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
        hidden = bool(fence or marker)
        if fence:
            if re.fullmatch(r"[ \t]*" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*", line.rstrip("\r\n")):
                fence = ""
        elif marker:
            fence = marker[1]
        out.append(re.sub(r"[^\r\n]", " ", line) if hidden else line)
    return "".join(out)


def parse_steps(text: str) -> list[dict]:
    """Prefer numbered headings, then top-level numbered lists; never silently truncate."""
    visible = _outside_fences(text)
    matches = list(STEP_HEADING.finditer(visible))
    listed = not matches
    if listed:
        candidates = list(LIST_STEP.finditer(visible))
        if candidates:
            indent = min(len(m["indent"]) for m in candidates)
            matches = [m for m in candidates if len(m["indent"]) == indent]
            # A standalone year-like sentence is not enough evidence of an executable list.
            # Lists starting at 1 or containing multiple peer items are explicit enough.
            if len(matches) == 1 and int(matches[0]["number"]) != 1:
                matches = []
    if not matches:
        body = text.strip()
        if not body:
            raise PolicyError("The plan has no steps to run.")
        return [{"id": 1, "title": body.splitlines()[0][:90], "body": body[:6000]}]
    if len(matches) > MAX_STEPS:
        raise PolicyError(f"Plan has {len(matches)} steps; the limit is {MAX_STEPS}. Split the plan into smaller plans.")
    steps = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if listed:
            # Only indented continuation lines belong to a list item. Section headings and
            # unindented prose end it, even when another numbered section follows later.
            offset = match.end()
            for line in text[offset:end].splitlines(keepends=True):
                shown = visible[offset:offset + len(line)]
                if shown.strip() and (shown.lstrip().startswith("#") or
                                    len(line) - len(line.lstrip(" \t")) <= len(match["indent"])):
                    end = offset
                    break
                offset += len(line)
        title = (match.group("title") or "").strip().strip(":—-–") or f"step {index + 1}"
        for marker in ("**", "__", "*", "_"):
            if title.startswith(marker) and title.endswith(marker) and len(title) > 2 * len(marker):
                title = title[len(marker):-len(marker)].strip()
                break
        steps.append({"id": len(steps) + 1, "title": title[:90],
                      "body": text[match.end():end].strip()[:6000]})
    return steps


def key_for(root: str, plan_sha: str) -> str:
    """Readable, unique per resolved project root, and per plan content.

    The folder's own name is not enough: two checkouts of one project share the name `demo`,
    and a ledger they both read hands a verified step to the folder that never ran it. The
    root half is exactly the key the project notes use, so the two stores cannot disagree.
    """
    return memory_store.key_for(root) + "-" + plan_sha[:16]


def relative_name(ws: Workspace, plan_file: str) -> str:
    """The plan's workspace-relative name, whether the caller has a path or a full path."""
    candidate = Path(plan_file)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(ws.root)
        except ValueError:
            raise PolicyError("The plan file must be inside the selected project folder.") from None
    return candidate.as_posix()


def _same_root(stored: object, root: Path) -> bool:
    """A ledger that disagrees about which folder it belongs to is not that folder's ledger."""
    try:
        return (str(Path(str(stored)).expanduser().resolve()).casefold()
                == str(Path(root).expanduser().resolve()).casefold())
    except OSError:
        return False


def open_book(plans_dir: Path, ws: Workspace, plan_file: str) -> tuple[Path, dict]:
    """Load the ledger for this plan, or start one. A changed plan starts a new ledger."""
    reference = ws.read(relative_name(ws, plan_file))
    parsed = parse_steps(reference["content"])
    path = plans_dir / (key_for(str(ws.root), reference["sha256"]) + ".json")
    if path.exists():
        try:
            book = json.loads(path.read_text(encoding="utf-8"))
            if (book.get("schema") == 1 and book.get("plan_sha256") == reference["sha256"]
                    and _same_root(book.get("root", ""), ws.root)
                    and isinstance(book.get("steps"), list)):
                rows = book["steps"]
                body = reference["content"].strip()
                fallback = (len(rows) == 1 and rows[0].get("id") == 1
                            and rows[0].get("title") == body.splitlines()[0][:90]
                            and rows[0].get("body") == body[:6000])
                changed = fallback and any(rows[0].get(k) != parsed[0].get(k)
                                           for k in ("title", "body"))
                if not (changed or fallback and len(parsed) > 1):
                    return path, book
                if (rows[0].get("status") != "pending" or rows[0].get("session_id")
                        or rows[0].get("verified_at")):
                    raise PolicyError("This legacy plan ledger is linked to prior work. Its progress was preserved. "
                                      "Review that work and create a revised plan before starting new steps.")
                # Rebuild only untouched fallback ledgers in memory. The old file stays intact
                # until record_session persists the first newly selected step.
        except (OSError, ValueError, TypeError):
            pass
    book = {"schema": 1, "root": str(ws.root), "plan_path": reference["path"],
            "plan_sha256": reference["sha256"],
            "steps": [dict(row, status="pending", session_id=None, verified_at=None)
                      for row in parsed]}
    return path, book


def step(book: dict, step_id: int) -> dict | None:
    return next((row for row in book["steps"] if row["id"] == step_id), None)


def current(book: dict) -> dict | None:
    return next((row for row in book["steps"] if row["status"] != "verified"), None)


def done_titles(book: dict) -> list[str]:
    return [row["title"] for row in book["steps"] if row["status"] == "verified"]


def task_for(book: dict, row: dict, note: str = "") -> str:
    """The per-turn task the ledger sends instead of a hand-written phase request."""
    lines = [f"Implement step {row['id']} of {len(book['steps'])} from the attached plan: "
             + row["title"] + "."]
    if row.get("body"):
        lines.append("This step asks for:\n" + row["body"][:3000])
    finished = done_titles(book)
    if finished:
        lines.append("Already applied and verified by a command run — do not redo, rename or "
                     "rewrite them: " + ", ".join(finished) + ".")
    lines.append("Touch only the files this step needs. Do not run builds or tests yourself; "
                 "the user runs the command and approves each proposal.")
    if note:
        lines.append("User note for this step: " + note[:800])
    return "\n".join(lines)[:4000]


def proof_reason(session: dict) -> str:
    """Why this session's last command run does, or does not, prove the step works."""
    if session.get("state") != "CHECKS_PASSED":
        return "the session is not in CHECKS_PASSED"
    runs = session.get("runs") or []
    if not runs:
        return "no command has been recorded for this session"
    run = runs[-1]
    if run.get("status") != "passed":
        return "the last command did not pass"
    proof = run.get("proof")
    if proof:
        if proof.get("failures") or proof.get("errors"):
            return "the test report shows failures or errors"
        if proof.get("tests", 0) - proof.get("skipped", 0) <= 0:
            return "the test report contains no executed test"
        return ""
    if run.get("tests_observed"):
        return ""
    # A compile-only recipe asks for no tests; the build passing is its proof.
    if str(run.get("recipe", "")).endswith("compile"):
        return ""
    return "no test was observed running"


def complete(path: Path, book: dict, step_id: int, session: dict) -> dict:
    """Mark a step verified only from a session that proved it, then persist the ledger."""
    row = step(book, step_id)
    if row is None:
        raise PolicyError("Unknown plan step: " + str(step_id)[:20])
    if row["status"] == "verified":
        return book
    if session.get("id") != row.get("session_id"):
        raise PolicyError("That session is not the one recorded for this plan step.")
    reason = proof_reason(session)
    if reason:
        raise PolicyError(f"Plan step {step_id} cannot be marked done: {reason}.")
    row.update(status="verified", verified_at=session.get("created"))
    atomic_json(path, book)
    return book


def record_session(path: Path, book: dict, step_id: int, session_id: str) -> dict:
    """Bind the session working on a step; a fix round rebinds it to the newer session."""
    row = step(book, step_id)
    if row is None:
        raise PolicyError("Unknown plan step: " + str(step_id)[:20])
    row["session_id"] = session_id
    if row["status"] != "verified":
        row["status"] = "in_progress"
    atomic_json(path, book)
    return book


def reopen(path: Path, book: dict, step_id: int) -> dict:
    """Un-verify a step whose applied files were rolled back, so the next one cannot build on nothing."""
    row = step(book, step_id)
    if row is None or row["status"] != "verified":
        return book
    row.update(status="in_progress", verified_at=None)
    atomic_json(path, book)
    return book


def progress_line(book: dict) -> str:
    verified = len(done_titles(book))
    row = current(book)
    if row is None:
        return f"Plan complete — {verified}/{len(book['steps'])} steps verified"
    return (f"Plan step {row['id']}/{len(book['steps'])}: {row['title']} "
            f"({verified} verified)")
