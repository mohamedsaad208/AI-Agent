"""The signed override store: what it refuses, what it obeys, and who is allowed to write it.

The request had three parts that pull against each other — change any configuration value from inside
the program, keep the file out of git, and make it so that "nobody can edit it except from inside the
program". The third is why this file exists at all, and these tests are the honest version of it: a
row written by another hand is *seen and named*, not followed. Nothing here claims a person cannot
rewrite the file, because on their own machine they can; what they lose by doing it is every row in it.

Precedence is measured, not assumed, because the interesting cases are the two where the row loses: an
address a profile spells out, and the model a window is showing.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_code_engineer import config, overrides
from ai_code_engineer.errors import AgentError


class Store(unittest.TestCase):
    """One temp app_dir per test: the key file makes every directory its own install."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name)
        self.profiles = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.profiles, ignore_errors=True))

    def profile(self, label, text):
        path = self.profiles / f"{label}.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def stored(self):
        return json.loads((self.app / overrides.FILE).read_text(encoding="utf-8"))


class TheFirstRunFile(Store):
    def test_the_first_run_writes_the_file_and_its_key(self):
        self.assertEqual(overrides.ensure(self.app), [overrides.KEY_FILE, overrides.FILE])
        self.assertTrue((self.app / overrides.FILE).is_file())
        self.assertTrue((overrides.key_path(self.app)).is_file())

    def test_the_file_documents_its_own_keys_and_the_order_they_resolve_in(self):
        """A template that says `{}` teaches nothing; the schema inside the file is the documentation."""
        overrides.ensure(self.app)
        doc = self.stored()["read first"]
        self.assertIn("timeout_seconds", doc["keys"])
        self.assertEqual(len(doc["keys"]), len(overrides.TYPES))
        self.assertEqual(len(doc["order"]), 5, "window, row, profile, environment, table")

    def test_the_documented_range_is_the_range_the_validator_uses(self):
        # The file states ranges it reads out of `config`; a number typed here would drift from there.
        overrides.ensure(self.app)
        for key, (low, high) in config.LIMITS.items():
            stated = overrides.document()["keys"][key]
            self.assertIn(f"{low} to {high}", stated, f"{key} is documented wrongly")

    def test_the_file_names_the_two_writers_and_no_third(self):
        overrides.ensure(self.app)
        text = self.stored()["read first"]["what"]
        self.assertIn("agent overrides", text)
        self.assertIn("never by editing this file", text)

    def test_a_second_run_writes_nothing_at_all(self):
        """The "edit it if it already exists" half of the request: an existing file is left alone."""
        overrides.ensure(self.app)
        overrides.put(self.app, "ollama", "timeout_seconds", 600, by="cli")
        before = (self.app / overrides.FILE).read_bytes()
        self.assertEqual(overrides.ensure(self.app), [])
        self.assertEqual((self.app / overrides.FILE).read_bytes(), before)

    def test_nothing_secret_is_written_into_it(self):
        overrides.put(self.app, "groq", "api_key_env", "GROQ_API_KEY", by="cli")
        self.assertEqual(self.stored()["rows"][0]["value"], "GROQ_API_KEY")
        self.assertNotIn("sk-", json.dumps(self.stored()))

    def test_the_key_is_this_install_s_and_the_file_is_mode_restricted_where_that_exists(self):
        overrides.ensure(self.app)
        secret = (overrides.key_path(self.app)).read_text(encoding="utf-8").strip()
        self.assertEqual(len(secret), 64)
        int(secret, 16)                                  # hex, or nothing signed with it verifies


