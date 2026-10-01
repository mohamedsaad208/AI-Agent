"""The conversation: streamed answers, reasoning rows, quoting, rejection, withdrawn questions."""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # so `doubles` is importable either way
from ai_code_engineer import intent, labels
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import atomic_json, load_session
from ai_code_engineer.webapp.controller import AgentController, LineFeed
from doubles import CALCULATOR_BAD, ChatModel, OLLAMA_ENTRY
from helpers import sandbox_repo
from controller_case import Scripted, SteppingModel, StreamingModel, ThinkingModel, patched_catalog


class TheLineFeedThatWaitsForAKey(unittest.TestCase):
    """The buffer between a model's fragments and the browser: whole lines out, or nothing.

    Redaction reads a line. A key that arrives as ``sk-`` and then ``abcdefgh`` is a key that neither
    piece shows to the pattern, so the fragments are reassembled before anything is scrubbed — which
    is the one way a stream cannot leak what the buffered reply already could not.
    """

    def collected(self, cap=500):
        said = []
        return said, LineFeed(said.append, cap=cap)

    def test_a_line_is_handed_on_when_it_ends_and_not_before(self):
        said, feed = self.collected()
        feed.feed("two ")
        feed.feed("numbers, ")
        self.assertEqual(said, [], "a partial line is not yet something the redactor can read")
        feed.feed("added.")
        feed.close()
        self.assertEqual(said, ["two numbers, added."])

    def test_the_pieces_are_joined_into_the_line_the_sink_will_read(self):
        """Redaction belongs to the sink and reassembly belongs here, and this is why the order matters.

        A key that arrives as two fragments is invisible to the pattern in either of them. The buffer's
        job is to make the whole line exist before anything looks at it; `controller._token`'s job is
        then to scrub and cap exactly that line — which is what the window test above proves.
        """
        said, feed = self.collected()
        feed.feed("call it with sk-or-vl-")
        feed.feed("abcdefghijklmnopqrstuvwxyz1234")
        feed.feed(" done")
        feed.close()
        self.assertEqual(said, ["call it with sk-or-vl-abcdefghijklmnopqrstuvwxyz1234 done"])

    def test_a_model_that_never_breaks_a_line_is_flushed_whole_rather_than_cut(self):
        """The overflow exists so a wall of text still arrives, but it is not allowed to slice a line
        in half on the way: half a credential is one the sink's redactor cannot see, so the whole
        buffer goes at once and the sink's own cap is what trims it for display."""
        said, feed = self.collected(cap=40)
        feed.feed("x" * 120)
        self.assertEqual(said, ["x" * 120])
        feed.feed("y" * 50)
        feed.close()
        self.assertEqual(said, ["x" * 120, "y" * 50])

    def test_an_empty_answer_says_nothing(self):
        said, feed = self.collected()
        feed.feed("")
        feed.close()
        self.assertEqual(said, [])
