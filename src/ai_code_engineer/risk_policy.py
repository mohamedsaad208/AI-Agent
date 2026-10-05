"""How much being wrong about this action costs, answered once.

Risk was rated in four places with four vocabularies. `policy.py` answers ALLOW, ASK or DENY for eight
action classes and is the only one of the four with a live decision point. `policy_engine.py` kept a
second ladder of seven names for the same acts. `review.py` kept a third, three bands wide, for a whole
proposal. `events.py` carries a fourth as a free-text string the terminal prints beside the ask. One
proposal could therefore be called `SECURITY_SENSITIVE` by the engine, `HIGH` by the reviewer and
`medium` by the event that asks the human — and a person shown three answers to one question learns to
read none of them.

The rating lives here, on two axes, because the question has two halves. `Operation` is what kind of act
this is, read off `policy`'s own classes instead of invented beside them, so every name here has a
decision point that gives it meaning. `Risk` is how much being wrong costs, in the four bands the event
and the terminal already speak, and it is what a human is shown.

A band may be more nervous than the table is strict: editing one DTO and editing the auth filter are both
writes the table answers ALLOW, because the table's job is to refuse things, and a refusal rate low
enough to keep the operator reading is the only refusal that works. A band may never be *calmer* than the
verdict shipped with it, which is why an `Assessment` carries both and why `verdict` is `policy.decide`'s
answer with nothing added. The band says "look at this one"; only the table says "and I will not do it
without you".

Two disagreements between the old copies are settled here, in favour of the table. `policy_engine` called a
write to `deploy.sh` security-sensitive; `policy.RUNS_LATER` does not list scripts and says why — nothing
in this tool runs a script it finds, a person does, and a rule that stops every script write teaches the
operator to approve without reading. So a script write is an ordinary write here, and what can lift a write
above `LOW` is a domain it names or a file whose job is run-time configuration. The other was scale:
`review` rated a proposal by its paths alone, `policy_engine` by one tool name at a time. Both are kept,
because they answer different questions — `assess` rates the single act the engine is about to take,
`assess_changes` rates the proposal a person is being asked to approve.

Nothing here refuses, approves, executes or remembers a grant: `policy.py` owns the table,
`permissions.py` owns what a folder said about it, and `gate.py` owns admission.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from . import policy
from .policy import (ALLOW, ASK, DENY, DELETE, EXECUTE_CUSTOM, EXECUTE_RECIPE, GIT_LOCAL,
                     NETWORK, READ, WRITE, WRITE_THAT_RUNS)


class Operation(str, Enum):
    """What kind of act this is — one `policy` action class seen from the human's side."""

    SAFE_READ = "SAFE_READ"
    LOCAL_WRITE = "LOCAL_WRITE"
    LOCAL_EXECUTION = "LOCAL_EXECUTION"
    SECURITY_SENSITIVE = "SECURITY_SENSITIVE"
    NETWORK_ACCESS = "NETWORK_ACCESS"
    DESTRUCTIVE = "DESTRUCTIVE"
    EXTERNAL_SIDE_EFFECT = "EXTERNAL_SIDE_EFFECT"


