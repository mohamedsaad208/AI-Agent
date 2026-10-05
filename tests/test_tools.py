"""What the tool layer guarantees about one turn's asking.

The loop in `engine.plan` is far too large to exercise one action at a time, so the fetching
tools are pinned here against a real workspace on disk: the exact field set each one accepts —
judged now by its `contracts.ToolContract`, which is what the production loop runs against — the
observation each returns, and — the half a model actually learns from — the advice that rides along
when an answer comes back empty. The execution tools (`run_tests`, `run_build`, `git_diff`) are
pinned here too: the command is always the runtime's own choice from the project's build files, and
the one security property that matters — no model text ever reaches an argv — is asserted as an
envelope refusal. The two cross-cutting connections are pinned here as well: the policy verdict the
registry asks before any handler runs (with the test's own answer standing in for the table), and the
state delta each tool hands back for the continuity record — written from the observation, so a
failed check survives the trim its output would not have. The two dialects an envelope is made
canonical from before any
field set is judged are pinned here as well, since the contract layer settles both for the tools
and for the loop's own control envelopes.
`propose`, `blocked` and `complete` are control flow, not tools, and are
asserted only as vocabulary, because the refusal for an unknown action has to list them or it hands
the model a menu missing the one thing it was trying to do.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import contracts, policy, symbols, tools
from ai_code_engineer.config import Settings
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.workspace import Workspace


class ToolTests(unittest.TestCase):
    """A real workspace, and the three callbacks a tool is allowed to call."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.registry = tools.default_registry()
        self.observed = {}
        self.events = []
        self.announced = []
        self.said = []

    def workspace(self, files=None):
        for name, content in (files or {}).items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
        return Workspace(self.root)

    def context(self, ws):
        return tools.ToolContext(
            ws=ws, settings=Settings(), observed=self.observed,
            record=lambda kind, **fields: self.events.append((kind, fields)),
            announce=lambda action, **fields: self.announced.append((action, fields)),
            progress=self.said.append)

    def context_under(self, ws, verdict):
        """A context whose policy answer is the test's, asked with the class of each tool run."""
        asked = []

        def answer(action_class):
            asked.append(action_class)
            return verdict

        context = self.context(ws)
        context.verdict = answer
        return context, asked

    def run_tool(self, action, ws):
        return self.registry.run(action, self.context(ws))

    # -- the vocabulary ----------------------------------------------------

    def test_the_vocabulary_lists_the_tools_first_and_the_control_actions_last(self):
        """The order a refusal repeats is the order the prompt introduced them in."""
        self.assertEqual(self.registry.names(),
                         ("list_files", "read_file", "search_code", "find_symbol",
                          "find_references", "run_tests", "run_build", "git_diff",
                          "propose", "blocked", "complete"))

    def test_gathering_is_exactly_the_tools_and_never_a_control_action(self):
        """The fast/strong split sends these turns to the cheap model, so a deciding action
        landing in the set would put a proposal in front of the model chosen for envelopes."""
        self.assertEqual(tools.GATHER_ACTIONS,
                         frozenset({"list_files", "read_file", "search_code", "find_symbol",
                                    "find_references", "run_tests", "run_build", "git_diff"}))
        self.assertEqual(tools.GATHER_ACTIONS & set(tools.CONTROL_ACTIONS), frozenset())

    def test_an_unknown_action_is_refused_naming_every_action_the_model_could_have_sent(self):
        with self.assertRaises(contracts.UnknownTool) as caught:
            self.run_tool({"action": "delete_everything"}, self.workspace())
        message = str(caught.exception)
        self.assertIn("action delete_everything with fields action", message)
        for name in self.registry.names():
            self.assertIn(name, message, name)

    def test_a_known_tool_asked_for_with_an_extra_field_is_refused_not_half_obeyed(self):
        """A model that adds a hopeful `content` to a read is guessing at the contract; silently
        ignoring the key teaches it that the guess worked."""
        ws = self.workspace({"app.py": "print(1)\n"})
        with self.assertRaises(contracts.MalformedCall):
            self.run_tool({"action": "read_file", "path": "app.py", "content": "x"}, ws)
        self.assertEqual(self.observed, {})

    def test_a_propose_envelope_is_refused_by_the_registry_as_outside_its_vocabulary(self):
        """`propose` is named in the menu and handled by the caller, so the registry must not
        quietly accept it: the loop decides what a proposal means."""
        with self.assertRaises(contracts.UnknownTool):
            self.run_tool({"action": "propose", "changes": []}, self.workspace())

    def test_a_complete_envelope_is_refused_by_the_registry_as_outside_its_vocabulary(self):
        """`complete` closes the task, which is control flow the loop owns, so the registry
        refuses it the way it refuses `propose` and names it in the refusal menu."""
        with self.assertRaises(contracts.UnknownTool) as caught:
            self.run_tool({"action": "complete", "summary": "nothing to do"}, self.workspace())
        self.assertIn("complete", str(caught.exception))

    # -- management --------------------------------------------------------

    def test_registering_a_second_tool_under_one_name_is_refused(self):
        """Keeping the first would leave a tool that looks registered and never runs."""
        registry = tools.ToolRegistry()
        contract = contracts.ToolContract("list_files")
        handler = lambda call, ctx: contracts.Result(contracts.OK)
        registry.register(contract, handler)
        with self.assertRaises(ValueError):
            registry.register(contract, handler)

    def test_a_custom_tool_joins_the_vocabulary_and_runs_on_the_same_context(self):
        registry = tools.ToolRegistry(control=tools.CONTROL_ACTIONS)
        registry.register(
            contracts.ToolContract("count_widgets", (contracts.Field("query"),)),
            lambda call, ctx: contracts.Result(contracts.OK,
                                               {"widgets": len(call.get("query"))},
                                               advice="Propose instead of blocking."))
        self.assertEqual(registry.names(),
                         ("count_widgets", "propose", "blocked", "complete"))
        outcome = registry.run({"action": "count_widgets", "query": "abc"},
                               self.context(self.workspace()))
        self.assertEqual(outcome.data, {"widgets": 3})
        self.assertEqual(outcome.advice, "Propose instead of blocking.")

    # -- the policy boundary -----------------------------------------------

    def test_every_builtin_tool_names_the_policy_class_its_execution_belongs_to(self):
        """The class is what the gate is asked before a handler may run, so a tool that runs the
        project's own command has to say so rather than sitting under the reading class."""
        self.assertEqual(
            {contract.name: contract.policy_action for contract in tools.BUILTIN_CONTRACTS},
            {"list_files": policy.READ, "read_file": policy.READ, "search_code": policy.READ,
             "find_symbol": policy.READ, "find_references": policy.READ,
             "run_tests": policy.EXECUTE_RECIPE, "run_build": policy.EXECUTE_RECIPE,
             "git_diff": policy.GIT_LOCAL})

    def test_a_class_the_policy_denies_never_reaches_the_handler(self):
        ran = []
        registry = tools.ToolRegistry()
        registry.register(contracts.ToolContract("run_tests", (),
                                                 policy_action=policy.EXECUTE_RECIPE),
                          lambda call, ctx: ran.append(True))
        context, asked = self.context_under(self.workspace(), policy.DENY)
        with self.assertRaises(contracts.PolicyDenied) as caught:
            registry.run({"action": "run_tests"}, context)
        self.assertEqual(ran, [], "a denied class must not execute")
        self.assertEqual(asked, [policy.EXECUTE_RECIPE], "the verdict was asked for the tool's class")
        message = str(caught.exception)
        self.assertIn("denies", message)
        self.assertIn("run_tests", message)
        self.assertIn("Do not retry", message)
        self.assertFalse(contracts.is_retryable(caught.exception))

    def test_an_ask_is_refused_because_a_task_run_has_nobody_to_ask(self):
        """The operator's word an ask wants cannot arrive in a task run, and running anyway would
        be answering for them — the refusal `cli.refuses_policy` already makes for a typed apply."""
        ran = []
        registry = tools.ToolRegistry()
        registry.register(contracts.ToolContract("run_tests", (),
                                                 policy_action=policy.EXECUTE_RECIPE),
                          lambda call, ctx: ran.append(True))
        context, _asked = self.context_under(self.workspace(), policy.ASK)
        with self.assertRaises(contracts.PolicyDenied) as caught:
            registry.run({"action": "run_tests"}, context)
        self.assertEqual(ran, [])
        message = str(caught.exception)
        self.assertIn("asks before", message)
        self.assertIn("nobody is typing", message)
        self.assertIn("run_tests", message)

    def test_a_verdict_the_gate_cannot_read_falls_closed_like_an_unknown_override(self):
        """A gate that falls open is not a gate: an answer outside the three verdicts is refused
        with the denials, the way `policy.decide` answers an override it cannot parse."""
        ran = []
        registry = tools.ToolRegistry()
        registry.register(contracts.ToolContract("git_diff", (),
                                                 policy_action=policy.GIT_LOCAL),
                          lambda call, ctx: ran.append(True))
        context, _asked = self.context_under(self.workspace(), "maybe")
        with self.assertRaises(contracts.PolicyDenied) as caught:
            registry.run({"action": "git_diff"}, context)
        self.assertEqual(ran, [])
        self.assertIn("denies", str(caught.exception))

    def test_an_allowed_class_runs_on_the_context_that_answered(self):
        ws = self.workspace({"app.py": "print(1)\n"})
        context, asked = self.context_under(ws, policy.ALLOW)
        outcome = self.registry.run({"action": "read_file", "path": "app.py"}, context)
        self.assertEqual(outcome.data["content"], "print(1)\n")
        self.assertEqual(asked, [policy.READ])

    def test_the_default_verdict_is_the_table_alone(self):
        """No caller passed an answer, so the classes the tools run under are judged by the module
        that owns them — and those classes are managed, so the table's own answer is the whole one."""
        context = self.context(self.workspace())
        for action_class in (policy.READ, policy.EXECUTE_RECIPE, policy.GIT_LOCAL):
            self.assertEqual(context.verdict(action_class), policy.decide(action_class), action_class)

    def test_a_tool_that_names_no_class_is_the_callers_own_and_is_not_gated(self):
        """The gate judges what the registry can classify: a class the caller never named is the
        caller's extension, and the model can only ask for it because the caller registered it."""
        registry = tools.ToolRegistry()
        registry.register(contracts.ToolContract("count_widgets", (contracts.Field("query"),)),
                          lambda call, ctx: contracts.Result(contracts.OK, {"widgets": 1}))
        context, asked = self.context_under(self.workspace(), policy.DENY)
        outcome = registry.run({"action": "count_widgets", "query": "a"}, context)
        self.assertEqual(outcome.data, {"widgets": 1})
        self.assertEqual(asked, [], "a tool with no class has no class to ask about")

    # -- the execution posture ----------------------------------------------

    def test_a_sandbox_below_what_a_contract_reaches_is_refused_before_the_handler_runs(self):
        """The policy verdict judges the folder's rule and the guard judges the run's posture;
        a tool the folder allows still may not run in a read-only run."""
        ws = self.workspace({"tests/test_app.py": self.PASSING_TEST})
        context = self.context(ws)
        context.guard = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_READ)
        with self.assertRaises(contracts.SandboxDenied) as caught:
            self.registry.run({"action": "run_tests"}, context)
        self.assertEqual(self.events, [], "a refused run announces nothing")
        self.assertEqual(context.call_id, "", "and leaves no id behind")
        self.assertIn("above this sandbox", str(caught.exception))

    # -- the envelope a turn arrives in ------------------------------------

    def test_an_asking_nested_under_a_wrapper_is_the_same_asking(self):
        """The function-calling dialect is what a model is tuned on, so the fields arrive nested;
        the flat envelope the prompt shows is one way of saying it and not the only one."""
        flat = {"action": "read_file", "path": "app.py"}
        for wrapper in contracts.ARG_WRAPPERS:
            with self.subTest(wrapper=wrapper):
                self.assertEqual(contracts.unwrap_args({"action": "read_file", wrapper: {"path": "app.py"}}),
                                 flat)
        self.assertEqual(contracts.unwrap_args(dict(flat)), flat, "nothing nested, nothing changed")

    def test_an_asking_that_nested_itself_entirely_is_still_found(self):
        """A model that wraps the whole envelope leaves no `action` at the top; lifting the wrapper
        is what names the action at all."""
        self.assertEqual(contracts.unwrap_args({"args": {"action": "list_files"}}),
                         {"action": "list_files"})

    def test_a_field_said_twice_with_two_values_is_refused_rather_than_guessed_at(self):
        """Which of the two paths was meant is not knowable here, and picking one would read a file
        the model never asked for."""
        with self.assertRaises(contracts.MalformedCall) as caught:
            contracts.unwrap_args({"action": "read_file", "path": "app.py",
                               "args": {"path": "other.py"}})
        self.assertIn("path", str(caught.exception))
        self.assertIn("Send each field once", str(caught.exception))

    def test_a_field_repeated_with_the_same_value_is_not_a_contradiction(self):
        """A wrapper echoing the envelope it sits in asked one thing; refusing it would cost a turn
        to settle nothing."""
        self.assertEqual(contracts.unwrap_args({"action": "read_file", "path": "app.py",
                                            "args": {"action": "read_file", "path": "app.py"}}),
                         {"action": "read_file", "path": "app.py"})

    def test_a_wrapper_that_is_not_an_object_is_left_for_the_shape_refusal_to_name(self):
        """Unwrapping is for objects. A string under `args` is a malformed envelope, and the refusal
        that names the field the model actually sent is more use than a guess at what it meant."""
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.run_tool({"action": "read_file", "args": "app.py"},
                          self.workspace({"app.py": "print(1)\n"}))
        self.assertIn("with fields action, args", str(caught.exception))
        self.assertEqual(self.observed, {})

    def test_the_explanation_is_taken_out_whichever_of_the_two_names_it_came_in(self):
        for key in contracts.RATIONALE_KEYS:
            with self.subTest(key=key):
                action = {"action": "blocked", key: "no such file"}
                self.assertEqual(contracts.pop_rationale(action), "no such file")
                self.assertEqual(action, {"action": "blocked"},
                                 "the field checks judge the asking, so the prose has to be out of it")

    def test_the_name_the_prompt_asked_for_wins_when_a_model_sends_both(self):
        action = {"action": "blocked", "reason": "the one asked for", "thought": "the other one"}
        self.assertEqual(contracts.pop_rationale(action), "the one asked for")
        self.assertEqual(action, {"action": "blocked"})

    def test_an_explanation_that_is_not_a_sentence_explains_nothing(self):
        """`blocked` is judged on what comes back from here, so a reason of the wrong type has to
        arrive as no reason at all rather than as something a loop would record."""
        action = {"action": "blocked", "reason": 42}
        self.assertEqual(contracts.pop_rationale(action), "")
        self.assertEqual(action, {"action": "blocked"})

    def test_a_tool_is_obeyed_when_its_asking_was_nested_and_explained(self):
        ws = self.workspace({"app.py": "print(1)\n"})
        outcome = self.run_tool({"action": "read_file", "thought": "the task names this file",
                                 "args": {"path": "app.py"}}, ws)
        self.assertEqual(outcome.data["content"], "print(1)\n")
        self.assertEqual(self.observed, {"app.py": outcome.data["sha256"]})

    def test_a_contract_field_is_still_exact_once_the_dialects_are_settled(self):
        """Flexibility is about where a field was put, never about which fields an action takes:
        the hopeful `content` is refused exactly as it was before."""
        with self.assertRaises(contracts.MalformedCall):
            self.run_tool({"action": "read_file", "args": {"path": "app.py", "content": "x"}},
                          self.workspace({"app.py": "print(1)\n"}))
        self.assertEqual(self.observed, {})

    # -- list_files --------------------------------------------------------

    def test_list_files_answers_with_the_names_and_says_when_it_stopped_early(self):
        ws = self.workspace({"app.py": "print(1)\n", "lib/util.py": "def f():\n    return 1\n"})
        outcome = self.run_tool({"action": "list_files"}, ws)
        self.assertEqual(set(outcome.data["files"]), {"app.py", "lib/util.py"})
        self.assertFalse(outcome.data["truncated"])
        self.assertEqual(outcome.advice, "")
        self.assertEqual(self.events, [("tool", {"name": "list_files", "count": 2})])
        self.assertEqual(self.announced, [("list_files", {"count": 2})])

    def test_an_empty_project_is_answered_as_an_invitation_to_propose(self):
        """A blank observation with nothing after it is the shape a small model replies to by
        ending the task, which is why the emptiness carries its own remedy — and says so as a
        status rather than leaving the caller to infer it from a list that happens to be empty."""
        outcome = self.run_tool({"action": "list_files"}, self.workspace())
        self.assertEqual(outcome.status, contracts.EMPTY)
        self.assertEqual(outcome.data, {"files": [], "truncated": False})
        self.assertIn("instead of blocking", outcome.advice)

    # -- read_file ---------------------------------------------------------

    def test_reading_a_file_authorises_a_proposal_against_the_digest_it_returned(self):
        ws = self.workspace({"app.py": "print(1)\n"})
        outcome = self.run_tool({"action": "read_file", "path": "app.py"}, ws)
        self.assertEqual(outcome.data["content"], "print(1)\n")
        self.assertEqual(outcome.advice, "")
        self.assertEqual(self.observed, {"app.py": outcome.data["sha256"]})
        self.assertEqual(self.announced,
                         [("read_file", {"path": "app.py",
                                         "digest": outcome.data["sha256"][:8]})])

    def test_a_missing_file_that_may_be_created_is_an_observation_and_not_a_failure(self):
        ws = self.workspace({"app.py": "print(1)\n"})
        # An authorisation from an earlier turn, for a path that has since stopped existing. What
        # is left behind is a digest of content nobody can now read, so it has to go.
        self.observed["new.py"] = "stale-digest"
        outcome = self.run_tool({"action": "read_file", "path": "new.py"}, ws)
        self.assertEqual(outcome.data["status"], "not_found")
        self.assertTrue(outcome.data["can_create"])
        self.assertEqual(self.observed, {})
        self.assertIn("propose it as a new file at new.py", outcome.advice)
        self.assertIn("File not found: new.py", self.said[0])
        self.assertEqual(self.events,
                         [("file_not_found", {"path": "new.py", "can_create": True})])

    def test_a_missing_instruction_file_is_reported_as_one_nobody_may_create(self):
        """Readable, so the miss is a real not-found rather than a policy refusal — and not
        writable, so the observation must not invite a proposal the loop would have to reject."""
        outcome = self.run_tool({"action": "read_file", "path": "AGENTS.md"}, self.workspace())
        self.assertEqual(outcome.data["status"], "not_found")
        self.assertFalse(outcome.data["can_create"])
        self.assertEqual(outcome.advice, "")
        self.assertEqual(outcome.data["next_step"],
                         "This path cannot be created under the current policy.")

    def test_a_file_too_large_for_the_window_is_refused_before_it_is_authorised(self):
        """The read is refused rather than truncated: an authorisation against half a file is what
        lets a proposal replace the half the model never saw — and the refusal now hands over the
        remedy it used to withhold, because the window is the model's to ask for."""
        ws = self.workspace({"big.py": "x" * 40000})
        with self.assertRaises(PolicyError) as caught:
            self.run_tool({"action": "read_file", "path": "big.py"}, ws)
        self.assertIn("offset", str(caught.exception))
        self.assertEqual(self.observed, {})

    def test_a_windowed_read_returns_its_lines_and_says_where_the_file_continues(self):
        ws = self.workspace({"app.py": "".join("line %d\n" % i for i in range(1, 11))})
        outcome = self.run_tool({"action": "read_file", "path": "app.py",
                                 "offset": 3, "limit": 4}, ws)
        self.assertEqual(outcome.data["content"], "line 3\nline 4\nline 5\nline 6\n")
        self.assertEqual((outcome.data["from_line"], outcome.data["to_line"],
                          outcome.data["total_lines"], outcome.data["truncated"]),
                         (3, 6, 10, True))
        self.assertIn("offset=7", outcome.data["next_step"])
        self.assertEqual(self.observed, {})

    def test_a_window_that_reaches_the_end_still_does_not_authorise_a_rewrite(self):
        """A tail the model saw in full is not the file in full: the digest gates writes, and the
        gate opens only for an answer that carried every line."""
        ws = self.workspace({"app.py": "".join("line %d\n" % i for i in range(1, 6))})
        outcome = self.run_tool({"action": "read_file", "path": "app.py", "offset": 4}, ws)
        self.assertEqual(outcome.data["content"], "line 4\nline 5\n")
        self.assertFalse(outcome.data["truncated"])
        self.assertEqual(self.observed, {})
        self.assertIn("complete read", outcome.data["next_step"])

    def test_a_read_from_line_one_that_covers_the_file_authorises_like_a_full_read(self):
        ws = self.workspace({"app.py": "a\nb\nc\n"})
        outcome = self.run_tool({"action": "read_file", "path": "app.py",
                                 "offset": 1, "limit": 50}, ws)
        self.assertEqual(outcome.data["content"], "a\nb\nc\n")
        self.assertEqual(self.observed, {"app.py": outcome.data["sha256"]})

    def test_an_offset_past_the_end_is_an_answer_with_the_line_count(self):
        ws = self.workspace({"app.py": "a\nb\n"})
        outcome = self.run_tool({"action": "read_file", "path": "app.py",
                                 "offset": 9, "limit": 5}, ws)
        self.assertEqual(outcome.status, contracts.EMPTY)
        self.assertIn("this file has 2 lines", outcome.data["next_step"])

    def test_a_file_above_the_whole_read_limit_still_opens_in_windows(self):
        ws = self.workspace({"big.py": "".join("line %d\n" % i for i in range(1, 20001))})
        with self.assertRaises(PolicyError):
            self.run_tool({"action": "read_file", "path": "big.py"}, ws)
        outcome = self.run_tool({"action": "read_file", "path": "big.py",
                                 "offset": 1, "limit": 3}, ws)
        self.assertEqual(outcome.data["content"], "line 1\nline 2\nline 3\n")
        self.assertTrue(outcome.data["truncated"])

    def test_a_window_outside_its_bounds_is_refused_at_the_envelope(self):
        ws = self.workspace({"app.py": "a\n"})
        for bad in ({"offset": 0}, {"limit": 0}, {"limit": 3000}):
            call = {"action": "read_file", "path": "app.py"}
            call.update(bad)
            with self.assertRaises(contracts.MalformedCall):
                self.run_tool(call, self.workspace({"app.py": "a\n"}))

    def test_a_windowed_read_announces_its_line_slice_to_the_user(self):
        ws = self.workspace({"app.py": "a\nb\nc\nd\ne\n"})
        outcome = self.run_tool({"action": "read_file", "path": "app.py", "offset": 2, "limit": 3}, ws)
        self.assertEqual(self.announced, [
            ('read_file', {'digest': outcome.data["sha256"][:8], 'path': 'app.py',
                           'from_line': 2, 'to_line': 4, 'total_lines': 5})
        ])

    # -- search_code -------------------------------------------------------

    def test_a_search_that_matches_nothing_says_so_and_points_at_proposing(self):
        ws = self.workspace({"app.py": "print(1)\n"})
        outcome = self.run_tool({"action": "search_code", "query": "no_such_token"}, ws)
        self.assertEqual(outcome.data, {"matches": []})
        self.assertIn("instead of blocking", outcome.advice)
        self.assertEqual(self.announced, [("search_code", {"query": "no_such_token", "count": 0})])

    # -- find_symbol / find_references -------------------------------------

    def test_find_symbol_answers_with_the_declaration_and_publishes_the_index_it_rebuilt(self):
        """`propose` reads these same rows for its impact report, so a tool that rebuilt the index
        has to leave the fresh copy on the context instead of taking it back out of scope."""
        ws = self.workspace({"app.py": "class Calc:\n    def add(self, a, b):\n        return a + b\n"})
        context = self.context(ws)
        context.rows = []
        outcome = self.registry.run({"action": "find_symbol", "query": "Calc"}, context)
        self.assertIn("Calc", [hit["name"] for hit in outcome.data["declarations"]])
        self.assertTrue(context.rows)
        self.assertEqual(outcome.advice, "")

    def test_an_empty_symbol_search_is_an_answer_rather_than_a_reason_to_search_again(self):
        ws = self.workspace({"app.py": "print(1)\n"})
        outcome = self.run_tool({"action": "find_symbol", "query": "NoSuchThing"}, ws)
        self.assertEqual(outcome.data["declarations"], [])
        self.assertIn("instead of searching again", outcome.data["next_step"])
        self.assertIn("instead of blocking", outcome.advice)

    def test_find_references_states_the_ceilings_it_always_obeyed(self):
        """One file with twenty uses reports six and looks complete, so the cap is stated rather
        than left for the model to infer from an answer that cannot see its own truncation."""
        ws = self.workspace({"calc.py": "def add(a, b):\n    return a + b\n",
                             "app.py": "import calc\ncalc.add(1, 2)\n"})
        outcome = self.run_tool({"action": "find_references", "query": "add"}, ws)
        self.assertTrue(outcome.data["sites"])
        self.assertEqual(outcome.data["caps"],
                         {"total": symbols.MAX_HITS, "per_file": symbols.PER_FILE_LIMIT})
        self.assertFalse(outcome.data["truncated"])

    def test_a_reference_nobody_makes_is_an_answer_that_points_at_the_text_search(self):
        ws = self.workspace({"app.py": "print(1)\n"})
        outcome = self.run_tool({"action": "find_references", "query": "NoSuchThing"}, ws)
        self.assertEqual(outcome.data["sites"], [])
        self.assertIn("search_code", outcome.data["next_step"])
        self.assertIn("or propose:", outcome.advice)

    # -- run_tests / run_build ---------------------------------------------

    # A passing suite and a failing one: the recipe is the machine's own interpreter running
    # unittest discovery, so both run for real in the throwaway copy and answer in seconds.
    PASSING_TEST = ("import unittest\n\n\nclass One(unittest.TestCase):\n"
                    "    def test_ok(self):\n        self.assertTrue(True)\n")
    FAILING_TEST = ("import unittest\n\n\nclass One(unittest.TestCase):\n"
                    "    def test_bad(self):\n        self.assertTrue(False, \"boom\")\n")

    def test_run_tests_with_no_build_file_is_an_answer_that_points_at_proposing(self):
        outcome = self.run_tool({"action": "run_tests"}, self.workspace())
        self.assertEqual(outcome.data["status"], "unavailable")
        self.assertIn("propose", outcome.advice)
        self.assertEqual(self.announced, [("run_tests", {"label": ""})])

    def test_run_tests_reports_a_passing_verdict_and_no_advice(self):
        ws = self.workspace({"tests/test_app.py": self.PASSING_TEST})
        outcome = self.run_tool({"action": "run_tests"}, ws)
        self.assertEqual(outcome.data["status"], "passed")
        self.assertTrue(outcome.data["tests_observed"])
        self.assertEqual(outcome.advice, "")
        self.assertEqual(self.events,
                         [("tool", {"name": "run_tests", "recipe": "python-unittest",
                                    "status": "passed", "seconds": outcome.data["seconds"]})])
        # The verdict rides to the continuity record from here, so a trim cannot drop it with the
        # turn that carried the output.
        self.assertEqual(outcome.state, {"evidence": ["run_tests (python-unittest): passed"]})

    def test_a_failing_run_carries_the_first_failure_into_the_state_delta(self):
        """The tail is what the *next* turn reads; this line is what the record keeps when that turn
        is long gone, so it has to name the verdict and the first thing the output blamed."""
        ws = self.workspace({"tests/test_app.py": self.FAILING_TEST})
        outcome = self.run_tool({"action": "run_tests"}, ws)
        self.assertEqual(outcome.data["status"], "failed")
        self.assertEqual(outcome.state,
                         {"evidence": ["run_tests (python-unittest): failed — "
                                       + outcome.data["failures"][0]]})

    def test_a_check_that_proved_nothing_is_not_evidence(self):
        """No recipe, or a run that never happened, establishes nothing about the code; writing it
        into the evidence list would teach the continuity record to hold verdicts that are not ones."""
        self.assertEqual(self.run_tool({"action": "run_tests"}, self.workspace()).state, {})
        ws = self.workspace({"tests/test_app.py": self.PASSING_TEST})
        self.assertEqual(self.run_tool({"action": "run_build"}, ws).state, {},
                         "run_build without a compile step is unavailable, not a verdict")

    def test_a_failing_run_is_evidence_with_a_proposal_for_a_next_step(self):
        ws = self.workspace({"tests/test_app.py": self.FAILING_TEST})
        outcome = self.run_tool({"action": "run_tests"}, ws)
        self.assertEqual(outcome.status, contracts.OK,
                         "a suite that failed is an observation that succeeded — the failure "
                         "is inside the evidence, not the status of the call")
        self.assertEqual(outcome.data["status"], "failed")
        self.assertTrue(outcome.data["failures"])
        self.assertIn("output_tail", outcome.data)
        self.assertIn("propose the smallest change", outcome.advice)
        self.assertIn("read that file", outcome.data["next_step"])

    def test_run_build_without_a_compile_step_says_so_instead_of_running_tests(self):
        """Only Maven has a separate compile recipe; a Python project answered with a build run
        would be the tests under another name, which is the dishonesty this observation refuses."""
        ws = self.workspace({"tests/test_app.py": self.PASSING_TEST})
        outcome = self.run_tool({"action": "run_build"}, ws)
        self.assertEqual(outcome.data["status"], "unavailable")
        self.assertIn("no separate compile step", outcome.data["reason"])
        self.assertEqual(outcome.advice, "")

    def test_an_execution_action_never_accepts_a_command_field(self):
        """The one security property of the execution tools: model prose cannot smuggle an argv,
        so a hopeful `command` is refused on the envelope like any other wrong field."""
        ws = self.workspace({"tests/test_app.py": self.PASSING_TEST})
        with self.assertRaises(contracts.MalformedCall):
            self.run_tool({"action": "run_tests", "command": "python -c import os"}, ws)
        with self.assertRaises(contracts.MalformedCall):
            self.run_tool({"action": "run_build", "command": "mvn deploy"}, ws)

    # -- git_diff ----------------------------------------------------------

    def test_git_diff_outside_a_repository_is_an_answer_not_a_failure(self):
        outcome = self.run_tool({"action": "git_diff"}, self.workspace())
        self.assertEqual(outcome.data["status"], "unavailable")
        self.assertTrue(outcome.data["reason"])

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_git_diff_shows_uncommitted_changes_and_names_untracked_files(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        # Bytes, not text: a CRLF worktree against an LF index (autocrlf) is a phantom diff that
        # would make a clean tree read as dirty.
        (self.root / "a.txt").write_bytes(b"one\n")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True, env=env)
        subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
                       cwd=self.root, check=True, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (self.root / "a.txt").write_bytes(b"two\n")
        (self.root / "b.txt").write_bytes(b"new\n")
        ws = Workspace(self.root)
        outcome = self.run_tool({"action": "git_diff"}, ws)
        self.assertEqual(outcome.data["status"], "ok")
        self.assertIn("-one", outcome.data["diff"])
        self.assertIn("+two", outcome.data["diff"])
        self.assertEqual(outcome.data["untracked"], ["b.txt"])
        self.assertEqual(outcome.advice, "")
        self.assertEqual(outcome.state, {"evidence": ["git_diff: uncommitted changes present (1 untracked)"]})

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_git_diff_on_a_clean_tree_is_an_empty_answer_that_points_at_proposing(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        (self.root / "a.txt").write_bytes(b"one\n")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True, env=env)
        subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
                       cwd=self.root, check=True, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = Workspace(self.root)
        outcome = self.run_tool({"action": "git_diff"}, ws)
        self.assertEqual(outcome.data["status"], "ok")
        self.assertEqual(outcome.data["diff"], "")
        self.assertIn("Propose it", outcome.advice)
        self.assertEqual(outcome.state, {"evidence": ["git_diff: nothing uncommitted against HEAD"]},
                         "the cleanliness of the tree is a fact about this instant the record keeps")

    # -- the ids one call is keyed by ----------------------------------------------

    def test_an_outcome_carries_the_run_id_and_the_id_its_row_was_announced_under(self):
        """One id for the row and the outcome, so a log line naming either points at both."""
        ws = self.workspace({"app.py": "print(1)\n"})
        context = self.context(ws)
        context.trace_id = "the run this call belongs to"
        row_ids = []

        def announce(action, **fields):
            row_ids.append(context.take_call_id())
            self.announced.append((action, fields))

        context.announce = announce
        outcome = self.registry.run({"action": "read_file", "path": "app.py"}, context)
        self.assertEqual(row_ids, [outcome.call_id])
        self.assertEqual(outcome.trace_id, "the run this call belongs to")

    def test_two_calls_in_one_run_share_the_run_id_and_never_the_call_id(self):
        ws = self.workspace({"app.py": "print(1)\n"})
        context = self.context(ws)
        context.trace_id = "one run"
        first = self.registry.run({"action": "read_file", "path": "app.py"}, context)
        second = self.registry.run({"action": "list_files"}, context)
        self.assertEqual(first.trace_id, second.trace_id)
        self.assertNotEqual(first.call_id, second.call_id)
        self.assertRegex(first.call_id, r"^[0-9a-f]{8}$")

    def test_the_in_flight_id_is_taken_once_so_two_rows_for_one_call_share_no_key(self):
        context = self.context(self.workspace())
        context.call_id = "abc12345"
        self.assertEqual(context.take_call_id(), "abc12345")
        self.assertEqual(context.take_call_id(), "")

    def test_a_call_is_not_left_in_flight_for_the_next_row_to_be_written_under(self):
        """The id is the registry's to hand over and to take back, whatever the handler did with it."""
        ws = self.workspace({"app.py": "print(1)\n"})
        context = self.context(ws)  # this harness's announce never takes the id
        outcome = self.registry.run({"action": "read_file", "path": "app.py"}, context)
        self.assertEqual(context.call_id, "")
        self.assertRegex(outcome.call_id, r"^[0-9a-f]{8}$")

    def test_a_refused_call_leaves_no_id_for_the_next_action_to_inherit(self):
        """A refusal is answered before a handler runs, so nothing announces under its id."""
        context = self.context(self.workspace())
        with self.assertRaises(contracts.UnknownTool):
            self.registry.run({"action": "delete_everything"}, context)
        self.assertEqual(context.call_id, "")


if __name__ == "__main__":
    unittest.main()
