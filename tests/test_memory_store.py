"""The two memory files a project keeps, and the four gates that make them trustworthy.

Memory written by the model has to be unreachable by the model, readable by a person, bounded on
every axis that a long session can stretch, and clean of the secrets a build log printed into it.
These tests hold each of those: the store's own reads and writes, the workspace policy that refuses
a proposal touching `.agent`, the caps that answer a full section by refusing the newcomer rather
than evicting the constraint that arrived first, and the redaction that runs before anything reaches
disk.
"""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import memory_store
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.memory_store import MemoryDoc, MemoryStore
from ai_code_engineer.workspace import Workspace

CHAT = "0" * 32
OTHER = "1" * 32


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.store = MemoryStore(self.root)

    def test_the_two_layers_live_where_the_specification_says(self):
        self.assertEqual(self.store.project_path(),
                         self.root / ".agent" / "memory" / "project.md")
        self.assertEqual(self.store.chat_path(CHAT),
                         self.root / ".agent" / "memory" / "chats" / (CHAT + ".md"))

    def test_a_project_that_remembered_nothing_created_no_folder(self):
        self.assertFalse((self.root / ".agent").exists())
        self.assertEqual(self.store.read_project().fields, {})

    def test_no_project_means_no_memory(self):
        with self.assertRaises(PolicyError):
            MemoryStore("   ")

    def test_a_chat_id_is_the_one_identity_the_engine_already_validates(self):
        for bad in ("../../project.md", "chat-1", CHAT[:31], CHAT + "z", ""):
            with self.assertRaises(PolicyError, msg=bad):
                self.store.chat_path(bad)

    def test_writing_a_chat_creates_only_that_one_file(self):
        self.store.set_text("task", "Fix the login redirect", memory_store.LAYER_CHAT, CHAT)
        self.store.set_text("task", "Another chat entirely", memory_store.LAYER_CHAT, OTHER)
        names = sorted(path.name for path in self.store.chats_dir.glob("*"))
        self.assertEqual(names, sorted([CHAT + ".md", OTHER + ".md"]))
        self.assertEqual(sorted(self.store.chat_ids()), sorted([CHAT, OTHER]))

    def test_reset_removes_the_layer_it_was_asked_about_and_no_other(self):
        self.store.set_text("task", "Fix it", memory_store.LAYER_CHAT, CHAT)
        self.store.add_items("constraints", ["Java 17"], memory_store.LAYER_PROJECT)
        self.assertTrue(self.store.reset(memory_store.LAYER_CHAT, CHAT))
        self.assertFalse(self.store.chat_path(CHAT).exists())
        self.assertTrue(self.store.project_path().exists())
        self.assertFalse(self.store.reset(memory_store.LAYER_CHAT, CHAT),
                         "resetting what is already gone says so rather than pretending")