class TheSignatureRefusesAnEdit(Store):
    """Every one of these is a person opening the JSON in another editor and typing."""

    def row(self, target="ollama", key="timeout_seconds", value=600):
        overrides.put(self.app, target, key, value, by="cli")

    def reload(self, raw):
        (self.app / overrides.FILE).write_text(json.dumps(raw), encoding="utf-8")

    def test_a_value_changed_by_hand_is_refused_and_named(self):
        self.row()
        raw = self.stored()
        raw["rows"][0]["value"] = 5
        self.reload(raw)
        self.assertEqual(overrides.rows(self.app), [])
        self.assertEqual(overrides.refusals(self.app)[0]["why"], "changed-outside")

    def test_a_row_added_by_hand_is_refused_whole(self):
        self.row()
        raw = self.stored()
        raw["rows"].append({"target": "ollama", "key": "max_turns", "value": 30, "at": "", "by": ""})
        self.reload(raw)
        self.assertEqual(overrides.rows(self.app), [])
        self.assertEqual(len(overrides.refusals(self.app)), 1, "one file, one reason")

    def test_a_row_deleted_by_hand_is_caught_too(self):
        """Silently losing an override is still a change of behaviour made outside the program."""
        self.row()
        overrides.put(self.app, "ollama", "max_turns", 30, by="cli")
        raw = self.stored()
        raw["rows"] = raw["rows"][:1]
        self.reload(raw)
        self.assertEqual(overrides.rows(self.app), [])

    def test_reordering_the_rows_is_an_edit(self):
        overrides.put(self.app, "ollama", "timeout_seconds", 600, by="cli")
        overrides.put(self.app, "*", "max_turns", 30, by="cli")
        raw = self.stored()
        raw["rows"] = list(reversed(raw["rows"]))
        self.reload(raw)
        self.assertEqual(overrides.rows(self.app), [])

    def test_a_file_copied_from_another_install_is_not_obeyed(self):
        self.row()
        other = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(other, ignore_errors=True))
        overrides.ensure(other)
        (other / overrides.FILE).write_bytes((self.app / overrides.FILE).read_bytes())
        self.assertEqual(overrides.rows(other), [])
        self.assertEqual(overrides.refusals(other)[0]["why"], "changed-outside")

    def test_a_missing_key_means_no_row_is_trusted(self):
        self.row()
        overrides.key_path(self.app).unlink()
        self.assertEqual(overrides.rows(self.app), [])

    def test_an_unreadable_file_is_named_rather_than_answered_as_empty(self):
        overrides.put(self.app, "ollama", "timeout_seconds", 600, by="cli")
        (self.app / overrides.FILE).write_text("{ not json", encoding="utf-8")
        self.assertEqual(overrides.rows(self.app), [])
        self.assertEqual(overrides.refusals(self.app)[0]["why"], "unreadable")

    def test_the_refusal_says_how_to_put_the_row_back(self):
        self.row()
        raw = self.stored()
        raw["rows"][0]["value"] = 5
        self.reload(raw)
        why = overrides.refusals(self.app)[0]["why"]
        self.assertIn("agent overrides", overrides.why_text(why, arabic=False))

    def test_a_field_the_schema_no_longer_knows_is_dropped_and_named(self):
        """A future version that removes a field must not brick every read with a valid signature."""
        self.row()
        shrunk = {key: value for key, value in overrides.TYPES.items() if key != "timeout_seconds"}
        with patch.object(overrides, "TYPES", shrunk):
            self.assertEqual(overrides.rows(self.app), [])
            refused = overrides.refusals(self.app)[0]
        self.assertEqual((refused["target"], refused["why"]), ("ollama", "unknown"))


