"""The one place that decides whether anything may run, whatever supplied the tool.

Before this module the question had two halves answered in two places: `tools.ToolRegistry._admit`
asked the folder's policy for the class a call belongs to, and `contracts.SandboxGuard.check` asked
the run's posture for the rung it reaches. That was complete while every tool in the vocabulary was
one this runtime wrote — the eight in `tools.BUILTIN_CONTRACTS` name their own class and their own
rung, and nothing else could be registered except by a caller that had already decided. A provider
whose tools arrive from *outside* breaks the assumption in the one direction that matters: the shape,
the class and the level are then claims made by somebody else, and a gate that trusts a claim it was
only ever built to audit is a gate that opens on a lie.

So this module holds the whole admission decision, and it holds it once. `admit` runs the judgements
in the order their refusals are worth stating — the envelope's shape, then the contract's
judgeability, then the folder's rule, then the run's posture — and adds the fourth the old pair had
no reason to carry: the provenance of the contract. A tool that arrives from outside is judged under
the class the *runtime* would assign to the rung it reaches, as well as under the class it declares
for itself, and the stricter answer wins. `LADDER_FLOOR` is that assignment, and it is deliberately
not negotiable by a provider: declaring `read` for a tool that reaches `network` narrows nothing,
because the declaring is the thing being distrusted. `Execute` and `network` on an unmanaged call
resolve to `execute_custom`, which the table answers ASK for and a task run therefore refuses — the
same posture `_admit` has always taken for a check nobody is there to approve, and the one
`policy.CONFIGURABLE_ACTIONS` leaves open to a folder that wants outside tools on.

`allows` is the same judgement with the envelope left out, and it exists so `capabilities.offerable`
can stop holding a private copy of these rules. A pre-flight is still only a pre-flight: what a
folder allows can change between building a request and executing one of its answers, so `admit`
re-runs on the live call and no caller may treat an offer as a pardon.

Nothing here executes anything, reads a file, or knows what a provider is. The refusals are the
taxonomy's (`PolicyDenied`, `SandboxDenied`, `MalformedCall`) and the sentences are the ones the
registry has always given a model, because a refusal a caller has to re-learn per subsystem is a
refusal it will re-make.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import contracts, policy
from .contracts import Call, MalformedCall, PolicyDenied, SandboxGuard, ToolContract

__all__ = [
    "NATIVE", "EXTERNAL", "KINDS",
    "Provenance", "native", "external",
    "OPEN_GUARD", "LADDER_FLOOR", "SecurityGate", "gate_for", "NATIVE_GATE",
]

# The two kinds of provenance a contract can arrive under. The distinction is not who wrote the
# handler — every handler is code somebody shipped — it is who decided what the call *means*: for a
# native tool the runtime wrote both the fields and the command, and for an external one a third
# party wrote the tool and the model's own text becomes its arguments.
NATIVE, EXTERNAL = "native", "external"
KINDS = (NATIVE, EXTERNAL)


# The execution posture the planning loop runs under: every rung the ladder has is inside what the
# policy table already judges, so the guard refuses only what a tighter caller hands it. Named here
# so the gate and `tools` state the planning posture once — two copies is how a default drifts.
OPEN_GUARD = SandboxGuard(max_side_effect=contracts.LEVEL_NETWORK)


def _rung(level: str) -> int:
    """The ladder's position of one level, with the level checked on the way past."""
    if level not in contracts.SIDE_EFFECT_LEVELS:
        raise ValueError("Unknown side-effect level: " + str(level))
    return contracts.SIDE_EFFECT_LEVELS.index(level)


@dataclass(frozen=True)
class Provenance:
    """Where a tool came from, as the gate needs it stated: an id and a kind.

    `provider` is the name the refusal and the audit row carry, and it is the only part a reader
    outside this module can act on — "the folder denies network" does not say who asked for it, and
    with two providers in one vocabulary that is the difference between a rule and a suspect.
    `managed` is derived rather than declared, because a provider that could set it false on a native
    tool, or true on an external one, would be choosing its own ceiling by describing itself.
    """

    provider: str = "native"
    kind: str = NATIVE

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError("Unknown provider kind: " + str(self.kind))
        if not str(self.provider or "").strip():
            raise ValueError("A provenance has to name the provider it came from")

    @property
    def managed(self) -> bool:
        """Whether the runtime chose what runs, rather than forwarding the caller's text to someone else."""
        return self.kind == NATIVE

    @property
    def external(self) -> bool:
        return self.kind == EXTERNAL


