"""Content that arrives from outside the tool — a repository, a model answer, a page — and then tries to
steer a surface (#14).

Four claims, all of them about the *shape* of the answer rather than the fact of a refusal. A guard that
changes behaviour silently is a guard nobody can debug: the rule file quietly not written, the secret
whose name reached the report but whose value did not, the address reached because the string looked
local. Each test aims at one of those and asserts the refusal names the thing it refused — the path, the
file, the address — in the same breath it says no.

Nothing here sends a packet. The one URL that is allowed to be reached points at a closed port, and the
answer asserted is the tool's own report of a failed request.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_code_engineer import engine, permissions, policy, repair
from ai_code_engineer.config import Settings
from ai_code_engineer.errors import AgentError, PolicyError
from ai_code_engineer.workspace import Workspace
from ai_code_engineer.webapp import controller
from doubles import Sentinel

STORE_FILES = (".agent-permissions.json", ".agent-modes.json")
SECRET_FILES = (".env", ".env.production", "credentials.json", "config/.env", "secrets.yaml")
SECRET = "zzz-secret-value-not-a-real-one"
CLOSED = "http://127.0.0.1:9/nothing-listening-here"
METADATA = "http://169.254.169.254/latest/meta-data/"


class OneAnswerProvider:
    """The same canned action every turn, which is what a model that will not let go of a path does."""

    model = "test"

    def __init__(self, action):
        self.action = action

    def generate(self, messages):
        return json.dumps(self.action)


class WritingTheRulesItIsCheckedAgainst(unittest.TestCase):
    """The two files this tool reads back as its own rules, reached the way a proposal reaches them.

    Every guard refuses something; the finding here is that the caller is told *which* path, because the
    caller is a model that would otherwise spend its next turn spelling the same file differently.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.ws = Workspace(self.root)

    def propose(self, path, body="{}"):
        action = {"action": "propose", "summary": "Set the rules", "checks": ["unit tests"],
                  "changes": [{"path": path, "content": body}]}
        return engine.plan(self.ws, "Change the rules", OneAnswerProvider(action), Settings(),
                           self.root / "runs", progress=lambda message: None)

    def test_a_proposal_naming_the_permission_store_is_refused_by_name(self):
        for name in STORE_FILES:
            with self.subTest(name=name):
                with self.assertRaises(AgentError) as caught:
                    self.propose(name)
                said = str(caught.exception)
                self.assertIn(name, said, "the refusal says which file it refused")
                self.assertIn("Protected path", said, "and says which gate answered")
                self.assertFalse((self.root / name).exists(), "and it wrote nothing")

    def test_a_spelling_change_does_not_open_the_name_gate(self):
        """Case, a nested directory and the bare name are three strings, one file."""
        for spelling in (STORE_FILES[0], STORE_FILES[0].upper(), "src/" + STORE_FILES[0]):
            with self.subTest(spelling=spelling):
                with self.assertRaises(PolicyError) as caught:
                    self.ws.path(spelling, writable=True)
                self.assertIn(STORE_FILES[0], str(caught.exception).casefold(),
                              "the refusal carries the name it matched, not the string it was handed")

    def test_the_rules_stay_readable_because_a_tool_that_cannot_see_its_limits_cannot_obey_them(self):
        """The write gate and the read gate answer different questions: an instruction file a model may
        not write is still a file it has to be able to read."""
        (self.root / "AGENTS.md").write_text("Never edit the migration folder.\n", encoding="utf-8")
        self.assertIn("Never edit the migration folder", self.ws.read("AGENTS.md")["content"])
        with self.assertRaises(PolicyError) as caught:
            self.ws.path("AGENTS.md", writable=True)
        self.assertIn("AGENTS.md", str(caught.exception))


