"""The first-run audit: what this machine can reach, said once, in the language the operator reads.

Three questions stop a new machine — can it run anything, who answers, what may `Send` become — and
each of them used to be answered somewhere else, in its own sentence, in one language only. `setup`
sequences the probes that already existed instead of adding new ones, so these tests guard the two
rules that make that safe: a row is only as confident as the probe behind it (a provider's fallback
name list is never reported as installed), and asking what is installed reaches neither the network
nor a directory walk until the operator asks for that check by name.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from collections import namedtuple
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # so `helpers` is importable either way

from ai_code_engineer import catalog, cli, config, setup
from helpers import sandbox_repo

LOCAL = {"id": "qwen2.5-coder:1.5b", "cloud": False}
SECOND_LOCAL = {"id": "llama3.2:1b", "cloud": False}
CLOUD = {"id": "llama-3.3-70b-versatile", "cloud": True}
OLLAMA_URL = "http://127.0.0.1:11434"


def arabic(text: str) -> bool:
    return any(0x0600 <= ord(char) <= 0x06ff for char in text)


def by_id(rows):
    return {row["id"]: row for row in rows}


class TheSequence(unittest.TestCase):
    """The order is the product: a first run reads top to bottom and stops at the first red line."""

    def audit(self, **kwargs):
        kwargs.setdefault("probe", False)
        return setup.audit(**kwargs)

    def test_the_rows_arrive_in_the_order_a_new_machine_needs_them(self):
        rows = self.audit()
        self.assertEqual([row["id"] for row in rows], list(setup.STEP_IDS))

    def test_every_row_is_a_status_a_sentence_and_something_to_do(self):
        for row in self.audit():
            self.assertEqual(set(row), {"id", "status", "text", "advice"}, row["id"])
            self.assertIn(row["status"], (setup.OK, setup.WARN, setup.BAD, setup.INFO), row["id"])
            self.assertTrue(row["text"].strip(), row["id"])

    def test_a_row_that_is_watchable_or_blocking_always_carries_advice(self):
        """A colour and a sentence are not an answer. The whole point of the audit is that a red line
        names the fix, so a WARN or BAD row that cannot say what to do next is a bug, not a verdict."""
        for row in self.audit():
            if row["status"] in (setup.WARN, setup.BAD):
                self.assertTrue(row["advice"].strip(), f"{row['id']} blocks without advising")

    def test_a_machine_that_has_granted_nothing_blocks_nothing(self):
        """A fresh install has no provider, no folder and no proof yet — none of which is a failure.
        Only `probe` and a real scan may say otherwise, or the card is red before anyone asks it."""
        rows = by_id(self.audit())
        self.assertEqual(setup.counts(self.audit())["bad"], 0)
        for step in ("provider", "model", "project", "demo"):
            self.assertEqual(rows[step]["status"], setup.INFO, step)

    def test_asking_what_is_installed_asks_nothing(self):
        """The card is built for a snapshot. If the audit probed, every streamed log line would mean a
        request to the provider and a walk of the granted folder."""
        with patch("ai_code_engineer.setup.reach", side_effect=AssertionError("probed")) as reach:
            rows = self.audit(repo=str(Path(tempfile.gettempdir())), probe=False)
        reach.assert_not_called()
        self.assertEqual(by_id(rows)["provider"]["status"], setup.INFO)
        self.assertEqual(by_id(rows)["model"]["status"], setup.INFO)


class EveryVerdictBranch(unittest.TestCase):
    """Each branch of each row, driven by a scripted probe rather than by this machine."""

    def rows(self, **kwargs):
        return setup.audit(probe=False, **kwargs)

    def patch_reach(self, entries, source, error=""):
        patcher = patch("ai_code_engineer.setup.reach", return_value=(entries, source, error))
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------ runtime ------------------------------
    def test_a_python_this_tool_cannot_run_on_is_blocked_not_advised(self):
        Version = namedtuple("Version", "major minor micro")
        with patch.object(sys, "version_info", Version(3, 9, 13)):
            row = setup.runtime_row(arabic=False)
        self.assertEqual(row["status"], setup.BAD)
        self.assertIn("3.9.13", row["text"])
        self.assertIn("3.11", row["advice"])

    def test_a_python_without_tk_is_a_warning_that_names_the_other_window(self):
        """Missing Tk does not break this tool — the web window needs nothing more — so the row must
        not read like a blocker, and must say where to work instead."""
        with patch("importlib.util.find_spec", return_value=None):
            row = setup.runtime_row(arabic=False)
        self.assertEqual(row["status"], setup.WARN)
        self.assertIn("web window", row["advice"])

    # ------------------------------ toolchain ------------------------------
    def patch_available(self, names):
        patcher = patch("ai_code_engineer.setup.runner.available", return_value=names)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_machine_that_only_runs_python_is_watchable(self):
        self.patch_available(["python-unittest", "python-pytest"])
        row = setup.toolchain_row(arabic=False)
        self.assertEqual(row["status"], setup.WARN)
        self.assertIn("PATH", row["advice"])

    def test_a_machine_with_a_build_tool_is_fine(self):
        self.patch_available(["python-unittest", "maven-test"])
        row = setup.toolchain_row(arabic=False)
        self.assertEqual(row["status"], setup.OK)
        self.assertEqual(row["advice"], "")

    # ------------------------------ provider and model ------------------------------
    def provider(self, kind=config.OLLAMA, endpoint=OLLAMA_URL, model="", arabic=False):
        return setup.provider_rows(kind, endpoint, None, model, arabic=arabic)

    def test_an_endpoint_this_tool_refuses_never_reaches_a_provider(self):
        rows = setup.provider_rows(config.OLLAMA, "ftp://example.com", None, "", arabic=False)
        self.assertEqual(rows[0]["status"], setup.BAD)
        self.assertIn("endpoint", rows[0]["text"])
        self.assertEqual(rows[1]["status"], setup.INFO)

    def test_an_unreachable_ollama_names_the_command_that_starts_it(self):
        self.patch_reach([], "", "connection refused")
        rows = self.provider()
        self.assertEqual(rows[0]["status"], setup.BAD)
        self.assertIn("ollama serve", rows[0]["advice"])

    def test_an_unreachable_local_server_names_loading_a_model_instead(self):
        """`ollama serve` is the wrong advice for LM Studio, and a person following it would still
        have nothing to select."""
        self.patch_reach([], "", "connection refused")
        rows = self.provider(config.BY_KEY["lmstudio"], "http://127.0.0.1:1234/v1")
        self.assertIn("LM Studio", rows[0]["advice"])
        self.assertNotIn("ollama serve", rows[0]["advice"])

    def test_a_provider_with_no_models_says_pull_and_how(self):
        self.patch_reach([], catalog.LIVE)
        rows = self.provider()
        self.assertEqual(rows[0]["status"], setup.OK)
        self.assertEqual(rows[1]["status"], setup.BAD)
        self.assertIn("ollama pull", rows[1]["advice"])

    def test_a_fallback_name_list_is_never_reported_as_installed(self):
        """The premise this round had wrong: `catalog` answers with built-in names when the live list
        fails. A first run that called those "installed" would promise a model nobody pulled, and the
        operator would find out on the first Send instead of here."""
        self.patch_reach([LOCAL], catalog.BUILT_IN)
        row = by_id(self.provider())["model"]
        self.assertEqual(row["status"], setup.WARN)
        self.assertIn("this tool", row["text"])
        self.assertNotEqual(row["status"], setup.OK)

    def test_a_live_list_of_only_remote_models_is_watchable(self):
        self.patch_reach([CLOUD], catalog.LIVE)
        row = by_id(self.provider())["model"]
        self.assertEqual(row["status"], setup.WARN)
        self.assertIn("policy", row["advice"].lower())

    def test_a_live_list_with_a_local_model_is_the_only_one_that_says_using_it(self):
        self.patch_reach([CLOUD, LOCAL, SECOND_LOCAL], catalog.LIVE)
        row = by_id(self.provider(model=SECOND_LOCAL["id"]))["model"]
        self.assertEqual(row["status"], setup.OK)
        self.assertIn(SECOND_LOCAL["id"], row["text"])

    def test_a_model_that_is_not_on_the_list_falls_back_without_claiming_it(self):
        self.patch_reach([LOCAL], catalog.LIVE)
        row = by_id(self.provider(model="a-name-nobody-pulled"))["model"]
        self.assertEqual(row["status"], setup.OK)
        self.assertIn(LOCAL["id"], row["text"])
        self.assertNotIn("a-name-nobody-pulled", row["text"])

    # ------------------------------ project ------------------------------
    def test_no_granted_folder_is_a_fact_and_not_a_failure(self):
        row = setup.project_row("", arabic=False)
        self.assertEqual(row["status"], setup.INFO)
        self.assertIn("--repo", row["advice"])

    def test_a_folder_that_is_not_there_is_blocked(self):
        row = setup.project_row("D:/no/such/folder", arabic=False)
        self.assertEqual(row["status"], setup.BAD)
        self.assertTrue(row["advice"])

    def test_a_granted_folder_with_no_command_explains_the_hidden_checks_card(self):
        with tempfile.TemporaryDirectory() as temp:
            empty = Path(temp) / "notes"
            empty.mkdir()
            row = setup.project_row(str(empty), arabic=False)
        self.assertEqual(row["status"], setup.WARN)
        self.assertIn("no runnable command", row["text"])

    def test_a_granted_folder_names_the_command_and_whether_git_can_reverse_it(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = sandbox_repo(temp)
            row = setup.project_row(str(repo), arabic=False)
        self.assertEqual(row["status"], setup.OK, row["text"])
        self.assertIn(repo.name, row["text"])
        self.assertIn("git", row["text"])

    # ------------------------------ demo and policy ------------------------------
    def test_the_proof_is_only_green_when_it_rolled_itself_back(self):
        passed = setup.demo_row({"proposal_apply_rollback": "passed"}, arabic=False)
        failed = setup.demo_row({"proposal_apply_rollback": "failed", "note": "rollback did not hold"},
                                arabic=False)
        self.assertEqual(passed["status"], setup.OK)
        self.assertEqual(failed["status"], setup.BAD)
        self.assertIn("rollback did not hold", failed["text"])
        self.assertIn("bug", failed["advice"])

    def test_the_promises_are_numbered_so_a_person_can_be_asked_which_one_they_skipped(self):
        row = setup.policy_row(arabic=False)
        for number in range(1, len(setup.POLICY) + 1):
            self.assertIn(f"({number})", row["text"])

    def test_the_position_row_names_all_three_positions_and_denies_a_fourth(self):
        row = setup.position_row(arabic=False)
        for mode in setup.intent.MODES:
            self.assertIn(setup.intent.label(mode), row["text"])
        self.assertIn("not a fourth choice", row["text"])


class TheAuditEndToEnd(unittest.TestCase):
    """The two calls the windows actually make: the card without a probe, the click with one."""

    def test_a_full_run_on_a_scripted_machine_blocks_nothing_and_invents_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = sandbox_repo(temp)
            with patch("ai_code_engineer.setup.reach", return_value=([LOCAL], catalog.LIVE, "")), \
                    patch("ai_code_engineer.setup.runner.available",
                          return_value=["python-unittest", "maven-test"]):
                rows = setup.audit(repo=str(repo), provider="ollama", endpoint=OLLAMA_URL,
                                   model=LOCAL["id"])
        counts = setup.counts(rows)
        self.assertEqual([row["id"] for row in rows], list(setup.STEP_IDS))
        self.assertEqual(counts["bad"], 0, setup.render(rows))
        self.assertEqual(counts["info"], 3, "policy, position and the proof nobody ran")
        self.assertEqual(counts["ok"], len(rows) - 3)
        self.assertIn(LOCAL["id"], by_id(rows)["model"]["text"])

    def test_the_demo_is_passed_in_rather_than_run_by_asking(self):
        """A report that writes to a temporary folder and rolls it back is a step the operator starts,
        not a side effect of wanting to know what is installed."""
        without = by_id(setup.audit(probe=False))["demo"]
        with_row = by_id(setup.audit(probe=False, demo={"proposal_apply_rollback": "passed"}))["demo"]
        self.assertEqual(without["status"], setup.INFO)
        self.assertEqual(with_row["status"], setup.OK)

    def test_the_two_entry_points_share_one_probe(self):
        """`agent doctor` and the audit used to ask Ollama separately and could disagree about whether
        it answered. One function, counted."""
        calls = []

        def counted(kind, endpoint="", api_key=None):
            calls.append(kind.key)
            return [LOCAL], catalog.LIVE

        with patch("ai_code_engineer.catalog.models_for", side_effect=counted):
            cli.doctor()
            setup.audit(provider="ollama", endpoint=OLLAMA_URL)
        self.assertEqual(calls, ["ollama", "ollama"])
        with patch("ai_code_engineer.setup.run_demo", return_value={"ok": True}) as proof:
            self.assertEqual(cli.demo(), {"ok": True}, "the CLI runs this module's proof, not its own")
        proof.assert_called_once()


class BothLanguages(unittest.TestCase):
    """Every row is a sentence the operator reads, and the module owns both halves of it."""

    def test_every_row_speaks_both(self):
        for name, build in (("audit", lambda a: setup.audit(probe=False, arabic=a)),
                             ("policy", lambda a: [setup.policy_row(arabic=a)]),
                             ("position", lambda a: [setup.position_row(arabic=a)]),
                             ("demo", lambda a: [setup.demo_row(
                                 {"proposal_apply_rollback": "failed", "note": "n"}, arabic=a)])):
            english = build(False)
            arabic_rows = build(True)
            for en, ar in zip(english, arabic_rows):
                self.assertFalse(arabic(en["text"]), f"{name} leaked Arabic into the English half")
                self.assertTrue(arabic(ar["text"]), f"{name} has no Arabic in it")

    def test_the_promises_do_not_print_both_halves_of_a_pair(self):
        """`POLICY` is a tuple of (English, Arabic). A row that interpolated the pair itself printed
        both languages on one line, which is exactly what an RTL reader cannot parse."""
        english = setup.policy_row(arabic=False)["text"]
        row = setup.policy_row(arabic=True)["text"]
        self.assertIn(setup.POLICY[0][0], english)
        self.assertNotIn(setup.POLICY[0][0], row)
        self.assertTrue(arabic(row))

    def test_no_row_carries_a_character_from_a_third_alphabet(self):
        """A stray ideograph reached a translated line once and nobody noticed until it printed."""
        strays = {"\u2014", "\u00b7", "\u2019", "\u2018", "\u201c", "\u201d"}
        for flag in (False, True):
            for row in setup.audit(probe=False, arabic=flag,
                                   demo={"proposal_apply_rollback": "passed"}):
                for char in row["text"] + row["advice"]:
                    if ord(char) < 128 or (0x0600 <= ord(char) <= 0x06ff) or char in strays:
                        continue
                    self.fail(f"U+{ord(char):04X} in {row['id']}: {row['text'][:60]!r}")

    def test_a_status_that_only_exists_as_a_colour_still_reaches_a_terminal(self):
        for flag in (False, True):
            for row in setup.audit(probe=False, arabic=flag):
                self.assertIn(setup.MARK[row["status"]], setup.render([row]))


class Rendering(unittest.TestCase):
    def test_advice_lands_under_the_row_it_belongs_to(self):
        text = setup.render([setup.row("toolchain", setup.WARN, arabic=False, en="Half the tools miss.",
                                       ar="نص الأدوات ناقص.", advice_en="Install Maven.",
                                       advice_ar="نصّب Maven.")])
        self.assertEqual(text.splitlines(), ["[!] Half the tools miss.", "     -> Install Maven."])

    def test_an_unknown_status_is_printed_as_unknown(self):
        """A row built by an older build, or by a card that grew a status this module does not know,
        must not render as a green tick."""
        self.assertTrue(setup.render([{"id": "x", "status": "shiny", "text": "t", "advice": ""}])
                        .startswith("[?]"))

    def test_the_counts_add_up_to_the_rows(self):
        rows = setup.audit(probe=False)
        counts = setup.counts(rows)
        self.assertEqual(sum(counts.values()), len(rows))
        self.assertEqual(set(counts), {"ok", "warn", "bad", "info"})


class TheFirstRunTest(unittest.TestCase):
    """Whether the card appears at launch is a claim about this machine, so it is tested as one."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)

    def registry(self, data):
        (self.app_dir / ".agent-projects.json").write_text(json.dumps(data), encoding="utf-8")

    def test_a_machine_with_no_registry_at_all_is_a_first_run(self):
        self.assertTrue(setup.first_run(self.app_dir))

    def test_a_machine_that_has_never_granted_a_folder_is_a_first_run(self):
        self.registry({"projects": [], "ui": {"mode": "chat"}})
        self.assertTrue(setup.first_run(self.app_dir))

    def test_a_machine_with_one_granted_folder_is_not(self):
        self.registry({"projects": [{"path": str(self.app_dir)}]})
        self.assertFalse(setup.first_run(self.app_dir))

    def test_a_registry_that_does_not_parse_is_treated_as_a_first_run(self):
        """A half-written file must not silence the wizard forever, and must not crash the window that
        opens it. The cost of showing it twice is one click; the cost of never showing it is a person
        who cannot find the checks."""
        (self.app_dir / ".agent-projects.json").write_text("{not json", encoding="utf-8")
        self.assertTrue(setup.first_run(self.app_dir))

    def test_a_registry_that_is_a_list_is_not_a_first_run_claim(self):
        self.registry([str(self.app_dir)])
        self.assertTrue(setup.first_run(self.app_dir))