class TheWriteRefusals(Store):
    def test_a_key_that_names_nothing_is_refused_by_name(self):
        with self.assertRaises(AgentError) as caught:
            overrides.put(self.app, "ollama", "max_turn", 4)
        self.assertIn("max_turn", str(caught.exception))
        self.assertIn("timeout_seconds", str(caught.exception), "the refusal lists what may be set")

    def test_a_number_out_of_range_is_refused_with_the_range(self):
        for value, field in ((5000, "timeout_seconds"), (10, "context_chars")):
            with self.subTest(value=value):
                with self.assertRaises(AgentError) as caught:
                    overrides.put(self.app, "ollama", field, value)
                self.assertIn(field, str(caught.exception))

    def test_a_credential_shaped_value_is_refused_and_nothing_is_written(self):
        with self.assertRaises(AgentError) as caught:
            overrides.put(self.app, "ollama", "model", "sk-ant-abcdefghijklmnopqrstuvwxyz12")
        self.assertIn("environment", str(caught.exception).lower())
        self.assertEqual(overrides.rows(self.app), [])

    def test_a_key_variable_may_be_named_but_never_held(self):
        overrides.put(self.app, "groq", "api_key_env", "GROQ_API_KEY", by="cli")
        with self.assertRaises(AgentError):
            overrides.put(self.app, "groq", "api_key_env", "sk-or-vl-abcdefghijklmnopqrstuvwxyz12")

    def test_an_address_cannot_be_set_for_every_provider_at_once(self):
        with self.assertRaises(AgentError) as caught:
            overrides.put(self.app, "*", "endpoint", "http://127.0.0.1:11500")
        self.assertIn("one provider", str(caught.exception))
        overrides.put(self.app, "ollama", "endpoint", "http://127.0.0.1:11500")
        self.assertEqual(overrides.values(self.app, "ollama")["endpoint"], "http://127.0.0.1:11500")

    def test_a_provider_nobody_listed_is_named(self):
        with self.assertRaises(AgentError) as caught:
            overrides.put(self.app, "anthropic", "timeout_seconds", 60)
        self.assertIn("anthropic", str(caught.exception))
        self.assertIn("ollama", str(caught.exception), "the refusal lists the targets that exist")

    def test_a_local_provider_cannot_be_moved_off_the_device_by_a_row(self):
        with self.assertRaises(AgentError):
            overrides.put(self.app, "ollama", "endpoint", "https://someone-elses-ollama.example")

    def test_a_blank_value_is_refused_because_removing_is_the_other_operation(self):
        with self.assertRaises(AgentError):
            overrides.put(self.app, "ollama", "model", "   ")

    def test_the_provider_itself_is_not_a_row(self):
        with self.assertRaises(AgentError):
            overrides.put(self.app, "ollama", "provider", "groq")

    def test_a_written_row_replaces_the_row_it_replaces_rather_than_stacking(self):
        overrides.put(self.app, "ollama", "timeout_seconds", 600, by="cli")
        overrides.put(self.app, "ollama", "timeout_seconds", 700, by="web")
        self.assertEqual(len(overrides.rows(self.app)), 1)
        self.assertEqual(overrides.values(self.app, "ollama")["timeout_seconds"], 700)

    def test_removing_a_row_reports_whether_one_was_there(self):
        overrides.put(self.app, "ollama", "timeout_seconds", 600, by="cli")
        self.assertTrue(overrides.delete(self.app, "ollama", "timeout_seconds"))
        self.assertFalse(overrides.delete(self.app, "ollama", "timeout_seconds"))
        self.assertEqual(overrides.rows(self.app), [])

    def test_the_fields_a_form_offers_are_the_fields_the_store_accepts(self):
        keys = [item["key"] for item in overrides.fields()]
        self.assertEqual(keys, sorted(overrides.TYPES))
        numeric = {item["key"]: (item["low"], item["high"]) for item in keys and overrides.fields()
                   if item["number"]}
        self.assertEqual(numeric, {key: value for key, value in config.LIMITS.items()})


