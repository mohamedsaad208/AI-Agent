"""The one risk rating: `RiskPolicy`, its two axes, and the agreement it now enforces.

Six rules these tests hold. A band is never calmer than the verdict shipped with it, because a card that
says `low` next to a refusal is worse than no card. A band may be more nervous than the verdict, because
the table's job is to refuse and the band's job is to be read. A proposal is rated by its worst act, not
by its average one. An act nobody can name is `critical` and denied, together. And the two vocabularies
that used to rate risk independently — `policy_engine`'s seven operation codes and `review`'s three bands —
now answer to this one, so the same proposal is never two different numbers in two different screens.
"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import policy, risk_policy
from ai_code_engineer.policy import ALLOW, ASK, DENY
from ai_code_engineer.risk_policy import Assessment, Operation, Risk, RiskPolicy
from ai_code_engineer.review import ChangeRiskLevel, ReviewManager


def change(path, deleted=False):
    return {"path": path, "before": "", "after": "x", "delete": deleted}


class TableTests(unittest.TestCase):
    def test_every_class_in_the_table_has_an_operation_and_a_floor_band(self):
        for action in policy.ACTIONS:
            self.assertIn(action, risk_policy.ACTION_OPERATION, f"{action} is unrateable")
            self.assertIn(risk_policy.ACTION_OPERATION[action], risk_policy.OPERATION_RISK)

    def test_a_band_is_never_calmer_than_the_verdict_it_ships_with(self):
        rated = RiskPolicy()
        for action in policy.ACTIONS:
            check = rated.assess(action, "app.py")
            if check.verdict in (ASK, DENY):
                self.assertIn(check.risk, (Risk.HIGH, Risk.CRITICAL),
                              f"{action} asks for approval but shows {check.risk.value}")

    def test_an_operation_named_here_is_a_class_the_table_answers_for(self):
        self.assertIs(risk_policy.ACTION_OPERATION[policy.WRITE_THAT_RUNS], Operation.SECURITY_SENSITIVE)
        self.assertIs(risk_policy.ACTION_OPERATION[policy.DELETE], Operation.DESTRUCTIVE)
        self.assertIs(risk_policy.ACTION_OPERATION[policy.NETWORK], Operation.NETWORK_ACCESS)


class SingleActTests(unittest.TestCase):
    def setUp(self):
        self.policy = RiskPolicy()

    def rate(self, verb, target="", task=""):
        return self.policy.assess(verb, target, task)

    def test_reads_are_low_and_need_nothing_from_anybody(self):
        for verb, target in (("read_file", "app.py"), ("grep", "src/"), ("list_files", "."),
                             ("git_diff", "HEAD")):
            check = self.rate(verb, target)
            self.assertIs(check.risk, Risk.LOW, f"{verb} {target}")
            self.assertIs(check.operation, Operation.SAFE_READ)
            self.assertEqual(check.verdict, ALLOW)

    def test_an_ordinary_write_is_allowed_and_read_as_an_ordinary_write(self):
        check = self.rate("write_file", "src/main/java/OrderDto.java")
        self.assertEqual(check.action, policy.WRITE)
        self.assertIs(check.operation, Operation.LOCAL_WRITE)
        self.assertIs(check.risk, Risk.LOW)
        self.assertEqual(check.verdict, ALLOW)

    def test_a_file_read_back_as_an_instruction_is_high_and_asks(self):
        check = self.rate("write_file", "pom.xml")
        self.assertEqual(check.action, policy.WRITE_THAT_RUNS)
        self.assertIs(check.operation, Operation.SECURITY_SENSITIVE)
        self.assertIs(check.risk, Risk.HIGH)
        self.assertEqual(check.verdict, ASK)
        self.assertIn("risk_reason_runs_later", check.reasons)

    def test_a_script_is_an_ordinary_write_because_nothing_here_runs_what_it_finds(self):
        # `policy_engine` used to call this security-sensitive; the table answers ALLOW, and a band that
        # cried approval for what the gate does not gate is the drift this module was written to end.
        check = self.rate("write_file", "scripts/deploy_helper.sh")
        self.assertEqual(check.action, policy.WRITE)
        self.assertIs(check.operation, Operation.LOCAL_WRITE)

    def test_a_run_time_switch_is_worth_a_look_without_being_worth_a_stop(self):
        check = self.rate("write_file", "src/main/resources/application.properties")
        self.assertIs(check.risk, Risk.MEDIUM)
        self.assertEqual(check.verdict, ALLOW)
        self.assertIn("risk_reason_configuration", check.reasons)

    def test_a_domain_raises_the_band_and_not_the_verdict(self):
        check = self.rate("write_file", "src/main/java/auth/TokenService.java")
        self.assertIs(check.risk, Risk.HIGH)
        self.assertEqual(check.verdict, ALLOW)
        self.assertIn("risk_reason_sensitive_domain", check.reasons)

    def test_reading_inside_a_sensitive_domain_stays_quiet(self):
        check = self.rate("read_file", "src/main/java/auth/TokenService.java")
        self.assertIs(check.risk, Risk.LOW)
        self.assertEqual(check.reasons, ())

    def test_a_domain_is_a_word_and_not_a_substring(self):
        check = self.rate("write_file", "src/authors/AuthorPost.java")
        self.assertIs(check.risk, Risk.LOW)
        self.assertTrue(risk_policy.is_sensitive("db/migration/V2__orders.sql"))

    def test_an_irreversible_command_outranks_its_own_class(self):
        check = self.rate("run_command", "rm -rf build/")
        self.assertIs(check.operation, Operation.DESTRUCTIVE)
        self.assertIs(check.risk, Risk.HIGH)
        self.assertEqual(check.verdict, ASK)
        self.assertIn("risk_reason_irreversible", check.reasons)

    def test_a_target_that_leaves_the_machine_is_critical(self):
        check = self.rate("run_command", "git push origin main")
        self.assertIs(check.operation, Operation.EXTERNAL_SIDE_EFFECT)
        self.assertIs(check.risk, Risk.CRITICAL)
        self.assertEqual(check.verdict, ASK)

    def test_writing_a_file_named_for_a_deployment_is_still_writing_a_file(self):
        check = self.rate("write_file", "deploy.yaml")
        self.assertIs(check.operation, Operation.LOCAL_WRITE)

    def test_a_local_test_is_not_the_same_act_as_a_neighbour(self):
        loopback = self.rate("fetch_url", "http://localhost:8080/api/orders")
        self.assertIs(loopback.operation, Operation.NETWORK_ACCESS)
        self.assertIs(loopback.risk, Risk.HIGH)
        self.assertNotIn("risk_reason_address_limited", loopback.reasons)

        metadata = self.rate("fetch_url", "http://169.254.169.254/latest/meta-data")
        self.assertIs(metadata.risk, Risk.CRITICAL)
        self.assertIn("risk_reason_address_limited", metadata.reasons)

        odd = self.rate("curl", "http://2130706433/")
        self.assertIs(odd.risk, Risk.CRITICAL)

    def test_a_url_in_the_target_is_a_network_call_whatever_verb_carried_it(self):
        check = self.rate("run_command", "curl -s https://api.example.com/orders")
        self.assertEqual(check.action, policy.NETWORK)
        self.assertIs(check.operation, Operation.NETWORK_ACCESS)

    def test_an_act_nobody_can_name_is_denied_and_shown_as_critical(self):
        check = self.rate("warp_drive", "core")
        self.assertEqual(check.verdict, DENY)
        self.assertIs(check.risk, Risk.CRITICAL)
        self.assertEqual(check.reasons, ("risk_reason_unclassified",))
        self.assertTrue(check.refused)
        self.assertTrue(check.needs_human)

    def test_a_class_handed_over_directly_is_used_untouched(self):
        check = self.rate(policy.DELETE, "data/orders.db")
        self.assertEqual(check.action, policy.DELETE)
        self.assertIs(check.operation, Operation.DESTRUCTIVE)


class FolderOverrideTests(unittest.TestCase):
    def test_a_folder_can_open_an_ask_without_cooling_the_band(self):
        opened = RiskPolicy({policy.NETWORK: ALLOW}).assess("fetch_url", "https://api.example.com")
        self.assertEqual(opened.verdict, ALLOW)
        # A host this tool does not resolve is a limited address, so the band says critical even though the
        # folder said go ahead: the override bought a permission, not a reason to look away.
        self.assertIs(opened.risk, Risk.CRITICAL)
        self.assertIn("risk_reason_address_limited", opened.reasons)
        self.assertFalse(opened.needs_human)

    def test_an_override_whose_words_cannot_be_read_refuses_everything(self):
        self.assertEqual(RiskPolicy({policy.NETWORK: "probably"}).assess(
            "fetch_url", "https://api.example.com").verdict, DENY)

    def test_the_default_table_is_the_answer_when_the_folder_said_nothing(self):
        self.assertEqual(RiskPolicy().assess(policy.EXECUTE_CUSTOM, "ls").verdict, ASK)
        self.assertEqual(RiskPolicy({}).assess(policy.EXECUTE_RECIPE, "mvn test").verdict, ALLOW)


class ProposalTests(unittest.TestCase):
    def test_an_empty_proposal_is_low_because_there_is_nothing_to_look_at(self):
        check = risk_policy.assess_changes([], "Refactor")
        self.assertIs(check.risk, Risk.LOW)
        self.assertEqual(check.files, 0)
        self.assertEqual(check.paths, ())

    def test_a_proposal_is_rated_by_its_worst_act_not_its_average_one(self):
        check = risk_policy.assess_changes(
            [change("src/a/OrderDto.java"), change("src/b/ItemDto.java"), change("pom.xml")],
            "Add a field to orders")
        self.assertIs(check.risk, Risk.HIGH)
        self.assertIs(check.operation, Operation.SECURITY_SENSITIVE)
        self.assertEqual(check.verdict, ASK)
        self.assertEqual(check.files, 3)
        self.assertIn("risk_reason_runs_later", check.reasons)

    def test_many_files_alone_are_worth_a_slower_look(self):
        check = risk_policy.assess_changes([change(f"src/mod{i}/Service.java") for i in range(6)])
        self.assertIs(check.risk, Risk.MEDIUM)
        self.assertEqual(check.verdict, ALLOW)
        self.assertIn("risk_reason_many_files", check.reasons)

    def test_a_deletion_is_a_delete_whatever_its_path_looks_like(self):
        check = risk_policy.assess_changes([change("src/OrderDto.java", deleted=True)])
        self.assertIs(check.operation, Operation.DESTRUCTIVE)
        self.assertEqual(check.action, policy.DELETE)
        self.assertEqual(check.verdict, ASK)
        self.assertTrue(check.needs_human)

    def test_a_task_that_names_a_migration_outranks_the_files_it_touches(self):
        check = risk_policy.assess_changes([change("src/main/java/OrderDto.java")],
                                           "Write the schema migration for orders")
        self.assertIs(check.risk, Risk.HIGH)
        self.assertIn("risk_reason_sensitive_domain", check.reasons)

    def test_a_proposal_that_touches_nothing_else_is_low_and_allowed(self):
        check = risk_policy.assess_changes([change("src/main/java/OrderDto.java"),
                                           change("src/test/java/OrderDtoTest.java")])
        self.assertIs(check.risk, Risk.LOW)
        self.assertEqual(check.verdict, ALLOW)
        self.assertEqual(check.reasons, ())

    def test_a_windows_spelling_and_a_nested_build_file_are_the_same_act(self):
        check = risk_policy.assess_changes([change("buildSrc\\build.gradle")])
        self.assertIs(check.operation, Operation.SECURITY_SENSITIVE)

    def test_stray_entries_are_ignored_rather_than_rated(self):
        check = risk_policy.assess_changes([None, "pom.xml", change("src/a.java")])
        self.assertEqual(check.files, 1)

    def test_the_dict_a_screen_reads_uses_the_bands_the_events_already_speak(self):
        check = risk_policy.assess_changes([change("Dockerfile")])
        self.assertEqual(check.to_dict()["risk_level"], "high")
        self.assertEqual(check.to_dict()["operation"], "SECURITY_SENSITIVE")
        self.assertEqual(check.to_dict()["verdict"], "ask")
        self.assertIsInstance(check, Assessment)


class OneVocabularyTests(unittest.TestCase):
    """The claim of the whole module: nothing else in the tool rates a thing on its own."""

    def test_the_codes_policy_engine_spoke_are_the_codes_here(self):
        from ai_code_engineer import policy_engine
        from ai_code_engineer.policy_engine import PolicyEngine, RiskLevel

        self.assertIs(RiskLevel, Operation)
        engine = PolicyEngine()
        for verb, target in (("read_file", "app.py"), ("write_file", "src/service.py"),
                             ("write_file", "package.json"), ("run_tests", "unittest"),
                             ("delete_file", "data.db"), ("run_command", "git push origin main"),
                             ("fetch_url", "https://api.github.com")):
            self.assertIs(engine.classify_risk(verb, target),
                          risk_policy.assess(verb, target).operation)

    def test_policy_engine_asks_for_exactly_what_the_table_asks_for(self):
        from ai_code_engineer.policy_engine import PolicyEngine

        engine = PolicyEngine()
        for verb, target in (("read_file", "app.py"), ("write_file", "src/service.py"),
                             ("run_tests", "unittest"), ("write_file", "package.json"),
                             ("delete_file", "data.db"), ("fetch_url", "https://api.github.com")):
            verdict, request = engine.evaluate(verb, target, "change")
            check = risk_policy.assess(verb, target)
            self.assertEqual(verdict, check.verdict)
            self.assertEqual(request is not None, check.needs_human)

    def test_the_review_card_shows_the_band_the_policy_gives(self):
        self.assertIs(ChangeRiskLevel, Risk)
        for files, task in ((["src/auth/token_service.py"], "Update JWT verification"),
                            (["src/payment.py"], "Fix billing"),
                            (["src/math_helper.py"], "Fix addition")):
            self.assertIs(ReviewManager.assess_risk(files, task),
                          risk_policy.assess_changes([{"path": f} for f in files], task).risk)

    def test_a_package_for_a_build_file_is_marked_high_and_readable(self):
        from ai_code_engineer.review import ReviewManager

        package = ReviewManager.create_package(
            task="Pin the plugin version", what_changed="Version bump", why="Reproducible builds",
            files_affected=["pom.xml"], diff_text="+ 1.2.3")
        self.assertIs(package.risk_level, Risk.HIGH)
        self.assertIn("**Risk Rating:** \U0001f534 HIGH", package.to_markdown())


if __name__ == "__main__":
    unittest.main()
