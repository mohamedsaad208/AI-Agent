"""CLI renderer for the unified UX event stream (Release 1 - Task 1.3).

The core stays silent and broadcasts typed events; this module is one of the views that
subscribes to that stream. It turns the 8 structured engineering events into labelled
terminal lines (never chat prose) driven by a small, fixed symbol vocabulary:

    ✓ done / passed        ✗ failed / fatal        ● running / active
    ○ pending / queued      ! needs attention (warning, approval, deletion)

A persistent status line at the top shows the current stage and a compact step
checklist (done/running/pending) that updates in place through ``rich.live.Live``. When
the console is not an interactive terminal (a pipe, CI) the same lines are simply
printed, so no spinner or in-place redraw is attempted where it cannot work. Colour is
left to ``rich``, which already honours ``NO_COLOR`` and terminal detection.
"""
from __future__ import annotations

import os
import sys
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Mapping, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from . import events as ux
from . import terminal
from .events import Event, EventKind, EventLevel


# ---------------------------------------------------------------------------
# Symbol vocabulary - the five glyphs the specification fixes for the CLI.
# ---------------------------------------------------------------------------

DONE = ("\u2713", "green")    # ✓
FAIL = ("\u2717", "red")      # ✗
RUN = ("\u25cf", "cyan")      # ●
PEND = ("\u25cb", "dim")      # ○
WARN = ("!", "yellow")        # !

_WEIGHT = {EventLevel.NORMAL: 1, EventLevel.VERBOSE: 2, EventLevel.DEBUG: 3}

# Which short engineering tag each structured kind is rendered under.
_TAG = {
    EventKind.STAGE_CHANGED.value: "STAGE",
    EventKind.STEP_UPDATED.value: "STEP",
    EventKind.TOOL_CALL.value: "TOOL",
    EventKind.FILE_CHANGED.value: "CHANGE",
    EventKind.TEST_RESULT.value: "TEST",
    EventKind.APPROVAL_REQUESTED.value: "ASK",
    EventKind.ERROR.value: "WARN",
    EventKind.FINAL_REPORT.value: "DONE",
}

_STEP_SYMBOL = {"done": DONE, "passed": DONE, "ok": DONE, "success": DONE,
                "running": RUN, "started": RUN, "failed": FAIL, "error": FAIL,
                "pending": PEND, "queued": PEND}

_CHANGE_SYMBOL = {"created": DONE, "modified": DONE, "deleted": WARN, "reverted": WARN}


def _level_of(event: Any) -> EventLevel:
    """The level a broadcast item carries, whether it is a typed dataclass or an Event."""
    raw = getattr(event, "level", None)
    if raw is None and isinstance(event, Mapping):
        raw = event.get("level")
    if isinstance(raw, EventLevel):
        return raw
    try:
        return EventLevel(str(raw))
    except (ValueError, TypeError):
        return EventLevel.NORMAL


def _status_symbol(status: str) -> tuple[str, str]:
    return _STEP_SYMBOL.get(str(status).lower(), RUN)


