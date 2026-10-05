"""Colored diff view for the CLI (Release 4 - Task 4.1).

A proposal is only reviewable when a person can see *what the bytes change*: one honest row per file
saying what happened to it, then the hunks themselves with three lines of context, so each change
reads in place instead of as a puzzle. This module is the view that draws that — from a session
record, a bare list of changes, or the ``file_changed`` event the core broadcasts.

It draws; :mod:`diff_parse` decides. Which lines are in a diff, how many moved, which badge a file
wears and what its caption says are answered once, in the painter, and the web rail and this terminal
both read that answer — a proposal showing different hunks in each surface is how a person stops
trusting a diff.

Colour is left to ``rich``: the diff body goes through ``Syntax`` with pygments' ``diff`` lexer, so
``NO_COLOR``, a piped stdout and a narrow terminal are handled by the library rather than by a
hand-written check here. One thing is added on top of the library's work, because diff content is
repository and model text: a control character is replaced before it reaches the terminal, the rule
``cli.safe_print`` already keeps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from rich.console import Console, Group
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .cli_view import _ensure_utf8_stdout
from .diff_parse import (CHANGE_TYPES, CONTEXT, SUMMARY_CHARS, counts, describe,
                         diff_lines, hunks, kind_of)
from .events import Event, EventKind, FileChanged

THEME = "monokai"

# The rail's own badge letters, kept as the CLI column too so a file labelled `D` in one window is
# not labelled `removed` in the other.
KIND = {"A": ("A", "green"), "D": ("D", "red"), "M": ("M", "yellow")}


# ---------------------------------------------------------------------------
# The view model.
# ---------------------------------------------------------------------------

def _visible(line: str) -> str:
    """One diff line with anything that could drive the terminal itself replaced."""
    return "".join(ch if ch in "\t" or 32 <= ord(ch) and not 127 <= ord(ch) <= 159 else "?"
                   for ch in line)


@dataclass(frozen=True)
class FileDiff:
    """One file in the change set, already painted: its badge, its caption and its hunks."""

    path: str
    kind: str = "M"
    summary: str = ""
    lines: tuple[str, ...] = ()
    added: int = 0
    removed: int = 0
    hunks: int = 0

    @property
    def symbol(self) -> tuple[str, str]:
        return KIND.get(self.kind, KIND["M"])

    def body(self) -> str:
        return "\n".join(self.lines)


@dataclass
class DiffData:
    """Everything the diff view prints, resolved from one source."""

    files: list[FileDiff] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(entry.added for entry in self.files)

    @property
    def removed(self) -> int:
        return sum(entry.removed for entry in self.files)

    def picking(self, path: str = "") -> "DiffData":
        """The same view narrowed to one file, or itself when nothing was named."""
        if not path:
            return self
        return DiffData([entry for entry in self.files if entry.path == path])


def _from_change(change: Mapping[str, Any], context: int) -> FileDiff:
    lines = diff_lines(change.get("before") or "", change.get("after") or "",
                       change["path"], context)
    add, dele = counts(lines)
    return FileDiff(path=str(change["path"]), kind=kind_of(change), summary=describe(change, lines),
                    lines=tuple(_visible(line) for line in lines), added=add, removed=dele,
                    hunks=hunks(lines))


def _from_event(data: Mapping[str, Any]) -> FileDiff:
    """A `file_changed` broadcast, which carries counts and a summary rather than both sides.

    Its `diff_summary` is either a sentence or, when the core had hunks to hand, the hunks
    themselves; the view tells the two apart instead of printing a diff as a caption.
    """
    path = str(data.get("path") or "")
    kind = CHANGE_TYPES.get(str(data.get("change_type") or "").lower(), "M")
    text = str(data.get("diff_summary") or data.get("diff") or "")
    body = tuple(_visible(line) for line in text.splitlines())
    if any(line.startswith("@@") for line in body):
        add, dele = counts(body)
        return FileDiff(path=path, kind=kind, hunks=hunks(body), lines=body,
                        added=int(data.get("lines_added") or add),
                        removed=int(data.get("lines_removed") or dele))
    return FileDiff(path=path, kind=kind, summary=" ".join(text.split())[:SUMMARY_CHARS],
                    added=int(data.get("lines_added") or 0),
                    removed=int(data.get("lines_removed") or 0))


def _rows(value: Any) -> list[Mapping[str, Any]]:
    return [row for row in value if isinstance(row, Mapping)]


def to_diff(source: Any, *, context: int = CONTEXT) -> DiffData:
    """A diff view from a session record, a change list, one file's change, or the event."""
    if isinstance(source, DiffData):
        return source
    if isinstance(source, FileChanged):
        source = source.to_event()
    if isinstance(source, Event):
        if source.kind == EventKind.FILE_CHANGED.value:
            return DiffData([_from_event(source.data)])
        raise TypeError("no change set to diff in a " + source.kind + " event")
    if isinstance(source, Mapping):
        if str(source.get("kind") or "") == EventKind.FILE_CHANGED.value:
            return DiffData([_from_event(source)])
        if "path" in source and ("before" in source or "after" in source):
            return DiffData([_from_change(source, context)])   # one file's change on its own
        if "changes" in source:
            return DiffData([_from_change(row, context)
                             for row in _rows(source.get("changes") or [])])
        if any(key in source for key in ("state", "task", "events")):
            return DiffData()   # a session that recorded no change set changed no file
        raise TypeError("no change set to diff in " + type(source).__name__)
    # A bare path is not a change set, so a str is refused rather than diffed as nothing.
    if isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
        return DiffData([_from_change(row, context) for row in _rows(source)])
    raise TypeError("no change set to diff in " + type(source).__name__)


