"""Headless orchestration: every decision the desktop window made, with no Tk in sight.

The shape of a turn is the same one the Tk window established: validate what the user
asked for, run the engine in a worker thread, and only then let a proposal be applied
after an explicit yes. The difference is that output is a stream of events instead of
widget updates, and the two places the window reached for a native dialog now ask the
front-end and block until it answers.
"""
from __future__ import annotations

import ctypes
import difflib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
import threading
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ..catalog import LIVE, models_for
from ..chat import (context_block, context_use, create_chat, load_chat, project_of, respond,
                    title_for)
from .. import config
from ..config import Settings
from ..engine import (MAX_TASK_CHARS, apply_proposal, atomic_json, chat_sessions, diff_size,
                      load_session, plan, project_key, propose_block, read_plan_reference, rollback)
from ..errors import AgentError, PolicyError
from ..labels import (INTERRUPTED_STATES, MUTABLE_STATES, STEP_FIELDS, TONE, UNVERIFIED_STATES,
                      applied_line, applied_note, artifact_card, batch_summary_line,
                      branch_started, branch_switched,
                      catalog_status_line, checkpoint_note, detail_section, executed_line,
                      executing_line, friendly_error, fix_offers_off_line, is_arabic,
                      log_dropped_line,
                      no_branch_note,
                      no_checkpoint_note, queue_notes,
                      restore_done, restore_offer, run_unrecorded_line, run_verdict, say, state_label,
                      status_text, step_has_detail, step_line, step_missing_line,
                      write_notice)
from ..providers import make_provider
from ..redaction import redact
from .. import memory as memory_store
from .. import git_integration, host, planbook, repair, runner, symbols
from ..verification import verify
from ..workspace import Workspace, ensure_project_dir

DEFAULT_MODEL = "qwen2.5-coder:1.5b"
RECOMMENDED = {
    "qwen2.5-coder:1.5b": "recommended here — valid proposals in ~25s, good default for iterating",
    "qwen3:4b": "more careful answers, roughly 2× slower on this machine",
}


def _mode_rows() -> tuple[tuple[str, config.Kind], ...]:
    """One row per provider the windows offer, as ``(label, kind)``.

    OpenRouter is two rows on purpose: its free list and its paid list differ in what they cost,
    which is a decision the user makes per task, not a setting. Every other row is one provider.
    """
    rows = []
    for kind in config.KINDS:
        if kind.free_only:
            rows.append((f"{kind.label} \u00b7 Free", kind))
            rows.append((f"{kind.label} \u00b7 Paid", kind))
        else:
            rows.append((kind.label, kind))
    return tuple(rows)


MODE_ROWS = _mode_rows()
MODES = tuple(label for label, _ in MODE_ROWS)
MODE_KIND = dict(MODE_ROWS)


def free_mode(kind: config.Kind) -> str:
    return f"{kind.label} \u00b7 Free"


def paid_mode(kind: config.Kind) -> str:
    return f"{kind.label} \u00b7 Paid"
PLAN_SUFFIXES = (".md", ".txt")
# A sidebar entry is a branch, and a branch is the only thing that owns a folder. "chat" never
# has one unless a project was bound to it by name, so the program cannot start pointed at a
# directory the user did not choose in this session.
BRANCH_CHAT, BRANCH_PROJECT = "chat", "project"
CHAT_COMPOSER, CHANGE_COMPOSER = "chat", "change"
CHAT_ID = re.compile(r"[a-f0-9]{32}")
# A closed palette, not free text: the value is painted into the sidebar, so anything a
# crafted registry could inject there would read to the user as their own label. Escapes
# keep the source plain ASCII, which a cp1252 console can at least print.
PROJECT_ICONS = ("📁", "🚀", "🐍", "☕", "⚛️",
                 "🦀", "🌐", "📦", "🧪", "🛠️")
DEFAULT_ICON = "📁"          # folder

# A project branch opens in Chat, and that is the right default: a greeting must not become a
# rejected diff. It is also the wrong answer to "add a logout button to the login page", so the
# opening of a message is checked before the mode is honoured. A request rarely starts on its
# verb — "عايز اعمل مشروع" opens on "عايز" and "I want to create" opens on "I" — so the first
# five folded tokens are scanned for a build verb, and an Arabic request word at the front counts
# on its own. Two shapes still veto the whole thing: a message that opens with a question word,
# and a message that ends with a question mark, because "how do I add a logout button?" is a
# question about code, not permission to write it.
# A match routes that one message down the reviewed-change path; it does not change the branch's
# mode, so the next message is prose again.
CHANGE_WINDOW = 5
CHANGE_VERBS = frozenset(("create", "add", "fix", "implement", "refactor", "build", "make",
                          "delete", "scaffold", "write", "setup", "generate"))
# Spelling is folded to one form first, so أنشئ and طوّر match what a person types rather
# than what a keyboard layout calls it.
ARABIC_CHANGE_VERBS = frozenset(("انشي", "عدل", "صلح", "اكتب", "ضيف", "اضف",
                                 "اعمل", "احذف", "غير", "ابني", "طور", "نفذ",
                                 "سو", "سوي", "ركب"))
# Egyptian and Levantine ways of opening "I want you to …". These count anywhere in the window,
# because the wanting word is rarely first ("يا صاحبي عايز تعمل login", "برضو عايز اضيف زر").
# What makes that safe is order: a message that opens with a question word, or ends with a question
# mark, has already been refused before these are consulted — "هل ممكن…" stays a question.
ARABIC_INTENT_WORDS = frozenset(("عايز", "محتاج", "ياريت", "ممكن", "اريد", "بدي", "حابب"))
# "لو سمحت" is two tokens, so it is matched as a prefix of the folded head rather than as a word.
# "من فضلك" is deliberately absent: its first token is already the question word "من", and a
# phrase that has to be tested before the veto is a phrase the veto will keep breaking.
ARABIC_INTENT_PHRASES = ("لو سمحت",)


ASKING_FIRST = frozenset(("what", "whats", "why", "how", "when", "where", "which", "who",
                          "explain", "describe", "tell", "is", "are", "does", "do", "can",
                          "could", "should", "would", "show", "difference", "meaning"))
ARABIC_ASKING_FIRST = frozenset(("ما", "متي", "اين", "كيف", "ليه", "لماذا", "هل", "اشرح",
                                 "عرف", "احكيلي", "ايه", "شو", "من", "كم"))
ARABIC_FOLDS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ؤ": "و", "ئ": "ي",
                              "ى": "ي", "ة": "ه", "ـ": ""})
ARABIC_MARKS = re.compile("[\\u064b-\\u0652\\u0670]")
QUESTION_ENDINGS = ("?", "\u061f")        # the Latin and the Arabic question mark
# "can you fix this" opens like a question and means a request; "can you add a column?" means a
# question. Only these four openers carry that ambiguity, and only a following "you" plus a build
# verb resolves it — so they are vetted by the mark instead of by the word.
REQUEST_MODAL = ("can", "could", "will", "would")


def _words(text: str, limit: int = CHANGE_WINDOW) -> list[str]:
    """The opening tokens, folded for spelling and stripped of their punctuation."""
    parts = (text or "").split()[:limit]
    return [re.sub("[^\\w'-]+", "", ARABIC_MARKS.sub("", part).translate(ARABIC_FOLDS)).casefold()
            for part in parts]


def _wants_a_change(stripped: str) -> bool:
    words = _words(stripped)
    if not words:
        return False
    asked = stripped.endswith(QUESTION_ENDINGS)
    first = words[0]
    if first in ARABIC_ASKING_FIRST:
        return False
    if first in ASKING_FIRST:
        if not (first in REQUEST_MODAL and "you" in words[1:3] and not asked):
            return False
    elif asked:
        return False
    if any(word in CHANGE_VERBS or word in ARABIC_CHANGE_VERBS for word in words):
        return True
    if any(word in ARABIC_INTENT_WORDS for word in words):
        return True
    return " ".join(words).startswith(ARABIC_INTENT_PHRASES)


def asks_for_a_change(text: str) -> bool:
    """True when the message opens by asking for files to be changed."""
    return _wants_a_change((text or "").strip())


def _clock() -> str:
    return datetime.now().strftime("%H:%M")


# How long a question may wait for the browser before the worker gives up on it. The window has to
# be long enough for a person to read a diff, and the ask is withdrawn when it expires (see _ask).
ASK_TIMEOUT = 1800

# The Activity list is shipped whole in every snapshot, so an uncapped one turns a 5 000-line build
# into a 5 000-line payload on every later push — the same reason streamed output never reaches the
# server at all (`_build_line`). What falls off the front is counted and said, not dropped quietly.
MAX_LOG_ENTRIES = 400

# The only file count a batch row trusts: the one the task spelled out in the scaffold's own words
# ("Create exactly three new files"). D36 was a small model answering that with two files, and this
# is the check that says so. An implied count in other prose is not counted, because reading it would
# refuse good work as often as it caught this.
BATCH_FILE_COUNT = re.compile(r"create exactly (one|two|three|four|five|six|seven|eight|[1-8]) "
                              r"(?:new )?files?", re.I)
COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}


def _asked_file_count(text: str) -> int | None:
    match = BATCH_FILE_COUNT.search(text or "")
    if not match:
        return None
    word = match.group(1)
    return int(word) if word.isdigit() else COUNT_WORDS.get(word.lower())