class ThePrecedence(Store):
    """Measured against the four sources, in the order the file itself prints."""

    def setUp(self):
        super().setUp()
        self.profile("quiet", '[model]\nprovider = "ollama"\nname = "m"\n')
        self.profile("stated", '[model]\nprovider = "ollama"\nname = "m"\n'
                               'endpoint = "http://127.0.0.1:12000"\n[limits]\nmax_turns = 2\n')

    def read(self, label="quiet", **env):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.update(env)
            return config.load_profile(label, directory=self.profiles, app_dir=self.app)

    def test_without_rows_a_profile_reads_exactly_as_it_always_did(self):
        quiet = self.read("quiet")
        self.assertEqual((quiet.max_turns, quiet.endpoint), (12, "http://127.0.0.1:11434"))
        self.assertEqual(self.read("stated").max_turns, 2)

    def test_a_row_beats_a_line_the_profile_stated(self):
        overrides.put(self.app, "*", "max_turns", 4, by="cli")
        self.assertEqual(self.read("quiet").max_turns, 4)
        self.assertEqual(self.read("stated").max_turns, 4, "the row is the preference being expressed")

    def test_a_row_beats_the_environment_variable(self):
        overrides.put(self.app, "ollama", "endpoint", "http://127.0.0.1:11500", by="cli")
        self.assertEqual(self.read("quiet", OLLAMA_HOST="http://127.0.0.1:11999").endpoint,
                         "http://127.0.0.1:11500")

    def test_a_row_never_redirects_an_address_the_profile_spelled_out(self):
        """The one value a silent change of would move code and a key somewhere nobody aimed them."""
        overrides.put(self.app, "ollama", "endpoint", "http://127.0.0.1:11500", by="cli")
        self.assertEqual(self.read("stated").endpoint, "http://127.0.0.1:12000")

    def test_the_row_lands_under_what_the_window_is_showing(self):
        kind = config.kind_for("ollama")
        overrides.put(self.app, "ollama", "endpoint", "http://127.0.0.1:11500", by="cli")
        overrides.put(self.app, "ollama", "model", "row-model", by="cli")
        overrides.put(self.app, "ollama", "timeout_seconds", 600, by="cli")
        typed = config.settings_for(kind, "http://127.0.0.1:13000", app_dir=self.app,
                                    model="picked", timeout_seconds=120)
        self.assertEqual(typed.endpoint, "http://127.0.0.1:13000", "the field on screen wins")
        self.assertEqual(typed.model, "picked", "the model the list selected wins")
        self.assertEqual(typed.timeout_seconds, 600, "a remembered preference is not a decision")

    def test_a_window_that_typed_nothing_gets_the_row_ahead_of_the_environment(self):
        overrides.put(self.app, "ollama", "endpoint", "http://127.0.0.1:11500", by="cli")
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://127.0.0.1:11999"}, clear=True):
            self.assertEqual(config.settings_for(config.kind_for("ollama"), "",
                                                 app_dir=self.app).endpoint,
                             "http://127.0.0.1:11500")

    def test_the_hint_a_window_shows_is_the_address_the_row_supplies(self):
        """`default_endpoint` is placeholder text; a row that changed the answer must change the hint."""
        overrides.put(self.app, "ollama", "endpoint", "http://127.0.0.1:11500", by="cli")
        self.assertEqual(config.default_endpoint(config.kind_for("ollama"), self.app),
                         "http://127.0.0.1:11500")
        self.assertEqual(config.default_endpoint(config.kind_for("groq"), self.app),
                         "https://api.groq.com/openai/v1", "a row for one provider stays off the others")

    def test_a_row_for_one_provider_does_not_leak_into_another(self):
        overrides.put(self.app, "ollama", "timeout_seconds", 600, by="cli")
        self.profile("groq", '[model]\nprovider = "groq"\nname = "m"\n')
        self.assertEqual(self.read("groq").timeout_seconds, 120)

    def test_the_providers_own_row_beats_the_wildcard(self):
        overrides.put(self.app, "*", "timeout_seconds", 300, by="cli")
        overrides.put(self.app, "ollama", "timeout_seconds", 600, by="cli")
        self.assertEqual(overrides.values(self.app, "ollama")["timeout_seconds"], 600)
        self.assertEqual(overrides.values(self.app, "groq")["timeout_seconds"], 300)

    def test_a_read_with_no_records_directory_consults_nothing(self):
        overrides.put(self.app, "*", "max_turns", 4, by="cli")
        self.assertEqual(config.load_profile("quiet", directory=self.profiles).max_turns, 12)
        self.assertEqual(config.settings_for(config.kind_for("ollama"), "").max_turns, 12)

    def test_a_run_with_no_profile_still_reads_the_rows(self):
        overrides.put(self.app, "*", "max_turns", 4, by="cli")
        overrides.put(self.app, "ollama", "endpoint", "http://127.0.0.1:11500", by="cli")
        settings = config.load_settings(None, self.app)
        self.assertEqual((settings.max_turns, settings.endpoint), (4, "http://127.0.0.1:11500"))

    def test_a_tampered_file_falls_back_to_the_profile_rather_than_failing_the_run(self):
        overrides.put(self.app, "*", "max_turns", 4, by="cli")
        raw = json.loads((self.app / overrides.FILE).read_text(encoding="utf-8"))
        raw["rows"][0]["value"] = 99999
        (self.app / overrides.FILE).write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(self.read("quiet").max_turns, 12)


