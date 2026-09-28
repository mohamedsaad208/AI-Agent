"""Close the loop between a real command run and the next reviewable proposal.

Nothing here writes project files. A failed run becomes evidence for one more
plan() turn, which still produces a proposal the user approves before anything
is written; that keeps the review-first guarantee intact while letting the
agent iterate against a compiler or test suite.
"""
from __future__ import annotations
from pathlib import Path

from .engine import atomic_json, event, load_session, shrink_warning
from .errors import PolicyError
from .labels import INTERRUPTED_STATES
from .redaction import redact
from . import runner

MAX_FIX_ROUNDS = 3
FAILURES_KEPT = 12
TAIL_KEPT = 2500

# The two windows ask the same question in the same words, so the wording lives beside the
# budget it names. Continuing is an answer, not a guess: the round is what the button says.
FIX_OFFER_TITLE = "Keep going?"
# The offer's third answer. It belongs to a batch, and a batch is the web window's queue: Tk has no
# queue to be the rest of, so the button is drawn by the browser alone and the two answers it already
# has keep meaning what they meant.
FIX_OFFER_ALT = "Don't ask again for this batch"


def _approval_clause(auto_apply: bool) -> str:
    """What stands between a proposal and the file. Both offers quote this sentence, and the
    folder's switch decides which of its two readings is true — so the switch is named in it."""
    if auto_apply:
        return ("This folder's Auto-Apply switch is on: what the model proposes writes itself and "
                "the command runs again. It still refuses to empty a file you wrote, and a code "
                "block you clicked still waits for Apply.")
    return ("Nothing is written until you approve it — this folder's Auto-Apply switch is off.")


def fix_offer(model: str, next_round: int, auto_apply: bool, limit: int = MAX_FIX_ROUNDS) -> str:
    return (f"Fix round {next_round} of {limit}: send this failure output back to {model} and ask "
            "for the smallest change that makes the command pass. It proposes a diff. "
            + _approval_clause(auto_apply))


def step_offer(model: str, done_id: int, nxt: dict, total: int, auto_apply: bool) -> str:
    return (f"Step {done_id} is verified. The next one is step {nxt['id']} of {total}: "
            f"“{nxt['title']}”. Starting it sends that task to {model} and proposes a diff. "
            + _approval_clause(auto_apply))

def removal_notice(session: dict | None) -> str:
    """What a proposal says about itself when it empties a file, or "" when none does.

    Both windows read this one copy: the Tk confirm text and the webapp's warning box quote it,
    and the refusal below is built from it.
    """
    if not session:
        return ""
    notes = [note for note in (shrink_warning(change) for change in session.get("changes", []))
             if note]
    if not notes:
        return ""
    return ("Removes most of an existing file:\n"
            + "\n".join("• " + note[:180] for note in notes[:3]) + "\n\n")


def must_ask(session: dict | None, prior: dict | None = None) -> str:
    """Why this proposal needs a click even in a folder whose Auto-Apply switch is on, or "".

    Two situations: a proposal that empties a file the developer wrote, which is the mistake a
    small model really does make; and a previous task that stopped mid-write, which means the
    folder already matches nothing on this card, so no review the user saw covers this write.
    A third: any deletion. The folder's switch takes away a click, and a file has no diff to
    review after the click that removed it.
    """
    deletes = [change.get("path", "") for change in (session or {}).get("changes", [])
               if isinstance(change, dict) and change.get("delete")]
    if deletes:
        rest = f" and {len(deletes) - 3} more" if len(deletes) > 3 else ""
        return ("Removes a file: " + ", ".join(deletes[:3]) + rest +
                ". Auto-Apply takes a click out of this, not a file out of the folder.")
    notice = removal_notice(session)
    if notice.strip():
        return notice.strip()
    if prior and prior.get("state") in INTERRUPTED_STATES:
        return ("The last task in this folder stopped part way through writing, so what is on "
                "disk is not what you reviewed.")
    return ""


STATE_FOR_RUN = {"passed": "CHECKS_PASSED", "failed": "VERIFICATION_FAILED",
                 "timeout": "VERIFICATION_BLOCKED", "unverified": "VERIFICATION_BLOCKED",
                 "unavailable": "VERIFICATION_BLOCKED"}


def record_run(session_path: Path, result: dict) -> dict:
    """Persist a slimmed run summary on the session; the full log stays out of the store."""
    session = load_session(session_path)
    if session["state"] not in {"APPLIED_UNVERIFIED", "CHECKS_PASSED", "VERIFICATION_FAILED",
                                "VERIFICATION_BLOCKED"}:
        raise PolicyError("Apply the reviewed proposal before running commands.")
    slim = {key: value for key, value in result.items() if key not in {"output", "tail", "failures"}}
    slim["failures"] = [redact(str(row)) for row in result.get("failures", [])][:FAILURES_KEPT]
    slim["tail"] = redact((result.get("tail") or "")[-TAIL_KEPT:])
    session.setdefault("runs", []).append(slim)
    session["state"] = STATE_FOR_RUN[result["status"]]
    event(session, "run", **{key: slim[key] for key in ("recipe", "status", "exit_code", "seconds")})
    atomic_json(session_path, session)
    return session


def last_run(session: dict) -> dict | None:
    runs = session.get("runs") or []
    return runs[-1] if runs else None


def fix_needed(session: dict) -> bool:
    return bool(last_run(session)) and last_run(session)["status"] in {"failed", "timeout"}


def evidence(run: dict) -> str:
    """Untrusted build/test output for the next turn, capped for small context budgets."""
    lines = ["Command: " + str(run.get("command", "")),
             "Exit code: " + str(run.get("exit_code")),
             "Status: " + str(run.get("status", ""))]
    if run.get("failures"):
        lines.append("Reported problems:")
        lines.extend("  " + str(row)[:300] for row in run["failures"][:FAILURES_KEPT])
    lines.append("End of output:")
    lines.append(str(run.get("tail", ""))[:TAIL_KEPT])
    return redact("\n".join(lines))[:12000]


def fix_task(run: dict) -> str:
    label = str(run.get("label") or run.get("recipe") or "the command")
    return (f"The {label} command failed. Read the affected files, then propose the smallest "
            "change that makes it pass. Keep existing behavior and public APIs; do not delete "
            "tests or weaken assertions to pass; do not add dependencies.")


def passes(session: dict) -> bool:
    return bool(last_run(session)) and last_run(session)["status"] == "passed"


def choose_recipe(repo: Path, preferred: str | None = None) -> tuple[str | None, list[str]]:
    options = runner.detect(repo)
    if preferred and preferred in options:
        return preferred, options
    return (options[0] if options else None), options
