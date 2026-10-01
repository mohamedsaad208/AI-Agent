"""What the window reads about the machine, the provider and the folder it was given."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # so `doubles` is importable either way
from ai_code_engineer import setup
from ai_code_engineer.webapp.controller import AgentController
from helpers import sandbox_repo
from controller_case import Scripted, patched_catalog


class TheFirstRunCard(unittest.TestCase):
    """Phase-3 item 7: the first-run checks as a card in the web window.

    The rows come from `setup`, so these tests hold the surface's own two promises and one bug the
    design invites. Opening the window must not ask anything over the network — the card is built with
    `probe=False`, and only the Check button probes. And "Don't show this again" has to outlive the
    next save of anything else, which it would not have: `_save_state` rebuilds its preference dict
    from named keys, so a flag nobody lists there is erased by the next unrelated write.
    """

    LOCAL = {"id": "qwen2.5-coder:1.5b", "cloud": False}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        patcher = patched_catalog()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.events = []
        self.made = []
        self.addCleanup(self._drain)
        self.reach = patch("ai_code_engineer.setup.reach",
                           side_effect=AssertionError("a snapshot probed the provider"))
        self.reach.start()
        self.addCleanup(self.reach.stop)
        self.controller = self.build()

    def build(self):
        controller = Scripted(self.app_dir)
        controller._emit = self.events.append
        self.made.append(controller)
        return controller

    def _drain(self):
        for controller in self.made:
            controller.cancel_event.set()
            controller.join(timeout=10)

    def card(self, controller=None):
        return (controller or self.controller).snapshot()["setup"]

    def rows(self, controller=None):
        return {row["id"]: row for row in self.card(controller)["rows"]}

    def action(self, type, **payload):
        self.controller.action(type, payload, self.events.append)
        self.controller.join()
        return self.card()

    def said(self, controller=None):
        """The sentence the server wrote last. `say()` lands on the status line, not in the chat, so a
        card sentence is read here rather than from `messages`."""
        return (controller or self.controller).status

    # ------------------------------- whether it shows -------------------------------
    def test_a_machine_that_has_never_granted_a_folder_opens_with_the_card(self):
        self.assertTrue(self.card()["show"])
        self.assertEqual([row["id"] for row in self.card()["rows"]],
                         ["runtime", "toolchain", "provider", "model", "project", "demo", "policy",
                          "position"])

    def test_a_machine_that_has_a_granted_folder_does_not_show_it(self):
        self.controller.set_repo(str(self.repo))
        self.controller.close()
        second = self.build()
        self.assertFalse(second.snapshot()["setup"]["show"],
                         "a person with projects listed does not want a wizard")

    def test_a_machine_that_already_dismissed_it_does_not_show_it_again(self):
        self.action("setup_hide")
        self.controller.close()
        self.assertFalse(self.build().snapshot()["setup"]["show"])

    # ------------------------------- what it costs to open -------------------------------
    def test_opening_the_window_asks_nothing_over_the_network(self):
        """`setUp`'s `reach` raises on purpose: the proof of this test is that the card still builds."""
        for _ in range(3):
            self.card()
        self.assertEqual(self.rows()["provider"]["status"], "info")
        self.assertEqual(self.rows()["model"]["status"], "info")
        self.assertEqual(self.rows()["demo"]["status"], "info")

    def test_the_rows_are_computed_once_and_a_snapshot_does_not_rebuild_them(self):
        with patch("ai_code_engineer.setup.audit", wraps=setup.audit) as counted:
            for _ in range(4):
                self.card()
        self.assertEqual(counted.call_count, 1, "the card re-ran its own audit per snapshot")

    def test_the_check_button_is_the_one_click_that_asks_the_provider(self):
        with patch("ai_code_engineer.setup.reach",
                   return_value=([self.LOCAL], "live", "")) as probed:
            card = self.action("setup_check")
        self.assertEqual(probed.call_count, 1)
        by_id = {row["id"]: row for row in card["rows"]}
        self.assertEqual(by_id["provider"]["status"], "ok")
        self.assertEqual(by_id["model"]["status"], "ok")
        self.assertTrue(card["show"], "the checks reopen the card they just refreshed")

    def test_a_blocked_check_reports_the_tally_rather_than_a_colour(self):
        patcher = patch("ai_code_engineer.setup.reach", return_value=([], "live", "connection refused"))
        with patcher:
            self.action("setup_check")
        self.assertIn("blocking", self.said())
        self.assertEqual(self.rows()["provider"]["status"], "bad")

    def test_the_card_never_carries_the_key_it_was_handed(self):
        self.controller.key = "sk-synthetic-secret-for-tests"
        patcher = patch("ai_code_engineer.setup.reach", return_value=([self.LOCAL], "live", ""))
        with patcher:
            self.action("setup_check")
        self.assertNotIn("sk-synthetic-secret", json.dumps(self.controller.snapshot()))

    # ------------------------------- the proof -------------------------------
    def test_the_offline_proof_replaces_only_the_row_it_proves(self):
        result = {"proposal_apply_rollback": "passed", "note": "", "llm_used": False}
        with patch("ai_code_engineer.setup.run_demo", return_value=result):
            card = self.action("setup_demo")
        by_id = {row["id"]: row for row in card["rows"]}
        self.assertEqual(by_id["demo"]["status"], "ok")
        self.assertEqual(by_id["provider"]["status"], "info", "the proof is not a re-check")
        self.assertEqual(card["demo"], result)
        self.assertIn("proof held", self.said())

    def test_a_proof_that_did_not_complete_is_said_as_one(self):
        with patch("ai_code_engineer.setup.run_demo",
                   return_value={"proposal_apply_rollback": "failed", "note": "rollback did not hold"}):
            self.action("setup_demo")
        self.assertEqual(self.rows()["demo"]["status"], "bad")
        self.assertIn("rollback did not hold", self.said())

    # ------------------------------- dismissing it -------------------------------
    def test_hiding_the_card_survives_the_next_unrelated_save(self):
        self.action("setup_hide")
        self.controller.set_pref("theme", "dark")
        self.controller.close()
        second = self.build()
        self.assertFalse(second.snapshot()["setup"]["show"])
        registry = json.loads((self.app_dir / ".agent-projects.json").read_text(encoding="utf-8"))
        self.assertTrue(registry["ui"]["setup_seen"])

    def test_the_way_back_is_the_click_that_rechecks(self):
        """There is no bare "show it again": the only route back to the card is the Settings entry that
        says it will ask this machine, so re-opening and re-checking are one honest click."""
        self.action("setup_hide")
        self.assertFalse(self.card()["show"])
        with patch("ai_code_engineer.setup.reach", return_value=([self.LOCAL], "live", "")):
            card = self.action("setup_check")
        self.assertTrue(card["show"])
        self.assertEqual(len(card["rows"]), 8)
        self.assertEqual({row["id"]: row["status"] for row in card["rows"]}["provider"], "ok")

    def test_a_hidden_card_still_answers_the_shape_the_front_end_reads(self):
        """The client reads `setup.rows` and `setup.counts` on every snapshot, so the keys have to be
        there even when the card is off — a missing key is a blank dock, not a hidden one."""
        self.action("setup_hide")
        card = self.card()
        self.assertEqual(set(card), {"show", "rows", "counts", "tally", "demo", "busy"})
        self.assertFalse(card["show"])
        self.assertEqual(sum(card["counts"].values()), len(card["rows"]))
