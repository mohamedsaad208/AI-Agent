"""What the unified gate guarantees about one call, whoever supplied the tool it names.

The point of consolidating the shape check, the policy verdict, the run's posture and the contract's
provenance into `gate.SecurityGate` is that a call cannot clear one of them and skip another, so
almost every test here is a refusal asserted at the *earliest* rung that applies and an assertion that
nothing ran. The half that is easy to get wrong and easy to lose in a refactor is the native case: an
escalation added for outside tools must not change what a built-in call costs or how many questions
the folder is asked about it, and `tools.py`'s own refusal sentences are pinned here because a model
learns those two sentences and a session row quotes them back hours later.

The provenance rules are asserted as *classes asked* rather than as prose, because the classes are the
decision: an external tool that declares `read` while reaching `network` is judged under
`execute_custom` too, and the only way to show that is to count what the folder was asked about.
Fail-closed defaults are the other half: an unreadable verdict, an undeclarable class, a rung above
the operator's cap for outside tools, and a pre-flight that disagrees with the live admission are all
refusals, and each is asserted against a handler that must never have been reached.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import contracts, gate, policy, tools
from ai_code_engineer.config import Settings
from ai_code_engineer.workspace import Workspace


class GateTests(unittest.TestCase):
    """A real workspace, and a verdict recorder standing in for the folder's rule."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.asked = []

    def context(self, verdict=None, guard=None, answer=policy.ALLOW):
        asked = self.asked

        def answer_class(action_class):
            asked.append(action_class)
            return verdict(action_class) if verdict else answer

        return tools.ToolContext(
            ws=Workspace(self.root), settings=Settings(), observed={},
            record=lambda kind, **fields: None,
            announce=lambda action, **fields: None,
            progress=lambda line: None,
            verdict=answer_class,
            guard=guard or gate.OPEN_GUARD)

    def admit(self, contract, envelope, *, provenance=gate.native(), guard=None,
              answer=policy.ALLOW, verdict=None, refusal=None):
        decide = verdict if verdict is not None else (lambda item: answer)

        def asked_about(action_class):
            self.asked.append(action_class)
            return decide(action_class)

        return gate.gate_for(provenance).admit(
            contract, envelope, verdict=asked_about, guard=guard or gate.OPEN_GUARD,
            refusal=refusal)


class TheNativeCase(GateTests):
    """A built-in call is judged exactly as it was before a gate existed, and no more."""

    def test_one_class_is_asked_once_and_the_call_comes_back_canonical(self):
        contract = contracts.ToolContract("read_file", (contracts.Field("path", contracts.PATH),),
                                          policy_action=policy.READ)
        call = self.admit(contract, {"action": "read_file", "path": "app.py"})
        self.assertEqual(call.args, {"path": "app.py"})
        self.assertEqual(self.asked, [policy.READ], "a native call costs one question")

    def test_a_nested_envelope_is_the_same_call_as_the_flat_one(self):
        contract = contracts.ToolContract("read_file", (contracts.Field("path", contracts.PATH),),
                                          policy_action=policy.READ)
        call = self.admit(contract, {"action": "read_file", "args": {"path": "app.py"}})
        self.assertEqual(call.get("path"), "app.py")

    def test_a_denied_class_is_refused_with_the_sentence_the_loop_has_always_given(self):
        contract = contracts.ToolContract("run_tests", (), policy_action=policy.EXECUTE_RECIPE)
        with self.assertRaises(contracts.PolicyDenied) as caught:
            self.admit(contract, {"action": "run_tests"}, answer=policy.DENY)
        message = str(caught.exception)
        for said in ("The folder's policy denies", "execute_recipe", "run_tests", "Do not retry"):
            self.assertIn(said, message)
        self.assertNotIn(" from native", message, "a built-in names no provider in its refusal")
        self.assertFalse(contracts.is_retryable(caught.exception))

    def test_an_ask_is_refused_because_a_task_run_has_nobody_to_ask(self):
        contract = contracts.ToolContract("run_tests", (), policy_action=policy.EXECUTE_RECIPE)
        with self.assertRaises(contracts.PolicyDenied) as caught:
            self.admit(contract, {"action": "run_tests"}, answer=policy.ASK)
        self.assertIn("asks before", str(caught.exception))
        self.assertIn("nobody is typing", str(caught.exception))

    def test_a_verdict_the_gate_cannot_read_falls_closed(self):
        contract = contracts.ToolContract("git_diff", (), policy_action=policy.GIT_LOCAL)
        with self.assertRaises(contracts.PolicyDenied) as caught:
            self.admit(contract, {"action": "git_diff"}, answer="maybe")
        self.assertIn("denies", str(caught.exception))

    def test_a_tool_that_names_no_class_has_no_class_to_ask_about(self):
        contract = contracts.ToolContract("count_widgets", (contracts.Field("query"),))
        self.admit(contract, {"action": "count_widgets", "query": "a"})
        self.assertEqual(self.asked, [])


