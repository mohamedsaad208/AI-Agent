"""Final report view for the CLI (Release 1 - Task 1.4).

The core stays silent and ends a run by broadcasting one structured ``final_report``
event; this module is the view that turns it — or a whole session record — into the
closing tables: a summary carrying a strict verification badge, the changed files with
that badge on each row, and the record of decisions and remaining risks.

The badge vocabulary is exactly three and the rule that picks one is empirical, the same
rule the verification loop keeps:

    VERIFIED      an executed check came back green with counted proof — tests were
                  really run, none failed or errored, exit code 0. An exit code alone
                  proves only that a command ran.
    INFERRED      something was observed (a run without a test count, a recorded
                  verification, a passing state the loop reached) but nothing counted
                  proves the change works.
    NOT CHECKED   no check ran at all.

Anything the record does not support collapses down, never up: an unknown or malformed
status string reads as NOT CHECKED, because a report may not claim more certainty than
the evidence holds. Colour is left to ``rich``, which already honours ``NO_COLOR`` and
terminal detection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import events as ux
from .cli_view import _ensure_utf8_stdout
from .events import Event, EventKind, FinalReport, VerificationStatus

# ---------------------------------------------------------------------------
# Badges - the three strict glyphs and their styles.
# ---------------------------------------------------------------------------

BADGE = {
    VerificationStatus.VERIFIED: ("\u2713 VERIFIED", "bold green"),       # ✓
    VerificationStatus.INFERRED: ("\u25d0 INFERRED", "bold yellow"),      # ◐
    VerificationStatus.NOT_CHECKED: ("\u25cb NOT CHECKED", "bold red"),   # ○
}


def as_status(value: Any) -> VerificationStatus:
    """The one badge a claim may wear; anything outside the vocabulary is NOT CHECKED."""
    text = " ".join(str(value or "").strip().upper().replace("_", " ").replace("-", " ").split())
    try:
        return VerificationStatus(text)
    except ValueError:
        return VerificationStatus.NOT_CHECKED


def badge(status: Any) -> Text:
    label, style = BADGE[as_status(status)]
    return Text(label, style=style)


# ---------------------------------------------------------------------------
# Evidence - reading the three badges off a session record.
# ---------------------------------------------------------------------------

ATTENTION_STATES = {"VERIFICATION_FAILED", "VERIFICATION_BLOCKED", "PARTIAL_APPLY"}


def _counted(run: Mapping[str, Any]) -> dict:
    """The test count a run produced, or {} when it produced none."""
    proof = run.get("proof")
    return proof if isinstance(proof, Mapping) else {}


def _green(run: Mapping[str, Any]) -> bool:
    """A run is proof only when it executed counted tests and all of them passed."""
    proof = _counted(run)
    if not proof or int(proof.get("tests") or 0) <= 0:
        return False
    if int(proof.get("failures") or 0) or int(proof.get("errors") or 0):
        return False
    exit_code = run.get("exit_code")
    status = str(run.get("status") or "").lower()
    return (exit_code == 0 or status in ("passed", "ok", "success"))


def _executed(run: Mapping[str, Any]) -> bool:
    return bool(run.get("status") or run.get("exit_code") is not None)


def status_from_session(session: Mapping[str, Any]) -> VerificationStatus:
    """The strictest honest badge the record supports: green proof, else some check, else none."""
    runs = [run for run in (session.get("runs") or []) if isinstance(run, Mapping)]
    executed = [run for run in runs if _executed(run)]
    if executed and all(_green(run) for run in executed):
        return VerificationStatus.VERIFIED
    checked = (bool(executed)
               or bool(session.get("verification"))
               or any(entry.get("kind") in ("verification", "run")
                      for entry in session.get("events") or [] if isinstance(entry, Mapping)))
    return VerificationStatus.INFERRED if checked else VerificationStatus.NOT_CHECKED


def _seconds(session: Mapping[str, Any]) -> float:
    """Wall time from the record's own stamps; 0.0 when either end is missing or unreadable."""
    def stamp(value: Any) -> Optional[datetime]:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        return parsed
    start = stamp(session.get("created"))
    ends = [stamp(entry.get("at")) for entry in session.get("events") or [] if isinstance(entry, Mapping)]
    ends = [one for one in ends if one is not None]
    if start is None or not ends:
        return 0.0
    return max(0.0, (max(ends) - start).total_seconds())


