"""The declarative register — schemas, categories, costs and model rows — tested as data.

Nothing here executes a tool, so every assertion is about a *description* agreeing with the
authority it describes. That is the failure mode this layer is built to avoid and the reason the
tests are written as comparisons rather than as fixed prose: a descriptor's `shape()`, `reaches()`
and required list are asserted against the contract it wraps, a field's JSON Schema against the
`Field` it spells, and `offerable()` against `SecurityGate.allows` called by hand on the same
contract. A private copy of a judgement can pass a test that quotes its own expected answer; only
a derived one passes a test that asks the real authority.

The other half is fail-closed defaults, because those are the rows a caller reads when nothing was
declared: an unexercised model is advisory and is offered nothing — and is never even asked the
gate's question — a missing capability flag is never a grant, a tightened guard removes a whole
class from the surface, and a live-table override changes `offerable()` without any argument
changing, since the posture questions read the folder's rule rather than a snapshot of it.

The import-time pairing is asserted in both directions too: a contract with no row and a row with
no contract both refuse to import, and the sentences name what is missing, because a tool that runs
but cannot be described is invisible to every picker and provider in the system.
"""
from pathlib import Path
import dataclasses
import json
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import capabilities, contracts, gate, policy, tools
from ai_code_engineer.capabilities import (
    ADVISORY_CAPABILITIES, BUILTIN_CATEGORIES, BUILTIN_DESCRIPTORS, BUILTIN_META, CATEGORIES,
    CATEGORY_DEPENDENCY, CATEGORY_GIT, CATEGORY_READ, CATEGORY_SHELL, CATEGORY_VERIFICATION,
    CATEGORY_WRITE, CapabilityRegistry, DEFAULT_OUTPUT_CHARS, DEFAULT_TIMEOUT_SECONDS,
    ModelCapabilities, ToolDescriptor)

TOOL_NAMES = tuple(c.name for c in tools.BUILTIN_CONTRACTS)


def descriptor(name="sample", fields=(), category=CATEGORY_READ, action=policy.READ,
               level=contracts.LEVEL_READ, summary="Read one thing, for these tests.",
               **envelope) -> ToolDescriptor:
    """One descriptor over a fresh contract, so a test states only what it is asserting."""
    return ToolDescriptor(
        contract=contracts.ToolContract(name, fields, policy_action=action, side_effect=level),
        category=category, summary=summary, **envelope)


def tool_model(window=8000) -> ModelCapabilities:
    return ModelCapabilities(tool_calling=True, structured_output=True, streaming=True,
                             context_window_tokens=window)


class TheVocabulary(unittest.TestCase):
    """The category names and the floors every descriptor must state."""

    def test_the_six_classes_of_the_plan_tool_table_are_the_whole_vocabulary(self):
        self.assertEqual(set(CATEGORIES),
                         {CATEGORY_READ, CATEGORY_WRITE, CATEGORY_GIT, CATEGORY_VERIFICATION,
                          CATEGORY_DEPENDENCY, CATEGORY_SHELL})
        self.assertEqual(len(CATEGORIES), len(set(CATEGORIES)), "one class, one name")
        for name in ("CATEGORY_READ", "CATEGORY_WRITE", "CATEGORY_GIT", "CATEGORY_VERIFICATION",
                     "CATEGORY_DEPENDENCY", "CATEGORY_SHELL", "CATEGORIES"):
            self.assertIn(name, capabilities.__all__)

    def test_the_stated_floors_are_usable_values(self):
        """A zero timeout and an unbounded ceiling are the two ways a description becomes a hang."""
        self.assertGreater(DEFAULT_TIMEOUT_SECONDS, 0)
        self.assertGreater(DEFAULT_OUTPUT_CHARS, 0)

    def test_categories_describe_and_never_gate(self):
        """A category is neither a policy class nor a rung: only those two answer "may this run"."""
        for unused in (CATEGORY_SHELL, CATEGORY_DEPENDENCY):
            self.assertNotIn(unused, policy.ACTIONS)
        capped = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_READ)
        shelved_read = descriptor(name="shellish", category=CATEGORY_SHELL,
                                  level=contracts.LEVEL_READ)
        self.assertEqual(CapabilityRegistry([shelved_read]).offerable(
            tool_model(), guard=capped, verdict=lambda item: policy.ALLOW), (shelved_read,),
            "calling a read `shell` withdraws nothing")
        shelved_execute = descriptor(name="shellish", category=CATEGORY_READ,
                                     level=contracts.LEVEL_EXECUTE)
        self.assertEqual(CapabilityRegistry([shelved_execute]).offerable(
            tool_model(), guard=capped, verdict=lambda item: policy.ALLOW), (),
            "the rung still decides, whatever the class says")


