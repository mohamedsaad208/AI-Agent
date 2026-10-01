"""Close the loop between a real command run and the next reviewable proposal.

Nothing here writes project files. A failed run becomes evidence for one more
plan() turn, which still produces a proposal the user approves before anything
is written; that keeps the review-first guarantee intact while letting the
agent iterate against a compiler or test suite.
"""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
from typing import Any

from .engine import (atomic_json, chat_sessions, event, load_session, shrink_warning,
                     unexpected_notice)
from .errors import AgentError, PolicyError
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

# What the loop says when it declines to spend another turn. Twice it said these in two places, worded
# differently in each, and the same stalled loop read as two different problems depending on which
# window was open. The advice names Checks because that is where both windows keep the full output.
FIX_STOP_HINT = "Try a narrower task, another model, or read the output in Checks."
REPEATED_PROPOSAL = ("This round proposed exactly the same files as an earlier one. Nothing new was "
                     "tried — the proposal is still there to review, but applying it again will not "
                     "change what the command says.")
REPEATED_ADVICE = ("The model repeated its previous proposal. Change the task, the model, or the "
                   "files it was shown — not the click.")


def stop_line(reason: str) -> str:
    return "🛑 " + reason.capitalize() + "."


def stop_status(reason: str) -> str:
    return reason.capitalize() + ". " + FIX_STOP_HINT


def round_status(model: str, round_number: int, kind: str, limit: int = MAX_FIX_ROUNDS) -> str:
    """What the loop is doing right now, said the same way in both windows.

    The kind is in the sentence because the odds are in it: "asking for the smallest change" means
    something different when the machine is missing the tool than when an assertion is.
    """
    return (f"Fix round {round_number}/{limit}: asking {model} for the smallest change that makes "
            "the command pass"
            + (f" (looks like a {kind} failure)" if kind and kind != "unknown" else "") + "…")


def _approval_clause(auto_apply: bool) -> str:
    """What stands between a proposal and the file. Both offers quote this sentence, and the
    folder's switch decides which of its two readings is true — so the switch is named in it."""
    if auto_apply:
        return ("This folder's Auto-Apply switch is on: what the model proposes writes itself and "
                "the command runs again. It still refuses to empty a file you wrote, and a code "
                "block you clicked still waits for Apply.")
    return ("Nothing is written until you approve it — this folder's Auto-Apply switch is off.")


def timeline(rows: list, limit: int = MAX_FIX_ROUNDS) -> str:
    """What the loop already tried, in the one wording both windows and the offer read from.

    A round counter was the whole history a user could see, and a counter is a promise rather than a
    record: three rounds of the same seven failures read exactly like three rounds of progress. These
    are the runs themselves — the same rows the report exports, so the card, the chat and the file
    cannot tell three different stories about one attempt.
    """
    shown = rows[-limit:]
    if not shown:
        return ""
    lines = ["Tried so far:"]
    for index, row in enumerate(shown, start=len(rows) - len(shown) + 1):
        detail = []
        if row.get("tests"):
            detail.append(str(row["tests"]) + " tests")
        if row.get("failures"):
            detail.append(str(row["failures"]) + " failing")
        if row.get("files"):
            detail.append(str(row["files"]) + " file(s) changed")
        kind = str(row.get("category") or "")
        if kind:
            detail.append("looks like " + kind)
        lines.append(str(index) + ". " + str(row.get("label") or "the command")
                     + (" in " + row["folder"] if row.get("folder") else "") + ": "
                     + str(row.get("status") or "ran") + " (" + ", ".join(detail) + ")")
    return "\n".join(lines)


def fix_offer(model: str, next_round: int, auto_apply: bool, limit: int = MAX_FIX_ROUNDS,
              history: list | None = None) -> str:
    block = timeline(history or [])
    return (f"Fix round {next_round} of {limit}: send this failure output back to {model} and ask "
            "for the smallest change that makes the command pass. It proposes a diff. "
            + _approval_clause(auto_apply)
            + ("\n\n" + block if block else ""))


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


