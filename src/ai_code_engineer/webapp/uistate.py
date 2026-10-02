"""The change set as the review card and the rail draw it.

This is the presentation half of a proposal: which lines changed, how many of them, and what one
honest sentence says about a file the model edited without describing its intent. The decisions —
whether Apply is allowed, whether the files are on disk, whether a refusal has to be reopened — stay
with the controller, because they read its state; every function here takes the bytes it was given.

`diff_lines` is the one painter both windows share. The scripted preview used to carry a second copy
of it, which is the kind of duplication that shows up as a diff rendering differently in the window
nobody reviews code in.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from .. import core
from ..labels import stage_label, stage_line

# Names a reader recognises, taken from the lines that actually changed. Three patterns because the
# languages the agent is pointed at differ in how they open a definition, not because each needs its
# own summary.
DEF_NAMES = re.compile(r"^\s*(?:(?:async\s+)?def|class|function)\s+([\w$]+)", re.MULTILINE)
BINDING_NAMES = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([\w$]+)\s*=", re.MULTILINE)
MARKUP_TAGS = re.compile(r"<([A-Za-z][\w-]*)\b")
MARKUP_SUFFIXES = {".html", ".htm", ".vue", ".jsx", ".tsx"}
SUMMARY_NAMES = 3
SUMMARY_CHARS = 160


def diff_lines(before: str, after: str, name: str) -> list[str]:
    """One file's unified diff, with `a/` and `b/` so a reader knows which side is which."""
    return list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                     fromfile="a/" + name, tofile="b/" + name, lineterm=""))


def added(lines: list[str]) -> list[str]:
    return [line[1:] for line in lines if line.startswith("+") and not line.startswith("+++")]


def removed(lines: list[str]) -> list[str]:
    return [line[1:] for line in lines if line.startswith("-") and not line.startswith("---")]


def describe(change: dict, lines: list[str]) -> str:
    """What this file's edit was, in one line the card can print.

    A recorded summary wins: it is the model's own sentence about the change, kept with the proposal.
    Without one the description is built from what is observable — the names that appeared or went
    away, or, if nothing so legible moved, how many lines. No guess at intent, and no second call to
    a model just to caption a file.
    """
    summary = change.get("summary") or change.get("description")
    if summary:
        return " ".join(str(summary).split())[:SUMMARY_CHARS]
    body = "\n".join(added(lines) + removed(lines))
    names = list(dict.fromkeys(DEF_NAMES.findall(body)))[:SUMMARY_NAMES]
    if not names:
        names = list(dict.fromkeys(BINDING_NAMES.findall(body)))[:SUMMARY_NAMES]
    if not names and Path(change["path"]).suffix.lower() in MARKUP_SUFFIXES:
        names = list(dict.fromkeys(MARKUP_TAGS.findall(body)))[:SUMMARY_NAMES]
    action = ("Removed" if change.get("delete") else "Added" if change["before"] is None
              else "Updated")
    text = (f"{action} {', '.join(names)}" if names else
            f"{action} file content: {len(added(lines))} lines added, "
            f"{len(removed(lines))} removed")
    return " ".join(str(text).split())[:SUMMARY_CHARS]


def review_files(changes: list[dict]) -> list[dict]:
    """The card's file list: one row per changed file, with its kind and its line counts."""
    rows = []
    for change in changes:
        lines = diff_lines(change["before"] or "", change["after"] or "", change["path"])
        rows.append({"path": change["path"], "summary": describe(change, lines),
                     "kind": ("D" if change.get("delete")
                              else "A" if change["before"] is None else "M"),
                     "add": len([line for line in lines
                                 if line.startswith("+") and not line.startswith("+++")]),
                     "del": len([line for line in lines
                                 if line.startswith("-") and not line.startswith("---")])})
    return rows


def file_view(change: dict | None) -> dict:
    """The three panes for one file: the diff, and each side on its own.

    A delete has no `after` at all, and the viewer shows the removal as the whole file going out with
    minus signs — which is only readable if the empty side is built here rather than assumed by the
    caller.
    """
    if not change:
        return {"diff": [], "before": [], "after": []}
    return {"diff": diff_lines(change["before"] or "", change["after"] or "", change["path"]),
            "before": (change["before"] or "").splitlines(),
            "after": (change["after"] or "").splitlines()}


def stage_block(current: str, *, arabic: bool = False) -> dict:
    """Where the run stands, as the strip draws it: the ordered stages, which ones it reached, one line.

    Both controllers build it here because the scripted preview is the window a design gets reviewed in,
    and a strip that exists in one of them and not the other is reviewed as nothing. The order comes from
    `core.STAGES`, so what a person sees is the lifecycle's own sequence rather than a copy of it that can
    fall behind.
    """
    codes = list(core.STAGES)
    where = codes.index(current) + 1 if current in codes else 0
    return {"current": current, "total": len(codes),
            "steps": [{"code": code, "label": stage_label(code, arabic=arabic),
                       "reached": bool(where) and codes.index(code) < where} for code in codes],
            "line": stage_line(current, arabic=arabic, stage_order=core.STAGES)}