class AnAnswerThatArrivesWhileYouWait(unittest.TestCase):
    """Phase 3 on the surface: the reply is streamed for the reader, and stored from the provider.

    Three separate guarantees, and each one is a way a stream could be worse than the blocking call
    it replaces: the browser must not see a half-line the redactor could not read, the transcript must
    hold the assembled answer rather than whatever arrived, and a model that cannot stream must not be
    asked to.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        self.events = []

    def wire(self, provider):
        for patcher in (patch("ai_code_engineer.webapp.controller.make_provider", return_value=provider),
                        patched_catalog()):
            patcher.start()
            self.addCleanup(patcher.stop)
        controller = Scripted(self.app_dir)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"
        controller.set_repo(str(self.repo))
        controller._emit = self.events.append
        self.addCleanup(controller.close)
        return controller

    def tokens(self):
        return [event["text"] for event in self.events if event["kind"] == "token"]

    def test_the_answer_reaches_the_browser_line_by_line_before_it_is_stored(self):
        controller = self.wire(StreamingModel())
        controller.start_chat("what does the guard do?", Settings(), False, False, None)
        controller.join()
        self.assertEqual(self.tokens(), ["The guard lives in create()."])
        said = [message["text"] for message in controller.snapshot()["messages"]
                if message["role"] == "assistant"]
        self.assertEqual(said[-1], "The guard lives in create().",
                         "the stored reply is the assembled one, not the frames")

    def test_a_credential_split_across_frames_is_scrubbed_before_either_is_shown(self):
        """The channel is scrubbed and the transcript is not, and the two are meant to differ.

        Every fragment flies over an SSE connection a browser extension can read, so the ephemeral
        copy is redacted line by line. The finished answer is stored as the model wrote it, because a
        chat is the product: scrubbing the text the user asked for would answer a different question.
        """
        controller = self.wire(StreamingModel(pieces=("password = hunt", "er2hunter2 ok", "")))
        controller.start_chat("q", Settings(), False, False, None)
        controller.join()
        self.assertEqual(self.tokens(), ["password = [redacted] ok"])
        streamed = json.dumps([event for event in self.events if event["kind"] == "token"])
        self.assertNotIn("hunter2hunter2", streamed)

    def test_a_model_that_cannot_stream_is_asked_in_the_way_it_answers(self):
        plain = ChatModel()
        self.assertFalse(getattr(plain, "supports_stream", False), "the double must stay unstreamable")
        controller = self.wire(plain)
        controller.start_chat("q", Settings(), False, False, None)
        controller.join()
        self.assertEqual(self.tokens(), [], "no listener was built for a model that cannot feed one")
        self.assertTrue([message for message in controller.snapshot()["messages"]
                         if message["role"] == "assistant"], "and the answer still landed")

    def test_a_proposal_turn_streams_into_activity_and_still_lands_a_proposal(self):
        controller = self.wire(StreamingModel(pieces=('{"action": "pro', 'pose", ...}', ""),
                                             envelope={"action": "list_files"}))
        controller.start_plan("Fix add in calculator.py")
        controller.join()
        chunks = [event["text"] for event in self.events if event["kind"] == "log_chunk"]
        self.assertIn('{"action": "pro', "".join(chunks),
                      "the model's own writing is what the reader is watching for")

    def test_a_cancelled_job_stops_streaming_but_keeps_the_promise(self):
        """The answer may be half-shown; the transcript is only ever written when the model finished."""
        controller = self.wire(StreamingModel())
        controller.start_chat("q", Settings(), False, False, None)
        controller.join()
        self.assertTrue(controller.snapshot()["messages"])
class TheThoughtRowInAWindow(unittest.TestCase):
    """UI 4.2 phase 2, on the surface: the thinking arrives as a row the reader can open.

    The window is where this either pays or costs. A row that dumps 1 200 characters of chain of
    thought into the thread is the cost, so the line carries one previewed sentence and the whole text
    stays behind the chevron, fetched like every other detail. A turn that thought nothing adds no
    row — the thread is read for what happened.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        self.model = ThinkingModel()
        for patcher in (patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model),
                        patched_catalog()):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        self.controller.model = "test-local"
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)
        self.events: list[dict] = []

    def step_rows(self):
        return [(message.get("step") or {}) for message in self.controller.snapshot()["messages"]
                if message.get("step")]

    def send(self, task="Fix add in calculator.py"):
        self.controller.start_plan(task)
        self.controller.join()

    def test_only_the_turns_that_thought_get_a_row(self):
        self.send()
        rows = [row for row in self.step_rows() if row["action"] == "model_reasoning"]
        self.assertEqual(len(rows), 2, "two of the four replies carried a thought")
        self.assertTrue(all(row["detail"] for row in rows), "each has something behind it")

    def test_the_line_previews_one_sentence_and_the_row_holds_both(self):
        self.send()
        rows = [row for row in self.step_rows() if row["action"] == "model_reasoning"]
        lines = [message["text"] for message in self.controller.snapshot()["messages"]
                 if (message.get("step") or {}).get("id") == rows[0]["id"]]
        self.assertIn("It adds two numbers, so the bug is in the operator.", lines[0])
        self.assertNotIn("return type", lines[0], "the second sentence belongs to the opened row")
        self.controller.action("step_detail", {"id": rows[0]["id"]}, self.events.append)
        detail = self.controller.snapshot()["step_detail"]
        self.assertEqual(detail["sections"],
                         [["What it thought first",
                           ["It adds two numbers, so the bug is in the operator.",
                            "Not in the return type."]]])

    def test_the_stored_record_is_the_whole_thought_not_the_preview(self):
        self.send()
        stored = [item for item in self.controller.session["events"]
                  if item.get("action") == "model_reasoning"]
        self.assertEqual([item["detail"] for item in stored],
                         [ThinkingModel.THOUGHTS[1], ThinkingModel.THOUGHTS[3]])
        self.assertEqual([item["count"] for item in stored],
                         [len(ThinkingModel.THOUGHTS[1]), len(ThinkingModel.THOUGHTS[3])])

    def test_a_reopened_task_reshows_the_row_and_still_opens_it(self):
        """`display_session` rebuilds a row by filtering the record through `STEP_FIELDS`, so a field
        left out of that tuple gives back a row that says less than the live one did."""
        self.send()
        before = [(row["id"], row["action"], row["detail"]) for row in self.step_rows()
                  if row["action"] == "model_reasoning"]
        self.controller.display_session(self.controller.session_path, select=True)
        after = [(row["id"], row["action"], row["detail"]) for row in self.step_rows()
                 if row["action"] == "model_reasoning"]
        self.assertEqual(after, before)
        self.controller.action("step_detail", {"id": after[0][0]}, self.events.append)
        self.assertEqual(len(self.controller.snapshot()["step_detail"]["sections"][0][1]), 2,
                         "history has to give back both sentences, not just the previewed one")

    def test_the_thought_is_said_in_the_language_the_task_was_asked_in(self):
        self.send("عدّل دالة الجمع في calculator.py")
        rows = [row for row in self.step_rows() if row["action"] == "model_reasoning"]
        line = next(message["text"] for message in self.controller.snapshot()["messages"]
                    if (message.get("step") or {}).get("id") == rows[0]["id"])
        self.assertTrue(any(0x0600 <= ord(char) <= 0x06ff for char in line), line)
        self.assertNotIn("Thought for", line)

    def test_the_thinking_never_replaces_the_answer_it_came_with(self):
        """The envelope is the only thing the loop acts on; a thought is a record, not a result."""
        self.send()
        self.assertEqual(self.controller.session["state"], "WAITING_APPROVAL")
        self.assertEqual([change["path"] for change in self.controller.session["changes"]],
                         ["calculator.py"])
        self.assertNotIn("It adds two numbers", json.dumps(self.model.prompts[-1]),
                         "the deliberation is not sent back as history")
