"""What the MCP provider guarantees before it asks a third party to run anything.

Two properties carry the file. The first is that a server's *declaration* is evidence, not an
grant: `contract_for` is asserted against shapes the contract layer cannot judge — a `number`, a
nested object, a required name the properties never described, a field called `state` the loop pops
before a contract sees it — and each one costs that tool and nothing else, because a runtime that
invented a contract for a schema it could not check would be handing its own gate a document to
trust. The second is that the process is optional in the strongest sense: a row that cannot be read,
a server that will not start, and a call that times out all produce an observation the loop can read
and a task that still plans, never an exception that ends the run.

The gate is exercised through the real `CompositeToolProvider` rather than against the provider
alone, because the property that matters is the order: the folder's word is asked before
`call_tool` is reached, so a refused outside tool never costs a spawn and never sends model text to a
third-party binary. The fake client counts what it was asked to do, which is how "never reached" is
asserted instead of assumed.

Wire framing is asserted too — one JSON-RPC object per line, `initialize` before `tools/list`, a
request from the server answered rather than ignored — because that half is the only place a protocol
mistake becomes a hang, and a hang on a stdio pipe is indistinguishable from a slow server until the
run's budget is gone.
"""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import cli, contracts, mcp, policy, tool_provider, tools
from ai_code_engineer.config import Settings
from ai_code_engineer.errors import AgentError
from ai_code_engineer.workspace import Workspace


def spec(**changes):
    row = {"id": "files", "command": "node", "args": ["server.js"], "enabled": True}
    row.update(changes)
    return mcp.McpServerSpec.from_dict(row)


RUNNER_KEYS = set(mcp.runner.ENV_KEYS)


class FakeClient:
    """A server that answers from a list, so no process is ever spawned by a test."""

    def __init__(self, tools=(), answers=None, fails=None):
        self.tools = list(tools)
        self.answers = answers or {}
        self.fails = fails or {}
        self.calls = []
        self.started = 0
        self.closed = 0

    def start(self):
        if "start" in self.fails:
            raise self.fails["start"]
        self.started += 1
        return {"protocolVersion": mcp.PROTOCOL_VERSION, "serverInfo": {"name": "fake"}}

    def list_tools(self):
        return self.tools

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name in self.fails:
            raise self.fails[name]
        return self.answers.get(name, {"content": [{"type": "text", "text": "answered"}]})

    def close(self):
        self.closed += 1