def approval_advisories(session: dict | None) -> str:
    """Everything the operator should read before approving, in one string, from one owner.

    Both windows call this instead of the two halves: the removal warning lived alone in the dialog until
    the unexpected-file flag was added, and a sentence that reaches one window's confirm box is the drift
    this project keeps recording. The order is the reading order — what a change destroys, then which
    changes nobody asked for.
    """
    return removal_notice(session) + unexpected_notice(session)


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


def round_history(runs_dir: Path, root: str, chat_id: str) -> list:
    """Every session in one chat and folder, oldest first — the rounds are sessions, not a counter.

    Read fresh by both windows, because the point is to compare the run that just finished with the
    ones before it, and a cached list is exactly as stale as the judgement it feeds. A folder whose
    records cannot be read is a folder with no history, which is the same answer as an empty one.
    """
    try:
        return [item for _, item in chat_sessions(runs_dir, root, chat_id)]
    except (AgentError, OSError):
        return []


def attempts_of(runs_dir: Path, root: str, chat_id: str, limit: int = MAX_FIX_ROUNDS) -> list:
    """The rounds the timeline and the offer draw from: this chat's attempts, newest window kept."""
    return attempts(round_history(runs_dir, root, chat_id))[-limit:]


def last_run(session: dict) -> dict | None:
    runs = session.get("runs") or []
    return runs[-1] if runs else None


def fix_needed(session: dict) -> bool:
    return bool(last_run(session)) and last_run(session)["status"] in {"failed", "timeout"}


def fix_task(run: dict) -> str:
    label = str(run.get("label") or run.get("recipe") or "the command")
    return (f"The {label} command failed{where_it_ran(run)}. Read the affected files, then propose "
            "the smallest change that makes it pass. Keep existing behavior and public APIs; do not "
            "delete tests or weaken assertions to pass; do not add dependencies.")


def where_it_ran(run: dict) -> str:
    """` in backend/auth-service`, or "" when the command ran at the root of the opened folder.

    A multi-module project has to be told which module failed, or the model reads the wrong tree and
    the round proposes a change to a file that was never involved. Relative, because the model's
    workspace is already the root and an absolute path is noise that can carry a username.
    """
    folder = runner.run_folder(run)
    return "" if not folder else " in " + folder


# ---------------------------------------------------------------- the shape of a failure
#
# `evidence()` used to send the same 12 failure lines plus the last 2 500 characters whatever the
# command had said. For a Maven run that cannot resolve its parent POM those 2 500 characters are a
# stack trace and a help URL — and the model spends its context on a build system's complaint instead
# of on the dependency that is missing. Naming the kind of failure is also what lets the loop stop
# honestly: "3 rounds, still failing" and "3 rounds, still 7 failures of the same kind" are different
# sentences, and only the second one tells the user the loop is not going anywhere.
CATEGORIES = (
    ("environment", (r"command not found", r"is not recognized", r"no such file or directory",
                     r"cannot find program", r"JAVA_HOME", r"not installed", r"Permission denied",
                     r"connection refused", r"could not resolve host", r"no route to host")),
    ("timeout", (r"timed out", r"timeout", r"cancelled after")),
    ("dependency", (r"non-resolvable parent pom", r"could not resolve", r"failed to collect dependencies",
                    r"artifact.*(not found|could not be resolved)", r"unresolved import",
                    r"modulenotfounderror", r"no module named", r"package .* is not installed",
                    r"peer dependency", r"lock file", r"cannot find module", r"fetch.*registry")),
    # Anchored where a compiler's own wording allows, because "AssertionError: expected 5 but was 4"
    # contains both "error: expected" and an assertion, and it is the second one.
    ("syntax", (r"syntaxerror", r"syntax error", r"unterminated", r"invalid syntax",
                r"parse error", r"unexpected token", r"^error: expected", r"\bmissing semicolon\b",
                r"class, interface, enum, or record expected", r"illegal start of",
                r"expected .{0,40}(at|on) line \d")),
    ("type", (r"typeerror", r"incompatible types", r"cannot be applied to", r"unresolved reference",
             r"has no attribute", r"is not assignable", r"no overload matches", r"symbol not found",
             r"cannot find symbol")),
    ("assertion", (r"assertionerror", r"assert\s*\(?", r"expected.*but was", r"want.*got",
                   r"comparison failure", r"expected:.*<", r"test failed", r"failed tests",
                   r"^\s*failed\b", r"^\s*\d+ failed", r"failures=\d", r"there were failing tests")),
)

