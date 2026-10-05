"""The twelve unified commands, answered the same way by every surface (Release 4 - Task 4.3).

One session record is what both windows and the terminal keep pointing at: the CLI opens it with
``agent <session>``, the web window holds it between jobs, and both need to answer the same twelve
questions about it — where is this run (*status*), what does it intend (*plan*), what would it
touch (*changes*, *diff*), what did the checks say (*tests*), what is dangerous about it (*risks*),
halt it (*stop*), redirect it (*steer*), close it out (*report*), put the files back (*undo*),
read what was remembered (*memory*), and see the list itself (*help*). This module is that answer,
written once: each command reads the stored session and the live host in front of it and returns
one :class:`CommandResult` carrying a plain-text reading, the rich renderables a capable terminal
should print instead, and a machine-readable ``data`` for a browser.

It draws nothing and decides nothing that the modules behind it already decided. The sections come
from ``engine.plan_mode_view``, the hunks from ``diff_view``, the closing tables from
``report_view``, the runs from ``report``, the band from ``risk_policy``; the writes go through
``engine.rollback`` with the session's own hash, and the live controls go through the host's own
``stop``/``_steering`` so a command can never claim a halt the running job did not accept.

The host is duck-typed on purpose, the way ``diff_view.to_diff`` accepts any recorded shape: it may
be a :class:`webapp.controller.Controller` (``busy``, ``cancellable``, ``stop()``, ``_steering``,
``session``, ``session_path``, ``memory_dir``, ``repo``), an :class:`Engine`-driven test double, or
nothing at all — a terminal running one command over a stored session. A command that needs a live
job says plainly when there is none; it never invents one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any, Optional

from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import compass, diff_view, labels, memory, report, report_view, session_flow
from . import memory_summarizer as brief, terminal
from .diff_parse import CONTEXT
from .engine import plan_mode_text, plan_mode_view, rollback
from .errors import AgentError
from .labels import MUTABLE_STATES, is_arabic, say, stage_line, state_label, status_text
from .planbook import progress_line
from .redaction import redact

# The one list every surface reads. The order is the order of a person's questions: find out where
# you are, then what it plans, then what it touched, then what it proved — and only then act.
COMMANDS = ("status", "plan", "changes", "diff", "tests", "risks",
            "stop", "steer", "report", "undo", "memory", "help")

# name -> (how it is typed, the one line that says what it answers)
USAGE = {
    "status": ("status", "Where this session stands: state, stage, and what is waiting."),
    "plan": ("plan", "The read-only plan — goal, affected files, steps, risks, test strategy."),
    "changes": ("changes", "One honest row per file the proposal touches, with its counts."),
    "diff": ("diff [PATH] [--context N]", "The hunks, coloured — every file, or only PATH."),
    "tests": ("tests", "Every command that ran, what it returned, and what it proved."),
    "risks": ("risks", "The risk band and every reason this proposal carries it."),
    "stop": ("stop", "Ask the job that is running to stop at its next checkpoint."),
    "steer": ("steer [--urgent] <instruction>", "Say one thing to the run that is already going."),
    "report": ("report [markdown|json]", "The closing report: verdict, files, decisions."),
    "undo": ("undo", "Restore every file this session wrote to what it held before."),
    "memory": ("memory [edit <SECTION> <text> | reset [project|chat|all] | approve | reject]",
               "The project and chat memory the compass is built from, and the pen that changes it."),
    "help": ("help", "This table."),
}

# Commands that answer only about a session. stop/steer act on a live job, and help needs nothing.
NEEDS_SESSION = {"status", "plan", "changes", "diff", "tests", "risks",
                 "report", "undo", "memory"}


@dataclass
class CommandResult:
    """One command's answer, ready for whichever surface is asking."""

    command: str
    text: str
    renderables: tuple = ()
    data: dict = field(default_factory=dict)

    def show(self, console: Optional[Console] = None) -> "CommandResult":
        """Print the renderables when there is a console to print them on, the text otherwise."""
        if self.renderables:
            out = console or Console()
            for block in self.renderables:
                out.print(block)
        else:
            from .cli_view import _ensure_utf8_stdout
            _ensure_utf8_stdout()
            (console or Console()).print(Text(redact(self.text)))
        return self