class RowTests(unittest.TestCase):
    """The operator's file, refused row by row rather than obeyed approximately."""

    def test_a_missing_file_is_the_ordinary_answer_and_says_nothing(self):
        specs, problems = mcp.load_specs(Path(tempfile.gettempdir()) / "no-such-mcp.json")
        self.assertEqual((specs, problems), ([], []))

    def test_an_unreadable_file_is_a_problem_not_a_failed_task(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        (folder / mcp.CONFIG_NAME).write_text("{not json", encoding="utf-8")
        specs, problems = mcp.load_specs(folder / mcp.CONFIG_NAME)
        self.assertEqual(specs, [])
        self.assertIn("unreadable", problems[0]["reason"])

    def test_a_row_with_an_unknown_key_starts_nothing(self):
        """The row was not written against this version, and guessing which key the operator meant
        is guessing about what command line to execute."""
        with self.assertRaises(AgentError):
            spec(shell=True)

    def test_env_holds_variable_names_and_never_values(self):
        self.assertEqual(spec(env=["API_TOKEN"]).env, ("API_TOKEN",))
        with self.assertRaises(AgentError) as caught:
            spec(env=["API_TOKEN=hunter2"])
        self.assertIn("never hold their values", str(caught.exception))

    def test_a_command_needing_a_shell_is_refused(self):
        for command in ("node && rm -rf", "server;id", "a|b", "server\x00"):
            with self.assertRaises(AgentError, msg=command):
                spec(command=command)

    def test_a_row_is_only_a_server_when_the_operator_said_so(self):
        """`enabled` defaults to off, so a row written into the wrong file, or left in a template,
        runs nothing until somebody types the word."""
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        (folder / mcp.CONFIG_NAME).write_text(json.dumps(
            {"servers": [{"id": "files", "command": "node"}]}), encoding="utf-8")
        specs, problems = mcp.load_specs(folder / mcp.CONFIG_NAME)
        self.assertEqual(specs, [], "a row nobody enabled starts no process")
        self.assertEqual(problems, [], "and a disabled row is not a mistake worth reporting")
        self.assertEqual(spec(enabled=False).enabled, False)

    def test_two_rows_sharing_one_id_are_one_server_and_one_problem(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        body = {"servers": [{"id": "files", "command": "node", "enabled": True},
                            {"id": "files", "command": "node", "enabled": True}]}
        (folder / mcp.CONFIG_NAME).write_text(json.dumps(body), encoding="utf-8")
        specs, problems = mcp.load_specs(folder / mcp.CONFIG_NAME)
        self.assertEqual(len(specs), 1)
        self.assertIn("share one id", problems[0]["reason"])

    def test_the_child_environment_is_the_scrubbed_base_plus_the_named_variables(self):
        env = spec(env=["NO_SUCH_VARIABLE_AT_ALL"]).child_env()
        self.assertIn("PATH", env)
        self.assertNotIn("NO_SUCH_VARIABLE_AT_ALL", env)
        self.assertTrue(all(key in RUNNER_KEYS for key in env), "no unnamed variable leaks in")


class SchemaTests(unittest.TestCase):
    """A server's advertised shape, mapped only as far as this runtime can judge it."""

    def test_a_read_only_tool_becomes_a_read_contract_with_its_bounds_kept(self):
        contract = mcp.contract_for("files", {
            "name": "read", "description": "Read a file",
            "annotations": {"readOnlyHint": True},
            "inputSchema": {"type": "object",
                            "properties": {"path": {"type": "string"},
                                           "lines": {"type": "integer", "minimum": 1,
                                                     "maximum": 50}},
                            "required": ["path"]}})
        self.assertEqual(contract.name, "mcp_files_read")
        self.assertEqual((contract.policy_action, contract.side_effect),
                         (policy.READ, contracts.LEVEL_READ))
        self.assertEqual(contract.field("path").kind, contracts.PATH,
                         "a path-shaped field is confined, not merely typed")
        self.assertEqual(contract.field("lines").maximum, 50)
        self.assertEqual(contract.field("path").required, True)
        self.assertEqual(contract.field("lines").required, False)

    def test_a_tool_that_says_nothing_about_itself_is_placed_at_the_top_of_the_ladder(self):
        contract = mcp.contract_for("files", {"name": "do", "inputSchema": {"type": "object"}})
        self.assertEqual((contract.policy_action, contract.side_effect),
                         (policy.NETWORK, contracts.LEVEL_NETWORK))

    def test_an_enum_and_a_boolean_and_a_string_array_all_survive_the_mapping(self):
        contract = mcp.contract_for("files", {"name": "many", "inputSchema": {"type": "object",
                                                                             "properties": {
            "mode": {"type": "string", "enum": ["a", "b"]},
            "force": {"type": "boolean"},
            "paths": {"type": "array", "items": {"type": "string"}, "maxItems": 4}}}})
        self.assertEqual(contract.field("mode").values, ("a", "b"))
        self.assertEqual(contract.field("force").kind, contracts.BOOLEAN)
        self.assertEqual(contract.field("paths").max_items, 4)

    def test_a_shape_this_layer_cannot_judge_refuses_the_tool(self):
        for name, prop in (("number", {"type": "number"}),
                           ("object", {"type": "object"}),
                           ("array_of_objects", {"type": "array", "items": {"type": "object"}}),
                           ("untyped", {})):
            with self.assertRaises(mcp.UnsupportedTool, msg=name):
                mcp.contract_for("files", {"name": "tool", "inputSchema": {
                    "type": "object", "properties": {name: prop}, "required": [name]}})

    def test_a_required_field_the_schema_never_described_is_refused(self):
        """The exact-field check would refuse every legal call to it, so the tool cannot be offered
        at all — and offering it would spend the run's turns on a mistake the model cannot fix."""
        with self.assertRaises(mcp.UnsupportedTool) as caught:
            mcp.contract_for("files", {"name": "tool", "inputSchema": {
                "type": "object", "properties": {}, "required": ["ghost"]}})
        self.assertIn("ghost", str(caught.exception))

    def test_a_field_the_loop_would_eat_before_a_contract_sees_it_is_refused(self):
        for field in ("state", "args", "reason", "trace_id", "action"):
            with self.assertRaises(mcp.UnsupportedTool, msg=field):
                mcp.contract_for("files", {"name": "tool", "inputSchema": {
                    "type": "object", "properties": {field: {"type": "string"}}}})

    def test_a_name_that_is_not_one_this_runtime_can_put_in_a_menu_is_refused(self):
        for name in ("", "read file", "read;file", "a" * 80):
            with self.assertRaises(mcp.UnsupportedTool, msg=name):
                mcp.contract_for("files", {"name": name, "inputSchema": {"type": "object"}})

    def test_a_description_is_capped_rather_than_trusted(self):
        """The server is being handed the model's attention. Length is the bound this layer can
        actually hold it to, since the prose is nobody else's vocabulary."""
        contract = mcp.contract_for("files", {"name": "tool",
                                              "description": "x" * 4000,
                                              "inputSchema": {"type": "object"}})
        self.assertLessEqual(len(contract.purpose), mcp.MAX_DESCRIPTION_CHARS)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "app.py").write_text("print(1)\n", encoding="utf-8")
        self.events = []
        self.announced = []

    def context(self, answer=policy.ALLOW):
        """A context whose folder answers `answer` — a verdict, or the fixed word a test wants.

        Most tests pin the folder's answer, because the answer under test is the gate's order, not
        the table's contents; the two tests that care about the table pass `policy.decide`.
        """
        verdict = answer if callable(answer) else (lambda item: answer)
        return tools.ToolContext(
            ws=Workspace(self.root), settings=Settings(), observed={},
            record=lambda kind, **fields: self.events.append((kind, fields)),
            announce=lambda action, **fields: self.announced.append((action, fields)),
            progress=lambda line: None,
            verdict=verdict, trace_id="trace-1")

    def provider(self, client, **changes):
        return mcp.McpToolProvider(spec(**changes), cwd=self.root, client=client)

    def router(self, provider):
        return tool_provider.CompositeToolProvider(
            tool_provider.default_providers([provider]), control=tools.CONTROL_ACTIONS)

    # -- discovery ---------------------------------------------------------

    def test_the_advertised_tools_arrive_namespaced_and_the_unusable_one_is_skipped(self):
        client = FakeClient(tools=[{"name": "read", "inputSchema": {"type": "object"},
                                    "annotations": {"readOnlyHint": True}},
                                   {"name": "wide", "inputSchema": {
                                       "type": "object",
                                       "properties": {"weight": {"type": "number"}}}}])
        provider = self.provider(client)
        self.assertEqual([c.name for c in provider.contracts()], ["mcp_files_read"])
        self.assertEqual(provider.skipped[0]["tool"], "wide")
        self.assertIn("cannot judge", provider.skipped[0]["reason"])

    def test_discovery_happens_once_and_the_process_is_started_once(self):
        client = FakeClient(tools=[{"name": "read", "inputSchema": {"type": "object"}}])
        provider = self.provider(client)
        provider.contracts()
        provider.contracts()
        self.assertEqual(client.started, 1)

    def test_an_allowlist_is_applied_to_the_servers_own_names(self):
        client = FakeClient(tools=[{"name": "read", "inputSchema": {"type": "object"}},
                                   {"name": "delete", "inputSchema": {"type": "object"}}])
        provider = self.provider(client, tools=["read"])
        self.assertEqual([c.name for c in provider.contracts()], ["mcp_files_read"])
        self.assertIn("allowlist", provider.skipped[0]["reason"])

    def test_a_server_that_will_not_start_offers_nothing_and_raises_nothing(self):
        client = FakeClient(fails={"start": mcp.McpUnavailable("the bridge is down")})
        provider = self.provider(client)
        self.assertEqual(provider.contracts(), ())
        self.assertIn("bridge is down", provider.problem)

    # -- the gate, in order ------------------------------------------------

    def test_a_default_folder_refuses_an_outside_tool_before_the_server_is_asked(self):
        """The run's whole posture in one assertion: the table answers ASK for `network` and
        `execute_custom`, nobody is typing, and the client is never touched — so no text a model
        wrote reaches a third-party binary on the strength of a manifest."""
        client = FakeClient(tools=[{"name": "patch", "inputSchema": {"type": "object"}}],
                            answers={"patch": {"content": [{"type": "text", "text": "done"}]}})
        provider = self.provider(client)
        router = self.router(provider)
        with self.assertRaises(contracts.PolicyDenied):
            router.run({"action": "mcp_files_patch"}, self.context(policy.decide))
        self.assertEqual(client.calls, [], "the server was never asked")

    def test_a_read_only_outside_tool_runs_on_the_tables_own_word(self):
        client = FakeClient(tools=[{"name": "read", "inputSchema": {"type": "object"},
                                    "annotations": {"readOnlyHint": True}}],
                            answers={"read": {"content": [{"type": "text", "text": "hi"}]}})
        provider = self.provider(client)
        router = self.router(provider)
        outcome = router.run({"action": "mcp_files_read"}, self.context(policy.decide))
        self.assertEqual(outcome.status, contracts.OK)
        self.assertEqual(client.calls, [("read", {})])
        self.assertEqual(outcome.trace_id, "trace-1")
        self.assertTrue(outcome.call_id)

    def test_the_run_sandbox_confines_an_outside_tools_path_argument(self):
        client = FakeClient(tools=[{"name": "read", "inputSchema": {
            "type": "object", "properties": {"path": {"type": "string"}},
            "required": ["path"]}, "annotations": {"readOnlyHint": True}}])
        provider = self.provider(client)
        router = self.router(provider)
        context = self.context(policy.decide)
        context.guard = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_NETWORK,
                                               allowed_prefixes=("src",))
        with self.assertRaises(contracts.SandboxDenied):
            router.run({"action": "mcp_files_read", "path": "app.py"}, context)
        self.assertEqual(client.calls, [])

    def test_the_operators_cap_hides_a_tool_the_run_cannot_allow(self):
        client = FakeClient(tools=[{"name": "patch", "inputSchema": {"type": "object"}}])
        provider = self.provider(client, max_side_effect=contracts.LEVEL_READ)
        router = self.router(provider)
        with self.assertRaises(contracts.PolicyDenied):
            router.run({"action": "mcp_files_patch"}, self.context())

    # -- the answer --------------------------------------------------------

    def test_text_content_comes_back_redacted_capped_and_never_as_state(self):
        """A third party's prose surviving the trim as a *fact* is how a fabricated claim outlives
        the turn that said it, so `state` stays empty whatever the answer contains."""
        client = FakeClient(
            tools=[{"name": "read", "inputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": True}}],
            answers={"read": {"content": [{"type": "text",
                                           "text": "token=abcdef123456\n" + "y" * 2000}]}})
        provider = self.provider(client, max_output_chars=300)
        outcome = provider.execute(provider.contract_for("mcp_files_read"),
                                   contracts.Call("mcp_files_read", {}), self.context())
        self.assertEqual(outcome.status, contracts.OK)
        self.assertNotIn("abcdef123456", outcome.data["text"])
        self.assertEqual(len(outcome.data["text"]), 300)
        self.assertTrue(outcome.data["truncated"])
        self.assertEqual(outcome.state, {})

    def test_an_error_inside_an_answered_call_is_a_failure_of_the_tools_own(self):
        client = FakeClient(
            tools=[{"name": "read", "inputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": True}}],
            answers={"read": {"content": [{"type": "text", "text": "no such entry"}],
                              "isError": True}})
        provider = self.provider(client)
        outcome = provider.execute(provider.contract_for("mcp_files_read"),
                                   contracts.Call("mcp_files_read", {}), self.context())
        self.assertEqual(outcome.status, contracts.FAILED)
        self.assertEqual(outcome.error_code, contracts.EXECUTION_FAILED)
        self.assertTrue(outcome.retryable, "the call landed; the work may land next time")

    def test_a_server_that_never_answers_is_an_observation_not_a_dead_run(self):
        """`empty` with the reason, the way `run_tests` reports a check that could not run: a task
        that wanted a filesystem bridge is not a task that has to be re-started by hand."""
        client = FakeClient(
            tools=[{"name": "read", "inputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": True}}],
            fails={"read": mcp.McpUnavailable("closed")})
        provider = self.provider(client)
        outcome = provider.execute(provider.contract_for("mcp_files_read"),
                                   contracts.Call("mcp_files_read", {}), self.context())
        self.assertEqual(outcome.status, contracts.EMPTY)
        self.assertEqual(outcome.data["status"], "unavailable")
        self.assertIn("closed", outcome.data["reason"])

    def test_structured_content_and_content_counts_come_back_as_data(self):
        client = FakeClient(
            tools=[{"name": "read", "inputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": True}}],
            answers={"read": {"content": [{"type": "text", "text": "one"},
                                          {"type": "image", "data": "..."}],
                              "structuredContent": {"rows": 2}}})
        provider = self.provider(client)
        outcome = provider.execute(provider.contract_for("mcp_files_read"),
                                   contracts.Call("mcp_files_read", {}), self.context())
        self.assertEqual(outcome.data["structured"], {"rows": 2})
        self.assertEqual(outcome.data["content_types"], {"text": 1, "image": 1})

    def test_a_refused_call_records_nothing(self):
        client = FakeClient(tools=[{"name": "patch", "inputSchema": {"type": "object"}}])
        provider = self.provider(client)
        with self.assertRaises(contracts.PolicyDenied):
            self.router(provider).run({"action": "mcp_files_patch"},
                                      self.context(lambda item: policy.DENY))
        self.assertEqual(self.events, [])
        self.assertEqual(self.announced, [], "a row for a call that never ran is a false record")

    def test_an_answered_call_records_which_provider_answered(self):
        """The session row has to say the answer came from outside, or a reader hours later cannot
        tell a third party's word from the runtime's own observation."""
        client = FakeClient(
            tools=[{"name": "read", "inputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": True}}])
        provider = self.provider(client)
        self.router(provider).run({"action": "mcp_files_read"}, self.context(policy.decide))
        self.assertEqual([row["provider"] for kind, row in self.events if kind == "tool"],
                         ["mcp:files"])

    def test_shutdown_closes_a_child_this_provider_opened_and_never_raises(self):
        client = FakeClient(tools=[])
        self.addCleanup(setattr, mcp, "McpClient", mcp.McpClient)
        mcp.McpClient = lambda row, cwd=None: client      # the provider builds its own client here
        provider = mcp.McpToolProvider(spec(), cwd=self.root)
        provider.contracts()
        provider.shutdown()
        provider.shutdown()
        self.assertEqual(client.closed, 1, "closed once, and the second shutdown said nothing")

    def test_an_injected_client_is_the_callers_to_close(self):
        """A provider that did not spawn the process has no business killing it: the test harness
        owns this one, and a shutdown that reached across would take a server another run is using."""
        client = FakeClient(tools=[])
        self.provider(client).shutdown()
        self.assertEqual(client.closed, 0)