class Risk(str, Enum):
    """How much being wrong costs, in the bands the event and the terminal already speak."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# The order a band is compared in, and the order a verdict is compared in. Both are used one way only:
# take the worst of several answers, never average them. An average of one safe read and one destructive
# delete reads as "medium", which is the sentence nobody should ever be shown about a delete.
_BANDS = (Risk.LOW, Risk.MEDIUM, Risk.HIGH, Risk.CRITICAL)
_STRICTNESS = (ALLOW, ASK, DENY)

# Every operation's floor band. Deliberately a table and not arithmetic on the enum: the ladder is not a
# single slope — a network call to a loopback port and a `git push` are different kinds of exposure, and
# the sentence explaining which belongs in `labels`, not in a formula.
OPERATION_RISK: dict[Operation, Risk] = {
    Operation.SAFE_READ: Risk.LOW,
    Operation.LOCAL_WRITE: Risk.LOW,
    Operation.LOCAL_EXECUTION: Risk.MEDIUM,
    Operation.SECURITY_SENSITIVE: Risk.HIGH,
    Operation.NETWORK_ACCESS: Risk.HIGH,
    Operation.DESTRUCTIVE: Risk.HIGH,
    Operation.EXTERNAL_SIDE_EFFECT: Risk.CRITICAL,
}

# `policy`'s classes are the alphabet, so an operation is named after the class that carries it and the
# one place that can say what the class means is the table.
ACTION_OPERATION: dict[str, Operation] = {
    READ: Operation.SAFE_READ,
    WRITE: Operation.LOCAL_WRITE,
    WRITE_THAT_RUNS: Operation.SECURITY_SENSITIVE,
    DELETE: Operation.DESTRUCTIVE,
    EXECUTE_RECIPE: Operation.LOCAL_EXECUTION,
    EXECUTE_CUSTOM: Operation.LOCAL_EXECUTION,
    GIT_LOCAL: Operation.LOCAL_EXECUTION,
    NETWORK: Operation.NETWORK_ACCESS,
}

# Tool names arrive from the agent's own vocabulary and from `policy_engine`'s older one, so the words are
# kept here rather than expected of every caller. A verb not listed is not assumed harmless.
TOOL_ACTIONS: dict[str, str] = {
    "read_file": READ, "list_dir": READ, "list_files": READ, "grep": READ,
    "search_code": READ, "find_symbol": READ, "read": READ,
    "write_file": WRITE, "patch_file": WRITE, "create_file": WRITE, "write": WRITE,
    "delete_file": DELETE, "remove_file": DELETE, "rmdir": DELETE, "delete": DELETE,
    "run_tests": EXECUTE_RECIPE, "run_build": EXECUTE_RECIPE,
    "run_command": EXECUTE_CUSTOM, "run_shell": EXECUTE_CUSTOM, "exec": EXECUTE_CUSTOM,
    "fetch_url": NETWORK, "http_request": NETWORK, "curl": NETWORK, "request": NETWORK,
    "git_commit": GIT_LOCAL, "git_branch": GIT_LOCAL, "git": GIT_LOCAL,
    # A read of the repository, not a local run: `git diff` asks nothing of it, and it is the reason the
    # review card can be drawn at all. Named as a read rather than guessed from the command text, because
    # the verb is the thing the caller chose.
    "git_diff": READ, "git_status": READ, "git_log": READ,
}

# A target that leaves the machine, whatever tool carried it. Push, deploy and publish are the three the
# old engine named; `send` and `release` are the same act in other verbs' clothes.
EXTERNAL_MARKS = ("git push", "push origin", "deploy", "publish", "release", "send ")

# Irreversible words inside a command are worth naming separately from the `delete` class, because a model
# that asks for `rm -rf` came through `run_command`, whose class alone would read as a local run.
DESTRUCTIVE_MARKS = ("rm -rf", "drop database", "truncate table", "reset --hard", "clean -fd")

MAX_FILES_BEFORE_MEDIUM = 5   # more than this many files in one proposal is its own reason to slow down

# A file whose whole job is to say what the program does at run time. The table cannot see a difference
# between editing `OrderDto.java` and editing `application.properties` — both are writes it answers ALLOW —
# and a band that said `low` about the second would be the copy of `review`'s older, weaker rule that this
# module keeps rather than drops.
CONFIG_SUFFIXES = (".yml", ".yaml", ".properties", ".toml", ".ini", ".cfg", ".conf")
_CONFIGURATION_WORD = re.compile(r"(?<![a-z0-9])config(?![a-z0-9])", re.I)


def is_configuration(path: str) -> bool:
    """Whether this path is a run-time switch rather than a line of code."""
    text = str(path or "").replace("\\", "/").casefold()
    return text.endswith(CONFIG_SUFFIXES) or bool(_CONFIGURATION_WORD.search(text))


# The domains where being wrong is not a compile error. Lifted from `review.py` with one correction: the
# old rule asked whether "auth" appeared in the text at all, so `src/author/` was an authentication
# change. A domain is a word, and a word ends where a letter or digit does. The Arabic entries are there
# because a task written in Arabic names its domain in Arabic.
SENSITIVE_DOMAINS = (
    "auth", "login", "password", "crypto", "token", "secret", "payment", "billing",
    "migration", "schema", "docker", "k8s", "مصادقة", "فوترة", "ترميز",
)
_DOMAIN_WORD = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(d) for d in SENSITIVE_DOMAINS) + r")(?![a-z0-9])",
    re.I)


def worst_verdict(verdicts: Iterable[str]) -> str:
    """The strictest answer of several, or ALLOW for none."""
    return max(verdicts, key=_STRICTNESS.index, default=ALLOW)


def verdict_floor(verdict: str) -> Risk:
    """The band a verdict will not let a rating fall below.

    This is the one invariant of the whole module, written out instead of hoped for: a person being asked
    to approve something is being told it can hurt, and a card that shows `medium` next to an ask teaches
    them to click through asks.
    """
    return {ASK: Risk.HIGH, DENY: Risk.CRITICAL}.get(verdict, Risk.LOW)


def settle(band: Risk, verdict: str) -> Risk:
    """Raise a band to whatever its verdict requires, and never below it."""
    return _raise(band, verdict_floor(verdict))


def is_sensitive(text: str) -> bool:
    """Whether this text names a domain where a wrong answer costs more than a rebuild."""
    return bool(_DOMAIN_WORD.search(str(text or "")))


@dataclass(frozen=True)
class Assessment:
    """One act, rated: the band a human is shown, and the verdict the gate already had."""

    risk: Risk = Risk.LOW
    operation: Operation = Operation.SAFE_READ
    action: str = READ
    verdict: str = ALLOW
    reasons: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    files: int = 0

    @property
    def needs_human(self) -> bool:
        """Whether the table will not proceed on its own — asked, or refused."""
        return self.verdict in (ASK, DENY)

    @property
    def refused(self) -> bool:
        return self.verdict == DENY

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk.value,
            "operation": self.operation.value,
            "action": self.action,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "paths": list(self.paths),
            "files": self.files,
        }


class RiskPolicy:
    """The single rating surface: an act or a proposal in, one band and one verdict out.

    `overrides` is a folder's answers from `permissions.overrides`, passed in rather than read here, so
    this module stays importable by a window that has no app folder and by a test that has no disk — the
    same shape `policy.decide` has always had. Rating without it is still correct about the band, which is
    the only thing a report shows; the verdict it carries is the folder's default table answer.
    """

    def __init__(self, overrides: Mapping[str, str] | None = None) -> None:
        self.overrides = dict(overrides or {})

    def verdict(self, action: str) -> str:
        return policy.decide(action, self.overrides.get(action, ""))

    def action_for(self, verb_or_action: str, target: str = "") -> str:
        """Which class of act this is, from a tool verb, a class named directly, or a URL in the target.

        A caller that already holds a class gets it back untouched, because a name that arrives as a class
        was chosen by somebody with the contract in front of them. A target that is plainly a URL is a
        network call whatever verb carried it — `run_command curl ...` leaves the machine the same way
        `fetch_url` does, and the class asked about is the one the operator cares about. A write is put to
        `policy.write_action` here, so the one list that knows which files change what runs later is
        consulted once rather than re-guessed by whoever asks next.
        """
        name = str(verb_or_action or "").strip().lower().replace("-", "_")
        if name in policy.ACTIONS:
            action = name
        else:
            action = TOOL_ACTIONS.get(name, "")
        if action and action != NETWORK and url_in(target):
            action = NETWORK
        if action == WRITE:
            action = policy.write_action(target)
        return action

    def operation_for(self, action: str, target: str = "") -> Operation:
        """The operation named by a class, after the target has had its say.

        The target can raise the answer but never settle it: `rm -rf` inside `run_command` is a delete and
        a `git push` is a thing that leaves the machine, and neither is what its class alone said. Only a
        target that *is* an act is read that way — a path named by a write is a filename, and a rule that
        saw `deploy.yaml` as a deployment would spend the operator's attention on the wrong thing, which
        is the failure the whole table exists to prevent.
        """
        if action in (EXECUTE_RECIPE, EXECUTE_CUSTOM, GIT_LOCAL, NETWORK):
            text = str(target or "").replace("\\", "/").lower()
            if any(mark in text for mark in EXTERNAL_MARKS):
                return Operation.EXTERNAL_SIDE_EFFECT
            if any(mark in text for mark in DESTRUCTIVE_MARKS):
                return Operation.DESTRUCTIVE
        # A class the table does not name is answered as sensitive, not as harmless and not as something
        # that left the machine: it is a thing this tool cannot describe, which is exactly what
        # `SECURITY_SENSITIVE` was ever asked to mean.
        return ACTION_OPERATION.get(action or "", Operation.SECURITY_SENSITIVE)

    def band_for(self, operation: Operation, action: str, target: str = "",
                 task: str = "") -> tuple[Risk, tuple[str, ...]]:
        """The floor band of an operation, lifted by what the table cannot see.

        What raises a band is always something the verdict table is blind to: where a request actually
        lands (`policy.address_class` — a loopback test of the app being built is not a call to a metadata
        service), and what the file or the words around it are *for* (an auth filter, a run-time
        configuration, a task that says migration). None of them raises a verdict, because the table is
        the only thing in the tool that refuses, and a rating that refused things would be a second table
        nobody agreed with.
        """
        band = OPERATION_RISK.get(operation, Risk.CRITICAL)
        reasons: list[str] = []
        if action == NETWORK:
            _host, found = policy.address_of(url_in(target) or target)
            if found in policy.LIMITED:
                band = _raise(band, Risk.CRITICAL)
                reasons.append("risk_reason_address_limited")
            else:
                reasons.append("risk_reason_address_public")
        if action == WRITE_THAT_RUNS:
            reasons.append("risk_reason_runs_later")
        elif action == WRITE and is_configuration(target):
            band = _raise(band, Risk.MEDIUM)
            reasons.append("risk_reason_configuration")
        if operation is Operation.EXTERNAL_SIDE_EFFECT:
            reasons.append("risk_reason_leaves_machine")
        if operation is Operation.DESTRUCTIVE:
            reasons.append("risk_reason_irreversible")
        # A domain raises a band only where something is being changed. Reading `LoginService.java` to
        # answer a question is not a risk event, and a tool that called every glance at an auth file
        # `high` would spend the attention the one real change needs.
        if action != READ and (is_sensitive(target) or is_sensitive(task)):
            band = _raise(band, Risk.HIGH)
            reasons.append("risk_reason_sensitive_domain")
        return band, tuple(reasons)

    def assess(self, verb_or_action: str, target: str = "", task: str = "") -> Assessment:
        """Rate one act, named either by its tool verb or by its `policy` class.

        An act with no class is the one case where band and verdict are decided together rather than
        independently, and both go the same way: `CRITICAL` and DENY. A tool that cannot be read is not a
        harmless tool, and a band that called it `low` would be the calmest possible answer to the one
        question that deserves the loudest.
        """
        action = self.action_for(verb_or_action, target)
        if not action:
            return Assessment(risk=Risk.CRITICAL, operation=Operation.SECURITY_SENSITIVE,
                              action=str(verb_or_action or ""), verdict=DENY,
                              reasons=("risk_reason_unclassified",),
                              paths=(str(target or ""),) if target else ())
        operation = self.operation_for(action, target)
        verdict = self.verdict(action)
        band, reasons = self.band_for(operation, action, target, task)
        band = settle(band, verdict)
        writes = action in (WRITE, WRITE_THAT_RUNS, DELETE)
        return Assessment(risk=band, operation=operation, action=action,
                          verdict=verdict, reasons=reasons,
                          paths=(str(target or ""),) if target else (), files=1 if writes else 0)

    def assess_changes(self, changes: Iterable[Any], task: str = "") -> Assessment:
        """Rate a whole proposal the way a person has to read it: worst act, not average act.

        Reads the proposal's own file list, the same source `policy.runs_later_paths` uses, so the card and
        the ask cannot disagree about which files were in it. A change whose `delete` is set is a delete
        even when its path names an ordinary DTO, because the class of the act is what the table answers
        for and the path only says which file.
        """
        items = [change for change in (changes or []) if isinstance(change, dict)]
        if not items:
            return Assessment(risk=Risk.LOW, operation=Operation.SAFE_READ, action=READ,
                              verdict=self.verdict(READ), paths=(), files=0)
        verdicts: list[str] = []
        reasons: list[str] = []
        paths: list[str] = []
        acts: list[tuple[Risk, Operation, str, str]] = []
        for change in items:
            path = str(change.get("path") or "")
            paths.append(path)
            action = DELETE if change.get("delete") else policy.write_action(path)
            operation = ACTION_OPERATION[action]
            verdict = self.verdict(action)
            band, why = self.band_for(operation, action, path, task)
            acts.append((settle(band, verdict), operation, action, verdict))
            verdicts.append(verdict)
            for reason in why:
                if reason not in reasons:
                    reasons.append(reason)
        if len(items) > MAX_FILES_BEFORE_MEDIUM:
            reasons.append("risk_reason_many_files")
        worst_verdict_found = worst_verdict(verdicts)
        # The act reported is the one carrying the worst band, so a proposal of nine DTO writes and one
        # `pom.xml` names the `pom.xml` — the answer the person is being asked to look at. The whole
        # proposal's band is then raised by what the table cannot see at all (that many files at once) and
        # finally to whatever its strictest verdict requires, because a card that reads `medium` beside an
        # ask is how asks get clicked through.
        band, operation, action, _ = max(acts, key=lambda act: _BANDS.index(act[0]))
        if len(items) > MAX_FILES_BEFORE_MEDIUM:
            band = _raise(band, Risk.MEDIUM)
        return Assessment(risk=settle(band, worst_verdict_found), operation=operation, action=action,
                          verdict=worst_verdict_found, reasons=tuple(reasons),
                          paths=tuple(paths), files=len(items))


def _raise(band: Risk, floor: Risk) -> Risk:
    """The more alarming of two bands."""
    return band if _BANDS.index(band) >= _BANDS.index(floor) else floor


URL_SHAPE = re.compile(r"https?://\S+")


def url_in(target: Any) -> str:
    """The first URL inside a target, which may be a whole command line.

    `policy.address_of` reads a URL; handing it `curl -s https://host/x` would answer `unknown` about the
    command's shape rather than about its destination, and `unknown` escalates.
    """
    found = URL_SHAPE.search(str(target or ""))
    return found.group(0) if found else ""


# The rating every caller who has no folder answers uses.
DEFAULT = RiskPolicy()


def assess(verb_or_action: str, target: str = "", task: str = "",
           overrides: Mapping[str, str] | None = None) -> Assessment:
    """Rate one act; `overrides` is a folder's own table answers, when the caller has them."""
    policy_ = RiskPolicy(overrides) if overrides else DEFAULT
    return policy_.assess(verb_or_action, target, task)


def assess_changes(changes: Iterable[Any], task: str = "",
                   overrides: Mapping[str, str] | None = None) -> Assessment:
    """Rate one proposal; the same worst-act rule whether or not a folder opened anything."""
    policy_ = RiskPolicy(overrides) if overrides else DEFAULT
    return policy_.assess_changes(changes, task)
