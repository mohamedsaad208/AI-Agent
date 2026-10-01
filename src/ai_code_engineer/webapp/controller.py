"""Headless orchestration: every decision the desktop window made, with no Tk in sight.

The shape of a turn is the same one the Tk window established: validate what the user
asked for, run the engine in a worker thread, and only then let a proposal be applied
after an explicit yes. The difference is that output is a stream of events instead of
widget updates, and the two places the window reached for a native dialog now ask the
front-end and block until it answers.
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import subprocess
import sys
import time
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ..catalog import LIVE, models_for
from ..chat import (context_block, create_chat, load_chat, project_of, respond,
                    title_for)
from .. import config
from .. import service_runner
from ..config import Settings
from ..engine import (MAX_TASK_CHARS, apply_proposal, atomic_json, chat_sessions, diff_size,
                      load_session, plan, project_key, proposal_rejected, propose_block,
                      read_plan_reference, reopen_proposal, rollback)
from ..engine import event as record_event    # `event` is a parameter name in `stream()` below
from ..errors import AgentError, PolicyError
from ..labels import (INTERRUPTED_STATES, MUTABLE_STATES, QUOTE_CHARS, STEP_FIELDS, TONE,
                      UNVERIFIED_STATES,
                      applied_line, applied_note, artifact_card, asked_of, batch_summary_line,
                      branch_started, branch_switched,
                      catalog_status_line, checkpoint_note, detail_section, executed_line,
                      executing_line, friendly_error, fix_offers_off_line, is_arabic,
                      log_dropped_line, log_line,
                      no_branch_note, no_checkpoint_note,
                      quote_reference, rejected_note,
                      restore_done, restore_offer, run_unrecorded_line, run_verdict, run_warning,
                      say, state_label,
                      status_text, step_has_detail, step_line, step_missing_line,
                      graph_caption, graph_empty_line,
                      write_notice)
from ..labels import note as shared_note     # `note` is a local variable in three methods here
from ..providers import make_provider
from ..redaction import redact
from .. import memory as memory_store
from .. import (git_integration, host, ignore, intent, modes, overrides, planbook, repair,
                runner, session_flow, setup, symbols)
from ..verification import verify
from ..workspace import Workspace, ensure_project_dir
from . import connection, projects, requestqueue, runresults, uistate

# The provider rows and the recommended models are the table's (`config.py`), not this window's:
# a second copy of `MODES` was how the web window and Tk ended up disagreeing about a provider's
# name once already, and `test_connection` now refuses to let the copy come back.
PLAN_SUFFIXES = (".md", ".txt")
# A sidebar entry is a branch, and a branch is the only thing that owns a folder. "chat" never
# has one unless a project was bound to it by name, so the program cannot start pointed at a
# directory the user did not choose in this session.
BRANCH_CHAT, BRANCH_PROJECT = "chat", "project"
CHAT_COMPOSER, CHANGE_COMPOSER, READ_COMPOSER = "chat", "change", "read"
CHAT_ID = re.compile(r"[a-f0-9]{32}")
# A closed palette, not free text: the value is painted into the sidebar, so anything a
# crafted registry could inject there would read to the user as their own label. Escapes
# keep the source plain ASCII, which a cp1252 console can at least print.
PROJECT_ICONS = ("📁", "🚀", "🐍", "☕", "⚛️",
                 "🦀", "🌐", "📦", "🧪", "🛠️",
                 "💻", "🖥️", "📱", "🤖", "🧠", "🎮",
                 "🗄️", "☁️", "🔌", "⚙️", "🔒", "📊",
                 "🛒", "💬", "🎨", "🧩", "🔬", "📡")
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


class LineFeed:
    """Whole lines out of a token stream.

    A model answers in fragments of a few characters, and redaction reads a line: a key that arrives
    as ``sk-`` then ``abcdef`` is one the pattern cannot see in either piece. So the fragments are
    reassembled here and handed on a line at a time — which is also the grain a reader watches, and
    the reason a build output has always streamed by the line rather than by the character.
    """

    def __init__(self, emit, cap: int = 500):
        self.emit = emit
        self.cap = cap
        self.held = ""

    def feed(self, piece: str) -> None:
        self.held += piece
        while "\n" in self.held:
            line, self.held = self.held.split("\n", 1)
            self.emit(line)
        if len(self.held) > self.cap:
            # A model that never breaks a line must not make the reader wait for the whole answer, and
            # must not grow this buffer without bound. Cut at the cap, on a line the redactor has seen.
            self.emit(self.held)
            self.held = ""

    def close(self) -> None:
        if self.held:
            self.emit(self.held)
            self.held = ""


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
        # First run writes the override file and its signing key beside the other records; an existing
        # pair is left alone, because rewriting a signed file on every start is how a row gets lost.
        overrides.ensure(self.app_dir)
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
        # One re-entrant lock over the state the two windows read. `ThreadingHTTPServer` runs a
        # thread per request and `run_job` adds a worker per task, so an action handler, a worker
        # announcing progress and a browser asking for a snapshot are all in this object at once —
        # the log and message lists were being appended and serialized against each other, and two
        # clicks in one tick could both claim the busy flag before either saw the other.
        #
        # It guards short transitions only: the busy claim and release, the message and log
        # mutators, the catalog and connection fields, and the whole of `snapshot`. It is **never
        # held across a wait** — `confirm_choice` blocks on an event whose answer arrives on another
        # HTTP thread, and holding the lock there would be a deadlock with the user holding the key.
        self._state = threading.RLock()

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
        self.catalogs: dict[str, list[dict]] = {mode: [] for mode in config.MODES}
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
        # Which module of a multi-project folder the command runs in, and every module that has one.
        # `_targets_root` records the folder the list was scanned from, so a task whose folder is not
        # the window's folder cannot be handed a module path that only made sense next to the other.
        self.targets: list[dict] = []
        self.target = ""
        self._targets_root = ""
        # The first-run card. Rows are computed once and stored, never per snapshot: building them asks
        # the provider, and a snapshot goes out on every streamed log line.
        self._setup_rows: list[dict] = []
        self._setup_demo: dict | None = None
        self._setup_open = False
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
                self._removed_projects = set(self._saved_ui.get("removed_projects", []))
        except (OSError, ValueError, KeyError, TypeError):
            self.projects, self.icons, self._removed_projects = {}, {}, set()
        if not hasattr(self, "_removed_projects"):
            self._removed_projects = set()
        self.chained = bool(self._saved_ui.get("plan_chained"))
        # The container choice belongs to the machine, never to the project: a repository must not get
        # to name the image the tool builds that repository inside.
        self.sandbox_on = bool(self._saved_ui.get("sandbox_on"))
        self.sandbox_image = str(self._saved_ui.get("sandbox_image") or "")
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
        # is held until the operator presses the strip's ▶, and carries the sentence that says why.
        self.queue.extend(requestqueue.restore(self._saved_ui.get("queue")))
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
        # A position chosen before the declaration file existed is honoured, not lost. Adopted once per
        # folder, and only where nothing has been declared yet, so this never overrules a choice made in
        # the terminal after the window closed.
        for saved_key, saved_mode in self._composer_pref.items():
            folder = self.projects.get(saved_key, "")
            if folder and not modes.mode_for(self.app_dir, folder):
                modes.declare(self.app_dir, folder, saved_mode, by=modes.SAVED)
        branch = self._saved_ui.get("last_branch")
        kind = branch.get("kind") if isinstance(branch, dict) else None
        key = branch.get("key") if isinstance(branch, dict) else ""
        saved_chat = self._saved_ui.get("last_chat")
        resumed = (self.chats / saved_chat / "chat.json"
                   if isinstance(saved_chat, str) and CHAT_ID.fullmatch(saved_chat) else None)
        if kind == BRANCH_PROJECT and self.projects.get(str(key)):
            self._select_branch(BRANCH_PROJECT, str(key))
            if self.composer == CHANGE_COMPOSER:
                self.auto_apply = bool(self._auto_pref.get(str(key), False))
        elif resumed is not None and resumed.exists():
            try:
                self.open_chat(resumed)
            except (AgentError, OSError):
                self._select_branch(BRANCH_CHAT)
        else:
            self._select_branch(BRANCH_CHAT)
        # A machine that has never granted a folder gets the card, once. "Never" is the operative word:
        # the dismiss writes `setup_seen`, and a first-run helper that came back every launch would be
        # the thing the operator learns to click through without reading.
        self._setup_open = (setup.first_run(self.app_dir)
                            and not bool(self._saved_ui.get("setup_seen")))

    # ------------------------------- prompts -------------------------------
    def _ask(self, kind: str, payload: dict) -> dict:
        """Push a modal to the front-end and block this thread until it answers.

        A question the browser never answers is withdrawn before this thread stops waiting. The
        wait ending on its own is invisible to the window otherwise, and the modal it leaves behind
        covers the app: every later question then stacks another one behind it, unanswered.
        """
        request_id = secrets.token_hex(8)
        waiter = threading.Event()
        with self._state:
            self._replies[request_id] = waiter
            # A list, not one slot: two questions were simultaneously live during the run — the previous
            # task's offer and the current one — and a single field would have dropped the first along
            # with the only id that could unblock it.
            self._active_asks.append({"kind": kind, "id": request_id, **payload})
        self._emit({"kind": kind, "id": request_id, **payload})
        # The wait is outside the lock, and that is the whole shape of this rule: the answer arrives
        # on an HTTP thread that has to take the lock to deliver it. A thread that waits for the user
        # while holding it would stop every other request in the window behind one open dialog.
        answered = waiter.wait(timeout=ASK_TIMEOUT)
        with self._state:
            self._replies.pop(request_id, None)
            self._active_asks = [a for a in self._active_asks if a.get("id") != request_id]
            if not answered:
                self._answers.pop(request_id, None)
                self.status = status_text("ask_expired")
        if not answered:
            self._emit({"kind": "retract", "id": request_id})
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

    # ---------------- the four Host verbs ----------------
    # The web half of `host.Host`, and the reason the contract is not Tk-only vocabulary. Every one of
    # these already existed under another name; the verb is the name the *other* window can be checked
    # against, which is what `tests/test_host.py` does — including a ratchet on the raw primitives
    # below, so a new un-routed sink has to be a deliberate act.
    def say(self, text: str) -> None:
        self.status = text

    def line(self, role: str, author: str, text: str) -> None:
        self._add(role, author, text)

    def ask(self, title: str, message: str, warning: str = "", ok_label: str = "Continue",
            alt_label: str = "") -> bool:
        return self.confirm(title, message, warning, alt_label or ok_label)

    def stream(self, event: dict) -> None:
        self._emit(event)

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
        with self._state:
            waiter = self._replies.pop(request_id, None)
            if waiter is None:
                return                  # a reply for a question that already closed or expired
            # Retire on the answering side too: the thread that asked has to be scheduled before it
            # removes itself, and a snapshot built in that gap would redraw a question already answered.
            self._active_asks = [a for a in self._active_asks if a.get("id") != request_id]
            self._answers[request_id] = reply or {}
        # Woken outside the lock: the worker this releases goes on to take the lock itself, and a
        # thread that has been handed the state it is waiting for must never be made to queue twice.
        waiter.set()

    # ------------------------------ jobs ------------------------------
    def run_job(self, operation, on_done, status: str, *, cancellable: bool = False,
                on_busy=None) -> None:
        with self._state:
            self._job_started = False
            if self.busy:
                # Two clicks in one tick used to both pass this check, because each HTTP handler read
                # the flag before either job had claimed it. The claim is inside the lock now, so
                # exactly one of them gets here — and a message that is dropped is work the user
                # already typed, so the caller still gets to say what it becomes.
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
            with self._state:
                self.busy = self.cancellable = False
                self.pending = None
            self._emit({"kind": "busy", "value": False, "cancellable": False})
            if failure is not None:
                # The detail is the exception verbatim, which is the one field that can carry a
                # provider's own text — including a bearer token it echoed back. Cap after redacting.
                log_msg = (f"{failure} [Detail: {redact(raw_error)[:300]}]"
                           if (raw_error and raw_error != failure) else failure)
                with self._state:
                    self.status = failure
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
    def queue_add(self, text: str, quote_of: object = None, *, reference: str = "") -> None:
        """Hold a message typed during a running task, and start it when that task ends.

        The click that queues is the approval: the user chose both the text and the moment, so
        this is the one path in the app that starts work without a second click. It is still the
        folder's own rules that run — Change mode proposes, Auto-Apply writes, Chat answers in
        prose — and **Stop** holds the queue, because a stop that is followed instantly by the
        next message is not a stop.

        A reference is resolved here, at the click, and the composed message is what the queue holds:
        by the time the queue drains the thread may have grown, and an index kept that long would
        point at a different row.
        """
        asked = (text or "").strip()
        task = reference + asked if reference else self.with_quote(asked, quote_of)
        if not asked:
            return
        if requestqueue.too_long(task):
            self._add("tool", "Tool", say(self.arabic,
                      en=f"That message is longer than {MAX_TASK_CHARS:,} characters, so it was "
                         "not queued.",
                      ar="هذه الرسالة أطول من ٤٠٠٠ حرف، لذلك لم تُضَف إلى قائمة الانتظار."))
            return
        if requestqueue.is_duplicate(self.queue, task, self.chat_id):
            self._queue_held = False
            return                       # the same message twice in a row is one queue line
        self.queue.append(requestqueue.row(
            task=task, asked=asked, chat=self.chat_id,
            branch=str(self.branch.get("key") or ""), project=self.repo, at=_clock()))
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
                requestqueue.apply_edit(item, text)
        self._queue_held = False
        self._save_state()
        self._emit({"kind": "state", "data": self.snapshot()})

    def queue_drop(self, item_id: str) -> None:
        self.queue = [item for item in self.queue if item.get("id") != item_id]
        self._save_state()
        self._emit({"kind": "state", "data": self.snapshot()})

    def queue_now(self, item_id: str) -> None:
        """Move one item to the front. With the queue already running this is the whole of
        "run this one next", and dropping a held queue with it is what ▶ resumes."""
        self.queue = requestqueue.to_front(self.queue, item_id)
        self._release_restored()
        self._queue_held = False
        self._drain_queue()
        self._emit({"kind": "state", "data": self.snapshot()})

    def queue_detached(self, item_id: str) -> None:
        """Take an item out of this conversation and ask it in a chat of its own.

        The row goes to the front with `detached` set and no chat of its own, so the drain asks it
        in a thread of its own the moment a switch is allowed — never in the conversation it left.
        """
        self.queue = requestqueue.detach(self.queue, item_id)
        self._queue_held = False
        self._drain_queue()
        self._emit({"kind": "state", "data": self.snapshot()})

    def queue_resume(self) -> None:
        self._release_restored()
        self._queue_held = False
        self._drain_queue()
        self._emit({"kind": "state", "data": self.snapshot()})

    def _release_restored(self) -> None:
        """A press on the strip is the approval a restored row was waiting for, for the whole
        batch rather than the one row clicked — see `requestqueue.release_restored`."""
        requestqueue.release_restored(self.queue)

    def _drain_queue(self) -> None:
        """Start the next queued message whose conversation is the one in front.

        A row leaves the queue only once a job has actually taken it. `start_plan` refuses for reasons
        the queue cannot fix — no model selected, the folder is gone, the message too long — and a
        drain that popped first lost the typed message with nothing but a status line to show for it;
        two create tasks disappeared that way in the ecommerce run.

        The reading of `busy` and the claim of a row are one step under the lock, because two jobs
        ending in the same tick both found an idle window here and both reached for the same row. The
        one that lost the job then re-queued the text the winner had already taken out of the queue,
        and the message ran twice — seen in the trace of `test_a_queued_batch_says_what_it_landed_
        when_it_ends` under load, as a batch that reported two tasks for one typed row. Only the
        decision is locked: `start_plan` takes this same lock itself, so the work stays outside it.
        """
        # The queue is stored in the same file as the rest of the window's state, and this is the one
        # place every mutation path passes through -- except an edit, which saves for itself.
        self._save_state()
        # A row that came back with the restart does not run on its own accord: the operator pressing
        # ▶ (or queueing something new beside it) is the approval, and until then the strip shows it
        # with the sentence that says it was typed in an earlier session.
        item, closing = None, False
        with self._state:
            if self._draining or self.busy or self._loading_session or self._queue_held or not self.queue:
                closing = not self._draining and not self.busy and not self.queue and bool(self._batch)
            else:
                item = next((row for row in self.queue if not row.get("restored")), None)
                if item is not None:
                    self._draining = True
        if closing:
            # Cleared when the batch it belongs to actually ends. A task with an empty queue is
            # not a batch of one that just closed: the flag has to outlast it, or "don't ask
            # again for this batch" would mean "not for the next two minutes".
            self._batch_fixes_off = False
            self._close_batch()
            return
        if item is None:
            return
        try:
            if item.get("detached"):
                key = item.get("branch") or ""
                if key:
                    self.new_chat_in(key)
                else:
                    self.new_chat()
                if self.busy:
                    return              # the switch starts nothing on its own; send below
                self._start_queued(item)
                if self._job_started:
                    self.queue.remove(item)
                    self._open_batch_row(item)
                return
            if item.get("chat") == self.chat_id:
                self._start_queued(item)
                if self._job_started:
                    self.queue.remove(item)
                    self._open_batch_row(item)
        finally:
            self._draining = False

    def _start_queued(self, item: dict) -> None:
        asked, reference = requestqueue.split_request(item)
        if reference:
            self.start_plan(asked, reference=reference)
        else:
            self.start_plan(asked)

    def _open_batch_row(self, item: dict) -> None:
        """Start recording the queued task that was just taken out of the queue."""
        first = (requestqueue.split_request(item)[0].strip().splitlines() or [""])
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

        The shape and its sentences come from `requestqueue.view`; this only hands it the state the
        window is standing on, including whether a question is holding a worker right now.
        """
        return requestqueue.view(self.queue, chat=self.chat_id, held=self._queue_held,
                                 arabic=self.arabic, ask_pending=bool(self._replies))

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
        with self._state:
            self.log.append(entry)
            if len(self.log) > MAX_LOG_ENTRIES:
                # Counted rather than trimmed: the dropped number is drawn at the top of Activity, because
                # a log that quietly got shorter reads as a task that did less than it did.
                self.log.pop(0)
                self.log_dropped += 1
        # `kind` names the SSE channel, so the entry travels inside its own field instead of
        # being flattened into the event — flattened, the two kinds collided.
        self._emit({"kind": "log", "entry": entry})

    def _add(self, role: str, author: str, text: str, step: dict | None = None,
             *, quote_of: object = None) -> None:
        message = {"role": role, "author": author, "text": text, "time": _clock()}
        if role == "user" and self.quoted(quote_of):
            message["replyTo"] = int(quote_of)
        if step:
            # A step row is a message with a handle on its own record: the sentence is what the row
            # says, and `step` is what lets it be opened again after a reload or a reopen.
            message["step"] = step
        with self._state:
            self.messages.append(message)
        self._emit({"kind": "message", "message": message})

    def note_failure(self, reference: str, detail: str) -> None:
        """The one place a raw exception may be written down: the Activity log, redacted and capped.

        `server._fail` answers the browser with a fixed sentence and this id, so the reason is still
        findable by a person who needs it and is never handed to whoever asked.
        """
        self._note("error", f"Request {reference}: {detail}")

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
        task = asked_of((self.session or {}).get("task") or "")
        if not task:
            task = asked_of(next((m.get("text", "") for m in reversed(self.messages)
                                  if m.get("role") == "user"), ""))
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
        """The whole UI contract, as a structure that cannot change under whoever reads it.

        The caller is a different thread from the one writing: the HTTP handler serializes this while
        a worker is appending its next line, and `self.messages` used to be handed out as the live
        list. Building it and freezing it happen inside the same lock, so no line can arrive between
        the two. Measured on the worst state this window holds — 200 messages, a 400-line Activity
        log and a 300-entry model list, 117 KB of snapshot — the copy costs 3.4 ms against a 1.0 ms
        serialization, and a state push happens per action, not per token.
        """
        with self._state:
            return copy.deepcopy(self._snapshot())

    def _snapshot(self) -> dict:
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
            # What the folder itself was told, by whichever surface told it. The badge is the window's
            # own choice; this is the fact the gates obey, and a seal that arrived from a terminal has
            # to be visible or every refusal below looks like a bug.
            "declared": self._declared_info(),
            "plan": plan_info,
            "provider": {"mode": self.mode, "modes": list(config.MODES), "model": self.model,
                         "models": self.visible_models(), "metrics": self._metrics_info()},
            # The whole connection row: a drawer that offered a provider without saying where it
            # points, or whether it wants a key, could only be filled by trial and error.
            "connection": self.connection_info(),
            # The signed rows a task is actually running on, beside the row that produced them:
            # a number the field and the run disagree about has to be visible in the same drawer.
            "overrides": self.overrides_info(),
            "recipes": [runner.RECIPES[name]["label"] for name in self.recipes],
            "recipe": self.recipe, "canRun": self._can_run(), "runInfo": self.run_info,
            "runStatus": self.run_status_info(),
            "services": service_runner.GLOBAL_SERVICES.all_snapshots(),
            "runConfig": service_runner.read_project_config(Path(self.repo)) if self.repo and Path(self.repo).is_dir() else None,
            "lastJob": getattr(self, "_last_job", None),
            "readiness": getattr(self, "_cached_readiness", None),
            "autoNotes": self.get_auto_notes(),
            "cmdHistory": self.get_custom_cmd_history(),
            "diagnosis": getattr(self, "_current_diagnosis", None),
            "repairBatch": getattr(self, "_repair_batch", None),
            # Which folder of a multi-project folder the command runs in. One entry means there is
            # nothing to choose, and the window says so rather than drawing a picker of one.
            "targets": [{"path": row["path"], "label": row["label"]} for row in self.targets],
            "target": self.target, "targetLabel": self.target_label(),
            # Rounds used to exist only as a number in one window's memory and a status line that had
            # already scrolled away by the third one. The count is in-memory and belongs in the
            # snapshot; the attempts themselves are read from the sessions when the offer or the stop
            # line needs them, never on every poll.
            "fixRounds": {"of": repair.MAX_FIX_ROUNDS, "spent": self._fix_round},
            # Said where the button is, not only in the docstring of the module that runs it.
            "runWarning": run_warning(arabic=self.arabic),
            # And said where the command it warns about will actually run.
            "sandbox": self.sandbox_info(),
            "memory": {"info": self._memory_info()},
            "settings": {"project": self.repo, "plan": self.plan_file, "chained": self.chained,
                         "auto_apply": self.auto_apply, "bound": bool(self.branch.get("bound")),
                         "timeout": self.request_timeout, "model_info": self._model_info(),
                         "memory": self._project_notes(), "memory_info": self._memory_info(),
                         "consent": self.cloud_ok},
            "artifact": self._artifact(), "review": self._review(), "banner": self._banner(),
            # The first-run card: rows computed once and stored, never probed per snapshot.
            "setup": self.setup_view(),
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
            "send": lambda: self.start_plan(payload.get("text", ""), payload.get("quote_of"),
                                            step_id=payload.get("step_id")),
            "complete_step": lambda: self.complete_step(int(payload.get("step_id") or 0)),
            "reopen": self.reopen,
            "queue_add": lambda: self.queue_add(payload.get("text", ""), payload.get("quote_of")),
            "queue_edit": lambda: self.queue_edit(str(payload.get("id", "")),
                                                   payload.get("text", "")),
            "queue_drop": lambda: self.queue_drop(str(payload.get("id", ""))),
            "queue_now": lambda: self.queue_now(str(payload.get("id", ""))),
            "queue_chat": lambda: self.queue_detached(str(payload.get("id", ""))),
            "queue_resume": self.queue_resume,
            "stop": self.stop, "apply": self.apply, "rollback": self.undo,
            "reject": self.reject,
            "git_branch": lambda: self.git_branch(str(payload.get("back", ""))),
            "git_restore": self.git_restore,
            "apply_block": lambda: self.offer_block(payload),
            "verify": self.check_changes, "run": lambda: self.run_tests(bool(payload.get("fix"))),
            # The container switch and its image field: one action, because a tick without the digest
            # it belongs to is half an answer.
            "sandbox": lambda: self.set_sandbox(payload),
            # Opening a step row: the id is the handle on the records this session already keeps, and
            # an empty one closes the row. Nothing here reads or writes the project.
            "step_detail": lambda: self.open_step(str(payload.get("id", ""))),
            # The module graph. No id, no payload: it describes the folder this window is on, and the
            # answer is the data itself, fetched on the click.
            "show_graph": self.open_graph,
            "new_chat": self.new_chat, "new_project": self.new_project, "example": self.example,
            # The first-run card: the two buttons that do work, and the one that dismisses it for good.
            # There is deliberately no bare "show it again": the only way back is the Settings entry
            # that says it will ask the machine, so re-opening and re-checking are the same click.
            "setup_check": self.run_setup_check, "setup_demo": self.run_setup_demo,
            "setup_hide": lambda: self.set_setup_open(False),
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
            "remove_project": lambda: self.remove_project(payload.get("project", "")),
            "pick_plan": self.browse_plan, "clear_plan": self.clear_plan,
            "set_mode": lambda: self.set_mode(payload.get("value", "")),
            "set_model": lambda: self.set_model(payload.get("value", "")),
            "set_filter": lambda: self.set_filter(payload.get("value", "")),
            "set_recipe": lambda: self.set_recipe(payload.get("value", "")),
            "set_target": lambda: self.set_target(str(payload.get("value", ""))),
            "set_chained": lambda: self.set_chained(bool(payload.get("value"))),
            "set_timeout": lambda: self.set_timeout(payload.get("value")),
            "set_key": lambda: self.set_key(payload.get("value", "")),
            "set_override": lambda: self.set_override(payload),
            "unset_override": lambda: self.unset_override(payload),
            "set_consent": lambda: self.set_consent(bool(payload.get("value"))),
            "set_style": lambda: self.set_pref("style", str(payload.get("style", "claude"))),
            "set_collapsed": lambda: self.set_pref("collapsed", bool(payload.get("value"))),
            "set_theme": lambda: self.set_pref("theme", str(payload.get("theme", "light"))),
            "set_draft": lambda: setattr(self, "_draft", payload.get("text", "")),
            "save_memory": lambda: self.save_memory(payload.get("text", "")),
            "select_file": lambda: setattr(self, "review_file", int(payload.get("index", 0))),
            "select_tab": lambda: setattr(self, "diff_tab", str(payload.get("tab", "diff"))),
            "refresh_models": self.check_setup,
            "run_app": lambda: self.run_app_service(payload),
            "stop_app": lambda: self.stop_app_service(payload),
            "restart_app": lambda: self.restart_app_service(payload),
            "stop_all_apps": self.stop_all_app_services,
            "run_build": self.run_build_action,
            "get_run_config": self.get_project_run_config,
            "save_run_config": lambda: self.save_project_run_config(payload),
            "get_readiness": self.get_project_readiness,
            "service_lines": lambda: self.get_service_lines(payload),
            "api_test": lambda: self.run_api_test(payload),
            "fix_errors": lambda: self.fix_run_failure(payload),
            "run_custom": lambda: self.run_custom_cmd(payload),
            "get_cmd_history": self.get_custom_cmd_history,
            "save_favorite_cmd": lambda: self.save_favorite_cmd(payload),
            "diagnose_terminal": lambda: self.diagnose_terminal(payload),
            "toggle_auto_notes": lambda: self.toggle_auto_notes(payload),
            "save_auto_notes": lambda: self.save_auto_notes(payload),
            "get_auto_notes": self.get_auto_notes,
        }
        handler = handlers.get(type)
        if handler is None:
            raise PolicyError("Unknown action: " + str(type)[:40])
        return handler()

    def list_dir(self, path: str, want_files=None) -> dict:
        """One directory level for the in-page picker — see `projects.listing` for the rules."""
        return projects.listing(path, want_files)

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
            modes.declare(self.app_dir, repo, CHANGE_COMPOSER, by=modes.WEB)
        self.auto_apply = (bool(self._auto_pref.get(str(self.branch.get("key") or ""), False))
                           if self.composer == CHANGE_COMPOSER and not self.branch["bound"] else False)
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

    def reading_only(self) -> bool:
        """The one question every write gate and every run gate asks.

        Deliberately not "a bound chat": a chat moved into a project reads its folder as context and
        still plans a proposal when a message asks for one, and that behaviour is pinned by tests and
        by the tooltip on its own badge. Read-only is the position that refuses even the proposal.

        Two things can put a folder here, and both are honoured: the badge this window is holding, and
        the declaration the folder itself carries — written by any surface, the other window or the
        terminal, and read from disk on every ask. A badge alone would mean a folder sealed in a
        terminal could be written from a window that never looked at the terminal.
        """
        return intent.read_only(self.composer) or modes.sealed(self.app_dir, self.repo)

    def _declared_info(self) -> dict:
        """The folder's own declaration, spoken, plus the line the badge owes it.

        `note` is only filled when the two disagree: a window showing Change over a folder a terminal
        sealed is the one case where a refusal needs an explanation before it needs a retry.
        """
        row = modes.row_for(self.app_dir, self.repo)
        sealed = row.get("mode") == intent.READ
        return {"sealed": sealed, "mode": row.get("mode", ""),
                "by": intent.source(row.get("by", ""), arabic=self.arabic), "at": row.get("at", ""),
                "note": (intent.followed(row.get("by", ""), row.get("at", ""), arabic=self.arabic)
                         if sealed and not intent.read_only(self.composer) else "")}

    def write_refusal(self, what: str) -> str:
        """The sentence every write gate prints, and it names where the promise came from.

        "Switch to Change mode" is useless advice when the seal was set by a terminal the window has
        never seen, so the declaration gets the sentence that says who set it and how to lift it.
        """
        return modes.refusal(self.app_dir, self.repo, what, arabic=self.arabic,
                             badge=self.composer)

    def _branch_mode(self, key: str) -> str:
        """A chat with no folder can only answer in prose; a project keeps the position it was told to
        hold, wherever it was told.

        A seal outranks everything, because it is the only row that promises *less*: a folder somebody
        told to stay read-only opens read-only here too. A stored Change does not outrank the window's
        own memory of a folder the person left on Chat — that row is not a promise about files, and
        letting it win would turn "the tool remembers your choice" into "the tool overrules it".
        Everything else goes through `intent.normalise`, because a pref written by an older version of
        this file has to land on the promise that writes nothing.
        """
        if not key:
            return CHAT_COMPOSER
        if modes.sealed(self.app_dir, self.projects.get(key, "")):
            return intent.READ
        return intent.normalise(self._composer_pref.get(key, CHAT_COMPOSER))

    def _grant_folder(self, key: str, resolved: str) -> None:
        """Register a folder and open it — the one way a project branch is entered by granting.

        A folder never seen before lands on Change mode, because naming a folder and asking for work
        on it is what the user asked the window to do. A folder the person has since switched by hand —
        here, in the other window, or in a terminal — comes back exactly the way they left it, so the
        grant never overrules a choice.
        """
        self.projects.setdefault(key, resolved)
        told = bool(key in self._composer_pref or modes.mode_for(self.app_dir, resolved))
        self._select_branch(BRANCH_PROJECT, key,
                            composer=None if told else CHANGE_COMPOSER)

    def set_composer(self, value: str) -> None:
        """What the next Send is allowed to become: prose, a read-only analysis, or a reviewed diff."""
        wanted = intent.normalise(value)
        if wanted != CHAT_COMPOSER and not self.repo:
            self.status = intent.needs_folder(wanted, arabic=self.arabic)
            return
        if wanted == CHANGE_COMPOSER and self.branch.get("bound"):
            # The folder this chat reads is not a folder it may write. Leaving the chat is the way.
            self.status = ("A chat moved into a project reads that folder only. Open the project "
                           "itself from the sidebar to ask for reviewed changes to its files.")
            return
        self.composer = wanted
        lifted = modes.sealed(self.app_dir, self.repo)
        if self.branch.get("key") and not self.branch.get("bound"):
            self._composer_pref[self.branch["key"]] = wanted
            # Read and Change are the two positions that say what happens to this folder's files, so
            # either of them is written down for every surface to obey. Chat is not: it writes nothing
            # on its own, and choosing it over a sealed folder leaves the seal standing rather than
            # quietly lifting a protection somebody else asked for.
            if wanted in (intent.READ, intent.CHANGE):
                modes.declare(self.app_dir, self.repo, wanted, by=modes.WEB)
        # The switch is a property of Change mode. Reading masks it for this conversation and leaves
        # the folder's stored preference alone, so switching back is not a silent re-arm of anything.
        self.auto_apply = (bool(self._auto_pref.get(str(self.branch.get("key") or ""), False))
                           if wanted == CHANGE_COMPOSER and not self.branch.get("bound") else False)
        self.subtitle = self._subtitle()
        self._save_state()
        if lifted and wanted == CHANGE_COMPOSER:
            # The window is about to say a sentence that ends a promise made elsewhere, so it says
            # that instead of the ordinary one.
            self.say(intent.unchecked(arabic=self.arabic))
            return
        self.status = intent.switched(wanted, arabic=self.arabic,
                                      project=Path(self.repo).name if self.repo else "")

    def set_auto_apply(self, value) -> None:
        """Turn on the switch that removes the click between reviewing a diff and writing it.

        It is per folder and it is off until asked, because this is the one setting in the
        program that lets a program change a disk without being told to each time. Turning
        it on for one repo says nothing about the next folder opened in the same window.
        The review itself is untouched: the proposal is still built, hashed and shown, and
        two cases still stop for an answer — see `apply`.
        """
        if self.reading_only():
            if not bool(value):
                return                      # switching a switch that is already off asks nothing of anyone
            # A conversation that promises to write nothing cannot be handed the switch that writes
            # without a click. The folder's stored preference is left alone; only this conversation
            # refuses, which is the promise a mode is allowed to keep.
            self.status = intent.no_auto_apply(arabic=self.arabic)
            return
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
        self.refresh_plan_status()
        if not self.ledger:
            try:
                planbook.open_book(self.plans, Workspace(Path(self.repo)), self.plan_file)
            except (AgentError, OSError) as exc:
                self.plan_file = ""
                self._save_state()
                message = friendly_error(exc)
                self.say(message)
                self.stream({"kind": "toast", "text": message, "level": "bad"})
                return
        # Attaching a plan is a decision to implement it, so this is the one path that selects
        # Change mode on the user's behalf instead of leaving prose as the default.
        self.set_composer(CHANGE_COMPOSER)
        self._save_state()
        self.status = shared_note("plan_attached_chained" if self.chained
                                  else "plan_attached_plain", arabic=self.arabic)

    def clear_plan(self) -> None:
        self.plan_file = ""
        self.ledger_path = self.ledger = None
        self.refresh_plan_status()

    def set_mode(self, value: str) -> None:
        if value not in config.MODES:
            return
        with self._state:
            self.selections[self.active_mode] = self.model
            self.active_mode = self.mode = value
            self.model = self.selections.get(value, "")
            self.cloud_ok = False
            self.model_filter = ""
            self.subtitle = self._subtitle()
            refresh = not self.catalogs.get(value)
        if refresh:
            self.check_setup()

    # ------------------------------- connection -------------------------------
    def active_kind(self) -> config.Kind:
        return config.MODE_KIND.get(self.mode, config.DEFAULT_KIND)

    def endpoint_for(self, mode: str = "") -> str:
        """Where this provider row actually is — typed value, saved value, or its own default."""
        kind = config.MODE_KIND.get(mode or self.mode, config.DEFAULT_KIND)
        return self.endpoints.get(kind.key, "") or config.default_endpoint(kind, self.app_dir)

    def set_endpoint(self, value: str) -> None:
        """Point the active row somewhere else. Refused loudly, never half-applied.

        An endpoint says where the code and the key go, so a typo must not be stored and discovered
        mid-task: the URL is checked on the way in, and a value that cannot be a valid base leaves
        the field exactly as it was. A change also drops that row's catalog — a list of models from
        the old address is not a list of models at the new one.
        """
        kind = self.active_kind()
        text = str(value or "").strip()
        with self._state:
            if not text:
                self.endpoints.pop(kind.key, None)
            else:
                try:
                    self.endpoints[kind.key] = config.check_endpoint(kind, text)
                except AgentError as exc:
                    self.status = friendly_error(exc)
                    return
            for label, row in config.mode_rows():
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
            settings = config.load_profile(label, app_dir=self.app_dir) if label else None
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
        return connection.info(kind=kind, mode=self.mode, endpoint=endpoint,
                               default_endpoint=config.default_endpoint(kind, self.app_dir),
                               profile=self.profile, profiles=self.available_profiles(),
                               source=self.catalog_source.get(self.mode, ""),
                               key_present=bool(self.key.strip()
                                                or os.environ.get(kind.key_env or "")))

    def cloud_choice(self) -> tuple[bool, bool]:
        """``(cloud, paid)`` for the row on screen — one answer, used by all three send paths."""
        return connection.cloud_choice(self.active_kind(), self.mode, self.selected_entry(),
                                       self.endpoint_for())

    def task_settings(self, cloud: bool) -> Settings | None:
        """The Settings for a task on the current row, or None with the reason on the status line.

        An endpoint is checked when it is typed, but a row can still be unusable — Custom with
        nothing in the field — and refusing here is what keeps a half-built Settings away from
        ``make_provider``, which would otherwise fail inside a worker thread.
        """
        kind = self.active_kind()
        try:
            return config.settings_for(kind, self.endpoint_for(), app_dir=self.app_dir, model=self.model,
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

    # ------------------------------- configuration overrides -------------------------------
    def overrides_info(self) -> dict:
        """The signed rows, the ones this file refused, and what may be added.

        Shipped whole rather than fetched: it is a handful of rows, the drawer cannot draw the
        "what is live now" line without it, and a refusal a person hand-edited has to be visible the
        moment the section opens — that is the entire promise the file makes about its own signature.
        """
        return {"rows": overrides.reported(self.app_dir, arabic=self.arabic),
                "keys": overrides.fields(),
                "targets": [overrides.EVERY] + [kind.key for kind in config.KINDS],
                "path": str(overrides.path(self.app_dir)), "kind": self.active_kind().key,
                "note": overrides.scope(arabic=self.arabic)}

    def set_override(self, payload: dict) -> None:
        """Sign one row. Refused loudly and stored nowhere if it is not a row this tool would obey.

        The value goes through ``overrides.put``, which validates it with the same ``validate`` a
        profile is judged by — so a number out of range, a credential-shaped string, a wildcard address
        and a key the schema does not know are all refused here rather than dropped silently later.
        """
        try:
            row = overrides.put(self.app_dir, str(payload.get("target", "")),
                                str(payload.get("key", "")), payload.get("value", ""),
                                by=modes.WEB, arabic=self.arabic)
        except AgentError as exc:
            self.status = friendly_error(exc)
            return
        self.status = overrides.written(row, arabic=self.arabic)

    def unset_override(self, payload: dict) -> None:
        """Remove one row, or say there was nothing to remove — a silent no-op reads as a save."""
        gone = overrides.delete(self.app_dir, str(payload.get("target", "")),
                                str(payload.get("key", "")))
        self.status = overrides.removed(str(payload.get("key", "")), arabic=self.arabic) if gone \
            else overrides.absent(str(payload.get("key", "")), arabic=self.arabic)

    def set_consent(self, value: bool) -> None:
        self.cloud_ok = bool(value)

    def timeout_seconds(self) -> int:
        return config.clamp_request_timeout(self.request_timeout)

    # ------------------------------ model catalog ------------------------------
    def selected_entry(self) -> dict | None:
        return next((entry for entry in self.catalogs.get(self.mode, []) if entry["id"] == self.model), None)

    def visible_models(self, mode: str | None = None) -> list[dict]:
        """The catalog as filtered — a view, never a mutation of what was loaded.

        See `connection.filter_models` for why the subset is a new list and why a search cannot
        deselect the model a running task is using.
        """
        return connection.filter_models(
            self.catalogs.get(mode if mode is not None else self.mode, []), self.model_filter)

    def model_changed(self) -> None:
        self.selections[self.mode] = self.model
        self.subtitle = self._subtitle()
        self._save_state()

    def _model_info(self) -> str:
        return connection.model_info(self.selected_entry(),
                                     loaded=len(self.catalogs.get(self.mode, [])),
                                     shown=len(self.visible_models()),
                                     query=self.model_filter, endpoint=self.endpoint_for())

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
            elif not self.model and any(entry["id"] == config.DEFAULT_MODEL for entry in catalog):
                self.model = config.DEFAULT_MODEL
            self.model_changed()
            self.status = catalog_status_line(arabic=self.arabic, count=len(catalog),
                                              model=self.model, label=selected_mode,
                                              live=source == LIVE)
            self._note("catalog", f"{selected_mode}: {len(catalog)} models ({source}).")

        operation = lambda: models_for(kind, endpoint, api_key)
        self.run_job(operation, done, "Refreshing available models…")

    # ------------------------------ first run ------------------------------
    def setup_view(self) -> dict:
        """The card, from rows that were computed once. Asking the provider is never a side effect.

        A snapshot goes out on every streamed log line, and building the audit asks a service over the
        network and walks a folder — so the rows are stored, and only the operator's click refreshes
        them.
        """
        if self._setup_open and not self._setup_rows:
            self._setup_rows = setup.audit(repo=self.repo.strip(), provider=self.active_kind().key,
                                           endpoint=self.endpoint_for(self.mode),
                                           api_key=self.key.strip() or None, model=self.model,
                                           arabic=self.arabic, demo=self._setup_demo, probe=False)
        counts = setup.counts(self._setup_rows)
        # The tally travels as a sentence: the card's numbers are the server's verdict in the window's
        # language, not a string the front end assembles from bare counts.
        return {"show": self._setup_open, "rows": self._setup_rows, "counts": counts,
                "tally": setup.tally(counts, arabic=self.arabic),
                "demo": self._setup_demo, "busy": self.busy}

    def run_setup_check(self) -> None:
        """Ask what this machine can reach. The one click that sends a request for the audit."""
        if self.busy:
            return
        kind, endpoint = self.active_kind(), self.endpoint_for(self.mode)
        repo, model, arabic = self.repo.strip(), self.model, self.arabic
        api_key = self.key.strip() or None
        previous = self._setup_demo

        def work():
            return setup.audit(repo=repo, provider=kind.key, endpoint=endpoint, api_key=api_key,
                               model=model, arabic=arabic, demo=previous)

        def done(rows):
            self._setup_rows = rows
            self._setup_open = True
            self.say(setup.checks_done(setup.counts(rows), arabic=arabic))

        self.run_job(work, done, "Checking this machine…")

    def run_setup_demo(self) -> None:
        """The offline proof: a temporary folder, a proposal, an apply, a rollback. Never this project."""
        if self.busy:
            return
        arabic = self.arabic

        def done(result):
            self._setup_demo = result
            if self._setup_rows:
                self._setup_rows = [setup.demo_row(result, arabic=arabic) if item["id"] == "demo" else item
                                    for item in self._setup_rows]
            self._setup_open = True
            self.say(setup.proof_done(result.get("proposal_apply_rollback") == "passed",
                                     result.get("note", ""), arabic=arabic))

        self.run_job(setup.run_demo, done, "Running the offline proof…")

    def set_setup_open(self, value: bool) -> None:
        """The card's own two buttons: show it again, or say this machine has already read it."""
        self._setup_open = bool(value)
        if not value:
            self._saved_ui["setup_seen"] = True
            self._save_state()

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

    def _metrics_info(self) -> dict:
        """The token counts the provider measured for the task on screen, if it measured any.

        An empty dict rather than zeros, in both directions: before a task has run, and after a provider
        that reports nothing. The drawer has to be able to tell "the model cost nothing" from "nobody
        said what it cost", and only the absence of a number keeps that honest.
        """
        return dict((self.session or {}).get("metrics") or {})

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
        """Map, conversation and what is left — see `projects.context_use`.

        Only a conversation that reads this folder can spend its budget here, which is the one thing
        this side has to answer before the measurement is taken.
        """
        chat = self.chat if self.branch.get("key") == key and self.chat else None
        return projects.context_use(path, notes, chat)

    def _toolchain_info(self, path: Path, key: str, exists: bool) -> dict:
        # ``self.recipe`` is a label chosen for the branch in front of the user; another project's
        # drawer can only report what would be picked by default.
        return projects.toolchain(path, exists=exists, request_timeout=self.request_timeout,
                                  selected_hint=(self.selected_recipe()
                                                 if self.branch.get("key") == key else None))

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
            # The one place this window wrote an exception's own text to a surface the browser
            # reads. A Windows `OSError` names a path, and a path in the status line is a piece of
            # the file system nobody granted. The reason still gets recorded, redacted, in Activity.
            self.status = "Could not open the folder."
            self._note("error", "Could not open the folder: " + redact(str(exc))[:120])
            return
        self.status = "Opened " + path.name + " in the file manager."

    def remove_project(self, key: str) -> None:
        """Remove a project from the sidebar list."""
        if not key:
            return
        with self._state:
            folder = self.projects.pop(key, None)
            if not hasattr(self, "_removed_projects"):
                self._removed_projects = set()
            self._removed_projects.add(key)
            self._saved_ui["removed_projects"] = list(self._removed_projects)
            if self.repo and (self.branch.get("key") == key or (folder and str(Path(self.repo).resolve()) == str(Path(folder).resolve()))):
                self._select_branch(BRANCH_CHAT, "")
            self._save_state()
            self.say("Removed project from the sidebar.")

    def save_memory(self, text: str) -> None:
        if not self.repo:
            self.status = status_text("need_folder_notes", arabic=self.arabic)
            return
        try:
            memory_store.write(self.memory_dir, self.repo, str(text).rstrip("\n"))
        except (AgentError, OSError) as exc:
            self.status = friendly_error(exc)
            return
        self.status = shared_note("notes_saved", arabic=self.arabic)
        self._note("memory", "Project notes updated.")

    # ------------------------------- planning -------------------------------
    def quoted(self, index: object) -> tuple[str, str] | None:
        """The message the next one answers, read out of this window's own record.

        Resolved here rather than sent as text on purpose: a quotation has to be what was actually
        said, and a payload can carry any words the sender likes. An index this thread does not have —
        a message that arrived after the click, a task that was switched out from under it — answers as
        no reference at all, so the message still sends.
        """
        try:
            position = int(index)
        except (TypeError, ValueError):
            return None
        rows = self.messages
        if not 0 <= position < len(rows):
            return None
        words = " ".join(str(rows[position].get("text", "")).split())
        if not words:
            return None
        return str(rows[position].get("role", "assistant")), words[:QUOTE_CHARS]

    def with_quote(self, asked: str, index: object) -> str:
        """The message the model will read: the reference block, then what was typed.

        The block is written in the language of the message being sent, not of the session still on
        screen — `self.arabic` reads the latter until this turn exists, and a sentence the operator is
        about to answer belongs to the question they just asked.
        """
        quoted = self.quoted(index)
        if not quoted:
            return asked
        return quote_reference(is_arabic(asked), quoted[0], quoted[1]) + asked

    def start_plan(self, text: str, quote_of: object = None, *, reference: str = "",
                   step_id: int | None = None) -> None:
        if self.busy:
            # Not a mistake to swallow. The window's own busy flag is a race when two clicks land in
            # one tick — a second Send then reaches the server while the first is still starting — and
            # dropping it loses a written prompt with no message and no trace. The queue is the
            # mechanism that already means "said while a task was running".
            self.queue_add(text, quote_of, reference=reference)
            return
        repo, asked = self.repo.strip(), (text or "").strip()
        # Composed before anything measures it, so the length ceiling covers what the model reads.
        # `asked` stays the operator's own words for the two questions that are only about those words:
        # whether there is a message at all, and whether it asks for a change — a quoted "fix add" must
        # not turn a question about that old task into a new change request.
        task = reference + asked if reference else self.with_quote(asked, quote_of)
        queued_reference = task[:-len(asked)] if asked else ""
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
        chained_plan = bool(repo and plan_file and (self.chained or step_id is not None))
        if (not asked and not chained_plan) or len(task) > MAX_TASK_CHARS or not self.model:
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
        # The position is decided before the message is read. `asks_for_a_change` exists to promote a
        # chat that turned out to name files, and promoting is exactly what Read-only refuses — so an
        # imperative here gets the analysis it is allowed, and the row above the answer says why no
        # diff follows it.
        if self.reading_only():
            self.start_chat(task, settings, cloud, paid, key, intent.no_proposal(arabic=self.arabic),
                            asked=asked, quote_of=quote_of)
            return
        # A bound project answers in prose by default: bound means it may *read* that project,
        # never that a greeting became a change request. A message that opens with "add" or
        # "صلح" is a different thing, and it is planned as a change — for this message only,
        # because remembering the route would turn the next "thanks" into a rejected diff.
        as_change = bool(repo) and self.composer == CHAT_COMPOSER and asks_for_a_change(asked)
        if not repo or (self.composer == CHAT_COMPOSER and not as_change):
            self.start_chat(task, settings, cloud, paid, key, asked=asked, quote_of=quote_of)
            return
        if plan_file:
            try:
                read_plan_reference(Workspace(Path(repo)), plan_file, settings)
            except (AgentError, OSError) as exc:
                self.status = friendly_error(exc)
                return
        if chained_plan:
            try:
                ledger_path, book = planbook.open_book(self.plans, Workspace(Path(repo)), plan_file)
                if step_id is not None:
                    row = planbook.step(book, int(step_id))
                    if row is None:
                        raise PolicyError(f"Plan step {step_id} not found.")
                else:
                    row = planbook.current(book)
                    if row is None:
                        if not asked:
                            raise PolicyError("Every step of this plan is already verified by a passing run.")
                        chained_plan = False
                        self.plan_file = ""
                    else:
                        step_id = row["id"]
                if chained_plan and row is not None:
                    task = planbook.task_for(book, row, task if (task and task != row.get("title")) else "")
            except (AgentError, OSError) as exc:
                self.say(friendly_error(exc))
                return
            if chained_plan:
                self.ledger_path, self.ledger = ledger_path, book
        prior = self._unverified_prior(self.chat_id, repo)
        if prior:
            note = shared_note("prior_write", arabic=self.arabic,
                                task=str(prior.get("task", ""))[:60], state=prior["state"])
            if prior["state"] in INTERRUPTED_STATES:
                if not self.confirm("Unfinished task",
                                    note + shared_note("prior_continue", arabic=self.arabic),
                                    ok_label="Continue anyway"):
                    self.status = note + shared_note("prior_blocked", arabic=self.arabic)
                    return
                self._add("tool", "Tool", note + shared_note("prior_continued", arabic=self.arabic))
            else:
                self._add("tool", "Tool", note + shared_note("prior_stacked", arabic=self.arabic))
                self.status = status_text("prior_unverified", arabic=self.arabic)
        self.title = asked.replace("\n", " ")[:45]
        self._add("user", "You", task, quote_of=quote_of)
        if as_change:
            self._add("tool", "Tool", "This branch is in Chat mode, so the answer would have been "
                                      "prose. The message asks for a change, so it is planned as a "
                                      "proposal instead: review the diff, then Apply to write it. "
                                      "The next message is Chat again.")
        self.session = self.session_path = None
        notes = self._project_notes()
        if notes:
            self._add("tool", "Tool", shared_note("notes_in_request", arabic=self.arabic,
                                                      count=len(notes)))
        chat_id = self.chat_id

        def work():
            provider = make_provider(settings, allow_cloud=cloud,
                                     data_class="public" if cloud else "restricted",
                                     api_key=key, allow_paid=paid)
            # A model turn has no row of its own, so its writing streams to the one place built for
            # text that is still arriving: the Activity panel. A JSON envelope is not prose, and the
            # reader's question during a turn is "is it still working", which is what this answers.
            feed = LineFeed(self._build_line) if getattr(provider, "supports_stream", False) else None
            try:
                return plan(Workspace(Path(repo)), task, provider, settings, self.runs,
                            progress=lambda line: self._progress(line),
                            step=self._step, on_token=(feed.feed if feed else None),
                            cancelled=self.cancel_event.is_set, plan_file=plan_file, chat_id=chat_id,
                            plan_step=step_id, memory=notes)
            finally:
                if feed:
                    feed.close()

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
        self.run_job(work, done, running, cancellable=True,
                     on_busy=lambda: self.queue_add(asked or task, reference=queued_reference))

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
        with self._state:
            # The compare and the append are one decision: two workers announcing the same read in the
            # same tick must collapse to one row, and checking outside the lock would let both add one.
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
        with self._state:
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
        # A copy taken under the lock, then searched outside it: `_detail_for` reads the session on
        # disk, and no reason exists to hold the window's state while a file is being opened.
        with self._state:
            messages = list(self.messages)
        for message in reversed(messages):
            step = message.get("step") or {}
            if step.get("id") == step_id:
                block = self._detail_for(step)
                # A row that cannot answer still answers in words: a click that does nothing is the
                # dead-button failure this window already had once.
                self.step_detail = block or missing
                return
        # A row the server has never heard of means the page is behind the task.
        self.step_detail = missing

    def open_graph(self) -> dict:
        """The module graph behind the Sources card, walked when the button is pressed.

        It answers the click rather than riding the snapshot: building it walks the tree and parses every
        source file, and a snapshot goes out on every streamed log line. The reply travels as `result`,
        so nothing is stored and there is no stale copy to invalidate when the folder changes.
        """
        blank = {"nodes": [], "edges": [], "columns": 0, "cyclic": False, "hidden": 0,
                 "caption": "", "note": graph_empty_line(arabic=self.arabic)}
        root = Path(self.repo) if self.repo else None
        if root is None or not root.is_dir():
            return blank
        try:
            _files, rows = Workspace(root).index()
        except (PolicyError, OSError):
            return blank
        data = symbols.graph(rows)
        if not data["nodes"]:
            return blank
        return {**data, "caption": graph_caption(
            self.arabic, nodes=len(data["nodes"]), edges=len(data["edges"]),
            cyclic=data["cyclic"], hidden=data["hidden"])}

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
        elif action == "model_reasoning":
            words = str(fields.get("detail") or "")
            if not words:
                return None
            sections = [[detail_section(self.arabic, "reasoning"), words.splitlines()]]
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
        self._stream(text, "log_chunk")

    def _stream(self, text: str, kind: str) -> None:
        """One piece of untrusted output, already redacted, said on the SSE channel and nowhere else.

        One owner because both consumers are the same promise with a different destination: build
        output goes to the live row, a model's answer goes to the bubble, and neither may reach the
        browser unscrubbed or unbounded. A snapshot would be wrong for both — the stored log keeps the
        run's summary, and a streamed answer is not finished yet.
        """
        self._emit({"kind": kind, "ts": _clock(), "text": text})

    def _token(self, text: str) -> None:
        """One line of a model's answer, arriving while the rest of it is still being generated."""
        text = redact(text).strip()[:500]
        if text:
            self._stream(text, "token")

    def start_chat(self, task: str, settings, cloud: bool, paid: bool, key: str | None,
                   note: str = "", asked: str = "", *, quote_of: object = None) -> None:
        """Prose answer. With a bound project it may read that folder's map; never write to it.

        `note` is a line the *mode* puts above its own answer. Read-only uses it for the refusal, which
        has to be part of the transcript — a status line is replaced by the next click, and "this
        conversation builds no proposal" is something the operator reads back afterwards.

        `asked` is the same message without its reference block. The chat is titled from it, because a
        conversation called `> [In reference to your earlier message: …` has stopped naming the question.
        """
        if self.chat is None or self.chat.get("id") != self.chat_id:
            self.chat = create_chat(self.model or Settings().model, self.chat_id,
                                    project=self._chat_project())
        chat = self.chat
        self.session = self.session_path = None
        self.title = chat.get("title") or (asked or task).replace("\n", " ")[:45]
        self._add("user", "You", task, quote_of=quote_of)
        if note:
            self.line("tool", "Tool", note)
        repo = self.repo

        def work():
            provider = make_provider(settings, allow_cloud=cloud,
                                     data_class="public" if cloud else "restricted",
                                     api_key=key, allow_paid=paid)
            # A stream is offered only to a model that says it can hold one, and the answer it produces
            # is the same one the buffered call returns: what arrives here is for the reader's sake.
            feed = LineFeed(self._token) if getattr(provider, "supports_stream", False) else None
            try:
                return respond(chat, provider, task, settings, self.chats,
                               context=self._chat_context(repo),
                               on_token=(feed.feed if feed else None))
            finally:
                if feed:
                    feed.close()

        def done(reply):
            self._add("assistant", "AI Code Engineer", reply)
            self.title = title_for(chat)
            self.status = (intent.answered(arabic=self.arabic) if self.reading_only() else
                           "Answered. Switch to Change mode when you want reviewed changes to these files."
                           if repo else
                           "Answered. Choose a project when you want reviewed changes to real files.")

        typed = asked or asked_of(task)
        reference = task[:-len(typed)] if typed else ""
        self.run_job(work, done, "Thinking…",
                     on_busy=lambda: self.queue_add(typed, reference=reference))

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
        with self._state:
            if self.busy and self.cancellable:
                self.cancel_event.set()
                self.status = shared_note("stop_requested", arabic=self.arabic)
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
    def approval_notice(self, session: dict | None = None) -> str:
        """The dialog text both windows show, from one owner: the removals and the unexpected files."""
        return repair.approval_advisories(session if session is not None else self.session)

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
        if self.reading_only():
            # The answer above may show a block in full, and this is the click that would turn it into
            # a proposal — so it is refused by name, not by the block being hidden.
            self.say(intent.no_proposal(arabic=self.arabic))
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
        if self.reading_only():
            # A proposal built before the badge moved is still on the screen, and the mode is a
            # position about this folder — not about whether a diff happens to be rendered right now.
            self.say(self.write_refusal("Apply"))
            return
        if self.rejected():
            self.say(say(self.arabic,
                         en="This proposal was declined. Reopen it for review before applying it.",
                         ar="رُفض هذا المقترح. أعد فتحه للمراجعة قبل تطبيقه."))
            return
        changes = self.session.get("changes", [])
        again = ""
        if self._auto_fix and self.selected_recipe():
            again = shared_note("apply_rerun_warning", arabic=self.arabic,
                                label=runner.RECIPES[self.selected_recipe()]["label"])
        notice = self.approval_notice()
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
            self.status = shared_note("syntax_failed" if result["status"] == "failed"
                                        else "syntax_clean", arabic=self.arabic)

        self.run_job(lambda: verify(path), done, "Checking syntax in changed files…")

    def rejected(self) -> bool:
        """Whether this window has already declined the proposal on screen.

        Read off the session's own events rather than kept in a flag beside them: a refusal belongs to
        one proposal hash, so a repair round that authors a different change is answerable again, and
        a task reopened after a restart brings its refusal back rather than offering Apply anew.
        """
        return proposal_rejected(self.session or {})

    def reopen(self) -> None:
        """Reopen for review only: even Auto-Apply must wait for another decision."""
        if self.busy or not self.session or not self.session_path or not self.rejected():
            return
        if self.reading_only():
            self.say(self.write_refusal("Reopen"))
            return
        self.session = reopen_proposal(self.session_path, self.session["proposal_hash"])
        self.line("tool", "Tool", say(self.arabic,
                  en="Proposal reopened for review. Nothing was written.",
                  ar="أُعيد فتح المقترح للمراجعة. لم تُكتب أي ملفات."))
        self.say(say(self.arabic, en="Review the proposal before applying it.",
                     ar="راجع المقترح قبل تطبيقه."))

    def reject(self) -> None:
        """Decline a proposal without discarding the work behind it.

        Nothing is written, so nothing needs rolling back — but the answer is recorded in the task's
        session, because "no" is a fact about the run that an exported session should carry, and
        because the refusal has to outlive the click that made it. The files are untouched either way,
        which is the whole promise this window has always made about a proposal.
        """
        if self.busy:
            return
        if self.reading_only():
            # Declining a write is not itself a write, and yet a sealed folder gets the same answer as
            # every other verb here: the mode is a position about this folder, not about whichever
            # diff happens to be on screen.
            self.say(self.write_refusal("Reject"))
            return
        changes = (self.session or {}).get("changes") or []
        if not changes:
            self.say(shared_note("proposal_reject_nothing", arabic=self.arabic))
            return
        if self.rejected():
            self.say(shared_note("proposal_reject_twice", arabic=self.arabic))
            return
        with self._state:
            record_event(self.session, "proposal_rejected",
                         hash=str(self.session.get("proposal_hash", "")))
            if self.session_path:
                atomic_json(self.session_path, self.session)
        text = rejected_note(arabic=self.arabic, count=len(changes))
        # `line` rather than `_add`, and no log row of its own: the thread and the card carry the answer,
        # and a reopened task shows it in the raw block from the record this just wrote. Every sink this
        # window speaks through is capped on purpose (tests/test_host.py).
        self.line("tool", "Tool", text)
        self.say(say(self.arabic, en="Proposal declined. Nothing was written.",
                     ar="رُفض المقترح. لم تُكتَب أي ملفات."))

    def undo(self) -> None:
        if self.busy:
            return
        if self.reading_only():
            # Rolling back is a write with a friendly name: it puts different bytes on the same paths.
            self.say(self.write_refusal("Roll back"))
            return
        if not self.session or self.session.get("state") not in MUTABLE_STATES | {"PARTIAL_APPLY", "APPLYING"}:
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
                self._add("tool", "Tool", shared_note("step_reopened", arabic=self.arabic, step=step_id))

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
        if self.reading_only():
            # The escalation is the larger write, so it cannot be the door left open: this replaces
            # bytes on disk from a commit, which is exactly what the position in front of it refuses.
            self.say(self.write_refusal("Restoring files from git"))
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
        if self.reading_only():
            # A branch switch is not a read: git rewrites the tracked files to match the commit you
            # move to, which is a larger change than any proposal this window would have asked about.
            self.say(self.write_refusal("Switching branches"))
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
        """Which folders in this project have a command, and what each one answers to.

        A single-project folder produces one target and the picker is not shown; a reactor or a
        monorepo produces one per module, because `backend/pom.xml` was always invisible to a scan
        that only looked at the opened folder's own children.
        """
        try:
            self.targets = (runner.targets(Path(self.repo))
                            if self.repo and Path(self.repo).is_dir() else [])
            self._targets_root = str(Path(self.repo).resolve()) if self.targets else ""
        except OSError:
            self.targets, self._targets_root = [], ""
        if not any(row["path"] == self.target for row in self.targets):
            self.target = self.targets[0]["path"] if self.targets else ""
        self.recipes = next((row["recipes"] for row in self.targets if row["path"] == self.target), [])
        labels = [runner.RECIPES[name]["label"] for name in self.recipes]
        if self.recipe not in labels:
            self.recipe = labels[0] if labels else ""

    def set_target(self, label: str) -> None:
        """Choose which module of this project the command runs in — by the label it was shown as."""
        chosen = next((row for row in self.targets if row["label"] == label), None)
        if chosen and chosen["path"] != self.target:
            self.target = chosen["path"]
            self.refresh_recipes()

    def target_label(self) -> str:
        return session_flow.label_for(self.targets, self.target)

    def selected_recipe(self) -> str | None:
        return session_flow.recipe_for(self.recipes, self.recipe)

    # The only states that lock the Run control: work in front of the user that has not been written
    # yet. Every other state -- including a task that blocked, cancelled or rolled back, and a folder
    # with no task at all -- has files on disk that a project command can still be pointed at, which
    # is what D28 was about: the build used to be unreachable exactly when the last task went wrong.
    RUN_LOCKED_STATES = {"DISCOVERING", "WAITING_APPROVAL"}

    def run_status_info(self) -> dict:
        """Whether each Run control is pressable — the answer `runresults.gate` builds from what
        this window holds, because the gate is presentation and the state underneath it is ours."""
        state = self.session.get("state") if self.session else ""
        return runresults.gate(
            has_project=bool(self.repo and Path(self.repo).is_dir()),
            busy=self.busy,
            waiting_approval=state in self.RUN_LOCKED_STATES,
            has_recipes=bool(self.recipes),
            sandbox_requested=self.sandbox_on,
            docker_available=runner.sandbox_available())

    def _can_run(self) -> bool:
        state = self.session.get("state") if self.session else ""
        return runresults.can_run(has_project=bool(self.repo and Path(self.repo).is_dir()),
                                  busy=self.busy,
                                  waiting_approval=state in self.RUN_LOCKED_STATES,
                                  has_recipes=bool(self.recipes))

    def sandbox_info(self) -> dict:
        """The container choice as the card draws it: the two values, whether this machine can honour
        them, and the one sentence saying what pressing Run will do now.

        The sentence is the same four answers the desktop window gives, out of `labels`, so a user who
        switches windows is not answering a different question.
        """
        image = self.sandbox_image.strip()
        available = runner.sandbox_available()
        return {"on": bool(self.sandbox_on and available), "image": image, "available": available,
                "note": shared_note(runner.sandbox_state(self.sandbox_on, image, available),
                                    arabic=self.arabic)}

    def set_sandbox(self, payload: dict) -> None:
        """A tick, a typed digest, or both — the card sends what it holds and the answer comes back."""
        if "on" in payload:
            self.sandbox_on = bool(payload.get("on"))
        if "image" in payload:
            self.sandbox_image = str(payload.get("image") or "")
        self._save_state()

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
        # The folder comes from the task when there is one and from the window when the last task
        # blocked or rolled back. A run is recorded on the session only when that session can hold
        # it -- `repair.record_run` refuses any state outside its own set, and writing into one of
        # those would rewrite a finished task's verdict.
        repo = self.session["root"] if self.session else self.repo.strip()
        # Read at the click, not kept on the window: unticking the box has to stop the container run,
        # and an image typed while it was unticked is not an answer to anything.
        sandbox = self.sandbox_image.strip() if self.sandbox_on else ""
        command = runner.display_command(recipe) + (" in Docker" if sandbox else "")
        if self.reading_only():
            # Reading a folder is not running inside it, and this is the only action in the mode that
            # can execute anything: a build runs whatever its own scripts do. So it asks once per
            # command, and names the command the answer is about.
            if not self.ask("Run this command?", intent.run_ask(
                    command, arabic=self.arabic, project=Path(repo).name), ok_label="Run it"):
                self.say(intent.run_declined(arabic=self.arabic))
                return
            # What it never runs is a fix round, because the output of a round is a proposal. That is
            # said where the loop would have started — `_offer_fix`, on a real failure — rather than as
            # a warning about something that may not happen.
            auto_fix = False
        self._auto_fix = bool(auto_fix)
        if auto_fix:
            self._fix_round = 0
        # The module list was scanned from the window's folder. A task pointed somewhere else gets
        # its own root and no module, rather than a path resolved against the wrong tree.
        target = self.target if str(Path(repo).resolve()) == self._targets_root else "."
        path = self.session_path
        recordable = bool(path) and state in MUTABLE_STATES
        label = runner.RECIPES[recipe]["label"]
        where = Path(repo).name if target in ("", ".") else PurePosixPath(target).name
        # Say the command before running it, not only after it fails: a project's own build
        # executes code the repository defines, and this is the last line worth reading first.
        self._run_step = self._step(executing_line(arabic=self.arabic, command=command),
                                    action="executing", fields={"command": command})

        def work():
            result = runner.run(Path(repo), recipe, timeout=runner.timeout_for(recipe),
                                progress=self._build_line, target=target, sandbox=sandbox)
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

        self.run_job(work, done, "Running %s in %s%s…" % (label, where, " Docker" if sandbox else ""))

    def report_run(self, result) -> None:
        self._last_job = {"type": "tests", **result}
        summary = runner.summarize(result)
        self.run_info = summary
        self._settle_run_step(result)
        # One row either way: the sentence is `runresults`' business, the state under it is ours.
        self._add("tool", "Checks", runresults.result_row(result, summary))
        if result["status"] == "passed":
            self._auto_fix = False
            self.status = status_text("command_passed", arabic=self.arabic) + summary
        else:
            if self._auto_fix and result["status"] in {"failed", "timeout"}:
                self.ask_for_fix(result)
                return
            self._auto_fix = False
            self.status = summary + " — the captured output is in the Checks tab."
            if result["status"] in {"failed", "timeout"} and self._offer_fix(result):
                return
        self.advance_plan(result)

    def run_app_service(self, payload: dict | None = None) -> dict:
        if not self.repo or not Path(self.repo).is_dir():
            raise PolicyError("No project folder selected.")
        payload = payload or {}
        repo = Path(self.repo).resolve()
        cfg = service_runner.read_project_config(repo)
        
        name = str(payload.get("name") or "app")
        command = payload.get("command") or cfg.get("app", {}).get("command")
        if not command and cfg.get("services"):
            first_svc = cfg["services"][0]
            name = first_svc.get("name", "app")
            command = first_svc.get("command")
        if not command:
            recipe = self.selected_recipe()
            if recipe:
                command = runner.display_command(recipe)
            else:
                command = "python main.py" if (repo / "main.py").is_file() else "npm start"

        cwd_rel = payload.get("cwd") or cfg.get("app", {}).get("cwd", ".")
        cwd = (repo / cwd_rel).resolve()
        port = payload.get("port") or cfg.get("app", {}).get("port")
        if port:
            try:
                port = int(port)
            except ValueError:
                port = None

        svc_id = f"{repo.name}:{name}"
        
        def on_line(line):
            self._build_line(f"[{name}] {line}")
            
        svc = service_runner.GLOBAL_SERVICES.start_service(
            svc_id, name, command, cwd, port=port, on_output=on_line
        )
        self.say(f"Service '{name}' started.")
        self.line("tool", "App", f"🚀 Started service '{name}' ({command}) in {cwd.name}")
        return svc.snapshot()

    def stop_app_service(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        repo_name = Path(self.repo).name if self.repo else ""
        name = str(payload.get("name") or "app")
        svc_id = payload.get("id") or f"{repo_name}:{name}"
        service_runner.GLOBAL_SERVICES.stop_service(svc_id)
        self.say(f"Service '{name}' stopped.")
        self.line("tool", "App", f"⏹ Stopped service '{name}'")
        return {"stopped": True, "id": svc_id}

    def restart_app_service(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        repo_name = Path(self.repo).name if self.repo else ""
        name = str(payload.get("name") or "app")
        svc_id = payload.get("id") or f"{repo_name}:{name}"
        service_runner.GLOBAL_SERVICES.restart_service(svc_id)
        self.say(f"Service '{name}' restarted.")
        return {"restarted": True, "id": svc_id}

    def stop_all_app_services(self) -> dict:
        service_runner.GLOBAL_SERVICES.stop_all()
        self.say("All services stopped.")
        self.line("tool", "App", "⏹ Stopped all services.")
        return {"stopped_all": True}

    def run_build_action(self) -> dict:
        if not self.repo or not Path(self.repo).is_dir():
            raise PolicyError("No project folder selected.")
        repo = Path(self.repo).resolve()
        cfg = service_runner.read_project_config(repo)
        command = cfg.get("build", {}).get("command")
        if not command:
            recipe = self.selected_recipe()
            if recipe and "compile" in recipe:
                command = runner.display_command(recipe)
            elif (repo / "pom.xml").is_file():
                command = "mvn compile"
            elif (repo / "package.json").is_file():
                command = "npm run build"
            elif (repo / "Cargo.toml").is_file():
                command = "cargo build"
            else:
                command = "python -m py_compile"

        cwd = (repo / cfg.get("build", {}).get("cwd", ".")).resolve()
        self.say(f"Building: {command}…")
        res = service_runner.execute_bounded_job(command, cwd, timeout=600, on_output=self._build_line)
        self._last_job = {"type": "build", **res}
        outcome = "succeeded" if res["success"] else "FAILED"
        self.say(f"Build {outcome} (exit {res['exit_code']}, {res['duration']}s)")
        self.line("tool", "Build", f"🔨 Build {outcome} — {command} (exit {res['exit_code']}, {res['duration']}s)")
        return res

    def get_service_lines(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        repo_name = Path(self.repo).name if self.repo else ""
        name = str(payload.get("name") or "app")
        svc_id = payload.get("id") or f"{repo_name}:{name}"
        svc = service_runner.GLOBAL_SERVICES.get_service(svc_id)
        lines = svc.get_lines(int(payload.get("max_lines") or 500)) if svc else []
        return {"id": svc_id, "lines": lines}

    def get_project_readiness(self) -> dict:
        if not self.repo or not Path(self.repo).is_dir():
            return {"tools": [], "wrappers": [], "configs": [], "recommendations": []}
        res = service_runner.check_project_readiness(Path(self.repo))
        self._cached_readiness = res
        return res

    def get_project_run_config(self) -> dict:
        if not self.repo or not Path(self.repo).is_dir():
            return {}
        return service_runner.read_project_config(Path(self.repo))

    def save_project_run_config(self, payload: dict) -> dict:
        if not self.repo or not Path(self.repo).is_dir():
            raise PolicyError("No project folder selected.")
        config_data = payload.get("config") or {}
        service_runner.save_project_config(Path(self.repo), config_data)
        self.say("Project run configuration saved.")
        return {"saved": True}

    def run_api_test(self, payload: dict) -> dict:
        url = str(payload.get("url") or "").strip()
        method = str(payload.get("method") or "GET").upper()
        if not url.startswith(("http://", "https://")):
            raise PolicyError("API test requires a valid http:// or https:// URL.")
        headers = dict(payload.get("headers") or {})
        body_text = payload.get("body")
        data = body_text.encode("utf-8") if body_text else None
        
        req = urllib.request.Request(url, data=data, method=method)
        for h, v in headers.items():
            req.add_header(h, v)
        if "User-Agent" not in headers:
            req.add_header("User-Agent", "AICodeEngineer-APITester")

        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                resp_body = resp.read(100_000).decode("utf-8", errors="replace")
                duration = round(time.monotonic() - start, 2)
                return {
                    "ok": True,
                    "status": resp.status,
                    "duration": duration,
                    "headers": dict(resp.headers),
                    "body": resp_body,
                }
        except urllib.error.HTTPError as e:
            err_body = e.read(50_000).decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            duration = round(time.monotonic() - start, 2)
            return {
                "ok": False,
                "status": e.code,
                "duration": duration,
                "headers": dict(e.headers) if hasattr(e, "headers") else {},
                "body": err_body,
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": 0,
                "error": str(exc),
                "duration": round(time.monotonic() - start, 2),
                "body": "",
            }

    def fix_run_failure(self, payload: dict | None = None) -> None:
        payload = payload or {}
        source = str(payload.get("source") or "check")
        last_job = getattr(self, "_last_job", {}) or {}
        diagnosis = payload.get("diagnosis") or last_job.get("diagnosis", {})
        tail = payload.get("output") or last_job.get("output", "")
        if not tail and self.run_info:
            tail = self.run_info

        round_num = getattr(self, "_fix_round", 0) + 1
        self._fix_round = round_num
        if round_num > 3:
            self.line("tool", "Fix", "🛑 Maximum auto-fix attempts reached (3). Manual review required.")
            self.say("Auto-fix limit reached.")
            return

        summary = diagnosis.get("summary", "execution failure")
        prev = getattr(self, "_last_fix_summary", "")
        if prev and prev == summary and round_num > 1:
            self.line("tool", "Fix", f"⚠️ The exact same failure repeated ('{summary}'). Stopping fix loop to prevent runaway cycles.")
            self.say("Identical failure repeated; manual intervention needed.")
            return
        self._last_fix_summary = summary

        batch_approved = bool(payload.get("batch_approved", False))
        max_attempts = int(payload.get("max_attempts") or 3)
        self._repair_batch = {
            "batch_approved": batch_approved,
            "max_attempts": max_attempts,
            "current_attempt": round_num,
            "status": "in_progress",
            "source": source,
            "command": last_job.get("command", "")
        }

        prompt = (
            f"Fix {source} failure: {summary}\n\n"
            f"Diagnostics:\n{diagnosis.get('suggestion', '')}\n\n"
            f"Output tail:\n```\n{tail[-2500:]}\n```\n\n"
            "Please analyze the error and propose necessary code changes to fix it."
        )
        self.start_plan(prompt)

    def run_custom_cmd(self, payload: dict | None = None) -> dict:
        if not self.repo or not Path(self.repo).is_dir():
            raise PolicyError("No project folder selected.")
        payload = payload or {}
        command = str(payload.get("command") or "").strip()
        if not command:
            raise PolicyError("Command cannot be empty.")
        subdir = str(payload.get("subdir") or "").strip()
        fav_name = str(payload.get("favorite_name") or "").strip()
        
        # Record command in history/favorites (redacting secrets)
        service_runner.record_custom_command(Path(self.repo), command, subdir, fav_name)
        
        self.say(f"Running: {command}…")
        try:
            res = service_runner.run_custom_command(Path(self.repo), command, subdir, timeout=300, on_output=self._build_line)
        except PolicyError as err:
            return {
                "command": command,
                "cwd": str(self.repo),
                "exit_code": 1,
                "output": str(err),
                "duration": 0.0,
                "success": False,
                "error": str(err),
            }
        self._last_job = {"type": "custom", "subdir": subdir, **res}
        outcome = "succeeded" if res["success"] else "FAILED"
        self.say(f"Command {outcome} (exit {res['exit_code']}, {res['duration']}s)")
        self.line("tool", "Terminal", f"⚡ Custom run {outcome} — {command} (exit {res['exit_code']}, {res['duration']}s)")
        return res

    def get_custom_cmd_history(self) -> dict:
        if not self.repo or not Path(self.repo).is_dir():
            return {"history": [], "favorites": []}
        return service_runner.get_custom_command_history(Path(self.repo))

    def save_favorite_cmd(self, payload: dict | None = None) -> dict:
        if not self.repo or not Path(self.repo).is_dir():
            raise PolicyError("No project folder selected.")
        payload = payload or {}
        command = str(payload.get("command") or "").strip()
        name = str(payload.get("name") or "").strip()
        subdir = str(payload.get("subdir") or "").strip()
        return service_runner.record_custom_command(Path(self.repo), command, subdir, name)

    def diagnose_terminal(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        last_job = getattr(self, "_last_job", {}) or {}
        output = payload.get("output") or last_job.get("output", "")
        command = payload.get("command") or last_job.get("command", "")
        cwd = payload.get("cwd") or last_job.get("cwd", str(self.repo))
        exit_code = int(payload.get("exit_code") if payload.get("exit_code") is not None else last_job.get("exit_code", 1))
        is_sel = bool(payload.get("is_selection", False))
        
        diag = service_runner.analyze_terminal_output(output, command, cwd, exit_code, is_selection=is_sel)
        self._current_diagnosis = diag
        return diag

    def toggle_auto_notes(self, payload: dict | None = None) -> dict:
        if not self.repo or not Path(self.repo).is_dir():
            raise PolicyError("No project folder selected.")
        payload = payload or {}
        notes = memory_store.read_auto_notes(self.memory_dir, self.repo)
        enabled = bool(payload.get("enabled", not notes.get("enabled", True)))
        notes["enabled"] = enabled
        memory_store.write_auto_notes(self.memory_dir, self.repo, notes)
        self.say(f"Automatic project notes {'enabled' if enabled else 'disabled'}.")
        return notes

    def save_auto_notes(self, payload: dict | None = None) -> dict:
        if not self.repo or not Path(self.repo).is_dir():
            raise PolicyError("No project folder selected.")
        payload = payload or {}
        notes = payload.get("notes") or {}
        memory_store.write_auto_notes(self.memory_dir, self.repo, notes)
        self.say("Automatic project notes updated.")
        return notes

    def get_auto_notes(self) -> dict:
        if not self.repo or not Path(self.repo).is_dir():
            return memory_store.read_auto_notes(self.memory_dir, "")
        return memory_store.read_auto_notes(self.memory_dir, self.repo)

    def round_history(self) -> list:
        """Every attempt in this chat and folder, oldest first."""
        return session_flow.attempt_history(self.runs, self.session, self.chat_id)

    def fix_rounds(self) -> list:
        """The attempt rows the offer, the stop line and the report all read."""
        return session_flow.attempt_rows(self.runs, self.session, self.chat_id)

    def _show_rounds(self) -> None:
        """Put the attempts on record where they stay readable after the round counter has moved on."""
        block = repair.timeline(self.fix_rounds())
        if block:
            self.line("tool", "Tool", block)

    def stop_fix_loop(self, reason: str) -> None:
        """The one answer both windows give when the loop has no reason to spend another turn.

        The two sentences are `repair`'s, because the Tk window reaches the same decision from a
        different place and used to word it differently on the way.
        """
        self._auto_fix = False
        self.line("tool", "Tool", repair.stop_line(reason))
        self.say(repair.stop_status(reason))
        self._show_rounds()

    def _offer_fix(self, run) -> bool:
        """Ask before spending a turn. Continuing used to be a status line to interpret.

        Nothing is written here either: the round produces a proposal, and Apply stays a
        separate, deliberate click.
        """
        if self.reading_only():
            # Reached from `report_run` after a command the user agreed to run. The round this offers
            # ends in a proposal, so the offer is not made — the failure stands in the transcript on
            # its own, which is what this mode was asked for.
            self.line("tool", "Tool", intent.no_fix_round(arabic=self.arabic))
            return False
        if not self.model:
            return False
        if not self.session or self.session.get("state") not in MUTABLE_STATES:
            # A fix round repairs *a task's* proposal, so it needs one to sit on. The run itself no
            # longer does — that is D28 -- and offering to fix what cannot be recorded would send the
            # model a task with no folder to change.
            return False
        if self._batch_fixes_off:
            # Suppressed first: the operator said once that no further round is wanted in this batch,
            # and a batch of red builds is not the place to argue about why each one is hopeless too.
            self._add("tool", "Tool", fix_offers_off_line(arabic=self.arabic))
            return False
        stop, reason = repair.should_stop(self.round_history(), self._fix_round)
        if stop:
            # D42's other half: three rounds that leave the same seven failures standing are not
            # progress, and asking the user to spend another model turn to find that out again is.
            self.stop_fix_loop(reason)
            return False
        answer = self.confirm_choice(repair.FIX_OFFER_TITLE,
                                     repair.fix_offer(self.model, self._fix_round + 1, self.auto_apply,
                                                      history=self.fix_rounds()),
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
        stop, reason = repair.should_stop(self.round_history(), self._fix_round)
        if stop:
            self.stop_fix_loop(reason)
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
        # The category belongs in the line the user reads, because "asking for a fix" and "asking for
        # a fix to a dependency the machine cannot resolve" are different odds of working.
        status = repair.round_status(settings.model, self._fix_round, repair.classify(run))

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
            # The proposal exists now, so this is the first moment its *contents* can be compared
            # with the previous rounds'. A second attempt that writes the same files with the same
            # text is not a second attempt, and counting it as one is how a loop reads as progress.
            earlier = [item for item in self.round_history() if item.get("id") != path.parent.name]
            if repair.already_tried(earlier, (self.session or {}).get("changes") or []):
                self._auto_fix = False
                self.line("tool", "Tool", "🔁 " + repair.REPEATED_PROPOSAL)
                self.say(repair.REPEATED_ADVICE)
                self._show_rounds()
                return
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
        """The ledger a stored session belongs to, or None when it is not a plan step.

        Which pair this window is standing on is state, so it is recorded here; the rule that decides
        whether a ledger may be trusted at all is `session_flow.ledger_for`, shared with Tk.
        """
        found = session_flow.ledger_for(self.plans, session)
        if found is None:
            return None
        self.ledger_path, self.ledger = found
        return found

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
            self._add("tool", "Tool", shared_note("step_open_detail", arabic=self.arabic, step=step_id,
                                                     reason=friendly_error(exc)))
            self.status = shared_note("step_still_open", arabic=self.arabic, step=step_id)
            return
        self.refresh_plan_status()
        nxt = planbook.current(book)
        self.line("tool", "Tool", f"Plan step {step_id}/{len(book['steps'])} verified. " + runner.summarize(result))
        if nxt is None:
            self.say(f"Plan complete — {len(book['steps'])} steps verified.")
            return
        if self.chained and self.composer == CHANGE_COMPOSER:
            self.start_plan("")
        elif self.chained:
            self._draft = planbook.task_for(book, nxt)
            self.say(f"Step {step_id} verified. Switch the badge to Change mode to let the "
                     "next step propose its work.")
        elif self._offer_next_step(book, nxt, step_id):
            return
        else:
            self._draft = planbook.task_for(book, nxt)
            self.say(f"Step {step_id} verified. The next step is ready in the message box — press Send.")

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

    def complete_step(self, step_id: int = 0) -> None:
        """Mark a plan step verified in the ledger when sequentially executed."""
        if not self.ledger or not self.ledger_path:
            self.refresh_plan_status()
        if not self.ledger or not self.ledger_path:
            return
        if not step_id:
            curr = planbook.current(self.ledger)
            step_id = curr["id"] if curr else 0
        if not step_id:
            return
        row = planbook.step(self.ledger, step_id)
        if row is None:
            return
        row.update(status="verified", verified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        atomic_json(self.ledger_path, self.ledger)
        self.refresh_plan_status()
        self.say(f"Plan step {step_id} verified.")
        self.line("tool", "Tool", f"Plan step {step_id}: {row.get('title', '')} verified.")

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
        return session_flow.read_cached(self._session_cache, path, load_session)

    def _load_chat_cached(self, path: Path) -> dict | None:
        return session_flow.read_cached(self._chat_cache, path, load_chat)

    def _nav_projects(self) -> list[dict]:
        """The sidebar's project nodes: one per granted folder, holding its tasks and bound chats.

        Filtering is the client's own — it happens on every keystroke, and a round trip per
        keystroke would make the box lag behind the typing.
        """
        removed = getattr(self, "_removed_projects", set())
        groups: dict[str, dict[str, list]] = {key: {} for key in self.projects if key not in removed}
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
            if not root or not folder or root in removed:
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
                if root in removed:
                    continue
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
                        "initials": projects.initials_for(folder.name), "icon": self.icons.get(root, ""),
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
        self.status = (shared_note("chat_reopened_project", arabic=self.arabic,
                                     project=Path(project["path"]).name) if project
                         else shared_note("chat_reopened_plain", arabic=self.arabic))

    def _history_steps(self, turn: dict) -> list[dict]:
        """The step rows a saved task used to have on screen while it ran.

        They are rebuilt from the session's own records, which is what makes them survive a restart:
        the sentence comes back through the same `labels.step_line` the live loop used, in the language
        the task was asked in, and the row keeps the stored id so opening it still finds its detail.
        A `run` event has no step row of its own — the command was announced by the window, not the
        loop — so it becomes one here, in the past tense, matched to its stored record in order.
        """
        arabic = is_arabic(asked_of(turn.get("task", "")))
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
            self.title = asked_of(session.get("task", "Saved task")).replace("\n", " ")[:45]
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
            # Said, not printed: the records are audit rows, and a reopened list of
            # `tool name=read_file path=pom.xml sha256=…` is the engine's notebook on the screen.
            replay = is_arabic(asked_of(str(session.get("task", ""))))
            for entry in events:
                # `audit` is what the client sorts by: these are records of what the task did, and the
                # thread already carries each one as a row. The Activity list keeps the sentences about
                # the task instead, and the reopened records go into the block under it.
                self.log.append({"ts": str(entry.get("at", ""))[-8:], "kind": entry.get("kind", ""),
                                 "text": log_line(replay, entry), "audit": True})
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
                                      "text": self.approval_notice(session) +
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
        self.status = (shared_note("new_chat_plain", arabic=self.arabic)
                       if self.branch.get("kind") != BRANCH_PROJECT else
                       shared_note("new_chat_project", arabic=self.arabic, project=Path(self.repo).name))
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
        self.status = shared_note("sample_ready", arabic=self.arabic)

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
                             has_project=bool(self.repo),
                             rejected=bool(changes) and self.rejected())

    def _review(self) -> dict:
        """The change set as the card reads it: the file rows and the view are `uistate`'s, the
        permissions below are this window's — they depend on the mode, the busy flag and the answer
        the user already gave."""
        session = self.session or {}
        changes = session.get("changes", [])
        state = session.get("state")
        declined = self.rejected()
        files = uistate.review_files(changes)
        chosen = changes[min(self.review_file, len(changes) - 1)] if changes else None
        return {
            "id": str(session.get("id", "")) + ":" + str(session.get("proposal_hash", "")),
            "pending": state == "WAITING_APPROVAL",
            "state": state_label(state), "tone": TONE.get(state or "", ""),
            "title": asked_of(session.get("task", "No proposal yet"))[:120],
            "detail": (f"{len(changes)} file(s)"
                       + (" · attached plan " + session["plan_reference"]["path"] if session.get("plan_reference") else "")
                       + (" · proposal " + str(session.get("proposal_hash", ""))[:4] + "…"
                          + str(session.get("proposal_hash", ""))[-4:] if session.get("proposal_hash") else "")),
            "canApply": (state == "WAITING_APPROVAL" and not self.busy and not self.reading_only()
                         and not declined),
            "canMutate": state in MUTABLE_STATES and not self.busy,
            "canRollback": (state in MUTABLE_STATES | {"PARTIAL_APPLY", "APPLYING"})
                           and not self.busy and not self.reading_only(),
            # The answer to a question this window already asked: a declined proposal keeps its files
            # listed and its diff readable, and loses the offer to write them.
            "rejected": declined,
            "canReopen": (state == "WAITING_APPROVAL" and declined and not self.busy
                          and not self.reading_only()),
            "reopenLabel": say(self.arabic, en="Reopen for review", ar="إعادة الفتح للمراجعة"),
            "files": files, "selected": min(self.review_file, max(0, len(changes) - 1)),
            "tab": self.diff_tab,
            "view": {**uistate.file_view(chosen), "checks": runresults.checks_lines(session)},
        }

    # ------------------------------ persistence ------------------------------
    def _sync_project(self) -> None:
        self.refresh_plan_status()          # the subtitle reads the ledger, so it goes first
        self.subtitle = self._subtitle()

    def _subtitle(self) -> str:
        """The header line: what the next Send would actually do, and with what."""
        if not self.repo:
            return "Standalone chat — no folder attached, nothing to change"
        parts = [Path(self.repo).name, intent.subtitle(self.composer)]
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
              # The window's own memory of where it left each folder, Chat included — which is the one
              # position the durable file does not carry, because Chat is not a promise about files.
              # Losing this map costs a folder its Chat label and reopens it on Change; it cannot put a
              # sealed folder back on the writable path, and that half is in `modes.FILE`.
              "composer": dict(self._composer_pref),
              "auto_apply": dict(self._auto_pref),
              "endpoints": dict(self.endpoints), "profile": self.profile,
              "request_timeout": self.timeout_seconds(), "plan_chained": bool(self.chained),
              "style": self._saved_ui.get("style", "claude"), "theme": self._saved_ui.get("theme", "light"),
              # Written by "Don't show this again". The dict below is rebuilt from named keys, so a
              # preference nobody lists here is erased by the next save of anything else.
              "setup_seen": bool(self._saved_ui.get("setup_seen")),
              "sandbox_on": bool(self.sandbox_on), "sandbox_image": self.sandbox_image.strip(),
              "collapsed": bool(self._saved_ui.get("collapsed", False))}
        if self.queue:
            # Stored so a batch survives the restart that would otherwise eat it, and restored held
            # (see the load path): nothing in here starts on its own accord.
            ui["queue"] = [dict(item) for item in self.queue[:20]]
        if self.model or self._pending_model:
            ui["model"] = self.model or self._pending_model
        if hasattr(self, "_removed_projects"):
            ui["removed_projects"] = list(self._removed_projects)
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
        with self._state:
            self.key = ""