class TheModelFilter(unittest.TestCase):
    """Searching the model list is a read over what the provider returned.

    The first version wrote the filtered subset back into the catalog, so a search cost the user
    every model that did not match until the next refresh — and then cleared ``model`` when the
    query stopped matching the one their task was running.
    """

    ENTRIES = [{"id": "qwen2.5-coder:1.5b", "name": "qwen 2.5 coder", "cloud": False,
                "description": "Runs locally on your device.  |  0.99 GB"},
               {"id": "llama3.1:70b-cloud", "name": "llama 3.1 70b", "cloud": True,
                "description": "Ollama cloud model - internet and an Ollama account required."},
               {"id": "codellama:13b", "name": "codellama 13b", "cloud": False,
                "description": "Runs locally on your device.  |  7.37 GB"}]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.controller = AgentController(Path(self.temp.name).resolve())
        self.addCleanup(self.controller.close)
        self.controller.catalogs["Ollama"] = [dict(entry) for entry in self.ENTRIES]
        self.controller.model = "codellama:13b"

    def listed(self):
        return [entry["id"] for entry in self.controller.visible_models()]

    def test_a_filter_narrows_the_list_without_losing_the_catalog(self):
        self.controller.set_filter("runs locally")
        self.assertEqual(self.listed(), ["qwen2.5-coder:1.5b", "codellama:13b"])
        self.controller.set_filter("")
        self.assertEqual(self.listed(), [entry["id"] for entry in self.ENTRIES],
                         "every model the provider listed is still there after a search")

    def test_the_view_cannot_be_used_to_rewrite_the_catalog(self):
        self.controller.visible_models().clear()
        self.assertEqual(len(self.controller.catalogs["Ollama"]), 3,
                         "the caller gets a list it may sort or drop without touching the store")

    def test_searching_never_deselects_the_model_a_task_is_running(self):
        self.controller.set_filter("cloud")
        self.assertNotIn("codellama:13b", self.listed(), "it is hidden from the list")
        self.assertEqual(self.controller.model, "codellama:13b", "but it is still the model in use")
        self.assertIn("codellama 13b", self.controller._model_info(),
                      "and the settings line still describes it")

    def test_the_snapshot_serves_the_view_rather_than_the_catalog(self):
        self.controller.set_filter("13b")
        served = [entry["id"] for entry in self.controller.snapshot()["provider"]["models"]]
        self.assertEqual(served, ["codellama:13b"])

    def test_the_snapshot_carries_the_cost_of_the_task_on_screen(self):
        self.controller.session = {"id": "s-1", "root": str(self.controller.app_dir),
                                   "state": "PROPOSED", "changes": [],
                                   "metrics": {"prompt_tokens": 2050, "completion_tokens": 180}}
        self.assertEqual(self.controller.snapshot()["provider"]["metrics"],
                         {"prompt_tokens": 2050, "completion_tokens": 180})

    def test_a_window_that_has_run_nothing_says_nothing_about_cost(self):
        # `{}` rather than zeros: the drawer would otherwise open claiming the last answer was free.
        self.assertEqual(self.controller.snapshot()["provider"]["metrics"], {})

    def test_the_settings_line_says_what_the_filter_hid(self):
        self.controller.model = ""
        self.controller.set_filter("cloud")
        info = self.controller.snapshot()["settings"]["model_info"]
        self.assertIn("1 of 3", info)
        self.assertIn("Clear the filter", info)

    def test_typing_in_the_box_does_not_write_preferences_to_disk(self):
        with patch.object(AgentController, "_save_state") as saved:
            for letter in "qwe":
                self.controller.set_filter(letter)
        self.assertEqual(saved.call_count, 0, "a filter is a view, not a preference")