# Shorthands, because a gate that has to be told twice what kind it is judging is a gate whose calls
# read as configuration rather than as a decision.
def native(provider: str = "native") -> Provenance:
    return Provenance(provider, NATIVE)


def external(provider: str) -> Provenance:
    return Provenance(provider, EXTERNAL)


# The class the runtime assigns to each rung of the ladder when it cannot trust the declaration. The
# rungs keep their own names in the ladder's order, so this reads as what it is: a map from "how
# invasive is this" to "which of the folder's rules governs it". `write` resolves to
# `write_that_runs` rather than `write` on purpose — a tool this runtime did not write can touch a
# build file, and the plan's one rule about writes that are really later executions is that they are
# asked about, not assumed. A declared class is still asked *beside* this one; the floor never
# replaces it, because a tool that declared `git_local` and reaches `read` should be refused when the
# folder denies git, not when it denies reading.
LADDER_FLOOR: dict[str, str] = {
    contracts.LEVEL_NONE: policy.READ,
    contracts.LEVEL_READ: policy.READ,
    contracts.LEVEL_WRITE: policy.WRITE_THAT_RUNS,
    contracts.LEVEL_EXECUTE: policy.EXECUTE_CUSTOM,
    contracts.LEVEL_NETWORK: policy.EXECUTE_CUSTOM,
}