class FakeChild:
    """A stand-in process that answers every request the client writes, on the same two pipes.

    The answers are automatic rather than queued by each test because a framing test that had to be
    fed its own reply by hand would spend its time proving the harness works — and because a client
    reading from a pipe that has nothing on it is the exact hang this class exists to make cheap to
    assert around.
    """

    def __init__(self, test, answers=None, silent=False):
        self.test = test
        self.stdin = self
        self.stdout = self
        self.pid = 12345
        self.lines = []
        self.answers = answers or {}
        self.foreign = False
        self.silent = silent
        self._read = 0

    def write(self, text):
        line = text.strip()
        self.test.sent.append(line)
        message = json.loads(line)
        if self.silent or "method" not in message or "id" not in message:
            return                      # a notification is not owed an answer
        result = self.answers.get(message["method"], {"ok": True})
        if self.foreign:
            self.lines.append(json.dumps({"jsonrpc": "2.0", "id": 999,
                                          "result": {"tools": [{"name": "not this call"}]}}))
        self.lines.append(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}))

    def flush(self):
        pass

    def close(self):
        pass

    def __iter__(self):
        while True:
            if self._read < len(self.lines):
                yield self.lines[self._read]
                self._read += 1
            else:
                time.sleep(0.005)


class FramingTests(unittest.TestCase):
    """The wire itself: one object per line, ordered, and no silent hangs."""

    def setUp(self):
        self.sent = []
        # An executable this machine certainly has, because the row is resolved before it is
        # spawned and the fake below never spawns anything.
        self.client = mcp.McpClient(spec(command=sys.executable), spawn=self._spawn)
        self.child = FakeChild(self)

    def _spawn(self, argv):
        self.argv = argv
        return self.child

    def _errors(self):
        return [json.loads(line) for line in self.sent if "error" in json.loads(line)]

    def test_initialize_is_sent_before_the_catalogue_is_asked_for(self):
        self.client.start()
        self.assertEqual(self.client.list_tools(), [])
        methods = [json.loads(line)["method"] for line in self.sent]
        self.assertEqual(methods[:3], ["initialize", "notifications/initialized", "tools/list"])
        self.assertEqual(self.argv, [sys.executable, "server.js"], "the row is the whole command line")

    def test_the_handshake_carries_the_version_this_client_speaks(self):
        self.client.start()
        opening = json.loads(self.sent[0])
        self.assertEqual(opening["params"]["protocolVersion"], mcp.PROTOCOL_VERSION)
        self.assertEqual(opening["method"], "initialize")

    def test_a_reply_for_a_different_id_is_dropped_rather_than_returned(self):
        """A server that answers out of order has not answered *this* call, and a client that returns
        the wrong answer is worse than one that times out."""
        self.child.foreign = True
        self.client.start()
        self.assertEqual(self.client.list_tools(), [],
                         "what arrived under another id never became this call's answer")

    def test_a_request_from_the_server_is_answered_so_its_writer_does_not_block(self):
        self.client.start()
        self.child.lines.append(json.dumps({"jsonrpc": "2.0", "id": 5,
                                            "method": "sampling/createMessage"}))
        deadline = time.time() + 2.0
        while time.time() < deadline and not self._errors():
            time.sleep(0.01)
        replied = self._errors()
        self.assertEqual(len(replied), 1, "the refusal is the only thing written back")
        self.assertEqual(replied[0]["id"], 5)
        self.assertEqual(replied[0]["error"]["code"], -32601)

    def test_a_server_that_stops_answering_costs_a_timeout_not_the_run(self):
        """The row's own deadline, and the taxonomy's `timeout` — retryable, because a slow bridge is
        not a wrong question, and the loop has to be able to ask it again next turn."""
        self.child.silent = True
        stalled = mcp.McpClient(spec(command=sys.executable, timeout_seconds=1),
                                spawn=self._spawn)
        with self.assertRaises(mcp.McpTimeout) as caught:
            stalled.start()
        self.assertIn("initialize", str(caught.exception))
        self.assertTrue(caught.exception.retryable)

    def test_a_row_naming_an_executable_that_is_not_installed_is_reported_not_run(self):
        client = mcp.McpClient(spec(command=this_system_has_no_such_binary))
        with self.assertRaises(mcp.McpUnavailable) as caught:
            client.start()
        self.assertIn("not installed", str(caught.exception))