class CliView:
    """A live terminal view over one event bus subscription.

    ``handle`` is the bus callback; it filters by level, appends a rendered engineering
    line, folds step/stage state into the persistent status block, and refreshes in
    place when a ``Live`` is attached (or streams the line when it is not).
    """

    def __init__(self, console: Optional[Console] = None,
                 level: EventLevel = EventLevel.NORMAL, *, live: Optional[bool] = None,
                 max_lines: int = 60) -> None:
        self.console = console or Console()
        self.level = level if isinstance(level, EventLevel) else _level_of(
            Event(kind="x", level=level))
        # ``rich`` may see a CI pty as a terminal; the capability answer says whether an
        # in-place redraw is allowed to paint one (Release 5 - Task 5.2).
        self.live = (self.console.is_terminal
                     and terminal.capabilities().spinners) if live is None else live
        self.max_lines = max_lines
        self.stage: str = ""
        self.activity: str = ""
        self.finished: bool = False
        self.steps: "OrderedDict[str, str]" = OrderedDict()   # step_id -> "status action"
        self.lines: list[Text] = []
        self._live_ref: Optional[Live] = None

    # -- subscription entry point ------------------------------------------------
    def handle(self, event: Any) -> None:
        if self.finished:
            return   # the run has reported; ignore the core's trailing bookkeeping
        if _WEIGHT[_level_of(event)] > _WEIGHT[self.level]:
            return
        rendered = self._apply(ux.coerce(event))
        if rendered is None:
            return
        self._commit(rendered)

    def note(self, text: str) -> None:
        """Route the core's free-text ``progress`` sink into the status line (never noise)."""
        self.activity = str(text or "")
        if self._live_ref is not None:
            self._live_ref.update(self.render(), refresh=True)
        elif not self.live and self.activity:
            self.console.print(Text("  " + self.activity, style="dim"))

    # -- state + line construction ------------------------------------------------
    def _apply(self, ev: Event) -> Optional[Text]:
        kind, d = ev.kind, ev.data
        if kind == EventKind.STAGE_CHANGED.value:
            return self._on_stage(d)
        if kind == EventKind.STEP_UPDATED.value:
            return self._on_step(ev, d)
        if kind == EventKind.TOOL_CALL.value:
            return self._on_tool(d)
        if kind == EventKind.FILE_CHANGED.value:
            return self._on_change(d)
        if kind == EventKind.TEST_RESULT.value:
            return self._on_test(d)
        if kind == EventKind.APPROVAL_REQUESTED.value:
            return self._on_approval(d)
        if kind == EventKind.ERROR.value:
            return self._on_error(d)
        if kind == EventKind.FINAL_REPORT.value:
            return self._on_report(d)
        return self._on_generic(ev, d)

    def _on_stage(self, d: Mapping[str, Any]) -> Text:
        self.stage = str(d.get("stage") or self.stage)
        sym = DONE if self.stage == ux.Stage.COMPLETED.value else (
            FAIL if self.stage == ux.Stage.FAILED.value else RUN)
        return self._line(sym, "STAGE", self._stage_text(d))

    @staticmethod
    def _stage_text(d: Mapping[str, Any]) -> str:
        previous, stage = str(d.get("previous_stage") or ""), str(d.get("stage") or "")
        return f"{previous} \u2192 {stage}" if previous else stage

    def _on_step(self, ev: Event, d: Mapping[str, Any]) -> Text:
        step_id = str(ev.id or d.get("step_id") or "")
        action = str(d.get("action") or "")
        status = str(d.get("status") or "running")
        self.steps[step_id or action] = f"{status} {action}"
        detail = action
        path = (d.get("details") or {}).get("path") if isinstance(d.get("details"), Mapping) else None
        if path:
            detail = f"{action} \u2192 {path}"
        return self._line(_status_symbol(status), "STEP", detail)

    def _on_tool(self, d: Mapping[str, Any]) -> Text:
        name = str(d.get("tool_name") or "")
        status = str(d.get("status") or "ok")
        ms = d.get("duration_ms") or 0
        tail = f"  ({float(ms):.0f}ms)" if ms else ""
        return self._line(_status_symbol("failed" if status not in ("ok", "success", "") else "ok"),
                          "TOOL", name + tail)

    def _on_change(self, d: Mapping[str, Any]) -> Text:
        change_type = str(d.get("change_type") or "modified")
        added, removed = int(d.get("lines_added") or 0), int(d.get("lines_removed") or 0)
        counts = f"  +{added} -{removed}" if (added or removed) else ""
        return self._line(_CHANGE_SYMBOL.get(change_type, DONE), "CHANGE",
                          f"{change_type} {d.get('path') or ''}".strip() + counts)

    def _on_test(self, d: Mapping[str, Any]) -> Text:
        passed = bool(d.get("passed"))
        total, ok = int(d.get("total_tests") or 0), int(d.get("passed_tests") or 0)
        score = f"  {ok}/{total} passed" if total else ""
        command = str(d.get("command") or "").strip()
        return self._line(DONE if passed else FAIL, "TEST", (command + score).strip()
                          or ("passed" if passed else "failed"))

    def _on_approval(self, d: Mapping[str, Any]) -> Text:
        risk = str(d.get("risk_level") or "medium")
        action = str(d.get("action") or "apply change")
        return self._line(WARN, "ASK", f"{action}  [risk: {risk}]  y/n/d")

    def _on_error(self, d: Mapping[str, Any]) -> Text:
        recoverable = bool(d.get("recoverable", True))
        message = str(d.get("message") or d.get("error_type") or "error")
        return self._line(WARN if recoverable else FAIL, "WARN", message)

    def _on_report(self, d: Mapping[str, Any]) -> Text:
        success = bool(d.get("success"))
        self.stage = ux.Stage.COMPLETED.value if success else ux.Stage.FAILED.value
        self.steps.clear()
        self.finished = True
        self.last_report = d
        verification = str(d.get("verification_status") or ux.VerificationStatus.NOT_CHECKED.value)
        files = len(d.get("files_changed") or [])
        return self._line(DONE if success else FAIL, "DONE",
                          f"{verification}  files:{files}")

    def _on_generic(self, ev: Event, d: Mapping[str, Any]) -> Optional[Text]:
        message = str(d.get("message") or d.get("detail") or d.get("summary") or d.get("path") or "")
        if not message:
            return None
        return self._line(RUN, ev.kind.upper()[:6], message)

    @staticmethod
    def _line(symbol: tuple[str, str], tag: str, detail: str) -> Text:
        text = Text()
        text.append(symbol[0] + " ", style=symbol[1])
        text.append(f"[{tag}] ", style="bold")
        text.append(detail)
        return text

    # -- rendering ---------------------------------------------------------------
    def status(self) -> Text:
        """The persistent top block: current stage, step checklist, activity hint."""
        block = Text()
        stage_sym = DONE if self.stage == ux.Stage.COMPLETED.value else (
            FAIL if self.stage == ux.Stage.FAILED.value else (RUN if self.stage else PEND))
        block.append(stage_sym[0] + " ", style=stage_sym[1])
        block.append(self.stage or "IDLE", style="bold")
        if self.steps:
            block.append("\n  ")
            joined = Text()
            for index, entry in enumerate(self.steps.values()):
                status, _, action = entry.partition(" ")
                sym = _status_symbol(status)
                if index:
                    joined.append("; ")
                joined.append(Text.assemble((sym[0] + " ", sym[1]), action))
            block.append(joined)
        if self.activity:
            block.append("\n  ")
            block.append(Text(self.activity, style="dim"))
        return block

    def render(self) -> Group:
        return Group(self.status(), *self.lines[-self.max_lines:])

    def _commit(self, line: Text) -> None:
        self.lines.append(line)
        if self._live_ref is not None:
            self._live_ref.update(self.render(), refresh=True)
        elif not self.live:
            self.console.print(line)


