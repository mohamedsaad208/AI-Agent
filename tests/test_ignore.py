"""One owner for "is this path worth looking at", and the gate it must never loosen.

Three lists used to answer this question by hand-copied agreement, and the cost showed twice: a
directory the planner refused was hidden in the picker for a different reason, and nothing in any of
them knew that this tool writes its own chat transcripts into the workspace it is asked to index.
Measured before the split was closed, 400 of the 591 paths the repository map walked on this project
were `.agent-webview/`, `.agent-chats/` and `.design-preview/`.

The tests below are therefore about two opposite risks: a name that moves from the security list to the
tidiness list and silently becomes readable, and a tidiness list so keen that it hides a file somebody
named on purpose.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_code_engineer import ignore, runner
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.workspace import Workspace

# Every name `workspace.BLOCKED_PARTS` held before this module existed. The list is written out here
# rather than imported because its whole purpose is to catch a future edit to the real one.
WAS_BLOCKED = (".git", ".env", ".ssh", ".aws", ".azure", ".gnupg", ".codex", ".agents", ".agent-runs",
               ".agent-projects.json", ".agent-plans", ".agent-memory", ".venv", "venv",
               "node_modules", "target", "build", "dist", "__pycache__", ".idea", ".gradle", ".m2")


class TheSecurityListStaysShut(unittest.TestCase):
    def test_every_name_the_old_policy_list_refused_is_still_refused(self):
        """Moving generated directories into their own category must not narrow the gate.

        `refused_dir` is what `Workspace.path()` asks, so a name dropped from both lists would become
        readable by a model that guessed it — the one regression worth a hard-coded copy of the past.
        """
        for name in WAS_BLOCKED:
            self.assertTrue(ignore.refused_dir(name), name)

    def test_the_gate_and_the_walk_ask_the_same_question(self):
        for name in WAS_BLOCKED + ("coverage", "htmlcov", ".agent-webview", "generated-sources"):
            with tempfile.TemporaryDirectory() as temp:
                folder = Path(temp) / name
                folder.mkdir()
                (folder / "inside.py").write_text("x = 1\n", encoding="utf-8")
                workspace = Workspace(Path(temp))
                with self.assertRaises(PolicyError, msg=name):
                    workspace.path(name + "/inside.py")
                self.assertEqual([row for row in workspace.files() if row.startswith(name + "/")],
                                 [], name)

    def test_a_credential_and_an_ntfs_alias_are_refused_and_a_source_file_that_names_one_is_not(self):
        self.assertTrue(ignore.credential("token.json"))
        self.assertTrue(ignore.credential(".env.production"))
        self.assertTrue(ignore.alias("TOKEN~1.JSON"))
        self.assertFalse(ignore.credential("JwtTokenProvider.java"))
        self.assertFalse(ignore.alias("service.py"))


class TheThreeQuestionsAreThree(unittest.TestCase):
    """The lists genuinely differ; what was wrong was agreeing by copy-paste."""

    def test_a_build_output_folder_is_not_a_project_even_though_it_might_be_a_file(self):
        self.assertTrue(ignore.project_dir("bin"))
        self.assertTrue(ignore.project_dir("vendor"))
        self.assertTrue(ignore.project_dir("target"))          # via the shared half
        self.assertFalse(ignore.refused_dir("bin"), "a Rails `bin/` holds executables people write")
        self.assertFalse(ignore.refused_dir("out"))

    def test_the_picker_adds_the_machine_and_nothing_else(self):
        self.assertTrue(ignore.picker_dir("AppData"))
        self.assertTrue(ignore.picker_dir("$RECYCLE.BIN"))
        self.assertTrue(ignore.picker_dir("node_modules"))
        self.assertFalse(ignore.picker_dir("projects"))
        self.assertFalse(ignore.picker_dir("bin"), "the picker is navigation, not a verdict on content")

    def test_the_walks_disagree_on_purpose_and_the_difference_is_tested(self):
        """`runner.projects` may refuse `bin` while the map still lists it, because the two questions
        are "could a build run here" and "is this file part of the project". A shared list would have
        made one of them wrong."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "bin").mkdir()
            (root / "bin" / "pom.xml").write_text("<project/>\n", encoding="utf-8")
            (root / "pom.xml").write_text("<project/>\n", encoding="utf-8")
            (root / "target").mkdir()
            (root / "target" / "pom.xml").write_text("<project/>\n", encoding="utf-8")
            listed = Workspace(root).files()
            self.assertIn("bin/pom.xml", listed, "the map may see it")
            self.assertNotIn("target/pom.xml", listed)
            offered = runner.projects(root)
            self.assertNotIn("bin", offered, "a build must not be offered in somebody's script folder")
            self.assertNotIn("target", offered)
            self.assertEqual(offered, ["."])