this_system_has_no_such_binary = "definitely-not-an-installed-binary-zzz"


class CommandWiringTests(unittest.TestCase):
    """The operator's file, read the way the command line reads it.

    `engine.plan` takes the providers and the CLI decides whether to look for a file at all; these are
    the two ways that decision can be wrong — a run that silently ignores a configured server, and a
    run that dies because somebody's `.agent-mcp.json` is half-written.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.app = Path(temp.name)
        self.said = []
        where = patch("ai_code_engineer.cli.app_dir", return_value=self.app)
        where.start()
        self.addCleanup(where.stop)

    def wired(self):
        return cli.external_providers(Workspace(self.app), self.said.append)

    def test_an_unconfigured_folder_offers_nothing_and_says_nothing(self):
        self.assertEqual(self.wired(), [])
        self.assertEqual(self.said, [], "no servers is not a problem to report")

    def test_a_configured_row_becomes_a_provider_without_asking_the_server(self):
        """Discovery is the loop's job, not the wiring's: a run that never asks for an outside tool
        must never pay for a process, and a process nobody started has no answer to give a window."""
        def never(row, cwd=None):
            raise AssertionError("wiring a provider must not build a client")
        self.addCleanup(setattr, mcp, "McpClient", mcp.McpClient)
        mcp.McpClient = never
        (self.app / mcp.CONFIG_NAME).write_text(json.dumps(
            {"servers": [{"id": "files", "command": "node", "enabled": True}]}), encoding="utf-8")
        providers = self.wired()
        self.assertEqual([provider.id for provider in providers], ["mcp:files"])
        self.assertEqual(providers[0].skipped, [], "nothing has been advertised yet")

    def test_a_row_that_cannot_be_read_is_said_and_costs_nothing_else(self):
        (self.app / mcp.CONFIG_NAME).write_text(json.dumps(
            {"servers": [{"id": "Files!", "command": "node", "enabled": True},
                         {"id": "ok", "command": "node", "enabled": True}]}), encoding="utf-8")
        providers = self.wired()
        self.assertEqual([provider.id for provider in providers], ["mcp:ok"])
        self.assertIn("MCP:", self.said[0])
        self.assertIn("id", self.said[0], "the sentence says which row was refused")


if __name__ == "__main__":
    unittest.main()
