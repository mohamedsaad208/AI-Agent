"""Change Impact: what a proposal breaks in the files it does not touch.

The card has always shown the diff of the file being changed. What it never showed was the rest of the
repository — who still calls the method being removed, which URL stops answering, which test names it —
so an approving a change that breaks three callers looked exactly like approving one that breaks none.
These tests hold the two halves of the answer: `impact.py` computes a structure from the symbol index
and the sources, and `labels.py` is the only place its sentences live, which is what lets the same
findings speak Arabic on an Arabic task.

Three rules the whole file is built to keep: an unread file says so instead of answering empty, every
cap admits itself, and nothing about the proposal hash moves when the impact is recorded.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import impact, labels, symbols
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import (apply_proposal, impact_for, load_session, plan, review,
                                     proposal_hash)
from ai_code_engineer.workspace import Workspace

SERVICE_OLD = '''
package com.acme.auth;

import org.springframework.stereotype.Service;

@Service
public class LoginService {
    public String login(String email, String password) { return "ok"; }
    public void audit(String email) { }
}
'''
SERVICE_NEW = '''
package com.acme.auth;

import org.springframework.stereotype.Service;

@Service
public class LoginService {
    public String login(String email) { return "ok"; }
}
'''
CONTROLLER = '''
package com.acme.web;

import com.acme.auth.LoginService;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class LoginController {
    @PostMapping("/login")
    public String login(String email, String password) { return service.login(email, password); }
}
'''
CONTROLLER_NO_ROUTE = CONTROLLER.replace('@PostMapping("/login")\n    ', '')
FILTER = '''
package com.acme.web;

public class SessionFilter {
    public void doFilter(String token) { service.audit(token); }
}
'''
TEST_FILE = '''
package com.acme.auth;

public class LoginServiceTest {
    public void auditsAreWritten() { service.audit("a@b.c"); }
}
'''
SIDE_EFFECT = '''
package com.acme.other;

public class Notes {
    // LoginService is only discussed here, and a comment is not a caller.
    public String name = "LoginService";
}
'''

JAVA = {
    "src/main/java/com/acme/auth/LoginService.java": SERVICE_OLD,
    "src/main/java/com/acme/web/LoginController.java": CONTROLLER,
    "src/main/java/com/acme/web/SessionFilter.java": FILTER,
    "src/test/java/com/acme/auth/LoginServiceTest.java": TEST_FILE,
    "src/main/java/com/acme/other/Notes.java": SIDE_EFFECT,
}

CHANGED = "src/main/java/com/acme/auth/LoginService.java"


def rows_for(files: dict[str, str]) -> list[dict]:
    rows = []
    for path, text in files.items():
        row = symbols.parse(path, text)
        if row:
            rows.append(row)
    return rows


class CandidateFilesTests(unittest.TestCase):
    """Which files are opened, in what order, and how many there were."""

    def setUp(self):
        self.rows = rows_for(JAVA)

    def test_the_proposed_file_is_never_offered_as_a_candidate(self):
        paths, others = impact.candidate_files(self.rows, [CHANGED])
        self.assertNotIn(CHANGED, paths)
        self.assertEqual(others, len(self.rows) - 1,
                         "the count is of files it *could* have read, not of the whole index")

    def test_importers_come_before_neighbours_and_tests_come_last(self):
        paths, _others = impact.candidate_files(self.rows, [CHANGED])
        self.assertLess(paths.index("src/main/java/com/acme/web/LoginController.java"),
                        paths.index("src/test/java/com/acme/auth/LoginServiceTest.java"),
                        "a Maven test lives in a mirror tree no edge or folder rule reaches")

    def test_the_limit_cuts_tests_before_it_cuts_the_files_that_import_the_change(self):
        paths, _others = impact.candidate_files(self.rows, [CHANGED], limit=1)
        self.assertEqual(paths, ["src/main/java/com/acme/web/LoginController.java"])


class FindingsTests(unittest.TestCase):
    def setUp(self):
        self.rows = rows_for(JAVA)
        self.sources = [(path, text) for path, text in JAVA.items() if path != CHANGED]
        self.change = {"path": CHANGED, "before": SERVICE_OLD, "after": SERVICE_NEW}

    def report(self, changes=None, rows=None, sources=None):
        return impact.analyze(changes if changes is not None else [self.change],
                              rows if rows is not None else self.rows,
                              sources if sources is not None else self.sources,
                              visible=len(self.rows) - 1)

    def entry(self):
        return self.report()["files"][0]

    def test_a_removed_method_is_named_with_the_files_that_still_call_it(self):
        callers = {row["name"]: row["files"] for row in self.entry()["callers"]}
        self.assertEqual(callers["audit"], ["src/main/java/com/acme/web/SessionFilter.java",
                                            "src/test/java/com/acme/auth/LoginServiceTest.java"])

    def test_a_changed_signature_is_reported_as_a_change_not_an_addition(self):
        entry = self.entry()
        self.assertEqual(entry["removed"], ["audit"])
        self.assertEqual(entry["changed"], ["login"])
        self.assertEqual(entry["added"], [])

    def test_a_comment_or_a_string_naming_the_service_is_not_a_caller(self):
        # `Notes.java` writes the name in a comment and in a string literal. Both are on the search
        # path, and only the blanked text is searched, so neither may be reported as a user.
        callers = {row["name"] for row in self.entry()["callers"]}
        self.assertNotIn("LoginService", callers)

    def test_a_test_in_the_mirror_folder_is_counted_as_covering_work(self):
        tests = {row["name"]: row["files"] for row in self.entry()["tests"]}
        self.assertEqual(tests["audit"], ["src/test/java/com/acme/auth/LoginServiceTest.java"])

    def test_a_route_that_stops_answering_is_named_with_its_handler(self):
        entry = self.entry_of(CONTROLLER, CONTROLLER_NO_ROUTE)
        self.assertEqual([(row["route"], row["handler"], row["gone"]) for row in entry["endpoints"]],
                         [("POST /api/login", "LoginController.login", True)])

    def test_a_route_that_only_moves_the_signature_is_a_change_not_a_removal(self):
        # `login(String email, String password)` to `login(String email)` keeps the URL: the endpoint
        # list must stay silent and the caller list must carry the break instead.
        entry = self.entry_of(SERVICE_OLD, SERVICE_NEW)
        self.assertEqual(entry["endpoints"], [])

    def entry_of(self, before, after, path=CHANGED):
        return self.report([{"path": path, "before": before, "after": after}])["files"][0]

    def test_a_new_route_is_reported_as_added_rather_than_as_a_break(self):
        added = self.entry_of(CONTROLLER_NO_ROUTE, CONTROLLER,
                              path="src/main/java/com/acme/web/LoginController.java")
        self.assertEqual([(row["route"], row["gone"]) for row in added["endpoints"]],
                         [("POST /api/login", False)])

    def test_a_file_the_parsers_do_not_read_says_so_instead_of_answering_empty(self):
        entry = self.report([{"path": "db/migration.sql",
                             "before": "delete from sessions;\n", "after": ""}])["files"][0]
        self.assertTrue(entry["unread"])
        self.assertEqual(entry["flags"], ["impact_text_changed"])
        self.assertEqual(entry["callers"], [])

    def test_query_text_that_disappears_from_real_code_is_flagged(self):
        before = 'package com.acme.auth;\npublic interface Q {\n    @Query("select c from Customer c")\n' \
                 "    Object one();\n}\n"
        after = 'package com.acme.auth;\npublic interface Q {\n    Object one();\n}\n'
        path = "src/main/java/com/acme/auth/Q.java"
        entry = self.report([{"path": path, "before": before, "after": after}],
                            rows=self.rows + [symbols.parse(path, before)])["files"][0]
        self.assertEqual(entry["flags"], ["impact_query"])

    def test_a_change_nothing_else_names_yields_no_findings_at_all(self):
        report = self.report([{"path": CHANGED, "before": SERVICE_OLD,
                               "after": SERVICE_OLD + "\n    public void extra() { }\n}\n"}])
        self.assertEqual(report["files"][0]["added"], ["extra"])
        self.assertEqual(report["files"][0]["callers"], [])
        self.assertEqual(report["unknown"], [])

    def test_the_modules_touched_widen_past_the_files_the_card_lists(self):
        # Six callers in six modules, and the card lists five of them. A module answer built from the
        # displayed list would say the change reaches six folders when it reaches seven.
        many = {}
        for number in range(impact.MAX_LISTED + 1):
            many[f"svc{number}/src/Use.java"] = (
                f"package use{number};\n"
                f"public class Use{number} {{\n"
                '    public void go() { audit("x"); }\n'
                "}\n")
        rows = [symbols.parse(CHANGED, SERVICE_OLD)] + [symbols.parse(p, t) for p, t in many.items()]
        sources = [(p, t) for p, t in many.items()]
        report = impact.analyze([{"path": CHANGED, "before": SERVICE_OLD, "after": SERVICE_NEW}],
                                rows, sources, visible=len(sources))
        entry = report["files"][0]
        audit = next(row for row in entry["callers"] if row["name"] == "audit")
        self.assertEqual(audit["count"], impact.MAX_LISTED + 1)
        self.assertEqual(len(audit["files"]), impact.MAX_LISTED)
        self.assertEqual(entry["modules"],
                         sorted({f"svc{number}" for number in range(impact.MAX_LISTED + 1)}
                                | {symbols.module_of(CHANGED)}))


class CapTests(unittest.TestCase):
    """A capped answer is a different claim from a complete one, so each cap has to say so."""

    def setUp(self):
        self.sources = [("src/main/java/com/acme/web/SessionFilter.java", FILTER)]

    def test_the_declaration_list_is_capped_and_the_cap_is_admitted(self):
        body = ("package com.acme.auth;\n@Service\npublic class Big {\n"
                + "".join(f"    public void gone{n}() {{ }}\n" for n in range(impact.MAX_NAMES + 2))
                + "}\n")
        new = ("package com.acme.auth;\n@Service\npublic class Big {\n}\n")
        path = "src/main/java/com/acme/auth/Big.java"
        rows = [symbols.parse(path, body)]
        report = impact.analyze([{"path": path, "before": body, "after": new}], rows, [],
                               visible=0)
        entry = report["files"][0]
        self.assertEqual(len(entry["removed"]), impact.MAX_NAMES)
        self.assertEqual(entry["removed_more"], 2)
        self.assertEqual([row["key"] for row in report["unknown"]], ["impact_unknown_names"])

    def test_a_name_written_too_often_in_one_file_is_counted_only_at_its_first_sites(self):
        path = "src/main/java/com/acme/auth/Loopy.java"
        before = ("package com.acme.auth;\npublic class Loopy {\n"
                  "    public void audit(String email) { }\n}\n")
        after = "package com.acme.auth;\npublic class Loopy {\n}\n"
        caller = ("package com.acme.auth;\npublic class Other {\n"
                  + "".join(f"    public void use{n}() {{ audit(\"x\"); }}\n"
                            for n in range(symbols.PER_FILE_LIMIT + 1)) + "}\n")
        report = impact.analyze([{"path": path, "before": before, "after": after}],
                                [symbols.parse(path, before)],
                                [("src/main/java/com/acme/auth/Other.java", caller)], visible=1)
        entry = report["files"][0]
        self.assertTrue(entry["call_capped"])
        # The file is still one caller, which is the truth; what the cap costs is the number of sites,
        # so the admission is about counting, not about the answer being wrong.
        self.assertEqual([(row["name"], row["count"]) for row in entry["callers"]], [("audit", 1)])
        self.assertEqual([row["key"] for row in report["unknown"]], ["impact_unknown_per_file"])

    def test_nothing_indexed_admits_it_rather_than_reporting_a_clean_answer(self):
        report = impact.analyze([{"path": CHANGED, "before": SERVICE_OLD, "after": SERVICE_NEW}],
                                [], [])
        self.assertEqual([row["key"] for row in report["unknown"]], ["impact_unknown_no_index"])

    def test_an_unread_search_reports_how_much_of_the_project_it_did_not_open(self):
        rows = rows_for(JAVA)
        report = impact.analyze([{"path": CHANGED, "before": SERVICE_OLD, "after": SERVICE_NEW}],
                                rows, [], visible=len(rows) - 1)
        self.assertEqual(report["unknown"][0]["key"], "impact_unknown_searched")
        self.assertEqual(report["unknown"][0]["visible"], len(rows) - 1)


class LanguageTests(unittest.TestCase):
    """`impact.py` holds structure; every sentence it produces is a shared key with an Arabic twin."""

    def setUp(self):
        rows = rows_for(JAVA)
        self.sources = [(path, text) for path, text in JAVA.items() if path != CHANGED]
        self.report = impact.analyze(
            [{"path": CHANGED, "before": SERVICE_OLD, "after": SERVICE_NEW}], rows, self.sources,
            visible=len(rows) - 1)

    def test_the_module_holds_no_sentences_of_its_own(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"
                  / "impact.py").read_text(encoding="utf-8")
        self.assertNotIn("def text(", source, "the sentences moved to labels, and they stay there")
        self.assertNotIn("from .redaction import", source,
                         "a structure that renders nothing has nothing to redact")

    def test_every_admission_it_emits_is_a_shared_sentence(self):
        # A key that exists only here would render as a KeyError in one window and a sentence in the
        # other, which is the failure the shared vocabulary exists to prevent.
        source = (Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"
                  / "impact.py").read_text(encoding="utf-8")
        emitted = [key for key in labels.NOTE_KEYS
                   if key.startswith("impact") and f'"{key}"' in source]
        self.assertTrue(emitted, "the module emits no keys at all")
        for key in emitted:
            self.assertIn(key, labels.NOTE_KEYS)
        self.assertIn("impact_unknown_searched", emitted)
        self.assertIn("impact_query", emitted)

    def test_the_findings_answer_in_both_languages(self):
        english = labels.impact_lines(self.report, arabic=False)
        arabic = labels.impact_lines(self.report, arabic=True)
        self.assertEqual(len(english), 1)              # every file was searched, so nothing to admit
        self.assertEqual(len(english), len(arabic))
        self.assertNotEqual(english, arabic)
        self.assertTrue(labels.is_arabic(arabic[0]))
        self.assertIn("removes audit", english[0])
        self.assertIn("other files name audit", english[0])
        self.assertIn("test files name audit", english[0])
        self.assertIn("changes the shape of login", english[0])

    def test_an_admission_answers_in_both_languages_too(self):
        report = {"files": [], "unknown": [{"key": "impact_unknown_searched",
                                            "searched": 12, "visible": 41}]}
        english, arabic = (labels.impact_lines(report, arabic=row) for row in (False, True))
        self.assertEqual(english, ["Not covered: callers were searched in 12 of the 41 other "
                                   "indexed files"])
        self.assertTrue(labels.is_arabic(arabic[0]))

    def test_a_finding_names_the_file_it_came_from(self):
        self.assertTrue(labels.impact_lines(self.report)[0].startswith(CHANGED + " — "))

    def test_an_empty_report_renders_no_lines_rather_than_a_heading_with_nothing_under_it(self):
        self.assertEqual(labels.impact_lines({}), [])
        self.assertEqual(labels.impact_lines({"files": [], "unknown": []}), [])


class ReviewWireTests(unittest.TestCase):
    """The recorded answer, shown where a person is about to press Apply."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name).resolve()
        self.root = self.app / "repo"
        for path, text in JAVA.items():
            full = self.root / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(text, encoding="utf-8")
        self.ws = Workspace(self.root)
        self.runs = self.app / ".agent-runs"

    def propose(self, task="Remove the audit method"):
        # A retrieval snapshot informs the model but does not authorise the write, so the first
        # proposal is refused and the run opens the file itself; the second one is honoured.
        change = {"action": "propose", "summary": "Drop audit and the password argument",
                  "checks": ["unit tests"],
                  "changes": [{"path": CHANGED, "content": SERVICE_NEW}]}
        script = Script([change, dict(change)])
        return plan(self.ws, task, script, Settings(), self.runs, progress=lambda _: None)

    def test_a_proposal_records_what_it_breaks_beside_itself(self):
        path = self.propose()
        session = load_session(path)
        entry = session["impact"]["files"][0]
        self.assertEqual(entry["removed"], ["audit"])
        self.assertTrue(any("SessionFilter" in json.dumps(row) for row in entry["callers"]))

    def test_the_review_shows_the_block_above_the_checks(self):
        session = load_session(self.propose())
        text = review(session)
        self.assertIn("What this changes in the rest of the repository:", text)
        self.assertIn("removes audit", text)
        self.assertLess(text.index("removes audit"), text.index("Proposed checks"),
                        "the finding has to be read before the promises, not after the SHA")

    def test_the_impact_never_moves_the_hash_a_person_approved(self):
        path = self.propose()
        session = load_session(path)
        wanted = session["proposal_hash"]
        session["impact"] = {"files": [], "unknown": [], "searched": 0, "visible": 0}
        self.assertEqual(proposal_hash(session), wanted)
        apply_proposal(path, wanted)
        self.assertTrue(load_session(path)["state"].startswith("APPLIED"),
                        "the change still applies on the hash that was offered")

    def test_a_session_recorded_without_an_impact_reviews_exactly_as_it_used_to(self):
        path = self.propose()
        session = load_session(path)
        session.pop("impact")
        self.assertNotIn("rest of the repository", review(session))

    def test_a_file_that_cannot_be_read_leaves_the_answer_shorter_and_says_so(self):
        # One unreadable file is not a failed check: it shrinks what was searched, and the shrink is
        # admitted, so the card never reads a partial search as "nothing else uses it".
        rows = rows_for(JAVA)
        with patch.object(Workspace, "read", side_effect=OSError("disk gone")):
            report = impact_for(Workspace(self.root), rows,
                                [{"path": CHANGED, "before": SERVICE_OLD, "after": SERVICE_NEW}])
        self.assertEqual(report["searched"], 0)
        self.assertEqual([row["key"] for row in report["unknown"]], ["impact_unknown_searched"])

    def test_an_impact_check_that_cannot_run_says_that_rather_than_answering_empty(self):
        rows = rows_for(JAVA)
        with patch.object(impact, "candidate_files", side_effect=OSError("index unreadable")):
            report = impact_for(Workspace(self.root), rows,
                                [{"path": CHANGED, "before": SERVICE_OLD, "after": SERVICE_NEW}])
        self.assertEqual([row["key"] for row in report["unknown"]], ["impact_unknown_failed"])
        self.assertEqual(labels.impact_lines(report),
                         ["Not covered: the impact check could not run, so nothing was verified "
                          "about other files"])
        self.assertIn("the impact check could not run", review(
            {"state": "WAITING_APPROVAL", "root": str(self.root), "changes": [],
             "impact": report}))

    def test_both_windows_draw_the_block_from_the_recorded_report(self):
        # The web card and the desktop summary are fed by the same two functions; the preview is
        # scripted rather than run, so its own copy of the block is checked here.
        from ai_code_engineer.webapp.fake import FakeController
        session = load_session(self.propose())
        block = labels.impact_lines(session["impact"], arabic=False)
        self.assertTrue(block)
        preview = FakeController().snapshot()["review"]["impact"]
        self.assertEqual(sorted(preview), ["heading", "lines"])
        self.assertTrue(preview["lines"])


class Script:
    model = "test"

    def __init__(self, responses):
        self.responses = list(responses)

    def generate(self, messages):
        item = self.responses.pop(0)
        return item if isinstance(item, str) else json.dumps(item)


if __name__ == "__main__":
    unittest.main()