class Commands:
    """The unified command handler over one session and, when there is one, the live host.

    ``session`` and ``session_path`` describe the stored record a terminal opened; ``host`` is the
    window's controller whose ``busy`` job the ``stop`` and ``steer`` commands act on and whose
    current session is used when none was passed. Paths for the ledger and the notes are taken from
    the host when it has them, from the session file's own ``.agent-runs`` layout when it does not.
    """

    def __init__(self, session: Optional[dict] = None, *,
                 session_path: Optional[Path] = None,
                 host: Any = None,
                 plans: Optional[Path] = None,
                 memory_dir: Optional[Path] = None,
                 arabic: Optional[bool] = None) -> None:
        self.host = host
        self.session = session if session is not None else getattr(host, "session", None)
        self.session_path = session_path if session_path is not None \
            else getattr(host, "session_path", None)
        self.plans = plans if plans is not None else getattr(host, "plans", None)
        root = str((self.session or {}).get("root") or getattr(host, "repo", "") or "")
        self.root = root
        if memory_dir is not None:
            self.memory_dir = memory_dir
        elif getattr(host, "memory_dir", None):
            self.memory_dir = Path(host.memory_dir)
        elif self.session_path is not None:
            self.memory_dir = memory.memory_dir_for(Path(self.session_path).parent.parent)
        else:
            self.memory_dir = None
        self._arabic = arabic if arabic is not None else \
            is_arabic(str((self.session or {}).get("task", "")))

    @property
    def arabic(self) -> bool:
        return bool(self._arabic)

    # ------------------------------------------------------------------ dispatch

    def run(self, line: str) -> CommandResult:
        """Answer one typed line: a command name, its arguments, and nothing else."""
        line = str(line or "").strip().lstrip("/")
        name, _, rest = line.partition(" ")
        return self.execute(name, rest.strip())

    def execute(self, name: str, args: str = "") -> CommandResult:
        name = str(name or "").strip().lower().lstrip("/")
        if name not in COMMANDS:
            raise AgentError("Unknown command: " + redact(str(name)[:40]) +
                             ". The twelve are: " + ", ".join(COMMANDS) + ".")
        result = getattr(self, "_" + name)(str(args or ""))
        if not result.text and not result.renderables:
            raise AgentError("The " + name + " command had nothing to answer — this session "
                             "recorded nothing of that kind.")
        return result

    def _need_session(self, name: str) -> dict:
        if not isinstance(self.session, dict):
            raise AgentError("No session is open for `" + name + "` — start a task, or point "
                             "the command at one: agent command <session> " + name + ".")
        return self.session

    # ------------------------------------------------------------------ the twelve

    def _status(self, args: str = "") -> CommandResult:
        session = self._need_session("status")
        events = [row for row in (session.get("events") or []) if isinstance(row, dict)]
        waiting = self._waiting()
        data = {"id": session.get("id", ""), "task": redact(str(session.get("task", ""))),
                "state": session.get("state", ""), "stage": session.get("stage", ""),
                "model": session.get("model", ""), "created": session.get("created", ""),
                "files": len(session.get("changes") or []),
                "runs": len(session.get("runs") or []), "events": len(events),
                "steering_waiting": len(waiting), "busy": self._busy()}
        lines = [
            "Task:    " + (data["task"] or "(none)"),
            "State:   " + state_label(data["state"], arabic=self.arabic),
            "Stage:   " + stage_line(data["stage"], arabic=self.arabic),
            "Model:   " + (data["model"] or "(none)") + "   Session: " + data["id"],
            f"Proposal: {data['files']} file(s)   Runs: {data['runs']}   Events: {data['events']}",
        ]
        if waiting:
            lines.append(f"Steering waiting: {len(waiting)} "
                         "(read at the run's next checkpoint)")
        if events:
            lines.append("")
            lines.append("Last events:")
            for row in events[-5:]:
                detail = " ".join(f"{key}={redact(str(value))[:60]}"
                                  for key, value in row.items()
                                  if key not in ("at", "kind"))
                lines.append(f"  {row.get('at', '')}  {row.get('kind', '')}"
                             + ("  " + detail if detail else ""))
        return CommandResult("status", "\n".join(lines), data=data)

    def _plan(self, args: str = "") -> CommandResult:
        session = self._need_session("plan")
        view = plan_mode_view(session)
        text = plan_mode_text(session)
        data = {"view": {key: value for key, value in view.items() if key != "affected_files"},
                "affected_files": view["affected_files"], "proposal_hash": session.get("proposal_hash", "")}
        renderables = [Text(redact(line)) for line in text.splitlines()]
        if self.plans is not None:
            pair = session_flow.ledger_for(Path(self.plans), session)
            if pair:
                step = progress_line(pair[1])
                renderables = [Text(step, style="bold")] + renderables
                data["plan_progress"] = step
        return CommandResult("plan", text, renderables=tuple(renderables), data=data)

    def _changes(self, args: str = "") -> CommandResult:
        session = self._need_session("changes")
        data = diff_view.collect(session)
        lines = [f"{len(data.files)} file(s)   +{data.added}   -{data.removed}"]
        lines += [f"  {entry.symbol[0]} {entry.path}  +{entry.added}/-{entry.removed} "
                  f"({entry.hunks} hunk{'s' if entry.hunks != 1 else ''})"
                  + (("  " + entry.summary) if entry.summary else "")
                  for entry in data.files]
        renderables = (diff_view.totals(data), diff_view.summary_table(data))
        return CommandResult("changes", "\n".join(lines), renderables=renderables,
                             data={"files": [{"path": entry.path, "kind": entry.kind,
                                              "added": entry.added, "removed": entry.removed}
                                             for entry in data.files]})

    def _diff(self, args: str = "") -> CommandResult:
        session = self._need_session("diff")
        path, context = _parse_diff_args(args)
        data = diff_view.collect(session, context=context, path=path)
        if path and not data.files:
            return CommandResult("diff", f"Nothing in this change set touches {path}.",
                                 data={"path": path, "files": []})
        lines = [f"{len(data.files)} file(s)   +{data.added}   -{data.removed}"]
        for entry in data.files:
            lines.append(f"--- {entry.symbol[0]} {entry.path} (+{entry.added}/-{entry.removed})")
            lines.extend(entry.lines)
        renderables = tuple(diff_view.renderables(data))
        return CommandResult("diff", "\n".join(lines), renderables=renderables,
                             data={"path": path, "context": context,
                                   "files": [entry.path for entry in data.files]})

    def _tests(self, args: str = "") -> CommandResult:
        session = self._need_session("tests")
        rows = report.runs_of(session)
        verification = session.get("verification") or None
        lines = [report_view._test_summary(session)]
        for row in rows:
            head = f"{row['label'] or row['recipe'] or 'run'}: {row['status']}"
            if row["exit_code"] is not None:
                head += f", exit {row['exit_code']}"
            if row["tests"]:
                head += f" — {row['tests']} tests, {row['failures']} failed, {row['errors']} errors"
            if row["sandbox"]:
                head += f" [sandbox {row['sandbox']}]"
            lines.append(head)
            for problem in row["reported_problems"][:6]:
                lines.append("    " + problem)
        if verification:
            lines.append("Verification: " + str(verification.get("status", "")))
            for check in verification.get("static") or []:
                lines.append("    " + redact(str(check))[:200])
        elif rows:
            lines.append("Verification: nothing has been checked against the stored record yet.")
        else:
            lines.append("No command has run for this session, so nothing here is proven.")
        return CommandResult("tests", "\n".join(lines),
                             data={"runs": rows, "verification": verification})

    def _risks(self, args: str = "") -> CommandResult:
        session = self._need_session("risks")
        risks = plan_mode_view(session)["risks"]
        lines = ["  • " + line for line in risks] or ["  • nothing the policy flags"]
        renderables = (Text.assemble(("Risks", "bold"), "\n",
                                      "\n".join(redact(line) for line in lines)),)
        return CommandResult("risks", "\n".join(lines), renderables=renderables,
                             data={"risks": risks})

    def _stop(self, args: str = "") -> CommandResult:
        if not self._busy():
            return CommandResult("stop", say(self.arabic,
                         en="Nothing is running to stop.",
                         ar="لا توجد مهمة تعمل لإيقافها."), data={"stopped": False})
        if not getattr(self.host, "cancellable", True):
            return CommandResult("stop", say(self.arabic,
                         en="The step in hand cannot be interrupted; the run answers stop at "
                            "its next checkpoint.",
                         ar="الخطوة الجارية لا يمكن مقاطعتها؛ ستستجيب المهمة للإيقاف عند نقطة "
                            "التحقق التالية."), data={"stopped": False})
        stop = getattr(self.host, "stop", None)
        if callable(stop):
            stop()
        elif getattr(self.host, "cancel_event", None) is not None:
            self.host.cancel_event.set()
        else:
            raise AgentError("The running job exposes no way to stop it.")
        return CommandResult("stop", say(self.arabic,
                     en="Stop requested — the run ends at its next checkpoint.",
                     ar="تم طلب الإيقاف — ستنتهي المهمة عند نقطة التحقق التالية."),
                             data={"stopped": True})

    def _steer(self, args: str = "") -> CommandResult:
        urgent, text = _parse_steer_args(args)
        if not text:
            raise AgentError("A steering instruction needs something to say "
                             "(`steer use the existing test fixture`).")
        if not self._busy():
            return CommandResult("steer", say(self.arabic,
                         en="Nothing is running to steer right now.",
                         ar="لا توجد مهمة تعمل الآن لتوجيهها."), data={"queued": False})
        inbox = self._inbox()
        if inbox is None:
            raise AgentError("The running job exposes no steering box.")
        row = inbox.submit(text, urgent=urgent)
        return CommandResult("steer", say(self.arabic,
                     en="Steering received — the run folds it in at its next step."
                        if not urgent else
                        "Steering received — the current ask is being cut short for it.",
                     ar="تم استقبال التوجيه — ستُطبّعه المهمة عند خطوتها التالية."
                        if not urgent else
                        "تم استقبال التوجيه — سيُقاطع الطلب الجاري لتطبيقه."),
                             data={"queued": True, "instruction": row})

    def _report(self, args: str = "") -> CommandResult:
        session = self._need_session("report")
        fmt = (args.strip().lower() or "markdown")
        if fmt not in ("markdown", "json"):
            raise AgentError("The report command writes markdown or json, not " + repr(fmt) + ".")
        text = report.export(session, fmt)
        data = report.summary(session)
        if fmt == "json":
            return CommandResult("report", text, data=data)
        return CommandResult("report", text,
                             renderables=tuple(report_view.tables(session)), data=data)

    def _undo(self, args: str = "") -> CommandResult:
        session = self._need_session("undo")
        if self.session_path is None:
            raise AgentError("Undo needs the stored session file, not just a copy of the record.")
        state = str(session.get("state", ""))
        if state not in MUTABLE_STATES | {"PARTIAL_APPLY", "APPLYING"}:
            raise AgentError("Nothing to undo: this session is "
                             + state_label(state, arabic=self.arabic)
                             + " — undo puts back only files a write already applied.")
        updated = rollback(Path(self.session_path), str(session.get("proposal_hash") or ""))
        self.session = updated
        restored = [row.get("path", "") for row in updated.get("events") or []
                    if isinstance(row, dict) and row.get("kind") == "rolled_back_file"]
        lines = [status_text("rolled_back", arabic=self.arabic)]
        lines += ["  restored: " + path for path in restored] or ["  no file needed restoring."]
        return CommandResult("undo", "\n".join(lines), data={"state": updated["state"],
                                                             "restored": restored})

    def _memory(self, args: str = "") -> CommandResult:
        action, _, rest = str(args or "").strip().partition(" ")
        action = action.casefold()
        if action in ("", "show"):
            return self._memory_show()
        if action == "edit":
            return self._memory_edit(rest)
        if action == "reset":
            return self._memory_reset(rest)
        if action in ("approve", "reject"):
            return self._memory_answer(action)
        raise AgentError("`memory` answers to `memory`, `memory edit <section> <text>`, "
                         "`memory reset [project|chat|all]`, `memory approve` and "
                         "`memory reject`.")

    def _memory_show(self) -> CommandResult:
        notes = {}
        if self.memory_dir and self.root:
            notes["project"] = memory.read(Path(self.memory_dir), self.root)
            notes["auto"] = memory.auto_notes_context(Path(self.memory_dir), self.root)
        recorded = redact(str((self.session or {}).get("memory", "")))
        lines = []
        lines.append("Project notes" + (" — none written." if not notes.get("project") else ":"))
        if notes.get("project"):
            lines.append(notes["project"].strip())
        if notes.get("auto"):
            lines.append("")
            lines.append("Auto notes (what the runs remembered):")
            lines.append(notes["auto"].strip())
        if recorded:
            lines.append("")
            lines.append("Recorded with this session:")
            lines.append(recorded.strip())
        data = {"project_notes": notes.get("project", ""), "auto_notes": notes.get("auto", ""),
                "session_notes": recorded}
        answer = self._compass_readout()
        if answer:
            data["memory"] = answer
            # A project with no memory yet is not a project with an empty memory to read out: the
            # window still gets the full record so it can offer to start one, but the terminal says
            # nothing, because the line "Project memory — <path>" over a file that does not exist
            # is a path the operator did not ask for and a thing they cannot check.
            if answer["has_memory"] or answer["pending_goal"]:
                lines.append("")
                lines.extend(self._memory_lines(answer))
        if not lines:
            lines.append("Nothing is remembered for this project yet.")
        return CommandResult("memory", "\n".join(lines), data=data)

    def _compass_readout(self) -> Optional[dict]:
        if not self.root:
            return None
        return compass.readout(compass.store_for(self.root), self.chat_id)

    def _memory_lines(self, answer: dict) -> list:
        """The two layers said the way the file says them, so a terminal and a window agree."""
        lines = ["Project memory — " + answer["paths"]["project"]]
        lines.append("  goal: " + (answer["goal"] or "(none stated)"))
        if answer["goal_source"]:
            lines.append("  goal recorded by: " + answer["goal_source"])
        for section in ("constraints", "decisions", "open_issues", "progress"):
            items = answer[section]
            if items:
                lines.append("  " + section.replace("_", " ") + ":")
                lines += ["    - " + item for item in items]
        chat = answer["chat"]
        if chat["id"]:
            lines.append("")
            lines.append("Chat memory — " + chat["file"])
            lines.append("  task: " + (chat["task"] or "(none)"))
            for section in ("steps", "decisions", "open_issues"):
                if chat[section]:
                    lines.append("  " + section.replace("_", " ") + ":")
                    lines += ["    - " + item for item in chat[section]]
        lines.append("")
        lines.append(f"Compass sent to the model: {answer['tokens']} of {answer['limit']} tokens")
        if answer["compass"]:
            lines.append(answer["compass"])
        if answer["pending_goal"]:
            lines.append("")
            lines.append("A new goal is waiting for you: " + answer["pending_goal"])
            lines.append("  `memory approve` to take it, `memory reject` to keep the stored one.")
        return lines

    def _store(self):
        if not self.root:
            raise AgentError("Memory belongs to a project folder, and none is open. Start a task "
                             "in one, or point the command at a session: agent command <session> memory.")
        return compass.store_for(self.root)

    def _memory_edit(self, rest: str) -> CommandResult:
        """One line typed by the user, written into the layer that owns the section.

        `edit <section>` on a list section appends rather than replaces: a person adding a rule to
        the constraints is not asking for the four rules beside it to be retyped. The whole file is
        what `memory edit` with no section opens, for the moments a line is not enough. Which layer
        owns which section, and what stops a line from being written, is `compass.edit_section`'s
        answer — the same one the window shows, in the same words.
        """
        section, _, text = str(rest or "").strip().partition(" ")
        section = section.casefold()
        store = self._store()
        if not section:
            return self._memory_open_editor(store)
        if text.strip() == "":
            raise AgentError(labels.note("memory_needs_text", arabic=self.arabic,
                                         section=section))
        if section == "goal":
            outcome = compass.state_goal(store, text, approved=True, source="user",
                                         arabic=self.arabic)
            if not outcome.applied and outcome.reason not in ("same", "hand-edit"):
                raise AgentError(labels.note("goal_empty", arabic=self.arabic))
            return CommandResult("memory", outcome.message + "\n  goal: " + outcome.goal,
                                 data={"goal": outcome.goal, "reason": outcome.reason})
        answer = compass.edit_section(store, section, text, self.chat_id,
                                      arabic=self.arabic)
        return CommandResult("memory",
                             labels.note("memory_written", arabic=self.arabic,
                                         layer=answer["layer"], section=answer["section"],
                                         text=redact(answer["written"])),
                             data=answer)

    def _memory_open_editor(self, store) -> CommandResult:
        """The file itself, in the user's editor, with the goal digest re-based when they close it."""
        path = store.project_path()
        if not path.is_file():
            # Not a failure to paper over by creating the file: the point of opening it is that a
            # person reads what is already held, and there is nothing held yet.
            raise AgentError("This project has no memory file yet, so there is nothing to open. "
                             "Say the first line instead: `memory edit goal <what the project is "
                             "for>`. The file will then be created at " + str(path) + ".")
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ""
        words = shlex.split(editor, posix=os.name != "nt") if editor else []
        if not words or not shutil.which(words[0]):
            raise AgentError("`memory edit` with no section opens your editor, and none is set. "
                             "Say it in one line instead: `memory edit goal <what the project is "
                             "for>`, or set EDITOR and try again. The file is " + str(path) + ".")
        if not terminal.capabilities().interactive:
            raise AgentError("`memory edit` needs a terminal to open an editor in. Pipe the file "
                             "instead, or say it in one line: `memory edit goal <text>`.")
        subprocess.run(words + [str(path)], check=False)
        adopted = brief.adopt_hand_edit(store)
        answer = self._compass_readout() or {}
        lines = ["The goal you typed in the file is now what the project is held to." if adopted
                 else "Nothing in the goal changed, so the record stayed where it was."]
        lines += self._memory_lines(answer) if answer else []
        return CommandResult("memory", "\n".join(lines), data={"adopted": adopted,
                                                               "memory": answer})

    def _memory_reset(self, rest: str) -> CommandResult:
        answer = compass.reset(self._store(), str(rest or "").strip() or "chat", self.chat_id,
                               arabic=self.arabic)
        before = self._memory_lines(answer["was"])
        said = labels.note("memory_reset_" + answer["said"], arabic=self.arabic)
        if not answer["removed"]:
            return CommandResult("memory", said, data={"removed": [], "was": before})
        # The words the store just dropped are printed under the sentence that says they went: a
        # memory with no other witness is unrecoverable, and the operator gets one look at it.
        return CommandResult("memory", said + "\n"
                             + labels.note("memory_reset_kept", arabic=self.arabic) + "\n"
                             + "\n".join(before), data={"removed": answer["removed"],
                                                        "was": before})

    def _memory_answer(self, action: str) -> CommandResult:
        outcome = compass.answer_goal(self._store(), action == "approve", arabic=self.arabic)
        return CommandResult("memory", outcome.message + "\n  goal: " + (outcome.goal or "(none)"),
                             data={"reason": outcome.reason, "goal": outcome.goal,
                                   "applied": outcome.applied})

    @property
    def chat_id(self) -> str:
        return str((self.session or {}).get("chat_id") or "")

    def _help(self, args: str = "") -> CommandResult:
        lines = ["Unified commands — the same twelve in the terminal and the window:", ""]
        lines += [f"  {USAGE[name][0]:<38} {USAGE[name][1]}" for name in COMMANDS]
        table = Table(title="Unified commands", title_style="bold", show_header=False, expand=False)
        table.add_column("Command", overflow="fold")
        table.add_column("Answers", overflow="fold")
        for name in COMMANDS:
            table.add_row(Text(USAGE[name][0], style="bold"), USAGE[name][1])
        return CommandResult("help", "\n".join(lines), renderables=(table,),
                             data={"commands": [{"name": name, "usage": USAGE[name][0],
                                                 "about": USAGE[name][1]} for name in COMMANDS]})

    # ------------------------------------------------------------------ host reads

    def _busy(self) -> bool:
        return bool(getattr(self.host, "busy", False))

    def _inbox(self):
        return getattr(self.host, "_steering", None)

    def _waiting(self) -> list[dict]:
        inbox = self._inbox()
        rows = list(inbox.waiting()) if inbox is not None else []
        if rows:
            return rows
        return [row for row in (self.session or {}).get("steering") or []
                if isinstance(row, dict) and not row.get("read")]


