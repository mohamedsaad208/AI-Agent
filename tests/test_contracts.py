"""The trace identifiers one call is keyed by, and the one place they are allowed to be judged.

`contracts.py` refuses a call whose envelope is wrong, and an id is the part of an envelope it must
not refuse *for meaning*: an id belongs to whoever sent the call, and this layer mints none. What it
does owe the caller is a promise that an id sent in an envelope comes back on the `Call`, on the row
a refusal writes, and on the `Result` handed back — because the whole reason an id exists is that
three different pieces of code produce those three things, and only the id says they were one event.
The tests here pin exactly that carry-through: the two dialects an envelope arrives in, the refusal
of a value that is not a string (which would arrive in a log line as the word `None`), and the fact
that an id is never treated as a field the contract does not claim.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import contracts


class TraceIdTests(unittest.TestCase):
    """One contract, judged over and over against the same two ids."""

    def setUp(self):
        self.contract = contracts.ToolContract(
            "read_file", (contracts.Field("path", contracts.PATH),),
            policy_action="read", side_effect=contracts.LEVEL_READ)

    def call(self, envelope):
        return self.contract.validate({"action": "read_file", "path": "src/app.py", **envelope})

    def test_the_ids_a_call_arrived_under_are_the_ids_the_call_carries(self):
        made = self.call({"trace_id": "run-1", "call_id": "step-9"})
        self.assertEqual((made.trace_id, made.call_id), ("run-1", "step-9"))

    def test_ids_are_carried_out_of_a_nested_envelope_exactly_as_out_of_a_flat_one(self):
        """The wrapper is a dialect, and an id sent inside it means the same thing."""
        made = self.contract.validate({"action": "read_file",
                                       "args": {"path": "src/app.py", "trace_id": "run-1",
                                                "call_id": "step-9"}})
        self.assertEqual((made.trace_id, made.call_id), ("run-1", "step-9"))

    def test_an_id_is_not_an_unclaimed_field(self):
        """A caller that sends its trace with the call is not guessing at the contract."""
        self.contract.validate({"action": "read_file", "path": "src/app.py", "trace_id": "run-1"})

    def test_an_id_that_is_not_a_string_is_refused_by_name(self):
        """A non-string id survives into a log line as the word `None`, which correlates nothing."""
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.call({"call_id": 9})
        self.assertIn("call_id", str(caught.exception))

    def test_a_call_without_ids_still_validates(self):
        """Ids are an offer to the caller, never a requirement it can be refused for ignoring."""
        made = self.call({})
        self.assertEqual((made.trace_id, made.call_id), ("", ""))

    def test_traced_hands_the_result_back_under_the_call_own_ids(self):
        made = self.call({"trace_id": "run-1", "call_id": "step-9"})
        stamped = made.traced(contracts.Result.ok({"content": "print(1)"}))
        self.assertEqual((stamped.trace_id, stamped.call_id), ("run-1", "step-9"))
        self.assertEqual(stamped.status, contracts.OK)
        self.assertEqual(stamped.error_code, "")

    def test_traced_carries_the_state_delta_along_with_the_ids(self):
        """The continuity fragment is part of what the call produced; stamping must not drop it."""
        made = self.call({"trace_id": "run-1", "call_id": "step-9"})
        stamped = made.traced(contracts.Result(
            contracts.OK, {"status": "passed"},
            state={"evidence": ["run_tests (python-unittest): passed"]}))
        self.assertEqual(stamped.state, {"evidence": ["run_tests (python-unittest): passed"]})
        self.assertEqual((stamped.trace_id, stamped.call_id), ("run-1", "step-9"))

    def test_a_failure_answered_against_a_call_carries_the_ids_into_the_record(self):
        """The row a refusal writes is the event a later reader has to be able to find again."""
        made = self.call({"trace_id": "run-1", "call_id": "step-9"})
        failed = contracts.Result.fail(contracts.ExecutionFailed("the build did not finish"), made)
        self.assertEqual((failed.trace_id, failed.call_id), ("run-1", "step-9"))
        self.assertEqual(failed.error_code, contracts.EXECUTION_FAILED)
        self.assertTrue(failed.retryable)

    def test_a_call_carrying_no_ids_does_not_erase_the_ids_a_result_already_had(self):
        made = self.contract.validate({"action": "read_file", "path": "src/app.py"})
        stamped = made.traced(contracts.Result(contracts.OK, {"content": ""}, "",
                                               trace_id="run-1", call_id="step-9"))
        self.assertEqual((stamped.trace_id, stamped.call_id), ("run-1", "step-9"))

    def test_a_sandbox_refusal_answered_against_a_call_is_keyed_by_that_call_s_ids(self):
        """A denial and an execution are rows of one shape, so a reader can tell them apart by
        code and find both by the same id."""
        made = self.call({"trace_id": "run-1", "call_id": "step-9"})
        guard = contracts.SandboxGuard(allowed_prefixes=("docs",))
        with self.assertRaises(contracts.SandboxDenied) as caught:
            guard.check(self.contract, made)
        failed = contracts.Result.fail(caught.exception, made)
        self.assertEqual((failed.trace_id, failed.call_id), ("run-1", "step-9"))
        self.assertEqual(failed.error_code, contracts.SANDBOX_DENIED)
        self.assertFalse(failed.retryable)


class FieldValidationTests(unittest.TestCase):
    """One contract carrying every field kind, judged call by call for exactness.

    A valid envelope is the baseline, and every refusal below names the part that was wrong:
    a missing required field, a hopeful extra key, a value of the wrong type, a number outside
    its bound, a list outside its size, a string outside its enum, and a path that traverses.
    """

    def setUp(self):
        self.contract = contracts.ToolContract(
            "search",
            (
                contracts.Field("query", contracts.STRING),
                contracts.Field("path", contracts.PATH),
                contracts.Field("limit", contracts.INTEGER, required=contracts.OPTIONAL,
                                minimum=1, maximum=100),
                contracts.Field("flags", contracts.STRING_LIST, required=contracts.OPTIONAL,
                                min_items=1, max_items=3),
                contracts.Field("mode", contracts.STRING, required=contracts.OPTIONAL,
                                values=("brief", "full")),
                contracts.Field("recursive", contracts.BOOLEAN, required=contracts.OPTIONAL),
            ),
        )

    def envelope(self, **fields):
        call = {"action": "search", "query": "todo", "path": "src"}
        call.update(fields)
        return call

    def test_a_full_valid_call_lands_every_field_in_args(self):
        made = self.contract.validate(self.envelope(
            limit=50, flags=["a", "b"], mode="full", recursive=True))
        self.assertEqual(made.tool, "search")
        self.assertEqual(made.args, {"query": "todo", "path": "src", "limit": 50,
                                     "flags": ["a", "b"], "mode": "full", "recursive": True})
        self.assertEqual(made.rationale, "")

    def test_optional_fields_stay_out_of_args_unless_supplied(self):
        made = self.contract.validate(self.envelope())
        self.assertEqual(made.args, {"query": "todo", "path": "src", "mode": "brief"})

    def test_an_absent_optional_enum_field_defaults_to_its_first_value(self):
        made = self.contract.validate(self.envelope())
        self.assertEqual(made.args["mode"], "brief")

    def test_a_missing_required_field_is_refused_by_name_and_shows_the_shape(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate({"action": "search", "path": "src"})
        message = str(caught.exception)
        self.assertIn("query", message)
        self.assertIn("Expected exactly:", message)

    def test_an_unclaimed_field_is_refused_by_name_not_dropped(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(limitt=5))
        self.assertIn("limitt", str(caught.exception))

    def test_a_non_object_envelope_is_refused(self):
        with self.assertRaises(contracts.MalformedCall):
            self.contract.validate("search path=src")

    def test_a_request_for_another_action_is_refused_as_unknown_tool(self):
        with self.assertRaises(contracts.UnknownTool):
            self.contract.validate(self.envelope(action="write_file"))

    def test_a_string_field_given_a_number_is_refused_by_type_not_value(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(query=7))
        self.assertIn("query must be a string (got int)", str(caught.exception))

    def test_a_float_is_refused_by_an_integer_field(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(limit=2.5))
        self.assertIn("limit must be a whole number (got float)", str(caught.exception))

    def test_a_bool_is_refused_by_an_integer_field(self):
        """True is an int in Python and must still not smuggle itself into a count."""
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(limit=True))
        self.assertIn("whole number", str(caught.exception))

    def test_an_integer_field_given_a_string_is_refused(self):
        with self.assertRaises(contracts.MalformedCall):
            self.contract.validate(self.envelope(limit="5"))

    def test_a_boolean_field_given_a_string_is_refused(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(recursive="yes"))
        self.assertIn("recursive must be true or false (got str)", str(caught.exception))

    def test_a_string_list_field_given_a_bare_string_is_refused(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(flags="abc"))
        self.assertIn("list of strings", str(caught.exception))

    def test_a_string_list_with_a_non_string_entry_is_refused(self):
        with self.assertRaises(contracts.MalformedCall):
            self.contract.validate(self.envelope(flags=["ok", 3]))

    def test_a_tuple_is_accepted_where_a_list_is(self):
        made = self.contract.validate(self.envelope(flags=("a", "b")))
        self.assertEqual(made.args["flags"], ("a", "b"))

    def test_an_integer_below_its_minimum_is_refused_with_both_numbers(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(limit=0))
        self.assertIn("limit must be at least 1 (got 0)", str(caught.exception))

    def test_an_integer_above_its_maximum_is_refused_with_both_numbers(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(limit=101))
        self.assertIn("limit must be at most 100 (got 101)", str(caught.exception))

    def test_the_bound_endpoints_themselves_are_accepted(self):
        self.assertEqual(self.contract.validate(self.envelope(limit=1)).args["limit"], 1)
        self.assertEqual(self.contract.validate(self.envelope(limit=100)).args["limit"], 100)

    def test_a_string_list_below_its_min_items_is_refused(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(flags=[]))
        self.assertIn("at least 1 entries (got 0)", str(caught.exception))

    def test_a_string_list_above_its_max_items_is_refused(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(flags=["a", "b", "c", "d"]))
        self.assertIn("at most 3 entries (got 4)", str(caught.exception))

    def test_a_value_outside_the_enum_is_refused_and_the_enum_is_named(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(mode="verbose"))
        message = str(caught.exception)
        self.assertIn("brief, full", message)
        self.assertNotIn("verbose", message)

    def test_a_path_that_is_absolute_is_refused(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(path="/etc/passwd"))
        self.assertIn("workspace-relative", str(caught.exception))

    def test_a_path_that_walks_out_of_the_workspace_is_refused(self):
        for traversal in ("../secret", "src/../../etc", "a/b/../c"):
            with self.subTest(path=traversal):
                with self.assertRaises(contracts.MalformedCall):
                    self.contract.validate(self.envelope(path=traversal))

    def test_a_plain_relative_path_is_accepted(self):
        made = self.contract.validate(self.envelope(path="src/app.py"))
        self.assertEqual(made.args["path"], "src/app.py")

    def test_a_nested_args_envelope_is_the_same_call_as_a_flat_one(self):
        flat = self.contract.validate(self.envelope())
        nested = self.contract.validate({"action": "search",
                                         "args": {"query": "todo", "path": "src"}})
        self.assertEqual(flat.args, nested.args)

    def test_a_field_given_twice_with_two_values_is_refused_not_guessed_at(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate({"action": "search", "query": "todo", "path": "src",
                                    "args": {"path": "other"}})
        self.assertIn("Contradictory envelope", str(caught.exception))

    def test_a_field_given_twice_with_agreeing_values_is_accepted(self):
        made = self.contract.validate({"action": "search", "query": "todo", "path": "src",
                                       "args": {"path": "src"}})
        self.assertEqual(made.args["path"], "src")

    def test_a_wrapper_that_is_not_an_object_is_refused_by_name(self):
        with self.assertRaises(contracts.MalformedCall) as caught:
            self.contract.validate(self.envelope(args="oops"))
        self.assertIn("args", str(caught.exception))

    def test_the_rationale_leaves_the_envelope_and_rides_on_the_call(self):
        made = self.contract.validate(self.envelope(reason="checking the premise"))
        self.assertEqual(made.rationale, "checking the premise")
        self.assertNotIn("reason", made.args)

    def test_a_thought_names_the_same_sentence_a_reason_does_and_reason_wins_both(self):
        thought = self.contract.validate(self.envelope(thought="from a reasoning model"))
        self.assertEqual(thought.rationale, "from a reasoning model")
        both = self.contract.validate(self.envelope(reason="first", thought="second"))
        self.assertEqual(both.rationale, "first")

    def test_a_non_string_rationale_is_removed_and_ignored_not_refused(self):
        made = self.contract.validate(self.envelope(reason=42))
        self.assertEqual(made.rationale, "")
        self.assertNotIn("reason", made.args)

    def test_the_shape_spelling_carries_bounds_optionality_and_enums(self):
        self.assertEqual(
            self.contract.shape(),
            "search(query, path, [limit[1..100]], [flags[1..3 items]], "
            "mode=brief|full, [recursive])")

    def test_the_menu_is_built_from_the_contracts_it_lists(self):
        self.assertEqual(contracts.menu(()), "No tools are registered.")
        one = contracts.ToolContract("ping", (), purpose="heartbeat")
        self.assertEqual(contracts.menu((one,)), "Allowed: ping()")

    def test_action_shape_names_fields_and_never_values(self):
        said = contracts.action_shape({"action": "search", "query": "topsecret"})
        self.assertIn("query", said)
        self.assertNotIn("topsecret", said)

    def test_a_field_with_an_unknown_kind_refuses_construction(self):
        with self.assertRaises(ValueError):
            contracts.Field("x", "number")

    def test_an_enum_on_a_non_string_field_refuses_construction(self):
        with self.assertRaises(ValueError):
            contracts.Field("x", contracts.INTEGER, values=("a", "b"))

    def test_numeric_bounds_on_a_non_integer_field_refuse_construction(self):
        with self.assertRaises(ValueError):
            contracts.Field("x", contracts.STRING, minimum=1)

    def test_size_bounds_on_a_non_list_field_refuse_construction(self):
        with self.assertRaises(ValueError):
            contracts.Field("x", contracts.INTEGER, max_items=3)

    def test_inverted_bounds_refuse_construction(self):
        with self.assertRaises(ValueError):
            contracts.Field("x", contracts.INTEGER, minimum=10, maximum=1)
        with self.assertRaises(ValueError):
            contracts.Field("x", contracts.STRING_LIST, min_items=5, max_items=2)

    def test_a_contract_with_duplicate_field_names_refuses_construction(self):
        with self.assertRaises(ValueError):
            contracts.ToolContract("dup", (contracts.Field("a"), contracts.Field("a")))

    def test_a_path_enum_is_legal_because_path_strings_can_be_choices(self):
        field = contracts.Field("root", contracts.PATH, values=("src", "tests"))
        self.assertEqual(field.check("src"), "")
        self.assertIn("must be one of", field.check("docs"))


class SandboxGuardTests(unittest.TestCase):
    """Whether a well-formed call may run at all, judged by ceiling and confinement."""

    def setUp(self):
        self.read_tool = contracts.ToolContract(
            "read_file", (contracts.Field("path", contracts.PATH),),
            side_effect=contracts.LEVEL_READ)
        self.write_tool = contracts.ToolContract(
            "write_file", (contracts.Field("path", contracts.PATH),),
            side_effect=contracts.LEVEL_WRITE)

    def call_for(self, contract, path="src/app.py"):
        return contracts.Call(contract.name, {"path": path})

    def test_a_read_at_or_under_the_ceiling_runs(self):
        guard = contracts.SandboxGuard()
        guard.check(self.read_tool, self.call_for(self.read_tool))

    def test_a_write_above_a_read_ceiling_is_denied_before_any_handler(self):
        guard = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_READ)
        with self.assertRaises(contracts.SandboxDenied) as caught:
            guard.check(self.write_tool, self.call_for(self.write_tool))
        message = str(caught.exception)
        self.assertIn("write", message)
        self.assertIn("read", message)

    def test_the_ceiling_is_inclusive_at_its_own_rung(self):
        guard = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_WRITE)
        guard.check(self.write_tool, self.call_for(self.write_tool))

    def test_a_none_ceiling_denies_even_reads(self):
        guard = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_NONE)
        with self.assertRaises(contracts.SandboxDenied):
            guard.check(self.read_tool, self.call_for(self.read_tool))

    def test_a_denied_level_message_never_quotes_the_path(self):
        guard = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_READ)
        with self.assertRaises(contracts.SandboxDenied) as caught:
            guard.check(self.write_tool, self.call_for(self.write_tool, "src/topsecret.py"))
        self.assertNotIn("topsecret", str(caught.exception))

    def test_reaches_compares_by_ladder_order_not_membership(self):
        self.assertTrue(self.write_tool.reaches(contracts.LEVEL_READ))
        self.assertTrue(self.write_tool.reaches(contracts.LEVEL_WRITE))
        self.assertFalse(self.read_tool.reaches(contracts.LEVEL_WRITE))
        with self.assertRaises(ValueError):
            self.read_tool.reaches("sideways")

    def test_an_unknown_level_refuses_construction_of_either_half(self):
        with self.assertRaises(ValueError):
            contracts.SandboxGuard(max_side_effect="mostly-safe")
        with self.assertRaises(ValueError):
            contracts.ToolContract("wild", (), side_effect="mostly-safe")

    def test_allows_answers_instead_of_raising(self):
        guard = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_NONE)
        self.assertFalse(guard.allows(self.read_tool, self.call_for(self.read_tool)))
        open_guard = contracts.SandboxGuard()
        self.assertTrue(open_guard.allows(self.read_tool, self.call_for(self.read_tool)))

    def test_an_absolute_path_is_denied_by_the_guard_even_when_validation_missed_it(self):
        guard = contracts.SandboxGuard()
        with self.assertRaises(contracts.SandboxDenied):
            guard.check(self.read_tool, self.call_for(self.read_tool, "/etc/passwd"))

    def test_a_parent_traversal_is_denied_by_the_guard(self):
        guard = contracts.SandboxGuard()
        for escape in ("../secret", "src/../../etc"):
            with self.subTest(path=escape):
                with self.assertRaises(contracts.SandboxDenied):
                    guard.check(self.read_tool, self.call_for(self.read_tool, escape))

    def test_a_drive_letter_path_is_denied_by_the_guard(self):
        guard = contracts.SandboxGuard()
        with self.assertRaises(contracts.SandboxDenied) as caught:
            guard.check(self.read_tool, self.call_for(self.read_tool, "C:/Windows/x"))
        self.assertIn("escapes the workspace", str(caught.exception))

    def test_a_denied_escape_names_the_field_without_naming_the_value(self):
        guard = contracts.SandboxGuard()
        with self.assertRaises(contracts.SandboxDenied) as caught:
            guard.check(self.read_tool, self.call_for(self.read_tool, "../topsecret/file"))
        message = str(caught.exception)
        self.assertIn("path", message)
        self.assertNotIn("topsecret", message)

    def test_allowed_prefixes_confine_every_path_argument(self):
        guard = contracts.SandboxGuard(allowed_prefixes=("src", "docs"))
        guard.check(self.read_tool, self.call_for(self.read_tool, "src/app.py"))
        guard.check(self.read_tool, self.call_for(self.read_tool, "docs"))
        with self.assertRaises(contracts.SandboxDenied):
            guard.check(self.read_tool, self.call_for(self.read_tool, "tests/x.py"))

    def test_a_prefix_only_counts_at_a_directory_boundary(self):
        guard = contracts.SandboxGuard(allowed_prefixes=("src",))
        with self.assertRaises(contracts.SandboxDenied):
            guard.check(self.read_tool, self.call_for(self.read_tool, "srcfiles/x"))

    def test_a_trailing_slash_on_a_prefix_changes_nothing(self):
        guard = contracts.SandboxGuard(allowed_prefixes=("src/",))
        guard.check(self.read_tool, self.call_for(self.read_tool, "src/app.py"))

    def test_an_empty_prefix_tuple_means_the_whole_workspace_is_in_bounds(self):
        guard = contracts.SandboxGuard(allowed_prefixes=())
        guard.check(self.read_tool, self.call_for(self.read_tool, "anything/at/all"))

    def test_a_list_of_paths_is_denied_for_its_one_bad_entry(self):
        tool = contracts.ToolContract(
            "read_many", (contracts.Field("path", contracts.PATH, required=contracts.OPTIONAL),),
            side_effect=contracts.LEVEL_READ)
        guard = contracts.SandboxGuard()
        with self.assertRaises(contracts.SandboxDenied):
            guard.check(tool, contracts.Call("read_many", {"path": ["src/a.py", "../b"]}))

    def test_a_path_field_that_was_never_supplied_is_no_business_of_the_guard(self):
        tool = contracts.ToolContract(
            "maybe", (contracts.Field("path", contracts.PATH, required=contracts.OPTIONAL),),
            side_effect=contracts.LEVEL_READ)
        contracts.SandboxGuard().check(tool, contracts.Call("maybe", {}))

    def test_a_sandbox_denial_is_a_policy_denial_and_is_never_retryable(self):
        guard = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_NONE)
        with self.assertRaises(contracts.PolicyDenied):
            guard.check(self.read_tool, self.call_for(self.read_tool))
        error = contracts.SandboxDenied("posture")
        self.assertEqual(error.code, contracts.SANDBOX_DENIED)
        self.assertFalse(error.retryable)
        self.assertFalse(contracts.is_retryable(error))

    def test_a_validated_call_still_faces_the_guard_after_validation_passes(self):
        tool = contracts.ToolContract(
            "write_note", (contracts.Field("path", contracts.PATH),),
            side_effect=contracts.LEVEL_WRITE)
        made = tool.validate({"action": "write_note", "path": "notes/todo.md"})
        guard = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_WRITE,
                                       allowed_prefixes=("src",))
        self.assertFalse(guard.allows(tool, made))


class ErrorTaxonomyTests(unittest.TestCase):
    """The one question the taxonomy answers: may this be tried again?"""

    TAXONOMY = (
        (contracts.MalformedCall, contracts.MALFORMED_CALL, False),
        (contracts.UnknownTool, contracts.UNKNOWN_TOOL, False),
        (contracts.PolicyDenied, contracts.POLICY_DENIED, False),
        (contracts.SandboxDenied, contracts.SANDBOX_DENIED, False),
        (contracts.ExecutionFailed, contracts.EXECUTION_FAILED, True),
        (contracts.Timeout, contracts.TIMEOUT, True),
    )

    def test_every_class_carries_its_own_code_and_its_own_retry_answer(self):
        for cls, code, retryable in self.TAXONOMY:
            with self.subTest(cls=cls.__name__):
                error = cls("a sentence for the reader")
                self.assertEqual(error.code, code)
                self.assertEqual(error.retryable, retryable)
                self.assertEqual(contracts.is_retryable(error), retryable)
                self.assertEqual(str(error), "a sentence for the reader")

    def test_retryable_codes_holds_exactly_the_two_classes_a_retry_could_help(self):
        self.assertEqual(contracts.RETRYABLE_CODES,
                         frozenset({contracts.EXECUTION_FAILED, contracts.TIMEOUT}))
        for cls, code, _ in self.TAXONOMY:
            self.assertEqual(code in contracts.RETRYABLE_CODES, cls().retryable)

    def test_a_refused_shape_a_wrong_name_and_a_denial_are_all_never_retryable(self):
        for error in (contracts.MalformedCall("x"), contracts.UnknownTool("x"),
                      contracts.PolicyDenied("x"), contracts.SandboxDenied("x")):
            with self.subTest(error=type(error).__name__):
                self.assertFalse(contracts.is_retryable(error))

    def test_the_subclassing_follows_the_action_the_caller_must_take(self):
        self.assertTrue(issubclass(contracts.SandboxDenied, contracts.PolicyDenied))
        self.assertTrue(issubclass(contracts.Timeout, contracts.ExecutionFailed))
        self.assertTrue(issubclass(contracts.PolicyDenied, contracts.ContractError))
        self.assertTrue(issubclass(contracts.ExecutionFailed, contracts.ContractError))

    def test_every_taxonomy_error_ships_advice_safe_to_show_the_caller(self):
        for cls, _, _ in self.TAXONOMY:
            with self.subTest(cls=cls.__name__):
                self.assertTrue(cls().advice)

    def test_a_raiser_may_replace_the_default_advice_without_touching_the_class(self):
        error = contracts.MalformedCall("nope", advice="Send exactly: read_file(path)")
        self.assertEqual(error.advice, "Send exactly: read_file(path)")
        self.assertEqual(contracts.MalformedCall().advice,
                         contracts.MalformedCall("x").advice)

    def test_an_unexpected_exception_is_not_retryable_by_default(self):
        """A bug does not fix itself between one attempt and the next."""
        self.assertFalse(contracts.is_retryable(RuntimeError("crash")))
        self.assertFalse(contracts.is_retryable(ValueError("shape")))

    def test_a_bare_contract_error_with_no_code_is_not_retryable(self):
        self.assertEqual(contracts.ContractError().code, "")
        self.assertFalse(contracts.is_retryable(contracts.ContractError("nameless")))

    def test_a_custom_error_inheriting_a_retryable_class_inherits_its_answer(self):
        class BuildFailed(contracts.ExecutionFailed):
            code = contracts.EXECUTION_FAILED

        self.assertTrue(contracts.is_retryable(BuildFailed("the compile stopped")))

    def test_the_codes_are_distinct_spellings_with_no_two_classes_sharing_one(self):
        codes = [cls().code for cls, _, _ in self.TAXONOMY]
        self.assertEqual(len(codes), len(set(codes)))


class ResultInvariantsTests(unittest.TestCase):
    """The closed three-way status and the promises each corner of it carries."""

    def test_an_unknown_status_refuses_construction(self):
        for status in ("success", "", "FAILED", None):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    contracts.Result(status)

    def test_a_failed_result_must_carry_a_code_and_no_other_status_may(self):
        with self.assertRaises(ValueError):
            contracts.Result(contracts.FAILED)
        for status in (contracts.OK, contracts.EMPTY):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    contracts.Result(status, {}, "", contracts.EXECUTION_FAILED)

    def test_each_corner_of_the_three_way_status_is_constructible_and_named(self):
        self.assertEqual(contracts.STATUSES, (contracts.OK, contracts.EMPTY, contracts.FAILED))
        self.assertEqual(contracts.Result.ok({"n": 1}).status, contracts.OK)
        self.assertEqual(contracts.Result.empty("nothing matched").status, contracts.EMPTY)
        failed = contracts.Result.fail(contracts.MalformedCall("bad shape"))
        self.assertEqual(failed.status, contracts.FAILED)

    def test_succeeded_reads_the_status_alone_and_absence_is_a_kind_of_success(self):
        self.assertTrue(contracts.Result.ok().succeeded)
        self.assertTrue(contracts.Result.empty().succeeded)
        self.assertFalse(contracts.Result.fail(contracts.ExecutionFailed("broke")).succeeded)

    def test_a_results_retry_answer_matches_the_errors_that_made_it(self):
        for error, retryable in ((contracts.ExecutionFailed("x"), True),
                                 (contracts.Timeout("x"), True),
                                 (contracts.MalformedCall("x"), False),
                                 (contracts.UnknownTool("x"), False),
                                 (contracts.PolicyDenied("x"), False),
                                 (contracts.SandboxDenied("x"), False)):
            with self.subTest(code=error.code):
                made = contracts.Result.fail(error)
                self.assertEqual(made.retryable, retryable)
                self.assertEqual(made.retryable, contracts.is_retryable(error))

    def test_a_failure_answer_carries_the_error_s_own_words_and_never_invented_data(self):
        error = contracts.ExecutionFailed("the suite died on import")
        made = contracts.Result.fail(error)
        self.assertEqual(made.message, "the suite died on import")
        self.assertEqual(made.error_code, contracts.EXECUTION_FAILED)
        self.assertEqual(made.advice, error.advice)
        self.assertEqual(made.data, {})
        self.assertEqual(made.state, {})

    def test_a_failure_answer_without_a_call_carries_empty_ids(self):
        made = contracts.Result.fail(contracts.Timeout("too slow"))
        self.assertEqual((made.trace_id, made.call_id), ("", ""))

    def test_an_empty_result_can_still_hold_data_a_caller_may_want(self):
        made = contracts.Result.empty("no matches", {"files": []})
        self.assertEqual(made.status, contracts.EMPTY)
        self.assertEqual(made.data, {"files": []})
        self.assertEqual(made.error_code, "")

    def test_default_data_and_state_are_not_shared_between_results(self):
        first = contracts.Result.ok()
        second = contracts.Result.ok()
        first.data["seen"] = True
        first.state["evidence"] = ["ran"]
        self.assertEqual(second.data, {})
        self.assertEqual(second.state, {})

    def test_a_result_is_frozen_so_a_downstream_cannot_reroll_the_status(self):
        import dataclasses
        made = contracts.Result.ok({"content": "x"})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            made.status = contracts.FAILED

    def test_traced_keeps_every_judgement_the_result_already_held(self):
        original = contracts.Result(contracts.FAILED, {}, "boom", contracts.EXECUTION_FAILED,
                                    "retry later", state={"verdict": "failed"})
        call = contracts.Call("run_tests", {}, trace_id="t-1", call_id="c-2")
        stamped = call.traced(original)
        self.assertEqual(stamped.status, original.status)
        self.assertEqual(stamped.data, original.data)
        self.assertEqual(stamped.message, original.message)
        self.assertEqual(stamped.error_code, original.error_code)
        self.assertEqual(stamped.advice, original.advice)
        self.assertEqual(stamped.state, {"verdict": "failed"})
        self.assertEqual((stamped.trace_id, stamped.call_id), ("t-1", "c-2"))

    def test_a_stamped_failure_still_satisfies_the_code_invariant(self):
        call = contracts.Call("run_tests", {}, trace_id="t-1")
        failed = call.traced(contracts.Result.fail(contracts.ExecutionFailed("broke")))
        self.assertEqual(failed.status, contracts.FAILED)
        self.assertTrue(failed.error_code)
        self.assertTrue(failed.retryable)

    def test_get_answers_from_args_with_the_caller_s_own_default(self):
        made = contracts.Call("tool", {"path": "src/app.py"})
        self.assertEqual(made.get("path"), "src/app.py")
        self.assertIsNone(made.get("limit"))
        self.assertEqual(made.get("limit", 10), 10)

    def test_unwrap_and_pop_survive_input_that_is_not_an_envelope(self):
        """The helpers settle dialects before judgement; garbage in is handed back, not crashed."""
        self.assertEqual(contracts.unwrap_args(["not", "a", "dict"]), ["not", "a", "dict"])
        self.assertEqual(contracts.pop_rationale("not a dict"), "")


if __name__ == "__main__":
    unittest.main()