# ---------------------------------------------------------------------------
# Startup banner (Release 5 - Task 5.3)
# ---------------------------------------------------------------------------

PROJECT_NAME = "AI Code Engineer"

# The mode vocabulary both windows share: PLAN reads and proposes, EXECUTE writes.
MODE_PLAN = "PLAN"
MODE_EXECUTE = "EXECUTE"

_MODE_GLYPH = {MODE_PLAN: (PEND, "plans and proposes; nothing is written"),
               MODE_EXECUTE: (RUN, "will write into the project once approved")}


def banner_renderable(project: str, model: str, mode: str,
                      caps: Optional[terminal.Capabilities] = None) -> Text:
    """The banner as one renderable, sized for the screen it is about to be printed on."""
    caps = caps or terminal.capabilities()
    mode = str(mode or MODE_EXECUTE).strip().upper()
    if mode not in _MODE_GLYPH:
        mode = MODE_EXECUTE
    glyph, meaning = _MODE_GLYPH[mode]
    if caps.narrow:
        # One honest line beats a panel that wraps into a ribbon of broken borders.
        line = Text()
        line.append(PROJECT_NAME, style="bold")
        line.append(f"  \u00b7  {project or '?'}  \u00b7  {model or '?'}  \u00b7  ")
        line.append(mode, style="bold")
        line.append(f" {glyph[0]}", style=glyph[1])
        return line
    block = Text()
    block.append(f"{PROJECT_NAME}\n", style="bold")
    block.append(Text.assemble(("  project  ", "dim"), (project or "?", "bold"), "\n",
                               ("  model    ", "dim"), (model or "?", "bold"), "\n",
                               ("  mode     ", "dim"), (mode, "bold"),
                               (f" {glyph[0]}", glyph[1]),
                               ("  " + meaning, "dim")))
    return block