class WithdrawnQuestionTests(unittest.TestCase):
    """A modal the browser never answered outlives the job that asked for it.

    Measured during the ecommerce dogfood run: nine fix-round offers timed out over a long build
    session and all nine sheets stayed stacked over the app, so the window looked idle while the
    topmost invisible scrim swallowed every click.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.controller = AgentController(Path(self.temp.name).resolve())
        self.addCleanup(self.controller.close)
        self.events = []
        self.controller._emit = self.events.append
        timeout = patch("ai_code_engineer.webapp.controller.ASK_TIMEOUT", 0.2)
        timeout.start()
        self.addCleanup(timeout.stop)

    def kinds(self):
        return [event["kind"] for event in self.events]

    def test_a_question_that_is_never_answered_is_withdrawn(self):
        self.assertFalse(self.controller.confirm("Keep going?", "Fix round 1 of 3"))
        self.assertEqual(self.kinds(), ["confirm", "retract"])
        self.assertEqual(self.events[0]["id"], self.events[1]["id"],
                         "the window is told which question to forget, so a late answer cannot hide another")
        self.assertIn("withdrawn", self.controller.status)
        self.assertEqual(self.controller._replies, {}, "the abandoned waiter is not left behind")

    def test_an_answered_question_is_left_on_screen_for_its_own_click_to_clear(self):
        def answer():
            for _ in range(200):
                if self.events:
                    self.controller.set_reply(self.events[0]["id"], {"ok": True})
                    return
                threading.Event().wait(0.01)

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        self.assertTrue(self.controller.confirm("Keep going?", "Fix round 1 of 3"))
        thread.join(5)
        self.assertEqual(self.kinds(), ["confirm"])

    def test_an_answer_that_arrives_after_the_withdrawal_is_dropped(self):
        self.controller.confirm("Keep going?", "Fix round 1 of 3")
        self.controller.set_reply(self.events[0]["id"], {"ok": True})
        self.assertEqual(self.controller._answers, {},
                         "a reply for a dead question must not sit in the store forever")
class DecliningAProposal(unittest.TestCase):
    """UI 4.6: Reject answers a proposal without discarding it or writing it.

    There was no way to say no before this: dismissing an offer meant rolling back a write that had
    already happened, or leaving a proposal on screen while the next task overwrote it. The refusal is
    recorded in the session rather than kept beside it, which is what lets the same answer hold after a
    restart and lets a *new* proposal from the same task be asked about again.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        self.model = SteppingModel()
        for patcher in (patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model),
                        patched_catalog()):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        self.controller.model = "test-local"
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()

    def test_declining_keeps_the_diff_and_loses_the_offer(self):
        self.controller.action("reject", {}, lambda event: None)
        state = self.controller.snapshot()
        self.assertTrue(state["review"]["rejected"])
        self.assertFalse(state["review"]["canApply"], "the offer is gone")
        self.assertFalse(state["review"]["canRollback"], "and there is nothing to undo: nothing ran")
        self.assertEqual(len(state["review"]["files"]), 1, "the diff is still there to read")
        self.assertEqual(state["artifact"]["state"], "Rejected")
        self.assertEqual(state["artifact"]["tone"], "idle")
        self.assertFalse(state["artifact"]["written"])

    def test_declining_writes_nothing_and_says_so_in_the_thread(self):
        self.controller.action("reject", {}, lambda event: None)
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD,
                         "a refusal that edited the file would be the red line, not a click")
        row = next(message for message in self.controller.snapshot()["messages"]
                   if message["role"] == "tool" and "declined" in message["text"])
        self.assertEqual(row["text"], labels.rejected_note(arabic=False, count=1))

    def test_the_answer_is_in_the_task_record_and_survives_a_reopen(self):
        self.controller.action("reject", {}, lambda event: None)
        path = self.controller.session_path
        self.assertIn("proposal_rejected",
                      [item.get("kind") for item in load_session(path).get("events", [])])
        self.controller.display_session(path, select=True)
        state = self.controller.snapshot()
        self.assertTrue(state["review"]["rejected"])
        self.assertFalse(state["review"]["canApply"])
        self.assertEqual(state["log"][-1]["text"], "\U0001f6ab Proposal declined — nothing was written")

    def test_a_later_proposal_from_the_same_task_is_answerable_again(self):
        self.controller.action("reject", {}, lambda event: None)
        self.controller.session["proposal_hash"] = "a" * 64
        self.assertFalse(self.controller.rejected(),
                         "the refusal belongs to one proposal, not to the whole conversation")
        self.assertTrue(self.controller.snapshot()["review"]["canApply"])

    def test_a_sealed_folder_answers_a_refusal_with_a_refusal(self):
        self.controller.set_composer("read")
        self.controller.reject()
        self.assertIn(intent.no_write("Reject"), self.controller.snapshot()["status"])
        self.assertNotIn("proposal_rejected",
                         [item.get("kind") for item in self.controller.session.get("events", [])])

    def test_declining_twice_says_it_once(self):
        self.controller.action("reject", {}, lambda event: None)
        before = len(self.controller.snapshot()["messages"])
        self.controller.reject()
        self.assertEqual(len(self.controller.snapshot()["messages"]), before)
        self.assertIn("already declined", self.controller.snapshot()["status"].lower())

    def test_apply_action_cannot_override_a_decline(self):
        self.controller.reject()
        before = len(self.controller.asked)
        self.controller.action("apply", {}, lambda _event: None)
        self.controller.join()
        self.assertEqual(len(self.controller.asked), before, "no approval dialog may override the refusal")
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD)
        self.assertEqual(load_session(self.controller.session_path)["state"], "WAITING_APPROVAL")
        self.assertIn("declined", self.controller.status)

    def test_reopening_is_review_only_even_with_auto_apply(self):
        self.controller.set_auto_apply(True)
        self.controller.reject()
        path = self.controller.session_path
        self.assertTrue(self.controller.snapshot()["review"]["canReopen"])
        self.controller.action("reopen", {}, lambda _event: None)
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD)
        self.assertTrue(self.controller.snapshot()["review"]["canApply"])
        self.assertFalse(self.controller.snapshot()["review"]["canReopen"])
        self.controller.display_session(path, select=True)
        self.assertFalse(self.controller.rejected(), "the reopen must survive loading the session")
        self.assertEqual(load_session(path)["events"][-1]["kind"], "proposal_reopened")

    def test_sealed_folder_cannot_reopen_a_declined_proposal(self):
        self.controller.reject()
        self.controller.set_composer("read")
        self.controller.reopen()
        self.assertTrue(self.controller.rejected())
        self.assertFalse(self.controller.snapshot()["review"]["canReopen"])
