"""Outside tools, on the loop's own terms: an optional MCP provider, judged like everything else.

The Model Context Protocol is a way for a third party to advertise tools — name, JSON Schema,
description — and to run them when asked. Nothing about that is safe by construction, and nothing
about it needs a new security model either: the run already has a contract layer that judges an
envelope exactly, a policy table that judges a class of action, a guard that judges a run's posture,
and a gate that judges all three against a contract's provenance. What an outside tool needs is a
*translation* into that vocabulary, and the translation is where every decision in this module lives.

Three decisions shape it, and all three lean the same way.

**The schema is a claim, not a grant.** `contract_for` maps a advertised JSON Schema onto a
`ToolContract`, and it maps only the kinds this runtime can judge a value against: string, enum,
bounded integer, boolean, string array, and the field names that mean a workspace path. A `number`,
a nested object, an unbounded array of anything, a required name the properties do not carry — all
refuse the *tool*, not the call, and the refusal is recorded rather than raised. Guessing a contract
for a shape this layer cannot check would be handing the gate a document the gate then trusts, and the
one failure mode a provider abstraction invites is a gate that grades on the submitted paperwork. The
side-effect rung and the policy class are the same reading: MCP's own `readOnlyHint`/`openWorldHint`
annotations can *lower* what is claimed only by stating so, and an absent or unreadable annotation
resolves to the top of the ladder. `LADDER_FLOOR` in `gate` then escalates whatever an outside tool
declared, so a server describing a network call as a read is judged as both.

**The operator's word, never the repository's.** Servers come from `.agent-mcp.json` in the
tool's own directory, beside `.agent-projects.json` — and deliberately not from a `mcp.json` inside a
project. A repository that could name a command line here would turn "open a folder" into "execute
that folder's contents", which is the exact escalation `policy.RUNS_LATER` exists to stop for
`package.json`. The same rule reads down into the row shape: `env` holds the *names* of variables,
the way `config.api_key_env` does, so a config file that might be pasted into an issue carries no
credential in it, and the child process gets a scrubbed environment built from `runner.ENV_KEYS`
plus those names and nothing else.

**A server that will not answer is a missing tool, not a failed task.** Discovery, execution and
shutdown all degrade to an observation the loop can read — `empty`, or `failed` with a retryable code
— because the alternative is a planning run that ends because somebody's filesystem bridge did not
start. The commands themselves are a fixed argv resolved with `shutil.which` and spawned without a
shell, the same posture `runner.py` states for its own recipes; a model's text reaches the server as
JSON *values*, never as an argv this process assembles from prose.

Framing is the protocol's own: one JSON-RPC 2.0 message per line on the child's stdin and stdout,
with `initialize` before `tools/list`. The stdio transport needs no dependency, and taking none is
what keeps this provider optional in the sense that matters — an agent that never configures a server
never imports a subprocess call.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from . import contracts, gate, policy, refusals, runner, tool_provider, tools
from .contracts import Call, ContractError, Field, Result, ToolContract
from .errors import AgentError
from .redaction import redact

__all__ = [
    "PROTOCOL_VERSION", "CONFIG_NAME", "MAX_SERVERS",
    "McpUnavailable", "McpTimeout", "UnsupportedTool",
    "McpServerSpec", "load_specs",
    "McpClient", "contract_for", "McpToolProvider", "providers_for",
    "providers_from_config",
]

# The protocol revision this client speaks. A server answering with a different one is answered by
# the version it named being *recorded*, not by a refusal: `initialize` is a negotiation, and a
# client that fails a run because a third party shipped a different revision is a client that gave
# the third party the veto.
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "ai-code-engineer", "version": "1"}

# Where an operator says which servers exist. A dotfile beside `.agent-projects.json`, in the tool's
# own directory and never inside the workspace the task is pointed at — see the module docstring.
CONFIG_NAME = ".agent-mcp.json"
MAX_SERVERS = 8

# The bounds a row and a schema are held to. Every one of these is a ceiling on what a third party can
# ask this process to spend: a server list of hundreds is a vocabulary nobody can read, an argument
# list of thousands is a payload, and a description of megabytes is a prompt-injection budget spent
# before the first tool ran.
MAX_ID_CHARS = 32
MAX_COMMAND_CHARS = 260
MAX_ARGS = 64
MAX_ARG_CHARS = 4096
MAX_ENV_NAMES = 32
MAX_TOOL_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 600
MAX_FIELDS = 16
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_OUTPUT_CHARS = 8000
MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS = 1, 300
MAX_PROTOCOL_MESSAGE_CHARS = 200_000

_ID = re.compile(r"[a-z][a-z0-9_-]*")
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.-]+")
# The same shape `config.ENV_NAME` states for `api_key_env`, restated rather than imported because
# this module is read by whoever has not configured a provider and importing `config` would pull the
# provider table in with it: a variable's *name*, never its value.
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.-]+")
# An executable, not a command line: no space that would hide an argument, no shell metacharacter
# that would need one. A path with directories in it is allowed (`./bin/server`) because an operator
# typing it is the whole authorisation there is for it.
_UNSAFE_COMMAND = re.compile(r"[&|<>^%;$`\n\r]")

# The envelope keys the layers above this one consume before a contract ever sees them. A field named
# like one of these would be silently eaten — `state` is popped by the loop, `args` unwrapped,
# `reason` read as the sender's sentence, and every `contracts.TASK_STATE_KEYS` name lifted out as
# the model's working state — and a tool whose documented argument vanishes before the
# handler is a tool that lies about its own contract.
RESERVED_FIELDS = frozenset({"action", "args", "parameters", "reason", "thought",
                             "state", "trace_id", "call_id", "changes", "content"}
                            | set(contracts.TASK_STATE_KEYS))

# The names that mean "this value is a place in the workspace", and so get `PATH` rather than
# `STRING`. A guess from a name, and deliberately a guess that can only *tighten*: a `PATH` field is
# traversal-checked and confined by the run's guard, and a server tool that genuinely needed an
# absolute path outside the folder is then refused instead of obeyed.
PATH_FIELDS = frozenset({"path", "file", "file_path", "filepath", "filename", "relative_path",
                         "dir", "directory", "folder"})


class McpUnavailable(ContractError):
    """No server is answering: it did not start, it closed, or it refused the method.

    Retryable by class — a process that is not up yet may be up next turn — which is why this is a
    distinct failure from a call that came back with an error *inside* it.
    """

    code = "mcp_unavailable"
    advice = ("The outside tool's server did not answer. Do not retry it in the same turn; read "
              "the files and propose the change the task needs: " + refusals.PROPOSE_SHAPE)


class McpTimeout(ContractError):
    """The server owed an answer when its deadline closed."""

    code = contracts.TIMEOUT
    advice = ("The outside tool ran out of time. Ask for a smaller call, or decide from the "
              "files instead of waiting on it again.")


class UnsupportedTool(ContractError):
    """This server advertises a tool this runtime cannot write a contract for.

    Not retryable and not the caller's fault: the fix is in the row or in the server, and the model
    cannot see either. The tool is therefore *not offered at all*, which is the only answer that
    leaves a menu a model can actually use.
    """

    code = "mcp_unsupported"
    advice = "This tool is not offered; choose one in the menu."


# ---------------------------------------------------------------- the operator's rows

def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


class McpServerSpec:
    """One server, as the operator described it — validated on the way in, not on the way out."""

    def __init__(self, id: str, command: str, *, args: Sequence[str] = (),
                 env: Sequence[str] = (), tools: Sequence[str] = (),
                 enabled: bool = False, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
                 max_output_chars: int = DEFAULT_OUTPUT_CHARS,
                 max_side_effect: str = contracts.LEVEL_NETWORK,
                 label: str = ""):
        self.id = id
        self.command = command
        self.args = tuple(args)
        self.env = tuple(env)
        self.tools = tuple(tools)
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.max_side_effect = max_side_effect
        self.label = label or id

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, McpServerSpec):
            return NotImplemented
        return self.__dict__ == other.__dict__

    def __repr__(self) -> str:
        # The env *names* are safe to say; a value never was in the file to begin with.
        return ("McpServerSpec(" + self.id + " " + self.command
                + " enabled=" + str(self.enabled) + ")")

    def argv(self, resolved: str) -> list[str]:
        return [resolved, *self.args]

    def child_env(self) -> dict:
        """The scrubbed base, plus exactly the variables this row names.

        `runner.ENV_KEYS` is the same allowlist the build recipes run under, and the row adds names
        rather than values: a secret reaches the child from this process's own environment, which is
        where the operator put it, and never from a file that gets committed by accident.
        """
        base = {key: value for key in runner.ENV_KEYS
                if (value := os.environ.get(key)) is not None}
        for name in self.env:
            value = os.environ.get(name)
            if value is not None:
                base[name] = value
        return base

    @classmethod
    def from_dict(cls, raw: dict, *, position: int = 0) -> "McpServerSpec":
        """One row, or the sentence saying why there is no row.

        Every refusal here is a refusal to *run* something: an unknown key means the row was not
        written against this version, a command with a metacharacter in it means somebody meant a
        shell, and a value in `env` means a credential arrived in a file that is read by whoever can
        read the config. Dropping such a row with a reason is the fail-closed answer; starting the
        process it half-describes is not.
        """
        if not isinstance(raw, dict):
            raise AgentError("MCP server #" + str(position) + " is not an object.")
        unknown = sorted(set(raw) - {"id", "command", "args", "env", "tools", "enabled",
                                    "timeout_seconds", "max_output_chars",
                                    "max_side_effect", "label"})
        if unknown:
            raise AgentError("MCP server #" + str(position) + " has unknown field(s): "
                             + ", ".join(unknown))
        server_id = _text(raw.get("id")).strip().lower()
        if not _ID.fullmatch(server_id) or len(server_id) > MAX_ID_CHARS:
            raise AgentError("MCP server id must be a lowercase name of at most "
                             + str(MAX_ID_CHARS) + " characters (server #" + str(position) + ").")
        command = _text(raw.get("command")).strip()
        if not command or len(command) > MAX_COMMAND_CHARS or _UNSAFE_COMMAND.search(command) \
                or "\x00" in command:
            raise AgentError("MCP server " + server_id + " names no safe executable to run.")
        args = raw.get("args", [])
        if not isinstance(args, list) or len(args) > MAX_ARGS \
                or any(not isinstance(item, str) or len(item) > MAX_ARG_CHARS for item in args):
            raise AgentError("MCP server " + server_id + " args must be a list of at most "
                             + str(MAX_ARGS) + " short strings.")
        names = raw.get("env", [])
        if not isinstance(names, list) or len(names) > MAX_ENV_NAMES \
                or any(not isinstance(item, str) or not _ENV_NAME.fullmatch(item)
                       for item in names):
            raise AgentError("MCP server " + server_id
                             + " env must name variables, never hold their values.")
        wanted = raw.get("tools", [])
        if not isinstance(wanted, list) or any(not isinstance(item, str) or not item
                                               or len(item) > MAX_TOOL_NAME_CHARS
                                               for item in wanted):
            raise AgentError("MCP server " + server_id + " tools must be a list of tool names.")
        timeout = raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if type(timeout) is not int or not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
            raise AgentError("MCP server " + server_id + " timeout_seconds must be between "
                             + str(MIN_TIMEOUT_SECONDS) + " and " + str(MAX_TIMEOUT_SECONDS) + ".")
        ceiling = raw.get("max_output_chars", DEFAULT_OUTPUT_CHARS)
        if type(ceiling) is not int or not 200 <= ceiling <= 200_000:
            raise AgentError("MCP server " + server_id
                             + " max_output_chars must be between 200 and 200000.")
        level = _text(raw.get("max_side_effect")).strip().lower() or contracts.LEVEL_NETWORK
        if level not in contracts.SIDE_EFFECT_LEVELS:
            raise AgentError("MCP server " + server_id + " max_side_effect must be one of: "
                             + ", ".join(contracts.SIDE_EFFECT_LEVELS) + ".")
        return cls(server_id, command, args=tuple(args), env=tuple(names),
                   tools=tuple(wanted), enabled=raw.get("enabled") is True,
                   timeout_seconds=timeout, max_output_chars=ceiling,
                   max_side_effect=level, label=_text(raw.get("label")).strip()[:120])


def load_specs(path: Path) -> tuple[list[McpServerSpec], list[dict]]:
    """The operator's servers, and the rows that could not be read as servers.

    A missing file is the ordinary answer and says nothing went wrong: no servers, no problems. An
    unreadable file or a broken row is reported as a problem and skipped rather than raised — a task
    that has nothing to do with MCP must still run, and a row nobody can read starts no process.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], []
    except (OSError, ValueError):
        return [], [{"server": "", "reason": "the MCP configuration is unreadable or not valid JSON"}]
    rows = data.get("servers") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return [], [{"server": "", "reason": "MCP configuration must hold a list of servers"}]
    specs, problems = [], []
    if len(rows) > MAX_SERVERS:
        # The extra rows start nothing and say so: a file that grows past the ceiling is read as far
        # as it can be honoured, rather than failing a task that needed none of it.
        problems.append({"server": "", "reason": "more servers than this runtime will read"})
        rows = rows[:MAX_SERVERS]
    seen: set[str] = set()
    for position, raw in enumerate(rows):
        try:
            spec = McpServerSpec.from_dict(raw, position=position)
        except AgentError as exc:
            problems.append({"server": "", "reason": str(exc)})
            continue
        if spec.id in seen:
            problems.append({"server": spec.id, "reason": "two servers share one id"})
            continue
        seen.add(spec.id)
        if spec.enabled:
            specs.append(spec)
    return specs, problems