class ModelRows(unittest.TestCase):
    """`ModelCapabilities` is a declaration about a model, and its defaults are refusals."""

    def test_a_row_that_declared_nothing_is_the_advisory_row(self):
        self.assertEqual(ModelCapabilities(), ADVISORY_CAPABILITIES)
        self.assertFalse(ADVISORY_CAPABILITIES.tool_calling)
        self.assertFalse(ADVISORY_CAPABILITIES.structured_output)
        self.assertFalse(ADVISORY_CAPABILITIES.streaming)
        self.assertEqual(ADVISORY_CAPABILITIES.context_window_tokens, 0)

    def test_an_unexercised_model_may_not_use_tools(self):
        """The one question the picker asks, answered from the flag that means "passed here"."""
        self.assertFalse(ADVISORY_CAPABILITIES.may_use_tools)
        self.assertTrue(ModelCapabilities(tool_calling=True).may_use_tools)

    def test_may_use_tools_is_tool_calling_rather_than_the_pile_of_other_flags(self):
        streaming_only = ModelCapabilities(streaming=True, structured_output=True,
                                           context_window_tokens=32000)
        self.assertFalse(streaming_only.may_use_tools)

    def test_an_unknown_window_is_zero_which_asks_nothing(self):
        self.assertEqual(ModelCapabilities().context_window_tokens, 0)
        self.assertEqual(ModelCapabilities(context_window_tokens=0).context_window_tokens, 0)

    def test_a_negative_window_is_refused_on_the_row_not_in_a_budget_somewhere_else(self):
        with self.assertRaises(ValueError) as caught:
            ModelCapabilities(context_window_tokens=-1)
        self.assertIn("negative", str(caught.exception))

    def test_a_row_is_a_value_not_a_slot(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            tool_model().tool_calling = False


class DescriptorValidation(unittest.TestCase):
    """The envelope a descriptor must state, refused when it states something unusable."""

    def test_an_unknown_category_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            descriptor(category="maybe")
        self.assertIn("Unknown tool category: maybe", str(caught.exception))

    def test_a_timeout_that_is_not_positive_is_refused_and_names_its_tool(self):
        for bad in (0, -5):
            with self.assertRaises(ValueError) as caught:
                descriptor(name="run_tests", timeout_seconds=bad)
            self.assertIn("timeout_seconds must be positive for run_tests", str(caught.exception))

    def test_an_output_ceiling_that_is_not_positive_is_refused_and_names_its_tool(self):
        for bad in (0, -1):
            with self.assertRaises(ValueError) as caught:
                descriptor(name="read_file", max_output_chars=bad)
            self.assertIn("max_output_chars must be positive for read_file", str(caught.exception))

    def test_a_descriptor_that_does_not_say_what_it_does_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            descriptor(name="git_diff", summary="")
        self.assertIn("must say what its tool does: git_diff", str(caught.exception))

    def test_the_costs_default_to_the_stated_floors(self):
        plain = descriptor()
        self.assertEqual(plain.timeout_seconds, DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(plain.max_output_chars, DEFAULT_OUTPUT_CHARS)
        self.assertTrue(plain.idempotent)
        self.assertTrue(plain.concurrent_reads)

    def test_a_descriptor_is_a_value(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            descriptor().category = CATEGORY_WRITE


class DelegationToTheContract(unittest.TestCase):
    """A descriptor wraps its contract; every judgement it echoes is the contract's own answer."""

    def test_name_side_effect_and_policy_class_come_from_the_contract(self):
        row = descriptor(name="apply_patch", action=policy.WRITE, level=contracts.LEVEL_WRITE)
        self.assertEqual(row.name, "apply_patch")
        self.assertEqual(row.side_effect, contracts.LEVEL_WRITE)
        self.assertEqual(row.policy_action, policy.WRITE)
        self.assertEqual(row.contract.name, row.name)

    def test_reaches_is_the_ladders_answer_and_not_a_category_guess(self):
        runner = descriptor(name="run_tests", action=policy.EXECUTE_RECIPE,
                            level=contracts.LEVEL_EXECUTE)
        for level in (contracts.LEVEL_NONE, contracts.LEVEL_READ, contracts.LEVEL_WRITE,
                      contracts.LEVEL_EXECUTE):
            self.assertTrue(runner.reaches(level), level)
        self.assertFalse(runner.reaches(contracts.LEVEL_NETWORK))
        self.assertEqual(runner.reaches(contracts.LEVEL_READ),
                         runner.contract.reaches(contracts.LEVEL_READ))

    def test_a_read_tool_does_not_reclaim_the_execute_rung_by_being_described(self):
        tour = descriptor()
        self.assertFalse(tour.reaches(contracts.LEVEL_WRITE))

    def test_reaching_an_unknown_level_is_still_the_contracts_refusal(self):
        with self.assertRaises(ValueError):
            descriptor().reaches("sideways")

    def test_shape_is_the_contracts_shape_verbatim(self):
        row = descriptor(name="read_file", fields=(contracts.Field("path", contracts.PATH),))
        self.assertEqual(row.shape(), row.contract.shape())
        self.assertEqual(row.shape(), "read_file(path)")


class FieldSchemas(unittest.TestCase):
    """`Field` spelled in the one JSON Schema vocabulary this agent uses."""

    def schemas(self, *fields):
        row = descriptor(fields=fields)
        return row.field_schemas()

    def test_a_string_is_a_string(self):
        self.assertEqual(self.schemas(contracts.Field("query"))["query"], {"type": "string"})

    def test_a_boolean_is_a_boolean_not_a_string(self):
        self.assertEqual(self.schemas(contracts.Field("flag", contracts.BOOLEAN))["flag"],
                         {"type": "boolean"})

    def test_an_integer_carries_only_the_bounds_it_declared(self):
        bounded = self.schemas(contracts.Field("limit", contracts.INTEGER, minimum=1, maximum=50))
        self.assertEqual(bounded["limit"],
                         {"type": "integer", "minimum": 1, "maximum": 50})
        open_ended = self.schemas(contracts.Field("limit", contracts.INTEGER))["limit"]
        self.assertEqual(open_ended, {"type": "integer"})
        self.assertNotIn("minimum", open_ended)
        self.assertNotIn("maximum", open_ended)

    def test_a_string_list_is_an_array_of_strings_with_its_size_bounds(self):
        listed = self.schemas(contracts.Field("paths", contracts.STRING_LIST, min_items=1,
                                              max_items=8))["paths"]
        self.assertEqual(listed["type"], "array")
        self.assertEqual(listed["items"], {"type": "string"})
        self.assertEqual(listed["minItems"], 1)
        self.assertEqual(listed["maxItems"], 8)

    def test_enumerated_values_become_an_enum_as_a_list(self):
        choice = self.schemas(contracts.Field("mode", values=("full", "stat")))["mode"]
        self.assertEqual(choice["enum"], ["full", "stat"])
        self.assertIsInstance(choice["enum"], list)

    def test_a_path_is_a_string_that_says_where_it_may_point(self):
        """The confinement the contract enforces is stated, because a provider cannot read a `Field`."""
        path = self.schemas(contracts.Field("path", contracts.PATH))["path"]
        self.assertEqual(path["type"], "string")
        self.assertIn("Workspace-relative", path["description"])
        self.assertIn("no absolute paths", path["description"])
        self.assertNotIn("kind", path)

    def test_a_fields_own_wording_beats_the_default_one(self):
        path = self.schemas(contracts.Field("path", contracts.PATH,
                                           description="The report to open."))["path"]
        self.assertEqual(path["description"], "The report to open.")

    def test_the_schema_set_is_the_fields_in_order_and_nothing_else(self):
        row = self.schemas(contracts.Field("query"),
                           contracts.Field("limit", contracts.INTEGER, required=False))
        self.assertEqual(list(row), ["query", "limit"])

    def test_the_schema_is_derived_from_the_contract_on_every_call(self):
        """No copy is cached at build time: a descriptor that stored its schema is the drift itself."""
        contract = contracts.ToolContract("grows", (contracts.Field("query"),))
        row = ToolDescriptor(contract=contract, category=CATEGORY_READ, summary="Say it.")
        self.assertIs(row.contract, contract)
        self.assertEqual(list(row.field_schemas()), ["query"])
        widened = dataclasses.replace(contract, fields=(contracts.Field("query"),
                                                       contracts.Field("mode")))
        grown = dataclasses.replace(row, contract=widened)
        self.assertEqual(list(grown.field_schemas()), ["query", "mode"])
        self.assertEqual(list(row.field_schemas()), ["query"], "the first row still says one field")


class ProviderSchema(unittest.TestCase):
    """The dialect a function-calling provider takes verbatim."""

    def test_the_envelope_is_a_function_naming_the_tool_and_its_summary(self):
        row = descriptor(name="search_code", summary="Text search across the workspace.")
        schema = row.json_schema()
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "search_code")
        self.assertEqual(schema["function"]["description"], "Text search across the workspace.")

    def test_the_trace_keys_are_advertised_but_never_required(self):
        row = descriptor(fields=(contracts.Field("query"),))
        parameters = row.json_schema()["function"]["parameters"]
        self.assertEqual(list(parameters["properties"]), ["query", "trace_id", "call_id"])
        self.assertEqual(parameters["properties"]["trace_id"], {"type": "string"})
        self.assertEqual(parameters["required"], ["query"])

    def test_required_names_exactly_the_fields_the_contract_would_refuse_a_call_without(self):
        row = descriptor(fields=(contracts.Field("path", contracts.PATH),
                                 contracts.Field("limit", contracts.INTEGER, required=False)))
        schema = row.json_schema()["function"]["parameters"]
        self.assertEqual(schema["required"], ["path"])
        self.assertEqual(schema["required"],
                         [f.name for f in row.contract.fields if f.required])

    def test_a_tool_that_takes_nothing_still_has_an_object_to_send(self):
        schema = descriptor(name="run_tests").json_schema()["function"]["parameters"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], [])

    def test_the_parameters_object_promises_no_undocumented_key(self):
        parameters = descriptor().json_schema()["function"]["parameters"]
        self.assertFalse(parameters["additionalProperties"])

    def test_every_advertised_shape_is_a_shape_the_validator_accepts(self):
        """A schema that advertises a call `validate` would refuse spends the turn being corrected."""
        for row in BUILTIN_DESCRIPTORS:
            schema = row.json_schema()["function"]["parameters"]
            shape = row.contract.shape()
            self.assertEqual(set(schema["properties"]),
                             {f.name for f in row.contract.fields} | set(contracts.TRACE_KEYS),
                             row.name)
            for name in schema["required"]:
                self.assertIn(name, shape, row.name)


class SummaryRows(unittest.TestCase):
    """The picker's row: the same data a refusal sentence is built from."""

    def test_the_row_carries_every_field_the_plan_shows_beside_a_model(self):
        row = descriptor(name="run_build", category=CATEGORY_VERIFICATION, timeout_seconds=90,
                         max_output_chars=1200, idempotent=False, concurrent_reads=False,
                         action=policy.EXECUTE_RECIPE, level=contracts.LEVEL_EXECUTE,
                         summary="Compile the project.")
        self.assertEqual(row.summary_row(), {
            "name": "run_build",
            "category": CATEGORY_VERIFICATION,
            "summary": "Compile the project.",
            "shape": row.contract.shape(),
            "policy_action": policy.EXECUTE_RECIPE,
            "side_effect": contracts.LEVEL_EXECUTE,
            "timeout_seconds": 90,
            "max_output_chars": 1200,
            "idempotent": False,
            "concurrent_reads": False,
        })

    def test_a_row_is_plain_data(self):
        row = descriptor().summary_row()
        self.assertTrue(all(isinstance(value, (str, int, bool)) for value in row.values()))

    def test_display_and_refusal_quote_one_menu(self):
        for row in BUILTIN_DESCRIPTORS:
            self.assertEqual(row.summary_row()["shape"], row.shape())
            self.assertEqual(row.summary_row()["policy_action"], row.policy_action)


class RegistryLookups(unittest.TestCase):
    """Name, category and order — the enumeration side of the registry."""

    def setUp(self):
        self.read = descriptor(name="read_file",
                               fields=(contracts.Field("path", contracts.PATH),))
        self.diff = descriptor(name="git_diff", category=CATEGORY_GIT,
                               action=policy.GIT_LOCAL)
        self.patch = descriptor(name="apply_patch", category=CATEGORY_WRITE,
                                action=policy.WRITE, level=contracts.LEVEL_WRITE)
        self.registry = CapabilityRegistry([self.read, self.diff, self.patch])

    def test_an_empty_registry_enumerates_nothing(self):
        empty = CapabilityRegistry()
        self.assertEqual(empty.names(), ())
        self.assertEqual(empty.descriptors, ())
        self.assertEqual(empty.json_schemas(), [])
        self.assertEqual(empty.by_category(CATEGORY_READ), ())
        self.assertNotIn("read_file", empty)

    def test_order_is_registration_order_because_somebody_reads_the_list(self):
        self.assertEqual(self.registry.names(), ("read_file", "git_diff", "apply_patch"))
        self.assertEqual([d.name for d in self.registry.descriptors],
                         list(self.registry.names()))

    def test_registration_rejects_a_shadowed_name_the_way_the_dispatcher_does(self):
        with self.assertRaises(ValueError) as caught:
            self.registry.register(descriptor(name="git_diff", category=CATEGORY_GIT))
        self.assertIn("Capability already registered: git_diff", str(caught.exception))
        self.assertEqual(len(self.registry.names()), 3, "the shadowed row did not land")

    def test_a_registry_can_be_seeded_from_an_iterable_that_is_not_a_list(self):
        seeded = CapabilityRegistry(iter([self.read, self.diff]))
        self.assertEqual(seeded.names(), ("read_file", "git_diff"))

    def test_membership_answers_for_names_and_shrugs_for_everything_else(self):
        self.assertIn("read_file", self.registry)
        self.assertNotIn("write_file", self.registry)
        self.assertNotIn(None, self.registry)
        self.assertNotIn(7, self.registry)

    def test_get_hands_back_the_descriptor_it_was_given(self):
        self.assertIs(self.registry.get("read_file"), self.read)

    def test_an_unknown_name_raises_and_lists_what_is_registered(self):
        """A silently absent row would shrink a provider payload without saying so."""
        with self.assertRaises(KeyError) as caught:
            self.registry.get("write_file")
        message = str(caught.exception)
        self.assertIn("No descriptor registered for: write_file", message)
        for name in self.registry.names():
            self.assertIn(name, message)

    def test_the_refusal_for_an_empty_registry_says_none_rather_than_nothing(self):
        with self.assertRaises(KeyError) as caught:
            CapabilityRegistry().get("read_file")
        self.assertIn("Registered: none", str(caught.exception))

    def test_by_category_selects_the_class_and_keeps_the_order(self):
        mixed = CapabilityRegistry([self.read, descriptor(name="search_code"), self.diff])
        self.assertEqual(tuple(d.name for d in mixed.by_category(CATEGORY_READ)),
                         ("read_file", "search_code"))
        self.assertEqual(tuple(d.name for d in mixed.by_category(CATEGORY_GIT)), ("git_diff",))

    def test_a_class_that_is_described_but_not_populated_is_empty_not_missing(self):
        for unused in (CATEGORY_SHELL, CATEGORY_DEPENDENCY):
            self.assertEqual(self.registry.by_category(unused), ())

    def test_an_unknown_class_is_refused_rather_than_answered_empty(self):
        """Returning () for "network" would look like a posture decision rather than a typo."""
        with self.assertRaises(ValueError) as caught:
            self.registry.by_category("network")
        self.assertIn("Unknown tool category: network", str(caught.exception))


class SchemaMinting(unittest.TestCase):
    """Provider payloads are built from the live contracts, every time."""

    def setUp(self):
        self.registry = CapabilityRegistry(BUILTIN_DESCRIPTORS)

    def test_the_whole_surface_in_registration_order(self):
        minted = self.registry.json_schemas()
        self.assertEqual([s["function"]["name"] for s in minted], list(TOOL_NAMES))

    def test_a_subset_is_the_same_schemas_for_that_subset_only(self):
        subset = self.registry.by_category(CATEGORY_VERIFICATION)
        self.assertEqual([s["function"]["name"] for s in self.registry.json_schemas(subset)],
                         ["run_tests", "run_build"])

    def test_descriptors_handed_in_do_not_have_to_come_from_this_registry(self):
        foreign = descriptor(name="external_thing", summary="Say it.")
        self.assertEqual([s["function"]["name"] for s in self.registry.json_schemas([foreign])],
                         ["external_thing"])

    def test_a_minted_schema_is_a_fresh_copy_not_the_stored_one(self):
        first = self.registry.json_schemas()[0]
        first["function"]["description"] = "scribbled over"
        self.assertNotEqual(self.registry.json_schemas()[0]["function"]["description"],
                            "scribbled over")


class OfferingTools(unittest.TestCase):
    """`offerable` is the advisory rule plus the gate's own answer, and nothing else."""

    def setUp(self):
        self.registry = CapabilityRegistry(BUILTIN_DESCRIPTORS)
        self.asked = []

    def verdict(self, answer=policy.ALLOW):
        asked = self.asked

        def decide(action_class):
            asked.append(action_class)
            return answer
        return decide

    def offered_names(self, model, **kwargs):
        return tuple(d.name for d in self.registry.offerable(model, **kwargs))

    def test_a_model_that_has_not_passed_calling_is_offered_nothing(self):
        """The plan's advisory rule, applied where offering a tool is what costs anything."""
        self.assertEqual(self.registry.offerable(ADVISORY_CAPABILITIES), ())

    def test_the_advisory_answer_costs_the_folder_no_questions_at_all(self):
        self.registry.offerable(ADVISORY_CAPABILITIES, verdict=self.verdict())
        self.assertEqual(self.asked, [])

    def test_a_model_without_a_window_still_advises_rather_than_calls(self):
        self.assertEqual(self.offered_names(ModelCapabilities(context_window_tokens=32000)), ())

    def test_the_open_posture_offers_the_builtins_whose_classes_the_table_allows(self):
        self.assertEqual(self.offered_names(tool_model()), TOOL_NAMES)

    def test_a_tighter_posture_withdraws_the_class_it_caps_rather_than_the_whole_surface(self):
        read_only = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_READ)
        self.assertEqual(self.offered_names(tool_model(), guard=read_only),
                         tuple(name for name in TOOL_NAMES
                               if self.registry.get(name).contract.side_effect ==
                               contracts.LEVEL_READ))
        self.assertNotIn("run_tests", self.offered_names(tool_model(), guard=read_only))

    def test_a_denied_class_vanishes_from_the_surface_with_the_guard_still_open(self):
        def decide(action_class):
            return policy.DENY if action_class == policy.READ else policy.ALLOW

        offered = self.offered_names(tool_model(), verdict=decide)
        self.assertNotIn("read_file", offered)
        self.assertIn("run_tests", offered)
        self.assertIn("git_diff", offered)

    def test_an_ask_is_not_offered_because_a_task_run_has_nobody_to_answer_it(self):
        """`ask` is an answer a caller could act on only if somebody were typing: it withdraws."""
        def decide(action_class):
            return policy.ASK if action_class == policy.EXECUTE_RECIPE else policy.ALLOW

        offered = self.offered_names(tool_model(), verdict=decide)
        self.assertEqual(offered, tuple(name for name in TOOL_NAMES
                                        if name not in ("run_tests", "run_build")))
        self.assertIn("read_file", offered, "a class the folder allows stays on the surface")

    def test_an_unreadable_verdict_falls_closed_the_way_the_table_falls_closed(self):
        self.assertEqual(self.offered_names(tool_model(), verdict=self.verdict("possibly")), ())

    def test_the_posture_questions_read_the_live_rule_not_a_snapshot_of_it(self):
        """No argument changes: the folder's word moved, and the surface moves with it."""
        before = self.offered_names(tool_model(), verdict=policy.decide)
        self.assertIn("read_file", before)
        with mock.patch.dict(policy.TABLE, {policy.READ: policy.DENY}):
            self.assertNotIn("read_file", self.offered_names(tool_model(),
                                                            verdict=policy.decide))
        self.assertIn("read_file", self.offered_names(tool_model(), verdict=policy.decide))

    def test_the_default_guard_is_the_planning_posture_and_the_default_verdict_is_the_folders(self):
        self.assertEqual(self.offered_names(tool_model()),
                         self.offered_names(tool_model(), guard=tools.OPEN_GUARD,
                                            verdict=policy.decide))

    def test_a_cap_on_outside_tools_withdraws_the_rung_it_caps(self):
        capped = gate.SecurityGate(provenance=gate.external("mcp:files"),
                                   max_external_side_effect=contracts.LEVEL_READ)
        offered = self.offered_names(tool_model(), security_gate=capped,
                                     verdict=self.verdict(policy.ALLOW))
        self.assertNotIn("run_tests", offered)
        self.assertIn("search_code", offered)

    def test_an_outside_provenance_is_judged_under_the_rung_it_reaches_too(self):
        """The escalation is the gate's, and `offerable` asks the gate instead of restating it."""
        outside = contracts.ToolContract("mcp_fetch", (contracts.Field("url"),),
                                         policy_action=policy.READ,
                                         side_effect=contracts.LEVEL_NETWORK)
        row = ToolDescriptor(contract=outside, category=CATEGORY_SHELL,
                             summary="Fetch a URL somebody else named.")
        registry = CapabilityRegistry([row])
        origin = gate.SecurityGate(provenance=gate.external("mcp:files"))
        asked = []

        def decide(action_class):
            asked.append(action_class)
            return policy.decide(action_class)

        self.assertEqual(registry.offerable(tool_model(), security_gate=origin,
                                            verdict=decide), (),
                         "a default folder asks before `execute_custom`, so nothing is offered")
        self.assertEqual(asked, [policy.READ, policy.EXECUTE_CUSTOM],
                         "the declared class and the rung's floor, in that order")

    def test_the_offer_is_exactly_what_the_gate_allows_when_asked_by_hand(self):
        """The one test that proves no private copy of the rules lives here."""
        postures = (
            (None, self.verdict(policy.ALLOW)),
            (contracts.SandboxGuard(max_side_effect=contracts.LEVEL_READ),
             self.verdict(policy.ALLOW)),
            (None, self.verdict(policy.DENY)),
            (None, policy.decide),
        )
        for guard, decide in postures:
            model = tool_model()
            live_guard = guard or tools.OPEN_GUARD
            expected = tuple(d for d in BUILTIN_DESCRIPTORS if gate.NATIVE_GATE.allows(
                d.contract, verdict=decide, guard=live_guard))
            self.assertEqual(self.registry.offerable(model, guard=guard, verdict=decide), expected)

    def test_the_surface_keeps_registration_order_rather_than_grouping_by_class(self):
        self.assertEqual(self.offered_names(tool_model()), self.registry.names())

    def test_an_offer_is_a_preflight_and_not_a_pardon(self):
        """The dispatcher re-checks the live call, so an offer can still be refused on arrival."""
        offered = self.registry.offerable(tool_model(), verdict=self.verdict(policy.ALLOW))
        self.assertIn("run_tests", tuple(d.name for d in offered))
        contract = self.registry.get("run_tests").contract
        with self.assertRaises(contracts.PolicyDenied):
            gate.NATIVE_GATE.admit(contract, {"action": "run_tests"},
                                   verdict=self.verdict(policy.DENY))