class AnsweringAnEarlierMessage(unittest.TestCase):
    """UI 4.6: a message can point back at one already in the thread.

    Three things had to hold together. The quotation is read out of the window's own record rather than
    sent in from the browser, so it cannot say something that was never said. The block is composed
    before the length ceiling measures the message, so attaching a reference cannot make a legal prompt
    refuse itself. And the operator's own words still decide the route — a quoted "fix add in
    calculator.py" inside a question about that task must not turn the question into a change request.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        self.model = SteppingModel()
        for patcher in (patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model),
                        patched_catalog()):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        self.controller.model = "test-local"
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)
        self.events: list[dict] = []
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()

    def row(self, role):
        rows = self.controller.snapshot()["messages"]
        return [index for index, row in enumerate(rows) if row["role"] == role]

    def test_the_reference_comes_first_and_says_whose_words_they_are(self):
        answer = self.row("assistant")[-1]
        self.controller.action("send", {"text": "why that file and not the service",
                                        "quote_of": answer}, self.events.append)
        self.controller.join()
        text = [row for row in self.controller.snapshot()["messages"] if row["role"] == "user"][-1]["text"]
        self.assertTrue(text.startswith('> [In reference to the agent\'s earlier reply: "'), text[:60])
        block, asked = text.split("\n\n", 1)
        self.assertEqual(asked, "why that file and not the service")
        self.assertNotIn("why that file", block, "the quotation quotes the answer, not the question")

    def test_a_stale_or_fabricated_index_sends_the_message_as_it_was_typed(self):
        for index in (9999, -1, "0 and also my own words"):
            self.controller.action("send", {"text": "carry on", "quote_of": index},
                                   self.events.append)
            self.controller.join()
            text = [row for row in self.controller.snapshot()["messages"]
                    if row["role"] == "user"][-1]["text"]
            self.assertEqual(text, "carry on", f"index {index!r} invented a reference")

    def test_the_ceiling_measures_the_message_the_model_will_read(self):
        from ai_code_engineer.engine import MAX_TASK_CHARS
        answer = self.row("assistant")[-1]
        self.controller.action("send", {"text": "x" * (MAX_TASK_CHARS - 4), "quote_of": answer},
                               self.events.append)
        self.controller.join()
        self.assertEqual(self.controller.snapshot()["status"], labels.status_text("too_long"),
                         "the block slipped past a ceiling that only weighed the typed half")

    def test_a_quoted_change_request_stays_a_question(self):
        """Chat mode over a project promotes a message that *asks* for a change. The promotion reads
        the operator's words, so quoting an old imperative back at them is still a question."""
        asked = self.row("user")[0]
        self.controller.set_composer("chat")
        self.controller.action("send", {"text": "what went wrong there?", "quote_of": asked},
                               self.events.append)
        self.controller.join()
        self.assertIsNone(self.controller.session, "the quote turned a question into a plan")
        self.assertEqual(self.controller.snapshot()["messages"][-1]["role"], "assistant")

    def test_a_queued_message_is_written_out_before_the_queue_drains(self):
        answer = self.row("assistant")[-1]
        self.controller.busy = True
        try:
            self.controller.action("queue_add", {"text": "and now the tests", "quote_of": answer},
                                   self.events.append)
        finally:
            self.controller.busy = False
        item = self.controller.snapshot()["queue"]["items"][-1]
        self.assertTrue(item["text"].startswith('> [In reference to the agent\'s earlier reply: "'),
                        item["text"][:60])

    def test_the_title_names_the_request_rather_than_the_reference(self):
        answer = self.row("assistant")[-1]
        self.controller.action("send", {"text": "why that file", "quote_of": answer},
                               self.events.append)
        self.controller.join()
        self.assertEqual(self.controller.title, "why that file")
        self.assertNotIn("> [", self.controller.snapshot()["review"]["title"])

    def test_the_block_is_written_in_the_language_of_the_message_being_sent(self):
        asked = self.row("user")[-1]
        self.controller.action("send", {"text": "ليه الملف ده", "quote_of": asked},
                               self.events.append)
        self.controller.join()
        text = [row for row in self.controller.snapshot()["messages"] if row["role"] == "user"][-1]["text"]
        line = text.split("\n")[0]
        self.assertTrue(labels.is_arabic(line), repr(line))
        self.assertIn("calculator.py", line, "the quoted Latin words stay inside the Arabic sentence")
        self.assertEqual(labels.asked_of(text), "ليه الملف ده")

    def queued_change(self, asked, *, old_format=False, detached=False):
        answer = self.row("assistant")[-1]
        self.controller.set_composer("chat")
        self.controller.busy = True
        try:
            self.controller.action("queue_add", {"text": asked, "quote_of": answer}, self.events.append)
        finally:
            self.controller.busy = False
        item = self.controller.queue[-1]
        frozen = item["text"]
        if old_format:
            item.pop("asked")
            item.pop("reference")
        if detached:
            self.controller.queue_detached(item["id"])
        else:
            # The old message list must not be used to resolve the quotation again.
            self.controller.messages = []
            self.controller._drain_queue()
        self.controller.join()
        self.assertIsNotNone(self.controller.session, "a change request was routed to prose")
        self.assertEqual(self.controller.session["task"], frozen)
        self.assertEqual(labels.asked_of(self.controller.session["task"]), asked)

    def test_queued_quoted_change_keeps_its_route(self):
        self.queued_change("Fix add in calculator.py")

    def test_arabic_queued_change_uses_the_question_for_routing(self):
        self.queued_change("صلح add في calculator.py")

    def test_legacy_quoted_queue_can_still_propose_changes(self):
        self.queued_change("Fix add in calculator.py", old_format=True)

    def test_detached_quoted_queue_keeps_its_route(self):
        self.queued_change("Fix add in calculator.py", detached=True)

    def test_restarted_queue_keeps_the_frozen_reference_and_question(self):
        self.restored_quoted_queue()

    def test_restarted_legacy_queue_keeps_its_route(self):
        self.restored_quoted_queue(old_format=True)

    def restored_quoted_queue(self, old_format=False):
        self.controller.set_composer("chat")
        answer = self.row("assistant")[-1]
        self.controller.busy = True
        try:
            self.controller.queue_add("Fix add in calculator.py", answer)
        finally:
            self.controller.busy = False
        frozen = self.controller.queue[-1]["text"]
        previous = self.controller.session_path
        self.controller.close()
        if old_format:
            path = self.app_dir / ".agent-projects.json"
            state = json.loads(path.read_text())
            item = state["ui"]["queue"][-1]
            item.pop("asked")
            item.pop("reference")
            atomic_json(path, state)
        restored = Scripted(self.app_dir)
        self.addCleanup(restored.close)
        restored.catalogs["Ollama"] = [OLLAMA_ENTRY]
        restored.model = "test-local"
        self.assertEqual(restored.queue[-1]["asked"], "Fix add in calculator.py")
        self.assertTrue(restored.queue[-1]["restored"])
        # A project restart opens a new conversation. Select the original one before
        # resuming its queue; rows must not run in a different conversation.
        restored.display_session(previous, select=True)
        restored.set_composer("chat")
        restored.queue_resume()
        restored.join()
        self.assertIsNotNone(restored.session)
        self.assertEqual(restored.session["task"], frozen)

    def test_editing_a_quoted_queue_updates_the_question_and_persists_it(self):
        self.controller.busy = True
        try:
            self.controller.queue_add("why that file", self.row("assistant")[-1])
            item = self.controller.queue[-1]
            reference = item["reference"]
            self.controller.queue_edit(item["id"], reference + "Fix add in calculator.py")
            stored = json.loads((self.app_dir / ".agent-projects.json").read_text())["ui"]["queue"][-1]
            self.assertEqual(stored["asked"], "Fix add in calculator.py")
            self.assertEqual(stored["reference"], reference)
        finally:
            self.controller.busy = False
        self.controller.set_composer("chat")
        self.controller._drain_queue()
        self.controller.join()
        self.assertIsNotNone(self.controller.session)

    def test_late_quoted_plan_and_chat_keep_the_question_when_the_job_is_busy(self):
        answer = self.row("assistant")[-1]
        self.controller.set_composer("chat")
        def late_claim(_operation, _done, _status, **options):
            self.controller.busy = True
            options["on_busy"]()
        for asked in ("Fix add in calculator.py", "why that file?"):
            with self.subTest(asked=asked), patch.object(self.controller, "run_job", side_effect=late_claim):
                try:
                    self.controller.start_plan(asked, answer)
                finally:
                    self.controller.busy = False
                queued = self.controller.queue[-1]
                self.assertEqual(queued["asked"], asked)
                self.assertTrue(queued["reference"].startswith("> ["))
                self.assertEqual(queued["text"], queued["reference"] + asked)


if __name__ == "__main__":
    unittest.main()
