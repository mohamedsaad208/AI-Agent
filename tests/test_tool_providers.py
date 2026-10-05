"""What the provider layer guarantees about a vocabulary made of more than one source.

`tools.py` guarantees things about one tool's asking and answer; this file is about the seams the
provider abstraction introduces: which gate judges a call (the owning provider's, never the
router's), what a merged menu looks like, and what a name two providers both claim costs. The
assertions are mostly *counts* and *orders* — how many classes the folder was asked about, which
provider's handler ran, in what order the vocabulary lists itself — because the failure this layer
invites is a silent one: a composite that judged an outside tool through the native gate, or that
dispatched a call the gate had refused, would keep answering until the run that depended on the rule
was over.

The native provider is pinned as the *same* registry the loop always ran: same contracts in the same
order, same handler, same one policy question per call, same refusal sentences. That is the
regression this task had to leave behind — an abstraction that changed what a built-in call costs
would be a rewrite wearing a wrapper.

The id cycle is asserted once, on the composite, rather than per provider, because `tools.admitted_run`
is the single implementation every provider dispatches through: a call leaves no `call_id` behind on
the context, and the result it hands back carries the same `trace_id` and `call_id` the row written
under it does.
"""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import contracts, engine, gate, policy, tool_provider, tools
from ai_code_engineer.config import Settings
from ai_code_engineer.workspace import Workspace


class StubProvider(tool_provider.ToolProvider):
    """An outside provider with one tool, so the composite's routing can be watched."""

    def __init__(self, tool="fetch_url", level=contracts.LEVEL_NETWORK, action=policy.READ,
                 origin=None, name=None):
        self.contract = contracts.ToolContract(name or "mcp_files_" + tool,
                                               (contracts.Field("url"),),
                                               purpose="Fetch a thing over the network.",
                                               policy_action=action, side_effect=level)
        self.ran = []
        super().__init__(origin or gate.external("mcp:files"))

    def contracts(self):
        return (self.contract,)

    def handle(self, contract, call, context):
        self.ran.append(call)
        return contracts.Result(contracts.OK, {"fetched": call.get("url")})


class ProviderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "app.py").write_text("print(1)\n", encoding="utf-8")
        self.asked = []
        self.events = []
        self.announced = []

    def context(self, answer=policy.ALLOW):
        return tools.ToolContext(
            ws=Workspace(self.root), settings=Settings(), observed={},
            record=lambda kind, **fields: self.events.append((kind, fields)),
            announce=lambda action, **fields: self.announced.append((action, fields)),
            progress=lambda line: None,
            verdict=lambda item: (self.asked.append(item), answer)[1],
            trace_id="trace-1")

    def router(self, *extra):
        return tool_provider.CompositeToolProvider(
            tool_provider.default_providers(extra), control=tools.CONTROL_ACTIONS)


class TheNativeProvider(ProviderTests):
    """The built-ins, as a provider — and nothing about them changed."""

    def test_it_offers_the_same_vocabulary_the_loop_always_ran(self):
        provider = tool_provider.NativeToolProvider()
        self.assertEqual(provider.contracts(), tools.BUILTIN_CONTRACTS)
        self.assertEqual(provider.id, "native")
        self.assertTrue(provider.provenance.managed)

    def test_a_built_in_call_still_costs_one_policy_question(self):
        """The escalation in `gate` is about tools this runtime did not write; applying it here would
        mean every read the loop performs suddenly asks about `execute_custom`."""
        router = self.router()
        outcome = router.run({"action": "read_file", "path": "app.py"}, self.context())
        self.assertEqual(outcome.status, contracts.OK)
        self.assertEqual(self.asked, [policy.READ])

    def test_a_handler_refused_by_the_gate_never_runs(self):
        router = self.router()
        with self.assertRaises(contracts.PolicyDenied):
            router.run({"action": "read_file", "path": "app.py"},
                       self.context(answer=policy.DENY))
        self.assertEqual(self.announced, [], "a refused call announces nothing")

    def test_a_custom_registry_can_be_supplied_rather_than_the_default(self):
        registry = tools.ToolRegistry(control=tools.CONTROL_ACTIONS)
        registry.register(contracts.ToolContract("count_widgets", (contracts.Field("query"),)),
                          lambda call, ctx: contracts.Result(contracts.OK, {"n": len(call.get("query"))}))
        provider = tool_provider.NativeToolProvider(registry)
        outcome = provider.execute(provider.contract_for("count_widgets"),
                                   contracts.Call("count_widgets", {"query": "abc"}),
                                   self.context())
        self.assertEqual(outcome.data, {"n": 3})