class ThePickerRow(unittest.TestCase):
    """`describe` is one build: the menu it shows is the menu the request will act on."""

    def setUp(self):
        self.registry = CapabilityRegistry(BUILTIN_DESCRIPTORS)

    def test_an_unknown_model_is_described_as_advisory(self):
        row = self.registry.describe()
        self.assertEqual(row["mode"], "advisory")
        self.assertEqual(row["offered"], [])
        self.assertEqual(row["model"], {"tool_calling": False, "structured_output": False,
                                       "streaming": False, "context_window_tokens": 0})

    def test_the_whole_surface_is_listed_even_when_none_of_it_is_offered(self):
        self.assertEqual([t["name"] for t in self.registry.describe()["tools"]], list(TOOL_NAMES))

    def test_a_tool_calling_model_is_described_in_tools_mode_with_the_same_list_offerable_gives(self):
        model = tool_model(window=32000)
        row = self.registry.describe(model)
        self.assertEqual(row["mode"], "tools")
        self.assertEqual(row["offered"], [d.name for d in self.registry.offerable(model)])
        self.assertEqual(row["model"]["context_window_tokens"], 32000)
        self.assertTrue(row["model"]["structured_output"])

    def test_a_posture_that_offers_nothing_is_advisory_even_for_a_calling_model(self):
        row = self.registry.describe(tool_model(), verdict=lambda item: policy.DENY)
        self.assertEqual(row["offered"], [])
        self.assertEqual(row["mode"], "advisory")

    def test_the_per_tool_rows_are_the_descriptors_own_rows(self):
        row = self.registry.describe(tool_model())
        self.assertEqual(row["tools"], [d.summary_row() for d in BUILTIN_DESCRIPTORS])

    def test_the_guard_and_verdict_reach_offerable_rather_than_being_summarised_here(self):
        read_only = contracts.SandboxGuard(max_side_effect=contracts.LEVEL_READ)
        row = self.registry.describe(tool_model(), guard=read_only)
        self.assertNotIn("run_build", row["offered"])
        self.assertEqual(row["offered"], [d.name for d in self.registry.offerable(
            tool_model(), guard=read_only)])
        self.assertEqual(row["mode"], "tools")