# A build tool's own summary line, kept whatever the category is: it is the one part of the output
# that states how many tests ran, and "the run is not a proof" is decided from it elsewhere.
SUMMARY_PATTERNS = (r"^Tests run:", r"^test result:", r"^# tests", r"^ok\s", r"^FAIL", r"BUILD FAILURE",
                    r"BUILD SUCCESS", r"^ERROR: ", r"^\s*\d+ passed")

# Stack frames and separators: printed right below the line that explains the failure, and saying
# only which jar the tool was standing in when it noticed. `[ERROR] at org.apache.maven…` carries the
# error marker, so a keyword filter alone cannot tell a frame from a diagnosis.
FRAME_PATTERNS = (r"\bat\s+[\w.$]+\(", r'^file\s+"[^"]+",\s+line\s+\d+', r"^traceback \(most recent",
                  r"^\s*\.{3,}\s*$", r"^\s*~+\^~*\s*$", r"^\(\S+ exception:\s", r"^\s*at \S+\.\S+")


def is_frame(line: str) -> bool:
    lowered = line.strip().lower()
    return any(re.search(pattern, lowered) for pattern in FRAME_PATTERNS)


def relevant(run: dict, category: str, limit: int = 4000) -> str:
    """The lines of the tail that speak about this kind of failure, in the order they were printed.

    The full output stays on the session and in Activity — this is the slice the model is sent, chosen
    so a small context window is spent on the failure rather than on a stack trace below it.
    """
    tail = str(run.get("tail") or "")
    patterns = dict(CATEGORIES).get(category) or ()
    rows, seen = [], set()
    for line in tail.splitlines():
        flat = line.strip()
        if not flat or flat in seen or is_frame(flat):
            continue
        lowered = flat.lower()
        if (any(re.search(pattern, lowered) for pattern in patterns)
                or any(re.search(pattern, flat) for pattern in SUMMARY_PATTERNS)
                or any(marker in lowered for marker in ("error", "fail", "cannot", "could not", "missing"))):
            seen.add(flat)
            rows.append(flat[:300])
    if not rows:
        rows = [line.strip()[:300] for line in tail.splitlines()
                if line.strip() and not is_frame(line)][-12:]
    return "\n".join(rows[-24:])[:limit]


def classify(run: dict) -> str:
    """What kind of failure this is, from the text the tool itself printed.

    Deliberately first-match in a fixed order: an environment failure that also contains the word
    "assert" is still the machine not having the tool, and the fix is not a code change. `unknown` is
    an honest answer and the loop treats it as one — it does not pretend to a diagnosis it lacks.

    A run that did not fail has no kind: labelling a passing build "unknown" would read as "the tool
    could not tell what went wrong", and nothing went wrong.
    """
    if run.get("status") == "passed":
        return ""
    text = "\n".join([str(run.get("tail") or ""), "\n".join(str(row) for row in (run.get("failures") or [])),
                      str(run.get("reason") or "")]).lower()
    if run.get("status") == "timeout" or run.get("timed_out"):
        return "timeout"
    if run.get("status") == "unavailable":
        return "environment"
    for name, patterns in CATEGORIES:
        if any(re.search(pattern, text, re.MULTILINE) for pattern in patterns):
            return name
    return "unknown"


def evidence(run: dict) -> str:
    """Untrusted build/test output for the next turn, capped for small context budgets."""
    category = classify(run)
    folder = runner.run_folder(run)
    lines = ["Command: " + str(run.get("command", ""))]
    if folder:
        lines.append("Folder: " + folder + " of the opened project")
    lines += ["Exit code: " + str(run.get("exit_code")),
              "Status: " + str(run.get("status", ""))]
    if category:
        lines.append("What this looks like: " + category)
    if run.get("failures"):
        lines.append("Reported problems:")
        lines.extend("  " + str(row)[:300] for row in run["failures"][:FAILURES_KEPT])
    picked = relevant(run, category)
    if picked:
        lines.append("The lines that say so:")
        lines.append(picked)
    return redact("\n".join(lines))[:12000]