class TheCommand(Store):
    """`agent overrides` — the terminal's half of the same file the windows write."""

    def setUp(self):
        super().setUp()
        where = patch("ai_code_engineer.cli.app_dir", return_value=self.app)
        where.start()
        self.addCleanup(where.stop)

    def run_command(self, argv):
        from io import StringIO
        from contextlib import redirect_stdout
        from ai_code_engineer.cli import main
        out = StringIO()
        with redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue()

    def test_listing_creates_the_file_and_says_what_it_is(self):
        code, text = self.run_command(["overrides", "--list"])
        self.assertEqual(code, 0, text)
        self.assertIn("Created", text)
        self.assertIn("Nothing is overridden", text)
        self.assertIn(overrides.FILE, text)

    def test_a_set_lands_in_the_file_that_every_surface_reads(self):
        code, text = self.run_command(["overrides", "--set", "ollama", "timeout_seconds", "600"])
        self.assertEqual(code, 0, text)
        self.assertEqual(overrides.values(self.app, "ollama")["timeout_seconds"], 600)
        self.assertIn("Signed", text)

    def test_a_refused_set_prints_the_reason_and_writes_nothing(self):
        self.run_command(["overrides", "--list"])
        code, text = self.run_command(["overrides", "--set", "ollama", "timeout_seconds", "5000"])
        self.assertEqual(code, 1)
        self.assertIn("Error:", text)
        self.assertEqual(overrides.rows(self.app), [])

    def test_the_list_shows_who_wrote_a_row_and_refuses_to_hide_a_bad_one(self):
        self.run_command(["overrides", "--set", "*", "max_turns", "20"])
        code, text = self.run_command(["overrides", "--list"])
        self.assertEqual(code, 0, text)
        self.assertIn("max_turns = 20", text)
        self.assertIn("cli", text, "which surface wrote it is part of what a listing owes")
        raw = self.stored()
        raw["rows"][0]["value"] = 3
        (self.app / overrides.FILE).write_text(json.dumps(raw), encoding="utf-8")
        code, text = self.run_command(["overrides", "--list"])
        self.assertIn("the whole file", text)
        self.assertIn("changed outside the program", text)

    def test_unset_removes_the_row_and_says_so(self):
        self.run_command(["overrides", "--set", "ollama", "timeout_seconds", "600"])
        code, text = self.run_command(["overrides", "--unset", "ollama", "timeout_seconds"])
        self.assertEqual(code, 0, text)
        self.assertIn("Removed", text)
        self.assertEqual(overrides.rows(self.app), [])
        code, text = self.run_command(["overrides", "--unset", "ollama", "timeout_seconds"])
        self.assertIn("Nothing to remove", text)

    def test_an_arabic_run_answers_in_arabic(self):
        self.run_command(["overrides", "--set", "ollama", "timeout_seconds", "600"])
        code, text = self.run_command(["overrides", "--list", "--arabic"])
        self.assertEqual(code, 0, text)
        self.assertTrue(any(0x0600 <= ord(char) <= 0x06FF for char in text),
                        "the listing came back in Latin script")

    def test_the_setup_wizard_leaves_the_file_behind(self):
        from ai_code_engineer.cli import run_setup
        args = type("A", (), {"arabic": False, "repo": None, "provider": "", "endpoint": "",
                              "model": "", "yes": True, "no_demo": True})()
        with patch("ai_code_engineer.cli.safe_print", lambda _text: None):
            run_setup(args)
        self.assertTrue((self.app / overrides.FILE).is_file())