class ANameThatMeansASecret(unittest.TestCase):
    """`ignore.SECRET_NAME` owns these names; the read gate and the walk have to agree about them."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        for name in SECRET_FILES:
            (self.root / name).write_text("DB_PASSWORD=" + SECRET + "\n", encoding="utf-8")
        self.ws = Workspace(self.root)

    def test_each_secret_name_is_refused_for_reading_and_says_which(self):
        for name in SECRET_FILES:
            with self.subTest(name=name):
                with self.assertRaises(PolicyError) as caught:
                    self.ws.read(name)
                self.assertIn(name.split("/")[-1], str(caught.exception))

    def test_the_walk_never_offers_one(self):
        """A name in the file map is an invitation to the next turn, and the map is built by a different
        function than the gate — so both have to answer from the same predicate."""
        listed = json.dumps(self.ws.files())
        self.assertIn("app.py", listed, "the map is not empty, so the cut below is a rule and not a bug")
        for name in SECRET_FILES:
            self.assertNotIn(name.split("/")[-1], listed, name)

    def test_the_ready_report_carries_names_and_never_a_value(self):
        """`.env` is the one file this tool reads on purpose, for the key names alone; the assertion is
        about what did not reach the report."""
        from ai_code_engineer import service_runner
        (self.root / ".env.example").write_text("DB_PASSWORD=\n", encoding="utf-8")
        self.assertNotIn(SECRET, json.dumps(service_runner.check_project_readiness(self.root)))


class AFailureLineStoredAsARule(unittest.TestCase):
    """The fix-round ledger: one sentence kept about a failure, written into the store every later task
    in the folder reads."""

    def setUp(self):
        self.session = {"fix_round": 2,
                        "evidence": "password=" + SECRET + " jdbc:postgresql://app:" + SECRET
                                    + "@db/sales\n\tat com.example.Pool(Pool.java:12)",
                        "runs": [{"status": "passed"}],
                        "summary": "bind the pool to the profile that has the credentials",
                        "changes": [{"path": "src/main/java/Pool.java"}]}

    def test_the_stored_rule_carries_no_value(self):
        label, fact = repair.settled_rule(self.session)
        self.assertTrue(label and fact, "the round is still worth a sentence")
        self.assertNotIn(SECRET, label + fact,
                         "a Maven failure line quotes the connection string it broke on")
        self.assertIn("[redacted]", label, "and it says something was taken out, rather than lying")

    def test_the_round_is_recorded_only_for_a_round_that_settled(self):
        """A first-pass run has no rule to learn, and a run that did not pass has no rule to keep: writing
        either would fill the store with stack traces every later task already has."""
        self.assertEqual(repair.settled_rule(dict(self.session, fix_round=0)), ("", ""))
        self.assertEqual(repair.settled_rule(dict(self.session, runs=[{"status": "failed"}])), ("", ""))
        self.assertEqual(repair.settled_rule(dict(self.session, evidence="")), ("", ""))


class TheRouteAndNotOnlyTheMethod(unittest.TestCase):
    """The address gate has to answer the page's action name, not only the handler a test can call."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "project"
        self.repo.mkdir()
        self.window = controller.AgentController(self.app_dir)
        self.window.repo = str(self.repo)
        self.asked = []
        self.window.confirm = lambda *args, **kwargs: (self.asked.append(args), True)[1]
        self.aimed = Sentinel()
        stopper = patch("urllib.request.urlopen", self.aimed)
        stopper.start()
        self.addCleanup(stopper.stop)

    def route(self, url):
        return self.window.action("api_test", {"url": url}, lambda event: None)

    def test_the_route_refuses_the_metadata_address_by_name_and_kind(self):
        with self.assertRaises(PolicyError) as caught:
            self.route(METADATA)
        said = str(caught.exception)
        self.assertIn("169.254.169.254", said)
        self.assertIn("link-local", said, "and says what kind of place it is, in words not codes")
        self.assertEqual(self.aimed.aimed, [], "nothing was aimed, so nothing was resolved either")

    def test_the_route_still_reaches_its_own_machine(self):
        """A guard that stops the work is a guard that gets turned off: loopback keeps exactly the one ask
        it had before this gate existed, and the request is built and sent."""
        res = self.route(CLOSED)
        self.assertTrue(res["ok"])
        self.assertEqual(self.aimed.aimed, [CLOSED])
        self.assertEqual(len(self.asked), 1, "the escalation added no dialog to an address at home")

    def test_a_declared_row_opens_a_private_address_through_the_route(self):
        """The escalation has one way out, and it is the way the refusal names: a row the folder wrote."""
        with self.assertRaises(PolicyError):
            self.route("http://10.0.0.9:3000/")
        self.assertEqual(self.aimed.aimed, [], "a yes on the ask does not reach a neighbour")
        permissions.declare(self.app_dir, self.repo, policy.NETWORK, policy.ALLOW)
        self.route("http://10.0.0.9:3000/")
        self.assertEqual(self.aimed.aimed, ["http://10.0.0.9:3000/"])
        self.assertEqual(self.asked, [], "the folder already answered this class, twice over")


if __name__ == "__main__":
    unittest.main()
