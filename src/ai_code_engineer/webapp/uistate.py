"""The change set as the review card and the rail draw it.

This is the presentation half of a proposal: which lines changed, how many of them, and what one
honest sentence says about a file the model edited without describing its intent. The decisions —
whether Apply is allowed, whether the files are on disk, whether a refusal has to be reopened — stay
with the controller, because they read its state; every function here takes the bytes it was given.

The painter itself is `diff_parse`'s, imported rather than kept beside it. The scripted preview used to
carry a second copy, which is the kind of duplication that shows up as a diff rendering differently in
the window nobody reviews code in — and a CLI drawing it a third way would show the same proposal with
different hunks in each surface. `diff_view` is that CLI, and the reason the painter holds no colour:
the web server reads it too, and the promises keep `rich` out of anything the application must run on.
"""
from __future__ import annotations

from .. import core
from ..diff_parse import diff_lines, file_view, review_files
from ..labels import stage_label, stage_line


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