class TheWebWindow(Store):
    def setUp(self):
        super().setUp()
        from ai_code_engineer.webapp.controller import AgentController
        from ai_code_engineer import catalog
        from doubles import OLLAMA_ENTRY

        def models_for(kind, endpoint="", api_key=None):
            return ([dict(OLLAMA_ENTRY)], catalog.LIVE),

        patcher = patch("ai_code_engineer.webapp.controller.models_for",
                        side_effect=lambda kind, endpoint="", api_key=None: ([dict(OLLAMA_ENTRY)],
                                                                             catalog.LIVE))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.controller = AgentController(self.app)
        self.controller.join()
        self.models_for = models_for

    def action(self, type_, payload):
        return self.controller.action(type_, payload, lambda _event: None)

    def rows(self):
        return self.controller.snapshot()["overrides"]["rows"]

    def test_the_window_writes_a_signed_row_that_a_profile_read_then_obeys(self):
        self.action("set_override", {"target": "ollama", "key": "timeout_seconds", "value": "600"})
        self.assertEqual(self.controller.snapshot()["overrides"]["rows"][0]["value"], 600)
        self.assertIn("timeout_seconds", self.controller.status)

    def test_the_refused_value_leaves_the_file_exactly_as_it_was(self):
        self.action("set_override", {"target": "ollama", "key": "timeout_seconds", "value": "600"})
        before = (self.app / overrides.FILE).read_bytes()
        self.action("set_override", {"target": "ollama", "key": "timeout_seconds", "value": "5000"})
        self.assertEqual((self.app / overrides.FILE).read_bytes(), before)
        self.assertIn("between", self.controller.status)

    def test_the_drawer_lists_a_row_it_never_wrote(self):
        """The web window has to show what the terminal signed, or the two look like two tools."""
        overrides.put(self.app, "ollama", "max_turns", 20, by="cli")
        row = next(item for item in self.rows() if item["key"] == "max_turns")
        self.assertEqual((row["value"], row["by"], row["state"]), (20, "cli", "in force"))

    def test_the_drawer_shows_a_refused_row_rather_than_dropping_it(self):
        overrides.put(self.app, "ollama", "timeout_seconds", 600, by="cli")
        raw = json.loads((self.app / overrides.FILE).read_text(encoding="utf-8"))
        raw["rows"][0]["value"] = 7
        (self.app / overrides.FILE).write_text(json.dumps(raw), encoding="utf-8")
        row = self.rows()[0]
        self.assertEqual(row["state"], "refused")
        self.assertEqual(row["key"], "*"), "the file-level refusal names the file, not a guess"

    def test_removing_a_row_is_answered_even_when_there_was_nothing_to_remove(self):
        self.action("set_override", {"target": "ollama", "key": "timeout_seconds", "value": "600"})
        self.action("unset_override", {"target": "ollama", "key": "timeout_seconds"})
        self.assertIn("no longer overridden", self.controller.status)
        self.action("unset_override", {"target": "ollama", "key": "timeout_seconds"})
        self.assertIn("no row", self.controller.status)

    def test_the_form_offers_the_targets_the_store_accepts(self):
        info = self.controller.snapshot()["overrides"]
        self.assertEqual(info["targets"], [overrides.EVERY] + [kind.key for kind in config.KINDS])
        self.assertEqual(info["kind"], "ollama")
        self.assertTrue(info["path"].endswith(overrides.FILE))

    def test_a_whole_file_refusal_names_the_file_rather_than_a_row(self):
        """The signature covers the set, so a hand edit refuses every row — and the drawer has to say
        "the whole file" instead of inventing a row nobody edited."""
        overrides.put(self.app, "ollama", "timeout_seconds", 600, by="cli")
        raw = self.stored()
        raw["rows"][0]["value"] = 7
        (self.app / overrides.FILE).write_text(json.dumps(raw), encoding="utf-8")
        row = self.controller.snapshot()["overrides"]["rows"][0]
        self.assertEqual((row["state"], row["display"]), ("refused", "the whole file"))
        self.assertIn("outside the program", row["why"])

    def test_an_arabic_window_is_given_arabic_refusals(self):
        """The refusal code is internal and the sentence the drawer prints is the server's, in the
        language the window asked in.

        The ranges stay English everywhere in this tool because they come from `validate`; translating
        half the rules and not the other half is how two surfaces start disagreeing.
        """
        overrides.put(self.app, "ollama", "timeout_seconds", 600, by="cli")
        shrunk = {key: value for key, value in overrides.TYPES.items() if key != "timeout_seconds"}
        # "Fix the settings", built from code points so this file stays ASCII and cannot arrive mangled.
        self.controller.messages.append({"role": "user", "text": "".join(
            map(chr, [0x0635, 0x0644, 0x0651, 0x062D])) + " " + "".join(
            map(chr, [0x0627, 0x0644, 0x0625, 0x0631, 0x062F, 0x0627, 0x062F, 0x0644]))})
        self.assertTrue(self.controller.arabic, "the window did not read its own language")
        with patch.object(overrides, "TYPES", shrunk):
            row = self.controller.snapshot()["overrides"]["rows"][0]
        self.assertEqual(row["state"], "refused")
        self.assertTrue(any(0x0600 <= ord(char) <= 0x06FF for char in row["why"]), row["why"])

    def test_opening_the_window_is_what_creates_the_file(self):
        self.assertTrue((self.app / overrides.FILE).is_file())
        self.assertTrue((self.app / overrides.key_path(self.app).name).is_file())

    def test_the_preview_sends_the_same_section_with_scripted_rows(self):
        from ai_code_engineer.webapp.fake import FakeController
        real, fake = self.controller.snapshot()["overrides"], FakeController().snapshot()["overrides"]
        self.assertEqual(set(real), set(fake), "the drawer is reviewed against a different shape")
        self.assertEqual([row["state"] for row in fake["rows"]], ["in force", "in force", "refused"],
                         "a preview that only takes the yes path never shows the refusal")
        self.assertEqual(fake["keys"], real["keys"])