def _test_summary(session: Mapping[str, Any]) -> str:
    """What the executed runs counted, in one line; the record's silence stays said."""
    runs = [run for run in (session.get("runs") or []) if isinstance(run, Mapping) and _executed(run)]
    if not runs:
        return "no project command was run for this session"
    parts = []
    for run in runs:
        proof = _counted(run)
        label = str(run.get("label") or run.get("recipe") or "run")
        if proof.get("tests") is not None:
            parts.append("{}: {} tests, {} failed, {} errors".format(
                label, proof.get("tests"), proof.get("failures"), proof.get("errors")))
        else:
            parts.append("{}: exit {}, no test count produced".format(
                label, run.get("exit_code")))
    return "; ".join(parts)


def files_from_session(session: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(path, change_type) per file event, in recorded order, latest state per path winning."""
    created = {change.get("path") for change in session.get("changes") or []
               if isinstance(change, Mapping) and not change.get("before")}
    rows: dict[str, str] = {}
    for entry in session.get("events") or []:
        if not isinstance(entry, Mapping):
            continue
        kind = str(entry.get("kind") or "")
        path = str(entry.get("path") or "")
        if not path:
            continue
        if kind == "written":
            rows[path] = "created" if path in created else "modified"
        elif kind == "removed":
            rows[path] = "deleted"
        elif kind == "rolled_back_file":
            rows[path] = "reverted"
    return list(rows.items())


def decisions_from_session(session: Mapping[str, Any]) -> list[tuple[str, str]]:
    """What the operator and the tool decided along the way: (outcome, detail)."""
    labels = {"proposal": "proposal presented", "approved": "proposal approved",
              "proposal_rejected": "proposal declined", "proposal_reopened": "proposal reopened",
              "rejected_action": "action refused", "stopped": "task stopped"}
    rows = []
    for entry in session.get("events") or []:
        if not isinstance(entry, Mapping):
            continue
        kind = str(entry.get("kind") or "")
        if kind not in labels:
            continue
        detail = str(entry.get("reason") or entry.get("summary") or entry.get("hash")
                     or entry.get("action") or "")
        rows.append((labels[kind], detail))
    return rows


def report_from_session(session: Mapping[str, Any]) -> FinalReport:
    status = status_from_session(session)
    state = str(session.get("state") or "")
    files = [path for path, _ in files_from_session(session)]
    verified = status is VerificationStatus.VERIFIED and state not in ATTENTION_STATES
    unneeded = state == "COMPLETED" and not files
    return FinalReport(
        task=str(session.get("task") or ""),
        success=verified or unneeded,
        duration_seconds=_seconds(session),
        files_changed=tuple(files),
        verification_status=status.value,
        test_summary=_test_summary(session),
        remaining_risks=tuple(_clean_risk(risk) for risk in session.get("risks") or [])
                       if isinstance(session.get("risks"), Sequence) else (),
    )


# ---------------------------------------------------------------------------
# Coercion - any of the shapes a report arrives in.
# ---------------------------------------------------------------------------

def _report_from_data(data: Mapping[str, Any], at: str = "") -> FinalReport:
    risks = data.get("remaining_risks") or []
    return FinalReport(
        task=str(data.get("task") or ""),
        success=bool(data.get("success")),
        duration_seconds=float(data.get("duration_seconds") or 0.0),
        files_changed=tuple(str(path) for path in (data.get("files_changed") or ())),
        verification_status=as_status(data.get("verification_status")).value,
        test_summary=str(data.get("test_summary") or ""),
        remaining_risks=tuple(_clean_risk(risk) for risk in risks if isinstance(risk, (Mapping, str))),
        at=at or ux.timestamp(),
    )


def _clean_risk(risk: Any) -> dict:
    """One remaining risk as {description, severity}, from the shapes callers use."""
    if isinstance(risk, str):
        return {"description": risk, "severity": ""}
    body = dict(risk)
    description = str(body.get("description") or body.get("risk") or body.get("text")
                      or body.get("what") or "")
    severity = str(body.get("severity") or body.get("level") or body.get("risk_level") or "")
    return {"description": description, "severity": severity}


def _is_session(value: Any) -> bool:
    """A session record, not a final_report wire dict that merely shares the ``task`` key."""
    return (isinstance(value, Mapping)
            and str(value.get("kind") or "") != EventKind.FINAL_REPORT.value
            and any(key in value for key in ("events", "state", "runs")))


def to_report(value: Any) -> FinalReport:
    """A FinalReport from the dataclass, the wire event, an Event, or a session record."""
    if isinstance(value, FinalReport):
        return value
    if isinstance(value, Event):
        return _report_from_data(value.data, at=value.at)
    if isinstance(value, Mapping):
        if str(value.get("kind") or "") == EventKind.FINAL_REPORT.value:
            return _report_from_data(value, at=str(value.get("at") or ""))
        if _is_session(value):
            return report_from_session(value)
        return _report_from_data(value)
    raise TypeError("no final report in " + type(value).__name__)


# ---------------------------------------------------------------------------
# The tables.
# ---------------------------------------------------------------------------

@dataclass
class ReportData:
    """Everything the closing view prints, resolved from one source."""

    report: FinalReport
    files: list[tuple[str, str]] = field(default_factory=list)
    decisions: list[tuple[str, str]] = field(default_factory=list)
    risks: list[dict] = field(default_factory=list)

    @property
    def status(self) -> VerificationStatus:
        return as_status(self.report.verification_status)


def _is_session(value: Any) -> bool:
    """A session record, not a final_report wire dict that merely shares the ``task`` key."""
    return (isinstance(value, Mapping)
            and str(value.get("kind") or "") != EventKind.FINAL_REPORT.value
            and any(key in value for key in ("events", "state", "runs")))


def collect(source: Any) -> ReportData:
    """Resolve a report plus its per-file and decision detail from any accepted shape."""
    report = to_report(source)
    if _is_session(source):
        return ReportData(report=report, files=files_from_session(source),
                          decisions=decisions_from_session(source),
                          risks=[_clean_risk(risk) for risk in report.remaining_risks])
    return ReportData(report=report,
                      files=[(path, "changed") for path in report.files_changed],
                      risks=[_clean_risk(risk) for risk in report.remaining_risks])


def summary_table(data: ReportData) -> Table:
    report = data.report
    table = Table(title="Final Report", show_header=False, title_style="bold")
    table.add_column("Field", style="bold", no_wrap=True)
    table.add_column("Value", overflow="fold")
    result = Text("\u2713 SUCCESS", style="bold green") if report.success else \
        Text("\u2717 FAILED", style="bold red")
    duration = (f"{report.duration_seconds:.1f}s" if report.duration_seconds
                else "not recorded")
    table.add_row("Task", report.task or "\u2014")
    table.add_row("Result", result)
    table.add_row("Duration", duration)
    table.add_row("Files changed", str(len(report.files_changed)))
    table.add_row("Tests", report.test_summary or Text("no test summary was recorded", style="dim"))
    table.add_row("Verification", badge(report.verification_status))
    return table


def files_table(data: ReportData) -> Table:
    table = Table(title="Changed Files", title_style="bold")
    table.add_column("File", overflow="fold")
    table.add_column("Change")
    table.add_column("Verification")
    if not data.files:
        table.add_row(Text("No files were changed.", style="dim"), "", "")
    for path, change_type in data.files:
        table.add_row(path, change_type, badge(data.report.verification_status))
    return table


def decisions_table(data: ReportData) -> Table:
    """The one summary of decisions and remaining risks the specification asks for."""
    table = Table(title="Decisions & Remaining Risks", title_style="bold")
    table.add_column("Type", no_wrap=True)
    table.add_column("Item", overflow="fold")
    table.add_column("Detail / severity", overflow="fold")
    for outcome, detail in data.decisions:
        tone = "green" if "approved" in outcome else (
            "red" if "refused" in outcome or "declined" in outcome or "stopped" in outcome else "yellow")
        table.add_row(Text("decision", style="dim"),
                      Text(outcome, style=tone), detail or "\u2014")
    for risk in data.risks:
        description = str(risk.get("description") or "")
        severity = str(risk.get("severity") or "")
        style = {"critical": "bold red", "high": "red", "medium": "yellow"}.get(severity.lower(), "dim")
        table.add_row(Text("risk", style="dim"),
                      Text(description or "unnamed risk", style=style), severity or "\u2014")
    if not data.decisions and not data.risks:
        table.add_row(Text("record", style="dim"),
                      Text("No decisions or remaining risks were recorded.", style="dim"), "")
    return table


def tables(source: Any) -> list[Table]:
    """The three closing tables for any accepted report source, without printing."""
    data = collect(source)
    return [summary_table(data), files_table(data), decisions_table(data)]


def render(source: Any, console: Optional[Console] = None) -> ReportData:
    """Print the final report tables and hand back what they were built from."""
    data = collect(source)
    if console is None:
        _ensure_utf8_stdout()   # the badge glyphs need it, as they did for the status line
    out = console or Console()
    out.print(summary_table(data))
    out.print(files_table(data))
    out.print(decisions_table(data))
    return data