class TheComposite(ProviderTests):
    """One vocabulary over N providers, and the gate that belongs to the tool actually named."""

    def test_the_merged_menu_lists_the_builtins_first_and_the_control_actions_last(self):
        router = self.router(StubProvider())
        self.assertEqual(router.names(),
                         tuple(contract.name for contract in tools.BUILTIN_CONTRACTS)
                         + ("mcp_files_fetch_url",) + tools.CONTROL_ACTIONS)

    def test_two_providers_claiming_one_name_are_refused_at_construction(self):
        """Not at the call, and not by keeping the first: a name that means two things makes every
        refusal about it a lie, and the menu would show one while the dispatcher ran the other."""
        with self.assertRaises(ValueError) as caught:
            self.router(StubProvider(name="read_file"))
        self.assertIn("read_file", str(caught.exception))
        self.assertIn("mcp:files", str(caught.exception))

    def test_a_native_name_resolves_to_the_native_provider_even_after_a_second_source_joins(self):
        router = self.router(StubProvider())
        outcome = router.run({"action": "list_files"}, self.context())
        self.assertEqual(outcome.status, contracts.OK)
        self.assertIn("files", outcome.data, "the built-in handler answered, not the outside one")

    def test_an_unknown_action_lists_the_whole_merged_vocabulary(self):
        """The refusal the model learns the menu from has to name the outside tool too, or the tool
        offered in the prompt is one that is missing from the answer it gets for a typo."""
        router = self.router(StubProvider())
        with self.assertRaises(contracts.UnknownTool) as caught:
            router.run({"action": "no_such_tool"}, self.context())
        for name in router.names():
            self.assertIn(name, str(caught.exception), name)

    def test_an_outside_tool_is_judged_by_its_own_providers_gate_not_the_routers(self):
        """The single property the whole abstraction exists for: the escalation travels with the
        source. The native call asked nothing about `execute_custom`; this one was asked about it,
        and the refusal names the provider that supplied the tool rather than leaving the operator to
        work out which server to go arguing with."""
        router = self.router(StubProvider())
        with self.assertRaises(contracts.PolicyDenied) as caught:
            router.run({"action": "mcp_files_fetch_url", "url": "http://example.invalid"},
                       self.context(answer=policy.ASK))
        self.assertIn(policy.EXECUTE_CUSTOM, self.asked)
        self.assertIn("mcp:files", str(caught.exception))

    def test_the_same_outside_tool_runs_when_the_folder_allows_what_it_reaches(self):
        stub = StubProvider()
        router = self.router(stub)
        outcome = router.run({"action": "mcp_files_fetch_url", "url": "http://x"},
                             self.context(answer=policy.ALLOW))
        self.assertEqual(outcome.data, {"fetched": "http://x"})
        self.assertEqual(len(stub.ran), 1)

    def test_a_call_that_cleared_the_gate_leaves_no_id_behind_and_hands_one_back(self):
        router = self.router(StubProvider())
        context = self.context()
        outcome = router.run({"action": "mcp_files_fetch_url", "url": "http://x"}, context)
        self.assertEqual(context.call_id, "")
        self.assertEqual(outcome.trace_id, "trace-1")
        self.assertTrue(outcome.call_id)

    def test_the_run_posture_binds_an_outside_tool_exactly_as_it_binds_a_built_in(self):
        router = self.router(StubProvider())
        context = self.context()
        context.guard = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_READ)
        with self.assertRaises(contracts.SandboxDenied):
            router.run({"action": "mcp_files_fetch_url", "url": "http://x"}, context)
        self.assertEqual(self.announced, [])

    def test_the_addendum_is_empty_for_a_run_with_nothing_outside(self):
        """The default run's prompt is the prompt it always was — an empty string rather than a block
        saying the runtime has no third-party tools."""
        self.assertEqual(self.router().addendum(), "")

    def test_the_addendum_shows_the_outside_shapes_and_labels_their_descriptions_as_data(self):
        """The name, fields and bounds are the runtime's; the sentence beside them was written by the
        server. A prompt that flattened the two would hand the provider the run's authority."""
        addendum = self.router(StubProvider()).addendum()
        self.assertIn("mcp_files_fetch_url(url)", addendum)
        self.assertIn("untrusted", addendum)
        self.assertIn("Fetch a thing over the network.", addendum)


class Defaults(ProviderTests):
    """What a run gets when nobody configured anything."""

    def test_the_default_provider_list_is_the_builtins_alone(self):
        self.assertEqual([provider.id for provider in tool_provider.default_providers()], ["native"])

    def test_the_default_router_answers_like_the_registry_it_replaced(self):
        router = tool_provider.default_router()
        self.assertEqual(router.names(), tools.default_registry().names())
        self.assertEqual(router.menu(), contracts.menu(tools.BUILTIN_CONTRACTS))


PROPOSE = {"action": "propose", "summary": "Change the answer", "checks": ["unit tests"],
           "changes": [{"path": "app.py", "content": "answer = 2\n"}]}

