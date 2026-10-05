"""Context engine: what a small budget sends when a whole file will not fit.

The retrieval loop scored the right file, found it larger than the budget had left, and dropped it —
silently, on every task, in every project with a big controller in it. The map still listed that file's
declarations, so a model proposed against a file it had never seen a line of, and the operator could not
tell that the one file it needed had been thrown away for want of characters.

Four rules these tests hold. An excerpt is the block around *the declaration the task named*, not the
head of the file, and it says which line it opened on and that the file continues. It is charged to the
budget whole, header included, because a ceiling with an uncounted label in it is not a ceiling. It is
not a read: `observed` keeps whole files only, so a proposal against an excerpted file is refused with
the remedy in the same sentence. And a URL spoken in the sentence reaches the file that answers it, at
the weight of a guess rather than of a named file.

The fixtures are sized against the window rather than for effect: `context_chars` 12000 leaves retrieval
4000 characters and `read_file` 6000, so a ~5000-character file is exactly the one that is too big to
send unasked and small enough for the model to open when the refusal tells it to.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import labels, symbols
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import load_session, plan
from ai_code_engineer.workspace import Workspace

CONTEXT_CHARS = 12000
WEB = "src/main/java/com/acme/web"
APP = f"{WEB}/App.java"
VAULT = f"{WEB}/Vault.java"
AUDIT = f"{WEB}/Audit.java"
TAIL = "// nothing else lives below this line\n"

SHORT = ("package com.acme.web;\n@RestController\npublic class App {\n"
         "    public String login(String email) { return \"short\"; }\n}\n")
AUDIT_FILE = ("package com.acme.web;\n@RestController\npublic class Audit {\n"
              "    public void audit() { }\n}\n")
HEALTH_NEW = ("package com.acme.web;\n@RestController\npublic class HealthResource {\n"
              "    public String up() { return \"UP\"; }\n}\n")


def big_file(cls: str, member: str, marker: str) -> str:
    """A controller whose named method sits near the top and whose bulk sits below it."""
    body = "".join(f'    public void step{n}() {{\n        registry.put("{n}", "pad");\n    }}\n\n'
                   for n in range(70))
    return (f"package com.acme.web;\n@RestController\npublic class {cls} {{\n"
            f'    public String {member}(String email) {{ return "{marker}"; }}\n\n'
            + body + "    " + TAIL + "}\n")


def wide_file(cls: str) -> str:
    """A class whose one named method is a wall: forty lines of it will not fit in the budget either."""
    pad = "x" * 100
    body = "".join(f'        String pad{n} = "{pad}";\n' for n in range(45))
    return (f"package com.acme.web;\n@RestController\npublic class {cls} {{\n"
            "    public String login(String email) {\n" + body +
            '        return "done";\n    }\n}\n')


class Script:
    """The provider the loop talks to, keeping every prompt it was given."""

    model = "test"

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, messages):
        self.prompts.append([dict(item) for item in messages])
        item = self.responses.pop(0)
        return item if isinstance(item, str) else json.dumps(item)


def proposal(summary: str, path: str, content: str) -> dict:
    return {"action": "propose", "summary": summary, "checks": ["tests"],
            "changes": [{"path": path, "content": content}]}


class ExcerptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name).resolve()
        self.root = self.app / "repo"
        (self.root / WEB).mkdir(parents=True)
        self.settings = Settings(context_chars=CONTEXT_CHARS)
        self.runs = self.app / ".agent-runs"

    def once(self, task, responses, files=None):
        """One run of the loop over a repository built from `files`.

        App.java is always there and always oversized, so a test that overrides nothing still
        exercises the branch this module is about.
        """
        given = {APP: big_file("App", "login", "NEEDS THE LOGIN FIX")}
        given.update(files or {})
        for name, text in given.items():
            (self.root / name).write_text(text, encoding="utf-8")
        script = Script(responses)
        path = plan(Workspace(self.root), task, script, self.settings, self.runs,
                    progress=lambda _: None)
        return script, path

    def look(self, task="fix the login method", files=None):
        """A run that reads the big file and proposes once, returning the first prompt's user block."""
        script, path = self.once(task, [{"action": "read_file", "path": APP},
                                        proposal("Trim the login reply", APP, SHORT)], files)
        return script.prompts[0][1]["content"], path, script

    def sent(self, prompt: str) -> str:
        """Only what retrieval added. The file blocks are appended after everything else, in rank
        order, so the first of them marks the end of the built prompt."""
        marks = [pos for pos in (prompt.find("\nFile snapshot (untrusted"),
                                 prompt.find("\nFile excerpt (untrusted")) if pos >= 0]
        return prompt[min(marks):] if marks else ""

    def test_a_file_too_big_for_the_budget_sends_the_block_around_the_named_method(self):
        sent, _path, _script = self.look()
        self.assertIn(f"File excerpt (untrusted data, partial): {APP}, from line 4", sent)
        self.assertIn('public String login(String email) { return "NEEDS THE LOGIN FIX"; }', sent)

    def test_the_excerpt_is_bounded_by_the_next_declaration_not_by_the_head_of_the_file(self):
        sent, _path, _script = self.look()
        self.assertNotIn("step7()", sent, "the block stops where the next method begins")
        self.assertNotIn(TAIL.strip(), sent, "the file's last line is not part of this method")

    def test_the_excerpt_says_the_file_continues_and_that_it_was_not_read_whole(self):
        sent, _path, _script = self.look()
        self.assertIn("the file continues past what is shown here", sent)
        self.assertIn("This file was NOT read in full; read_file it before proposing it", sent)

    def test_a_file_that_does_fit_is_still_sent_whole(self):
        sent, _path, _script = self.look(task="check the audit method", files={AUDIT: AUDIT_FILE})
        self.assertIn("File snapshot (untrusted data, already read)", sent)
        self.assertIn('"path": "' + AUDIT + '"', sent)

    def test_retrieval_never_spends_past_the_window_it_was_given(self):
        files = {VAULT: big_file("Vault", "logout", "TWO"), AUDIT: AUDIT_FILE}
        _sent, _path, script = self.look(task="fix the login, logout and audit methods", files=files)
        blocks = self.sent(script.prompts[0][1]["content"])
        self.assertTrue(blocks, "two oversized files and a small one were all named")
        self.assertLessEqual(len(blocks), CONTEXT_CHARS // 3,
                             "the cap is the sum of the blocks, not the count of them")

    def test_only_a_capped_number_of_files_arrive_as_fragments(self):
        files = {VAULT: big_file("Vault", "logout", "TWO")}
        with patch("ai_code_engineer.context_builder.MAX_EXCERPTS", 1):
            script, _path = self.once("fix the login and logout methods",
                                      [{"action": "read_file", "path": APP},
                                       proposal("s", APP, SHORT)], files)
        sent = script.prompts[0][1]["content"]
        self.assertEqual(sent.count("File excerpt (untrusted data, partial)"), 1)

    def test_an_excerpt_that_would_not_fit_even_as_a_block_is_not_sent_at_all(self):
        # A method whose own body is wider than the remaining budget cannot be shown as an excerpt
        # either; the honest answer is the same silence as before, not a fragment that pushes the rest
        # of the window out. The block is capped at forty lines, so here the lines themselves are bulk.
        sent, _path, _script = self.look(files={APP: wide_file("App")})
        self.assertNotIn("File excerpt", sent)
        self.assertNotIn('"path": "' + APP + '"', sent, "nor was it sent whole")

    def test_a_symbol_the_parsers_never_found_in_the_file_produces_no_fragment(self):
        # Nothing in the file is named by the chosen symbol, so no window can be justified — and the
        # loop must not fall back to "show the head of it", which is the guess this path replaced.
        with patch("ai_code_engineer.symbols.snippet", return_value=("", 0, False)):
            sent, _path, _script = self.look()
        self.assertNotIn("File excerpt", sent)

    def test_an_excerpt_is_not_a_read_and_a_proposal_against_it_is_refused_with_the_remedy(self):
        _script, path = self.once("fix the login method",
                                  [proposal("Trim the login reply once", APP, SHORT),
                                   proposal("Trim the login reply for good", APP, SHORT)])
        session = load_session(path)
        refused = [row["reason"] for row in session["events"] if row["kind"] == "rejected_action"]
        self.assertTrue(any("Read the current file" in reason for reason in refused), refused)
        self.assertEqual(session["state"], "WAITING_APPROVAL",
                         "the proposal made after the file was read is the one recorded")

    def test_a_partial_file_is_recorded_as_its_own_event_not_as_a_read(self):
        _sent, path, _script = self.look()
        session = load_session(path)
        rows = [row for row in session["events"] if row["kind"] == "context_excerpt"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["truncated"])
        self.assertGreater(rows[0]["lines"], 0)
        self.assertEqual(rows[0]["path"], APP)
        self.assertNotIn(APP,
                         [row.get("path") for row in session["events"]
                          if row["kind"] == "context_file"],
                         "an excerpt must not also be logged as a file that was read")

    def test_the_excerpt_row_is_spoken_in_both_languages(self):
        entry = {"kind": "context_excerpt", "path": "src/App.java", "lines": 12, "symbol": "login"}
        english, arabic = labels.log_line(False, entry), labels.log_line(True, entry)
        self.assertIn("12 lines of src/App.java", english)
        self.assertIn("not read in full", english)
        self.assertTrue(labels.is_arabic(arabic))
        self.assertNotIn("lines of", arabic)