class NoiseAsNoise(unittest.TestCase):
    def test_a_minified_or_generated_file_leaves_the_list_and_a_similar_name_does_not(self):
        for name in ("app.min.js", "vendor.min.css", "vendor.bundle.js", "messages_pb2.py",
                     "Widget.designer.cs", "yarn.lock", "a.js.map"):
            self.assertTrue(ignore.generated_file(name), name)
        for name in ("admin.js", "messages.py", "Widget.cs", "block.json", "a.py"):
            self.assertFalse(ignore.generated_file(name), name)

    def test_a_generated_directory_says_so_in_its_name(self):
        self.assertTrue(ignore.generated_dir("generated-sources"))
        self.assertTrue(ignore.generated_dir("generated"))
        self.assertTrue(ignore.generated_dir("gen"))
        self.assertFalse(ignore.generated_dir("chargeback-api"),
                         "a segment must say generated; sharing two letters is not a signal")
        self.assertTrue(ignore.refused_dir("generated"))

    def test_the_tools_own_output_is_the_expensive_case(self):
        """The regression this round was built for: the agent's chat transcripts and preview folder sat
        in no list and filled two thirds of the map on the project's own repository."""
        for name in (".agent-webview", ".agent-chats", ".design-preview"):
            self.assertTrue(ignore.refused_dir(name), name)