# ---------------------------------------------------------------- the client

class McpClient:
    """One server process, one line-framed JSON-RPC 2.0 conversation.

    The reader is a thread and the calls are `queue.get(timeout=...)` because a stdio server that
    hangs must cost this run a timeout and not a wedged loop, and `select` on a pipe is not a
    portable answer on the platform this tool is used on most. Anything the server sends that is not
    an answer to a request this client made is either a notification (dropped) or a request (answered
    with `method not found`, so the server's own writer does not block waiting for a reply that is
    never coming).
    """

    def __init__(self, spec: McpServerSpec, *, cwd: Path | None = None,
                 spawn: Callable[[list[str]], subprocess.Popen] | None = None):
        self.spec = spec
        self.cwd = Path(cwd) if cwd else None
        self._spawn = spawn or self._popen
        self._proc: subprocess.Popen | None = None
        self._replies: queue.Queue = queue.Queue()
        self._reader: threading.Thread | None = None
        self._next_id = 0
        self.server_info: dict[str, Any] = {}

    # -- the process -------------------------------------------------------

    def _popen(self, argv: list[str]) -> subprocess.Popen:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, cwd=str(self.cwd) if self.cwd else None,
                                env=self.spec.child_env(), shell=False,
                                text=True, encoding="utf-8", errors="replace",
                                line_buffering=True, creationflags=flags)

    def start(self) -> dict:
        """Launch, then `initialize`. A server this process cannot speak to is reported, not raised.

        The executable is resolved with `shutil.which` from the row's own name — the same posture
        `runner` states for its recipes: a constant argv, no shell, and nothing assembled from text a
        model wrote.
        """
        if self._proc is not None:
            return self.server_info
        resolved = shutil.which(self.spec.command) or (
            self.spec.command if ("/" in self.spec.command or "\\" in self.spec.command
                                  or os.path.sep in self.spec.command) else "")
        if not resolved or not os.path.isfile(resolved):
            raise McpUnavailable("The MCP server " + self.spec.id + " names an executable that "
                                  "is not installed: " + self.spec.command)
        try:
            self._proc = self._spawn(self.spec.argv(resolved))
        except OSError as exc:
            raise McpUnavailable("The MCP server " + self.spec.id + " did not start: "
                                  + redact(str(exc))[:160]) from exc
        self._reader = threading.Thread(target=self._pump, daemon=True,
                                       name="mcp-reader-" + self.spec.id)
        self._reader.start()
        self.server_info = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO})
        self.notify("notifications/initialized", {})
        return self.server_info

    def close(self) -> None:
        """Stop the child and its tree. This never raises: it runs on the way out of a run."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        runner.kill_tree(proc)
        try:
            proc.wait(timeout=5)
        except (subprocess.SubprocessError, OSError):
            pass

    # -- the conversation --------------------------------------------------

    def _pump(self) -> None:
        """Every line the child writes, parsed or dropped, until it closes."""
        proc = self._proc
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if len(line) > MAX_PROTOCOL_MESSAGE_CHARS:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if "id" in message and "method" in message:
                    self._refuse(message)
                    continue
                self._replies.put(message)
        except (OSError, ValueError):
            pass
        finally:
            self._replies.put(None)          # closed: every waiter wakes to the fact

    def _refuse(self, message: dict) -> None:
        try:
            self._write({"jsonrpc": "2.0", "id": message.get("id"),
                         "error": {"code": -32601, "message": "method not supported"}})
        except McpUnavailable:
            pass

    def _write(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpUnavailable("The MCP server " + self.spec.id + " is not running.")
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpUnavailable("The MCP server " + self.spec.id
                                 + " closed before the request was written: "
                                 + redact(str(exc))[:160]) from exc

    def request(self, method: str, params: dict | None = None,
                timeout: int | None = None) -> dict:
        """One call, and its `result` — or the refusal that says the server did not answer it.

        The deadline is the row's own, so an operator who set fifteen seconds for a slow bridge is not
        overruled by a default. A reply for a different id is dropped rather than returned: a server
        that answers out of order has not answered *this* call.
        """
        self._next_id += 1
        call_id = self._next_id
        payload = {"jsonrpc": "2.0", "id": call_id, "method": method,
                   "params": params or {}}
        self._write(payload)
        deadline = timeout or self.spec.timeout_seconds
        while True:
            try:
                message = self._replies.get(timeout=deadline)
            except queue.Empty:
                raise McpTimeout("The MCP server " + self.spec.id + " did not answer " + method
                                 + " within " + str(deadline) + "s.") from None
            if message is None:
                raise McpUnavailable("The MCP server " + self.spec.id + " closed during "
                                     + method + ".")
            if message.get("id") != call_id:
                continue
            if "error" in message:
                error = message.get("error")
                said = redact(str(error.get("message") if isinstance(error, dict) else error))[:200]
                raise McpUnavailable("The MCP server " + self.spec.id + " refused " + method
                                     + ": " + said)
            result = message.get("result")
            return result if isinstance(result, dict) else {"value": result}

    def notify(self, method: str, params: dict | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def list_tools(self) -> list[dict]:
        raw = self.request("tools/list", {"cursor": None}).get("tools")
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})


# ---------------------------------------------------------------- the translation

def contract_for(server_id: str, tool: dict) -> ToolContract:
    """One advertised MCP tool, as a contract this runtime's gate can judge.

    The name is namespaced before anything else, because two providers in one vocabulary will both
    have a tool called `read` and a collision that reaches the menu is a name that means two things.
    The description is capped and carried as the contract's `purpose`, untrusted and labelled as
    untrusted where it is shown — an outside server is being handed the model's attention, and that
    is the reason the shape it may ask for is decided here rather than read from its prose.

    Raises `UnsupportedTool` with the sentence a log and the skip record both need, so a server with a
    shape this layer cannot check loses that tool and nothing else.
    """
    name = _text(tool.get("name")).strip()
    if not name or len(name) > MAX_TOOL_NAME_CHARS or not _TOOL_NAME.fullmatch(name):
        raise UnsupportedTool("MCP tool name is not usable: " + redact(str(name))[:80])
    described = _text(tool.get("description")).strip()[:MAX_DESCRIPTION_CHARS]

    schema = tool.get("inputSchema")
    if not isinstance(schema, dict) or _text(schema.get("type")).lower() not in ("", "object"):
        raise UnsupportedTool(name + ": its input schema is not an object")
    properties = schema.get("properties")
    if properties is None:
        properties = {}
    if not isinstance(properties, dict):
        raise UnsupportedTool(name + ": its input schema properties are not an object")
    if len(properties) > MAX_FIELDS:
        raise UnsupportedTool(name + ": it advertises more fields than this runtime will judge")
    required = schema.get("required", [])
    if not isinstance(required, list):
        raise UnsupportedTool(name + ": its required list is not a list")
    missing = sorted(item for item in required if not isinstance(item, str)
                     or item not in properties)
    if missing:
        # A field the schema says must arrive and never describes is not a contract; and a field
        # whose name is not a string would not survive the exact key checks the gate runs.
        raise UnsupportedTool(name + ": it requires fields it does not describe ("
                              + ", ".join(str(item)[:40] for item in missing) + ")")

    annotations = tool.get("annotations")
    level, action = _effects(annotations)

    fields: list[Field] = []
    for raw_name in sorted(properties):
        if not isinstance(raw_name, str) or not raw_name.isidentifier():
            raise UnsupportedTool(name + ": field " + str(raw_name)[:40]
                                  + " is not a name this runtime can ask a model for")
        if raw_name in RESERVED_FIELDS or raw_name == "action":
            raise UnsupportedTool(name + ": field " + raw_name
                                  + " is reserved by the loop's own envelope")
        prop = properties[raw_name]
        if not isinstance(prop, dict):
            raise UnsupportedTool(name + ": field " + raw_name + " has no usable schema")
        field = _field_for(raw_name, prop, required=raw_name in required)
        if field is None:
            raise UnsupportedTool(name + ": field " + raw_name + " has a type this runtime cannot "
                                  "judge (" + _text(prop.get("type")) + ")")
        fields.append(field)

    contract = ToolContract("mcp_" + server_id + "_" + name, tuple(fields),
                            purpose=described, policy_action=action, side_effect=level)
    return contract


def _effects(annotations: Any) -> tuple[str, str]:
    """The rung and the policy class an outside tool's own annotations establish.

    Read-only is the one claim an annotation can *lower*, and only when it is literally `true`; a
    server that says it touches the world outside, or says it destroys, or says nothing at all, is
    placed at the top of the ladder. That is not pessimism for its own sake: `gate.LADDER_FLOOR`
    escalates an unmanaged contract from the rung it reaches, so a wrong claim from a lazy manifest
    costs that tool a turn and never costs the folder a rule.
    """
    read_only = annotations.get("readOnlyHint") if isinstance(annotations, dict) else None
    open_world = annotations.get("openWorldHint") if isinstance(annotations, dict) else None
    destructive = annotations.get("destructiveHint") if isinstance(annotations, dict) else None
    if open_world is True:
        return contracts.LEVEL_NETWORK, policy.NETWORK
    if destructive is True:
        return contracts.LEVEL_WRITE, policy.WRITE
    if read_only is True:
        return contracts.LEVEL_READ, policy.READ
    return contracts.LEVEL_NETWORK, policy.NETWORK


def _field_for(raw_name: str, prop: dict, *, required: bool) -> Field | None:
    """One JSON Schema property as a typed `Field`, or None when no kind fits.

    Bounds travel with the field whenever the schema states them, because a bound the contract does
    not carry is a bound the handler has to remember — and the point of the contract layer is that
    handlers are not written to survive what validation should have refused.
    """
    kind = _text(prop.get("type")).strip().lower()
    described = _text(prop.get("description")).strip()[:200]
    enum = prop.get("enum")
    values = tuple(item for item in enum if isinstance(item, str)) if isinstance(enum, list) else ()
    if raw_name in PATH_FIELDS and not values:
        return Field(raw_name, contracts.PATH, required=required, description=described)
    if kind == "string":
        return Field(raw_name, required=required, values=values, description=described)
    if kind == "integer":
        return Field(raw_name, contracts.INTEGER, required=required,
                     minimum=_number(prop.get("minimum")), maximum=_number(prop.get("maximum")),
                     description=described)
    if kind == "boolean":
        return Field(raw_name, contracts.BOOLEAN, required=required, description=described)
    if kind == "array":
        items = prop.get("items")
        if not isinstance(items, dict) or _text(items.get("type")).strip().lower() != "string":
            return None
        return Field(raw_name, contracts.STRING_LIST, required=required,
                     min_items=_number(prop.get("minItems")), max_items=_number(prop.get("maxItems")),
                     description=described)
    return None


def _number(value: Any) -> int | None:
    """A stated bound, kept only if it is a whole number — a float bound is a `number` schema's, and
    this layer has no kind for that."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