@dataclass(frozen=True)
class SecurityGate:
    """The admission decision for one provenance: shape, folder rule, run posture — and only then,
    a handler.

    `max_external_side_effect` is the operator's cap on what an outside tool may even be *considered*
    for, and it is the reason the field is on the gate rather than left to the run's guard: a caller
    may legitimately plan a build (a native `run_tests` reaching `execute`) while refusing to let a
    third-party binary do the same thing. The two questions have different answers for the same rung,
    so they need two ceilings, and an unmanaged contract is measured against both.
    """

    provenance: Provenance = Provenance()
    max_external_side_effect: str = contracts.LEVEL_NETWORK

    def __post_init__(self) -> None:
        _rung(self.max_external_side_effect)

    # -- the judgements ----------------------------------------------------

    def classes(self, contract: ToolContract) -> tuple[str, ...]:
        """The classes this call must be answered for, in the order they are asked.

        Declared first, floored second, deduplicated: the declared class is the provider's own
        sentence about what it does and the folder may deny it for a reason the floor would not name,
        while the floor is what this runtime will not be talked out of. A native contract answers only
        for what it declared — the classes the built-in tools run under are managed, and escalating
        them would be a second opinion about a call this code already writes.
        """
        asked: list[str] = []
        declared = str(contract.policy_action or "")
        if declared:
            asked.append(declared)
        if self.provenance.external:
            floor = LADDER_FLOOR[contract.side_effect]
            if floor not in asked:
                asked.append(floor)
        return tuple(asked)

    def audit(self, contract: ToolContract) -> None:
        """Refuse a contract this gate cannot judge, before judging anything about it.

        Two refusals, both about a declaration rather than a call. A contract reaching above the
        operator's external ceiling is refused here, so it never reaches the model's menu — which is
        the only place a refusal costs nothing — and an unmanaged contract that declares a class
        outside the table's vocabulary is refused too: the folder has no word to answer it with, and
        an unknown answer is not a grant.
        """
        if not self.provenance.external:
            return
        if _rung(contract.side_effect) > _rung(self.max_external_side_effect):
            raise PolicyDenied(
                "This provider's tools are capped at side-effect level '"
                + self.max_external_side_effect + "', and " + contract.name + " reaches '"
                + contract.side_effect + "'. It is not offered at all.")
        declared = str(contract.policy_action or "")
        if declared and declared not in policy.ACTIONS:
            raise PolicyDenied(
                contract.name + " declares a policy class this runtime cannot ask about ('"
                + declared + "'), so it will not run. Do not retry it.")

    def judge(self, contract: ToolContract,
              verdict: Callable[[str], str] = policy.decide) -> None:
        """Ask the folder about every class this call belongs to, and refuse on the first no.

        One question per class, in the order `classes` gives them, and the strictest refusal is the
        one raised rather than the first reached: a denial ends the asking, because a class the folder
        closed is not made better by a second class that merely wants a human. An ask is remembered
        and raised only when nothing denied, since ask is the answer a model can act on (propose, or
        block naming the check) while deny is not. A verdict outside the three falls with the
        denials, the way `policy.decide` answers an override it cannot parse.
        """
        deferred = ""
        for action_class in self.classes(contract):
            answer = verdict(action_class)
            if answer == policy.ALLOW:
                continue
            if answer == policy.ASK:
                deferred = deferred or action_class
                continue
            self._deny(contract, action_class, "denies")
        if deferred:
            self._deny(contract, deferred, "asks before")

    def posture(self, contract: ToolContract, call: Call,
                guard: SandboxGuard = OPEN_GUARD) -> None:
        """Ask the run's posture the question the folder's rule does not answer.

        The guard's own ceiling first — it is the sentence the loop has always produced for an
        overreaching rung, and path confinement belongs to it — then the operator's cap on outside
        tools, which is a second and possibly lower ceiling only this gate knows about.
        """
        guard.check(contract, call)
        if self.provenance.external and \
                _rung(contract.side_effect) > _rung(self.max_external_side_effect):
            raise contracts.SandboxDenied(
                contract.name + " reaches side-effect level '" + contract.side_effect
                + "', above the '" + self.max_external_side_effect + "' ceiling this run sets for "
                + self.provenance.provider + " tools.")

    # -- the entry points --------------------------------------------------

    def admit(self, contract: ToolContract, envelope: dict[str, Any], *,
              verdict: Callable[[str], str] = policy.decide,
              guard: SandboxGuard = OPEN_GUARD,
              refusal: Callable[[dict, str], str] | None = None) -> Call:
        """The whole decision, in one call: judge the sentence, then judge letting it be said here.

        Shape first, always: a mistyped envelope is the caller's mistake about its own call, and
        answering it with a refusal about a policy class teaches the model a rule it never tried to
        break. `refusal` is the caller's chance to restate a shape complaint as its own menu — only
        the vocabulary a run is being driven against can list it, and the gate deliberately does not
        know that vocabulary. Then the contract is audited, the folder is asked, and the posture is
        checked, so a handler is reached only by a call that cleared all four.

        Raises the taxonomy's refusals, and returns the canonical `Call` for everything else.
        """
        try:
            call = contract.validate(envelope)
        except MalformedCall as exc:
            if refusal is None:
                raise
            raise MalformedCall(refusal(envelope, str(exc))) from None
        self.audit(contract)
        self.judge(contract, verdict)
        self.posture(contract, call, guard)
        return call

    def allows(self, contract: ToolContract, *,
               verdict: Callable[[str], str] = policy.decide,
               guard: SandboxGuard = OPEN_GUARD) -> bool:
        """Whether a call of this contract *could* run here — a pre-flight, with no envelope.

        `capabilities.offerable` needs the answer as a bool rather than as an exception, and it needs
        the same answer the live call will get, which is why this is written through `audit`, `judge`
        and the guard's own level comparison rather than beside them: a pre-flight with its own rules
        is a second authority, and the drift between two authorities is the bug this whole module
        exists to prevent. Paths are not judged here, because there is no call to judge them on; that
        half stays with `admit`, which every real call still passes through.
        """
        try:
            self.audit(contract)
            self.judge(contract, verdict)
            if _rung(contract.side_effect) > _rung(guard.max_side_effect):
                return False
            if self.provenance.external and \
                    _rung(contract.side_effect) > _rung(self.max_external_side_effect):
                return False
        except (PolicyDenied, contracts.SandboxDenied):
            return False
        return True

    def _deny(self, contract: ToolContract, action_class: str, said: str) -> None:
        """The refusal the loop has always given, with this call's provider named into it.

        Kept word-for-word where the registry used to build it, because a model learns these two
        sentences and a reader of a session row reads them back hours later. The provider rides in
        only when it is not the runtime's own, so a native refusal is unchanged.
        """
        whose = "" if self.provenance.managed else " from " + self.provenance.provider
        if said == "asks before":
            raise PolicyDenied(
                "The folder's policy asks before " + action_class + ", and nobody is typing "
                "during a task run: " + contract.name + whose + " will not start. Do not retry it; "
                'propose the change, or return action="blocked" saying that check needs the operator.')
        raise PolicyDenied(
            "The folder's policy denies " + action_class + ": " + contract.name + whose
            + " will not run in it. Do not retry it; base the answer on what you have already read, "
            + 'or return action="blocked" if the task truly needs that check.')


def gate_for(provenance: Provenance,
             max_external_side_effect: str = contracts.LEVEL_NETWORK) -> SecurityGate:
    """The gate one provider is judged under."""
    return SecurityGate(provenance=provenance,
                        max_external_side_effect=max_external_side_effect)


# The gate the built-in vocabulary runs under: native provenance, and no cap beyond the ladder
# itself — which is the whole point of a native tool, that the runtime that wrote it is the runtime
# that judges it.
NATIVE_GATE = SecurityGate(provenance=native())