def collect(source: Any, *, context: int = CONTEXT, path: str = "") -> DiffData:
    """Resolve the view's files from any accepted shape, narrowed to ``path`` when named."""
    return to_diff(source, context=context).picking(path)


# ---------------------------------------------------------------------------
# The renderables.
# ---------------------------------------------------------------------------

def totals(data: DiffData) -> Text:
    """The one line a reader checks first: how many files, how many lines each way."""
    text = Text()
    text.append(f"{len(data.files)} file{'' if len(data.files) == 1 else 's'}  ", style="bold")
    text.append(f"+{data.added}", style="green")
    text.append("  ")
    text.append(f"-{data.removed}", style="red")
    return text


def summary_table(data: DiffData) -> Table:
    table = Table(title="Changes", title_style="bold", expand=False)
    table.add_column("File", overflow="fold")
    table.add_column("Change", no_wrap=True)
    table.add_column("Lines", no_wrap=True)
    table.add_column("What changed", overflow="fold")
    if not data.files:
        table.add_row(Text("No files were changed.", style="dim"), "", "", "")
        return table
    for entry in data.files:
        symbol, style = entry.symbol
        lines = Text.assemble((f"+{entry.added}", "green"), "  ", (f"-{entry.removed}", "red"))
        table.add_row(entry.path, Text(f"{symbol} {entry.hunks}h", style=style), lines,
                      entry.summary or Text("\u2014", style="dim"))
    return table


def diff_block(entry: FileDiff) -> Group:
    """One file's hunks, coloured, under its own header."""
    symbol, style = entry.symbol
    header = Text.assemble((symbol + " ", style), (entry.path, "bold"),
                           ("  " + f"+{entry.added} -{entry.removed}", "dim"))
    if not entry.lines:
        return Group(header, Text("    nothing to show: the two sides are identical", style="dim"))
    body = Syntax(entry.body(), "diff", theme=THEME, word_wrap=True,
                  background_color="default")   # inherit the terminal's own ground
    return Group(header, body)


def renderables(data: DiffData, *, with_summary: bool = True) -> list:
    """The blocks the view prints, in order, without printing them."""
    if not with_summary:
        return [diff_block(entry) for entry in data.files]
    return [totals(data), summary_table(data)] + [diff_block(entry) for entry in data.files]


def render(source: Any, console: Optional[Console] = None, *, path: str = "",
           context: int = CONTEXT, with_summary: bool = True) -> DiffData:
    """Print the per-file summary and the coloured diffs; hand back what they were built from."""
    data = collect(source, context=context, path=path)
    if console is None:
        _ensure_utf8_stdout()   # the header badges need it, as they did for the status line
    out = console or Console()
    if path and not data.files:
        # Said plainly rather than as "no files were changed": the change set has files, just not
        # the one that was asked for, and the second reading is the one that sends a person hunting.
        out.print(Text(f"Nothing in this change set touches {path}.", style="yellow"))
        return data
    for block in renderables(data, with_summary=with_summary):
        out.print(block)
    return data
