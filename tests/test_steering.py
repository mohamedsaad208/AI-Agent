"""Mid-run Steering (T2.3): what the operator says to a run that is already going.

A `SteeringInbox` is the one place a view hands the loop a new instruction without starting a second
run. These tests drive real `plan()` loops with a provider that remembers every prompt it was asked
with, because the promise being tested is narrow and easy to break by accident: an instruction
submitted between two turns is read by the turn that follows, the work already done is kept, the
instruction survives a trimmed history, and none of it moves the number a person types to approve.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_code_engineer import events as ux
from ai_code_engineer import taskstate
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import (MAX_STEER_CHARS, SteeringInbox, load_session, plan,
                                     plan_mode_view, proposal_hash)
from ai_code_engineer.errors import Cancelled, PolicyError
from ai_code_engineer.workspace import Workspace

APP = "answer = 1\n"
APP_V2 = "answer = 2\n"
READ_APP = {"action": "read_file", "path": "app.py"}
STEER = "Do not touch the build file."


def proposal(content: str = APP_V2) -> dict:
    return {"action": "propose", "summary": "Change the answer", "checks": ["unit tests"],
            "changes": [{"path": "app.py", "content": content}]}


class AskingProvider:
    """A scripted model that remembers the prompts it was given, and can be spoken to mid-ask.

    `on_ask` runs before each answer, with the ask number and the cancel predicate the loop handed
    over. That hook is how a test stands in for a person typing into a window while a slow model is
    still writing: the submission happens inside the run, not around it.
    """

    model = "test"

    def __init__(self, responses, on_ask=None):
        self.responses = list(responses)
        self.on_ask = on_ask
        self.prompts: list[list[dict]] = []

    def generate(self, messages, cancelled=None):
        self.prompts.append([dict(row) for row in messages])
        if self.on_ask is not None:
            self.on_ask(len(self.prompts), cancelled)
        value = self.responses[len(self.prompts) - 1]
        return value if isinstance(value, str) else json.dumps(value)

    def texts(self, ask_number: int) -> str:
        """Everything the ask numbered `ask_number` (1-based) was shown, as one haystack."""
        return "\n".join(str(row.get("content", "")) for row in self.prompts[ask_number - 1])


class SteeringCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text(APP, encoding="utf-8")
        self.ws = Workspace(self.root)
        self.runs = self.base / "runs"
        self.bus = ux.EventBus()
        self.lines: list[str] = []

    def run_plan(self, provider, task="Update the answer", inbox=None, **kwargs):
        kwargs.setdefault("progress", self.lines.append)
        path = plan(self.ws, task, provider, Settings(), self.runs, event_bus=self.bus,
                    steering=inbox, **kwargs)
        return path, load_session(path)

    def speak_during(self, inbox, *instructions, at_ask=1, urgent=False):
        def hook(number, _cancelled):
            if number == at_ask:
                for text in instructions:
                    inbox.submit(text, urgent=urgent)
        return hook

    def events_of(self, session, kind):
        return [row for row in session["events"] if row.get("kind") == kind]

    def streamed(self, kind):
        return [row for row in self.bus.history if getattr(row, "kind", "") == kind]

    def turn_lines(self):
        return [line for line in self.lines if line.startswith("Turn ")]


class ReadBetweenTurns(SteeringCase):
    def test_the_instruction_reaches_the_next_ask(self):
        inbox = SteeringInbox()
        provider = AskingProvider([READ_APP, proposal()], on_ask=self.speak_during(inbox, STEER))
        self.run_plan(provider, inbox=inbox)
        self.assertEqual(len(provider.prompts), 2)
        self.assertNotIn(STEER, provider.texts(1))
        self.assertIn(STEER, provider.texts(2))

    def test_the_run_continues_where_it_stood(self):
        # The failure this guards against is a restart dressed up as steering: a fresh session, a lost
        # read, and a turn counter back at one.
        inbox = SteeringInbox()
        provider = AskingProvider([READ_APP, proposal()], on_ask=self.speak_during(inbox, STEER))
        _path, session = self.run_plan(provider, inbox=inbox)
        self.assertIn("Tool observation (untrusted)", provider.texts(2))
        self.assertEqual(self.turn_lines(), ["Turn 1/12: asking test...", "Turn 2/12: asking test..."])
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertEqual(len(list(self.runs.glob("*/session.json"))), 1,
                         "steering opened a second run instead of continuing this one")

    def test_two_instructions_in_one_gap_are_both_read_in_order(self):
        inbox = SteeringInbox()
        provider = AskingProvider([READ_APP, proposal()], on_ask=self.speak_during(
            inbox, STEER, "Keep the function name."))
        _path, session = self.run_plan(provider, inbox=inbox)
        self.assertEqual([row["text"] for row in session["steering"]],
                         [STEER, "Keep the function name."])
        self.assertLess(provider.texts(2).index(STEER),
                        provider.texts(2).index("Keep the function name."))

    def test_an_instruction_is_said_back_to_the_operator(self):
        inbox = SteeringInbox()
        provider = AskingProvider([READ_APP, proposal()], on_ask=self.speak_during(inbox, STEER))
        self.run_plan(provider, inbox=inbox)
        line = [row for row in self.lines if "Steering applied" in row]
        self.assertEqual(len(line), 1)
        self.assertIn(STEER, line[0])

    def test_the_record_says_who_said_what_and_when(self):
        inbox = SteeringInbox()
        provider = AskingProvider([READ_APP, proposal()], on_ask=self.speak_during(inbox, STEER))
        _path, session = self.run_plan(provider, inbox=inbox)
        row = session["steering"][0]
        self.assertEqual(row["text"], STEER)
        self.assertEqual(row["turn"], 2)
        self.assertFalse(row["urgent"])
        self.assertTrue(row["id"] and row["at"] and row["read"])
        self.assertEqual([(e["text"], e["turn"]) for e in self.events_of(session, "steer")],
                         [(STEER, 2)])
        announced = [e for e in self.events_of(session, "step") if e.get("action") == "steer"]
        self.assertEqual(len(announced), 1)
        self.assertEqual(announced[0]["detail"], STEER)

    def test_the_instruction_is_a_binding_constraint_of_the_run(self):
        # Constraints are the one class `taskstate` never evicts, which is what keeps an instruction
        # alive after the turn that carried it is trimmed for budget.
        inbox = SteeringInbox()
        provider = AskingProvider([READ_APP, proposal()], on_ask=self.speak_during(inbox, STEER))
        _path, session = self.run_plan(provider, inbox=inbox)
        self.assertIn(STEER, session["task_state"]["constraints"])
        asked_with = provider.texts(2)
        self.assertIn("user_constraints (binding, never drop or weaken)", asked_with)

    def test_a_trimmed_turn_still_says_the_instruction(self):
        # The turn that carried the instruction is droppable; the constraint is not. Folding every turn
        # the run kept, exactly as the budget trim does, is how the loop goes on obeying a steer it can
        # no longer quote from the conversation.
        inbox = SteeringInbox()
        provider = AskingProvider([READ_APP, proposal()], on_ask=self.speak_during(inbox, STEER))
        _path, session = self.run_plan(provider, inbox=inbox)
        state = session["task_state"]
        for message in provider.prompts[-1][2:]:
            taskstate.fold_turn(state, message)
        block = taskstate.block(state, 4000)
        self.assertIn("user_constraints (binding, never drop or weaken)", block)
        self.assertIn(STEER, block)

    def test_the_view_sees_a_steer_row_and_a_step_row_at_normal_level(self):
        inbox = SteeringInbox()
        provider = AskingProvider([READ_APP, proposal()], on_ask=self.speak_during(inbox, STEER))
        self.run_plan(provider, inbox=inbox)
        steer = self.streamed("steer")
        self.assertEqual(len(steer), 1)
        self.assertTrue(steer[0].known)
        self.assertEqual(steer[0].level, ux.EventLevel.NORMAL.value)
        self.assertEqual(steer[0].data["text"], STEER)
        stepped = [row for row in self.bus.history
                   if isinstance(row, ux.StepUpdated) and row.action == "steer"]
        self.assertEqual(len(stepped), 1)

    def test_steering_does_not_move_the_proposal_hash(self):
        # The hash is what a person types to approve; an instruction that changed the remaining turns
        # must not quietly invalidate the number already on screen.
        inbox = SteeringInbox()
        provider = AskingProvider([READ_APP, proposal()], on_ask=self.speak_during(inbox, STEER))
        _path, session = self.run_plan(provider, inbox=inbox)
        bare = {key: value for key, value in session.items() if key != "steering"}
        self.assertEqual(proposal_hash(session), proposal_hash(bare))


class UrgentInterrupt(SteeringCase):
    def test_an_urgent_instruction_cuts_the_ask_short(self):
        inbox = SteeringInbox()
        asks = {"count": 0}
        seen: list[str] = []

        class SlowModel:
            model = "slow"

            def generate(self, messages, cancelled=None):
                asks["count"] += 1
                seen.append("\n".join(str(row.get("content", "")) for row in messages))
                if asks["count"] == 1:
                    # The instruction is typed while this ask is still running, and the ask is long:
                    # a slow model must not be the reason the instruction waits.
                    def type_it():
                        time.sleep(0.02)
                        inbox.submit("Stop gathering and propose now.", urgent=True)
                    threading.Thread(target=type_it, daemon=True).start()
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if cancelled is not None and cancelled():
                            raise Cancelled("Operation cancelled.")
                        time.sleep(0.005)
                    self.fail("the loop never cut the slow ask short")
                if asks["count"] == 2:
                    return json.dumps(READ_APP)
                return json.dumps(proposal())

        _path, session = self.run_plan(SlowModel(), inbox=inbox)
        # Three asks, not four: the cut one is not re-run, and the ask that follows is already steered.
        self.assertEqual(asks["count"], 3)
        self.assertNotIn("Stop gathering and propose now.", seen[0])
        self.assertIn("Stop gathering and propose now.", seen[1])
        # Read on the turn whose ask was cut, not on the turn that follows it: the point of `urgent` is
        # that the instruction is obeyed one ask sooner than a turn-boundary checkpoint could manage.
        self.assertEqual(session["steering"][0]["turn"], 1)
        self.assertTrue(session["steering"][0]["urgent"])
        self.assertEqual(session["state"], "WAITING_APPROVAL")

    def test_a_stop_outranks_an_instruction_waiting(self):
        # The interrupt is the loop's own reason to cut an ask; a person's Stop ends the run, and the
        # instruction they had not read yet stays in the box instead of being swallowed by the stop.
        inbox = SteeringInbox()
        inbox.submit("Any way you can hurry?", urgent=True)
        provider = AskingProvider([READ_APP, proposal()])
        with self.assertRaises(Cancelled):
            self.run_plan(provider, inbox=inbox, cancelled=lambda: True)
        self.assertEqual([row["text"] for row in inbox.waiting()], ["Any way you can hurry?"])

    def test_a_cancelled_ask_with_nothing_to_read_still_ends_the_run(self):
        class CutModel:
            model = "cut"

            def generate(self, messages, cancelled=None):
                raise Cancelled("Operation cancelled.")

        with self.assertRaises(Cancelled):
            self.run_plan(CutModel(), inbox=SteeringInbox())


class QueueContract(unittest.TestCase):
    def setUp(self):
        self.inbox = SteeringInbox()

    def test_a_blank_instruction_is_refused(self):
        for blank in ("", "   ", "\n\t", None):
            with self.assertRaises(PolicyError):
                self.inbox.submit(blank)

    def test_an_instruction_is_one_line_and_capped(self):
        row = self.inbox.submit("  keep\n  the   name  ")
        self.assertEqual(row["text"], "keep the name")
        long = self.inbox.submit("x" * (MAX_STEER_CHARS + 500))
        self.assertEqual(len(long["text"]), MAX_STEER_CHARS)

    def test_the_queue_is_bounded_so_a_keyboard_cannot_bury_a_run(self):
        small = SteeringInbox(limit=2)
        small.submit("one")
        small.submit("two")
        with self.assertRaises(PolicyError):
            small.submit("three")
        self.assertEqual([row["text"] for row in small.drain()], ["one", "two"])

    def test_the_loop_only_interrupts_for_an_urgent_instruction(self):
        self.inbox.submit("patient note")
        self.assertFalse(self.inbox.interrupt_requested())
        self.inbox.submit("stop now", urgent=True)
        self.assertTrue(self.inbox.interrupt_requested())
        self.inbox.drain()
        self.assertFalse(self.inbox.interrupt_requested())

    def test_applied_rows_are_kept_apart_from_waiting_ones(self):
        self.inbox.submit("read")
        self.inbox.drain()
        self.inbox.submit("unread")
        self.assertEqual([row["text"] for row in self.inbox.applied()], ["read"])
        self.assertEqual([row["text"] for row in self.inbox.waiting()], ["unread"])


class LeftOverInstructions(SteeringCase):
    def test_an_instruction_no_turn_read_walks_into_the_next_step(self):
        # A plan is executed one step per run, and a steer said during step 1 belongs to step 2 onward:
        # submitted after the last checkpoint, it is left in the box rather than dropped with the run.
        inbox = SteeringInbox()
        first = AskingProvider([READ_APP, proposal()],
                               on_ask=self.speak_during(inbox, "Name the helper in English.",
                                                        at_ask=2))
        self.run_plan(first, inbox=inbox)
        self.assertEqual(inbox.applied(), [])
        self.assertEqual([row["text"] for row in inbox.waiting()], ["Name the helper in English."])

        second = AskingProvider([READ_APP, proposal("answer = 3\n")])
        _path, session = self.run_plan(second, inbox=inbox)
        self.assertIn("Name the helper in English.", second.texts(1))
        self.assertEqual(session["steering"][0]["turn"], 1)
        self.assertEqual(inbox.waiting(), [])


class NoInbox(SteeringCase):
    def test_a_run_with_no_box_says_and_records_exactly_what_it_said_before(self):
        provider = AskingProvider([READ_APP, proposal()])
        _path, session = self.run_plan(provider)
        self.assertEqual(session["steering"], [])
        self.assertEqual(self.events_of(session, "steer"), [])
        self.assertEqual(self.streamed("steer"), [])
        self.assertFalse([row for row in self.lines if "Steering" in row])


class PlanViewShowsIt(unittest.TestCase):
    def test_the_read_only_plan_says_what_the_run_was_steered_to(self):
        session = {"task": "Update the answer", "events": [], "changes": [],
                   "task_state": {"goal": "Update the answer", "decisions": [], "acceptance": []},
                   "steering": [{"id": "a1", "at": "", "read": "", "urgent": False,
                                 "text": STEER, "turn": 2}]}
        self.assertIn("Steered at turn 2: " + STEER, plan_mode_view(session)["steps"])


if __name__ == "__main__":
    unittest.main()
