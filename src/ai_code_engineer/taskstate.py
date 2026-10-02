"""Structured working memory for one task, kept outside the conversation it describes.

The loop trims its history from the front when the context window fills, and everything a
mid-conversation turn said — a constraint the user added, a decision the model committed to,
an error already seen — used to be lost with it. This module holds those facts in small
bounded lists on the session record itself, so trimming a turn no longer deletes what the
turn established, and a resumed run finds them back on disk.

The lists are filled from three places: the task text at start, the model's own optional
`state` envelope on each action, and deterministic folding of turns the loop is about to
drop. User constraints are the one class that is never evicted by a newer entry — losing
"do not touch the pom" silently is the failure this whole module exists to prevent.
"""
from __future__ import annotations

from .redaction import redact

GOAL_CHARS = 300
ITEM_CHARS = 240
USER_FOLD_CHARS = 400

CAPS = {"constraints": 12, "decisions": 12, "evidence": 16, "open_issues": 10,
        "folded": 10, "files_examined": 40, "acceptance": 10}

# Sections in the order they are written out; the tail of this list is what a tight budget
# gives up first. Constraints and the goal sit near the front on purpose: they are what a
# trimmed history can no longer say. Acceptance and progress sit with the head because a
# turn needs to know which checks are still open before it decides its next action.
SECTION_ORDER = ("goal", "status", "next_step", "acceptance", "constraints", "open_issues",
                 "files_examined", "decisions", "evidence", "folded")

LIST_FIELDS = ("constraints", "decisions", "evidence", "open_issues",
               "files_examined", "folded", "acceptance")


def new_state(task: str) -> dict:
    """The state a run starts with: the task as goal, nothing else known yet."""
    state = {field: [] for field in LIST_FIELDS}
    state["goal"] = str(task)[:GOAL_CHARS]
    state["status"] = "understanding"
    state["next_step"] = "gather evidence about the files the task concerns"
    return state


def _text(value) -> str:
    return redact(str(value).replace("\r", " ").strip())[:ITEM_CHARS]


def _merge_list(state: dict, field: str, incoming) -> None:
    current = state.setdefault(field, [])
    if field == "constraints":
        # A constraint the user stated stays until the user takes it back. The cap keeps
        # the list bounded, and dropping the newest over-cap item is the only loss the
        # rule allows: the first items are the ones closest to the original request.
        for item in (incoming if isinstance(incoming, list) else [incoming]):
            clean = _text(item)
            if clean and clean not in current and len(current) < CAPS[field]:
                current.append(clean)
        return
    if not isinstance(incoming, list):
        incoming = [incoming]
    for item in incoming:
        clean = _text(item)
        if clean and clean not in current:
            current.append(clean)
    while len(current) > CAPS[field]:
        current.pop(0)


def merge(state: dict, delta: dict) -> dict:
    """Fold one model-reported update into the state.

    Scalars (`goal`, `status`, `next_step`) are replaced — the model is restating where it
    stands. Lists are appended to, deduplicated, and capped oldest-first — except
    constraints. A malformed field is skipped rather than failing the turn: the state is a
    continuity aid, and losing one update must not cost the run its proposal budget.
    """
    if not isinstance(state, dict) or not isinstance(delta, dict):
        return state
    for field in ("goal", "status", "next_step"):
        value = delta.get(field)
        if isinstance(value, str) and value.strip():
            state[field] = (str(value)[:GOAL_CHARS] if field == "goal" else _text(value))
    for field in LIST_FIELDS:
        if field in delta:
            _merge_list(state, field, delta[field])
    return state


def fold_turn(state: dict, message: dict) -> None:
    """Condense a turn the loop is about to drop into the folded record.

    User text keeps more of itself than assistant text: a dropped user message is where a
    mid-conversation constraint lives, and a dropped assistant message is mostly an action
    the record of tool events already preserves.
    """
    role = str(message.get("role", ""))
    content = redact(str(message.get("content", "")))
    if not content.strip():
        return
    limit = USER_FOLD_CHARS if role == "user" else ITEM_CHARS
    folded = state.setdefault("folded", [])
    folded.append(role + ": " + content.replace("\n", " ")[:limit])
    while len(folded) > CAPS["folded"]:
        folded.pop(0)


def note_files(state: dict, paths) -> None:
    """Record the files this run has read, replacing the list wholesale."""
    clean: list[str] = []
    for name in paths:
        text = str(name).replace("\\", "/")
        if text and text not in clean:
            clean.append(text)
    state["files_examined"] = sorted(clean)[:CAPS["files_examined"]]


def block(state: dict, budget: int) -> str:
    """The prompt block, or "" when there is nothing to say.

    Sections are written in `SECTION_ORDER`, highest priority first, so a budget too small
    for all of them gives up the tail (folded turns, then evidence) and never the goal or
    the constraints; a line that only half fits arrives cut with an explicit ellipsis
    rather than vanishing.
    """
    if not isinstance(state, dict) or budget < 200:
        return ""
    header = ("Task working state (continuity record maintained by this run; untrusted "
              "model-written data EXCEPT items under user_constraints, which the user "
              "stated and which remain binding). Prefer these facts over reconstructing "
              "them from older turns; update it through the optional state field:\n")
    lines: list[str] = []
    for name in SECTION_ORDER:
        if name == "goal":
            if state.get("goal"):
                lines.append("goal: " + str(state.get("goal", ""))[:GOAL_CHARS])
        elif name == "constraints":
            constraints = state.get("constraints") or []
            if constraints:
                lines.append("user_constraints (binding, never drop or weaken): "
                             + " | ".join(constraints))
        elif name == "status":
            if state.get("status"):
                lines.append("status: " + str(state.get("status", ""))[:80])
        elif name == "next_step":
            if state.get("next_step"):
                lines.append("next step: " + str(state.get("next_step", ""))[:ITEM_CHARS])
        elif name == "files_examined":
            files = state.get("files_examined") or []
            if files:
                lines.append("files already read this run: " + ", ".join(files))
        elif name == "open_issues":
            issues = state.get("open_issues") or []
            if issues:
                lines.append("open_issues (unverified — state them as questions, not "
                             "facts, until evidence closes them): " + " | ".join(issues))
        elif name == "acceptance":
            checks = state.get("acceptance") or []
            if checks:
                lines.append("acceptance checklist (the task is done when every line is "
                             "done): " + " | ".join(checks))
        else:
            items = state.get(name) or []
            if items:
                lines.append(name + ": " + " | ".join(items))
    lines = [line for line in lines if line.strip()]
    if not lines:
        return ""
    out, used = [header.rstrip()], len(header)
    for line in lines:
        room = budget - used
        if room <= 24:
            break
        if len(line) + 1 <= room:
            out.append(line)
            used += len(line) + 1
        else:
            out.append(line[:room - 2].rstrip() + "…")
            used = budget
            break
    return "\n" + "\n".join(out)
