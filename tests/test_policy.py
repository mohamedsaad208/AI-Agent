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


class AddressTests(unittest.TestCase):
    """Where a request target is, answered from the string alone — the third question `policy` decides.

    The vocabulary matters more than the ranges: a caller that cannot tell a metadata address from a dev
    server is one yes away from sending credentials to it, and a class the module invented silently would
    make the escalation answer questions nobody asked.
    """

    def test_this_machines_own_loopback_is_not_an_escalation(self):
        for host in ("127.0.0.1", "127.5.5.5", "::1"):
            self.assertEqual(policy.address_class(host), policy.LOOPBACK, host)
        self.assertNotIn(policy.LOOPBACK, policy.LIMITED,
                         "testing the app you are building is why the button exists")

    def test_the_metadata_address_is_link_local_and_a_neighbour_is_private(self):
        self.assertEqual(policy.address_class("169.254.169.254"), policy.LINK_LOCAL)
        self.assertEqual(policy.address_class("fe80::1"), policy.LINK_LOCAL)
        for host in ("10.0.0.5", "192.168.1.7", "172.16.5.9", "fd00::1"):
            self.assertEqual(policy.address_class(host), policy.PRIVATE, host)
        for host in ("169.254.169.254", "10.0.0.5", "172.16.5.9", "fd00::1"):
            self.assertIn(policy.address_class(host), policy.LIMITED, host)

    def test_a_public_host_is_answered_as_one(self):
        self.assertEqual(policy.address_class("8.8.8.8"), policy.PUBLIC)
        self.assertEqual(policy.address_class("172.15.0.1"), policy.PUBLIC,
                         "the private range starts at 172.16, and a class that guesses is a class that lies")

    def test_a_name_is_judged_as_a_name_and_never_resolved(self):
        """The limit, stated: finding out whether a name is private is done by sending the request this
        gate exists to think about first, so a name keeps today's verdict instead of being escalated."""
        self.assertEqual(policy.address_class("localhost"), policy.NAMED)
        self.assertEqual(policy.address_class("db.internal"), policy.NAMED)
        self.assertEqual(policy.address_class("deadbeef"), policy.NAMED,
                         "a label that merely looks like hex is a name, not an address form")
        self.assertNotIn(policy.NAMED, policy.LIMITED)
        source = Path(policy.__file__).read_text(encoding="utf-8")
        for shape in ("getaddrinfo", "socket.", "urlopen("):
            self.assertNotIn(shape, source, "the class reads a string: " + shape)

    def test_a_form_that_is_neither_a_name_nor_a_readable_address_escalates(self):
        """`127.1`, `0177.0.0.1` and `2130706433` are loopback to some resolvers and nothing to others.
        An address this tool cannot read is not a public host, so it is `unknown`, and `unknown` is one of
        the limited classes."""
        for host in ("127.1", "0177.0.0.1", "2130706433", "0x7f000001", "", "http://127.0.0.1"):
            self.assertEqual(policy.address_class(host), policy.UNKNOWN, repr(host))
        self.assertIn(policy.UNKNOWN, policy.LIMITED)

    def test_the_host_comes_out_of_the_url_with_the_port_and_the_credentials_left_behind(self):
        for url, host in (("http://127.0.0.1:8080/users", "127.0.0.1"),
                          ("http://user:pass@10.0.0.9:3000/x", "10.0.0.9"),
                          ("http://[::1]:9/admin", "::1"),
                          ("https://api.example.com", "api.example.com")):
            self.assertEqual(policy.address_of(url), (host, policy.address_class(host)), url)

    def test_a_url_this_library_rejects_has_no_readable_host(self):
        for bad in ("", "not a url", "http://[::1"):
            self.assertEqual(policy.address_of(bad)[1], policy.UNKNOWN, bad)

    def test_the_range_answers_are_the_standard_librarys_and_are_recorded_as_such(self):
        """CGNAT (`100.64.0.0/10`) is not `is_private` in Python, so it answers public here. Asserting it
        keeps the agreement honest: if the library ever changes, this test moves with it and says so."""
        self.assertEqual(policy.address_class("100.64.0.1"), policy.PUBLIC)

    def test_a_link_local_that_names_its_interface_is_still_link_local(self):
        """`fe80::1%eth0` and the percent form the URL carries are one address to the library, so writing
        the interface down does not drop a metadata-range target out of the escalated set."""
        for host in ("fe80::1", "fe80::1%eth0", "fe80::1%25eth0"):
            self.assertEqual(policy.address_class(host), policy.LINK_LOCAL, host)
        self.assertEqual(policy.address_of("http://[fe80::1%25eth0]/x")[1], policy.LINK_LOCAL)

    def test_every_class_has_a_word_in_both_languages(self):
        """The refusal is the one place an operator reads a class, and a missing key there would raise in
        the middle of a dialog that already asked them to decide."""
        for arabic in (False, True):
            words = labels.address_words(arabic)
            self.assertEqual(set(words), set(policy.ADDRESS_CLASSES))
            self.assertEqual({code for code, word in words.items() if labels.is_arabic(word) == arabic},
                             set(policy.ADDRESS_CLASSES))
            self.assertEqual(len(set(words.values())), len(words), "two classes sharing a word is one bug")
        for key in ("policy_addr_limited", "policy_addr_unreadable"):
            english, arabic_text = labels.NOTE_TEMPLATES[key]
            self.assertTrue(labels.is_arabic(arabic_text), key)
            self.assertEqual(english.count("{"), arabic_text.count("{"), key)
            self.assertEqual(labels.note(key, host="7f00host", kind="linkkind").count("7f00host"), 1, key)
            self.assertEqual(labels.note(key, host="7f00host", kind="linkkind").count("linkkind"), 1, key)


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
            for banned in ("webapp", "controller", "providers", "gui", "requests", "socket"):
                self.assertNotIn("import " + banned, source, module.__name__ + " imports " + banned)
            self.assertNotIn("ipaddress.", source.split("def address_class")[1].split("\n\n")[0]
                             if "def address_class" in source else "",
                             "the class answers from the parsed address, not by resolving it")


if __name__ == "__main__":
    unittest.main()
