"""What a run is worth remembering, how the goal is kept safe, and what the compass may cost.

Three rules live here, all of them about size and consent, because the store below them is only
bytes and the compass above them is only wording:

**A goal is never changed in silence.** The Goal section is what every later task is measured
against, so a model that can restate it can measure its own work against anything it likes. An
attempt from the engine is therefore recorded as a *proposal* — one pending at a time, kept in the
sidecar, said to the user next time they look — and the file's own goal keeps saying what it said
before. The two ways it moves are both loud: the user approving the pending proposal, and the user
typing a goal themselves. A hand edit to ``project.md`` is the second kind, and it is detected by
the digest the guard reads rather than being treated as an intrusion: a person editing the file is
the user speaking, so the guard re-baselines to what they wrote.

**Local models get a ceiling, not a hint.** The compass is the one block in the prompt whose whole
purpose is to be small: a 4B model with a 4k window cannot spend a third of it on recitation. The
ceiling is 1500 tokens and it is spent by priority — the goal, then the constraints, then the
decisions — so a memory that outgrows its budget gives up the oldest progress note and never the
rule that was stated first.

**A summary says what the run did, in the run's own provenance.** Applied is applied, untested is
untested, and a rolled-back change is corrected rather than left claiming work that is no longer on
disk. Nothing here invents a verification the runner did not report; a small model reads the
compass as an account of this project, and the one thing that account must never do is flatter the
run that wrote it.

Nothing here opens a file: ``memory_store`` owns the bytes, ``compass`` owns the sentences the model
reads, and this module turns a session record into bounded updates and decides whether a goal may
move.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import re

from . import labels
from .memory_store import MemoryStore, clean_item, clean_scalar
from .redaction import redact

# The compass ceiling, in tokens rather than characters, because the thing it protects is a model's
# window and every local profile names its own. Characters are what the caller's budget is measured
# in, so the two are converted with the same estimator everywhere.
COMPASS_TOKENS = 1500

# Rough sizes for the tokenizers a local model actually ships with: roughly four characters per
# token in Latin text, roughly two in Arabic (its subword pieces are shorter), and about one token
# per character in the CJK ranges. This is an estimator, not a tokenizer — no dependency, no
# download, and the same answer offline as online — so it is rounded up and deliberately
# over-estimates a little: a ceiling that lets an oversized block through is not a ceiling.
LATIN_CHARS_PER_TOKEN = 4
SEMI_CHARS_PER_TOKEN = 2
ARABIC_BLOCK = re.compile(r"[\u0600-\u08ff\ufb1d-\ufdff]")
CJK_BLOCK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff00-\uffff]")

# One stored line per kind of thing, and how many of them a single run may add. The cap is on the
# *update*, not the file: a run that touched forty files is remembered by the eight that matter,
# because the fortieth line of progress is the one that pushes the goal out of the budget.
STEP_ITEMS = 8
DECISION_ITEMS = 6
ISSUE_ITEMS = 5
STEP_CHARS = 200

# The order a memory gives up its sections in when the budget runs out: lowest priority is cut
# first. A progress note is the least binding thing in the document and the newest in it, so it
# goes before a constraint the user stated three chats ago.
PRIORITY = ("goal", "constraints", "open_issues", "decisions", "task", "steps", "progress")

STATUS_WORDS = {"applied": "applied", "verified": "verified", "rolled_back": "rolled back",
                "untested": "untested", "blocked": "blocked", "rejected": "rejected"}


@dataclass
class GoalOutcome:
    """What the guard decided about one attempt to state the project's goal.

    `applied` is the only answer that means the file changed. `pending` means the proposal is held
    for the user and the goal still says what it said; `hand-edit` means a person typed the goal in
    the file and the guard re-baselined to their wording rather than arguing with it.
    """

    applied: bool
    reason: str
    goal: str
    pending: str = ""
    message: str = ""


@dataclass
class Fit:
    """A memory cut to fit, and an honest account of what did not fit."""

    kept: list = field(default_factory=list)
    sections: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    tokens: int = 0

    def text(self) -> str:
        return "\n".join(self.kept)

    @property
    def truncated(self) -> bool:
        return bool(self.dropped)


def estimate_tokens(text) -> int:
    """How many tokens a local model's tokenizer is likely to spend on this text.

    Three buckets, because that is where the sizes actually differ: Latin prose, Arabic script, and
    the CJK ranges, where a single character is a token. Anything else — digits, punctuation,
    spaces — rides with Latin, which is what a byte-pair tokenizer mostly does with them.
    """
    raw = str(text or "")
    if not raw:
        return 0
    cjk = len(CJK_BLOCK.findall(raw))
    arabic = len(ARABIC_BLOCK.findall(raw))
    latin = len(raw) - cjk - arabic
    spent = (latin / LATIN_CHARS_PER_TOKEN) + (arabic / SEMI_CHARS_PER_TOKEN) + float(cjk)
    return max(1, math.ceil(spent))


def tokens_for_chars(chars: int) -> int:
    """The token ceiling a caller holding `chars` of prompt room is allowed to spend."""
    return max(0, int(chars) // LATIN_CHARS_PER_TOKEN)


def fit(lines, budget_tokens: int, *, prefix: str = "") -> Fit:
    """The lines that fit the ceiling, in priority order, and the sections that did not.

    `prefix` is the text the caller will put in front of what is kept — a block header is part of
    what the window pays for, so it is charged here rather than counted nowhere. The running cost is
    measured over the joined text rather than summed line by line, because a token estimator rounds
    up: eight lines each rounded up are eight tokens a caller would never actually spend, and a
    ceiling that only ever over-sends is a ceiling nobody trusts.

    A line is kept whole or not at all — half a constraint reads as a rule the user never stated —
    with one exception: the goal. A goal that only half fits is shortened with an explicit ellipsis
    and still sent, because a run with no goal drifts, and a run with a truncated one at least
    drifts in the right direction.
    """
    budget = max(0, int(budget_tokens))

    def cost(kept) -> int:
        return estimate_tokens(prefix + "\n".join(kept)) if kept else estimate_tokens(prefix)

    ranked = []
    for index, item in enumerate(lines):
        section = str(item[0])
        rank = PRIORITY.index(section) if section in PRIORITY else len(PRIORITY)
        ranked.append((rank, index, section, str(item[1])))
    ranked.sort()
    answer = Fit()
    for _rank, _index, section, line in ranked:
        if not line.strip():
            continue
        trial = answer.kept + [line]
        if cost(trial) <= budget:
            answer.kept = trial
            answer.sections.append(section)
            continue
        if section == "goal" and "goal" not in answer.sections:
            short = line
            while short and cost(answer.kept + [short + "…"]) > budget:
                short = short[:len(short) - 16]
            if len(short) > 24:
                answer.kept = answer.kept + [short + "…"]
                answer.sections.append(section)
                continue
        answer.dropped.append(section)
    answer.tokens = cost(answer.kept)
    return answer


def normalize(text) -> str:
    """Two ways of saying one goal, compared as one thing."""
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


# ---------------------------------------------------------------------- goal protection


def record_goal(store: MemoryStore, text, *, approved: bool = False,
                source: str = "agent", arabic: bool = False) -> GoalOutcome:
    """State the project's goal — or propose the change, when nobody approved it.

    `approved` is the whole rule: it is set by a person typing a goal or clicking approve, never by
    a run that would like the record to match what it did. An unapproved statement that differs
    from the protected goal becomes the single pending proposal, replacing whatever was waiting
    there, because a queue of five proposed goals is five ways to hide the newest one.
    """
    wanted = clean_scalar(text)
    current = store.goal()
    if not wanted:
        return GoalOutcome(False, "empty", current,
                           store.read_meta().get("pending_goal", ""),
                           message=labels.note("goal_empty", arabic=arabic))
    # A goal in the file that does not match the digest written with it was typed by a hand. That
    # person outranks the model, so the record is re-based on their wording before anything else.
    if current and not store.goal_is_current():
        store.set_goal(current, source="user")
        return GoalOutcome(False, "hand-edit", current,
                           message=labels.note("goal_hand_edit", arabic=arabic))
    if normalize(current) == normalize(wanted):
        return GoalOutcome(False, "same", current,
                           message=labels.note("goal_same", arabic=arabic))
    if not current:
        store.set_goal(wanted, source="user" if approved else source)
        return GoalOutcome(True, "opened", wanted,
                           message=labels.note("goal_opened", arabic=arabic))
    if approved:
        store.set_goal(wanted, source="user")
        return GoalOutcome(True, "approved", wanted,
                           message=labels.note("goal_replaced", arabic=arabic))
    store.note_pending_goal(wanted)
    return GoalOutcome(False, "pending", current, wanted,
                       message=labels.note("goal_pending", arabic=arabic))


def pending_goal(store: MemoryStore) -> str:
    return str(store.read_meta().get("pending_goal") or "")


def adopt_hand_edit(store: MemoryStore) -> bool:
    """Take a goal that was typed into the file as the user's own, and stop arguing with it.

    Returns whether the record moved. A digest that no longer matches the Goal section is the only
    evidence there is that a person edited the markdown rather than the engine, and the person wins:
    the file's wording becomes the protected goal, with the user named as its author.
    """
    if store.goal_is_current():
        return False
    store.set_goal(store.goal(), source="user")
    return True


def approve_goal(store: MemoryStore, *, arabic: bool = False) -> GoalOutcome:
    """Promote the waiting proposal — the answer to one keypress, and the only loud path in."""
    wanted = pending_goal(store)
    if not wanted:
        return GoalOutcome(False, "none", store.goal(),
                           message=labels.note("goal_none", arabic=arabic))
    goal = store.set_goal(wanted, source="user")
    return GoalOutcome(True, "approved", goal,
                       message=labels.note("goal_now", arabic=arabic, goal=goal))


def reject_goal(store: MemoryStore, *, arabic: bool = False) -> GoalOutcome:
    wanted = pending_goal(store)
    if not wanted:
        return GoalOutcome(False, "none", store.goal(),
                           message=labels.note("goal_none", arabic=arabic))
    store.clear_pending_goal()
    return GoalOutcome(False, "rejected", store.goal(),
                       message=labels.note("goal_kept", arabic=arabic, goal=store.goal()))


# ---------------------------------------------------------------------- what a run leaves behind


def _state(session: dict, name: str) -> list:
    state = session.get("task_state")
    items = state.get(name) if isinstance(state, dict) else None
    return [str(item) for item in (items or []) if str(item).strip()]


def verification_of(session: dict) -> str:
    """What the checks actually said, in the number of words the proof justifies.

    Read off the last run the runner reported, never off the model's own summary: a proposal that
    claims its tests passed and a run whose command exited 1 are the same sentence to a model that
    is told which one happened, and the second reading is the expensive one.
    """
    runs = [row for row in (session.get("runs") or []) if isinstance(row, dict)]
    if not runs:
        return "untested"
    last = runs[-1]
    proof = last.get("proof") if isinstance(last.get("proof"), dict) else None
    if last.get("status") == "passed":
        if proof and proof.get("tests"):
            return f"{proof.get('tests')} tests, {proof.get('failures', 0)} failed"
        return "command passed, no test report"
    if last.get("status") == "failed":
        return "last command failed"
    return str(last.get("status") or "untested")


def status_of(session: dict, reported: str = "applied") -> str:
    """One word for where this task ended, with the verification folded into it."""
    state = str(session.get("state") or "")
    if state == "ROLLED_BACK":
        return "rolled_back"
    if reported == "verified" or state == "CHECKS_PASSED":
        return "verified"
    if state in ("PARTIAL_APPLY", "APPLYING"):
        return "untested"
    if reported == "blocked":
        return "blocked"
    return "applied"


def progress_line(session: dict, status: str = "applied") -> str:
    """The one line that says what this run did, safe for the record to be read years from now."""
    what = redact(str(session.get("summary") or session.get("task") or "a change"))
    files = len(session.get("changes") or [])
    word = STATUS_WORDS.get(status, status)
    stamp = str(session.get("created") or "")[:10]
    line = (stamp + ": " if stamp else "") + word + " — " + redact(what)[:STEP_CHARS - 40]
    if files:
        line += f" ({files} file{'s' if files != 1 else ''})"
    line += " — " + verification_of(session)
    return clean_item(line)


def steps_of(session: dict, *, arabic: bool = False, limit: int = STEP_ITEMS) -> list:
    """What the loop did, as engineering lines rather than as chat.

    The step events are the run's own actions, announced the same way the CLI status line announced
    them, so the memory and the screen the person watched say one thing. An event that will not
    phrase itself is skipped: this is a summary of a run that already happened, and a missing line
    is not a reason to fail the task that is about to.
    """
    rows = [row for row in (session.get("events") or [])
            if isinstance(row, dict) and row.get("kind") == "step"]
    out: list[str] = []
    for row in rows[-limit * 3:]:
        fields = {key: value for key, value in row.items() if key not in ("at", "kind", "id")}
        action = str(row.get("action") or "")
        if not action:
            continue
        try:
            line = redact(labels.step_line(arabic, action, **fields).strip())
        except Exception:     # noqa: BLE001 — a phrasing failure must never cost a run its memory
            line = redact(action + " " + " ".join(str(value) for value in fields.values())[:80])
        line = clean_item(line)
        if line and line not in out:
            out.append(line)
    return out[-limit:]


def memory_update(session: dict, *, status: str = "applied", arabic: bool = False) -> dict:
    """The bounded additions one finished run is worth, split by the layer they belong to.

    Project memory keeps what outlives the conversation — the decisions, the questions the run could
    not close, the line saying what it changed. Chat memory keeps what only this conversation needs:
    the task it was given and the steps it took. A run that produced nothing to say produces an
    empty update, which writes no file.
    """
    final = status_of(session, status)
    project = {"progress": [progress_line(session, final)]}
    decisions = _state(session, "decisions")[:DECISION_ITEMS]
    issues = _state(session, "open_issues")[:ISSUE_ITEMS]
    constraints = _state(session, "constraints")
    if decisions:
        project["decisions"] = decisions
    if issues:
        project["open_issues"] = issues
    if constraints:
        project["constraints"] = constraints
    chat_id = str(session.get("chat_id") or "")
    chat = {}
    if chat_id:
        chat["task"] = redact(str(session.get("task") or ""))[:300]
        steps = steps_of(session, arabic=arabic)
        if steps:
            chat["steps"] = steps
    return {"project": {key: value for key, value in project.items() if value},
            "chat": {key: value for key, value in chat.items() if value},
            "status": final, "goal": redact(str(session.get("goal") or ""))}