# The first words of the block `CompositeToolProvider.addendum` puts in front of an outside tool's
# shapes. Pinned as text because the sentence is the guarantee: a prompt that showed the shapes
# without it would hand a third party's description to the model as the runtime's own rule.
OUTSIDE_BLOCK = "Tools offered by this run's external providers"


class RecordingProvider:
    """A model that says the turns it was handed, and keeps every prompt it was asked with.

    The prompt is the artefact under test in the wiring cases: a tool the loop can run but never
    showed the model is a tool nobody uses, and a tool shown to the model that the loop will not run
    is a turn spent on a lie.
    """

    model = "test-local"

    def __init__(self, responses):
        self.responses = list(responses)
        self.turns: list[str] = []

    def generate(self, messages, json_mode=True):
        self.turns.append(" ".join(str(item.get("content", "")) for item in messages))
        return json.dumps(self.responses[min(len(self.turns) - 1, len(self.responses) - 1)])


class WhatTheLoopIsOffered(unittest.TestCase):
    """The seam only counts if `plan` runs on it.

    Three things have to be true at once for a provider to be *wired* rather than merely written: the
    outside shapes reach the prompt the model reads, the loop's one dispatcher routes an answer to the
    provider that supplied it, and a run that configured nothing shows the model the prompt it saw
    before providers existed. The last is the one a wiring test usually skips, and it is the one that
    decides whether this layer is optional in the sense the task promised.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.ws = Workspace(self.root)
        self.runs = self.root / "runs"

    def planned(self, responses, extra=()):
        model = RecordingProvider(responses)
        path = engine.plan(self.ws, "Ask the outside thing, then change app.py", model,
                           Settings(), self.runs, progress=lambda _line: None,
                           tool_providers=extra)
        return path, model

    def test_an_outside_tool_is_shown_to_the_model_and_routed_to_its_provider(self):
        stub = StubProvider(level=contracts.LEVEL_READ, action=policy.READ)
        _path, model = self.planned([{"action": "mcp_files_fetch_url",
                                      "url": "https://example.test"}, PROPOSE], extra=[stub])
        self.assertEqual(len(stub.ran), 1, "the loop reached the outside provider's handler")
        self.assertEqual(stub.ran[0].get("url"), "https://example.test")
        self.assertIn("mcp_files_fetch_url(url)", model.turns[0],
                      "the shape the gate judges is the shape the model was shown")
        self.assertIn(OUTSIDE_BLOCK, model.turns[0],
                      "and it arrives labelled as somebody else's prose, not as the loop's rule")

    def test_the_session_records_which_provider_supplied_the_vocabulary(self):
        stub = StubProvider(level=contracts.LEVEL_READ, action=policy.READ)
        path, _model = self.planned([{"action": "mcp_files_fetch_url",
                                      "url": "https://example.test"}, PROPOSE], extra=[stub])
        rows = [item for item in engine.load_session(path)["events"]
                if item.get("kind") == "tool_provider"]
        self.assertEqual([row["provider"] for row in rows], ["mcp:files"])
        self.assertEqual(rows[0]["tools"], 1, "the record says what was offered, not that it existed")

    def test_a_run_with_nothing_configured_shows_the_model_no_outside_vocabulary(self):
        """A native-only plan is the plan this loop always ran, prompt included."""
        _path, model = self.planned([{"action": "read_file", "path": "app.py"}, PROPOSE])
        self.assertNotIn("mcp_files_fetch_url", model.turns[0])
        self.assertNotIn(OUTSIDE_BLOCK, model.turns[0])

    def test_an_outside_tool_the_folder_will_not_let_run_costs_a_refusal_not_a_dead_run(self):
        """The escalation is the point: a tool declaring `read` while reaching the network is judged
        as `execute_custom` too, the table asks, a task run has nobody to ask, and the loop is handed
        back the sentence — so it proposes instead of dying on a source it cannot use."""
        stub = StubProvider()
        path, _model = self.planned([{"action": "mcp_files_fetch_url",
                                      "url": "https://example.test"},
                                     {"action": "read_file", "path": "app.py"}, PROPOSE],
                                    extra=[stub])
        self.assertEqual(stub.ran, [], "a refused call never reaches the provider")
        self.assertEqual(engine.load_session(path)["state"], "WAITING_APPROVAL")

    def test_the_loop_closes_a_provider_it_was_handed_when_the_run_ends(self):
        """A child process this run started is this run's to stop, including on the way out of a
        refusal — which is the only reason `shutdown` is on the interface at all."""
        closed = []

        class Shutdown(StubProvider):
            def shutdown(self):
                closed.append(self.id)

        stub = Shutdown(level=contracts.LEVEL_READ, action=policy.READ)
        self.planned([{"action": "read_file", "path": "app.py"}, PROPOSE], extra=[stub])
        self.assertEqual(closed, ["mcp:files"])