def startup_banner(project: str = "", model: str = "", mode: str = MODE_EXECUTE, *,
                   console: Optional[Console] = None,
                   caps: Optional[terminal.Capabilities] = None,
                   env=None) -> None:
    """Print the launch banner: who you started, what answers, and in what position."""
    _ensure_utf8_stdout()
    out = console or Console()
    caps = caps or terminal.capabilities(env=env)
    if not caps.color and not out.no_color:
        out.no_color = True   # the operator asked for no colour; rich would not have heard
    out.print(banner_renderable(project, model, mode, caps))


# ---------------------------------------------------------------------------
# Wiring helper
# ---------------------------------------------------------------------------

def _default_level() -> EventLevel:
    verbosity = (os.environ.get("AGENT_VERBOSITY") or "").strip().lower()
    if verbosity in ("verbose", "debug"):
        return EventLevel(verbosity)
    return EventLevel.NORMAL


def _ensure_utf8_stdout() -> None:
    """Give the stream an encoding that can carry ✓ ✗ ● ○ at all.

    A cp1252 console cannot encode the status symbols, and the whole view is built out
    of them; a half line that survives beats a traceback where the run should show.
    """
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") not in ("utf8", "utf"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass


@contextmanager
def event_view(bus: ux.EventBus, console: Optional[Console] = None,
               level: Optional[EventLevel] = None):
    """Subscribe a :class:`CliView` to ``bus`` for the duration of the block.

    In a terminal the view runs under ``Live`` and redraws the status line in place;
    anywhere else it simply streams the rendered lines. The level defaults to ``NORMAL``
    unless ``AGENT_VERBOSITY`` raises it, matching what the bus itself would dispatch.
    """
    _ensure_utf8_stdout()
    view = CliView(console=console, level=level or _default_level())
    bus.subscribe(view.handle, min_level=view.level)
    if not view.live:
        try:
            yield view
        finally:
            _unsubscribe(bus, view)
            if getattr(view, "last_report", None):
                try:
                    from . import report_view
                    report_view.render(view.last_report, console=view.console)
                except Exception:
                    pass
        return
    with Live(view.status(), console=view.console, refresh_per_second=12,
              vertical_overflow="visible") as live_ref:
        view._live_ref = live_ref
        try:
            yield view
        finally:
            _unsubscribe(bus, view)
            live_ref.update(view.render(), refresh=True)
            if getattr(view, "last_report", None):
                try:
                    from . import report_view
                    report_view.render(view.last_report, console=view.console)
                except Exception:
                    pass


def _unsubscribe(bus: ux.EventBus, view: CliView) -> None:
    handle = view.handle
    with bus._lock:
        bus._subscribers = [pair for pair in bus._subscribers if pair[0] != handle]