def _parse_diff_args(args: str) -> tuple[str, int]:
    """``diff [PATH] [--context N]`` — the first bare word is the path, ``--context`` the count."""
    path, context = "", CONTEXT
    tokens = str(args or "").split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--context" and index + 1 < len(tokens):
            try:
                context = max(0, int(tokens[index + 1]))
            except ValueError:
                raise AgentError("--context wants a number of lines, not " + repr(tokens[index + 1]) + ".")
            index += 2
            continue
        if not path and not token.startswith("-"):
            path = token
        index += 1
    return path, context


def _parse_steer_args(args: str) -> tuple[bool, str]:
    """``steer [--urgent] <instruction>`` — the instruction keeps its spaces, one line only."""
    text = str(args or "").strip()
    urgent = False
    if text.lower().startswith("--urgent"):
        urgent = True
        text = text[len("--urgent"):].strip()
    return urgent, " ".join(text.split())


def handle(line: str, session: Optional[dict] = None, *,
           session_path: Optional[Path] = None, host: Any = None,
           plans: Optional[Path] = None, memory_dir: Optional[Path] = None,
           arabic: Optional[bool] = None) -> CommandResult:
    """One-shot answer for a caller that keeps no handler of its own."""
    return Commands(session, session_path=session_path, host=host, plans=plans,
                    memory_dir=memory_dir, arabic=arabic).run(line)