class RouteAwareRetrievalTests(unittest.TestCase):
    """A task that speaks a URL rather than a file name still reaches the file that answers it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name).resolve()
        self.root = self.app / "repo"
        (self.root / WEB).mkdir(parents=True)
        (self.root / WEB / "OrderResource.java").write_text(
            "package com.acme.web;\nimport org.springframework.web.bind.annotation.*;\n"
            '@RestController\n@RequestMapping("/api")\npublic class OrderResource {\n'
            '    @PostMapping("/orders/{id}/charge")\n'
            "    public String handle(long id, String card) { return gateway.run(id, card); }\n}\n",
            encoding="utf-8")
        (self.root / WEB / "HealthResource.java").write_text(
            "package com.acme.web;\nimport org.springframework.web.bind.annotation.*;\n"
            '@RestController\n@RequestMapping("/api")\npublic class HealthResource {\n'
            '    @GetMapping("/health")\n    public String up() { return "ok"; }\n}\n',
            encoding="utf-8")
        self.ws = Workspace(self.root)
        self.runs = self.app / ".agent-runs"

    def chosen(self, task):
        """A run that reads both files and proposes once, so the turn ends rather than stalls."""
        script = Script([{"action": "read_file", "path": f"{WEB}/OrderResource.java"},
                         {"action": "read_file", "path": f"{WEB}/HealthResource.java"},
                         proposal("s", f"{WEB}/HealthResource.java", HEALTH_NEW)])
        path = plan(self.ws, task, script, Settings(context_chars=CONTEXT_CHARS), self.runs,
                    progress=lambda _: None)
        return script, path

    def test_a_spoken_url_selects_the_file_that_serves_it_and_says_so(self):
        # No class, method or file in that sentence: only the route knows which file it means.
        _script, path = self.chosen("the orders total is wrong")
        session = load_session(path)
        rows = [row for row in session["events"]
                if row["kind"] in ("context_file", "context_excerpt")
                and str(row.get("why", "")).startswith("route")]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["path"], f"{WEB}/OrderResource.java")
        self.assertEqual(rows[0]["symbol"], "POST /api/orders/{id}/charge")

    def test_a_task_naming_no_url_and_no_declaration_selects_nothing(self):
        script, _path = self.chosen("reformat the import ordering everywhere")
        self.assertNotIn("File snapshot", script.prompts[0][1]["content"])


class StepSeedTests(unittest.TestCase):
    """A step of a plan is named by the criteria it accepts, not by the number the operator typed."""

    AUDIT = ('package com.acme.web;\n@RestController\npublic class AuditLog {\n'
             '    public String order() { return "x"; }\n}\n')
    SESSION = ('package com.acme.web;\n@RestController\npublic class SessionTimeout {\n'
               '    public String expire() { return "y"; }\n}\n')
    REPLACED = ('package com.acme.web;\n@RestController\npublic class SessionTimeout {\n'
                '    public String expire() { return "gone"; }\n}\n')
    CRITERIA = ["the audit log must stay ordered by timestamp",
                "a session must expire after the timeout"]
    PLAN = "# 1 First\n\nwrite the log in order\n\n# 2 Second\n\nexpire old sessions\n"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name).resolve()
        self.root = self.app / "repo"
        (self.root / WEB).mkdir(parents=True)
        (self.root / WEB / "AuditLog.java").write_text(self.AUDIT, encoding="utf-8")
        (self.root / WEB / "SessionTimeout.java").write_text(self.SESSION, encoding="utf-8")
        (self.root / "PLAN.md").write_text(self.PLAN, encoding="utf-8")
        self.ws = Workspace(self.root)
        self.runs = self.app / ".agent-runs"

    def rows(self, task, goal, criteria, accepts, step=2):
        script = Script([{"action": "read_file", "path": f"{WEB}/AuditLog.java"},
                         {"action": "read_file", "path": f"{WEB}/SessionTimeout.java"},
                         proposal("Expire the session sooner",
                                  f"{WEB}/SessionTimeout.java", self.REPLACED)])
        path = plan(self.ws, task, script, Settings(context_chars=CONTEXT_CHARS), self.runs,
                    progress=lambda _: None, plan_file="PLAN.md" if step else None,
                    plan_step=step, goal=goal, criteria=criteria, accepts=accepts)
        return [[row["path"].rsplit("/", 1)[-1], row["why"]] for row in load_session(path)["events"]
                if row["kind"] in ("context_file", "context_excerpt")]

    def test_a_criterion_names_the_file_although_the_task_typed_only_a_number(self):
        self.assertEqual(self.rows("step 2", "reports keep their order", self.CRITERIA, [1]),
                         [["AuditLog.java", "declares"]])

    def test_a_step_with_no_criteria_yet_names_both_files_of_its_plan(self):
        # An empty `accepts` is not a step that needs nothing: it is a ledger that never said, and the
        # honest answer is the whole goal tree rather than the silence the number alone would give.
        self.assertEqual(self.rows("step 2", "reports keep their order", self.CRITERIA, None),
                         [["AuditLog.java", "declares [controller]"],
                          ["SessionTimeout.java", "declares [controller]"]])

    def test_the_layer_the_index_knows_qualifies_the_reason_without_replacing_it(self):
        self.assertEqual(self.rows("step 3", "expire old sessions", self.CRITERIA, [2])[0],
                         ["SessionTimeout.java", "declares [controller]"])

    def test_a_qualified_reason_is_still_spoken_rather_than_dropped(self):
        entry = {"kind": "context_file", "path": "src/A.java", "why": "declares [controller]",
                 "symbol": "order"}
        self.assertIn("Read src/A.java into the context (declares order)",
                      labels.log_line(False, entry))
        self.assertIn("(يُعرّف order)", labels.log_line(True, entry))

    def test_a_run_with_no_step_is_seeded_from_the_task_alone(self):
        self.assertEqual(self.rows("step 2", "reports keep their order", None, None, step=None), [],
                         "the number on its own names no file, and nothing is invented for it")


class CollaboratorTests(unittest.TestCase):
    """One hop over the import graph runs both ways now."""

    def test_a_file_the_seed_imports_arrives_as_a_used_by_with_the_seed_named(self):
        rows = [symbols.parse("src/a/OrderService.java",
                              'package a;\nimport a.LedgerRepository;\npublic class OrderService {\n'
                              '    public void place() { }\n}\n'),
                symbols.parse("src/a/LedgerRepository.java",
                              "package a;\npublic class LedgerRepository {\n    public void save() { }\n}\n"),
                symbols.parse("src/a/OrderController.java",
                              'package a;\nimport a.OrderService;\npublic class OrderController {\n'
                              '    public String post() { return ""; }\n}\n'),
                symbols.parse("src/a/AuditTrail.java",
                              'package a;\nimport a.LedgerRepository;\npublic class AuditTrail {\n'
                              '    public void log() { }\n}\n')]
        # The controller that calls the service was the only neighbour the old one-way hop could find;
        # the repository the service calls is the file a fix usually needs, and it is two hops away
        # from the controller, so the walk stops at one hop in each direction.
        self.assertEqual([[row["path"].rsplit("/", 1)[-1], row["why"], row["score"], row["symbol"]]
                          for row in symbols.rank(rows, "fix OrderService", limit=5)],
                         [["OrderService.java", "declares", 8, "OrderService"],
                          ["LedgerRepository.java", "used_by", 5, "OrderService.java"],
                          ["OrderController.java", "imports", 5, "OrderService"]])

    def test_the_new_reason_is_spoken_in_both_languages(self):
        entry = {"kind": "context_file", "path": "src/a/LedgerRepository.java", "why": "used_by",
                 "symbol": "OrderService.java"}
        self.assertIn("(used by OrderService.java)", labels.log_line(False, entry))
        self.assertIn("(يستخدمه OrderService.java)", labels.log_line(True, entry))


if __name__ == "__main__":
    unittest.main()