class TheModuleGraph(unittest.TestCase):
    """Item 11's picture: the Sources card asks for the module graph, and the click is answered.

    Three rules this window has already paid for are pinned here. The graph is *fetched* — it is not a
    snapshot field, because a snapshot goes out on every streamed log line and building this walks the
    tree. The answer is data the caller can draw, never a bare event. And a click that cannot draw
    anything answers in words, because an empty sheet reads as a project with no structure.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.app_dir = Path(temp.name).resolve()
        self.repo = self.app_dir / "reactor"
        for folder, name, imports in (("core", "Registry", []),
                                      ("auth", "Jwt", ["core.Registry"]),
                                      ("web", "Endpoint", ["auth.Jwt", "core.Registry"])):
            where = self.repo / folder / "src/main/java/com/acme"
            where.mkdir(parents=True)
            (where / (name + ".java")).write_text(
                "package com.acme.%s;\n" % folder
                + "".join("import %s;\n" % item for item in imports)
                + "public class %s {\n}\n" % name, encoding="utf-8", newline="\n")
        for patcher in (patched_catalog(),):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)
        self.events: list[dict] = []

    def graph(self):
        return self.controller.action("show_graph", {}, self.events.append)

    def test_the_click_answers_with_modules_and_the_edges_between_them(self):
        got = self.graph()
        self.assertEqual(sorted(node["name"] for node in got["nodes"]), ["auth", "core", "web"])
        self.assertEqual([(edge["from"], edge["to"]) for edge in got["edges"]],
                         [("auth", "core"), ("web", "auth"), ("web", "core")])
        self.assertEqual({node["name"]: node["column"] for node in got["nodes"]},
                         {"core": 0, "auth": 1, "web": 2})

    def test_the_sentence_under_the_picture_is_written_by_the_server(self):
        """One surface, one caption: the JS draws what it is told rather than assembling counts into
        English, which is how the Tk window and the web window drifted apart in the first place."""
        got = self.graph()
        self.assertIn("3 modules, 3 dependencies", got["caption"])

    def test_the_caption_follows_the_language_of_the_window(self):
        """The caption is a sentence the tool writes, so it follows the task's language like every other
        one. Arabic arrives from code points so the file stays ASCII on the way to the shell."""
        self.controller.session = {"task": "".join(map(chr, [0x0644, 0x064a, 0x0647, 0x0645, 0x0648]))}
        text = self.graph()["caption"]
        self.assertTrue(any(0x0600 <= ord(char) <= 0x06ff for char in text), text)
        self.assertNotIn("modules", text)

    def test_the_graph_is_never_shipped_with_the_snapshot(self):
        state = self.controller.snapshot()
        self.assertNotIn("graph", state, "a fetched field became a shipped one")
        self.graph()
        self.assertNotIn("graph", self.controller.snapshot())

    def test_a_click_that_reached_no_module_answers_in_words(self):
        """A standalone chat has no folder to walk, and a sheet that opened blank would be a claim about
        the project rather than about the window."""
        self.controller.set_repo("")
        got = self.graph()
        self.assertEqual(got["nodes"], [])
        self.assertTrue(got["note"], "the dead click answered with nothing at all")

    def test_an_empty_folder_answers_in_words_too(self):
        blank = self.app_dir / "empty"
        blank.mkdir()
        self.controller.set_repo(str(blank))
        got = self.graph()
        self.assertEqual(got["nodes"], [])
        self.assertTrue(got["note"])

    def test_a_cycle_in_the_project_is_said_rather_than_drawn_quietly(self):
        left = self.repo / "core/src/main/java/com/acme"
        (left / "Loop.java").write_text("package com.acme.core;\nimport com.acme.web.Endpoint;\n"
                                        "public class Loop {\n}\n", encoding="utf-8", newline="\n")
        got = self.graph()
        self.assertTrue(got["cyclic"])
        self.assertIn("cycle", got["caption"], "the layout is approximate and the reader must know")

    def test_the_click_changes_nothing_in_the_project(self):
        """Reading a folder is what this is: no event reaches the thread, and no file is touched."""
        before = sorted(str(path.relative_to(self.repo)) for path in self.repo.rglob("*.java"))
        self.graph()
        self.assertEqual(self.events, [])
        self.assertEqual(sorted(str(path.relative_to(self.repo)) for path in self.repo.rglob("*.java")),
                         before)


if __name__ == "__main__":
    unittest.main()