# ---------------------------------------------------------------- the provider

class McpToolProvider(tool_provider.ToolProvider):
    """One MCP server, as a provider: its advertised tools, contracted, gated, and dispatched.

    Discovery is lazy and cached, because `contracts()` is asked by whoever builds a request and again
    by whoever runs the answer, and spawning a third-party process is not something a menu build
    should do twice — nor at all, when nothing in the run ever asks for one of its tools. A server
    that cannot be reached leaves the provider offering nothing and stating why, which is the same
    shape as a project with no build file: an observation the loop reads, not a task that died.

    `execute` never consults the gate: the composite admits with *this* provider's gate — external
    provenance, `max_external_side_effect` from the row — and hands an already-admitted call down.
    That ordering is what makes the abstraction safe to hand to a third party at all.
    """

    def __init__(self, spec: McpServerSpec, *, cwd: Path | None = None,
                 client: McpClient | None = None,
                 provenance: gate.Provenance | None = None):
        self.spec = spec
        self.cwd = Path(cwd) if cwd else None
        self._client = client
        self._owns_client = client is None
        self._contracts: tuple[ToolContract, ...] | None = None
        self._names: dict[str, str] = {}          # contracted name -> the server's own name
        self.skipped: list[dict] = []
        self.problem = ""
        origin = provenance or gate.external("mcp:" + spec.id)
        # The row's own ceiling is the operator's cap, and it travels on the gate rather than being
        # left to the run's guard: a build may legitimately be planned (`run_tests` reaches `execute`)
        # while the same rung is refused for a third-party binary.
        super().__init__(origin, gate.gate_for(origin, max_external_side_effect=spec.max_side_effect))

    @property
    def id(self) -> str:
        return "mcp:" + self.spec.id

    def client(self) -> McpClient:
        if self._client is None:
            self._client = McpClient(self.spec, cwd=self.cwd)
            self._owns_client = True
        return self._client

    def discover(self) -> tuple[ToolContract, ...]:
        """Ask the server what it has, and keep only what this runtime can write a contract for.

        The allowlist is applied to the server's own names, before any mapping, because the server
        decides what it advertises and the operator decides what this run may ask for — and the two
        answers must not be confused by a namespaced name that already swallowed the first.
        """
        if self._contracts is not None:
            return self._contracts
        out: list[ToolContract] = []
        self.skipped, self.problem = [], ""
        try:
            client = self.client()
            client.start()
            advertised = client.list_tools()
        except (ContractError, AgentError) as exc:
            self.problem = redact(str(exc))[:200]
            self._contracts = ()
            return self._contracts
        wanted = set(self.spec.tools)
        for tool in advertised:
            name = _text(tool.get("name"))
            if wanted and name not in wanted:
                self.skipped.append({"tool": name[:80], "reason": "not in this server's allowlist"})
                continue
            try:
                contract = contract_for(self.spec.id, tool)
            except UnsupportedTool as exc:
                self.skipped.append({"tool": name[:80], "reason": str(exc)[:200]})
                continue
            if contract.name in self._names:
                self.skipped.append({"tool": name[:80],
                                     "reason": "two advertised tools map to one name"})
                continue
            self._names[contract.name] = name
            out.append(contract)
        self._contracts = tuple(out)
        return self._contracts

    def contracts(self) -> tuple[ToolContract, ...]:
        return self.discover()

    def handle(self, contract: ToolContract, call: Call,
               context: "tools.ToolContext") -> Result:
        """Send one admitted call to the server, and translate its answer back into a `Result`.

        The server's own name goes out on the wire; the namespaced name is this vocabulary's only.
        What the tool reports as an error inside an answered call is `failed` with a retryable code —
        the call landed, the work did not — while a call that never landed is `empty` with the reason,
        the way `run_tests` reports a check that could not run rather than a check that failed.
        """
        server_name = self._names.get(call.tool, "")
        if not server_name:
            raise contracts.UnknownTool("This provider has no tool named " + str(call.tool))
        return self._invoke(call, context)

    def _invoke(self, call: Call, context: "tools.ToolContext") -> Result:
        server_name = self._names[call.tool]
        context.progress("Asking " + self.spec.label + " to run " + server_name + "...")
        try:
            payload = self.client().call_tool(server_name, dict(call.args))
        except (McpUnavailable, McpTimeout, ContractError) as exc:
            context.record("tool", name=call.tool, provider=self.id, status="unavailable",
                           reason=str(exc)[:180])
            context.announce(call.tool, label=self.spec.label, detail="")
            return Result(status=contracts.EMPTY,
                          data={"provider": self.id, "tool": server_name, "status": "unavailable",
                                "reason": redact(str(exc))[:200],
                                "next_step": "Decide from the files; this outside check could not run."},
                          advice=("An outside tool that could not run proves nothing about the code. "
                                  "Read the files and propose the smallest change: "
                                  + refusals.PROPOSE_SHAPE))
        text, types = _content(payload)
        cap = self.spec.max_output_chars
        shown = redact(text)
        truncated = len(shown) > cap
        if truncated:
            shown = shown[-cap:]
        structured = payload.get("structuredContent")
        data: dict[str, Any] = {"provider": self.id, "server": self.spec.id,
                                "tool": server_name, "text": shown, "truncated": truncated,
                                "content_types": types}
        if isinstance(structured, dict):
            data["structured"] = structured
        context.record("tool", name=call.tool, provider=self.id,
                       status="failed" if payload.get("isError") else "ok", chars=len(shown))
        context.announce(call.tool, label=self.spec.label, detail=shown)
        if payload.get("isError"):
            return Result(status=contracts.FAILED, data=data, message=shown[:300] or "failed",
                          error_code=contracts.EXECUTION_FAILED,
                          advice=("The outside tool answered and reported a failure of its own. "
                                  "Retry only if something changed; otherwise propose from the "
                                  "files: " + refusals.PROPOSE_SHAPE))
        if not shown.strip() and not structured:
            data["next_step"] = ("The call answered with nothing, which is an answer: the outside "
                                 "tool has no content for this asking.")
            return Result(status=contracts.EMPTY, data=data,
                          advice=("An empty outside answer is not a task failure. Read the files "
                                  "and propose the smallest change: " + refusals.PROPOSE_SHAPE))
        # No `state` delta is written from an outside tool's answer, on purpose: the continuity record
        # is the run's own evidence, and `tools.py` states the rule that it is written from what this
        # runtime observed — a third party's prose surviving the trim as a *fact* is how a fabricated
        # claim outlives the turn that said it.
        return Result(status=contracts.OK, data=data)

    def shutdown(self) -> None:
        """Close the child, if this provider opened it. Never raises."""
        if self._client is not None and self._owns_client:
            try:
                self._client.close()
            finally:
                self._client = None