class ShapeBeforePermission(GateTests):
    """A caller that mistyped its asking is answered about its asking."""

    def test_a_missing_field_is_refused_as_shape_even_when_the_class_is_denied(self):
        """The alternative is a model told about a rule it never reached, and it goes and fixes the
        field only to be refused by the rule next turn — two turns for one mistake."""
        contract = contracts.ToolContract("read_file", (contracts.Field("path", contracts.PATH),),
                                          policy_action=policy.READ)
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.admit(contract, {"action": "read_file"}, answer=policy.DENY)
        self.assertEqual(self.asked, [], "the folder was never asked about a call that did not exist")
        self.assertIn("missing its field path", str(caught.exception))

    def test_the_caller_can_restate_a_shape_complaint_as_its_own_menu(self):
        contract = contracts.ToolContract("read_file", (contracts.Field("path", contracts.PATH),),
                                          policy_action=policy.READ)
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.admit(contract, {"action": "read_file", "path": 3},
                       refusal=lambda action, detail: "Allowed: read_file(path)")
        self.assertIn("Allowed: read_file(path)", str(caught.exception))
        self.assertEqual(self.asked, [])

    def test_no_refusal_is_restated_over_an_unknown_action_name(self):
        """`UnknownTool` is the menu's own refusal and the caller has already built it; the gate has
        no business re-wording a name it cannot know is missing from a bigger vocabulary."""
        contract = contracts.ToolContract("read_file", (), policy_action=policy.READ)
        with self.assertRaises(contracts.UnknownTool):
            self.admit(contract, {"action": "write_file"},
                       refusal=lambda action, detail: "should not be used")


class ThePosture(GateTests):
    """The folder's rule and the run's posture are different questions, and both are asked."""

    def test_a_rung_above_the_sandbox_ceiling_is_refused_though_the_folder_allowed_it(self):
        contract = contracts.ToolContract("run_tests", (), policy_action=policy.EXECUTE_RECIPE,
                                          side_effect=contracts.LEVEL_EXECUTE)
        tight = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_READ)
        with self.assertRaises(contracts.SandboxDenied) as caught:
            self.admit(contract, {"action": "run_tests"}, guard=tight)
        self.assertIn("above this sandbox", str(caught.exception))

    def test_a_path_that_walks_out_of_the_workspace_is_refused_by_the_guard_not_the_table(self):
        contract = contracts.ToolContract("read_file", (contracts.Field("path", contracts.PATH),),
                                          policy_action=policy.READ)
        confined = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_NETWORK,
                                          allowed_prefixes=("src",))
        with self.assertRaises(contracts.SandboxDenied):
            self.admit(contract, {"action": "read_file", "path": "secrets/app.py"},
                       guard=confined)


