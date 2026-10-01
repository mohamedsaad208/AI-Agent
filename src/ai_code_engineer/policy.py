"""What kind of action a thing is, and who asked for it.

Four places already refuse things, each with its own idea of why: `workspace.py` gates a path,
`runner.py` gates a command name, `config.py` gates a provider URL, `git_integration.py` simply has no
push in it. None of them knows the word for the *class* of the action, so nothing in the tool can answer
the question the operator asks — "will you ask me before you do that?" — without reading four modules.

Two things are decided here and nothing else. The first is that a proposed change to `.ai_project.json`
is not the same kind of act as a proposed change to a DTO, because the tool reads that file back as the
command to run (`service_runner.py` calls it `CONFIG_FILE`); a write that changes what will execute later
is its own class. The second is that the same class has a different answer depending on who asked: a
person pressing a button and a model mid-task are not the same requester, and "unauthorised network" is
only a meaningful phrase once that distinction is in the table.

The table is code, the verdicts are codes, and the sentences are not here — a reason is a `labels` key
like every other thing the tool says. Overrides are remembered by `permissions.py`, which is the file
this module can be read without.
"""
from __future__ import annotations

ALLOW, ASK, DENY = "allow", "ask", "deny"
VERDICTS = (ALLOW, ASK, DENY)

OPERATOR, TASK = "operator", "task"
ORIGINS = (OPERATOR, TASK)

READ = "read"
WRITE = "write"
WRITE_THAT_RUNS = "write_that_runs"
DELETE = "delete"
EXECUTE_RECIPE = "execute_recipe"
EXECUTE_CUSTOM = "execute_custom"
GIT_LOCAL = "git_local"
NETWORK = "network"

ACTIONS = (READ, WRITE, WRITE_THAT_RUNS, DELETE, EXECUTE_RECIPE, EXECUTE_CUSTOM, GIT_LOCAL, NETWORK)

# Files this tool itself reads back as instructions, or that a build it starts will execute. A write to
# any of them is the one write class worth stopping for: the change is not the harm, the *later run* is.
# Deliberately a list of names and not a list of patterns — `.sh` is not here, because nothing in the
# tool runs a shell script it finds; a person does, and a rule that asks on every script write teaches
# the operator to approve without reading, which is the failure this table exists to prevent.
RUNS_LATER = frozenset({
    ".ai_project.json", "package.json", "package-lock.json", "pom.xml", "build.gradle",
    "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "conftest.py", "pytest.ini",
    "tox.ini", "setup.cfg", "makefile", "dockerfile", "docker-compose.yml", "compose.yaml",
})

# (who asked) -> what the table answers. A row is written out in full for both origins so that adding a
# class cannot silently inherit an answer for one of them.
TABLE: dict[str, dict[str, str]] = {
    READ: {OPERATOR: ALLOW, TASK: ALLOW},
    WRITE: {OPERATOR: ALLOW, TASK: ALLOW},
    WRITE_THAT_RUNS: {OPERATOR: ASK, TASK: ASK},
    DELETE: {OPERATOR: ASK, TASK: ASK},
    EXECUTE_RECIPE: {OPERATOR: ALLOW, TASK: ALLOW},
    EXECUTE_CUSTOM: {OPERATOR: ASK, TASK: DENY},
    GIT_LOCAL: {OPERATOR: ALLOW, TASK: ALLOW},
    NETWORK: {OPERATOR: ASK, TASK: DENY},
}


def write_action(path: str) -> str:
    """The class of a proposed write: the ordinary one, or the one that changes what runs later.

    Matched on the last path component, case-folded, because a reactor writes `buildSrc/build.gradle`
    and a Windows caller hands back backslashes. A directory named `Dockerfile` is not a thing this tool
    writes, so the basename is the whole rule.
    """
    name = str(path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].casefold()
    return WRITE_THAT_RUNS if name in RUNS_LATER else WRITE


def decide(action: str, origin: str = OPERATOR, override: str = "") -> str:
    """The verdict for one action class, as asked by one requester, unless the folder overrode it.

    Anything this table does not name answers DENY: an unknown class, an unknown origin, and an override
    whose value is not a verdict each say "the rule is not legible, so it does not permit". A permission
    store that falls open is a store that grants what it cannot read.
    """
    row = TABLE.get(str(action or ""))
    if row is None:
        return DENY
    verdict = row.get(str(origin or ""))
    if verdict is None:
        return DENY
    value = str(override or "").strip().lower()
    if not value:
        return verdict
    return value if value in VERDICTS else DENY


def runs_later_paths(changes) -> list[str]:
    """Which paths in a proposal change what this tool will execute later.

    Computed from the proposal's own list rather than from the diff, because the question is which
    *file* the operator is being asked about, and a window that recomputed it from content would end up
    with three slightly different answers for the same proposal.
    """
    return [str(change.get("path", "")) for change in changes or []
            if isinstance(change, dict)
            and write_action(str(change.get("path", ""))) == WRITE_THAT_RUNS]
