"""The requests that wait their turn: add, edit, reorder, hold, release and drain."""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # so `doubles` is importable either way
from ai_code_engineer.webapp.controller import AgentController
from doubles import PROOF, run_result
from controller_case import ControllerCase, GatingModel, Scripted


class QueueTests(ControllerCase):
    """A prompt that arrives mid-flight waits its turn: the order, the chat it belongs to, the stop
    that holds the queue, and the drain that takes a row exactly once."""

    model_factory = GatingModel
    extra_patches = (lambda: patch("ai_code_engineer.runner.run",
                                   return_value=run_result(proof=PROOF)),)

    def setUp(self):
        super().setUp()
        self.events = []

    def emit(self, event):
        self.events.append(event)

    def action(self, type, **payload):
        self.controller.action(type, payload, self.emit)

    def running(self):
        """Block until the controller says it is busy, so the queue is filled mid-flight."""
        self.model.gate.clear()
        self.controller.start_plan("Fix add in calculator.py")
        for _ in range(400):
            if self.controller.busy and self.controller.session is None and self.model.prompts:
                return
            threading.Event().wait(0.02)
        self.fail("the first task never got as far as the model")

    def finish(self):
        self.model.gate.set()
        self.controller.join()

    def queued(self):
        return [item["text"] for item in self.controller.snapshot()["queue"]["items"]]

    def asked(self):
        """Every message the model was actually sent, in order.

        Assertions go through this and through the thread rather than through a count of model
        calls: the planning loop can ask more than once per task, so a count measures the loop and
        not the queue.
        """
        return [json.dumps(prompt, ensure_ascii=False) for prompt in self.model.prompts]

    def thread(self):
        return [message["text"] for message in self.controller.snapshot()["messages"]
                if message["role"] == "user"]

    # -------------------------------- the queue itself --------------------------------
    def test_sending_midflight_queues_the_message_instead_of_dropping_it(self):
        self.running()
        self.action("queue_add", text="And add a test for the duplicate case")
        self.assertEqual(self.queued(), ["And add a test for the duplicate case"])
        self.assertTrue(self.controller.busy, "queueing must not disturb the running task")

    def test_the_queued_message_starts_by_itself_when_the_running_task_ends(self):
        self.running()
        self.action("queue_add", text="And add a test")
        self.finish()
        self.assertEqual(self.queued(), [], "it left the queue")
        self.assertEqual(self.thread()[-1], "And add a test", "the queue never ran it")
        self.assertIn("And add a test", self.asked()[-1])
        self.assertEqual(self.controller.session["task"], "And add a test")

    def test_two_queued_messages_run_in_the_order_they_were_typed(self):
        self.running()
        self.action("queue_add", text="First asked")
        self.action("queue_add", text="Second asked")
        self.assertEqual(self.queued(), ["First asked", "Second asked"])
        self.finish()
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.thread()[-2:], ["First asked", "Second asked"],
                         "FIFO is the whole contract of a queue")

    def test_a_queued_message_belongs_to_the_chat_it_was_typed_in(self):
        self.running()
        self.action("queue_add", text="Only makes sense here")
        chat = self.controller.chat_id
        self.finish()
        self.assertEqual(self.controller.session["task"], "Only makes sense here")
        self.assertEqual(self.controller.chat_id, chat, "it ran in its own conversation")

    def test_a_queued_message_does_not_run_in_someone_elses_chat(self):
        """The item belongs to the conversation that typed it, and `elsewhere` is how the strip
        admits it exists without drawing a line that would fire in the wrong thread."""
        self.running()
        self.action("queue_add", text="Wait for me")
        chat = self.controller.chat_id
        self.controller.cancellable = True
        self.controller.stop()                       # a stop holds the queue
        self.model.gate.set()
        self.controller.join()
        self.controller.new_chat()
        state = self.controller.snapshot()["queue"]
        self.assertEqual(state["items"], [], "this chat has nothing of its own waiting")
        self.assertEqual(state["elsewhere"], 1, "and the strip says so rather than hiding it")
        self.assertNotIn("Wait for me", self.thread(), "it must not run in a chat that never asked")
        key = next(iter(self.controller.projects))
        self.controller._select_branch("project", key, chat_id=chat)
        self.assertEqual(self.controller.chat_id, chat)
        self.assertEqual(self.queued(), ["Wait for me"], "back home, its line is there again")
        self.action("queue_resume")
        self.controller.join()
        self.assertEqual(self.thread()[-1], "Wait for me")

    def test_stop_holds_the_queue_so_stop_means_stop(self):
        self.running()
        self.action("queue_add", text="Would have fired next")
        self.controller.cancellable = True
        self.controller.stop()
        self.model.gate.set()
        self.controller.join()
        self.assertNotIn("Would have fired next", self.thread(), "the queue ran through a stop")
        self.assertEqual(self.queued(), ["Would have fired next"])
        self.assertTrue(self.controller.snapshot()["queue"]["held"])
        self.action("queue_resume")
        self.controller.join()
        self.assertEqual(self.thread()[-1], "Would have fired next", "and ▶ lets it go again")

    # -------------------------------- the per-item actions --------------------------------
    def test_editing_changes_what_will_run(self):
        self.running()
        self.action("queue_add", text="Add a test")
        item = self.controller.queue[0]["id"]
        self.action("queue_edit", id=item, text="Add two tests, please")
        self.assertEqual(self.queued(), ["Add two tests, please"])
        self.finish()
        self.assertEqual(self.controller.session["task"], "Add two tests, please")

    def test_dropping_removes_it_without_touching_the_running_task(self):
        self.running()
        self.action("queue_add", text="Forget this one")
        self.action("queue_drop", id=self.controller.queue[0]["id"])
        self.assertEqual(self.queued(), [])
        self.assertTrue(self.controller.busy)
        self.finish()
        self.assertNotIn("Forget this one", self.thread())

    def test_run_next_moves_the_item_to_the_front(self):
        self.running()
        self.action("queue_add", text="Asked first")
        self.action("queue_add", text="Asked second")
        self.action("queue_now", id=self.controller.queue[-1]["id"])
        self.assertEqual(self.queued(), ["Asked second", "Asked first"])
        self.finish()
        self.assertEqual(self.thread()[-2:], ["Asked second", "Asked first"],
                         "the item moved to the front, so the other one runs last")

    def test_a_message_queued_after_the_task_already_ended_runs_at_once(self):
        """The click can land a moment too late; a line that says "Queued" and never fires is a lie."""
        self.action("queue_add", text="Nothing is running, so this just sends")
        self.controller.join()
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.controller.session["task"], "Nothing is running, so this just sends")

    def test_opening_in_a_separate_chat_moves_it_out_of_this_conversation(self):
        self.running()
        chat = self.controller.chat_id
        self.action("queue_add", text="This deserves its own thread")
        self.action("queue_chat", id=self.controller.queue[0]["id"])
        self.assertTrue(self.controller.queue[0]["detached"],
                        "mid-task it waits for the branch to be movable, and never runs here")
        self.model.gate.set()
        self.controller.join()
        self.assertNotEqual(self.controller.chat_id, chat, "it asked in a chat of its own")
        self.assertEqual(self.controller.repo, str(self.repo),
                         "and a new chat in this project keeps the folder it was written for")
        self.assertIn("This deserves its own thread", self.asked()[-1])
        self.assertEqual(self.thread()[-1], "This deserves its own thread",
                         "it appears in the thread of the chat it was moved to")
        self.assertEqual(self.queued(), [])

    def test_the_queue_comes_back_held_and_starts_nothing(self):
        """The old rule was "in memory only", because a queued change request can be pointed at
        files that moved on since it was typed. Losing a twelve-message batch to one restart is the
        other half of the same problem, so the queue is stored and what holds the invariant is the
        hold itself: nothing drains until the operator presses ▶."""
        self.running()
        self.action("queue_add", text="Do not start this on its own")
        again = Scripted(self.app_dir)              # the window restarts mid-batch
        self.addCleanup(again.close)
        self.model.gate.set()
        self.controller.join()
        self.assertEqual([item["text"] for item in again.queue], ["Do not start this on its own"])
        self.assertTrue(all(item.get("restored") for item in again.queue),
                        "a row typed in another session has to say so")
        again.join(timeout=5)
        self.assertIsNone(again.session, "held means no worker took it")
        self.assertTrue(again.snapshot()["queue"]["held"])
        again.queue_resume()
        self.assertFalse(any(item.get("restored") for item in again.queue),
                         "the press is the approval, and it retires the note")

    def test_a_queued_batch_says_what_it_landed_when_it_ends(self):
        self.controller.set_auto_apply(True)
        self.running()
        self.action("queue_add", text="Create exactly two new files. One of them will not appear.")
        self.finish()
        texts = [message["text"] for message in self.controller.snapshot()["messages"]
                 if message["role"] == "tool"]
        summary = [text for text in texts if "Batch finished" in text]
        self.assertEqual(len(summary), 1, "one row per batch, not one per task: " + repr(texts[-3:]))
        self.assertIn("1 task(s)", summary[0])

    def test_two_drains_in_the_same_tick_take_the_row_once(self):
        """A finished job is not the only thread that reaches for a queued row: two jobs can end in one
        tick, and until the claim was made under the same lock as `busy` both of them saw an idle
        window. The one that lost then re-queued the text the winner had already taken out of the
        queue, so the operator's single message ran twice and the batch reported two tasks for it —
        seen 7 times in 60 runs of the test above under load, and 8 in 60 at this commit's parent.

        This reproduces the tick instead of waiting for it: the second drain is called while the first
        is still choosing, which is the exact moment the two used to disagree about.
        """
        queued = "Add a helper that reads the config file"
        self.controller.set_auto_apply(True)
        self.running()
        self.action("queue_add", text=queued)
        real = self.controller.start_plan
        reached, while_it_chose = [], []

        def watching(text, quote_of=None):
            reached.append(text)
            if text == queued and not while_it_chose:
                while_it_chose.append("second drain")
                second = threading.Thread(target=self.controller._drain_queue)
                second.start()
                second.join(timeout=10)
                # Read inside the first drain's own decision: a row the second one took would be gone,
                # and a row it re-queued would be a second copy of the same text.
                while_it_chose.append(len(self.controller.queue))
            return real(text, quote_of)

        self.controller.start_plan = watching
        self.finish()
        self.assertEqual(reached.count(queued), 1,
                         "one reach for the row, not one per drain: " + repr(reached))
        self.assertEqual(while_it_chose[1:], [1], "the row was still queued when the second drain "
                         "looked — it took nothing, and re-queued nothing")
        self.assertEqual(self.queued(), [])
        summary = next(text for text in (message["text"] for message in
                                         self.controller.snapshot()["messages"]
                                         if message["role"] == "tool") if "Batch finished" in text)
        self.assertIn("1 task(s)", summary)

    def test_the_batch_row_flags_the_count_the_task_asked_for(self):
        """D36: "Create exactly two new files" answered with one. The flag reads only the literal
        scaffold phrase, so a task that never named a count is not second-guessed."""
        self.controller.set_auto_apply(True)
        self.running()
        self.action("queue_add", text="Create exactly three new files")
        self.finish()
        summary = next(text for text in (message["text"] for message in
                                         self.controller.snapshot()["messages"]
                                         if message["role"] == "tool") if "Batch finished" in text)
        self.assertIn("asked for a file count it did not deliver", summary)

    def test_a_task_that_named_no_count_is_not_judged_for_one(self):
        self.controller.set_auto_apply(True)
        self.running()
        self.action("queue_add", text="Add a helper that reads the config file")
        self.finish()
        summary = next(text for text in (message["text"] for message in
                                         self.controller.snapshot()["messages"]
                                         if message["role"] == "tool") if "Batch finished" in text)
        self.assertNotIn("did not deliver", summary)

    def test_a_message_too_long_is_refused_where_it_stands(self):
        self.running()
        before = len(self.controller.snapshot()["messages"])
        self.action("queue_add", text="x" * 4001)
        self.assertEqual(self.queued(), [])
        messages = self.controller.snapshot()["messages"]
        self.assertEqual(len(messages), before + 1, "the refusal has to be visible, not a silent no")
        self.assertIn("4,000", messages[-1]["text"])
        self.model.gate.set()
        self.controller.join()

    def test_the_same_message_twice_is_one_line(self):
        self.running()
        self.action("queue_add", text="Did that run yet?")
        self.action("queue_add", text="Did that run yet?")
        self.assertEqual(self.queued(), ["Did that run yet?"])
        self.model.gate.set()
        self.controller.join()

    def test_the_snapshot_describes_the_strip(self):
        payload = self.controller.snapshot()["queue"]
        self.assertEqual(sorted(payload), ["chat", "elsewhere", "elsewhere_note", "held", "held_note",
                                           "items", "kind", "when", "when_detached", "when_restored"],
                         "the strip needs identity, state and the server's own sentences")
        self.assertEqual(payload["items"], [])
        self.assertFalse(payload["held"])
        self.action("queue_add", text="Idle add runs at once")
        self.controller.join()
        self.assertIn("when", self.controller.snapshot()["queue"])
