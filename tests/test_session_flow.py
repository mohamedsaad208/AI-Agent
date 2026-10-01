"""What a record on disk means is answered once, for both windows.

`host.py` is the seam for what a window *says*; this is the seam for what a file *is*. The two windows
may disagree about presentation -- one holds a `StringVar`, the other a snapshot dict -- and that costs
nothing. A disagreement about whether a plan ledger may be trusted, or which runs count as this chat's
attempts, is a bug that only shows up in whichever window is not being tested, which is how this
project has already had to undo it twice.

So each rule below is written once and each window keeps only its own state: the ledger it is standing
on, the cache it holds, the label it is drawing. The ratchet class is what keeps it that way.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import planbook, repair, runner, session_flow
from ai_code_engineer.errors import AgentError
from ai_code_engineer.workspace import Workspace

SRC = Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"
PLAN = """# Delivery plan

## Phase 1: scaffold
Create the application and its manifest.

## Phase 2: login endpoint
Add the login route and its tests.
"""


def stored_session(run_id, root, chat_id, created="2026-09-29T10:00:00", **extra):
    """One round, written the way the loop writes it: a folder of one `session.json`."""
    session = dict(extra, id=run_id, schema=1, events=[], root=str(root), chat_id=chat_id,
                   created=created, state="VERIFICATION_FAILED",
                   changes=[{"path": "app.py", "after": "answer = 2\n"}],
                   runs=[{"label": "Python unittest", "status": "failed", "exit_code": 1,
                          "seconds": 1.5, "failures": [], "tail": "FAILED (failures=3)",
                          "proof": {"tests": 7, "failures": 3, "errors": 0, "source": "JUnit XML"},
                          "target": "."}])
    folder = Path(root).parent / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "session.json").write_text(json.dumps(session), encoding="utf-8")
    return session


class TheCacheReadsAFileOnce(unittest.TestCase):
    """A navigation list reads every session in the folder on each poll; parsing them twice is the
    cost that made the sidebar lag."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.path = self.base / "session.json"
        self.calls = []

    def load(self, path):
        self.calls.append(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_same_file_is_parsed_once_however_often_it_is_asked_for(self):
        self.path.write_text('{"id": "a"}', encoding="utf-8")
        cache = {}
        first = session_flow.read_cached(cache, self.path, self.load)
        for _ in range(3):
            self.assertEqual(session_flow.read_cached(cache, self.path, self.load), first)
        self.assertEqual(len(self.calls), 1, "the reader was asked again for an unchanged file")

    def test_a_rewrite_is_read_again_because_the_record_actually_changed(self):
        cache = {}
        self.path.write_text('{"id": "a"}', encoding="utf-8")
        session_flow.read_cached(cache, self.path, self.load)
        self.path.write_text('{"id": "a-longer-name"}', encoding="utf-8")
        self.assertEqual(session_flow.read_cached(cache, self.path, self.load), {"id": "a-longer-name"})
        self.assertEqual(len(self.calls), 2)

    def test_a_missing_file_is_answered_without_bothering_the_reader(self):
        self.assertIsNone(session_flow.read_cached({}, self.base / "gone.json", self.load))
        self.assertEqual(self.calls, [])

    def test_a_broken_record_stays_answered_as_none(self):
        """Re-reading a file that will not parse turns one unreadable session into a repeated cost on
        every poll, and it is not going to heal between two of them a second apart."""
        self.path.write_text("not json at all", encoding="utf-8")
        cache = {}

        def refuse(_path):
            self.calls.append(1)
            raise AgentError("Session is unreadable or invalid.")

        self.assertIsNone(session_flow.read_cached(cache, self.path, refuse))
        self.assertIsNone(session_flow.read_cached(cache, self.path, refuse))
        self.assertEqual(len(self.calls), 1, "the failure was not cached")


class ThePlanLedgerIsTrustedOrNot(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.repo = root / "repo"
        self.repo.mkdir()
        (self.repo / "plan.md").write_text(PLAN, encoding="utf-8")
        self.plans = root / "plans"
        self.path, self.book = planbook.open_book(self.plans, Workspace(self.repo), "plan.md")

    def session(self, sha):
        return {"root": str(self.repo), "plan_step": 1,
                "plan_reference": {"path": "plan.md", "sha256": sha}}

    def test_a_session_that_is_not_a_plan_step_has_no_ledger(self):
        self.assertIsNone(session_flow.ledger_for(self.plans, {"root": str(self.repo)}))
        no_reference = {"root": str(self.repo), "plan_step": 1}
        self.assertIsNone(session_flow.ledger_for(self.plans, no_reference))

    def test_a_matching_reference_opens_the_ledger_it_belongs_to(self):
        found = session_flow.ledger_for(self.plans, self.session(self.book["plan_sha256"]))
        self.assertEqual(found[0], self.path)
        self.assertEqual(found[1]["steps"][0]["status"], "pending")

    def test_a_plan_whose_text_changed_describes_steps_this_session_never_implemented(self):
        """The whole answer is the sha: the ledger on disk still says step 1 is verified against the
        plan as written yesterday, and that is a claim about work nobody did."""
        (self.repo / "plan.md").write_text(PLAN + "\n## Phase 3: token filter\nRotate it nightly.\n",
                                           encoding="utf-8")
        self.assertIsNone(session_flow.ledger_for(self.plans, self.session(self.book["plan_sha256"])))


class TheAttemptsOfAChat(unittest.TestCase):
    """The repair loop's memory: what ran, what it cost, in this chat and this folder."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.runs = self.base / "runs"
        self.session = stored_session("round-1", self.repo, "chat-a")

    def test_a_window_with_no_open_folder_has_no_attempts(self):
        self.assertEqual(session_flow.attempt_rows(self.runs, None, "chat-a"), [])
        self.assertEqual(session_flow.attempt_history(self.runs, None, "chat-a"), [])

    def test_a_turn_from_another_chat_is_not_this_chats_history(self):
        stored_session("round-2", self.repo, "chat-b")
        rows = session_flow.attempt_rows(self.runs, {"root": str(self.repo)}, "chat-a")
        self.assertEqual([row["session"] for row in rows], ["round-1"])

    def test_the_history_holds_every_round_and_the_rows_the_offer_draws(self):
        for index in range(1, 6):
            stored_session("extra-%d" % index, self.repo, "chat-a",
                           created="2026-09-29T10:0%d:00" % index)
        history = session_flow.attempt_history(self.runs, {"root": str(self.repo)}, "chat-a")
        self.assertEqual(len(history), 6, "every session of the chat, oldest first")
        self.assertEqual(history[0]["id"], "round-1")
        self.assertEqual([row["session"] for row in
                          session_flow.attempt_rows(self.runs, {"root": str(self.repo)}, "chat-a")],
                         [item["id"] for item in history][-repair.MAX_FIX_ROUNDS:])

    def test_a_folder_that_is_not_the_opened_one_is_not_this_chats_history(self):
        other = self.base / "elsewhere"
        other.mkdir()
        stored_session("round-9", other, "chat-a")
        rows = session_flow.attempt_rows(self.runs, {"root": str(self.repo)}, "chat-a")
        self.assertEqual([row["session"] for row in rows], ["round-1"])


class TheLabelsThatPickACommand(unittest.TestCase):
    """A person chooses a label; the engine runs a key. Keeping the two apart is this lookup."""

    def test_the_label_on_screen_resolves_to_the_recipe_key(self):
        recipes = ["maven-test", "python-unittest"]
        chosen = runner.RECIPES["maven-test"]["label"]
        self.assertEqual(session_flow.recipe_for(recipes, chosen), "maven-test")

    def test_a_label_that_is_no_longer_offered_disables_run(self):
        self.assertIsNone(session_flow.recipe_for(["maven-test"], "A recipe since removed"))

    def test_a_module_of_a_multi_project_folder_is_named_as_the_row_names_it(self):
        targets = [{"path": "backend/auth", "label": "auth-service"}]
        self.assertEqual(session_flow.label_for(targets, "backend/auth"), "auth-service")
        self.assertEqual(session_flow.label_for(targets, "frontend"), "")


class TheRuleLivesHere(unittest.TestCase):
    """Both windows have to ask, and neither may keep its own copy of what a record means."""

    def windows(self):
        return {"Tk": (SRC / "gui.py").read_text(encoding="utf-8"),
                "web": (SRC / "webapp" / "controller.py").read_text(encoding="utf-8")}

    def test_both_windows_ask_the_shared_layer(self):
        for name, source in self.windows().items():
            for call in ("session_flow.read_cached", "session_flow.ledger_for",
                         "session_flow.attempt_rows", "session_flow.attempt_history",
                         "session_flow.recipe_for", "session_flow.label_for"):
                self.assertIn(call + "(", source, "%s answers %s by itself" % (name, call))

    def test_neither_window_reads_the_attempt_record_on_its_own(self):
        for name, source in self.windows().items():
            for direct in ("repair.round_history(", "repair.attempts_of("):
                self.assertNotIn(direct, source,
                                 "%s went around session_flow for %s" % (name, direct))

    def test_neither_window_invents_a_second_file_cache(self):
        needle = "st_mtime_ns"
        for name, source in self.windows().items():
            self.assertNotIn(needle, source, "%s is caching a record read again" % name)
        self.assertIn(needle, (SRC / "session_flow.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