class GitignoreRules(unittest.TestCase):
    PATTERNS = """
# comment, and a blank line follows

target/
logs
*.log
build/
!keep.log
node_modules
src/main/resources/**/secret*
.tmp/
dist
"""

    def setUp(self):
        self.rules = ignore.Rules(ignore.Rules.parse(self.PATTERNS))

    def check(self, path, is_dir, want, why=""):
        self.assertEqual(self.rules.ignores(path, is_dir), want,
                         "{}: {}{}".format(path, "ignored" if want else "kept", (" — " + why) if why else ""))

    def test_a_directory_rule_takes_everything_under_it(self):
        self.check("target", True, True)
        self.check("target/classes", True, True, "an ancestor is ignored")
        self.check("a/b/target", True, True, "no slash means any depth")
        self.check("src/main/resources", False, False)

    def test_a_name_without_a_suffix_matches_a_directory_and_its_contents(self):
        self.check("logs", True, True)
        self.check("logs/run.txt", False, True)
        self.check("mylogs", True, False, "a prefix is not a match")
        self.check("logs-old", True, False)

    def test_a_star_stops_at_a_slash_and_two_stars_do_not(self):
        self.check("debug.log", False, True)
        self.check("a/b/debug.log", False, True)
        self.check("keep.log", False, False, "a later ! line puts it back")
        self.check("src/main/resources/x/secret.yml", False, True, "** crosses a directory")
        self.check("src/main/resources/a/b/secret.yml", False, True)
        self.check("src/main/resources/app.yml", False, False)

    def test_a_pattern_with_a_slash_is_anchored_and_one_without_is_not(self):
        # `src/main/resources/**/secret*` carries an inner slash, so it stops meaning "anywhere".
        self.check("other/place/secret.yml", False, False)
        self.check("dist", False, True, "no slash, so it matches a file or a directory at any depth")
        self.check("a/b/dist", True, True)

    def test_a_trailing_slash_means_directories_only(self):
        self.check("build", True, True)
        self.check("build", False, False)
        self.check("a/b/build/out.jar", True, True)

    def test_character_classes_and_quoted_ranges(self):
        rules = ignore.Rules(ignore.Rules.parse("file[0-9].txt\n!odd[abc]x\n"))
        self.assertTrue(rules.ignores("file3.txt", False))
        self.assertFalse(rules.ignores("fileX.txt", False))
        self.assertFalse(rules.ignores("oddax", False), "a negated line that matches puts it back")

    def test_a_nested_file_refines_the_parents(self):
        nested = self.rules.child("*.log\n!keep.log\n")
        self.assertTrue(nested.ignores("vendor/other.log", False))
        self.assertFalse(nested.ignores("vendor/keep.log", False),
                         "the directory's own ! line is tried last")

    def test_a_file_under_an_excluded_directory_cannot_be_put_back(self):
        """git's rule, inherited rather than reinvented: `logs/` is excluded, so no `!` line inside it
        re-includes anything — a tool that disagreed would list files a person cannot commit."""
        nested = self.rules.child("!logs/important.log\n")
        self.assertTrue(nested.ignores("logs/important.log", False))
        self.assertTrue(self.rules.ignores("logs/anything.txt", False))

    def test_an_unreadable_or_huge_file_is_no_rules_rather_than_a_failed_task(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertFalse(ignore.Rules.load(Path(temp)))
            (Path(temp) / ".gitignore").write_text("a/\n", encoding="utf-8")
            self.assertTrue(ignore.Rules.load(Path(temp)))
            (Path(temp) / ".gitignore").write_text("x\n" * 40000, encoding="utf-8")
            self.assertFalse(ignore.Rules.load(Path(temp)), "over the size limit is treated as none")


class TheWalkUsesThem(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        for relative, text in {
            ".gitignore": "logs/\n*.tmp\n",
            "app.py": "import os\n",
            "README.md": "# project\n",
            "logs/run.txt": "noise\n",
            "build/lib.py": "noise\n",
            ".agent-chats/chat.json": "[]\n",
            ".agent-webview/index.html": "<p>x</p>\n",
            "src/main/generated/Dto.java": "class Dto {}\n",
            "static/app.min.js": "1\n",
            "scratch.tmp": "noise\n",
            "bin/run.sh": "#!/bin/sh\n",
        }.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.workspace = Workspace(self.root)

    def test_only_the_source_is_left(self):
        # `.gitignore` stays: it is a file somebody wrote, and the tool's own policy has always listed
        # it so a task can see what the repository chose to hide.
        self.assertEqual(self.workspace.files(),
                         [".gitignore", "README.md", "app.py", "bin/run.sh"])

    def test_the_walk_reports_what_it_stopped_at(self):
        self.workspace.files()
        self.assertGreaterEqual(self.workspace.skipped_generated, 4)
        self.assertGreaterEqual(self.workspace.skipped_ignored, 2)
        note = self.workspace.map_note()
        self.assertIn("not shown", note)
        self.assertIn(".gitignore", note)

    def test_the_note_is_absent_on_a_clean_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            clean = Path(temp)
            (clean / "a.py").write_text("x = 1\n", encoding="utf-8")
            workspace = Workspace(clean)
            workspace.files()
            self.assertEqual(workspace.map_note(), "")

    def test_a_named_path_still_reaches_a_file_the_walk_did_not_list(self):
        """Hiding noise and refusing access are different decisions, and only the first one was made:
        an operator who asks for `static/app.min.js` by name gets it."""
        read = self.workspace.read("static/app.min.js")
        self.assertEqual(read["content"].strip(), "1")

    def test_a_nesting_gitignore_applies_below_its_own_directory_only(self):
        nested = self.root / "src" / ".gitignore"
        nested.write_text("Dto.java\n", encoding="utf-8")
        (self.root / "src" / "Dto.java").write_text("class Dto {}\n", encoding="utf-8")
        (self.root / "Dto.java").write_text("class Other {}\n", encoding="utf-8")
        listed = self.workspace.files()
        self.assertIn("Dto.java", listed, "the root keeps a file a subdirectory chose to ignore")
        self.assertNotIn("src/Dto.java", listed)


if __name__ == "__main__":
    unittest.main()