class AnOutsideTool(GateTests):
    """Provenance is the fourth judgement: what a third party declared is audited, not trusted."""

    def setUp(self):
        super().setUp()
        self.network = contracts.ToolContract(
            "mcp_files_fetch_url", (contracts.Field("url"),),
            policy_action=policy.READ, side_effect=contracts.LEVEL_NETWORK)
        self.origin = gate.external("mcp:files")

    def test_the_rung_it_reaches_is_asked_about_as_well_as_the_class_it_declared(self):
        """The whole escalation in one assertion: a server that describes a network call as a read
        does not get judged as a read, because the describing is the thing being distrusted."""
        with self.assertRaises(contracts.PolicyDenied):
            self.admit(self.network, {"action": "mcp_files_fetch_url", "url": "http://x"},
                       provenance=self.origin, verdict=policy.decide)
        self.assertEqual(self.asked, [policy.READ, policy.EXECUTE_CUSTOM])

    def test_a_default_folder_denies_an_unmanaged_call_rather_than_guessing(self):
        """`execute_custom` and `network` are ASK in the table and nobody is typing during a run, so
        an outside tool is refused on the table's own word until the operator says otherwise."""
        with self.assertRaises(contracts.PolicyDenied) as caught:
            self.admit(self.network, {"action": "mcp_files_fetch_url", "url": "http://x"},
                       provenance=self.origin, verdict=policy.decide)
        self.assertIn("asks before", str(caught.exception))
        self.assertIn("mcp:files", str(caught.exception), "the refusal says who supplied it")

    def test_a_read_only_outside_tool_is_judged_as_a_read(self):
        """The floor for `read` is `read`, so an honestly-annotated tool costs one question and runs
        where the folder allows reading — the escalation is about the rung, not a blanket veto."""
        read = contracts.ToolContract("mcp_files_read", (contracts.Field("path", contracts.PATH),),
                                       policy_action=policy.READ, side_effect=contracts.LEVEL_READ)
        self.admit(read, {"action": "mcp_files_read", "path": "app.py"}, provenance=self.origin,
                   verdict=policy.decide)
        self.assertEqual(self.asked, [policy.READ])

    def test_a_declared_class_outside_the_table_is_refused_before_anything_is_asked(self):
        strange = contracts.ToolContract("mcp_x_y", (), policy_action="do_anything",
                                         side_effect=contracts.LEVEL_WRITE)
        with self.assertRaises(contracts.PolicyDenied) as caught:
            self.admit(strange, {"action": "mcp_x_y"}, provenance=self.origin,
                       verdict=policy.decide)
        self.assertIn("cannot ask about", str(caught.exception))
        self.assertEqual(self.asked, [])

    def test_the_operators_cap_stops_a_tool_reaching_above_it(self):
        """The run's guard may legitimately allow `execute` — a native `run_tests` needs it — while
        the same rung is refused for a binary nobody in this runtime wrote."""
        write = contracts.ToolContract("mcp_files_patch", (), policy_action=policy.WRITE,
                                       side_effect=contracts.LEVEL_WRITE)
        capped = gate.SecurityGate(provenance=self.origin,
                                   max_external_side_effect=contracts.LEVEL_READ)
        with self.assertRaises(contracts.PolicyDenied) as caught:
            capped.admit(write, {"action": "mcp_files_patch"}, verdict=policy.decide)
        self.assertIn("not offered at all", str(caught.exception))
        self.assertTrue(capped.allows(contracts.ToolContract(
            "mcp_files_read", (), policy_action=policy.READ, side_effect=contracts.LEVEL_READ),
            verdict=policy.decide))

    def test_the_denial_of_a_declared_class_ends_the_asking(self):
        """A second question about a call the folder already closed costs the model a turn and tells
        it nothing it did not already have."""
        asked = []

        def answer(action_class):
            asked.append(action_class)
            return policy.DENY if action_class == policy.READ else policy.ASK

        with self.assertRaises(contracts.PolicyDenied) as caught:
            gate.gate_for(self.origin).admit(self.network,
                                             {"action": "mcp_files_fetch_url", "url": "x"},
                                             verdict=answer)
        self.assertEqual(asked, [policy.READ])
        self.assertIn("denies", str(caught.exception))


class APreflightThatAgreesWithTheGate(GateTests):
    """`allows` is the same judgement without an envelope, or the offer and the call drift apart."""

    def test_every_answer_allows_gives_is_the_answer_admit_would_give(self):
        candidates = (
            (self.context(answer=policy.ALLOW), tools.BUILTIN_CONTRACTS[1], gate.native()),
            (self.context(answer=policy.DENY), tools.BUILTIN_CONTRACTS[5], gate.native()),
        )
        for context, contract, origin in candidates:
            admission = gate.gate_for(origin)
            allowed = admission.allows(contract, verdict=context.verdict, guard=context.guard)
            try:
                admission.admit(contract, {"action": contract.name, "path": "app.py"},
                                verdict=context.verdict, guard=context.guard)
                admitted = True
            except contracts.ContractError:
                admitted = False
            self.assertEqual(allowed, admitted, contract.name)

    def test_a_pre_flight_never_offers_an_outside_tool_the_operator_capped(self):
        origin = gate.external("mcp:files")
        admission = gate.SecurityGate(provenance=origin,
                                      max_external_side_effect=contracts.LEVEL_READ)
        self.assertFalse(admission.allows(
            contracts.ToolContract("mcp_files_patch", (), policy_action=policy.WRITE,
                                   side_effect=contracts.LEVEL_WRITE),
            verdict=lambda item: policy.ALLOW))

    def test_a_pre_flight_of_a_native_tool_asks_only_what_the_call_would_ask(self):
        admission = gate.NATIVE_GATE

        def answer(action_class):
            self.asked.append(action_class)
            return policy.ALLOW

        self.assertTrue(admission.allows(tools.BUILTIN_CONTRACTS[1], verdict=answer))
        self.assertEqual(self.asked, [policy.READ])