class PolicyTests(unittest.TestCase):
    """The reason the store sits inside the project at all: `.agent` is policy, not source."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.store = MemoryStore(self.root)
        self.ws = Workspace(self.root)

    def test_a_proposal_cannot_write_the_memory_it_is_held_to(self):
        self.store.set_goal("Ship the auth endpoint", source="user")
        with self.assertRaises(PolicyError):
            self.ws.path(".agent/memory/project.md", writable=True)

    def test_the_memory_never_shows_up_in_the_repository_map(self):
        self.store.set_goal("Ship the auth endpoint")
        self.store.add_items("decisions", ["Use the existing session table"],
                             memory_store.LAYER_PROJECT)
        self.assertNotIn(".agent", json.dumps(self.ws.repo_map()))

    def test_a_model_cannot_read_its_own_compass_as_a_source_file(self):
        self.store.set_goal("Ship the auth endpoint")
        with self.assertRaises(PolicyError):
            self.ws.read(".agent/memory/project.md")


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.store = MemoryStore(self.root)

    def test_sections_survive_being_written_and_read_back(self):
        doc = MemoryDoc("project")
        doc.fields = {"goal": "Ship the auth endpoint",
                      "constraints": ["Never rename the package", "Java 17 only"],
                      "decisions": ["Session table, not JWT"],
                      "open_issues": ["Does the proxy strip the header?"],
                      "progress": ["2026-10-05: applied the route (verified)"]}
        self.store.write_project(doc)
        again = self.store.read_project()
        self.assertEqual(again.fields, doc.fields)
        self.assertEqual(again.layer, "project")

    def test_the_file_reads_as_markdown_a_person_can_edit(self):
        self.store.set_text("task", "Fix the redirect", memory_store.LAYER_CHAT, CHAT)
        self.store.add_items("steps", ["Read the controller", "Found the missing await"],
                             memory_store.LAYER_CHAT, CHAT)
        text = self.store.chat_path(CHAT).read_text(encoding="utf-8")
        self.assertIn("# Chat memory", text)
        self.assertIn("## Current task", text)
        self.assertIn("- Found the missing await", text)

    def test_an_emptied_document_takes_the_file_with_it(self):
        self.store.set_text("task", "Fix it", memory_store.LAYER_CHAT, CHAT)
        self.store.set_text("task", "", memory_store.LAYER_CHAT, CHAT)
        self.assertFalse(self.store.chat_path(CHAT).exists())
        self.assertEqual(self.store.read_chat(CHAT).fields, {})

    def test_a_save_leaves_no_temporary_file_behind(self):
        self.store.set_goal("First")
        self.store.set_goal("Second", source="user")
        strays = [path.name for path in self.store.dir.glob("*")
                  if path.suffix == ".tmp" or not path.name.endswith((".md", ".json"))]
        self.assertEqual(strays, [])

    def test_a_section_somebody_added_by_hand_is_not_deleted_on_the_next_write(self):
        path = self.store.project_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Project memory\n\n## Goal\nShip it\n\n## House rules\n"
                        "- Never touch the pom\n", encoding="utf-8")
        self.store.add_items("decisions", ["Session table"], memory_store.LAYER_PROJECT)
        self.assertIn("Never touch the pom", path.read_text(encoding="utf-8"))

    def test_the_goal_digest_follows_the_goal_and_reports_a_hand_edit(self):
        self.store.set_goal("Ship the auth endpoint", source="user")
        self.assertTrue(self.store.goal_is_current())
        project = self.store.project_path()
        project.write_text(project.read_text(encoding="utf-8").replace(
            "Ship the auth endpoint", "Ship the whole billing rewrite"), encoding="utf-8")
        self.assertFalse(self.store.goal_is_current())
        self.assertEqual(self.store.read_project().text("goal"), "Ship the whole billing rewrite")

    def test_the_sidecar_holds_only_the_record_and_nothing_else(self):
        self.store.set_goal("Ship it", source="user")
        meta = self.store.read_meta()
        self.assertEqual(meta["goal_source"], "user")
        self.assertEqual(len(meta["goal_sha256"]), 64)
        self.assertNotIn("Ship it", json.dumps(meta))

    def test_a_sidecar_that_will_not_parse_costs_nothing_and_is_written_again(self):
        self.store.set_goal("Ship it")
        self.store.meta_path().write_text("{ not json", encoding="utf-8")
        self.assertEqual(self.store.read_meta()["goal_sha256"], "")
        self.store.note_pending_goal("Ship the billing rewrite")
        self.assertEqual(self.store.read_meta()["pending_goal"], "Ship the billing rewrite")


class BoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.store = MemoryStore(self.root)

    def test_a_full_section_refuses_the_newcomer_rather_than_evicting_the_first(self):
        first = ["constraint " + str(index) for index in range(memory_store.CAPS["constraints"])]
        self.store.add_items("constraints", first, memory_store.LAYER_PROJECT)
        answer = self.store.add_items("constraints", ["a brand new rule"],
                                      memory_store.LAYER_PROJECT)
        self.assertEqual(answer["refused"], ["a brand new rule"])
        kept = self.store.read_project().items("constraints")
        self.assertEqual(kept[0], "constraint 0")
        self.assertNotIn("a brand new rule", kept)

    def test_an_item_already_there_is_not_written_twice(self):
        self.store.add_items("decisions", ["Session table"], memory_store.LAYER_PROJECT)
        answer = self.store.add_items("decisions", ["Session table", "Redis cache"],
                                      memory_store.LAYER_PROJECT)
        self.assertEqual(answer["added"], ["Redis cache"])
        self.assertEqual(len(self.store.read_project().items("decisions")), 2)

    def test_one_item_is_one_line_and_no_longer_than_the_cap(self):
        self.store.add_items("progress", ["a note\nwith a second line\tand a tab",
                                          "x" * (memory_store.ITEM_CHARS + 400)],
                             memory_store.LAYER_PROJECT)
        items = self.store.read_project().items("progress")
        self.assertEqual(len(items[0]), len(items[0].strip()))
        self.assertNotIn("\n", items[0])
        self.assertEqual(len(items[1]), memory_store.ITEM_CHARS)

    def test_a_document_too_long_is_refused_rather_than_cut(self):
        doc = MemoryDoc("project")
        doc.fields = {"progress": ["y" * memory_store.ITEM_CHARS] * memory_store.CAPS["progress"]}
        doc.extra = [memory_store.Extra("Padding", ["z" * memory_store.MAX_DOC_CHARS])]
        with self.assertRaises(PolicyError):
            self.store.write_project(doc)
        self.assertFalse(self.store.project_path().exists())

    def test_a_secret_never_reaches_the_memory_file(self):
        self.store.add_items("decisions", ["Datasource password=hunter01"],
                             memory_store.LAYER_PROJECT)
        text = self.store.project_path().read_text(encoding="utf-8")
        self.assertNotIn("hunter01", text)
        self.assertIn("[redacted]", text)
        self.assertNotIn("hunter01", self.store.read_project().items("decisions")[0])

    def test_an_unknown_section_is_not_silently_written(self):
        with self.assertRaises(PolicyError):
            self.store.add_items("feelings", ["optimistic"], memory_store.LAYER_PROJECT)
        with self.assertRaises(PolicyError):
            self.store.set_text("goal", "Typed straight past the guard",
                                memory_store.LAYER_PROJECT)

    def test_the_store_never_writes_outside_its_own_folder(self):
        self.store.set_goal("Ship it")
        self.assertTrue(all(path.is_relative_to(self.store.dir)
                            for path in self.store.dir.rglob("*") if path.is_file()))
        self.assertEqual(self.store.chat_ids(), [])


if __name__ == "__main__":
    unittest.main()