class SecondSendBecomesAQueueItem(unittest.TestCase):
    """Two clicks in one tick used to cost the second message entirely.

    Measured in the ecommerce run: three prompts were sent back to back, one started, and the other two
    reached `start_plan` while the client's `busy` flag had not yet arrived — `run_job` answered with a
    silent `return`, so two written tasks vanished without a status line or a trace.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.controller = AgentController(Path(self.temp.name).resolve())
        self.addCleanup(self.controller.close)
        self.events = []
        self.controller._emit = self.events.append

    def test_the_job_itself_hands_a_late_message_to_the_queue(self):
        """The race is at the claim, not at the check: `busy` is read by two HTTP threads before either
        job has set it, so the guard that actually drops the work is the one inside `run_job`."""
        landed = []
        self.controller.busy = True
        before = self.controller.status
        self.controller.run_job(lambda: "never runs", lambda result: None, "Working…",
                                on_busy=lambda: landed.append("queued"))
        self.assertEqual(landed, ["queued"])
        self.assertEqual(self.controller.status, before,
                         "a refused job must not leave its running line on the strip")

    def test_both_ways_a_message_arrives_are_wired_to_it(self):
        """A plan and a chat reply are the two entries; either may be the one that arrives late."""
        import inspect
        from ai_code_engineer.webapp import controller
        source = Path(inspect.getfile(controller)).read_text(encoding="utf-8")
        self.assertEqual(source.count("on_busy=lambda: self.queue_add("), 2,
                         "start_plan and start_chat both hand their text to the queue")

    def test_a_refused_start_does_not_spend_the_row(self):
        """The drain used to pop before starting, so any of `start_plan`'s refusals ate the message."""
        self.controller.model = ""                      # a refusal the queue can do nothing about
        self.controller.queue.append({"id": "x1", "text": "Create exactly one new file: A.java",
                                      "chat": self.controller.chat_id})
        self.controller._drain_queue()
        self.assertEqual([item["text"] for item in self.controller.queue],
                         ["Create exactly one new file: A.java"])

    def test_a_start_that_takes_the_row_is_the_one_that_removes_it(self):
        taken = []

        def accept(text):
            taken.append(text)
            self.controller._job_started = True         # what `run_job` records on the real path

        self.controller.start_plan = accept
        self.controller.queue.append({"id": "x1", "text": "Create exactly one new file: A.java",
                                      "chat": self.controller.chat_id})
        self.controller._drain_queue()
        self.assertEqual(taken, ["Create exactly one new file: A.java"])
        self.assertEqual(self.controller.queue, [])

    def test_a_message_sent_during_a_running_task_joins_the_queue(self):
        self.controller.busy = True
        self.controller.start_plan("Create exactly one new file: A.java")
        self.assertEqual([item["text"] for item in self.controller.queue],
                         ["Create exactly one new file: A.java"])
        self.assertIn("toast", [event.get("kind") for event in self.events],
                      "the window has to be told the message was taken, not left guessing")

    def test_the_strip_names_an_unanswered_question_as_the_reason_for_waiting(self):
        """Two rows sat in the strip for half an hour with busy=False and held=False, beside a sentence
        promising they ran when the task ended. They were waiting on a fix-round dialog — and a question
        that can hold the queue for the whole timeout is not "the current task". """
        self.controller.queue.append({"id": "x1", "text": "Create exactly one new file: A.java",
                                      "chat": self.controller.chat_id})
        self.assertIn("current task ends", self.controller._queue_view()["when"])
        self.controller._replies["q1"] = threading.Event()
        self.assertIn("answer the question", self.controller._queue_view()["when"],
                      "the strip has to say what the rows are actually behind")
        self.controller._replies.pop("q1")
        self.assertIn("current task ends", self.controller._queue_view()["when"])

    def test_the_queued_messages_do_not_start_while_the_task_runs(self):
        self.controller.busy = True
        self.controller.start_plan("Create exactly one new file: A.java")
        self.controller.start_plan("Create exactly one new file: B.java")
        self.assertEqual(len(self.controller.queue), 2, "the queue holds both, in order")
        self.assertEqual([item["text"] for item in self.controller.queue],
                         ["Create exactly one new file: A.java",
                          "Create exactly one new file: B.java"])


if __name__ == "__main__":
    unittest.main()
