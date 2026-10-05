"""One file's diff, painted — the function every surface reads a proposal through.

This is the only module allowed to decide what a diff looks like: which lines are in it, how many
of them moved, which of the three badges a file wears, and what one honest sentence says about a
file the model edited. It answers in plain strings and dicts because the CLI colours them, the rail
lays them out in HTML, and the Tk window tags them; three renderings of one decision, not three
decisions. Keeping it free of ``rich`` is what lets the web server import it at all — the promises
say the application runs on the standard library alone, and an optional prettiness belongs in the
view that uses it, not in the answer every view depends on.

Nothing here guesses intent. A summary is the recorded sentence when the proposal kept one,
otherwise the names that actually moved, otherwise a line count — and no second call to a model
just to caption a file.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

CONTEXT = 3

# Names a reader recognises, taken from the lines that actually changed. Three patterns because the
# languages the agent is pointed at differ in how they open a definition, not because each needs its
# own summary.
DEF_NAMES = re.compile(r"^\s*(?:(?:async\s+)?def|class|function)\s+([\w$]+)", re.MULTILINE)
BINDING_NAMES = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([\w$]+)\s*=", re.MULTILINE)
MARKUP_TAGS = re.compile(r"<([A-Za-z][\w-]*)\b")
MARKUP_SUFFIXES = {".html", ".htm", ".vue", ".jsx", ".tsx"}
SUMMARY_NAMES = 3
SUMMARY_CHARS = 160

CHANGE_TYPES = {"created": "A", "added": "A", "deleted": "D", "removed": "D",
                "modified": "M", "updated": "M", "reverted": "M"}


def diff_lines(before: str, after: str, name: str, context: int = CONTEXT) -> list[str]:
    """One file's unified diff, with `a/` and `b/` so a reader knows which side is which."""
    return list(difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(),
                                     fromfile="a/" + name, tofile="b/" + name,
                                     n=max(0, int(context)), lineterm=""))


def added(lines: Sequence[str]) -> list[str]:
    return [line[1:] for line in lines if line.startswith("+") and not line.startswith("+++")]


def removed(lines: Sequence[str]) -> list[str]:
    return [line[1:] for line in lines if line.startswith("-") and not line.startswith("---")]


def counts(lines: Sequence[str]) -> tuple[int, int]:
    return len(added(lines)), len(removed(lines))


def hunks(lines: Sequence[str]) -> int:
    return len([line for line in lines if line.startswith("@@")])


def kind_of(change: Mapping[str, Any]) -> str:
    """A/M/D read off the record itself: a delete first, then a file with no previous state.

    A recorded `change_type` is honoured as well, because that is all an event broadcast carries.
    """
    declared = str(change.get("change_type") or "").lower()
    if change.get("delete") or declared in ("deleted", "removed"):
        return "D"
    if change.get("before") is None or declared in ("created", "added"):
        return "A"
    return CHANGE_TYPES.get(declared, "M")


def describe(change: Mapping[str, Any], lines: Sequence[str]) -> str:
    """What this file's edit was, in one line the card can print.

    A recorded summary wins: it is the model's own sentence about the change, kept with the proposal.
    Without one the description is built from what is observable — the names that appeared or went
    away, or, if nothing so legible moved, how many lines.
    """
    summary = change.get("summary") or change.get("description")
    if summary:
        return " ".join(str(summary).split())[:SUMMARY_CHARS]
    body = "\n".join(added(lines) + removed(lines))
    names = list(dict.fromkeys(DEF_NAMES.findall(body)))[:SUMMARY_NAMES]
    if not names:
        names = list(dict.fromkeys(BINDING_NAMES.findall(body)))[:SUMMARY_NAMES]
    if not names and Path(str(change.get("path") or "")).suffix.lower() in MARKUP_SUFFIXES:
        names = list(dict.fromkeys(MARKUP_TAGS.findall(body)))[:SUMMARY_NAMES]
    action = ("Removed" if change.get("delete") else "Added" if change.get("before") is None
              else "Updated")
    text = (f"{action} {', '.join(names)}" if names else
            f"{action} file content: {len(added(lines))} lines added, "
            f"{len(removed(lines))} removed")
    return " ".join(str(text).split())[:SUMMARY_CHARS]


def review_files(changes: Sequence[Mapping[str, Any]], context: int = CONTEXT) -> list[dict]:
    """The card's file list: one row per changed file, with its kind and its line counts."""
    rows = []
    for change in changes:
        lines = diff_lines(change["before"] or "", change["after"] or "", change["path"], context)
        add, dele = counts(lines)
        rows.append({"path": change["path"], "summary": describe(change, lines),
                     "kind": kind_of(change), "add": add, "del": dele})
    return rows


def file_view(change: Optional[Mapping[str, Any]], context: int = CONTEXT) -> dict:
    """The three panes for one file: the diff, and each side on its own.

    A delete has no `after` at all, and the viewer shows the removal as the whole file going out with
    minus signs — which is only readable if the empty side is built here rather than assumed by the
    caller.
    """
    if not change:
        return {"diff": [], "before": [], "after": []}
    return {"diff": diff_lines(change["before"] or "", change["after"] or "",
                               change["path"], context),
            "before": (change["before"] or "").splitlines(),
            "after": (change["after"] or "").splitlines()}