class NoProjectIsEverTouched(unittest.TestCase):
    """The red line the audit is allowed to demonstrate and never cross."""

    def test_the_offline_proof_leaves_nothing_behind_and_asks_no_model(self):
        import glob
        before = set(glob.glob(str(Path(tempfile.gettempdir()) / "ai-agent-demo-*")))
        result = setup.run_demo()
        self.assertEqual(result["proposal_apply_rollback"], "passed")
        self.assertFalse(result["llm_used"])
        self.assertIn("Synthetic demo only", result["note"])
        self.assertEqual(set(glob.glob(str(Path(tempfile.gettempdir()) / "ai-agent-demo-*"))) - before,
                         set())


class TheSentencesBothWindowsShare(unittest.TestCase):
    """The wizard's own three lines, single-sourced the way `intent` single-sourced the refusals.

    Both windows had written the tally and the proof verdict themselves, which left the Tk one saying
    them in English to a person reading Arabic. Same guard as `test_intent`: a sentence that exists in
    a window is a sentence that will drift.
    """

    SURFACES = {"gui.py": Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer" / "gui.py",
                "cli.py": Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer" / "cli.py",
                "controller.py": (Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"
                                  / "webapp" / "controller.py")}
    COPIED = ("Checks done:", "The proof held", "The proof did not complete", "to watch")

    def test_the_tally_counts_in_the_language_it_is_asked(self):
        counts = {"ok": 3, "warn": 1, "bad": 2, "info": 4}
        english = setup.tally(counts)
        self.assertEqual(english, "3 ok · 1 to watch · 2 blocking")
        self.assertTrue(arabic(setup.tally(counts, arabic=True)))
        self.assertFalse(arabic(english))

    def test_a_finished_check_names_the_tally_it_found(self):
        counts = setup.counts(setup.audit(probe=False))
        english = setup.checks_done(counts)
        self.assertTrue(english.startswith("Checks done: "))
        self.assertIn(setup.tally(counts), english)
        ar = setup.checks_done(counts, arabic=True)
        self.assertTrue(arabic(ar))
        self.assertNotIn("Checks done", ar)

    def test_the_proof_verdict_is_one_sentence_whichever_surface_ran_it(self):
        self.assertTrue(setup.proof_done(True).startswith("The proof held"))
        self.assertIn("rollback did not hold", setup.proof_done(False, "rollback did not hold"))
        self.assertTrue(arabic(setup.proof_done(True, arabic=True)))
        self.assertTrue(arabic(setup.proof_done(False, "note", arabic=True)))

    def test_no_surface_words_the_result_itself(self):
        for name, path in self.SURFACES.items():
            text = path.read_text(encoding="utf-8")
            for sentence in self.COPIED:
                self.assertNotIn(sentence, text, f"{name} writes {sentence!r} itself")
            self.assertIn("setup.tally", text, f"{name} prints the audit without the shared tally")

    def test_both_windows_answer_the_check_with_the_shared_sentence(self):
        """The terminal has no click to answer for, so only the two windows call these — and each of
        them must call both, or one surface is back to wording a verdict on its own."""
        for name in ("gui.py", "controller.py"):
            text = self.SURFACES[name].read_text(encoding="utf-8")
            self.assertIn("setup.checks_done", text, name)
            self.assertIn("setup.proof_done", text, name)


if __name__ == "__main__":
    unittest.main()
