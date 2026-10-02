"""What kind of action a thing is, and where a network request is aimed.

Four places already refuse things, each with its own idea of why: `workspace.py` gates a path,
`runner.py` gates a command name, `config.py` gates a provider URL, `git_integration.py` simply has no
push in it. None of them knows the word for the *class* of the action, so nothing in the tool can answer
the question the operator asks — "will you ask me before you do that?" — without reading four modules.

Two things are decided here and nothing else. The first is that a proposed change to `.ai_project.json`
is not the same kind of act as a proposed change to a DTO, because the tool reads that file back as the
command to run (`service_runner.py` calls it `CONFIG_FILE`); a write that changes what will execute later
is its own class. The second is where a request target actually is — because a scheme check answers what
a string looks like, and the one button that sends a request (`run_api_test`) was trusting it, so
`address_class` answers the destination question in codes, next to the table that decides what to do about
them. Every current command and network entry point is operator-triggered; task-originated policy belongs
at a task-originated execution boundary if one is added later.

The table is code, the verdicts are codes, and the sentences are not here — a reason is a `labels` key
like every other thing the tool says. Overrides are remembered by `permissions.py`, which is the file
this module can be read without.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

ALLOW, ASK, DENY = "allow", "ask", "deny"
VERDICTS = (ALLOW, ASK, DENY)

READ = "read"
WRITE = "write"
WRITE_THAT_RUNS = "write_that_runs"
DELETE = "delete"
EXECUTE_RECIPE = "execute_recipe"
EXECUTE_CUSTOM = "execute_custom"
GIT_LOCAL = "git_local"
NETWORK = "network"

ACTIONS = (READ, WRITE, WRITE_THAT_RUNS, DELETE, EXECUTE_RECIPE, EXECUTE_CUSTOM, GIT_LOCAL, NETWORK)
# Only these classes currently have a decision point that reads the folder's policy. The other
# classes remain in TABLE as vocabulary for safeguards implemented elsewhere, but must not be
# presented as overrides that a folder can set without changing behavior.
CONFIGURABLE_ACTIONS = (WRITE_THAT_RUNS, EXECUTE_CUSTOM, NETWORK)
MANAGED_ACTIONS = (READ, WRITE, DELETE, EXECUTE_RECIPE, GIT_LOCAL)

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

# The default answer for each action class. Actor-specific answers are intentionally absent until an
# action can actually be initiated by more than the operator and has a distinct enforcement boundary.
TABLE: dict[str, str] = {
    READ: ALLOW,
    WRITE: ALLOW,
    WRITE_THAT_RUNS: ASK,
    DELETE: ASK,
    EXECUTE_RECIPE: ALLOW,
    EXECUTE_CUSTOM: ASK,
    GIT_LOCAL: ALLOW,
    NETWORK: ASK,
}


def write_action(path: str) -> str:
    """The class of a proposed write: the ordinary one, or the one that changes what runs later.

    Matched on the last path component, case-folded, because a reactor writes `buildSrc/build.gradle`
    and a Windows caller hands back backslashes. A directory named `Dockerfile` is not a thing this tool
    writes, so the basename is the whole rule.
    """
    name = str(path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].casefold()
    return WRITE_THAT_RUNS if name in RUNS_LATER else WRITE


def decide(action: str, override: str = "") -> str:
    """The default verdict for one action class, unless the folder overrode it.

    An unknown class or an override whose value is not a verdict answers DENY: a permission store that
    falls open is a store that grants what it cannot read. Actor-specific policies belong at an actual
    actor-aware execution boundary; current command and network entry points are operator actions.
    """
    verdict = TABLE.get(str(action or ""))
    if verdict is None:
        return DENY
    # A folder may answer an ASK differently, but a stored override can never open a hard DENY.
    if verdict == DENY:
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


# Where a request target is, as far as a string can say. `unknown` is not a failure to read: it is the
# answer for a host that is neither a name nor a readable address — an empty authority, or a decimal form
# like `2130706433` or `0177.0.0.1` that some stacks still route to loopback — and it escalates, because
# an address this tool cannot read is not a public host.
LOOPBACK, LINK_LOCAL, PRIVATE, PUBLIC, NAMED, UNKNOWN = (
    "loopback", "link_local", "private", "public", "named", "unknown")
ADDRESS_CLASSES = (LOOPBACK, LINK_LOCAL, PRIVATE, PUBLIC, NAMED, UNKNOWN)

# The classes that are not this machine's own: a request that lands here leaves the laptop and arrives at
# a neighbour — a private network, a link-local metadata service, or somewhere the tool cannot see.
LIMITED = (LINK_LOCAL, PRIVATE, UNKNOWN)

# A host is a label or a number: decimal-and-dot forms are the ones some stacks still route (`127.1`,
# `0177.0.0.1`, `2130706433`), and so is a written-out hex literal. A word that merely looks like hex is
# still a name — `deadbeef` is a legal label, and a gate that escalates every legal label is a gate that
# teaches the operator to approve without reading.
_NUMERIC_SHAPE = re.compile(r"(?:[0-9.]+|0[xX][0-9a-fA-F]+)")


def address_class(host: str) -> str:
    """One of the six codes, from the host string alone.

    It never resolves a name, and that is the decision rather than an oversight: finding out whether
    `metadata.example` is private is done by sending the request this gate exists to think about first.
    A **name** therefore answers `named` and keeps the verdict the table already gives it. Loopback keeps
    its verdict too, because testing the app you are building is the reason the button exists. CGNAT
    (`100.64.0.0/10`) reads as `public`, because that is what the standard library says and this module
    does not argue with it.
    """
    value = str(host or "").strip().rstrip(".").lower()
    if not value:
        return UNKNOWN
    try:
        found = ipaddress.ip_address(value)
    except ValueError:
        # A host is a label or a number, never a path: a caller that handed over a whole URL by accident
        # is not holding a name, and answering `named` for it would let it through the gate unasked.
        if "/" in value or " " in value:
            return UNKNOWN
        return UNKNOWN if _NUMERIC_SHAPE.fullmatch(value) else NAMED
    if found.is_loopback:
        return LOOPBACK
    if found.is_link_local:
        return LINK_LOCAL
    return PRIVATE if found.is_private else PUBLIC


def address_of(url: str) -> tuple[str, str]:
    """The host a URL would send to, and its class.

    The parsing lives beside the class so a caller cannot hand a whole URL to `address_class` and be
    answered `named` for what is really an address. A URL this library rejects has no readable host, which
    is `unknown`, which escalates.
    """
    try:
        host = urlsplit(str(url or "")).hostname or ""
    except ValueError:
        host = ""
    return host, address_class(host)
