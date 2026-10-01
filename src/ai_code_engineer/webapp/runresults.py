"""What a finished command looks like to the person reading the window.

Three builders, no state. They take the values the controller already holds and return strings and
dicts, so the whole responsibility of "reporting an execution result" is readable in one file while
every write — ``status``, ``run_info``, ``_last_job``, the message list — stays in the controller,
which is where the lock that guards them lives.

Nothing here imports the controller, so no cycle is possible between the two.
"""
from __future__ import annotations

from .. import repair
from ..redaction import redact


def result_row(result: dict, summary: str) -> str:
    """The Checks row one finished command earns.

    A tool row is plain text — the thread escapes it and never runs markdown — and the pill collapses
    newlines, so this has to read as one sentence with no markup.

    On a failure the row is built from the raw runner result rather than the slimmed record the
    session stores, and ``runner`` does not scrub: the two other paths that show command output redact
    it (``repair.record_run`` for storage and the model, ``_build_line`` for the stream). A test that
    prints its own connection string must not find the chat to do it in — every row here can be copied
    out with one click.
    """
    if result["status"] == "passed":
        return "✅ " + summary
    msg = "❌ " + summary + " — command: " + redact(str(result.get("command", "")))
    failures = result.get("failures") or []
    if failures:
        msg += (" · Errors: " + " | ".join(redact(str(f).strip())
                                           for f in failures[:repair.FAILURES_KEPT]))
    elif result.get("reason"):
        msg += " · Reason: " + redact(str(result["reason"]))
    elif result.get("tail"):
        err_lines = [l.strip() for l in result["tail"].strip().splitlines()
                     if "[ERROR]" in l or "Error" in l or "Exception" in l]
        sample = err_lines[-6:] or [l.strip() for l in result["tail"].strip().splitlines()][-6:]
        if sample:
            msg += " · Output: " + " | ".join(redact(line) for line in sample)
    msg += " · The full output is in Activity."
    return msg


def gate(*, has_project: bool, busy: bool, waiting_approval: bool, has_recipes: bool,
         sandbox_requested: bool, docker_available: bool) -> dict:
    """Whether each Run control is pressable, and the reason printed when it is not.

    The caller answers the four questions about itself; this decides what they mean together. A
    reason is a card the window draws, not a raised error — a greyed button has to say what would
    unlock it, in the order the operator can act on.
    """
    reasons = []
    if not has_project:
        reasons.append({
            "code": "no_project",
            "title": "No project selected",
            "action": "Select or open a project folder in the sidebar."
        })
    if busy:
        reasons.append({
            "code": "agent_busy",
            "title": "Agent is busy",
            "action": "Wait for the current operation to complete or press Stop."
        })
    if waiting_approval:
        reasons.append({
            "code": "waiting_approval",
            "title": "Changes waiting for approval",
            "action": "Review the pending changes and click Apply or Rollback before running."
        })

    can_run_general = has_project and not busy and not waiting_approval
    return {
        "canRun": can_run_general,
        "canRunApp": can_run_general,
        "canRunTests": can_run_general and has_recipes,
        "canBuild": can_run_general,
        "reasons": reasons,
        "disabledMessage": (reasons[0]["title"] + ": " + reasons[0]["action"]) if reasons else "",
        "dockerAvailable": docker_available,
        "dockerOn": bool(sandbox_requested and docker_available),
        "dockerNote": "Docker is optional. Local execution runs directly using your system tools.",
    }


def can_run(*, has_project: bool, busy: bool, waiting_approval: bool, has_recipes: bool) -> bool:
    """The one question `gate` answers for the Tests control, asked on its own."""
    return has_project and not busy and not waiting_approval and has_recipes


def checks_lines(session: dict) -> list[str]:
    """The Checks card of the review block: proposals are not proof, and this says which is which.

    The header line exists because a model can *propose* checks it never ran; the run section below
    it only ever prints what a command actually answered.
    """
    out = ["Proposed checks (not execution results):"] + ["• " + check for check in session.get("checks", [])]
    result = session.get("verification")
    if result:
        out.append("Latest check: " + result.get("status", "unknown"))
        out += [item["path"] + ": " + item["status"] for item in result.get("static", [])]
        if result.get("reason"):
            out.append(result["reason"])
    runs = session.get("runs") or []
    if runs:
        last = runs[-1]
        out.append(f"Command runs ({len(runs)}): last was {last.get('command', '')}")
        out.append(f"→ {last.get('status')} · exit {last.get('exit_code')} · {last.get('seconds')}s")
        out += ["  " + str(row)[:200] for row in (last.get("failures") or [])[:8]]
    return out