class TheDesktopWindow(Store):
    """The same file, the same refusals, from Tk — checked on a real withdrawn window."""

    def setUp(self):
        import tkinter as tk
        super().setUp()
        from ai_code_engineer.gui import AgentWindow
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        with patch("ai_code_engineer.gui.AgentWindow.check_setup", lambda _self: None), \
                patch("ai_code_engineer.catalog.models_for",
                      return_value=([{"id": "test-local", "name": "Test", "cloud": False}], "live")):
            self.window = AgentWindow(self.root, self.app)
        self.root.update_idletasks()

    def tearDown(self):
        # The window owns the interpreter: closing it first, then destroying, is what `test_gui` does
        # to keep a pending after() callback from firing into a dead Tcl.
        self.window.cancel_timers()
        self.root.destroy()
        super().tearDown()

    def test_the_frame_is_packed_where_the_endpoint_is_set(self):
        self.assertEqual(self.window.override_frame.winfo_manager(), "pack")
        self.assertEqual(self.window.override_rows.get_children(), ())

    def test_saving_from_the_window_signs_a_row_the_other_surfaces_read(self):
        self.window.override_key.set("timeout_seconds")
        self.window.override_target.set("ollama")
        self.window.override_value.set("600")
        self.window.save_override()
        self.assertEqual(overrides.values(self.app, "ollama")["timeout_seconds"], 600)
        self.assertIn("timeout_seconds", self.window.status.get())

    def test_a_refused_value_says_why_and_keeps_the_list_unchanged(self):
        self.window.override_key.set("timeout_seconds")
        self.window.override_value.set("5000")
        self.window.save_override()
        self.assertEqual(overrides.rows(self.app), [])
        self.assertIn("between", self.window.status.get())
        self.assertEqual(self.window.override_rows.get_children(), ())

    def test_the_row_a_terminal_wrote_is_listed_and_can_be_removed(self):
        overrides.put(self.app, "groq", "timeout_seconds", 60, by="cli")
        self.window.refresh_overrides()
        children = self.window.override_rows.get_children()
        self.assertEqual(len(children), 1)
        self.window.override_rows.selection_set(children[0])
        self.window.remove_override()
        self.assertEqual(overrides.rows(self.app), [])
        self.assertIn("no longer overridden", self.window.status.get())

    def test_the_field_box_shows_the_range_it_will_accept(self):
        self.window.override_key.set("max_turns")
        self.window.show_override_range()
        self.assertIn("1 to 30", self.window.override_hint.cget("text"))

    def test_the_opening_line_refuses_the_wildcard_for_an_address(self):
        """Tk names a provider from the row on screen, so this is the one refusal it can walk into."""
        self.window.override_key.set("endpoint")
        self.window.override_target.set(overrides.EVERY)
        self.window.override_value.set("http://127.0.0.1:11500")
        self.window.save_override()
        self.assertEqual(overrides.rows(self.app), [])
        self.assertIn("one provider", self.window.status.get())


class TheSharedRules(unittest.TestCase):
    """The rules that keep one file, one validator and one list of names."""

    def test_the_store_never_writes_anywhere_but_the_directory_it_was_given(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / "app"
            app.mkdir()
            self.assertEqual(overrides.ensure(app), [overrides.KEY_FILE, overrides.FILE])
            self.assertEqual(sorted(path.name for path in app.iterdir()),
                             sorted([overrides.FILE, overrides.KEY_FILE]))

    def test_both_new_files_are_ignored_so_never_one_of_the_project_s(self):
        text = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
        for name in (overrides.FILE, overrides.KEY_FILE):
            self.assertIn("\n" + name + "\n", text, f"{name} could be committed with a project")

    def test_the_read_gate_refuses_both_names(self):
        """The signature is the backstop; the read gate is the front door. A granted folder that *is*
        this tool's own directory must not hand a proposal the file that decides where tasks run."""
        from ai_code_engineer import ignore
        for name in (overrides.FILE, overrides.KEY_FILE):
            self.assertTrue(ignore.protected_dir(name), name)

    def test_the_rows_are_the_only_numbers_the_file_states(self):
        """`overrides.py` must not restate a range: it reads them out of `config`."""
        import inspect
        from ai_code_engineer import overrides as module
        source = inspect.getsource(module)
        for low, high in config.LIMITS.values():
            self.assertNotIn(f"{low}, {high}", source)

    def test_no_model_address_is_written_in_the_store(self):
        from tests.test_connection import http_literals
        self.assertEqual(http_literals(Path(overrides.__file__)), [])