class TheBuiltInSurface(unittest.TestCase):
    """The eight tools, described — and the drift between the two tables refused at import."""

    def test_the_two_tables_cover_exactly_the_dispatchers_vocabulary(self):
        self.assertEqual(set(BUILTIN_CATEGORIES), set(TOOL_NAMES))
        self.assertEqual(set(BUILTIN_META), set(TOOL_NAMES))

    def test_every_row_names_a_class_this_module_recognises(self):
        for name, category in BUILTIN_CATEGORIES.items():
            self.assertIn(category, CATEGORIES, name)

    def test_the_recipe_runners_are_verification_rather_than_shell(self):
        """The plan's distinction: a build runs the project's own fixed command, shell runs the model's."""
        self.assertEqual(BUILTIN_CATEGORIES["run_tests"], CATEGORY_VERIFICATION)
        self.assertEqual(BUILTIN_CATEGORIES["run_build"], CATEGORY_VERIFICATION)
        self.assertNotIn("shell", BUILTIN_CATEGORIES.values())

    def test_the_five_gathering_tools_are_reads(self):
        gathering = ("list_files", "read_file", "search_code", "find_symbol", "find_references")
        self.assertTrue(all(BUILTIN_CATEGORIES[name] == CATEGORY_READ for name in gathering))

    def test_repository_history_is_its_own_class(self):
        self.assertEqual(BUILTIN_CATEGORIES["git_diff"], CATEGORY_GIT)

    def test_every_descriptor_is_built_over_the_dispatcher_contract_itself(self):
        by_name = {c.name: c for c in tools.BUILTIN_CONTRACTS}
        self.assertEqual([d.name for d in BUILTIN_DESCRIPTORS], list(TOOL_NAMES))
        for row in BUILTIN_DESCRIPTORS:
            self.assertIs(row.contract, by_name[row.name])

    def test_the_recipe_runners_declare_a_full_run_and_decline_the_scheduling_guarantees(self):
        for name in ("run_tests", "run_build"):
            row = CapabilityRegistry(BUILTIN_DESCRIPTORS).get(name)
            self.assertEqual(row.timeout_seconds, BUILTIN_META[name]["timeout_seconds"])
            self.assertGreater(row.timeout_seconds, DEFAULT_TIMEOUT_SECONDS)
            self.assertFalse(row.idempotent)
            self.assertFalse(row.concurrent_reads)

    def test_every_read_tool_opts_into_concurrency_on_the_record(self):
        for row in CapabilityRegistry(BUILTIN_DESCRIPTORS).by_category(CATEGORY_READ):
            self.assertTrue(row.idempotent, row.name)
            self.assertTrue(row.concurrent_reads, row.name)

    def test_every_row_states_a_cost_and_a_ceiling(self):
        for name, meta in BUILTIN_META.items():
            self.assertGreater(meta["timeout_seconds"], 0, name)
            self.assertGreater(meta["max_output_chars"], 0, name)
            self.assertTrue(meta["summary"], name)

    def test_the_default_registry_is_the_described_builtin_surface(self):
        registry = capabilities.default_registry()
        self.assertEqual(registry.names(), TOOL_NAMES)
        self.assertEqual(registry.descriptors, BUILTIN_DESCRIPTORS)

    def test_the_default_registry_is_a_new_answer_each_time(self):
        first, second = capabilities.default_registry(), capabilities.default_registry()
        self.assertIsNot(first, second)
        self.assertEqual(first.names(), second.names())

    def test_a_contract_without_a_row_refuses_to_import_and_names_itself(self):
        """A tool that runs but cannot be described is invisible to every picker and provider."""
        extra = contracts.ToolContract("new_thing", (), policy_action=policy.READ,
                                       side_effect=contracts.LEVEL_READ)
        with mock.patch.object(tools, "BUILTIN_CONTRACTS", tools.BUILTIN_CONTRACTS + (extra,)):
            with self.assertRaises(ValueError) as caught:
                capabilities._builtin_descriptors()
        self.assertIn("Tool contracts without capability descriptors: new_thing",
                      str(caught.exception))

    def test_a_row_without_a_contract_refuses_to_import_and_shows_the_vocabulary(self):
        phantom = dict(BUILTIN_META)
        phantom["ghost_tool"] = dict(BUILTIN_META["git_diff"])
        with mock.patch.object(capabilities, "BUILTIN_META", phantom):
            with self.assertRaises(ValueError) as caught:
                capabilities._builtin_descriptors()
        message = str(caught.exception)
        self.assertIn("Capability descriptors with no registered contract: ghost_tool", message)
        self.assertIn("Contracts:", message)
        for name in TOOL_NAMES:
            self.assertIn(name, message)

    def test_both_drifts_are_named_in_sorted_order_when_there_are_several(self):
        extra_a = contracts.ToolContract("zeta", (), policy_action=policy.READ)
        extra_b = contracts.ToolContract("alpha", (), policy_action=policy.READ)
        with mock.patch.object(tools, "BUILTIN_CONTRACTS",
                               tools.BUILTIN_CONTRACTS + (extra_a, extra_b)):
            with self.assertRaises(ValueError) as caught:
                capabilities._builtin_descriptors()
        self.assertIn("alpha, zeta", str(caught.exception))

    def test_the_whole_builtin_surface_mints_a_schema_a_provider_could_be_handed_verbatim(self):
        payload = capabilities.default_registry().json_schemas()
        self.assertEqual(len(json.loads(json.dumps(payload))), len(TOOL_NAMES))


if __name__ == "__main__":
    unittest.main()