class AgentController:
    """One user, one window, one task at a time. ``busy`` is the lock that keeps it that way."""

    def __init__(self, app_dir: Path) -> None:
        self.app_dir = Path(app_dir).resolve()
        self.runs = self.app_dir / ".agent-runs"
        self.chats = self.app_dir / ".agent-chats"
        # Plan step ledgers live outside the approved project folder: a step that gates
        # the next step must not be writable by the model working on that step.
        self.plans = self.app_dir / ".agent-plans"
        # Same reason for the user's standing notes: they are instructions the model must
        # not be able to rewrite into something it will obey on the next task.
        self.memory_dir = self.app_dir / ".agent-memory"
        self.cancel_event = threading.Event()
        self._emit = lambda _event: None
        self._replies: dict[str, threading.Event] = {}
        self._answers: dict[str, dict] = {}
        # Every question still waiting for an answer, with the id the answer has to carry. The ids are
        # minted here and delivered once over SSE, so before this a restart — or a page that missed the
        # event — had an unanswerable question holding a worker: measured as a 30-minute stall on
        # `busy=False`. A list, not one slot, because two offers were live at the same time.
        self._active_asks: list[dict] = []
        self._session_cache: dict[Path, tuple[tuple, dict | None]] = {}
        self._chat_cache: dict[Path, tuple[tuple, dict | None]] = {}
        self._jobs: list[threading.Thread] = []

        self.status = "Ready — a new chat answers in prose. Choose a project for reviewed changes."
        self.log: list[dict] = []
        self.log_dropped = 0
        # The one step row the window has open. Fetched, never shipped with every snapshot: a single
        # run's stored tail is 2 500 characters, and a build per turn would ride every later push.
        self.step_detail: dict | None = None
        # Index of the "Executing …" row that is waiting for its command to answer.
        self._run_step = -1
        self.messages: list[dict] = []
        self.title = "New chat"
        self.subtitle = "Standalone chat — no folder attached, nothing to change"
        self.repo = ""
        self.plan_file = ""
        self.chained = False
        self.mode = "Ollama"
        self.model = ""
        self.model_filter = ""
        self.key = ""
        self.cloud_ok = False
        # One endpoint per provider row, because a machine can have LM Studio *and* Ollama running
        # and switching between them must not lose where the other one lives. Empty means the row's
        # own default; nothing here is a secret, so all of it is safe to persist.
        self.endpoints: dict[str, str] = {}
        self.profile = ""
        # Which provider row each catalog came from: a live answer and the built-in fallback read
        # differently, and the window has to say which one the user is looking at.
        self.catalog_source: dict[str, str] = {}
        self.request_timeout = config.REQUEST_TIMEOUT_DEFAULT
        self.recipe = ""
        self.run_info = "No command has run yet."
        self.catalogs: dict[str, list[dict]] = {mode: [] for mode in MODES}
        self.selections: dict[str, str] = {}
        self.active_mode = "Ollama"
        self.busy = False
        self.cancellable = False
        self.pending: str | None = None
        # The newest line the running job said, so the strip can tell a progress line from an outcome
        # worth keeping when the job ends.
        self._last_progress = ""
        # The last step sentence drawn into the chat, so a model that reads the same file twice
        # does not say so twice.
        self._last_step = ""
        self.session: dict | None = None
        self.session_path: Path | None = None
        self.chat: dict | None = None
        self.chat_id = uuid.uuid4().hex
        self.current_project = ""
        self.branch: dict = {"kind": BRANCH_CHAT, "key": "", "id": self.chat_id}
        self.composer = CHAT_COMPOSER
        self._composer_pref: dict[str, str] = {}
        # Auto-Apply belongs to a folder and starts off. `wrote_without_asking` is set by the
        # one apply that skipped the confirmation, so the message afterwards can say so.
        self._auto_pref: dict[str, bool] = {}
        self.auto_apply = False
        self.wrote_without_asking = False
        # How many files the last automatic write touched, so the review card can keep saying
        # "this appeared here without a click" after the status line has moved on.
        self.auto_banner = 0
        self.ledger_path: Path | None = None
        self.ledger: dict | None = None
        self.recipes: list[str] = []
        self.review_file = 0
        self.diff_tab = "diff"
        self._fix_round = 0
        self._auto_fix = False
        self._loading_session = False
        self._reverting = False
        self._pending_model = ""
        self._draft = ""
        # The branch a task branch was started from, so the chip can offer the way back. In memory
        # on purpose: after a restart the folder's git history is the record, not this window.
        self._git_base = ""
        # The commit made before this task wrote its files, and the restore offer that grows out of
        # it when the session's own rollback refuses. Both belong to one task, so a new task clears
        # them: a hash from the previous job would restore the wrong version of the same path.
        self._task_commit = ""
        self._git_restore: dict = {}
        # Messages sent while a task is running. In memory on purpose: a queued change request that
        # outlives a reboot can be applied to files that have moved on since it was typed.
        self.queue: list[dict] = []
        # Stop stops the queue as well as the task, or pressing it would be followed immediately by
        # the next queued message starting. Any queue action, or a new task, clears it.
        self._queue_held = False
        # The fix offer's third answer, and it lives exactly as long as the batch does: an unanswered
        # offer blocks the worker that asked it, so every red build in a queue cost the whole ask
        # timeout. Turning the offers off is the operator's call, never the tool's.
        self._batch_fixes_off = False
        # What the queue has run so far, so the end of a batch can be said in one row instead of
        # being counted by the user scrolling. `_batch_row` is the task currently in flight.
        self._batch: list[dict] = []
        self._batch_row: dict | None = None
        # A detached item opens a new chat, which selects a branch, which would otherwise try to
        # drain the queue again from inside the drain that started it.
        self._draining = False
        # Set by `run_job` on the side that knows, and read by the drain to decide whether a queued
        # row has been taken. `start_plan` has seven ways to refuse, and a row consumed by a refusal
        # is a typed message that no longer exists anywhere.
        self._job_started = False
        self.projects: dict[str, str] = {}
        self.icons: dict[str, str] = {}
        self._saved_ui: dict = {}
        try:
            registry = json.loads((self.app_dir / ".agent-projects.json").read_text(encoding="utf-8"))
            for entry in registry["projects"]:
                # An entry is a bare path or a {"path", "icon"} pair: the file on disk predates
                # icons, and a granted folder must not vanish from the sidebar over a format bump.
                path = entry if isinstance(entry, str) else \
                    entry.get("path") if isinstance(entry, dict) else None
                if not isinstance(path, str) or not path.strip():
                    continue
                key = project_key(path)
                self.projects[key] = str(Path(path).resolve())
                icon = entry.get("icon") if isinstance(entry, dict) else None
                if isinstance(icon, str) and icon in PROJECT_ICONS:
                    self.icons[key] = icon
            if isinstance(registry.get("ui"), dict):
                self._saved_ui = registry["ui"]
        except (OSError, ValueError, KeyError, TypeError):
            self.projects, self.icons = {}, {}
        self.chained = bool(self._saved_ui.get("plan_chained"))
        saved_auto = self._saved_ui.get("auto_apply")
        if isinstance(saved_auto, dict):
            self._auto_pref = {str(row): bool(flag) for row, flag in saved_auto.items()}
        self.request_timeout = config.clamp_request_timeout(self._saved_ui.get("request_timeout"))
        saved_endpoints = self._saved_ui.get("endpoints")
        if isinstance(saved_endpoints, dict):
            self.endpoints = {str(row): str(value) for row, value in saved_endpoints.items()
                              if row in config.BY_KEY and isinstance(value, str)}
        saved_profile = self._saved_ui.get("profile")
        self.profile = saved_profile if isinstance(saved_profile, str) else ""
        saved_mode = self._saved_ui.get("mode")
        if isinstance(saved_mode, str) and saved_mode in self.catalogs:
            self.mode = self.active_mode = saved_mode
        if isinstance(self._saved_ui.get("model"), str) and self._saved_ui["model"]:
            self._pending_model = self._saved_ui["model"]
        # A batch outlives the window that typed it, but it does not resume itself: a queued change
        # request can be pointed at files that moved on since it was written, so every restored row
        # is held until the operator presses ▶, and carries the sentence that says why.
        saved_queue = self._saved_ui.get("queue")
        if isinstance(saved_queue, list):
            for item in saved_queue[:20]:
                if (isinstance(item, dict) and isinstance(item.get("text"), str)
                        and item["text"].strip() and len(item["text"]) <= MAX_TASK_CHARS):
                    self.queue.append({**item, "restored": True})
            self._queue_held = bool(self.queue)
        last = self._saved_ui.get("last_project")
        if isinstance(last, str) and last and Path(last).is_dir():
            self.projects.setdefault(project_key(last), str(Path(last).resolve()))
        # Which folder the window opens on belongs to the branch the user left selected, not to
        # one remembered path applied to everything. `last_project` is read into the project list
        # above so a granted folder keeps showing in the sidebar, and never auto-attaches: that is
        # how a greeting turned into a rejected diff.
        saved = self._saved_ui.get("composer")
        self._composer_pref = {str(k): v for k, v in saved.items() if isinstance(v, str)} \
            if isinstance(saved, dict) else {}
        branch = self._saved_ui.get("last_branch")
        kind = branch.get("kind") if isinstance(branch, dict) else None
        key = branch.get("key") if isinstance(branch, dict) else ""
        saved_chat = self._saved_ui.get("last_chat")
        resumed = (self.chats / saved_chat / "chat.json"
                   if isinstance(saved_chat, str) and CHAT_ID.fullmatch(saved_chat) else None)
        if kind == BRANCH_PROJECT and self.projects.get(str(key)):
            self._select_branch(BRANCH_PROJECT, str(key))
        elif resumed is not None and resumed.exists():
            try:
                self.open_chat(resumed)
            except (AgentError, OSError):
                self._select_branch(BRANCH_CHAT)
        else:
            self._select_branch(BRANCH_CHAT)

    # ------------------------------- prompts -------------------------------
    def _ask(self, kind: str, payload: dict) -> dict:
        """Push a modal to the front-end and block this thread until it answers.

        A question the browser never answers is withdrawn before this thread stops waiting. The
        wait ending on its own is invisible to the window otherwise, and the modal it leaves behind
        covers the app: every later question then stacks another one behind it, unanswered.
        """
        request_id = secrets.token_hex(8)
        waiter = threading.Event()
        self._replies[request_id] = waiter
        # A list, not one slot: two questions were simultaneously live during the run — the previous
        # task's offer and the current one — and a single field would have dropped the first along
        # with the only id that could unblock it.
        self._active_asks.append({"kind": kind, "id": request_id, **payload})
        self._emit({"kind": kind, "id": request_id, **payload})
        answered = waiter.wait(timeout=ASK_TIMEOUT)
        self._replies.pop(request_id, None)
        self._active_asks = [a for a in self._active_asks if a.get("id") != request_id]
        if not answered:
            self._answers.pop(request_id, None)
            self._emit({"kind": "retract", "id": request_id})
            self.status = status_text("ask_expired")
            return {}
        return self._answers.pop(request_id, {}) or {}

    def confirm_choice(self, title: str, message: str, warning: str = "", ok_label: str = "Continue",
                       alt_label: str = "") -> dict:
        """The same question as `confirm`, answered with everything the reply carried.

        `set_reply` already stores the whole body, so a third button needs no transport work — only a
        caller that can read it. `confirm` throws the rest away because for it yes/no is the question.
        """
        payload = {"title": title, "message": message, "warning": warning, "confirm": ok_label}
        if alt_label:
            payload["alt"] = alt_label
        return self._ask("confirm", payload)

    def confirm(self, title: str, message: str, warning: str = "", ok_label: str = "Continue") -> bool:
        answer = self.confirm_choice(title, message, warning, ok_label)
        return bool(answer.get("ok"))

    def ask_directory(self, title: str, hint: str = "", mustexist: bool = True) -> Path | None:
        start = self.repo or str(Path.home())
        answer = self._ask("folder", {"title": title, "hint": hint, "path": start, "mustexist": mustexist})
        chosen = answer.get("path") if answer.get("ok") else None
        return Path(chosen) if chosen else None

    def ask_plan_file(self) -> Path | None:
        start = self.repo or str(Path.home())
        answer = self._ask("folder", {"title": "Attach a project plan", "path": start,
                                      "hint": "Choose a .md or .txt plan inside the project folder.",
                                      "files": list(PLAN_SUFFIXES)})
        chosen = answer.get("path") if answer.get("ok") else None
        return Path(chosen) if chosen else None

    def set_reply(self, request_id: str, reply: dict) -> None:
        waiter = self._replies.pop(request_id, None)
        if waiter is None:
            return                      # a reply for a question that already closed or expired
        # Retire on the answering side too: the thread that asked has to be scheduled before it
        # removes itself, and a snapshot built in that gap would redraw a question already answered.
        self._active_asks = [a for a in self._active_asks if a.get("id") != request_id]
        self._answers[request_id] = reply or {}
        waiter.set()

    # ------------------------------ jobs ------------------------------
    def run_job(self, operation, on_done, status: str, *, cancellable: bool = False,
                on_busy=None) -> None:
        self._job_started = False
        if self.busy:
            # Two clicks in one tick both pass the caller's `busy` check, because each HTTP handler
            # reads the flag before either job has claimed it. A message that is dropped here is
            # work the user already typed, so the caller gets to say what it becomes instead.
            if on_busy is not None:
                on_busy()
            return
        self._job_started = True
        self.busy, self.cancellable = True, cancellable
        self.cancel_event.clear()
        self.status = status
        self._last_progress = ""
        self.pending = status
        self._emit({"kind": "busy", "value": True, "cancellable": cancellable})
        self._emit({"kind": "status", "text": status})
        self._note("job", status)

        def worker():
            result, failure, raw_error = None, None, None
            try:
                result = operation()
            except Exception as exc:                       # noqa: BLE001 - reported, never raised
                raw_error = str(exc)
                failure = friendly_error(exc)
            self.busy = self.cancellable = False
            self.pending = None
            self._emit({"kind": "busy", "value": False, "cancellable": False})
            if failure is not None:
                self.status = failure
                # The detail is the exception verbatim, which is the one field that can carry a
                # provider's own text — including a bearer token it echoed back. Cap after redacting.
                log_msg = (f"{failure} [Detail: {redact(raw_error)[:300]}]"
                           if (raw_error and raw_error != failure) else failure)
                self._note("error", log_msg)
                self._add("tool", "Tool", failure)
            else:
                try:
                    on_done(result)
                except (AgentError, OSError, ValueError) as exc:
                    self.status = friendly_error(exc)
                    self._note("error", f"{self.status} [Detail: {redact(str(exc))[:300]}]")
            # The strip keeps the last sentence it was told. If nothing but the running line and
            # this job's own progress said anything, they are dropped — "Connecting to the model…"
            # sitting there after the answer arrived is the phantom this window keeps having to kill.
            if self.status in (status, self._last_progress):
                self.status = ""
            # The queue runs from here rather than from a timer: this is the only moment that is
            # known to be "the task has finished", and a chained job (propose → apply → run) keeps
            # busy set until the chain is truly done, so the drain waits for the last step.
            self._drain_queue()
            self._emit({"kind": "state", "data": self.snapshot()})

        job = threading.Thread(target=worker, name="agent-job", daemon=True)
        self._jobs.append(job)
        job.start()

    # ------------------------------ queue ------------------------------
    def queue_add(self, text: str) -> None:
        """Hold a message typed during a running task, and start it when that task ends.

        The click that queues is the approval: the user chose both the text and the moment, so
        this is the one path in the app that starts work without a second click. It is still the
        folder's own rules that run — Change mode proposes, Auto-Apply writes, Chat answers in
        prose — and **Stop** holds the queue, because a stop that is followed instantly by the
        next message is not a stop.
        """
        task = (text or "").strip()
        if not task:
            return
        if len(task) > MAX_TASK_CHARS:
            self._add("tool", "Tool", say(self.arabic,
                      en=f"That message is longer than {MAX_TASK_CHARS:,} characters, so it was "
                         "not queued.",
                      ar="هذه الرسالة أطول من ٤٠٠٠ حرف، لذلك لم تُضَف إلى قائمة الانتظار."))
            return
        if any(item.get("text") == task and item.get("chat") == self.chat_id for item in self.queue):
            self._queue_held = False
            return                       # the same message twice in a row is one queue line
        self.queue.append({"id": uuid.uuid4().hex[:8], "text": task, "chat": self.chat_id,
                           "branch": str(self.branch.get("key") or ""),
                           "project": self.repo, "at": _clock()})
        self._queue_held = False
        # The message is on its way either way, so the confirmation is the server's — and it goes
        # out as the toast event the client already handles but nothing had ever sent.
        self._emit({"kind": "toast", "text": say(self.arabic,
                    en="Queued — it runs when the current task ends",
                    ar="أُضيفت إلى قائمة الانتظار — تُنفَّذ عند انتهاء المهمة الجارية")})
        # The click can land after the task it was queued behind has already finished, and a
        # message that says "Queued" and then never runs is worse than one that just sends.
        self._drain_queue()
        self._emit({"kind": "state", "data": self.snapshot()})

    def queue_edit(self, item_id: str, text: str) -> None:
        for item in self.queue:
            if item.get("id") == item_id:
                item["text"] = (text or "").strip()[:MAX_TASK_CHARS]
        self._queue_held = False
        self._emit({"kind": "state", "data": self.snapshot()})

    def queue_drop(self, item_id: str) -> None:
        self.queue = [item for item in self.queue if item.get("id") != item_id]
        self._save_state()
        self._emit({"kind": "state", "data": self.snapshot()})

    def queue_now(self, item_id: str) -> None:
        """Move one item to the front. With the queue already running this is the whole of
        "run this one next", and dropping a held queue with it is what ▶ resumes."""
        ids = [item.get("id") for item in self.queue]
        if item_id in ids:
            item = self.queue.pop(ids.index(item_id))
            self.queue.insert(0, item)
        self._release_restored()
        self._queue_held = False
        self._drain_queue()
        self._emit({"kind": "state", "data": self.snapshot()})

    def queue_detached(self, item_id: str) -> None:
        """Take an item out of this conversation and ask it in a chat of its own.

        The branch cannot move while a job runs — `_select_branch` refuses so a chained apply
        cannot find itself pointed at another folder — so an item detached mid-task waits for the
        moment the switch is allowed, and then opens there. It never runs in the chat it left.
        """
        for index, item in enumerate(self.queue):
            if item.get("id") == item_id:
                self.queue.pop(index)
                self.queue.insert(0, {**item, "detached": True, "chat": ""})
                break
        self._queue_held = False
        self._drain_queue()
        self._emit({"kind": "state", "data": self.snapshot()})

    def queue_resume(self) -> None:
        self._release_restored()
        self._queue_held = False
        self._drain_queue()
        self._emit({"kind": "state", "data": self.snapshot()})

    def _release_restored(self) -> None:
        """A press on the strip is the approval a restored row was waiting for.

        It covers every row rather than the one clicked, because the batch is the unit the operator
        is resuming: pressing ▶ beside "2 waiting" and having one of them run is the surprise.
        """
        for item in self.queue:
            item.pop("restored", None)

    def _drain_queue(self) -> None:
        """Start the next queued message whose conversation is the one in front.

        A row leaves the queue only once a job has actually taken it. `start_plan` refuses for reasons
        the queue cannot fix — no model selected, the folder is gone, the message too long — and a
        drain that popped first lost the typed message with nothing but a status line to show for it;
        two create tasks disappeared that way in the ecommerce run.
        """
        # The queue is stored in the same file as the rest of the window's state, and this is the one
        # place every mutation path passes through -- except an edit, which saves for itself.
        self._save_state()
        # A row that came back with the restart does not run on its own accord: the operator pressing
        # ▶ (or queueing something new beside it) is the approval, and until then the strip shows it
        # with the sentence that says it was typed in an earlier session.
        runnable = next((item for item in self.queue if not item.get("restored")), None)
        if self.busy or self._loading_session or self._queue_held or not self.queue:
            if not self.queue and not self.busy and self._batch:
                # Cleared when the batch it belongs to actually ends. A task with an empty queue is
                # not a batch of one that just closed: the flag has to outlast it, or "don't ask
                # again for this batch" would mean "not for the next two minutes".
                self._batch_fixes_off = False
                self._close_batch()
            return
        if runnable is None:
            return
        item = runnable
        self._draining = True
        try:
            if item.get("detached"):
                key = item.get("branch") or ""
                if key:
                    self.new_chat_in(key)
                else:
                    self.new_chat()
                if self.busy:
                    return              # the switch starts nothing on its own; send below
                self.start_plan(item.get("text", ""))
                if self._job_started:
                    self.queue.remove(item)
                    self._open_batch_row(item)
                return
            if item.get("chat") == self.chat_id:
                self.start_plan(item.get("text", ""))
                if self._job_started:
                    self.queue.remove(item)
                    self._open_batch_row(item)
        finally:
            self._draining = False

    def _open_batch_row(self, item: dict) -> None:
        """Start recording the queued task that was just taken out of the queue."""
        first = ((item.get("text") or "").strip().splitlines() or [""])
        self._batch_row = {"task": (first[0] if first else "")[:60], "paths": []}
        self._batch.append(self._batch_row)

    def _close_batch(self) -> None:
        """Say what the batch did, once, when its queue ran out.

        The discrepancy flag is deliberately narrow: it fires only when the task text literally asked
        for a count of new files — the shape D36 actually hit, where "Create exactly two new files"
        delivered one — because parsing arbitrary prose for an implied file count would refuse good
        work as often as it caught this.
        """
        rows, self._batch = self._batch, []
        self._batch_row = None
        if not rows:
            return
        paths = list(dict.fromkeys(path for row in rows for path in row["paths"]))
        short = [row["task"] for row in rows
                 if (asked := _asked_file_count(row.get("task", ""))) is not None
                 and len(row["paths"]) < asked]
        self._add("tool", "Batch", batch_summary_line(arabic=self.arabic, tasks=len(rows),
                                                      files=len(paths), paths=paths, short=short))

    def _queue_view(self) -> dict:
        """What the strip shows: the messages, and whether they are held.

        The sentences are built here for the same reason the banner's are: the client cannot tell
        what language the task was asked in, and a strip that mixed English status lines into an
        Arabic conversation would be the drift this project keeps having to undo.

        Items queued for another conversation stay in the list — they belong to that chat and will
        run when the user goes back to it — but they are not drawn here, because an invisible line
        that fires later is exactly the surprise this strip exists to avoid.
        """
        here = [{"id": item.get("id", ""), "text": item.get("text", ""),
                 "detached": bool(item.get("detached")), "at": item.get("at", ""),
                 "restored": bool(item.get("restored"))}
                for item in self.queue if item.get("chat") == self.chat_id or item.get("detached")]
        elsewhere = len([item for item in self.queue
                         if item.get("chat") not in {self.chat_id, ""} and not item.get("detached")])
        return {"items": here, "held": bool(self._queue_held), "elsewhere": elsewhere,
                **queue_notes(self.arabic, elsewhere, bool(self._replies))}

    def join(self, timeout: float = 60.0) -> None:
        """Wait for every started job — including one a completion started behind it.

        A job's `on_done` can begin another job, which is exactly what Auto-Apply does: the
        proposal's completion calls apply, whose completion runs the project's command. One
        pass over the list would return with that chain still writing, so each job is waited
        on once and any that appeared while waiting is waited on too.
        """
        deadline = time.monotonic() + timeout
        seen = set()
        while True:
            pending = [job for job in self._jobs if id(job) not in seen]
            if not pending:
                break
            for job in pending:
                seen.add(id(job))
                job.join(max(0.05, deadline - time.monotonic()))
        self._jobs.clear()

    def _note(self, kind: str, text: str) -> None:
        entry = {"ts": _clock(), "kind": kind, "text": text}
        self.log.append(entry)
        if len(self.log) > MAX_LOG_ENTRIES:
            # Counted rather than trimmed: the dropped number is drawn at the top of Activity, because
            # a log that quietly got shorter reads as a task that did less than it did.
            self.log.pop(0)
            self.log_dropped += 1
        # `kind` names the SSE channel, so the entry travels inside its own field instead of
        # being flattened into the event — flattened, the two kinds collided.
        self._emit({"kind": "log", "entry": entry})

    def _add(self, role: str, author: str, text: str, step: dict | None = None) -> None:
        message = {"role": role, "author": author, "text": text, "time": _clock()}
        if step:
            # A step row is a message with a handle on its own record: the sentence is what the row
            # says, and `step` is what lets it be opened again after a reload or a reopen.
            message["step"] = step
        self.messages.append(message)
        self._emit({"kind": "message", "message": message})

    # ------------------------------ contract ------------------------------
    def _git_info(self) -> dict:
        """The git fields the branch chip draws.

        Only names and a count cross the boundary: the chip says "main, 3 changed", and the
        path list behind that count belongs to the server, which is also the only place that
        ever acts on it. An empty dict means there is no folder to ask about, so the front
        end has one less thing to special-case.

        `base` is the exception to "ask git": it is what *this window* moved HEAD away from, so
        git has no way to answer it and the chip needs it to offer the return trip.
        """
        if not self.repo:
            return {}
        info = git_integration.status(self.repo)
        shown = {key: info[key] for key in ("repo", "branch", "detached", "head", "dirty")}
        if self._git_base:
            shown["base"] = self._git_base
        if self._git_restore.get("paths"):
            shown["restore"] = {"commit": self._git_restore.get("commit", ""),
                                "paths": len(self._git_restore["paths"])}
        return shown

    @property
    def arabic(self) -> bool:
        """Was the task in front of the window asked in Arabic?

        The model is already told to answer in the language it was asked in; this is the same rule
        applied to the sentences we write. It reads the session's task first, then the last thing
        the user typed, because a status line set before any session exists still answers to that
        person. Anything with no text of theirs to look at stays English.
        """
        task = (self.session or {}).get("task") or ""
        if not task:
            task = next((m.get("text", "") for m in reversed(self.messages)
                         if m.get("role") == "user"), "")
        return is_arabic(task)

    def _banner(self) -> dict:
        """The auto-write note as a finished sentence.

        The server writes the text rather than the card, because the card would have to guess the
        language: only this side knows what the task was asked in. `count` stays for anything that
        wants the number instead of the prose.
        """
        if not self.auto_banner:
            return {"count": 0, "text": ""}
        return {"count": self.auto_banner,
                "text": applied_note(arabic=self.arabic, count=self.auto_banner)}

    def snapshot(self) -> dict:
        project = Path(self.repo) if self.repo else None
        plan_info = self._plan_info()
        return {
            "prefs": {"style": self._saved_ui.get("style", "claude"),
                      "theme": self._saved_ui.get("theme", "light"),
                      "collapsed": bool(self._saved_ui.get("collapsed", False))},
            "busy": self.busy, "cancellable": self.cancellable, "pending": self.pending,
            # The strip under the header draws from here. It used to be write-only — about sixty
            # sentences were assigned to this field and none of them reached a surface, which is
            # why the ones this window does show had to be rebuilt in the front end.
            "status": self.status,
            "current": self.session_path.parent.name if self.session_path else self.chat_id,
            "header": {"title": self.title, "subtitle": self.subtitle},
            "project": None if not project else {"name": project.name, "path": str(project)},
            "branch": {**self.branch, "projectName": project.name if project else ""},
            "git": self._git_info(),
            "icons": list(PROJECT_ICONS),
            "composer": self.composer,
            "plan": plan_info,
            "provider": {"mode": self.mode, "modes": list(MODES), "model": self.model,
                         "models": self.visible_models()},
            # The whole connection row: a drawer that offered a provider without saying where it
            # points, or whether it wants a key, could only be filled by trial and error.
            "connection": self.connection_info(),
            "recipes": [runner.RECIPES[name]["label"] for name in self.recipes],
            "recipe": self.recipe, "canRun": self._can_run(), "runInfo": self.run_info,
            "memory": {"info": self._memory_info()},
            "settings": {"project": self.repo, "plan": self.plan_file, "chained": self.chained,
                         "auto_apply": self.auto_apply, "bound": bool(self.branch.get("bound")),
                         "timeout": self.request_timeout, "model_info": self._model_info(),
                         "memory": self._project_notes(), "memory_info": self._memory_info(),
                         "consent": self.cloud_ok},
            "artifact": self._artifact(), "review": self._review(), "banner": self._banner(),
            "queue": self._queue_view(),
            # The questions a worker is blocked on, with their ids: a page that connects after the
            # event was sent can still answer one, which is the whole of D33.
            "asks": [dict(a) for a in self._active_asks],
            "draft": self._draft,
            "messages": self.messages, "log": self.log, "log_dropped": self.log_dropped,
            # The same fact as a sentence: `log_dropped` is the number, this is what Activity prints.
            "log_note": (log_dropped_line(arabic=self.arabic, count=self.log_dropped)
                         if self.log_dropped else ""),
            # The one step row the window has open, with the records behind it. Fetched on a click
            # rather than shipped every push — see `open_step`.
            "step_detail": self.step_detail,
            "projects": self._nav_projects(), "chats": self._nav_chats(),
        }

    def action(self, type: str, payload: dict, emit) -> dict | None:
        self._emit = emit
        handlers = {
            "send": lambda: self.start_plan(payload.get("text", "")),
            "queue_add": lambda: self.queue_add(payload.get("text", "")),
            "queue_edit": lambda: self.queue_edit(str(payload.get("id", "")),
                                                   payload.get("text", "")),
            "queue_drop": lambda: self.queue_drop(str(payload.get("id", ""))),
            "queue_now": lambda: self.queue_now(str(payload.get("id", ""))),
            "queue_chat": lambda: self.queue_detached(str(payload.get("id", ""))),
            "queue_resume": self.queue_resume,
            "stop": self.stop, "apply": self.apply, "rollback": self.undo,
            "git_branch": lambda: self.git_branch(str(payload.get("back", ""))),
            "git_restore": self.git_restore,
            "apply_block": lambda: self.offer_block(payload),
            "verify": self.check_changes, "run": lambda: self.run_tests(bool(payload.get("fix"))),
            # Opening a step row: the id is the handle on the records this session already keeps, and
            # an empty one closes the row. Nothing here reads or writes the project.
            "step_detail": lambda: self.open_step(str(payload.get("id", ""))),
            "new_chat": self.new_chat, "new_project": self.new_project, "example": self.example,
            "open": lambda: self.open_item(payload.get("kind", "session"), payload.get("id", "")),
            "pick_project": self.browse,
            "set_composer": lambda: self.set_composer(str(payload.get("value", ""))),
            "set_auto_apply": lambda: self.set_auto_apply(bool(payload.get("value"))),
            # Both are validated inside: an endpoint that cannot be a base is refused and reported,
            # and a profile label that is not a bare name reads no file at all.
            "set_endpoint": lambda: self.set_endpoint(str(payload.get("value", ""))),
            "set_profile": lambda: self.set_profile(str(payload.get("value", ""))),
            "bind_chat": lambda: self.bind_chat(payload.get("chat", ""), payload.get("project")),
            "set_icon": lambda: self.set_icon(payload.get("project", ""), payload.get("value", "")),
            "new_chat_in": lambda: self.new_chat_in(payload.get("project", "")),
            "reveal": lambda: self.reveal(payload.get("project", "")),
            "pick_plan": self.browse_plan, "clear_plan": self.clear_plan,
            "set_mode": lambda: self.set_mode(payload.get("value", "")),
            "set_model": lambda: self.set_model(payload.get("value", "")),
            "set_filter": lambda: self.set_filter(payload.get("value", "")),
            "set_recipe": lambda: self.set_recipe(payload.get("value", "")),
            "set_chained": lambda: self.set_chained(bool(payload.get("value"))),
            "set_timeout": lambda: self.set_timeout(payload.get("value")),
            "set_key": lambda: self.set_key(payload.get("value", "")),
            "set_consent": lambda: self.set_consent(bool(payload.get("value"))),
            "set_style": lambda: self.set_pref("style", str(payload.get("style", "claude"))),
            "set_collapsed": lambda: self.set_pref("collapsed", bool(payload.get("value"))),
            "set_theme": lambda: self.set_pref("theme", str(payload.get("theme", "light"))),
            "set_draft": lambda: setattr(self, "_draft", payload.get("text", "")),
            "save_memory": lambda: self.save_memory(payload.get("text", "")),
            "select_file": lambda: setattr(self, "review_file", int(payload.get("index", 0))),
            "select_tab": lambda: setattr(self, "diff_tab", str(payload.get("tab", "diff"))),
            "refresh_models": self.check_setup,
        }
        handler = handlers.get(type)
        if handler is None:
            raise PolicyError("Unknown action: " + str(type)[:40])
        return handler()

    def list_dir(self, path: str, want_files=None) -> dict:
        root = Path(path or Path.home())
        if not root.is_dir():
            root = Path.home()
        dirs, files = [], []
        for entry in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
            try:
                if entry.is_dir():
                    if not _hidden(entry) and entry.name.casefold() not in SKIP_DIRS:
                        dirs.append(entry)
                elif want_files and entry.suffix.lower() in {str(s) for s in want_files}:
                    files.append(entry)
            except OSError:
                continue
        # A drive root has no parent, so without the drive list the picker is a dead end on C: and
        # a project on another disk cannot be reached at all.
        return {"path": str(root), "parent": str(root.parent) if root.parent != root else None,
                "roots": drive_roots(),
                "dirs": [{"name": p.name, "path": str(p)} for p in dirs[:400]],
                "files": [{"name": p.name, "path": str(p)} for p in files[:400]]}

    # --------------------------- selection setters ---------------------------
    def _select_branch(self, kind: str, key: str = "", *, chat_id: str | None = None,
                       composer: str | None = None, bound: bool = False) -> None:
        """The only writer of ``self.repo``: a folder belongs to the selected branch or to nothing.

        ``chat_id`` means "keep this conversation" — moving a chat between branches must not
        reset the thread the user is reading, so only a branch with no chat of its own calls
        ``new_task``.

        ``bound`` is a chat that reads a project. It keeps the folder for context and nothing
        else: neither the folder's remembered mode nor its Auto-Apply switch is taken, because
        the promise to a bound chat is that it cannot write, and the folder it was dropped on may
        have been left in Change with the switch on.
        """
        if self.busy:
            return
        repo = ""
        if kind == BRANCH_PROJECT:
            repo = self.projects.get(key, "")
            if not repo or not Path(repo).is_dir():
                self.status = "That project folder is not available. Open it again from the sidebar."
                return
        if repo != self.repo.strip():
            self._loading_session = True
            try:
                self.project_changed(repo)          # keeps the unsent-draft confirmation
            finally:
                self._loading_session = False
            if repo != self.repo.strip():
                return                              # the user chose to stay on the current project
        self.branch = {"kind": kind, "key": key if repo else "", "id": chat_id or self.chat_id,
                       "bound": bool(repo and bound)}
        self.composer = (CHAT_COMPOSER if self.branch["bound"]
                         else composer or self._branch_mode(self.branch["key"]))
        # A folder the window just put into Change mode has to still be in Change mode after a
        # restart. `set_composer` writes this map when a *person* flips the badge; the paths that
        # select a branch with an explicit composer wrote nothing, so a folder granted in one session
        # came back as Chat in the next and its first message was answered in prose. Only the Change
        # grant is recorded: `new_chat_in` names Chat on purpose for one conversation, and a bound
        # chat must never be able to write anything at all.
        if composer == CHANGE_COMPOSER and not self.branch["bound"] and self.branch["key"]:
            self._composer_pref[self.branch["key"]] = CHANGE_COMPOSER
        self.auto_apply = bool(self._auto_pref.get(str(self.branch.get("key") or ""), False)) \
            if not self.branch["bound"] else False
        if chat_id:
            self.chat_id = chat_id
        else:
            self.new_task()
        # project_changed saved the state while the branch was still the previous one, so the
        # branch that was actually selected is written once more.
        self._save_state()
        self.subtitle = self._subtitle()
        # Arriving at a conversation that has something waiting starts it here; without this the
        # queue would only move when a job finished, and a stopped task never finishes again.
        if not self._draining:
            self._drain_queue()

    def _branch_mode(self, key: str) -> str:
        """A chat with no folder can only answer in prose; a project keeps the mode it was left in."""
        if not key:
            return CHAT_COMPOSER
        return self._composer_pref.get(key, CHAT_COMPOSER)

    def _grant_folder(self, key: str, resolved: str) -> None:
        """Register a folder and open it — the one way a project branch is entered by granting.

        A folder never seen before lands on Change mode, because naming a folder and asking for work
        on it is what the user asked the window to do. A folder the person has since switched by hand
        comes back exactly the way they left it, so the grant never overrules a choice.
        """
        self.projects.setdefault(key, resolved)
        self._select_branch(BRANCH_PROJECT, key,
                            composer=None if key in self._composer_pref else CHANGE_COMPOSER)

    def set_composer(self, value: str) -> None:
        wanted = CHANGE_COMPOSER if value == CHANGE_COMPOSER else CHAT_COMPOSER
        if wanted == CHANGE_COMPOSER and not self.repo:
            self.status = "Choose a project in the sidebar before asking for reviewed changes."
            return
        if wanted == CHANGE_COMPOSER and self.branch.get("bound"):
            # The folder this chat reads is not a folder it may write. Leaving the chat is the way.
            self.status = ("A chat moved into a project reads that folder only. Open the project "
                           "itself from the sidebar to ask for reviewed changes to its files.")
            return
        self.composer = wanted
        if self.branch.get("key") and not self.branch.get("bound"):
            self._composer_pref[self.branch["key"]] = wanted
        self.subtitle = self._subtitle()
        self._save_state()
        self.status = ("Change mode: Send proposes a diff you review before any file is written."
                       if wanted == CHANGE_COMPOSER else
                       "Chat mode: Send answers in prose and cannot write files.")

    def set_auto_apply(self, value) -> None:
        """Turn on the switch that removes the click between reviewing a diff and writing it.

        It is per folder and it is off until asked, because this is the one setting in the
        program that lets a program change a disk without being told to each time. Turning
        it on for one repo says nothing about the next folder opened in the same window.
        The review itself is untouched: the proposal is still built, hashed and shown, and
        two cases still stop for an answer — see `apply`.
        """
        if not self.repo:
            self.status = "Choose a project before turning Auto-Apply on."
            return
        if self.branch.get("bound"):
            # Writing the folder's pref from here would arm the project branch that owns it.
            self.status = ("A chat moved into a project cannot switch it to writing itself. Turn "
                           "the switch on while the project itself is selected.")
            return
        wanted = bool(value)
        self.auto_apply = wanted
        key = str(self.branch.get("key") or project_key(self.repo))
        if wanted:
            self._auto_pref[key] = True
        else:
            self._auto_pref.pop(key, None)
        self._save_state()
        self.status = ("\u26a1 Auto-Apply is ON for " + Path(self.repo).name +
                       ": a proposal that comes back from the model writes itself, then runs the"
                       " project's own command. It still asks before emptying a file you wrote, and"
                       " a code block you click still waits for Apply." if wanted else
                       "Auto-Apply is OFF: every proposal waits for you to click Apply.")

    def bind_chat(self, chat_id, project) -> None:
        """Link a chat to a granted project so its answers read that folder's context.

        The browser names a project by its registry key, never by a path, so a dropped leaf
        cannot aim a conversation at a folder the user has not granted. Binding adds read
        context only: a chat still has no tools, no proposal, and no way to write.
        """
        if self.busy or not isinstance(chat_id, str) or not CHAT_ID.fullmatch(chat_id):
            self.status = "Only a chat listed in the sidebar can be moved."
            return
        key = "" if project in (None, "") else str(project)
        folder = self.projects.get(key, "") if key else ""
        if key and not folder:
            self.status = "That project has not been granted access, so a chat cannot be moved into it."
            return
        path = self.chats / chat_id / "chat.json"
        if not path.exists():
            self.status = "That chat no longer exists."
            return
        try:
            chat = load_chat(path)
        except (AgentError, OSError) as exc:
            self.status = friendly_error(exc)
            return
        chat["project"] = {"key": key, "path": folder} if key else None
        try:
            atomic_json(path, chat)
        except OSError:
            self.status = "Could not save the chat's project link."
            return
        if key:
            self.status = "Chat moved into " + Path(folder).name + "."
        else:
            self.status = "Chat detached from its project."
        if chat_id == self.chat_id:
            self.chat = chat
            self._select_branch(BRANCH_PROJECT if key else BRANCH_CHAT, key, chat_id=chat_id,
                                bound=bool(key))
            # The notice belongs to the conversation it describes, not to whichever one is open.
            notice = ("Now reading " + Path(folder).name + " as context. This is still chat mode: "
                      "your next message is answered in prose and nothing is proposed." if key else
                      "This chat is on its own again — no project context is read.")
            self._add("tool", "Tool", notice)
        self._note("chat", "Moved a chat " + ("into " + Path(folder).name if key else "out of its project") + ".")

    def set_icon(self, key, icon) -> None:
        """Label a project with one of the palette marks; anything else is refused."""
        key = str(key or "")
        if key not in self.projects:
            self.status = "That project is not one you have granted access to."
            return
        if icon not in PROJECT_ICONS:
            self.status = "Choose one of the offered project marks."
            return
        self.icons[key] = "" if icon == DEFAULT_ICON else icon
        self._save_state()
        self.status = "Project mark updated."

    def new_chat_in(self, key) -> None:
        """The ＋ on a project row: a fresh chat that reads that folder, nothing else changes."""
        key = str(key or "")
        if key not in self.projects:
            self.status = "That project is not one you have granted access to."
            return
        self._select_branch(BRANCH_PROJECT, key, composer=CHAT_COMPOSER)
        self.status = "New chat in " + Path(self.repo).name + " — it reads that project and writes nothing."

    def set_repo(self, value: str) -> None:
        """Programmatic entry point for "work on this folder" — it selects a project branch too.

        ``browse`` and ``new_project`` deliberately land in chat mode; a caller that names a
        folder and asks for work on it gets the reviewed-change path.
        """
        raw = (value or "").strip()
        if not raw:
            self._select_branch(BRANCH_CHAT)
            return
        key = project_key(raw)
        try:
            resolved = str(Path(raw).resolve())
        except OSError:
            resolved = raw
        self._grant_folder(key, resolved)

    def project_changed(self, raw: str) -> None:
        if self._reverting:
            return
        identity = project_key(raw) if raw.strip() else ""
        if identity == self.current_project:
            return
        if not self._loading_session and self._draft.strip():
            previous = self.projects.get(self.current_project, "") if self.current_project else ""
            if not self.confirm("Switch project",
                                "Switching projects starts a new chat and clears your draft. Continue?"):
                self._reverting = True
                try:
                    self.repo = previous
                    self._sync_project()
                finally:
                    self._reverting = False
                return
        self.repo = raw.strip()
        self.current_project = identity
        self.cloud_ok = False
        if identity:
            self.projects[identity] = str(Path(self.repo).resolve())
        self._save_state()
        self._sync_project()
        self.refresh_recipes()
        if not self._loading_session:
            self.new_task()

    def browse(self) -> None:
        chosen = self.ask_directory("Select project folder",
                                    "The agent reads and proposes inside this folder only.")
        if not chosen:
            return
        key = project_key(str(chosen))
        self._grant_folder(key, str(Path(chosen).resolve()))
        self._adopted(Path(chosen).name)

    def new_project(self) -> None:
        if self.busy:
            return
        chosen = self.ask_directory("Choose a folder for the new project",
                                    "An empty folder, or one that does not exist yet.", mustexist=False)
        if not chosen:
            return
        try:
            folder = ensure_project_dir(chosen)
        except (AgentError, OSError) as exc:
            self.status = friendly_error(exc)
            return
        key = project_key(str(folder))
        self._grant_folder(key, str(Path(folder).resolve()))
        self.status = status_text("granted", arabic=self.arabic) + str(folder)
        self._adopted(Path(folder).name)

    def _adopted(self, name: str) -> None:
        """Granting a folder is a decision to build in it, so Send proposes from then on."""
        self._add("tool", "Tool", "Working in " + name + ". Describe what you want built or fixed: "
                  "Send proposes a diff, nothing is written until you click Apply. The badge by Send "
                  "switches back to Chat when you only want to ask.")

    def browse_plan(self) -> None:
        path = self.ask_plan_file()
        if not path:
            return
        if not self.repo:
            key = project_key(str(path.parent))
            self.projects.setdefault(key, str(Path(path.parent).resolve()))
            self._select_branch(BRANCH_PROJECT, key, chat_id=self.chat_id)
        try:
            reference = read_plan_reference(Workspace(Path(self.repo)), str(path), Settings())
        except (AgentError, OSError) as exc:
            self.status = friendly_error(exc)
            return
        self.plan_file = str(Path(self.repo) / reference["path"])
        # Attaching a plan is a decision to implement it, so this is the one path that selects
        # Change mode on the user's behalf instead of leaving prose as the default.
        self.set_composer(CHANGE_COMPOSER)
        self._save_state()
        self.status = ("Plan attached. Send starts its first unfinished step." if self.chained
                       else "Plan attached. Describe the phase, then press Send.")
        self.refresh_plan_status()

    def clear_plan(self) -> None:
        self.plan_file = ""
        self.ledger_path = self.ledger = None
        self.refresh_plan_status()

    def set_mode(self, value: str) -> None:
        if value not in MODES:
            return
        self.selections[self.active_mode] = self.model
        self.active_mode = self.mode = value
        self.model = self.selections.get(value, "")
        self.cloud_ok = False
        self.model_filter = ""
        self.subtitle = self._subtitle()
        if not self.catalogs.get(value):
            self.check_setup()

    # ------------------------------- connection -------------------------------
    def active_kind(self) -> config.Kind:
        return MODE_KIND.get(self.mode, config.DEFAULT_KIND)

    def endpoint_for(self, mode: str = "") -> str:
        """Where this provider row actually is — typed value, saved value, or its own default."""
        kind = MODE_KIND.get(mode or self.mode, config.DEFAULT_KIND)
        return self.endpoints.get(kind.key, "") or kind.base

    def set_endpoint(self, value: str) -> None:
        """Point the active row somewhere else. Refused loudly, never half-applied.

        An endpoint says where the code and the key go, so a typo must not be stored and discovered
        mid-task: the URL is checked on the way in, and a value that cannot be a valid base leaves
        the field exactly as it was. A change also drops that row's catalog — a list of models from
        the old address is not a list of models at the new one.
        """
        kind = self.active_kind()
        text = str(value or "").strip()
        if not text:
            self.endpoints.pop(kind.key, None)
        else:
            try:
                self.endpoints[kind.key] = config.check_endpoint(kind, text)
            except AgentError as exc:
                self.status = friendly_error(exc)
                return
        for label, row in MODE_ROWS:
            if row is kind:
                self.catalogs[label] = []
                self.catalog_source.pop(label, None)
        self.subtitle = self._subtitle()
        self._save_state()
        self.check_setup()

    def set_profile(self, label: str) -> None:
        """Adopt one ``profiles/*.toml``: provider row, model, endpoint and limits.

        The file names an environment variable, never a key, so choosing a profile cannot put a
        credential on disk — and the key field is cleared rather than filled, because the value it
        would need is not in the file.
        """
        self.profile = label
        try:
            settings = config.load_profile(label) if label else None
        except AgentError as exc:
            self.status = friendly_error(exc)
            self.profile = ""
            self._save_state()
            return
        if settings is not None:
            self.apply_connection(settings.provider, settings.endpoint, settings.model)
        self._save_state()

    def available_profiles(self) -> list[str]:
        return config.profile_names()

    def apply_connection(self, provider: str, endpoint: str, model: str = "") -> None:
        """Move the window onto another provider row without losing the model choice by accident."""
        mode = config.mode_for(provider, model)
        if not mode:
            self.status = friendly_error(AgentError(f"Unknown provider: {provider}"))
            return
        self.set_mode(mode)
        if endpoint:
            self.set_endpoint(endpoint)
        if model:
            self.selections[self.mode] = model
            self.model = model
            self._pending_model = ""
            self.model_changed()
        self.check_setup()

    def connection_info(self) -> dict:
        kind = self.active_kind()
        endpoint = self.endpoint_for()
        needs_consent = config.needs_consent(kind, endpoint)
        return {"kind": kind.key, "label": kind.label, "endpoint": endpoint,
                "default_endpoint": kind.base, "cloud": kind.cloud, "shape": kind.shape,
                "needs_key": kind.needs_key, "key_env": kind.key_env or "",
                "consent": needs_consent, "paid": self.mode.endswith(" \u00b7 Paid"),
                "profile": self.profile, "profiles": self.available_profiles(),
                "source": self.catalog_source.get(self.mode, ""),
                "key_present": bool(self.key.strip() or os.environ.get(kind.key_env or ""))}

    def cloud_choice(self) -> tuple[bool, bool]:
        """``(cloud, paid)`` for the row on screen — one answer, used by all three send paths.

        Two things can make a task leave the device: the provider row itself, and a model entry the
        catalog marked cloud (an Ollama "cloud" tag answers over the internet from a local URL).
        """
        kind = self.active_kind()
        entry = self.selected_entry()
        paid = kind.free_only and self.mode == paid_mode(kind)
        cloud = config.needs_consent(kind, self.endpoint_for()) or bool(entry and entry.get("cloud"))
        return cloud, paid

    def task_settings(self, cloud: bool) -> Settings | None:
        """The Settings for a task on the current row, or None with the reason on the status line.

        An endpoint is checked when it is typed, but a row can still be unusable — Custom with
        nothing in the field — and refusing here is what keeps a half-built Settings away from
        ``make_provider``, which would otherwise fail inside a worker thread.
        """
        kind = self.active_kind()
        try:
            return config.settings_for(kind, self.endpoint_for(), model=self.model,
                                       api_key_env=kind.key_env,
                                       max_turns=8 if cloud else 12,
                                       timeout_seconds=self.timeout_seconds())
        except AgentError as exc:
            self.status = friendly_error(exc)
            return None

    def set_model(self, value: str) -> None:
        self.model = value
        self.model_changed()

    def set_filter(self, value: str) -> None:
        # A view change, not a state change: nothing here is worth a disk write per keystroke,
        # and the filter is not the kind of thing you want restored next launch.
        self.model_filter = str(value or "")

    def set_recipe(self, label: str) -> None:
        self.recipe = label
        self._save_state()

    def set_chained(self, value: bool) -> None:
        self.chained = value
        self.refresh_plan_status()
        self._save_state()

    def set_timeout(self, value) -> None:
        # Unreadable input falls back to what is already on screen, so the field never jumps.
        self.request_timeout = config.clamp_request_timeout(value, self.request_timeout)
        self._save_state()

    def set_key(self, value: str) -> None:
        self.key = str(value or "")          # memory only; never written to disk

    def set_consent(self, value: bool) -> None:
        self.cloud_ok = bool(value)

    def timeout_seconds(self) -> int:
        return config.clamp_request_timeout(self.request_timeout)

    # ------------------------------ model catalog ------------------------------
    def selected_entry(self) -> dict | None:
        return next((entry for entry in self.catalogs.get(self.mode, []) if entry["id"] == self.model), None)

    def visible_models(self, mode: str | None = None) -> list[dict]:
        """The catalog as filtered — a view, never a mutation of what was loaded.

        ``filter_models`` used to write the subset back into ``self.catalogs``, so searching the
        list cost you every dropped entry until the next Refresh, and then cleared ``self.model``
        when the query stopped matching it: typing three letters could deselect the model running
        your task.
        """
        query = self.model_filter.strip().casefold()
        entries = self.catalogs.get(mode if mode is not None else self.mode, [])
        if not query:
            return list(entries)
        kept = [entry for entry in entries
                if query in " ".join([entry.get("id", ""), entry.get("name", ""),
                                      entry.get("description", "")]).casefold()]
        kept.sort(key=lambda entry: entry.get("id") != DEFAULT_MODEL)
        return kept

    def model_changed(self) -> None:
        self.selections[self.mode] = self.model
        self.subtitle = self._subtitle()
        self._save_state()

    def _model_info(self) -> str:
        entry = self.selected_entry()
        if entry:
            text = entry["name"] + " — " + entry["description"]
            if entry["id"] in RECOMMENDED:
                text += " · ★ " + RECOMMENDED[entry["id"]]
            return text
        loaded = len(self.catalogs.get(self.mode, []))
        shown = len(self.visible_models())
        if shown != loaded:
            return (f"{shown} of {loaded} models match \"{self.model_filter.strip()}\". "
                    "Clear the filter to see the rest.")
        return f"{loaded} models available at {self.endpoint_for()}. Select one from the list."

    def check_setup(self) -> None:
        """Ask the selected provider what it has. Read-only: no code leaves, no token is generated."""
        if self.busy:
            return
        selected_mode, api_key = self.mode, (self.key.strip() or None)
        kind = self.active_kind()
        endpoint = self.endpoint_for(selected_mode)

        def done(result):
            entries, source = result
            self.catalog_source[selected_mode] = source
            if kind.free_only:
                free, paid = config.free_mode(kind), config.paid_mode(kind)
                self.catalogs[free] = [entry for entry in entries if entry.get("free")]
                self.catalogs[paid] = [entry for entry in entries if not entry.get("free")]
            else:
                self.catalogs[selected_mode] = entries
            pending, self._pending_model = self._pending_model, ""
            catalog = self.catalogs.get(self.mode, [])
            if pending and not self.model and any(entry["id"] == pending for entry in catalog):
                self.model = pending
            elif not self.model and any(entry["id"] == DEFAULT_MODEL for entry in catalog):
                self.model = DEFAULT_MODEL
            self.model_changed()
            self.status = catalog_status_line(arabic=self.arabic, count=len(catalog),
                                              model=self.model, label=selected_mode,
                                              live=source == LIVE)
            self._note("catalog", f"{selected_mode}: {len(catalog)} models ({source}).")

        operation = lambda: models_for(kind, endpoint, api_key)
        self.run_job(operation, done, "Refreshing available models…")

    # ------------------------------- memory -------------------------------
    def _project_notes(self) -> str:
        if not self.repo:
            return ""
        try:
            return memory_store.read(self.memory_dir, self.repo)
        except AgentError:
            return ""

    def _memory_info(self) -> str:
        if not self.repo:
            return "Choose a project folder to edit its notes."
        saved = self._project_notes()
        return f"{len(saved)} chars saved · {memory_store.key_for(self.repo)}.md · sent with every task here"

    def project_info(self, key: str) -> dict:
        """Everything the per-project drawer shows, for any folder the user has granted.

        Walking the folder to measure its map is the slow part; it happens here, on the
        request thread, and §7's index cache makes the second opening instant.
        """
        root = self.projects.get(str(key))
        if not root:
            # A PolicyError, not a ValueError: friendly_error keeps the reason for an AgentError and
            # replaces anything else with "Could not complete the operation."
            raise PolicyError("Unknown project.")
        path = Path(root)
        exists = path.is_dir()
        notes = ""
        if exists:
            try:
                notes = memory_store.read(self.memory_dir, root)
            except AgentError:
                notes = ""
        return {"key": str(key), "name": path.name, "path": str(path), "exists": exists,
                "icon": self.icons.get(str(key), ""), "notes": notes,
                "notes_limit": memory_store.MAX_MEMORY,
                "notes_file": f"{memory_store.key_for(root)}.md",
                "notes_dir": str(self.memory_dir),
                "context": self._context_use(path, key, notes),
                "toolchain": self._toolchain_info(path, key, exists)}

    def _context_use(self, path: Path, key: str, notes: str) -> dict:
        """Map, conversation and what is left, against the configured context budget."""
        settings = Settings()
        blank = {"system": 0, "context": 0, "turns": 0, "kept": 0, "used": len(notes),
                 "budget": settings.context_chars, "remaining": settings.context_chars,
                 "est_tokens": 0, "map": 0, "notes": len(notes), "files": 0, "bound": False}
        if not path.is_dir():
            return blank
        try:
            repo = Workspace(path)
            text = repo.repo_map()
            files = len(repo.files(limit=symbols.MAX_FILES))
        except (PolicyError, OSError):
            return blank
        # Only a conversation that reads this folder can spend its budget here.
        chat = self.chat if self.branch.get("key") == key and self.chat else None
        context = context_block(text, notes) if text or notes else ""
        use = context_use(chat, settings, context)
        return {**use, "map": len(text), "notes": len(notes), "files": files,
                "bound": bool(chat)}

    def _toolchain_info(self, path: Path, key: str, exists: bool) -> dict:
        if not exists:
            return {"detected": [], "selected": "", "timeout": 0, "proof": "",
                    "request_timeout": self.request_timeout}
        detected = runner.detect(path)
        # ``self.recipe`` is a label chosen for the branch in front of the user; another
        # project's drawer can only report what would be picked by default.
        selected = self.selected_recipe() if self.branch.get("key") == key else None
        selected = selected or (detected[0] if detected else "")
        entry = runner.RECIPES.get(selected) or {}
        writes_report = bool(entry.get("reports") or entry.get("junit_arg"))
        return {"detected": [{"name": name, "label": runner.RECIPES[name]["label"],
                              "command": shlex.join(runner.RECIPES[name]["command"])}
                             for name in detected],
                "selected": selected,
                "timeout": runner.timeout_for(selected) if selected else 0,
                "proof": (entry.get("proof_source", "JUnit XML report") if writes_report
                          else "the command's own summary line") if selected else "",
                "request_timeout": self.request_timeout}

    def reveal(self, key: str) -> None:
        """Show a granted project folder in the file manager.

        The one deliberate exception to "nothing runs but the recipe allowlist": it starts a
        program with a fixed argv and no shell, and the path comes from the folder the user
        already granted — never from the browser, which only sends the key.
        """
        root = self.projects.get(str(key))
        if not root:
            self.status = "That folder is not one of the projects you opened."
            return
        path = Path(root)
        if not path.is_dir():
            self.status = "That project folder is not on this disk right now."
            return
        if os.name == "nt":
            command = ["explorer.exe", str(path)]
        elif sys.platform == "darwin":
            command = ["open", str(path)]
        else:
            command = ["xdg-open", str(path)]
        try:
            subprocess.Popen(command, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.status = "Could not open the folder: " + str(exc)[:120]
            return
        self.status = "Opened " + path.name + " in the file manager."

    def save_memory(self, text: str) -> None:
        if not self.repo:
            self.status = status_text("need_folder_notes", arabic=self.arabic)
            return
        try:
            memory_store.write(self.memory_dir, self.repo, str(text).rstrip("\n"))
        except (AgentError, OSError) as exc:
            self.status = friendly_error(exc)
            return
        self.status = "Project notes saved outside the project folder."
        self._note("memory", "Project notes updated.")

    # ------------------------------- planning -------------------------------
    def start_plan(self, text: str) -> None:
        if self.busy:
            # Not a mistake to swallow. The window's own busy flag is a race when two clicks land in
            # one tick — a second Send then reaches the server while the first is still starting — and
            # dropping it loses a written prompt with no message and no trace. The queue is the
            # mechanism that already means "said while a task was running".
            self.queue_add(text)
            return
        repo, task = self.repo.strip(), (text or "").strip()
        plan_file = self.plan_file.strip() or None
        entry = self.selected_entry()
        cloud, paid = self.cloud_choice()
        if repo and not Path(repo).is_dir():
            self.status = status_text("need_folder_exists", arabic=self.arabic)
            return
        # A new task means a new "before": an offer left over from the last one would restore the
        # wrong bytes for a path both tasks touched.
        self._task_commit, self._git_restore = "", {}
        if not self._draining:
            # A Send by hand is a new batch, even with an empty queue: "don't ask again" was answered
            # against work the operator was watching, and it does not travel to the next one.
            self._batch_fixes_off = False
        chained_plan = bool(repo and plan_file and self.chained)
        if (not task and not chained_plan) or len(task) > MAX_TASK_CHARS or not self.model:
            self.status = status_text("too_long", arabic=self.arabic)
            return
        if entry is None:
            self.status = status_text("pick_model", arabic=self.arabic)
            return
        if cloud and not self.cloud_ok:
            self.status = status_text("consent_message", arabic=self.arabic)
            return
        settings = self.task_settings(cloud)
        if settings is None:
            return
        key = self.key.strip() or None
        self._draft = ""
        # A bound project answers in prose by default: bound means it may *read* that project,
        # never that a greeting became a change request. A message that opens with "add" or
        # "صلح" is a different thing, and it is planned as a change — for this message only,
        # because remembering the route would turn the next "thanks" into a rejected diff.
        as_change = bool(repo) and self.composer == CHAT_COMPOSER and asks_for_a_change(task)
        if not repo or (self.composer == CHAT_COMPOSER and not as_change):
            self.start_chat(task, settings, cloud, paid, key)
            return
        if plan_file:
            try:
                read_plan_reference(Workspace(Path(repo)), plan_file, settings)
            except (AgentError, OSError) as exc:
                self.status = friendly_error(exc)
                return
        step_id = None
        if chained_plan:
            try:
                ledger_path, book = planbook.open_book(self.plans, Workspace(Path(repo)), plan_file)
                row = planbook.current(book)
                if row is None:
                    raise PolicyError("Every step of this plan is already verified by a passing run.")
                step_id = row["id"]
                task = planbook.task_for(book, row, task)
            except (AgentError, OSError) as exc:
                self.status = friendly_error(exc)
                return
            self.ledger_path, self.ledger = ledger_path, book
        prior = self._unverified_prior(self.chat_id, repo)
        if prior:
            note = ("The last task here ('" + str(prior.get("task", ""))[:60] + "') left files applied "
                    "without a passing command run (" + prior["state"] + ").")
            if prior["state"] in INTERRUPTED_STATES:
                if not self.confirm("Unfinished task",
                                    note + "\n\nContinue with a new task anyway? Rolling back that task "
                                    "first is the safer step.", ok_label="Continue anyway"):
                    self.status = note + " Roll it back or run its command, then start the new task."
                    return
                self._add("tool", "Tool", note + " Continuing on this state by your choice.")
            else:
                self._add("tool", "Tool", note + " Run its command (or roll it back) before stacking "
                          "more changes on top of it.")
                self.status = status_text("prior_unverified", arabic=self.arabic)
        self.title = task.replace("\n", " ")[:45]
        self._add("user", "You", task)
        if as_change:
            self._add("tool", "Tool", "This branch is in Chat mode, so the answer would have been "
                                      "prose. The message asks for a change, so it is planned as a "
                                      "proposal instead: review the diff, then Apply to write it. "
                                      "The next message is Chat again.")
        self.session = self.session_path = None
        notes = self._project_notes()
        if notes:
            self._add("tool", "Tool", "Your saved project notes (" + str(len(notes)) +
                      " characters) are part of this request.")
        chat_id = self.chat_id

        def work():
            provider = make_provider(settings, allow_cloud=cloud,
                                     data_class="public" if cloud else "restricted",
                                     api_key=key, allow_paid=paid)
            return plan(Workspace(Path(repo)), task, provider, settings, self.runs,
                        progress=lambda line: self._progress(line),
                        step=self._step,
                        cancelled=self.cancel_event.is_set, plan_file=plan_file, chat_id=chat_id,
                        plan_step=step_id, memory=notes)

        def done(path):
            if step_id is not None and self.ledger_path:
                try:
                    planbook.record_session(self.ledger_path, self.ledger, step_id, path.parent.name)
                except (AgentError, OSError) as exc:
                    self._add("tool", "Tool", "The plan ledger was not updated: " + friendly_error(exc))
            self.display_session(path)
            if self.session and self.session.get("changes"):
                self.status = status_text("proposal_ready", arabic=self.arabic)
                self._emit({"kind": "view", "value": "preview"})
                self.auto_apply_ready()
            else:
                self.status = status_text("no_proposal", arabic=self.arabic)

        # A status set here is overwritten the moment the job starts, so the routing notice
        # rides along as the running line — which is also the entry the log keeps.
        running = ("⚡ Switched to Change mode to propose and write file edits…" if as_change
                   else "Connecting to the model and preparing changes…")
        self.run_job(work, done, running, cancellable=True, on_busy=lambda: self.queue_add(task))

    def _progress(self, line: str, *, record: bool = True) -> None:
        self.pending = line
        self._last_progress = line
        # `status` is the field the strip draws from the snapshot, so a state push mid-run cannot
        # erase what the window just said; the event is only there to make it arrive instantly.
        self.status = line
        if record:
            self._note("progress", line)
        self._emit({"kind": "status", "text": line})

    def _step(self, line: str, step_id: str = "", action: str = "",
              fields: dict | None = None) -> int:
        """One tool action, announced in the conversation as a row that can be opened.

        `engine.plan` announces an action through `progress` and `step` with the same line, so the
        strip and the stored log already have their row when this runs: this adds the conversation row
        and moves the status, and records no log row of its own. Two log rows for one action used to
        double the history of every tool call.

        Consecutive repeats collapse because a small model reads the same file twice in a row often
        enough to fill the thread with one sentence, and a row that says nothing new costs the reader
        the same attention as one that does.

        `action` and `fields` are what the row opens to. A row whose action has no stored detail of its
        own gets no chevron — an expander that opens onto nothing is worse than no expander. Returns
        the index of the row it added, or -1 when the line collapsed into the one before it.
        """
        index = -1
        if line != self._last_step:
            self._last_step = line
            self._add("tool", "Steps", line, step={
                "id": step_id or uuid.uuid4().hex[:8], "action": action,
                "fields": dict(fields or {}),
                "detail": step_has_detail(action, fields)})
            index = len(self.messages) - 1
        self._progress(line, record=False)
        return index

    def _settle_run_step(self, result: dict) -> None:
        """Turn the "Executing …" row into what actually came back.

        That row is the only place the command was named before it ran, and it is the row the user
        will look for afterwards. Left in the present tense it reads as a build still going, which is
        how a finished red run gets waited on.
        """
        index, self._run_step = self._run_step, -1
        if not 0 <= index < len(self.messages):
            return
        message = self.messages[index]
        step = message.get("step") or {}
        if step.get("action") != "executing":
            return
        step["action"] = "executed"
        step["detail"] = step_has_detail("executed", step.get("fields"))
        message["text"] = executed_line(
            arabic=self.arabic, command=str(step.get("fields", {}).get("command", "")),
            verdict=run_verdict(self.arabic, result))

    def open_step(self, step_id: str) -> None:
        """Open one step row, or close every one of them when the id is empty.

        The detail is fetched rather than shipped. A stored run tail alone is 2 500 characters, and a
        task with a build per turn would carry all of them on every later snapshot — the same reason
        streamed output never reaches the server at all.
        """
        self.step_detail = None
        if not step_id:
            return
        missing = {"id": step_id, "sections": [], "files": [],
                   "note": step_missing_line(arabic=self.arabic)}
        for message in reversed(self.messages):
            step = message.get("step") or {}
            if step.get("id") == step_id:
                block = self._detail_for(step)
                # A row that cannot answer still answers in words: a click that does nothing is the
                # dead-button failure this window already had once.
                self.step_detail = block or missing
                return
        # A row the server has never heard of means the page is behind the task.
        self.step_detail = missing

    def _detail_for(self, step: dict) -> dict | None:
        """The inside of one row, from the records this session already keeps."""
        action = str(step.get("action", ""))
        fields = step.get("fields") or {}
        session = self.session or {}
        sections: list[list] = []
        files: list[str] = []
        if action in {"executing", "executed"} or fields.get("command"):
            command = str(fields.get("command", ""))
            runs = session.get("runs") or []
            run = next((item for item in reversed(runs) if item.get("command") == command),
                       runs[-1] if runs else None)
            if run is None:
                # The command ran and no session could hold its record — the D28 case, where the task
                # before it blocked or rolled back. The row still answers, with the sentence that says
                # why there is nothing else inside.
                return {"id": step.get("id", ""), "sections": [], "files": [],
                        "note": run_unrecorded_line(arabic=self.arabic,
                                                    project=Path(self.repo or ".").name)}
            sections = [[detail_section(self.arabic, "command"), [str(run.get("command", ""))]],
                        [detail_section(self.arabic, "result"),
                         [run_verdict(self.arabic, run),
                          str(run.get("label") or run.get("recipe") or "")]],
                        [detail_section(self.arabic, "problems"),
                         [str(row)[:300] for row in run.get("failures") or []]],
                        [detail_section(self.arabic, "output"),
                         (str(run.get("tail") or "")).splitlines()]]
        elif action in {"propose", "applied"}:
            files = [str(name) for name in fields.get("names") or []]
            if not files:
                return None
            sections = []
        elif action == "search_code":
            sections = [[detail_section(self.arabic, "search"),
                         [str(fields.get("query", "")),
                          f"{fields.get('count', 0)} match(es)"]]]
        elif action == "list_files":
            sections = [[detail_section(self.arabic, "files"),
                         [f"{fields.get('count', 0)} file(s) in the folder"]]]
        elif action == "read_file":
            # What a read row can add over its own sentence is *which version* the model saw — the
            # digest is the difference between "it read the file" and "it read the file as it stood
            # before the last write". Without it there is nothing to open, so no chevron.
            path = str(fields.get("path", ""))
            digest = next((str(item.get("sha256", ""))[:8]
                           for item in reversed(session.get("events") or [])
                           if item.get("kind") in {"tool", "auto_read"}
                           and item.get("path") == path), "")
            if not digest:
                return None
            sections = [[detail_section(self.arabic, "read"), [path, "sha256 " + digest]]]
        else:
            return None
        sections = [[title, [row for row in rows if str(row).strip()]]
                    for title, rows in sections]
        sections = [[title, rows] for title, rows in sections if rows]
        if not sections and not files:
            return None
        return {"id": step.get("id", ""), "sections": sections, "files": files}

    def _build_line(self, line: str) -> None:
        """One line of build output, streamed while the command is still running.

        Redacted here rather than at display time: a test that prints its own connection
        string would otherwise reach the browser intact. These lines go on the SSE channel
        only — the stored log keeps the run's summary, so a 5 000-line build cannot bloat
        every later snapshot.
        """
        text = redact(line).strip()[:500]
        if not text:
            return
        self.pending = text
        self._emit({"kind": "log_chunk", "ts": _clock(), "text": text})

    def start_chat(self, task: str, settings, cloud: bool, paid: bool, key: str | None) -> None:
        """Prose answer. With a bound project it may read that folder's map; never write to it."""
        if self.chat is None or self.chat.get("id") != self.chat_id:
            self.chat = create_chat(self.model or Settings().model, self.chat_id,
                                    project=self._chat_project())
        chat = self.chat
        self.session = self.session_path = None
        self.title = chat.get("title") or task.replace("\n", " ")[:45]
        self._add("user", "You", task)
        repo = self.repo

        def work():
            provider = make_provider(settings, allow_cloud=cloud,
                                     data_class="public" if cloud else "restricted",
                                     api_key=key, allow_paid=paid)
            return respond(chat, provider, task, settings, self.chats, context=self._chat_context(repo))

        def done(reply):
            self._add("assistant", "AI Code Engineer", reply)
            self.title = title_for(chat)
            self.status = ("Answered. Switch to Change mode when you want reviewed changes to these files."
                           if repo else
                           "Answered. Choose a project when you want reviewed changes to real files.")

        self.run_job(work, done, "Thinking…", on_busy=lambda: self.queue_add(task))

    def _chat_project(self) -> dict | None:
        """What a new chat records as its home: the selected project, or nothing at all."""
        key = self.branch.get("key") or ""
        if self.branch.get("kind") != BRANCH_PROJECT or not key or not self.repo:
            return None
        return {"key": key, "path": str(Path(self.repo).resolve())}

    def _chat_context(self, repo: str) -> str:
        """The repository map and standing notes, gathered here so a chat can read without tools.

        Building the map means walking the folder, which is why the caller does it on the worker
        thread. A project that cannot be read answers with no context rather than an error: a
        question about a folder is not a reason to fail the question.
        """
        if not repo or not Path(repo).is_dir():
            return ""
        try:
            return context_block(Workspace(Path(repo)).repo_map(), self._project_notes())
        except (AgentError, OSError):
            return ""

    def stop(self) -> None:
        if self.busy and self.cancellable:
            self.cancel_event.set()
            self.status = "Stop requested. Waiting for the current model request to finish."
            # A queue that carried on the instant this task ended would make the button mean
            # "skip to the next one". It holds; ▶ in the strip resumes it.
            self._queue_held = True

    def _unverified_prior(self, chat_id: str, repo: str) -> dict | None:
        try:
            turns = chat_sessions(self.runs, repo, chat_id)
        except (AgentError, OSError):
            return None
        for _, item in reversed(turns):
            if item.get("state") in UNVERIFIED_STATES:
                return item
        return None

    # --------------------------- apply / check / undo ---------------------------
    def removal_notice(self, session: dict | None = None) -> str:
        return repair.removal_notice(session if session is not None else self.session)

    def offer_block(self, payload: dict) -> None:
        """Turn one code block from an answer into a proposal, exactly like a model would.

        The browser sends the file name it saw in the block's header and the text it is
        already showing; neither is trusted. The path goes through the same workspace
        policy as a model's proposal, the content is re-checked for syntax and size, and
        what comes back is a WAITING_APPROVAL session — so the Apply dialog, the hash
        guard and rollback all mean what they usually mean.
        """
        if self.busy or not self.repo:
            self.status = "Choose a project before applying a block to a file."
            return
        name = str(payload.get("path", ""))[:240]
        content = str(payload.get("content", ""))
        task = ("Write " + name + " from a code block in the chat answer")[:MAX_TASK_CHARS]
        repo, chat_id, model = self.repo, self.chat_id, self.model

        def work():
            try:
                return propose_block(Workspace(Path(repo)), task, name, content, self.runs,
                                     chat_id=chat_id, model=model or "user")
            except PolicyError as exc:
                # friendly_error turns a bare "unchanged file" into advice about the Chat badge.
                # That is right for a message and nonsense for a block the user just clicked, so
                # the refusal is re-said in block terms before it reaches the status line.
                reason = str(exc)
                reason = ("it holds the same text the file already holds"
                          if "unchanged file" in reason else reason)
                raise AgentError("That block could not become a proposal: " + reason) from None

        def done(path):
            self.display_session(path)
            self.status = ("That block is a proposal now. Review the diff, then Apply to write it.")
            self._emit({"kind": "view", "value": "preview"})

        self.run_job(work, done, "Turning that block into a reviewed proposal...")

    def apply(self) -> None:
        if self.busy or not self.session or self.session.get("state") != "WAITING_APPROVAL":
            return
        changes = self.session.get("changes", [])
        again = ""
        if self._auto_fix and self.selected_recipe():
            again = ("\nIt will then run " + runner.RECIPES[self.selected_recipe()]["label"] +
                     " again in that folder, which executes the project's own build and test code.")
        notice = self.removal_notice()
        prior = self._unverified_prior(self.chat_id, self.repo or "")
        # `must_ask` is the whole refusal rule, and it is the same one the switch's own tooltip
        # describes: an emptying proposal, or a folder a previous task left half-written.
        reason = repair.must_ask(self.session, prior)
        self.wrote_without_asking = bool(self.auto_apply and not reason)
        self.auto_banner = len(changes) if self.wrote_without_asking else 0
        if not self.wrote_without_asking:
            prompt = host.apply_prompt(self.session, notice=notice, reason=reason, again=again)
            if not self.confirm(prompt["title"], prompt["message"], prompt["warning"],
                                prompt["ok_label"]):
                return
        path, approved = self.session_path, self.session["proposal_hash"]
        self.run_job(lambda: apply_proposal(path, approved), self._applied, "Applying the changes you reviewed…")

    def auto_apply_ready(self) -> None:
        """Write a proposal the moment it lands, for a folder whose switch is on.

        Called by the jobs that can produce a proposal. It is deliberately not wired to the
        Apply-to-File button: that click already is the request, and what Auto-Apply removes
        is the click that follows a model's work. `apply` still asks when a proposal empties
        an existing file, so the switch never writes something it would have to explain.
        """
        if self.auto_apply and self.session and self.session.get("changes"):
            self.apply()

    def _checkpoint(self) -> None:
        """Commit the files this task just wrote, when that folder is under git anyway.

        The commit is the last step of a write that already happened, so a failure here is
        reported and forgotten: the change is on disk, and the in-session rollback is
        unaffected. Only the proposal's own paths are staged — an unrelated edit in the
        same working tree is nobody's business to commit.
        """
        if not self.repo or not self.session:
            return
        if not git_integration.status(self.repo)["repo"]:
            return                       # not a git folder: nothing to say about a checkpoint
        paths = [change.get("path", "") for change in self.session.get("changes", [])]
        outcome = git_integration.checkpoint(self.repo, self.session.get("task", ""),
                                             self.session.get("id", ""), paths)
        if outcome["ok"]:
            # The parent of this commit is the only hash that means "before this task" for these
            # paths, and it is what a later refused rollback can offer to restore from.
            self._task_commit = outcome["before"]
            self.status = ("Applied, and committed " + str(len(paths)) +
                           " file(s) to git as " + outcome["hash"] +
                           ". Roll back from this card, or with git.")
            self._add("tool", "Tool",
                      checkpoint_note(arabic=self.arabic, count=len(paths), sha=outcome["hash"]))
        else:
            self._add("tool", "Tool",
                      no_checkpoint_note(arabic=self.arabic, reason=outcome["reason"][:200]))

    def _applied(self, _result) -> None:
        git_integration.forget()
        self.display_session(self.session_path)
        self._checkpoint()
        automatic, self.wrote_without_asking = self.wrote_without_asking, False
        count = len(self.session.get("changes", [])) if self.session else 0
        removed = sum(1 for change in ((self.session or {}).get("changes") or [])
                      if change.get("delete"))
        if self._batch_row is not None:
            # The batch ledger counts what landed, not what was asked: a fix round that writes the
            # same path twice is one file in the user's folder, and the summary dedupes on read.
            self._batch_row["paths"].extend(change["path"] for change in
                                            ((self.session or {}).get("changes") or []))
        if not automatic:
            # The click happened, and this is the record of what it did. Before this the only trace
            # of a manual apply in the conversation was the git row, so a folder outside git looked
            # like nothing had happened at all. It carries a step handle so the row opens onto the
            # files it wrote — the paths are the one thing the sentence itself leaves out.
            written = [change["path"] for change in ((self.session or {}).get("changes") or [])]
            self._add("tool", "Tool", applied_line(arabic=self.arabic, count=count, removed=removed),
                      step={"id": uuid.uuid4().hex[:8], "action": "applied",
                            "fields": {"count": count, "names": written},
                            "detail": step_has_detail("applied", {"names": written})})
        if automatic:
            self.status = ("\u2705 Applied changes to " + str(count) +
                           " file(s) automatically. Roll back from this card.")
            self.auto_banner = count
            # The notice carries the model's own summary: for a write nobody clicked for, that is
            # the first place the user reads what actually happened to their files.
            lines, total, rewrote = diff_size((self.session or {}).get("changes") or [])
            self._add("tool", "Tool", write_notice(arabic=self.arabic, count=count,
                                                   summary=(self.session or {}).get("summary") or "",
                                                   lines=lines, total=total, rewrote=rewrote))
            if not self._auto_fix:
                # A write nobody clicked for still has to be followed by the run that a clicked
                # apply would have had. The branch's selection can be empty while the folder does
                # have a command the tool detected — clearing it is a real state, and every card
                # after it said "tests have not run". Fall back to the detected command instead of
                # skipping the check: runner.detect() listed it, so nothing here is invented.
                if self.recipes:
                    if self.selected_recipe() is None:
                        self.recipe = runner.RECIPES[self.recipes[0]]["label"]
                    self.run_tests(False)
                    return
                self.status = status_text("applied_no_command", arabic=self.arabic)
                return
        if self._auto_fix:
            self.status = status_text("applied_rerun", arabic=self.arabic)
            self.run_tests(False)
            return
        self.status = status_text("applied_idle", arabic=self.arabic)

    def check_changes(self) -> None:
        if self.busy or not self.session or self.session.get("state") not in MUTABLE_STATES:
            return
        path = self.session_path

        def done(result):
            self.display_session(path)
            self.status = ("Syntax check found a problem. Open the Checks tab for details."
                           if result["status"] == "failed" else
                           "Syntax checks finished. Project tests have not run; verification remains incomplete.")

        self.run_job(lambda: verify(path), done, "Checking syntax in changed files…")

    def undo(self) -> None:
        if self.busy or not self.session or self.session.get("state") not in MUTABLE_STATES | {"PARTIAL_APPLY", "APPLYING"}:
            return
        if not self.confirm("Roll back changes",
                            "Restore this task's files to their previous contents?\nLater edits will block "
                            "rollback to protect your work.", ok_label="Roll back"):
            return
        path, approved = self.session_path, self.session["proposal_hash"]
        # The refusal this can answer is the one about a later edit, and only where this program made
        # a commit that means "before this task". With nothing to escalate to, the old path is kept
        # exactly as it was: the failure row is the right answer, not a dead end with a button on it.
        escalatable = bool(self._task_commit and self.repo and self.session.get("changes"))

        def operation():
            if not escalatable:
                return rollback(path, approved)
            try:
                return rollback(path, approved)
            except PolicyError as exc:
                if "Rollback would overwrite a later edit" not in str(exc):
                    raise
                return {"later_edit": friendly_error(exc)}

        def done(result):
            if isinstance(result, dict) and result.get("later_edit"):
                self.status = "Rollback refused: the files changed after this task wrote them."
                self._git_restore = {"commit": self._task_commit, "paths": [
                    change.get("path", "") for change in self.session.get("changes", [])]}
                self._add("tool", "Tool", restore_offer(
                    arabic=self.arabic, commit=self._task_commit,
                    count=len(self._git_restore["paths"])))
                return
            git_integration.forget()
            self.auto_banner = 0
            self.display_session(path)
            self.status = status_text("rolled_back", arabic=self.arabic)
            self._git_restore = {}
            step_id = self.session.get("plan_step") if self.session else None
            pair = self.ledger_for(self.session) if step_id is not None and self.session else None
            if pair:
                try:
                    planbook.reopen(pair[0], pair[1], step_id)
                except (AgentError, OSError) as exc:
                    self.status = status_text("ledger_needs_look", arabic=self.arabic) + friendly_error(exc)
                    return
                self.refresh_plan_status()
                self._add("tool", "Tool", "Reopened plan step " + str(step_id) +
                          ": the files that passed are gone, so the step must be implemented again.")

        self.run_job(operation, done, "Rolling back changes…")

    def git_restore(self) -> None:
        """Put this task's own files back to the commit made before it wrote them.

        This is the escalation, not a second undo, and the two cannot disagree about anything: it only
        appears after the session rollback refused, it can only name the files that proposal wrote, and
        it only knows a hash this program committed itself. `git reset` is not used at any point — it
        would take the rest of the working tree with it, including work no proposal ever proposed.
        """
        offer = dict(self._git_restore or {})
        paths = [name for name in offer.get("paths", []) if name]
        if self.busy or not paths or not self.repo:
            return
        commit = offer.get("commit", "")
        if not self.confirm("Restore these files from git",
                            "Replace what is in " + str(len(paths)) + " file(s) now with the copy in"
                            " commit " + commit + "?\nThat overwrites any edit made after the task"
                            " wrote them. Nothing outside this list is touched.",
                            ok_label="Restore " + str(len(paths)) + " file(s)"):
            return

        def done(outcome):
            self._git_restore = {}
            restored, skipped = outcome.get("restored", []), outcome.get("skipped", [])
            git_integration.forget(self.repo)
            self.display_session(self.session_path)
            if not outcome.get("ok"):
                self.status = "git restored nothing."
                self._add("tool", "Tool",
                          no_branch_note(arabic=self.arabic,
                                         reason=str(outcome.get("reason") or "git said nothing")[:200]))
                return
            self.status = ("Restored " + str(len(restored)) + " file(s) from " + commit + ".")
            self._add("tool", "Tool", restore_done(arabic=self.arabic, commit=commit,
                                                   restored=restored, skipped=skipped))

        self.run_job(lambda: git_integration.restore_paths(self.repo, commit, paths), done,
                     "Asking git for the earlier copies…")

    def git_branch(self, back: str = "") -> None:
        """Start a git branch for this task, or return to the branch this window came from.

        Moving HEAD is the largest claim this window can make on a working tree, so it happens only
        on this click, only after a confirmation that names the branch, and only through
        `git_integration`'s two non-destructive forms: `checkout -b`, which carries the uncommitted
        work with it, and an unforced switch, which git refuses rather than overwrite. The reason to
        offer it at all is a folder where every milestone is committed — without a branch of its own,
        that history lands on whatever branch the developer happened to be standing on.
        """
        if self.busy or not self.repo:
            return
        states = git_integration.status(self.repo)
        if not states["repo"]:
            self.status = "This folder is not a git repository, so there is no branch to change."
            return
        target = str(back or "").strip()
        task = (self.session or {}).get("task") or self._draft
        tail = (self.session or {}).get("id") if self.session else self.chat_id
        name = git_integration.task_branch_name(task, tail or self.chat_id)
        if target:
            ask, body, ok = ("Return to a git branch",
                             "Move HEAD back to " + target + "?\nNothing is forced: git refuses if a"
                             " file here would be overwritten, and that refusal is reported.",
                             "Go to " + target)
        else:
            ask, body, ok = ("Start a git branch for this task",
                             "Create and switch to " + name + "?\nThe uncommitted work moves with it,"
                             " and this task's commits land there instead of on " +
                             (states["branch"] or "a detached HEAD") + ".",
                             "Create branch")
        if not self.confirm(ask, body, ok_label=ok):
            return

        def done(outcome):
            reason = str(outcome.get("reason") or "")[:200]
            if not outcome.get("ok"):
                self.status = "git did not change the branch."
                self._add("tool", "Tool",
                          no_branch_note(arabic=self.arabic, reason=reason or "git said nothing"))
                return
            moved = outcome.get("branch") or ""
            if outcome.get("created"):
                self._git_base = outcome.get("from") or ""
            if moved == self._git_base:
                self._git_base = ""          # back where we started, so there is nothing to return to
            self.status = ("On " + moved + " — this task's commits land here."
                           if outcome.get("created") else "On " + moved + ".")
            if outcome.get("created"):
                text = branch_started(arabic=self.arabic, branch=moved,
                                      back=outcome.get("from") or "")
            elif reason.startswith("already on"):
                text = no_branch_note(arabic=self.arabic, reason=reason)
            else:
                text = branch_switched(arabic=self.arabic, branch=moved)
            self._add("tool", "Tool", text)

        self.run_job((lambda: git_integration.switch_branch(self.repo, target)) if target
                     else (lambda: git_integration.start_task_branch(self.repo, task, tail or self.chat_id)),
                     done, "Asking git about branches…")

    def refresh_recipes(self) -> None:
        try:
            self.recipes = runner.detect(Path(self.repo)) if self.repo and Path(self.repo).is_dir() else []
        except OSError:
            self.recipes = []
        labels = [runner.RECIPES[name]["label"] for name in self.recipes]
        if self.recipe not in labels:
            self.recipe = labels[0] if labels else ""

    def selected_recipe(self) -> str | None:
        labels = [runner.RECIPES[name]["label"] for name in self.recipes]
        try:
            return self.recipes[labels.index(self.recipe)]
        except ValueError:
            return None

    # The only states that lock the Run control: work in front of the user that has not been written
    # yet. Every other state -- including a task that blocked, cancelled or rolled back, and a folder
    # with no task at all -- has files on disk that a project command can still be pointed at, which
    # is what D28 was about: the build used to be unreachable exactly when the last task went wrong.
    RUN_LOCKED_STATES = {"DISCOVERING", "WAITING_APPROVAL"}

    def _can_run(self) -> bool:
        state = self.session.get("state") if self.session else ""
        return (not self.busy and bool(self.recipes) and bool(self.repo)
                and Path(self.repo).is_dir() and state not in self.RUN_LOCKED_STATES)

    def run_tests(self, auto_fix: bool = False) -> None:
        if self.busy:
            return
        state = self.session.get("state") if self.session else ""
        if state in self.RUN_LOCKED_STATES or not (self.session or self.repo.strip()):
            self.status = status_text("apply_first", arabic=self.arabic)
            return
        recipe = self.selected_recipe()
        if recipe is None:
            self.status = status_text("no_recipe", arabic=self.arabic)
            return
        self._auto_fix = bool(auto_fix)
        if auto_fix:
            self._fix_round = 0
        # The folder comes from the task when there is one and from the window when the last task
        # blocked or rolled back. A run is recorded on the session only when that session can hold
        # it -- `repair.record_run` refuses any state outside its own set, and writing into one of
        # those would rewrite a finished task's verdict.
        repo = self.session["root"] if self.session else self.repo.strip()
        path = self.session_path
        recordable = bool(path) and state in MUTABLE_STATES
        label = runner.RECIPES[recipe]["label"]
        # Say the command before running it, not only after it fails: a project's own build
        # executes code the repository defines, and this is the last line worth reading first.
        # The interpreter is named, not pathed, because the absolute path is noise here and the
        # argv the child actually gets is recorded in the log.
        command = " ".join("python" if part == sys.executable else str(part)
                           for part in runner.RECIPES[recipe]["command"])
        self._run_step = self._step(executing_line(arabic=self.arabic, command=command),
                                    action="executing", fields={"command": command})

        def work():
            result = runner.run(Path(repo), recipe, timeout=runner.timeout_for(recipe),
                                progress=self._build_line)
            return (repair.record_run(path, result) if recordable else None), result

        def done(pair):
            if pair[0] is not None:
                self.display_session(path)
            self.report_run(pair[1])
            if pair[0] is None:
                # A run that no session can hold is still a run the operator asked for and paid
                # for; saying nothing would leave the folder's state to a scrollback line.
                self._add("tool", "Checks",
                          run_unrecorded_line(arabic=self.arabic, project=Path(repo).name))

        self.run_job(work, done, f"Running {label} in {Path(repo).name}…")

    def report_run(self, result) -> None:
        summary = runner.summarize(result)
        self.run_info = summary
        self._settle_run_step(result)
        if result["status"] == "passed":
            self._add("tool", "Checks", "✅ " + summary)
            self._auto_fix = False
            self.status = status_text("command_passed", arabic=self.arabic) + summary
        else:
            # A tool row is plain text — the thread escapes it and never runs markdown — and the
            # pill collapses newlines, so this line has to read as one sentence with no markup.
            # And this row is built from the raw runner result rather than the slimmed record the
            # session stores, and `runner` does not scrub: the two other paths that show command
            # output redact it (`repair.record_run` for storage and the model, `_build_line` for the
            # stream). A test that prints its own connection string must not find the chat to do it
            # in — every row here can be copied out with one click.
            msg = "❌ " + summary + " — command: " + redact(str(result.get("command", "")))
            failures = result.get("failures") or []
            if failures:
                msg += (" · Errors: " + " | ".join(redact(str(f).strip())
                                                   for f in failures[:repair.FAILURES_KEPT]))
            elif result.get("reason"):
                msg += " · Reason: " + redact(str(result["reason"]))
            elif result.get("tail"):
                err_lines = [l.strip() for l in result["tail"].strip().splitlines()
                             if "[ERROR]" in l or "Error" in l or "Exception" in l]
                sample = err_lines[-6:] or [l.strip() for l in result["tail"].strip().splitlines()][-6:]
                if sample:
                    msg += " · Output: " + " | ".join(redact(line) for line in sample)
            msg += " · The full output is in Activity."
            self._add("tool", "Checks", msg)
            if self._auto_fix and result["status"] in {"failed", "timeout"}:
                self.ask_for_fix(result)
                return
            self._auto_fix = False
            self.status = summary + " — the captured output is in the Checks tab."
            if result["status"] in {"failed", "timeout"} and self._offer_fix(result):
                return
        self.advance_plan(result)

    def _offer_fix(self, run) -> bool:
        """Ask before spending a turn. Continuing used to be a status line to interpret.

        Nothing is written here either: the round produces a proposal, and Apply stays a
        separate, deliberate click.
        """
        if self._fix_round >= repair.MAX_FIX_ROUNDS or not self.model:
            return False
        if not self.session or self.session.get("state") not in MUTABLE_STATES:
            # A fix round repairs *a task's* proposal, so it needs one to sit on. The run itself no
            # longer does — that is D28 -- and offering to fix what cannot be recorded would send the
            # model a task with no folder to change.
            return False
        if self._batch_fixes_off:
            # Suppressed, but never silently: the failure line above is the only other evidence the
            # tool saw this at all.
            self._add("tool", "Tool", fix_offers_off_line(arabic=self.arabic))
            return False
        answer = self.confirm_choice(repair.FIX_OFFER_TITLE,
                                     repair.fix_offer(self.model, self._fix_round + 1, self.auto_apply),
                                     ok_label="Run the fix round", alt_label=repair.FIX_OFFER_ALT)
        if answer.get("alt"):
            self._batch_fixes_off = True
            self._add("tool", "Tool", fix_offers_off_line(arabic=self.arabic))
            return False
        if not answer.get("ok"):
            return False
        self._auto_fix = True
        self.ask_for_fix(run)
        return True

    def ask_for_fix(self, run) -> None:
        if self._fix_round >= repair.MAX_FIX_ROUNDS:
            self._auto_fix = False
            self.status = (f"Stopped after {repair.MAX_FIX_ROUNDS} fix rounds and the command still fails. "
                           "Try a narrower task, another model, or inspect the output in Checks.")
            return
        cloud, paid = self.cloud_choice()
        if cloud and not self.cloud_ok:
            self._auto_fix = False
            self.status = status_text("consent_project", arabic=self.arabic)
            return
        settings = self.task_settings(cloud)
        if settings is None:
            self._auto_fix = False
            return
        self._fix_round += 1
        repo, chat_id = self.session["root"], self.chat_id
        reference, step_id = self.session.get("plan_reference"), self.session.get("plan_step")
        plan_file = str(Path(repo) / reference["path"]) if reference and step_id is not None else None
        task, evidence = repair.fix_task(run), repair.evidence(run)
        notes = self._project_notes()
        status = (f"Fix round {self._fix_round}/{repair.MAX_FIX_ROUNDS}: asking {settings.model} "
                  "for the smallest change that makes the command pass…")

        def work():
            provider = make_provider(settings, allow_cloud=cloud,
                                     data_class="public" if cloud else "restricted",
                                     api_key=self.key.strip() or None, allow_paid=paid)
            return plan(Workspace(Path(repo)), task, provider, settings, self.runs,
                        progress=lambda line: self._progress(line),
                        step=self._step, cancelled=self.cancel_event.is_set,
                        chat_id=chat_id, extra_context=evidence, plan_file=plan_file,
                        plan_step=step_id if plan_file else None, memory=notes)

        def done(path):
            self.display_session(path)
            if plan_file:
                try:
                    pair = self.ledger_for(self.session)
                    if pair:
                        planbook.record_session(pair[0], pair[1], step_id, path.parent.name)
                except (AgentError, OSError) as exc:
                    self._add("tool", "Tool", "The plan ledger was not updated: " + friendly_error(exc))
            self._emit({"kind": "view", "value": "preview"})
            self.status = status_text("fix_ready", arabic=self.arabic)
            self.auto_apply_ready()

        self.run_job(work, done, status, cancellable=True)

    # ------------------------------ plan ledger ------------------------------
    def ledger_for(self, session: dict) -> tuple[Path, dict] | None:
        reference, step_id = session.get("plan_reference"), session.get("plan_step")
        if not reference or step_id is None:
            return None
        try:
            path, book = planbook.open_book(self.plans, Workspace(Path(session["root"])), reference["path"])
        except (AgentError, OSError):
            return None
        if book.get("plan_sha256") != reference["sha256"]:
            return None
        self.ledger_path, self.ledger = path, book
        return path, book

    def advance_plan(self, result) -> None:
        session = self.session
        if session is None:
            return
        pair = self.ledger_for(session)
        if pair is None:
            return
        path, book = pair
        step_id = session["plan_step"]
        row = planbook.step(book, step_id)
        if row is None or row["status"] == "verified":
            return
        if result["status"] != "passed":
            self.status = (planbook.progress_line(book) + " — step " + str(step_id) +
                           " stays open until a command run proves it.")
            return
        try:
            planbook.complete(path, book, step_id, session)
        except (AgentError, OSError) as exc:
            self._add("tool", "Tool", "The command passed, but step " + str(step_id) +
                      " is not marked done: " + friendly_error(exc))
            self.status = "Step " + str(step_id) + " still open — see the conversation."
            return
        self.refresh_plan_status()
        nxt = planbook.current(book)
        self._add("tool", "Tool", f"Plan step {step_id}/{len(book['steps'])} verified. " + runner.summarize(result))
        if nxt is None:
            self.status = f"Plan complete — {len(book['steps'])} steps verified."
            return
        if self.chained and self.composer == CHANGE_COMPOSER:
            self.start_plan("")
        elif self.chained:
            self._draft = planbook.task_for(book, nxt)
            self.status = (f"Step {step_id} verified. Switch the badge to Change mode to let the "
                           "next step propose its work.")
        elif self._offer_next_step(book, nxt, step_id):
            return
        else:
            self._draft = planbook.task_for(book, nxt)
            self.status = (f"Step {step_id} verified. The next step is ready in the message box — press Send.")

    def _offer_next_step(self, book: dict, nxt: dict, done_id: int) -> bool:
        """Chained mode is off, so the next step starts on an answer rather than on a guess."""
        if not self.model:
            return False
        question = repair.step_offer(self.model, done_id, nxt, len(book["steps"]), self.auto_apply)
        if not self.confirm("Continue with the plan?", question,
                            warning="Auto-continue can be switched on in Settings to skip this "
                                    "question for every later step.",
                            ok_label=f"Start step {nxt['id']}"):
            return False
        self.start_plan(planbook.task_for(book, nxt))
        return True

    def refresh_plan_status(self) -> None:
        repo, plan_file = self.repo.strip(), self.plan_file.strip()
        if not repo or not plan_file or not Path(repo).is_dir():
            self.ledger_path = self.ledger = None
            return
        try:
            self.ledger_path, self.ledger = planbook.open_book(self.plans, Workspace(Path(repo)), plan_file)
        except (AgentError, OSError):
            self.ledger_path = self.ledger = None

    def _plan_info(self) -> dict | None:
        if not self.plan_file or self.ledger is None:
            if not self.plan_file:
                return None
            self.refresh_plan_status()
        if self.ledger is None:
            return None
        book = self.ledger
        row = planbook.current(book)
        verified = len(planbook.done_titles(book))
        return {"name": Path(book["plan_path"]).name, "step": row["id"] if row else len(book["steps"]),
                "total": len(book["steps"]), "verified": verified,
                "note": planbook.progress_line(book),
                "steps": [{"id": step["id"], "title": step["title"], "status": step["status"],
                           "current": bool(row) and step["id"] == row["id"]} for step in book["steps"]]}

    # ------------------------------ sessions ------------------------------
    def _load_session_cached(self, path: Path) -> dict | None:
        try:
            info = path.stat()
        except OSError:
            return None
        key = (info.st_mtime_ns, info.st_size)
        cached = self._session_cache.get(path)
        if cached and cached[0] == key:
            return cached[1]
        try:
            session = load_session(path)
        except (AgentError, OSError):
            session = None
        self._session_cache[path] = (key, session)
        return session

    def _load_chat_cached(self, path: Path) -> dict | None:
        try:
            info = path.stat()
        except OSError:
            return None
        key = (info.st_mtime_ns, info.st_size)
        cached = self._chat_cache.get(path)
        if cached and cached[0] == key:
            return cached[1]
        try:
            chat = load_chat(path)
        except (AgentError, OSError):
            chat = None
        self._chat_cache[path] = (key, chat)
        return chat

    def _nav_projects(self) -> list[dict]:
        """The sidebar's project nodes: one per granted folder, holding its tasks and bound chats.

        Filtering is the client's own — it happens on every keystroke, and a round trip per
        keystroke would make the box lag behind the typing.
        """
        groups: dict[str, dict[str, list]] = {key: {} for key in self.projects}
        # A chat that was dropped onto a project belongs to that project's node, so it is listed
        # beside the tasks instead of in the standalone column.
        bound: dict[str, list] = {}
        for path in self.chats.glob("*/chat.json"):
            chat = self._load_chat_cached(path)
            project = project_of(chat) if chat else None
            if not project:
                continue
            root = str(project.get("key", ""))
            folder = str(project.get("path", ""))
            if not root or not folder:
                continue
            self.projects.setdefault(root, folder)
            groups.setdefault(root, {})
            title = title_for(chat)
            bound.setdefault(root, []).append(
                {"id": path.parent.name, "kind": "chat", "title": title, "state": "",
                 "updated": chat.get("created", ""), "busy": False})
        for path in self.runs.glob("*/session.json"):
            session = self._load_session_cached(path)
            if session is None:
                continue
            try:
                root = project_key(session["root"])
                self.projects.setdefault(root, str(Path(session["root"]).resolve()))
                groups.setdefault(root, {}).setdefault(session.get("chat_id", session["id"]), []).append((path, session))
            except (KeyError, TypeError, ValueError, OSError):
                continue
        out = []
        for root, chats in groups.items():
            folder = Path(self.projects[root])
            ordered = sorted(chats.values(),
                             key=lambda turns: max(t[1].get("created", "") for t in turns), reverse=True)
            items = []
            for turns in ordered:
                turns.sort(key=lambda pair: (pair[1].get("created", ""), pair[1]["id"]))
                latest = turns[-1][1]
                title = latest.get("task", "Chat").replace("\n", " ")[:60]
                items.append({"id": turns[-1][0].parent.name, "kind": "session", "title": title,
                              "state": latest.get("state", ""),
                              "updated": latest.get("created", ""), "busy": False})
            items.extend(bound.get(root, []))
            items.sort(key=lambda item: item["updated"], reverse=True)
            out.append({"key": root, "name": folder.name, "path": str(folder),
                        "initials": initials_for(folder.name), "icon": self.icons.get(root, ""),
                        "chats": items})
        return sorted(out, key=lambda group: group["name"].casefold())

    def _nav_chats(self) -> list[dict]:
        loose = []
        for path in self.chats.glob("*/chat.json"):
            chat = self._load_chat_cached(path)
            if chat and not project_of(chat):
                loose.append((path, chat))
        loose.sort(key=lambda pair: pair[1].get("created", ""), reverse=True)
        return [{"id": path.parent.name, "title": title_for(chat), "updated": chat.get("created", "")}
                for path, chat in loose]

    def open_item(self, kind: str, item_id: str) -> None:
        if self.busy or not isinstance(item_id, str) or not CHAT_ID.fullmatch(item_id):
            return
        if kind == "chat":
            path = self.chats / item_id / "chat.json"
            if path.exists():
                self.open_chat(path)
            return
        path = self.runs / item_id / "session.json"
        if not path.exists():
            return
        try:
            self.display_session(path, select=True)
            self.status = status_text("task_opened", arabic=self.arabic)
        except (AgentError, OSError, ValueError) as exc:
            self.status = friendly_error(exc)

    def open_chat(self, path: Path) -> None:
        chat = load_chat(path)
        project = project_of(chat)
        if project:
            key = str(project.get("key", ""))
            self.projects.setdefault(key, str(project.get("path", "")))
        self._select_branch(BRANCH_PROJECT if project else BRANCH_CHAT,
                           str(project["key"]) if project else "", chat_id=chat["id"],
                           composer=CHAT_COMPOSER, bound=bool(project))   # a saved chat is a conversation
        self.chat = chat
        self.chat_id = chat["id"]
        self.session = self.session_path = None
        self.title = title_for(chat)
        self.messages = []
        for turn in chat["turns"]:
            if turn.get("role") == "user":
                self.messages.append({"role": "user", "author": "You", "text": turn.get("content", ""), "time": ""})
            elif turn.get("role") == "assistant":
                self.messages.append({"role": "assistant", "author": "AI Code Engineer",
                                      "text": turn.get("content", ""), "time": ""})
        self.status = ("Chat reopened — it reads " + Path(project["path"]).name + " as context."
                       if project else "Chat reopened on its own — no project folder is attached.")

    def _history_steps(self, turn: dict) -> list[dict]:
        """The step rows a saved task used to have on screen while it ran.

        They are rebuilt from the session's own records, which is what makes them survive a restart:
        the sentence comes back through the same `labels.step_line` the live loop used, in the language
        the task was asked in, and the row keeps the stored id so opening it still finds its detail.
        A `run` event has no step row of its own — the command was announced by the window, not the
        loop — so it becomes one here, in the past tense, matched to its stored record in order.
        """
        arabic = is_arabic(turn.get("task", ""))
        records = turn.get("runs") or []
        rows: list[dict] = []
        seen_runs = 0
        for item in turn.get("events") or []:
            kind = item.get("kind")
            if kind == "step":
                action = str(item.get("action", ""))
                fields = {key: item[key] for key in STEP_FIELDS if key in item}
                line = step_line(arabic, action, **fields)
                # The loop records every action it takes and the thread shows a repeated one once, so
                # the rebuild has to collapse the same way or history would show a step nobody saw.
                if rows and rows[-1]["text"] == line:
                    continue
                rows.append({"role": "tool", "author": "Steps", "time": "",
                             "text": line,
                             "step": {"id": str(item.get("id", "")), "action": action,
                                      "fields": fields, "detail": step_has_detail(action, fields)}})
            elif kind == "run":
                if seen_runs < len(records):
                    record = records[seen_runs]
                else:
                    # A run no session could hold is still a run the operator paid for (D28), and the
                    # event carries its verdict. The recipe name stands in for the command there.
                    record = {key: item.get(key) for key in ("status", "exit_code", "seconds")}
                    record["command"] = str(item.get("recipe", ""))
                seen_runs += 1
                command = str(record.get("command", ""))
                rows.append({"role": "tool", "author": "Steps", "time": "",
                             "text": executed_line(arabic=arabic, command=command,
                                                   verdict=run_verdict(arabic, record)),
                             "step": {"id": f"{str(turn.get('id', ''))[:8]}-run-{seen_runs}",
                                      "action": "executed", "fields": {"command": command},
                                      "detail": step_has_detail("executed", {"command": command})}})
        return rows

    def display_session(self, path: Path, *, select: bool = False) -> None:
        session = load_session(path)
        self.session, self.session_path = session, path
        self.review_file = 0
        if select:
            self.title = session.get("task", "Saved task").replace("\n", " ")[:45]
            self.chat_id = session.get("chat_id", session["id"])
            self._loading_session = True
            try:
                key = project_key(session["root"])
                if key != self.current_project:
                    self.projects.setdefault(key, str(Path(session["root"]).resolve()))
                    self._select_branch(BRANCH_PROJECT, key, chat_id=self.chat_id,
                                        composer=CHANGE_COMPOSER)
            finally:
                self._loading_session = False
            self.cloud_ok = False
            events = session.get("events", [])
            self.log = []
            self.log_dropped = 0
            # The same ceiling the live list carries, taken off the front so the most recent work is
            # what survives. The count is stated on the list rather than left to be inferred from a
            # log that got shorter on its own.
            if len(events) > MAX_LOG_ENTRIES:
                self.log_dropped = len(events) - MAX_LOG_ENTRIES
                events = events[self.log_dropped:]
            for entry in events:
                self.log.append({"ts": str(entry.get("at", ""))[-8:], "kind": entry.get("kind", ""),
                                 "text": " ".join(f"{k}={v}" for k, v in entry.items() if k not in {"at", "kind"})})
            reference = session.get("plan_reference")
            self.plan_file = str(Path(session["root"]) / reference["path"]) if reference else ""
            self.messages = []
            turns = chat_sessions(self.runs, session["root"], self.chat_id)
            if not any(turn["id"] == session["id"] for _, turn in turns):
                turns.append((path, session))
            for _, turn in turns:
                self.messages.append({"role": "user", "author": "You",
                                      "text": turn.get("task", "Saved task"), "time": ""})
                # Only the task this window was opened on gets its steps back: the earlier turns in
                # the chat are there for their answers, and rebuilding every one of their tool calls
                # would bury the task the user came to look at.
                if turn.get("id") == session.get("id"):
                    self.messages.extend(self._history_steps(turn))
                self.messages.append({"role": "assistant", "author": "AI Code Engineer",
                                      "text": state_label(turn.get("state")) + "\n\n" +
                                      (turn.get("summary") or turn.get("error") or "No applicable proposal was created."),
                                      "time": ""})
            if session.get("changes"):
                self.messages.append({"role": "tool", "author": "Changes",
                                      "text": self.removal_notice(session) +
                                      ", ".join(change["path"] for change in session["changes"])})
            self.refresh_recipes()
        self.refresh_plan_status()

    def new_task(self) -> None:
        """Start a fresh conversation. The branch it belongs to is left alone."""
        if self.busy:
            return
        self.chat_id = uuid.uuid4().hex
        self.plan_file = ""
        self.cloud_ok = False
        self._auto_fix = False
        self._fix_round = 0
        self.log = []
        self.log_dropped = 0
        self.step_detail = None
        self._run_step = -1
        self.session = self.session_path = None
        self.chat = None
        self.ledger_path = self.ledger = None
        self.title = "New chat"
        self.subtitle = self._subtitle()
        self.run_info = "No command has run yet."
        self._draft = ""
        self.branch = {**self.branch, "id": self.chat_id}
        self.reset_conversation()
        self.status = ("New chat — it answers in prose and reads no project files. Choose a project "
                       "in the sidebar and it can read that folder too."
                       if self.branch.get("kind") != BRANCH_PROJECT else
                       "New chat in " + Path(self.repo).name + " — Send answers in prose; the badge "
                       "by Send switches to reviewed changes.")
        self.refresh_recipes()

    def new_chat(self) -> None:
        """The sidebar's New chat: a branch of its own, with no folder attached to it."""
        self._select_branch(BRANCH_CHAT)

    def example(self) -> None:
        self.new_task()
        key = project_key(str(self.app_dir / "examples" / "demo_repo"))
        self.projects.setdefault(key, str((self.app_dir / "examples" / "demo_repo").resolve()))
        self._select_branch(BRANCH_PROJECT, key, chat_id=self.chat_id, composer=CHANGE_COMPOSER)
        self._draft = ("Fix the add function in calculator.py so it adds the two numbers "
                       "instead of subtracting them.")
        self.set_mode("Ollama")
        self.status = "Sample ready in Change mode. Choose an Ollama model, then press Send."

    def reset_conversation(self) -> None:
        self.messages = [{
            "role": "assistant", "author": "AI Code Engineer", "time": _clock(),
            "text": "What would you like to work on?\n\nAsk me anything as it is, or choose a project "
                    "in the sidebar and I will propose reviewed changes to its files."}]

    # ------------------------------ projection ------------------------------
    def _artifact(self) -> dict:
        """The right-hand card. `written` is what keeps it honest: the same count reads as a
        proposal before the click and as files on disk after it."""
        state = self.session.get("state") if self.session else None
        changes = (self.session or {}).get("changes", [])
        return artifact_card(state,
                             arabic=self.arabic,
                             count=len(changes),
                             project=Path(self.session["root"]).name if self.session else "",
                             summary=(self.session or {}).get("summary") or "",
                             written=bool(changes) and state in MUTABLE_STATES | INTERRUPTED_STATES,
                             has_project=bool(self.repo))

    def _review(self) -> dict:
        session = self.session or {}
        changes = session.get("changes", [])
        state = session.get("state")
        files = []
        for change in changes:
            lines = _diff(change["before"] or "", change["after"] or "", change["path"])
            files.append({"path": change["path"],
                          "kind": ("D" if change.get("delete")
                                   else "A" if change["before"] is None else "M"),
                          "add": sum(1 for line in lines if line.startswith("+") and not line.startswith("+++")),
                          "del": sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))})
        chosen = changes[min(self.review_file, len(changes) - 1)] if changes else None
        return {
            "state": state_label(state), "tone": TONE.get(state or "", ""),
            "title": session.get("task", "No proposal yet")[:120],
            "detail": (f"{len(changes)} file(s)"
                       + (" · attached plan " + session["plan_reference"]["path"] if session.get("plan_reference") else "")
                       + (" · proposal " + str(session.get("proposal_hash", ""))[:4] + "…"
                          + str(session.get("proposal_hash", ""))[-4:] if session.get("proposal_hash") else "")),
            "canApply": state == "WAITING_APPROVAL" and not self.busy,
            "canMutate": state in MUTABLE_STATES and not self.busy,
            "canRollback": (state in MUTABLE_STATES | {"PARTIAL_APPLY", "APPLYING"}) and not self.busy,
            "files": files, "selected": min(self.review_file, max(0, len(changes) - 1)),
            "tab": self.diff_tab,
            "view": {
                # A delete has no `after` at all, and the viewer shows the removal as the whole
                # file going out with minus signs -- which is only readable if the empty side is
                # built here rather than assumed by the caller.
                "diff": _diff(chosen["before"] or "", chosen["after"] or "", chosen["path"]) if chosen else [],
                "before": (chosen["before"] or "").splitlines() if chosen else [],
                "after": (chosen["after"] or "").splitlines() if chosen else [],
                "checks": self._checks_text(session),
            } if chosen else {"diff": [], "before": [], "after": [], "checks": self._checks_text(session)},
        }

    def _checks_text(self, session: dict) -> list[str]:
        out = ["Proposed checks (not execution results):"] + ["• " + check for check in session.get("checks", [])]
        result = session.get("verification")
        if result:
            out.append("Latest check: " + result.get("status", "unknown"))
            out += [item["path"] + ": " + item["status"] for item in result.get("static", [])]
            if result.get("reason"):
                out.append(result["reason"])
        runs = session.get("runs") or []
        if runs:
            last = runs[-1]
            out.append(f"Command runs ({len(runs)}): last was {last.get('command', '')}")
            out.append(f"→ {last.get('status')} · exit {last.get('exit_code')} · {last.get('seconds')}s")
            out += ["  " + str(row)[:200] for row in (last.get("failures") or [])[:8]]
        return out

    # ------------------------------ persistence ------------------------------
    def _sync_project(self) -> None:
        self.refresh_plan_status()          # the subtitle reads the ledger, so it goes first
        self.subtitle = self._subtitle()

    def _subtitle(self) -> str:
        """The header line: what the next Send would actually do, and with what."""
        if not self.repo:
            return "Standalone chat — no folder attached, nothing to change"
        parts = [Path(self.repo).name,
                 "chat · reads as context" if self.composer == CHAT_COMPOSER else "change · reviewed diff"]
        plan_info = self._plan_info()
        if plan_info:
            parts.append(f"step {plan_info['step']} of {plan_info['total']}"
                         + (" · step-by-step" if self.chained else ""))
        parts.append(self.model or "no model selected")
        return " · ".join(parts)

    def _save_state(self) -> None:
        # No `last_project` any more: which folder the window opens on is a property of the branch
        # the user left selected, so a launch cannot point the whole program at one directory.
        ui = {"mode": self.mode, "last_chat": self.chat_id,
              "last_branch": {"kind": self.branch.get("kind", BRANCH_CHAT),
                              "key": self.branch.get("key", "")},
              "composer": dict(self._composer_pref),
              "auto_apply": dict(self._auto_pref),
              "endpoints": dict(self.endpoints), "profile": self.profile,
              "request_timeout": self.timeout_seconds(), "plan_chained": bool(self.chained),
              "style": self._saved_ui.get("style", "claude"), "theme": self._saved_ui.get("theme", "light"),
              "collapsed": bool(self._saved_ui.get("collapsed", False))}
        if self.queue:
            # Stored so a batch survives the restart that would otherwise eat it, and restored held
            # (see the load path): nothing in here starts on its own accord.
            ui["queue"] = [dict(item) for item in self.queue[:20]]
        if self.model or self._pending_model:
            ui["model"] = self.model or self._pending_model
        self._saved_ui = ui
        try:
            atomic_json(self.app_dir / ".agent-projects.json", {
                "projects": [{"path": path, "key": key, "icon": self.icons.get(key, "")}
                             for key, path in self.projects.items()], "ui": ui})
        except OSError:
            self.status = status_text("registry_unsaved", arabic=self.arabic)

    def set_pref(self, name: str, value: str) -> None:
        """Style and theme belong to the window, but they survive a restart with the rest."""
        self._saved_ui[name] = value
        self._save_state()

    def close(self) -> None:
        self.key = ""


SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "program files", "windows", "system32",
             "appdata", "downloads", "$recycle.bin", "programdata", "perflogs", "recovery"}


def _hidden(path: Path) -> bool:
    return path.name.startswith(".")


def drive_roots() -> list[str]:
    """Every mounted root, so the folder picker can leave the drive it started on."""
    if os.name != "nt":
        return ["/"]
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except (OSError, AttributeError):
        mask = 1
    return [letter for letter in
            (chr(ord("A") + index) + ":\\" for index in range(26) if mask & (1 << index))
            if Path(letter).is_dir()]


def initials_for(name: str) -> str:
    """A two-letter mark that survives near-identical folder names.

    ``demo2`` and ``demo_repo`` both start with "de", which made the sidebar avatars
    indistinguishable; segment boundaries and a trailing digit separate them.
    """
    parts = [part for part in re.split(r"[\s._\-]+", name) if part]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).casefold()
    word = parts[0] if parts else "?"
    digit = next((char for char in word[1:] if char.isdigit()), "")
    return (word[0] + (digit or (word[1] if len(word) > 1 else ""))).casefold()


def _diff(before: str, after: str, name: str) -> list[str]:
    return list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                     fromfile="a/" + name, tofile="b/" + name, lineterm=""))
