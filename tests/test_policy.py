"""The policy table and the folder rules that override it.

Two questions are answered here and nowhere else in the tool: what *kind* of action a thing is, and who
asked for it. Before this, four modules refused things with four different ideas of why, and no one could
say whether a proposed write to `.ai_project.json` — the file the runtime reads back as the command to
run — was an ordinary edit.

Five rules these tests hold. A class the table does not name is refused, and so is an origin it does not
name, and so is an override whose words it cannot read: a permission store that falls open is a store
that grants what it cannot parse. A write that changes what will run is its own class, matched by the
last path component however the caller spelled it. An override is remembered per folder in a file that
is *outside* every approved folder, because a proposal that can edit the rules it is being checked
against is not checked against anything. An override may loosen an ask and may never open a deny the
table gives to a model. And nothing in either module imports a window.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import labels, permissions, policy
from ai_code_engineer.workspace import Workspace


class TableTests(unittest.TestCase):
    def test_every_class_answers_for_both_requesters(self):
        for action in policy.ACTIONS:
            for origin in policy.ORIGINS:
                self.assertIn(policy.TABLE[action][origin], policy.VERDICTS, action + " " + origin)

    def test_the_table_names_exactly_the_classes_it_documents(self):
        self.assertEqual(sorted(policy.TABLE), sorted(policy.ACTIONS))

    def test_a_class_nobody_defined_is_refused(self):
        self.assertEqual(policy.decide("sudo"), policy.DENY)
        self.assertEqual(policy.decide(""), policy.DENY)
        self.assertEqual(policy.decide(None), policy.DENY)

    def test_a_requester_nobody_defined_is_refused(self):
        self.assertEqual(policy.decide(policy.EXECUTE_CUSTOM, "cron"), policy.DENY)
        self.assertEqual(policy.decide(policy.EXECUTE_CUSTOM, ""), policy.DENY)

    def test_a_model_asking_for_a_shell_command_is_refused_where_a_person_is_asked(self):
        self.assertEqual(policy.decide(policy.EXECUTE_CUSTOM, policy.OPERATOR), policy.ASK)
        self.assertEqual(policy.decide(policy.EXECUTE_CUSTOM, policy.TASK), policy.DENY)

    def test_a_model_asking_to_reach_the_network_is_refused(self):
        self.assertEqual(policy.decide(policy.NETWORK, policy.OPERATOR), policy.ASK)
        self.assertEqual(policy.decide(policy.NETWORK, policy.TASK), policy.DENY)

    def test_the_quiet_classes_are_quiet_for_everyone(self):
        for action in (policy.READ, policy.WRITE, policy.EXECUTE_RECIPE, policy.GIT_LOCAL):
            for origin in policy.ORIGINS:
                self.assertEqual(policy.decide(action, origin), policy.ALLOW, action + " " + origin)

    def test_a_write_that_changes_what_runs_is_asked_for_whichever_hand_proposed_it(self):
        for origin in policy.ORIGINS:
            self.assertEqual(policy.decide(policy.WRITE_THAT_RUNS, origin), policy.ASK)

    def test_an_unreadable_override_is_a_refusal_not_a_permission(self):
        self.assertEqual(policy.decide(policy.READ, override="maybe"), policy.DENY)
        self.assertEqual(policy.decide(policy.READ, override="ALLOW"), policy.ALLOW,
                         "the table's own words are case-insensitive; a stranger's are not")

    def test_an_override_only_replaces_the_answer_for_a_class_that_exists(self):
        self.assertEqual(policy.decide("read_fil", override="allow"), policy.DENY)


class WriteClassTests(unittest.TestCase):
    def test_the_file_the_runtime_reads_back_as_its_own_command_is_its_own_class(self):
        self.assertEqual(policy.write_action(".ai_project.json"), policy.WRITE_THAT_RUNS)

    def test_a_build_file_is_matched_by_its_last_component_however_it_was_spelled(self):
        for path in ("buildSrc/build.gradle", "src\\main\\pom.xml", "/project/Package.JSON",
                     "docker/Dockerfile", "nested/dir/"):
            self.assertIn(policy.write_action(path), policy.ACTIONS)
        self.assertEqual(policy.write_action("src\\main\\pom.xml"), policy.WRITE_THAT_RUNS)
        self.assertEqual(policy.write_action("app/Dockerfile"), policy.WRITE_THAT_RUNS)

    def test_an_ordinary_source_file_is_an_ordinary_write(self):
        self.assertEqual(policy.write_action("src/main/java/a/LoginService.java"), policy.WRITE)

    def test_a_script_is_not_a_write_that_runs_because_nothing_here_runs_it(self):
        # The list is a list of files *this tool* reads back or builds with. A `.sh` is run by a person,
        # and an ask on every script write is how a rule ends up approved without being read.
        self.assertEqual(policy.write_action("scripts/deploy.sh"), policy.WRITE)
        self.assertEqual(policy.write_action("run.ps1"), policy.WRITE)

    def test_an_empty_path_is_a_write_and_not_a_crash(self):
        self.assertEqual(policy.write_action(""), policy.WRITE)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name).resolve()
        self.root = self.app / "repo"
        self.root.mkdir()
        self.other = self.app / "other"
        self.other.mkdir()

    def test_a_folder_nobody_spoke_about_answers_the_table(self):
        self.assertEqual(permissions.overrides(self.app, self.root), {})
        self.assertEqual(permissions.verdict(self.app, self.root, policy.EXECUTE_CUSTOM), policy.ASK)

    def test_a_declared_verdict_survives_the_round_trip(self):
        permissions.declare(self.app, self.root, policy.EXECUTE_CUSTOM, policy.ALLOW, by="web")
        self.assertEqual(permissions.overrides(self.app, self.root),
                         {policy.EXECUTE_CUSTOM: policy.ALLOW})
        self.assertEqual(permissions.verdict(self.app, self.root, policy.EXECUTE_CUSTOM), policy.ALLOW)

    def test_two_surfaces_read_the_same_file(self):
        permissions.declare(self.app, self.root, policy.NETWORK, policy.DENY, by="web")
        # Nothing in this module is per-window state, so the second surface is the same file read by a
        # reader that has parsed nothing yet — which is what another process is.
        self.assertIn(".agent-permissions.json", [p.name for p in self.app.iterdir()])
        permissions._cache.clear()
        self.assertEqual(permissions.overrides(self.app, self.root)[policy.NETWORK], policy.DENY,
                         "a rule one window saved is a rule the other window obeys")

    def test_a_rule_for_one_folder_is_not_a_rule_for_the_next(self):
        permissions.declare(self.app, self.root, policy.EXECUTE_CUSTOM, policy.ALLOW)
        self.assertEqual(permissions.verdict(self.app, self.other, policy.EXECUTE_CUSTOM), policy.ASK)

    def test_a_loosened_ask_does_not_loosen_the_denies_the_table_gives_a_model(self):
        permissions.declare(self.app, self.root, policy.EXECUTE_CUSTOM, policy.ALLOW)
        self.assertEqual(permissions.verdict(self.app, self.root, policy.EXECUTE_CUSTOM,
                                            policy.TASK), policy.DENY,
                         "a lifted ask is about the operator's own button, not the model's")

    def test_a_declared_deny_is_honoured_for_the_operator_too(self):
        permissions.declare(self.app, self.root, policy.WRITE_THAT_RUNS, policy.DENY)
        self.assertEqual(permissions.verdict(self.app, self.root, policy.WRITE_THAT_RUNS), policy.DENY)

    def test_a_hand_written_verdict_the_table_cannot_read_refuses_everything_it_names(self):
        permissions.declare(self.app, self.root, policy.DELETE, policy.ALLOW)
        file = permissions.path(self.app)
        stored = json.loads(file.read_text(encoding="utf-8"))
        key = permissions.folder_key(self.root)
        stored["permissions"][key]["actions"][policy.DELETE] = "perhaps"
        file.write_text(json.dumps(stored), encoding="utf-8")
        self.assertEqual(permissions.overrides(self.app, self.root)[policy.DELETE], policy.DENY)

    def test_a_rule_naming_a_class_that_does_not_exist_is_dropped_not_stored(self):
        self.assertEqual(permissions.declare(self.app, self.root, "sudo", policy.ALLOW), {})
        self.assertEqual(permissions.overrides(self.app, self.root), {})

    def test_a_row_written_by_hand_with_unknown_classes_keeps_only_the_ones_that_exist(self):
        permissions.declare(self.app, self.root, policy.DELETE, policy.ALLOW)
        file = permissions.path(self.app)
        stored = json.loads(file.read_text(encoding="utf-8"))
        stored["permissions"][permissions.folder_key(self.root)]["actions"]["read_fil"] = "allow"
        file.write_text(json.dumps(stored), encoding="utf-8")
        self.assertEqual(sorted(permissions.overrides(self.app, self.root)), [policy.DELETE])

    def test_an_unreadable_file_declares_nothing_rather_than_permitting_everything(self):
        permissions.declare(self.app, self.root, policy.EXECUTE_CUSTOM, policy.ALLOW)
        permissions.path(self.app).write_text("{ not json", encoding="utf-8")
        self.assertEqual(permissions.overrides(self.app, self.root), {})
        self.assertEqual(permissions.verdict(self.app, self.root, policy.EXECUTE_CUSTOM), policy.ASK)

    def test_forgetting_one_class_leaves_the_others_declared(self):
        permissions.declare(self.app, self.root, policy.DELETE, policy.DENY)
        permissions.declare(self.app, self.root, policy.NETWORK, policy.ALLOW)
        permissions.forget(self.app, self.root, policy.DELETE)
        self.assertEqual(permissions.overrides(self.app, self.root), {policy.NETWORK: policy.ALLOW})

    def test_forgetting_the_folder_returns_it_to_the_table(self):
        permissions.declare(self.app, self.root, policy.NETWORK, policy.ALLOW)
        permissions.forget(self.app, self.root)
        self.assertEqual(permissions.overrides(self.app, self.root), {})
        self.assertEqual(permissions.listed(self.app), [])

    def test_an_empty_folder_argument_writes_no_rule_anywhere(self):
        # `project_key("")` resolves to wherever the process was started, and a rule must never land on
        # that folder by accident — the same guard `modes.py` carries for the same reason.
        self.assertEqual(permissions.declare(self.app, "", policy.NETWORK, policy.ALLOW), {})
        self.assertEqual(permissions.overrides(self.app, ""), {})

    def test_the_listing_says_which_folder_which_class_and_who_said_so(self):
        permissions.declare(self.app, self.root, policy.EXECUTE_CUSTOM, policy.ALLOW, by="cli")
        rows = permissions.listed(self.app)
        self.assertEqual(len(rows), 1)
        self.assertEqual([rows[0][k] for k in ("action", "verdict", "by")],
                         [policy.EXECUTE_CUSTOM, policy.ALLOW, "cli"])
        self.assertEqual(Path(rows[0]["path"]).name, "repo")


class VocabularyTests(unittest.TestCase):
    """Every verdict the table can give is spoken in both languages, and named by a class that exists."""

    def test_each_sentence_asked_for_is_a_key_that_exists(self):
        for pair in labels.POLICY_SENTENCES.values():
            for key in pair:
                self.assertIn(key, labels.NOTE_TEMPLATES, key)

    def test_each_of_those_keys_answers_in_arabic_too(self):
        for pair in labels.POLICY_SENTENCES.values():
            for key in pair:
                english, arabic_text = labels.NOTE_TEMPLATES[key]
                self.assertTrue(labels.is_arabic(arabic_text), key)
                self.assertFalse(labels.is_arabic(english), key)
                self.assertEqual(english.count("{}"), arabic_text.count("{}"),
                                 key + " loses a placeholder in one language")

    def test_the_classes_with_a_sentence_are_classes_the_table_answers_for(self):
        self.assertTrue(set(labels.POLICY_SENTENCES) <= set(policy.ACTIONS),
                        "a sentence for a class nobody can name is a refusal nobody can explain")

    def test_an_allowed_action_is_silence_in_either_language(self):
        for arabic in (False, True):
            self.assertEqual(labels.policy_line(arabic, policy.EXECUTE_CUSTOM, policy.ALLOW), "")
            self.assertEqual(labels.policy_line(arabic, "read", policy.DENY), "",
                             "a class with no sentence says nothing rather than guessing")

    def test_the_ask_and_the_deny_of_one_class_are_two_different_sentences(self):
        ask = labels.policy_line(False, policy.NETWORK, policy.ASK)
        deny = labels.policy_line(False, policy.NETWORK, policy.DENY)
        self.assertTrue(ask and deny and ask != deny)
        self.assertTrue(labels.is_arabic(labels.policy_line(True, policy.NETWORK, policy.ASK)))

    def test_the_verdict_words_flip_with_the_language(self):
        english, arabic = labels.policy_verdicts(False), labels.policy_verdicts(True)
        self.assertEqual(sorted(english), sorted(policy.VERDICTS))
        self.assertEqual(sorted(arabic), sorted(policy.VERDICTS))
        self.assertTrue(all(labels.is_arabic(word) for word in arabic.values()))


class PlacementTests(unittest.TestCase):
    """The store is outside the folder a proposal can edit, and the gate inside says so."""

    def test_the_file_is_named_where_the_app_keeps_its_own_records(self):
        self.assertEqual(permissions.path("/srv/app").name, ".agent-permissions.json")

    def test_a_workspace_would_refuse_that_name_inside_a_project(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve() / "repo"
        root.mkdir()
        (root / ".agent-permissions.json").write_text("{}", encoding="utf-8")
        ws = Workspace(root)
        with self.assertRaises(Exception):
            ws.path(".agent-permissions.json", writable=True)

    def test_neither_module_reaches_for_a_window_or_a_provider(self):
        for module in (permissions, policy):
            source = (Path(module.__file__).read_text(encoding="utf-8"))
            for banned in ("webapp", "controller", "providers", "gui", "requests"):
                self.assertNotIn("import " + banned, source, module.__name__ + " imports " + banned)


if __name__ == "__main__":
    unittest.main()