def _content(payload: dict) -> tuple[str, dict[str, int]]:
    """The text of an answer, and a count of every content type it carried.

    Non-text parts are counted and dropped rather than shown: this loop reasons in text, and an image
    block summarised to a word would be a claim about content nobody looked at.
    """
    parts = payload.get("content")
    if not isinstance(parts, list):
        return "", {}
    said: list[str] = []
    types: dict[str, int] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        kind = _text(part.get("type")) or "unknown"
        types[kind] = types.get(kind, 0) + 1
        if kind == "text" and isinstance(part.get("text"), str):
            said.append(part["text"])
    return "\n".join(said), types


def providers_for(specs: Iterable[McpServerSpec], *, cwd: Path | None = None,
                  clients: dict[str, McpClient] | None = None
                  ) -> list[McpToolProvider]:
    """The providers for these rows — one per server, none for a row that cannot run."""
    out: list[McpToolProvider] = []
    for spec in specs:
        if not spec.enabled:
            continue
        client = (clients or {}).get(spec.id)
        out.append(McpToolProvider(spec, cwd=cwd, client=client))
    return out


def providers_from_config(path: Path, *, cwd: Path | None = None
                          ) -> tuple[list[McpToolProvider], list[dict]]:
    """Read the operator's file and hand back the providers and the problems.

    Callers pass `cwd` as the workspace the task is pointed at: the server runs with that folder as
    its own, which is the only reason a filesystem bridge can answer about the project at all, and the
    reason the guard's path confinement is worth having — the server may reach the folder, and the
    calls reaching it are still judged against the run's prefixes.
    """
    specs, problems = load_specs(path)
    return providers_for(specs, cwd=cwd), problems