# ---------------------------------------------------------------- the rounds already spent

def change_digest(changes: list) -> str:
    """A fingerprint of *what would be written*, independent of the task text around it.

    `proposal_hash` covers the summary and the checks, which a re-worded second attempt changes; the
    question the loop needs to answer is whether the files are the same, and that is this.
    """
    digest = hashlib.sha256()
    for change in sorted(changes or [], key=lambda item: str(item.get("path", ""))):
        if not isinstance(change, dict):
            continue
        body = change.get("after")
        digest.update(str(change.get("path", "")).encode("utf-8", "replace"))
        digest.update(b"\0")
        digest.update(b"DELETE" if change.get("delete") or body is None
                      else str(body).encode("utf-8", "replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def attempts(sessions: list) -> list:
    """One row per attempt in a chat, oldest first: what ran, what it looked like, what it changed.

    Rounds are separate sessions, not one session with a counter, so the history is the chat. This is
    also what a report and the timeline in the window are both drawn from — one reading of the record,
    three places that show it.
    """
    rows = []
    for item in sessions:
        for run in (item.get("runs") or []):
            folder = runner.run_folder(run)
            rows.append({"session": item.get("id", ""), "at": run.get("at") or item.get("created", ""),
                         "state": item.get("state", ""), "label": run.get("label", ""),
                         "folder": Path(folder).name if folder else "",
                         "status": run.get("status", ""), "exit_code": run.get("exit_code"),
                         "seconds": run.get("seconds"),
                         "tests": (run.get("proof") or {}).get("tests") or 0,
                         "failures": (run.get("proof") or {}).get("failures") or 0,
                         "category": classify(run),
                         "files": len(item.get("changes") or [])})
    return rows


def already_tried(sessions: list, changes: list) -> bool:
    """Is this proposal the same set of files with the same contents as one already attempted?

    A model that is asked twice for the same fix will often answer with exactly the same diff. Burning
    a round on it is not iteration, and the user watching the rounds counts it as progress.
    """
    seen = set()
    for item in sessions:
        proposal = item.get("changes") or []
        if proposal:
            seen.add(change_digest(proposal))
    return bool(changes) and change_digest(changes) in seen


def stalled(sessions: list) -> str:
    """Did the last attempt make the run any better? "" when it did, or when there is nothing to say.

    Comparison is only honest between two runs of the same command — 7 failures in Maven and 4 in
    pytest are not a trend. A run that never produced a test count has no numbers to compare, which
    is exactly what a missing tool or an unresolvable artifact looks like, so its second reading is
    by kind. Otherwise the loop keeps its budget and says nothing, because a false "no progress"
    stop is worse than one more round.
    """
    runs = [row for row in attempts(sessions) if row.get("seconds") is not None]
    if len(runs) < 2:
        return ""
    previous, latest = runs[-2], runs[-1]
    if (previous["label"], previous["folder"]) != (latest["label"], latest["folder"]):
        # Two different commands are not a trend, and neither are the same command run in two modules
        # of one reactor: auth-service's three failures say nothing about product-service's three.
        return ""
    if latest["status"] == "passed":
        return ""
    if (previous["category"] == latest["category"]
            and latest["category"] in {"environment", "dependency"}):
        return ("the same " + latest["category"] + " failure in the last two rounds: the last "
                "change did not move it")
    if previous["tests"] or latest["tests"]:
        now, before = (latest["failures"], -latest["tests"]), (previous["failures"], -previous["tests"])
        if now >= before:
            word = "the same as" if now == before else "worse than"
            return ("no progress: " + str(latest["failures"]) + " failing of "
                    + str(latest["tests"]) + " tests, " + word + " the round before")
    return ""


# What makes two recorded failures the same error. A stack line number, a test count, an absolute path
# and a `[ERROR]` prefix all move when nothing about the problem moved, so a fingerprint that kept them
# would call the same broken build a new one every round — and the whole point is to notice the repeat.
ERROR_PREFIX = re.compile(r"^(?:\s*(?:\[?\s*(?:ERROR|WARNING|INFO|SEVERE)\]?|ERROR:\s*|FAILED\b|FAIL\b)"
                          r"\s*[:\[\]]*\s*)+", re.I)
# The position a compiler hangs on a filename (`Foo.java:12`, `Foo.java:[45,12]`) is stripped with the
# file. What comes *after* a filename is kept: `::test_total` and `.method()` are the part of a pytest or
# Maven line that says which test broke, and a token that gobbled them would fingerprint a whole suite as
# one anonymous failure and hide which case is still open.
FILE_TOKEN = re.compile(r"(?:[A-Za-z]:[\\/])?[^\s|]*\.(?:java|kt|kts|py|go|rs|js|jsx|ts|tsx|xml|gradle|"
                        r"json|ya?ml|properties|mod|toml|c|cpp|h)\b(?:[:/]\[?\d+(?:,\s*\d+)?\]?)?",
                        re.I)
COORDINATES = re.compile(r"\b(?:at|line)\s+~?\d+\b|\b:\d+(?::\d+)?\b|\bL\d+:\d+\b", re.I)
COUNTERS = re.compile(r"\b\d+(?:\.\d+)*\b")
ERROR_LINES_KEPT = 6
ERROR_LINE_CHARS = 160


def error_identity(run: dict) -> str:
    """The failure's own words, with its coordinates taken out and its secrets taken out with them.

    This text is written into a model prompt and into the thread, so redaction is not optional here:
    a Maven or pytest failure line routinely quotes the connection string that broke, and the repository
    redactor cannot help a value that never looked like a key.
    """
    if not isinstance(run, dict) or run.get("status") == "passed":
        return ""
    lines = [str(row) for row in (run.get("failures") or []) if str(row).strip()]
    if not lines:
        # No parsed failures means the command failed before it reported any — a missing tool, an
        # unresolvable artifact, a syntax error Maven never reached. The tail's last lines are where
        # those say what happened.
        lines = [line for line in str(run.get("tail", "")).splitlines() if line.strip()]
    cleaned = []
    for line in lines[-ERROR_LINES_KEPT:] if not run.get("failures") else lines[:ERROR_LINES_KEPT]:
        text = ERROR_PREFIX.sub("", str(line))
        text = FILE_TOKEN.sub("<file>", text)
        text = COORDINATES.sub("", text)
        text = COUNTERS.sub("N", " ".join(text.split()).casefold())
        text = redact(text)[:ERROR_LINE_CHARS]
        if text.strip(" .,:;-"):
            cleaned.append(text)
    return "\n".join(cleaned)


def error_fingerprint(run: dict) -> str:
    """A short, stable id for one error — the identity above, hashed so it is cheap to carry."""
    identity = error_identity(run)
    if not identity:
        return ""
    return hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()[:12]


def unresolved(sessions: list, exclude_chat: str = "") -> list[dict]:
    """Build errors this project has hit and never saw pass, whoever hit them.

    `stalled()` answers "did this round help"; this answers the question a person asks when they start a
    second chat for a build the first one could not fix — has this exact failure already been fought? An
    error is settled when a run of the *same command in the same folder* passed after its last
    occurrence; anything else is still open, and a task that walks into it is told before it spends a
    round discovering it again.
    """
    failures, passes = [], []
    for item in sessions or []:
        if not isinstance(item, dict):
            continue
        # A session with no chat identity is its own, the same way `chat_sessions` treats one, so the
        # task being planned now cannot mistake a bare run for somebody else's conversation.
        owner = str(item.get("chat_id", "") or item.get("id", ""))
        if exclude_chat and owner == exclude_chat:
            continue
        created = str(item.get("created", ""))
        for run in (item.get("runs") or []):
            stamp = str(run.get("at") or created)
            where = {"at": stamp, "label": str(run.get("label", "")),
                     "folder": runner.run_folder(run), "status": str(run.get("status", ""))}
            if where["status"] == "passed":
                passes.append(where)
                continue
            mark = error_fingerprint(run)
            if mark:
                failures.append({**where, "fingerprint": mark, "session": str(item.get("id", "")),
                                 "sample": (error_identity(run).splitlines() or [""])[0]})

    out = []
    for mark in dict.fromkeys(row["fingerprint"] for row in failures):
        seen = sorted((row for row in failures if row["fingerprint"] == mark), key=lambda row: row["at"])
        last = seen[-1]
        settled = any(row["status"] == "passed" and row["at"] > last["at"]
                      and (row["label"], row["folder"]) == (last["label"], last["folder"])
                      for row in passes)
        if settled:
            continue
        out.append({"fingerprint": mark, "count": len(seen), "since": seen[0]["at"],
                    "last": last["at"], "label": last["label"], "folder": last["folder"],
                    "sample": last["sample"], "tasks": len({row["session"] for row in seen})})
    return sorted(out, key=lambda row: (-row["count"], row["last"]))


def should_stop(sessions: list, round_number: int, limit: int = MAX_FIX_ROUNDS) -> tuple[bool, str]:
    """The one question both windows ask before spending a model turn: is there a reason not to?"""
    rows = attempts(sessions)
    if round_number >= limit:
        # The extra clause is only said when the record shows a command that still fails: `ask_for_fix`
        # can be reached with a budget spent and a run that passed, and then the honest sentence about
        # why the loop is over is just the budget.
        still = (" and the command still fails"
                 if rows and rows[-1]["status"] in {"failed", "timeout"} else "")
        return True, "stopped after " + str(limit) + " fix rounds" + still
    reason = stalled(sessions)
    if reason:
        return True, reason
    return False, ""


def passes(session: dict) -> bool:
    return bool(last_run(session)) and last_run(session)["status"] == "passed"


def choose_recipe(repo: Path, preferred: str | None = None) -> tuple[str | None, list[str]]:
    options = runner.detect(repo)
    if preferred and preferred in options:
        return preferred, options
    return (options[0] if options else None), options


# ---------------------------------------------------------------------------
# Phase 2: AgentCore State Machine Loop Integration
# ---------------------------------------------------------------------------

def verification_from_run(run: dict) -> Any:
    """Build a typed VerificationResult from a runner output dictionary."""
    from .core import VerificationResult
    status = str(run.get("status", "unknown"))
    passed = status == "passed"
    command = str(run.get("command") or run.get("recipe", ""))
    exit_code = int(run.get("exit_code", 0 if passed else 1))
    summary = str(run.get("label") or run.get("recipe") or "") + ": " + status
    failures = [str(f) for f in run.get("failures", [])]
    tail = str(run.get("tail", ""))
    return VerificationResult(
        passed=passed,
        status=status,
        command=command,
        exit_code=exit_code,
        summary=summary,
        failures=failures,
        output_tail=tail,
        details={"recipe": run.get("recipe"), "seconds": run.get("seconds")},
    )


def execute_verification(
    core: Any,
    state: Any,
    repo: Path,
    recipe: str,
    target: str = "",
    sandbox: str = "",
    cancelled: Any = None,
) -> Any:
    """Execute a recipe via runner, construct VerificationResult, and advance AgentCore state.

    Transitions AgentState:
      VERIFYING -> DONE   (if run passed)
      VERIFYING -> FIXING (if run failed or timed out)
    """
    from .core import AgentStatus
    if state.status != AgentStatus.VERIFYING:
        raise PolicyError(f"Cannot verify from state {state.status.value}; must be in VERIFYING.")

    run_dict = runner.run(
        repo,
        recipe,
        timeout=runner.timeout_for(recipe),
        target=target,
        sandbox=sandbox,
        cancelled=cancelled,
    )
    verif = verification_from_run(run_dict)
    core.record_verification(state, verif)
    return verif


def handle_fix_evaluation(
    core: Any,
    state: Any,
    sessions: list,
    round_number: int,
    limit: int = MAX_FIX_ROUNDS,
) -> tuple[bool, str]:
    """Evaluate whether the fix loop should continue or terminate.

    If should stop: transitions state to FAILED and returns (False, reason).
    If should continue: keeps state in FIXING and returns (True, "continue").
    """
    from .core import AgentStatus
    if state.status != AgentStatus.FIXING:
        raise PolicyError(f"Cannot evaluate fix loop from state {state.status.value}; must be in FIXING.")

    stop, reason = should_stop(sessions, round_number, limit=limit)
    if stop:
        core.fail(state, reason)
        return False, reason
    return True, "continue"
