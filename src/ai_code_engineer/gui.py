"""Native desktop workflow; background work never touches Tk widgets directly."""
from __future__ import annotations

from dataclasses import replace
import difflib
import json
import uuid
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText

from .catalog import ollama_models, openrouter_models
from .chat import create_chat, load_chat, respond, title_for
from . import config
from .config import Settings
from .engine import (MAX_TASK_CHARS, apply_proposal, atomic_json, chat_sessions, load_session, plan,
                     project_key, read_plan_reference, rollback)
from .errors import AgentError, PolicyError
from .labels import (INTERRUPTED_STATES, MUTABLE_STATES, STATES, UNVERIFIED_STATES,  # noqa: F401
                     friendly_error, is_arabic, state_label, status_text)
from .providers import make_provider
from .redaction import redact
from . import memory as memory_store
from . import planbook, repair, runner
from . import host
from .verification import verify
from .workspace import Workspace, ensure_project_dir

# Soft, coherent cool palette. Names NAVY/TEAL are kept because they are
# referenced widely; NAVY is the sidebar tint, TEAL is the accent.
BG = "#f6f8fb"
PANEL = "#ffffff"
INK = "#2b3440"
MUTED = "#8a94a3"
NAVY = "#eef2f7"
TEAL = "#5b7cfa"
TEAL_INK = "#ffffff"
LINE = "#e4e9f0"
HOVER = "#eaeff6"
ACCENT_SOFT = "#eaf0ff"
# Sidebar pseudo-project for conversations that have no folder attached.
CHAT_GROUP = "chats"
SEARCH_PLACEHOLDER = "Search tasks and chats"
BUBBLE_FONT = ("Segoe UI", 12)
# Measured on this machine with the app's own request path (see docs/MODEL-BENCHMARK.md):
# qwen2.5-coder:1.5b was the fastest model that produced valid, correct proposals.
DEFAULT_MODEL = "qwen2.5-coder:1.5b"
RECOMMENDED = {
    "qwen2.5-coder:1.5b": "recommended here — valid proposals in ~25s, good default for iterating",
    "qwen3:4b": "more careful answers, roughly 2× slower on this machine",
}


def text_set(widget: tk.Text, content: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", content)
    widget.configure(state="disabled")


def rounded_rect(canvas, x1, y1, x2, y2, radius=22, **kwargs):
    r = min(radius, (x2-x1)/2, (y2-y1)/2)
    return canvas.create_polygon(x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,
        x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1,
        smooth=True, splinesteps=24, **kwargs)


class RoundedPanel(tk.Canvas):
    def __init__(self, parent, height=160, fill=PANEL, border=LINE, **kwargs):
        super().__init__(parent, bg=kwargs.pop("bg", BG), height=height, highlightthickness=0, **kwargs)
        self.fill, self.border = fill, border
        self.inner = tk.Frame(self, bg=fill)
        self.window = self.create_window(18, 16, anchor="nw", window=self.inner)
        self.bind("<Configure>", self.redraw)

    def redraw(self, event=None):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("panel")
        rounded_rect(self, 2, 2, w-2, h-2, radius=18, fill=self.fill, outline=self.border, tags="panel")
        self.tag_lower("panel")
        self.itemconfigure(self.window, width=max(1,w-36), height=max(1,h-32))


class ViewStack(ttk.Frame):
    """Notebook without a tab strip: exactly one page is visible at a time."""

    def __init__(self, parent):
        super().__init__(parent)
        self.current = None

    def add(self, frame, text=""):
        return frame

    def select(self, frame):
        if self.current is frame and frame.winfo_manager():
            return
        if self.current is not None:
            self.current.pack_forget()
        self.current = frame
        frame.pack(fill="both", expand=True)


class Tooltip:
    """Hover tooltip; Tk has no built-in one."""

    def __init__(self, widget, text, delay=400):
        self.widget, self.text, self.delay = widget, text, delay
        self.after_id = None
        self.window = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, _=None):
        self._hide()
        self.after_id = self.widget.after(self.delay, self._show)

    def _show(self):
        if self.window is not None:
            return
        self.window = window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        x = self.widget.winfo_rootx() + max(0, self.widget.winfo_width() // 2 - 90)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        window.wm_geometry(f"+{x}+{y}")
        tk.Label(window, text=self.text, bg="#2b2d31", fg="white", padx=8, pady=4,
                 font=("Segoe UI", 9), justify="left").pack()

    def _hide(self, _=None):
        if self.after_id is not None:
            self.widget.after_cancel(self.after_id)
            self.after_id = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


class AgentWindow:
    def __init__(self, root: tk.Tk, app_dir: Path):
        self.root = root
        self.app_dir = app_dir.resolve()
        self.runs = self.app_dir / ".agent-runs"
        self.chats = self.app_dir / ".agent-chats"
        # Plan step ledgers live outside the approved project folder: a step that gates
        # the next step must not be writable by the model working on that step.
        self.plans = self.app_dir / ".agent-plans"
        # Same reason for the user's standing notes: they are instructions the model must
        # not be able to rewrite into something it will obey on the next task.
        self.memory_dir = self.app_dir / ".agent-memory"
        self.ledger_path: Path | None = None
        self.ledger: dict | None = None
        self.session_path: Path | None = None
        self.session: dict | None = None
        self.chat: dict | None = None
        self.recent_paths: list[Path] = []
        self.chat_id = uuid.uuid4().hex
        self.current_project = ""
        self._loading_session = False
        self._reverting = False
        self._pending_model = ""
        self._session_cache: dict[Path, tuple[tuple, dict | None]] = {}
        self._chat_cache: dict[Path, tuple[tuple, dict | None]] = {}
        self._rendered_width = 0
        self.project_nodes = {}
        self.chat_nodes = {}
        self.projects, self._saved_ui = {}, {}
        try:
            registry = json.loads((self.app_dir / ".agent-projects.json").read_text(encoding="utf-8"))
            self.projects = {project_key(p): str(Path(p).resolve()) for p in registry["projects"] if isinstance(p, str)}
            if isinstance(registry.get("ui"), dict):
                self._saved_ui = registry["ui"]
        except (OSError, ValueError, KeyError, TypeError):
            self.projects = {}
        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.cancellable = False
        self.closing = False
        self.done_handler = None
        self._timers: set[str] = set()
        self.job_controls: list[tuple[ttk.Widget, str]] = []
        self.repo = tk.StringVar()
        self.plan_file = tk.StringVar()
        # Chained mode turns an attached plan into an ordered ledger: the tool picks the
        # next unverified step, and a passing command run is what unlocks the one after it.
        self.chained = tk.BooleanVar(value=bool(self._saved_ui.get("plan_chained")))
        self.plan_status = tk.StringVar(value="")
        self.memory_info = tk.StringVar(value="Choose a project folder to edit its notes.")
        self.mode = tk.StringVar(value="Ollama")
        self.model = tk.StringVar()
        self.model_filter = tk.StringVar()
        self.search = tk.StringVar()
        self.model_info = tk.StringVar(value="Loading the model list…")
        self.catalogs = {"Ollama": [], "OpenRouter · Free": [], "OpenRouter · Paid": []}
        self.selections = {}
        self.active_mode = "Ollama"
        self.key = tk.StringVar()
        try:
            saved_timeout = int(self._saved_ui.get("request_timeout") or config.REQUEST_TIMEOUT_DEFAULT)
        except (TypeError, ValueError):
            saved_timeout = 300
        self.request_timeout = tk.IntVar(value=min(900, max(30, saved_timeout)))
        self.cloud_ok = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready — choose your project and describe the change")
        self.state_label = tk.StringVar(value="No task open")
        self.artifact = tk.StringVar(value="No proposal yet")
        self.artifact_detail = tk.StringVar(value="Choose a project to turn a request into reviewed file changes.")
        self.recipe = tk.StringVar()
        self.run_info = tk.StringVar(value="No command has run yet.")
        self.recipes: list[str] = []
        self._fix_round = 0
        self._auto_fix = False
        self.root.title("AI Code Engineer")
        width = min(1560, max(1100, self.root.winfo_screenwidth() - 80))
        height = min(880, max(620, self.root.winfo_screenheight() - 100))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(1000, 650)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._build()
        self.repo.trace_add("write", self.project_changed)
        self.search.trace_add("write", lambda *_: self.refresh_recent())
        saved_mode = self._saved_ui.get("mode")
        if isinstance(saved_mode, str) and saved_mode in self.catalogs:
            self.mode.set(saved_mode)
            self.active_mode = saved_mode
            # Restore needs the cloud/key UI packed for a restored cloud mode;
            # otherwise the approval prompt appears with no visible checkbox.
            self.model_changed()
        pending = self._saved_ui.get("model")
        if isinstance(pending, str) and pending:
            self._pending_model = pending
        # `last_project` is deliberately not applied here: a folder the previous session happened to
        # have open cannot become this session's workspace, in this window or the web one.
        self.refresh_recent()
        self.update_buttons()
        saved_chat = self._saved_ui.get("last_chat")
        if isinstance(saved_chat, str) and saved_chat and not self.repo.get().strip():
            resumed = self.chats / saved_chat / "chat.json"
            if resumed.exists():
                self.open_chat(resumed)
                self.status.set("Ready — ask directly, or choose a project to work on its files")
        self._schedule(80, self.poll)
        self._schedule(150, self.check_setup)

    def _style(self):
        self.root.option_add("*Font", "{Segoe UI} 10")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("Card.TLabel", background=PANEL, foreground=INK)
        style.configure("Muted.TLabel", foreground=MUTED, background=BG)
        style.configure("TButton", padding=(14, 9), background=PANEL, foreground=INK, borderwidth=0,
                        relief="flat", focusthickness=0)
        style.map("TButton", background=[("active", HOVER)], foreground=[("disabled", "#98a2b3")])
        style.configure("Accent.TButton", background=TEAL, foreground=TEAL_INK, borderwidth=0)
        style.map("Accent.TButton", background=[("disabled", "#c3cfe8"), ("active", "#4a68e0")],
                  foreground=[("disabled", "#f4f6fd")])
        style.configure("TEntry", padding=9, fieldbackground=PANEL, bordercolor=LINE, lightcolor=LINE,
                        darkcolor=LINE, relief="flat")
        style.configure("TCombobox", padding=(9, 7), fieldbackground=PANEL, background=PANEL,
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, arrowcolor=MUTED, relief="flat")
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)], selectbackground=[("readonly", PANEL)],
                  selectforeground=[("readonly", INK)])
        style.configure("TCheckbutton", background=BG, foreground=INK, padding=6)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 9), background="#eaeef4", foreground=MUTED, borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", TEAL)])
        style.configure("Treeview", rowheight=32, background=PANEL, fieldbackground=PANEL, foreground=INK,
                        borderwidth=0)
        style.map("Treeview", background=[("selected", ACCENT_SOFT)], fieldbackground=[("selected", ACCENT_SOFT)],
                  foreground=[("selected", INK)])
        style.configure("Treeview.Heading", background="#eef2f8", foreground=MUTED, padding=8, borderwidth=0)
        style.configure("Projects.Treeview", background=NAVY, fieldbackground=NAVY, borderwidth=0,
                        relief="flat", rowheight=30)
        style.map("Projects.Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", INK)])
        style.configure("Horizontal.TProgressbar", thickness=3, background=TEAL, troughcolor="#e4e9f0",
                        borderwidth=0, lightcolor=TEAL, darkcolor=TEAL)
        for bar in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(bar, gripcount=0, arrowsize=0, borderwidth=0, relief="flat",
                            background="#d5dbe4", troughcolor=BG, activebackground="#c0c8d4")

    def button(self, parent, text, command, *, accent=False, track=True, tip=None):
        try:
            parent_bg = parent.cget("background")
        except tk.TclError:
            parent_bg = PANEL
        base = TEAL if accent else parent_bg
        hover = "#4a68e0" if accent else HOVER
        button = tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                           highlightthickness=0, bg=base, fg=TEAL_INK if accent else INK,
                           activebackground=hover, activeforeground=TEAL_INK if accent else INK,
                           disabledforeground="#f2f4fa" if accent else "#a8b0bb",
                           padx=14, pady=9, cursor="hand2", font=("Segoe UI", 11))

        def tint(color):
            # Disabled buttons must not light up on hover.
            if str(button["state"]) != "disabled":
                button.configure(bg=color)

        button.bind("<Enter>", lambda _=None: tint(hover))
        button.bind("<Leave>", lambda _=None: tint(getattr(button, "base_bg", base)))
        if tip:
            Tooltip(button, tip)
        if track:
            self.job_controls.append((button, "normal"))
        return button

    def _build(self):
        sidebar = tk.Frame(self.root, bg=NAVY, width=268)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="AI Code Engineer", bg=NAVY, fg=INK,
                 font=("Segoe UI Semibold", 15)).pack(anchor="w", padx=20, pady=(26, 20))
        self.button(sidebar, "＋   New chat", self.new_chat, accent=True).pack(anchor="w", padx=14, pady=(0, 4))
        self.button(sidebar, "↗   Open saved task", self.open_file).pack(anchor="w", padx=14, pady=3)
        self.search_box = ttk.Entry(sidebar, textvariable=self.search)
        self.search_box.pack(fill="x", padx=14, pady=(8, 0))
        self.search_box.insert(0, "Search tasks and chats")
        self.search_box.configure(foreground=MUTED)
        self.search_box.bind("<FocusIn>", self._search_focus)
        self.search_box.bind("<FocusOut>", self._search_focus)
        tk.Frame(sidebar, bg=LINE, height=1).pack(fill="x", pady=16)
        tk.Label(sidebar, text="Workspace", bg=NAVY, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(4, 8))
        self.project_name = tk.StringVar(value="No project — just chat")
        project_button = self.button(sidebar, "", self.browse, tip="Choose a project folder to turn requests into reviewed code changes")
        project_button.configure(textvariable=self.project_name, anchor="w", wraplength=214, justify="left")
        project_button.pack(fill="x", padx=14)
        self.clear_button = self.button(sidebar, "Clear project", lambda: self.repo.set(""), track=False)
        self.clear_button.configure(anchor="w", font=("Segoe UI", 10))
        self.clear_button.pack(fill="x", padx=14, pady=(4, 0))
        new_project_button = self.button(sidebar, "New project folder…", self.new_project, track=False,
                                         tip="Grant access to an empty folder and build a new project there")
        new_project_button.configure(anchor="w", font=("Segoe UI", 10))
        new_project_button.pack(fill="x", padx=14, pady=(2, 0))
        tk.Label(sidebar, text="Tasks", bg=NAVY, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(24, 8))
        # Packed from the bottom up so the expanding tree can never push them off screen.
        self.button(sidebar, "Try sample project", self.example).pack(side="bottom", anchor="w", padx=14, pady=10)
        tk.Frame(sidebar, bg=LINE, height=1).pack(side="bottom", fill="x")
        tk.Label(sidebar, text="●  You always review first", bg=NAVY, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="bottom", anchor="w", padx=20, pady=(8, 16))
        self.recent = ttk.Treeview(sidebar, show="tree", selectmode="browse", style="Projects.Treeview")
        self.recent.column("#0", width=222, minwidth=100)
        self.recent.pack(side="top", fill="both", expand=True, padx=18)
        self.recent.bind("<<TreeviewSelect>>", self.open_recent)
        main = ttk.Frame(self.root)
        main.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(main, padding=(26, 14))
        header.pack(fill="x")
        self.title = tk.StringVar(value="New chat")
        ttk.Label(header, textvariable=self.title, font=("Segoe UI Semibold", 13)).pack(side="left")
        self.view_buttons = {}
        for label, key, first in (("Activity", "details", True), ("Changes", "review", False), ("Chat", "task", False)):
            view_button = self.button(header, label, lambda key=key: self.select_view(key), track=False)
            view_button.pack(side="right", padx=(0, 6) if not first else 0)
            self.view_buttons[key] = view_button
        tk.Frame(main, bg=LINE, height=1).pack(fill="x")
        body = ttk.Frame(main)
        body.pack(fill="both", expand=True)
        sources = ttk.Frame(body, width=292, padding=(10, 18, 16, 0))
        sources.pack(side="right", fill="y")
        sources.pack_propagate(False)
        self.sources_panel = sources
        def fit_sources(event):
            if event.width < 1000:
                sources.pack_forget()
            elif not sources.winfo_manager():
                sources.pack(side="right", fill="y", before=center)
        body.bind("<Configure>", fit_sources)
        card = RoundedPanel(sources, height=540)
        card.pack(fill="x")
        ttk.Label(card.inner, text="Artifact", foreground=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 6))
        ttk.Label(card.inner, textvariable=self.artifact, font=("Segoe UI Semibold", 11),
                  wraplength=212, justify="left").pack(anchor="w")
        ttk.Label(card.inner, textvariable=self.artifact_detail, foreground=MUTED, font=("Segoe UI", 9),
                  wraplength=212, justify="left").pack(anchor="w", pady=(2, 8))
        self.button(card.inner, "Review proposed files  ↗", lambda: self.select_view("review"), track=False).pack(anchor="w")
        tk.Frame(card.inner, bg=LINE, height=1).pack(fill="x", pady=14)
        # Checks: run the project's own build/test command, then optionally ask for a fix.
        self.checks_frame = ttk.Frame(card.inner)
        ttk.Label(self.checks_frame, text="Checks", foreground=MUTED, font=("Segoe UI", 10)).pack(anchor="w")
        self.recipe_box = ttk.Combobox(self.checks_frame, textvariable=self.recipe, values=[],
                                       state="readonly", width=24)
        self.recipe_box.pack(anchor="w", pady=(3, 6))
        Tooltip(self.recipe_box, "Command detected in this project folder")
        run_row = ttk.Frame(self.checks_frame)
        run_row.pack(fill="x")
        self.run_button = self.button(run_row, "▶  Run", lambda: self.run_tests(False), track=False,
                                      tip="Run the selected command inside this project folder")
        self.run_button.pack(side="left")
        self.fix_button = self.button(run_row, "Run & fix", lambda: self.run_tests(True), track=False,
                                      tip="Run it, and when it fails ask the model for the next fix. You still approve every write.")
        self.fix_button.pack(side="left", padx=8)
        ttk.Label(self.checks_frame, textvariable=self.run_info, foreground=MUTED, font=("Segoe UI", 9),
                  wraplength=212, justify="left").pack(anchor="w", pady=(6, 0))
        self.job_controls.append((self.recipe_box, "readonly"))
        # Packed here so it sits between the artifact summary and Sources; hidden
        # again by refresh_recipes() while no project folder is selected.
        self.checks_frame.pack(anchor="w", pady=(0, 12))
        self.sources_label = ttk.Label(card.inner, text="Sources", foreground=MUTED, font=("Segoe UI", 10))
        self.sources_label.pack(anchor="w")
        self.source_name = tk.StringVar(value="Attach a project plan")
        source = self.button(card.inner, "", self.source_action, tip="Attach or view the project plan")
        source.configure(textvariable=self.source_name, anchor="w", wraplength=214, justify="left")
        source.pack(fill="x", pady=8)
        self.button(card.inner, "＋  Attach plan", self.browse_plan).pack(anchor="w")
        self.button(card.inner, "Settings  ›", self.show_settings,
                    tip="API key, model filter and details").pack(anchor="w", pady=(6, 0))
        center = ttk.Frame(body, padding=(24, 0, 24, 10))
        center.pack(side="left", fill="both", expand=True)
        self.tabs = ViewStack(center)
        self.tabs.pack(fill="both", expand=True)
        self.task_tab = ttk.Frame(self.tabs, padding=(14, 12, 14, 0))
        self.review_tab = ttk.Frame(self.tabs, padding=14)
        self.details_tab = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(self.task_tab, text="Conversation")
        self.tabs.add(self.review_tab, text="Changes")
        self.tabs.add(self.details_tab, text="Activity")
        self.select_view("task")
        self._task_page()
        self._review_page()
        log_card = RoundedPanel(self.details_tab)
        log_card.pack(fill="both", expand=True)
        self.log = ScrolledText(log_card.inner, wrap="word", font=("Consolas", 10), bg=PANEL, fg=INK,
                                relief="flat", padx=14, pady=14, state="disabled")
        self.log.pack(fill="both", expand=True)
        self.progress = ttk.Progressbar(center, mode="indeterminate")
        self.status_label = ttk.Label(center, textvariable=self.status, style="Muted.TLabel", anchor="center",
                                      font=("Segoe UI", 10))
        self.status_label.pack(fill="x", pady=(6, 0))
        self.status_label.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(200,e.width)))
        self.repo.trace_add("write", self._sync_project_chip)
        self.plan_file.trace_add("write", lambda *_: self.refresh_plan_status())
        self._sync_project_chip()
        self.reset_conversation()

    def _sync_project_chip(self, *_):
        repo = self.repo.get().strip()
        self.project_name.set("▱  " + Path(repo).name if repo else "No project — just chat")
        self.clear_button.configure(state="normal" if repo else "disabled")

    def select_view(self, key: str):
        page = getattr(self, key + "_tab")
        self.tabs.select(page)
        for name, widget in self.view_buttons.items():
            active = name == key
            widget.base_bg = ACCENT_SOFT if active else PANEL
            widget.configure(bg=widget.base_bg, fg=TEAL if active else INK)
        return page

    def source_action(self):
        self.view_plan() if self.plan_file.get() else self.browse_plan()

    def show_settings(self):
        self.settings_window.deiconify()
        self.settings_window.lift()

    def clear_conversation(self):
        self.messages = []
        self.render_messages()

    def reset_conversation(self):
        self.clear_conversation()
        self.chat_message("AI Code Engineer", "What would you like to work on?\n\nAsk me anything as it is, or choose a project in the sidebar and I will propose reviewed changes to its files.")

    @property
    def arabic(self) -> bool:
        """Was the thing in front of this window asked in Arabic?

        The same rule the web window applies: the session's own task first, then the last message
        the user typed, then English. The Tk window used to have no answer here at all — it read
        `STATES` directly and wrote its own English sentences, which is the whole reason the fallback
        window stayed English under an Arabic task.
        """
        task = (self.session or {}).get("task") or ""
        if not task:
            for author, text in reversed(getattr(self, "messages", [])):
                if author == "You":
                    task = text
                    break
        return is_arabic(task)

    def chat_message(self, author, message):
        self.messages.append((author,message))
        self.render_messages()
        self._idle(lambda: self.conversation.yview_moveto(1))

    def render_messages(self, event=None):
        if getattr(self, "_rendering_messages", False):
            return
        if event is not None and getattr(self, "_rendered_width", 0) == max(280, self.conversation.winfo_width()):
            return
        self._rendering_messages = True
        try:
            self._render_messages()
        finally:
            self._rendering_messages = False

    def _wrap_metrics(self, text: str, pixel_width: int, metrics) -> tuple[int, int]:
        """Greedy word wrap with real font metrics: Tk only bundles display lines
        once a widget is mapped, which is too late to lay the canvas out.
        Returns the line count and the widest rendered line."""
        space = metrics.measure(" ")
        lines, widest = 0, 0
        for paragraph in text.split("\n"):
            count, current = 1, 0
            for word in paragraph.split(" "):
                width = metrics.measure(word)
                if not current:
                    current = width
                elif current + space + width <= pixel_width:
                    current += space + width
                else:
                    count += 1
                    current = width
                while current > pixel_width:
                    count += 1
                    current -= pixel_width
                widest = max(widest, min(current, pixel_width))
            lines += count
        return lines, widest

    def _render_messages(self):
        c = self.conversation
        for child in c.winfo_children():
            child.destroy()
        c.delete("all")
        width = max(280,c.winfo_width())
        pad = 18
        y = 36
        metrics = getattr(self, "_bubble_metrics", None)
        if metrics is None:
            metrics = self._bubble_metrics = tkfont.Font(font=BUBBLE_FONT)
        char_px = max(4, metrics.measure("0"))
        line_px = metrics.metrics("linespace")
        for author,message in getattr(self,"messages",[]):
            user = author == "You"
            limit = max(120, min(620 if user else 760, width-110) - 2*pad)
            lines, used = self._wrap_metrics(message, limit, metrics)
            inner = max(min(60, limit), min(used + 2, limit))
            text_width = inner + 2*pad
            left = width-text_width-44 if user else 26
            if user:
                top = y
            else:
                c.create_text(left+pad,y+2,text=author,anchor="nw",fill=MUTED,font=("Segoe UI Semibold",10))
                top = y+26
            view = tk.Text(c, wrap="word", font=BUBBLE_FONT, bg=ACCENT_SOFT if user else PANEL,
                           fg=INK, relief="flat", bd=0, highlightthickness=0, padx=0, pady=0,
                           width=max(10, inner//char_px), height=1, cursor="xterm",
                           exportselection=False, spacing1=1, spacing3=1,
                           selectbackground="#bfd3f7", selectforeground=INK)
            view.insert("1.0", message)
            # `justify` is a Text *tag* option here, not a widget option — measured: the widget
            # rejects -justify. Right-aligning the line is as far as Tk 8.6 goes without a bidi
            # engine, and it is what makes an Arabic paragraph readable in the fallback window.
            if is_arabic(message):
                view.tag_configure("rtl", justify="right")
                view.tag_add("rtl", "1.0", "end")
            view.configure(state="disabled")
            self.text_menu(view, readonly=True)
            height = lines * (line_px + 2) + 4
            c.create_window(left+pad, top+pad, window=view, width=inner, height=height, anchor="nw")
            bottom = top + pad + height + pad
            bubble = rounded_rect(c, left, top-8, left+text_width, bottom, radius=16,
                                  fill=ACCENT_SOFT if user else PANEL, outline="" if user else LINE)
            c.tag_lower(bubble)
            y = bottom+30
        c.configure(scrollregion=(0,0,width,max(y,c.winfo_height())))
        self._rendered_width = width

    def text_menu(self, widget, *, readonly=False):
        def copy():
            try:
                value = widget.get("sel.first", "sel.last")
            except tk.TclError:
                return "break"
            widget.clipboard_clear()
            widget.clipboard_append(value)
            return "break"

        def select_all():
            widget.tag_add("sel", "1.0", "end-1c")
            return "break"

        menu = tk.Menu(widget, tearoff=False)
        menu.add_command(label="Copy", command=copy, accelerator="Ctrl+C")
        if not readonly:
            menu.add_command(label="Cut", command=lambda: widget.event_generate("<<Cut>>"), accelerator="Ctrl+X")
            menu.add_command(label="Paste", command=lambda: widget.event_generate("<<Paste>>"), accelerator="Ctrl+V")
        menu.add_separator()
        menu.add_command(label="Select all", command=select_all, accelerator="Ctrl+A")

        def popup(event):
            widget.focus_set()
            menu.entryconfigure("Copy", state="normal" if widget.tag_ranges("sel") else "disabled")
            if not readonly:
                enabled = str(widget.cget("state")) != "disabled"
                menu.entryconfigure("Cut", state="normal" if enabled and widget.tag_ranges("sel") else "disabled")
                menu.entryconfigure("Paste", state="normal" if enabled else "disabled")
            try:
                menu.tk_popup(event.x_root,event.y_root)
            finally:
                menu.grab_release()
            return "break"

        widget.bind("<Control-c>", lambda _: copy())
        widget.bind("<Control-a>", lambda _: select_all())
        widget.bind("<Button-3>", popup)
        if readonly:
            widget.bind("<Button-1>", lambda _: widget.focus_set(), add=True)
            widget.bind("<MouseWheel>", lambda e: (self.conversation.yview_scroll(-int(e.delta/120),"units"), "break")[1])

    def _task_page(self):
        composer = RoundedPanel(self.task_tab, height=196)
        composer.pack(fill="x",side="bottom",pady=(14,6))
        self.task = tk.Text(composer.inner, height=2, wrap="word", font=("Segoe UI",12),
                            relief="flat", bd=0, bg=PANEL,fg=INK,insertbackground=TEAL,undo=True,
                            padx=2, pady=4, spacing1=2, spacing3=2)
        self.task.pack(fill="both",expand=True,padx=2,pady=(4,10))
        self.text_menu(self.task)
        self.task.bind("<Control-Return>", lambda _: (self.start_plan(), "break")[1])
        def enter_sends(event):
            if event.state & 0x0001:  # Shift+Enter keeps the newline.
                return None
            self.start_plan()
            return "break"
        self.task.bind("<Return>", enter_sends)
        self.prompt_hint = tk.Label(self.task,text="Ask anything — or choose a project and describe the change",
                                    bg=PANEL,fg="#a6aeb9",font=("Segoe UI",12),cursor="xterm")
        self.prompt_hint.place(x=6,y=6)
        self.prompt_hint.bind("<Button-1>",lambda _: self.task.focus_set())
        def prompt_hint(_=None):
            self.prompt_hint.place_forget()
            if not self.task.get("1.0","end").strip() and self.root.focus_get() != self.task:
                self.prompt_hint.place(x=6,y=6)
        self.task.bind("<FocusIn>",prompt_hint)
        self.task.bind("<FocusOut>",prompt_hint)
        self.task.bind("<<Modified>>",lambda _: (prompt_hint(), self.task.edit_modified(False)) if self.task.edit_modified() else None)
        tk.Frame(composer.inner, bg=LINE, height=1).pack(fill="x", pady=(0, 8))
        actions = ttk.Frame(composer.inner)
        actions.pack(fill="x")
        self.button(actions, "+  Plan", self.browse_plan,
                    tip="Attach a project plan (.md or .txt)").pack(side="left")
        self.mode_box = ttk.Combobox(actions, textvariable=self.mode, values=list(self.catalogs),
                                     state="readonly", width=18)
        self.mode_box.pack(side="left", padx=(10, 0))
        self.mode_box.bind("<<ComboboxSelected>>", self.mode_changed)
        Tooltip(self.mode_box, "Provider — local Ollama or OpenRouter cloud")
        self.model_box = ttk.Combobox(actions, textvariable=self.model, values=[], state="readonly", width=30)
        self.model_box.pack(side="left", padx=6)
        self.model_box.bind("<<ComboboxSelected>>", self.model_changed)
        Tooltip(self.model_box, "Model — open Settings for the full list and filter")
        self.job_controls.extend([(self.mode_box, "readonly"), (self.model_box, "readonly")])
        self.button(actions, "Settings", self.show_settings, track=False,
                    tip="API key, model filter and details").pack(side="left")
        self.start_button = self.button(actions, "Send  ↑", self.start_plan, accent=True, tip="Send (Enter)")
        self.start_button.configure(font=("Segoe UI Semibold", 11), padx=18, pady=6)
        self.start_button.pack(side="right")
        self.stop_button = self.button(actions, "Stop", self.stop, track=False,
                                       tip="Stop after the current model request finishes")
        self.stop_button.pack(side="right", padx=6)
        chat = ttk.Frame(self.task_tab)
        chat.pack(fill="both",expand=True)
        self.conversation=tk.Canvas(chat,bg=BG,highlightthickness=0)
        scroll=ttk.Scrollbar(chat,orient="vertical",command=self.conversation.yview)
        self.conversation.configure(yscrollcommand=scroll.set)
        self.conversation.pack(side="left",fill="both",expand=True)
        scroll.pack(side="right",fill="y")
        self.conversation.bind("<Configure>",self.render_messages)
        self.conversation.bind("<MouseWheel>",lambda e: self.conversation.yview_scroll(-int(e.delta/120),"units"))
        self.settings_window=tk.Toplevel(self.root)
        self.settings_window.title("Project & model settings")
        self.settings_window.geometry("650x740")
        self.settings_window.minsize(580,600)
        self.settings_window.configure(bg="white")
        self.settings_window.transient(self.root)
        self.settings_window.protocol("WM_DELETE_WINDOW",self.settings_window.withdraw)
        self.settings_window.withdraw()
        self.button(self.settings_window,"Done",self.settings_window.withdraw,track=False).pack(side="bottom",anchor="e",padx=20,pady=10)
        self.inspector=ttk.Frame(self.settings_window,padding=20)
        self.inspector.pack(fill="both",expand=True)
        canvas = tk.Canvas(self.inspector, bg=BG, highlightthickness=0, width=240)
        scrollbar = ttk.Scrollbar(self.inspector, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        parent = ttk.Frame(canvas, padding=(0, 0, 6, 8))
        window = canvas.create_window((0, 0), window=parent, anchor="nw")
        parent.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        def scroll_sources(event):
            widget = event.widget
            while widget is not None:
                if widget == self.inspector:
                    canvas.yview_scroll(-int(event.delta / 120), "units")
                    return "break"
                widget = getattr(widget, "master", None)
        self.root.bind("<MouseWheel>", scroll_sources, add=True)
        ttk.Label(parent, text="Project & sources", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 18))
        ttk.Label(parent, text="Outputs", foreground=MUTED).pack(anchor="w", pady=(0, 6))
        self.button(parent, "Review proposed files", lambda: self.select_view("review")).pack(fill="x")
        ttk.Separator(parent).pack(fill="x", pady=18)
        ttk.Label(parent, text="Project folder", anchor="w").pack(fill="x", pady=(0, 6))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=self.repo)
        entry.pack(fill="x", pady=(0, 6))
        entry.configure(state="readonly")
        self.job_controls.append((entry, "readonly"))
        self.button(row, "Browse…", self.browse).pack(anchor="w")
        ttk.Label(parent, text="Attached plan", anchor="w").pack(fill="x", pady=(14, 6))
        plan_row = ttk.Frame(parent)
        plan_row.pack(fill="x")
        plan_entry = ttk.Entry(plan_row, textvariable=self.plan_file)
        plan_entry.pack(fill="x", pady=(0, 6))
        self.job_controls.append((plan_entry, "normal"))
        self.button(plan_row, "Attach…", self.browse_plan).pack(side="left", padx=(0, 5))
        self.button(plan_row, "View", self.view_plan).pack(side="left", padx=(0, 5))
        self.button(plan_row, "Clear", self.clear_plan).pack(side="left")
        chained_box = ttk.Checkbutton(parent, variable=self.chained,
                                      text="Step-by-step plan mode: work one step at a time")
        chained_box.pack(anchor="w", pady=(6, 0))
        self.job_controls.append((chained_box, "normal"))
        Tooltip(chained_box, "The task is taken from the plan's next unfinished step, and the step is only "
                             "marked done when a command run proves its tests passed. Proposals still need "
                             "your approval. The message box then holds an optional note for that step.")
        self.chained.trace_add("write", lambda *_: (self.refresh_plan_status(), self._save_state()))
        ttk.Label(parent, textvariable=self.plan_status, style="Muted.TLabel", wraplength=500,
                  justify="left").pack(fill="x", pady=(4, 0))
        ttk.Separator(parent).pack(fill="x", pady=18)
        ttk.Label(parent, text="Project notes (memory)", anchor="w").pack(fill="x", pady=(0, 6))
        self.memory_box = ScrolledText(parent, height=7, wrap="word", relief="flat",
                                       font=("Segoe UI", 10), padx=8, pady=6, bg="white", fg=INK)
        self.memory_box.pack(fill="x")
        self.memory_box.bind("<KeyRelease>", lambda *_: self.refresh_memory_info())
        self.job_controls.append((self.memory_box, "normal"))
        Tooltip(self.memory_box, "Standing decisions for this folder — package names, language level, "
                                 "naming rules, what not to touch. Sent with every task here, and stored "
                                 "outside the project so a proposal can never rewrite the notes it is held to.")
        memory_row = ttk.Frame(parent)
        memory_row.pack(fill="x", pady=(6, 0))
        self.button(memory_row, "Save notes", self.save_memory).pack(side="left")
        ttk.Label(memory_row, textvariable=self.memory_info, style="Muted.TLabel", wraplength=330,
                  justify="left").pack(side="left", padx=(12, 0))
        ttk.Label(parent, text="Provider and model are selected next to the message box.",
                  style="Muted.TLabel", wraplength=500, justify="left").pack(fill="x", pady=(20, 6))
        ttk.Label(parent, text="Filter models by name", anchor="w").pack(fill="x", pady=(5, 3))
        model_filter = ttk.Entry(parent, textvariable=self.model_filter)
        model_filter.pack(fill="x")
        self.job_controls.append((model_filter, "normal"))
        self.model_filter.trace_add("write", lambda *_: self.filter_models())
        ttk.Label(parent, textvariable=self.model_info, style="Muted.TLabel", wraplength=500).pack(fill="x", pady=(6, 4))
        timeout_row = ttk.Frame(parent)
        timeout_row.pack(fill="x", pady=(4, 0))
        ttk.Label(timeout_row, text="Request timeout (seconds)", anchor="w").pack(side="left")
        timeout_box = ttk.Spinbox(timeout_row, from_=config.REQUEST_TIMEOUT_LOW,
                                  to=config.REQUEST_TIMEOUT_HIGH, increment=30, width=6,
                                  textvariable=self.request_timeout)
        timeout_box.pack(side="right")
        Tooltip(timeout_box, "Small local models need minutes for a large proposal; raise this if requests time out.")
        self.job_controls.append((timeout_box, "normal"))
        self.request_timeout.trace_add("write", lambda *_: self._save_state())
        self.cloud_frame = ttk.Frame(parent)
        self.key_frame = ttk.Frame(self.cloud_frame)
        self.key_frame.pack(fill="x")
        ttk.Label(self.key_frame, text="OpenRouter API key (not saved)", anchor="w").pack(fill="x", pady=(8, 3))
        key_entry = ttk.Entry(self.key_frame, textvariable=self.key, show="•")
        key_entry.pack(fill="x")
        check = ttk.Checkbutton(self.cloud_frame, variable=self.cloud_ok,
                                text="Allow this public / synthetic code\nto be sent to the cloud service.")
        check.pack(anchor="w", pady=4)
        self.cloud_consent = check
        self.job_controls.extend([(key_entry, "normal"), (check, "normal")])
        self.footer_actions = ttk.Frame(parent)
        self.footer_actions.pack(fill="x", pady=(15, 0))
        self.button(self.footer_actions, "Refresh models", self.check_setup).pack(side="left")
        self.hint = ttk.Label(parent, text="You will review a proposal first. Project files are not changed automatically.", style="Muted.TLabel", anchor="w", wraplength=500, justify="left")
        self.hint.pack(fill="x", pady=(12, 0))

    def _review_page(self):
        parent = self.review_tab
        self.headline = ttk.Label(parent, textvariable=self.state_label,
                                  font=("Segoe UI", 13, "bold"), anchor="w")
        self.headline.pack(fill="x", pady=(0, 8))
        # Tk 8.6 has no bidi engine — measured on this machine: a label takes `justify` and `anchor`
        # but no `-direction` — so alignment is the honest ceiling for the fallback window. The
        # headline follows the language of the sentence that is currently in it.
        self.state_label.trace_add("write", lambda *_: self.headline.configure(
            anchor="e" if is_arabic(self.state_label.get()) else "w"))
        self.summary = ScrolledText(parent, height=3, wrap="word", relief="flat", font=("Segoe UI", 10), padx=8, pady=6, state="disabled")
        self.summary.pack(fill="x", pady=(0, 8))
        self.files = ttk.Treeview(parent, columns=("kind",), show="tree headings", height=3, selectmode="browse")
        self.files.heading("#0", text="File")
        self.files.heading("kind", text="Change")
        self.files.column("#0", width=300, stretch=True)
        self.files.column("kind", width=110, stretch=False, anchor="center")
        self.files.pack(fill="x", pady=(0, 8))
        self.files.bind("<<TreeviewSelect>>", self.show_change)
        code_tabs = ttk.Notebook(parent)
        code_tabs.pack(fill="both", expand=True)
        self.code_views = {}
        for key, title in (("diff", "Diff"), ("before", "Before"), ("after", "After"), ("checks", "Checks")):
            frame = ttk.Frame(code_tabs)
            code_tabs.add(frame, text=title)
            view = ScrolledText(frame, wrap="none", height=8, font=("Consolas", 10), relief="flat", padx=8, pady=8,
                                bg="white", fg=INK, state="disabled")
            horizontal = ttk.Scrollbar(frame, orient="horizontal", command=view.xview)
            horizontal.pack(side="bottom", fill="x")
            view.configure(xscrollcommand=horizontal.set)
            view.pack(fill="both", expand=True)
            self.code_views[key] = view
        diff = self.code_views["diff"]
        diff.tag_configure("add", foreground="#11613c", background="#e3f4ea")
        diff.tag_configure("remove", foreground="#9f2424", background="#ffebeb")
        diff.tag_configure("header", foreground="#1c5596", background="#edf3fc")
        row = ttk.Frame(parent)
        row.pack(fill="x")
        self.apply_button = self.button(row, "Apply changes", self.apply, accent=True, track=False)
        self.apply_button.pack(side="right")
        self.verify_button = self.button(row, "Check syntax", self.check_changes, track=False)
        self.verify_button.pack(side="right", padx=8)
        self.rollback_button = self.button(row, "Roll back changes", self.undo, track=False)
        self.rollback_button.pack(side="left")

    def append_log(self, value: str):
        self.log.configure(state="normal")
        self.log.insert("end", value + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def update_buttons(self):
        for widget, enabled in self.job_controls:
            widget.configure(state="disabled" if self.busy else enabled)
        self.task.configure(state="disabled" if self.busy else "normal")
        state = self.session.get("state") if self.session else None
        self.apply_button.configure(state="normal" if not self.busy and state == "WAITING_APPROVAL" else "disabled")
        self.verify_button.configure(state="normal" if not self.busy and state in MUTABLE_STATES else "disabled")
        self.rollback_button.configure(state="normal" if not self.busy and state in MUTABLE_STATES | {"PARTIAL_APPLY", "APPLYING"} else "disabled")
        runnable = not self.busy and state in MUTABLE_STATES and bool(self.recipes)
        self.run_button.configure(state="normal" if runnable else "disabled")
        self.fix_button.configure(state="normal" if runnable else "disabled")
        self.stop_button.configure(state="normal" if self.busy and self.cancellable else "disabled")
        if self.busy and self.cancellable:
            self.stop_button.pack(side="right", before=self.start_button, padx=3)
        else:
            self.stop_button.pack_forget()
        self.recent.state(["disabled"] if self.busy else ["!disabled"])

    def run_job(self, operation, on_done, status: str, *, cancellable=False):
        if self.busy:
            return
        self.busy, self.cancellable = True, cancellable
        self.cancel_event.clear()
        self.done_handler = on_done
        self.status.set(status)
        self.append_log(status)
        self.progress.pack(fill="x", before=self.status_label, pady=(6, 0))
        self.progress.start(12)
        self.update_buttons()

        def worker():
            try:
                self.events.put(("done", operation()))
            except Exception as exc:
                self.events.put(("error", friendly_error(exc)))

        threading.Thread(target=worker, name="agent-desktop-worker", daemon=True).start()

    def _schedule(self, delay: int, func) -> None:
        """Every idle callback goes through here, so a pending one can be cancelled.

        A timer that fires after ``root.destroy()`` makes Tk print
        ``invalid command name "…poll"`` on stderr — which is exactly what filled the test log.
        """
        def fire():
            self._timers.discard(handle)
            func()
        handle = self.root.after(delay, fire)
        self._timers.add(handle)

    def _idle(self, func) -> None:
        """An after_idle request that a closing window can still take back."""
        def run():
            self._timers.discard(handle)
            func()
        handle = self.root.after_idle(run)
        self._timers.add(handle)

    def cancel_timers(self) -> int:
        """Drop every scheduled callback; returns how many were still pending."""
        pending, self._timers = set(self._timers), set()
        for handle in pending:
            try:
                self.root.after_cancel(handle)
            except (tk.TclError, ValueError):
                pass
        return len(pending)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    # Every runner line arrives here, so this is the one place that has to scrub
                    # them: a build that prints its JDBC URL must not show the password whole.
                    value = redact(value).strip()[:500]
                    self.append_log(value)
                    self.status.set(value)
                    continue
                self.busy = self.cancellable = False
                self.progress.stop()
                self.progress.pack_forget()
                callback = self.done_handler
                self.done_handler = None
                if kind == "done":
                    try:
                        callback(value)
                    except (AgentError, OSError, ValueError) as exc:
                        self.status.set(friendly_error(exc))
                else:
                    self.status.set(value)
                    self.append_log(value)
                    self.chat_message("AI Code Engineer", value)
                    if not self.catalogs.get(self.mode.get()):
                        self.model_info.set("No model list loaded. Click Refresh models to try again.")
                    # A partially applied mutation must update available recovery actions.
                    if self.session_path:
                        try:
                            self.display_session(self.session_path, select_tab=False)
                        except (AgentError, OSError):
                            pass
                self.refresh_recent()
                self.update_buttons()
                if self.closing:
                    self.key.set("")
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self._schedule(80, self.poll)

    def browse(self):
        path = filedialog.askdirectory(title="Select project folder", parent=self.root)
        if path:
            self.repo.set(path)

    def _save_state(self):
        ui = {"mode": self.mode.get(), "last_project": self.repo.get().strip(),
              "last_chat": self.chat_id, "request_timeout": self.request_timeout_seconds(),
              "plan_chained": bool(self.chained.get())}
        model = self.model.get() or self._pending_model
        if model:
            ui["model"] = model
        try:
            atomic_json(self.app_dir / ".agent-projects.json",
                        {"projects": list(self.projects.values()), "ui": ui})
        except OSError:
            self.status.set(status_text("registry_unsaved", arabic=self.arabic))

    def _has_draft(self):
        # Sent turns are already saved, so only an unsent message is worth confirming.
        return bool(self.task.get("1.0", "end").strip())

    def project_changed(self, *_):
        if self._reverting:
            return
        raw = self.repo.get().strip()
        identity = project_key(raw) if raw else ""
        if identity == self.current_project:
            return
        if not self._loading_session and self._has_draft():
            previous = self.projects.get(self.current_project, "") if self.current_project else ""
            if not messagebox.askyesno("Switch project",
                                       "Switching projects starts a new chat and clears your draft. Continue?",
                                       parent=self.root):
                self._reverting = True
                try:
                    self.repo.set(previous)
                finally:
                    self._reverting = False
                return
        self.current_project = identity
        self.cloud_ok.set(False)
        self.load_memory()
        if raw:
            self.projects[identity] = str(Path(raw).resolve())
        self._save_state()
        if not self._loading_session:
            self.new_task()
        self.refresh_recent()

    def browse_plan(self):
        path = filedialog.askopenfilename(title="Attach a project plan", initialdir=self.repo.get() or str(self.app_dir),
                                          filetypes=[("Plan files", "*.md *.txt")], parent=self.root)
        if not path:
            return
        if not self.repo.get().strip():
            self.repo.set(str(Path(path).parent))
        try:
            reference = read_plan_reference(Workspace(Path(self.repo.get().strip())), path, Settings())
        except (AgentError, OSError) as exc:
            self.status.set(friendly_error(exc))
            return
        self.plan_file.set(str(Path(self.repo.get()) / reference["path"]))
        if not self.chained.get() and not self.task.get("1.0", "end").strip():
            self.task.insert("1.0", "Read the attached plan and inspect the current project. Implement the next incomplete phase only. Preserve completed work. Propose at most 8 files. Do not run builds, tests, or the application.")
        if self.chained.get():
            self.status.set("Plan attached. Send starts its first unfinished step; each step unlocks the "
                            "next only after a command run proves it. The message box is an optional note.")
        else:
            self.status.set("Plan attached. Specify the phase to implement, select a model, then click Send.")

    def clear_plan(self):
        self.plan_file.set("")
        self.ledger_path = self.ledger = None
        self.plan_status.set("")

    def project_notes(self) -> str:
        """The saved notes for the folder in the project box, empty when there is none."""
        repo = self.repo.get().strip()
        if not repo:
            return ""
        try:
            return memory_store.read(self.memory_dir, repo)
        except AgentError:
            return ""

    def load_memory(self):
        """Show the selected project's notes in the Settings editor."""
        box = getattr(self, "memory_box", None)
        if box is None:
            return
        repo = self.repo.get().strip()
        box.configure(state="normal" if repo else "disabled")
        box.delete("1.0", "end")
        notes = self.project_notes()
        if notes:
            box.insert("1.0", notes)
        box.edit_reset()
        self.refresh_memory_info()

    def refresh_memory_info(self):
        repo = self.repo.get().strip()
        if not repo:
            self.memory_info.set("Choose a project folder to edit its notes.")
            return
        saved = self.project_notes()
        typed = self.memory_box.get("1.0", "end").rstrip("\n")
        state = "unsaved" if typed.strip() != saved.strip() else \
            (f"{len(saved)} characters saved" if saved else "no notes yet")
        self.memory_info.set(state + f" · {memory_store.key_for(repo)}.md · sent with every task here")

    def save_memory(self):
        repo = self.repo.get().strip()
        if not repo:
            self.status.set(status_text("need_folder_notes", arabic=self.arabic))
            return
        try:
            memory_store.write(self.memory_dir, repo, self.memory_box.get("1.0", "end").rstrip("\n"))
        except (AgentError, OSError) as exc:
            self.status.set(friendly_error(exc))
            return
        self.refresh_memory_info()
        self.status.set("Project notes saved outside the project folder, so a proposal cannot rewrite them.")

    def refresh_plan_status(self):
        """Mirror the attached plan's ledger into the header chip and the Settings panel."""
        repo, plan_file = self.repo.get().strip(), self.plan_file.get().strip()
        chip = "▤  " + Path(plan_file).name if plan_file else "Attach a project plan"
        self.source_name.set(chip)
        if not repo or not plan_file or not Path(repo).is_dir():
            self.ledger_path = self.ledger = None
            self.plan_status.set("")
            return
        try:
            path, book = planbook.open_book(self.plans, Workspace(Path(repo)), plan_file)
        except (AgentError, OSError) as exc:
            self.ledger_path = self.ledger = None
            self.plan_status.set("Plan steps: " + friendly_error(exc))
            return
        self.ledger_path, self.ledger = path, book
        row = planbook.current(book)
        # The chip is the one place the step count stays visible while the user works in
        # the main window; the Settings panel holds the whole ledger line.
        self.source_name.set(chip + " — " + (f"step {row['id']}/{len(book['steps'])}" if row
                                             else "all steps verified"))
        line = planbook.progress_line(book)
        if self.chained.get() and row is not None:
            line += " — Send works on it."
        self.plan_status.set(line)

    def ledger_for(self, session: dict) -> tuple[Path, dict] | None:
        """The ledger a stored session belongs to, or None when it is not a plan step."""
        reference, step_id = session.get("plan_reference"), session.get("plan_step")
        if not reference or step_id is None:
            return None
        try:
            path, book = planbook.open_book(self.plans, Workspace(Path(session["root"])),
                                            reference["path"])
        except (AgentError, OSError):
            return None
        if book.get("plan_sha256") != reference["sha256"]:
            return None
        self.ledger_path, self.ledger = path, book
        return path, book

    def advance_plan(self, result) -> None:
        """Close a step on proven evidence and move the ledger to the next one."""
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
            self.status.set(planbook.progress_line(book) + " — step " + str(step_id) +
                            " stays open until a command run proves it.")
            return
        try:
            planbook.complete(path, book, step_id, session)
        except (AgentError, OSError) as exc:
            self.chat_message("Tool", "The command passed, but step " + str(step_id) + " is not marked done: "
                              + friendly_error(exc))
            self.status.set("Step " + str(step_id) + " still open — see the conversation.")
            return
        self.refresh_plan_status()
        nxt = planbook.current(book)
        self.chat_message("Tool", f"Plan step {step_id}/{len(book['steps'])} verified. " + runner.summarize(result))
        if nxt is None:
            self.status.set(f"Plan complete — {len(book['steps'])} steps verified.")
            return
        if self.chained.get():
            self.task.delete("1.0", "end")
            self.status.set(f"Step {step_id} verified. Starting step {nxt['id']}: {nxt['title']}")
            self.start_plan()
        else:
            self.task.delete("1.0", "end")
            self.task.insert("1.0", planbook.task_for(book, nxt))
            self.status.set(f"Step {step_id} verified. The next step is in the message box — press Send.")

    def view_plan(self):
        try:
            if not self.plan_file.get().strip():
                raise AgentError("Attach a plan file first.")
            reference = read_plan_reference(Workspace(Path(self.repo.get().strip())), self.plan_file.get().strip(), Settings())
        except (AgentError, OSError) as exc:
            self.status.set(friendly_error(exc))
            return
        window = tk.Toplevel(self.root)
        window.title("Attached plan — " + reference["path"])
        window.geometry("800x600")
        view = ScrolledText(window, wrap="word", font=("Segoe UI", 11), padx=14, pady=14)
        view.pack(fill="both", expand=True)
        text_set(view, reference["content"])

    def new_task(self):
        if self.busy:
            return
        self.chat_id = uuid.uuid4().hex
        self.plan_file.set("")
        self.cloud_ok.set(False)
        self._auto_fix = False
        self._fix_round = 0
        text_set(self.log, "")
        self.session = self.session_path = None
        self.chat = None
        self.title.set("New chat")
        self.reset_conversation()
        self.task.delete("1.0", "end")
        self.clear_review()
        self.select_view("task")
        self.status.set("New chat — ask directly, or choose a project to work on its files")
        self.refresh_recent()
        self.refresh_recipes()
        self.update_buttons()

    def new_chat(self):
        """The sidebar's New chat: a conversation of its own, with no folder carried over."""
        if self.repo.get().strip():
            self.repo.set("")       # project_changed resets the conversation on the way through
        else:
            self.new_task()

    def new_project(self):
        """Grant access to a folder where a not-yet-existing project will be built."""
        if self.busy:
            return
        path = filedialog.askdirectory(title="Choose a folder for the new project",
                                       parent=self.root, mustexist=False)
        if not path:
            return
        try:
            folder = ensure_project_dir(Path(path))
        except (AgentError, OSError) as exc:
            self.status.set(friendly_error(exc))
            return
        self.new_task()
        self.repo.set(str(folder))
        self.status.set(status_text("granted", arabic=self.arabic) + str(folder) +
                        "\nDescribe what to build. Nothing is written until you approve the proposal.")

    def example(self):
        self.new_task()
        self.plan_file.set("")
        self.repo.set(str(self.app_dir / "examples" / "demo_repo"))
        self.task.insert("1.0", "Fix the add function in calculator.py so it adds the two numbers instead of subtracting them.")
        self.mode.set("Ollama")
        self.mode_changed()
        self.status.set("Sample ready. Choose an Ollama model, then click Send.")

    def mode_changed(self, _event=None):
        self.selections[self.active_mode] = self.model.get()
        self.active_mode = self.mode.get()
        self.model.set(self.selections.get(self.active_mode, ""))
        self.cloud_ok.set(False)
        self.model_filter.set("")
        self.filter_models()
        if not self.catalogs.get(self.active_mode):
            self.check_setup()

    def selected_model(self):
        return next((entry for entry in self.catalogs.get(self.mode.get(), [])
                     if entry["id"] == self.model.get()), None)

    def filter_models(self):
        query = self.model_filter.get().strip().casefold()
        entries = self.catalogs.get(self.mode.get(), [])
        values = [entry["id"] for entry in entries
                  if query in (entry["id"] + " " + entry["name"]).casefold()]
        values.sort(key=lambda name: name != DEFAULT_MODEL)
        self.model_box.configure(values=values)
        if self.model.get() and not any(entry["id"] == self.model.get() for entry in entries):
            self.model.set("")
        self.model_changed()

    def model_changed(self, _event=None):
        entry = self.selected_model()
        self.selections[self.mode.get()] = self.model.get()
        info = (entry["name"] + "\n" + entry["description"]) if entry else \
            f"{len(self.model_box.cget('values'))} models available. Select one from the list."
        if entry and entry["id"] in RECOMMENDED:
            info += "\n★ " + RECOMMENDED[entry["id"]]
        self.model_info.set(info)
        openrouter = self.mode.get().startswith("OpenRouter")
        cloud = openrouter or bool(entry and entry.get("cloud"))
        if cloud:
            self.cloud_frame.pack(fill="x", before=self.footer_actions)
            if openrouter:
                self.key_frame.pack(fill="x", before=self.cloud_consent)
            else:
                self.key_frame.pack_forget()
        else:
            self.cloud_frame.pack_forget()
            self.cloud_ok.set(False)
        if self.mode.get() == "OpenRouter · Paid":
            self.hint.configure(text="Paid requests use your OpenRouter balance. Prices are catalog estimates; set a spending limit on your API key.")
        else:
            self.hint.configure(text="You will review a proposal first. Project files are not changed automatically.")
        self._save_state()

    def check_setup(self):
        if self.busy:
            return
        selected_mode = self.mode.get()
        api_key = self.key.get().strip() or None

        def done(result):
            if selected_mode == "Ollama":
                self.catalogs["Ollama"] = result
            else:
                self.catalogs["OpenRouter · Free"] = [entry for entry in result if entry["free"]]
                self.catalogs["OpenRouter · Paid"] = [entry for entry in result if not entry["free"]]
            self.filter_models()
            pending = self._pending_model
            self._pending_model = ""
            if pending and not self.model.get() and any(entry["id"] == pending for entry in self.catalogs.get(self.mode.get(), [])):
                self.model.set(pending)
            if not self.model.get() and any(entry["id"] == DEFAULT_MODEL for entry in self.catalogs.get(self.mode.get(), [])):
                self.model.set(DEFAULT_MODEL)
            self.model_changed()
            count = len(self.catalogs[self.mode.get()])
            self.status.set(f"{count} models loaded. Using {self.model.get() or 'the model you choose'}." if count else
                            "No models found for this provider. Check the service or choose another provider.")
            self.append_log(f"Refreshed {selected_mode}: {count} models. No generation request was sent.")
        operation = ollama_models if selected_mode == "Ollama" else lambda: openrouter_models(api_key)
        self.run_job(operation, done, "Refreshing available models…")

    def unverified_prior_task(self, chat_id: str, repo: str) -> dict | None:
        """The last task in this chat whose files were applied without a passing run."""
        try:
            turns = chat_sessions(self.runs, repo, chat_id)
        except (AgentError, OSError):
            return None
        for _, item in reversed(turns):
            if item.get("state") in UNVERIFIED_STATES:
                return item
        return None

    def start_plan(self):
        if self.busy:
            return
        repo = self.repo.get().strip()
        task = self.task.get("1.0", "end").strip()
        plan_file = self.plan_file.get().strip() or None
        model = self.model.get().strip()
        openrouter = self.mode.get().startswith("OpenRouter")
        entry = self.selected_model()
        cloud = openrouter or bool(entry and entry.get("cloud"))
        paid = self.mode.get() == "OpenRouter · Paid"
        if repo and not Path(repo).is_dir():
            self.status.set(status_text("need_folder_exists", arabic=self.arabic))
            return
        # In step-by-step mode the ledger supplies the task, so an empty message box is a
        # note-free run rather than a missing request.
        chained_plan = bool(repo and plan_file and self.chained.get())
        if (not task and not chained_plan) or len(task) > MAX_TASK_CHARS or not model:
            self.status.set(status_text("too_long", arabic=self.arabic))
            return
        if entry is None:
            self.status.set(status_text("pick_model", arabic=self.arabic))
            return
        if cloud and not self.cloud_ok.get():
            self.status.set(status_text("consent_message", arabic=self.arabic))
            return
        settings = replace(Settings(), provider="openrouter" if openrouter else "ollama", model=model,
                           max_turns=8 if cloud else 12)
        key = self.key.get().strip() or None
        if not repo:
            self.start_chat(task, settings, cloud, paid, key)
            return
        if plan_file:
            try:
                read_plan_reference(Workspace(Path(repo)), plan_file, settings)
            except (AgentError, OSError) as exc:
                self.status.set(friendly_error(exc))
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
                self.status.set(friendly_error(exc))
                return
            self.ledger_path, self.ledger = ledger_path, book
        chat_id = self.chat_id
        prior = self.unverified_prior_task(chat_id, repo)
        if prior:
            note = ("The last task here ('" + str(prior.get("task", ""))[:60] + "') left files applied "
                    "without a passing command run (" + prior["state"] + ").")
            if prior["state"] in INTERRUPTED_STATES:
                if not messagebox.askyesno("Unfinished task",
                                           note + "\n\nContinue with a new task anyway? Rolling back that "
                                           "task first is the safer step.", parent=self.root):
                    self.status.set(note + " Roll it back or run its command, then start the new task.")
                    return
                self.chat_message("Tool", note + " Continuing on this state by your choice.")
            else:
                self.chat_message("Tool", note + " Run its command (or roll it back) before stacking "
                                  "more changes on top of it.")
                self.status.set(status_text("prior_unverified", arabic=self.arabic))
        self.title.set(task.replace("\n", " ")[:45])
        self.chat_message("You", task)
        self.session = self.session_path = None
        self.clear_review()
        notes = self.project_notes()
        if notes:
            self.chat_message("Tool", "Your saved project notes (" + str(len(notes)) +
                              " characters) are part of this request.")

        def work():
            provider = make_provider(settings, allow_cloud=cloud, data_class="public" if cloud else "restricted", api_key=key,
                                     allow_paid=paid)
            return plan(Workspace(Path(repo)), task, provider, settings, self.runs,
                        progress=lambda line: self.events.put(("progress", line)), cancelled=self.cancel_event.is_set,
                        plan_file=plan_file, chat_id=chat_id, plan_step=step_id, memory=notes)

        def done(path):
            if step_id is not None:
                try:
                    planbook.record_session(self.ledger_path, self.ledger, step_id, path.parent.name)
                except (AgentError, OSError) as exc:
                    self.chat_message("Tool", "The plan ledger was not updated: " + friendly_error(exc))
            self.display_session(path)
            if self.session and self.session.get("changes"):
                self.select_view("review")
                self.status.set(status_text("proposal_ready", arabic=self.arabic))
            else:
                self.status.set(status_text("no_proposal", arabic=self.arabic))
        self.run_job(work, done, "Connecting to the model and preparing changes…", cancellable=True)

    def current_chat(self):
        if self.chat is None or self.chat.get("id") != self.chat_id:
            self.chat = create_chat(self.model.get() or Settings().model, self.chat_id)
        return self.chat

    def start_chat(self, task: str, settings, cloud: bool, paid: bool, key: str | None):
        """No project selected: plain question answering, no proposal, no file access."""
        chat = self.current_chat()
        self.session = self.session_path = None
        self.clear_review()
        self.title.set(chat.get("title") or task.replace("\n", " ")[:45])
        self.chat_message("You", task)
        self.task.delete("1.0", "end")

        def work():
            provider = make_provider(settings, allow_cloud=cloud, data_class="public" if cloud else "restricted",
                                     api_key=key, allow_paid=paid)
            return respond(chat, provider, task, settings, self.chats)

        def done(reply):
            self.chat_message("AI Code Engineer", reply)
            self.title.set(title_for(chat))
            self.status.set("Answered. Choose a project when you want reviewed changes to real files.")
            self.refresh_recent()
        self.run_job(work, done, "Thinking…")

    def stop(self):
        if self.busy and self.cancellable:
            self.cancel_event.set()
            self.stop_button.configure(state="disabled")
            self.status.set("Stop requested. Waiting for the current model request to finish; no changes will be applied.")

    def _load_session_cached(self, path: Path):
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

    def _load_chat_cached(self, path: Path):
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

    def _search_focus(self, _event=None):
        focused = self.root.focus_get() is self.search_box
        if focused and self.search.get() == SEARCH_PLACEHOLDER:
            self.search.set("")
            self.search_box.configure(foreground=INK)
        elif not focused and not self.search.get().strip():
            self.search.set(SEARCH_PLACEHOLDER)
            self.search_box.configure(foreground=MUTED)

    def _search_query(self) -> str:
        value = self.search.get().strip().casefold()
        return "" if value == SEARCH_PLACEHOLDER.casefold() else value

    def refresh_recent(self):
        query = self._search_query()
        expanded = {self.project_nodes[node] for node in self.recent.get_children()
                    if self.recent.item(node, "open") and node in self.project_nodes}
        self.recent.delete(*self.recent.get_children())
        self.project_nodes, self.chat_nodes = {}, {}
        groups = {key: {} for key in self.projects}
        for path in self.runs.glob("*/session.json"):
            session = self._load_session_cached(path)
            if session is None:
                continue
            try:
                root = project_key(session["root"])
                self.projects[root] = session["root"]
                chat = session.get("chat_id", session["id"])
                groups.setdefault(root, {}).setdefault(chat, []).append((path, session))
            except (KeyError, TypeError, ValueError, OSError):
                continue
        loose = []
        for path in self.chats.glob("*/chat.json"):
            chat = self._load_chat_cached(path)
            if chat:
                loose.append((path, chat))
        loose.sort(key=lambda pair: pair[1].get("created", ""), reverse=True)
        visible_chats = [pair for pair in loose
                         if not query or query in title_for(pair[1]).casefold()]
        if visible_chats:
            self.recent.insert("", "end", iid=CHAT_GROUP, text="Chats  ·  no project",
                               open=CHAT_GROUP in expanded or not self.current_project)
            self.project_nodes[CHAT_GROUP] = CHAT_GROUP
            for index, (path, chat) in enumerate(visible_chats):
                child = CHAT_GROUP + "-chat-" + str(index)
                self.recent.insert(CHAT_GROUP, "end", iid=child, text=title_for(chat))
                self.chat_nodes[child] = ("chat", path)
        for number, (root, chats) in enumerate(groups.items()):
            folder = Path(self.projects[root])
            label = folder.name + " · " + str(folder.parent)
            ordered = sorted(chats.values(), key=lambda turns: max(t[1].get("created", "") for t in turns), reverse=True)
            turns_by_chat = []
            for turns in ordered:
                turns.sort(key=lambda pair: (pair[1].get("created", ""), pair[1]["id"]))
                title = turns[0][1].get("task", "Chat").replace("\n", " ")[:60]
                if not query or query in title.casefold() or query in label.casefold():
                    turns_by_chat.append((turns[-1][0], title))
            if query and not turns_by_chat and query not in label.casefold():
                continue
            node = "project-" + str(number)
            self.recent.insert("", "end", iid=node, text=label,
                               open=root in expanded or root == self.current_project or bool(query))
            self.project_nodes[node] = root
            for index, (path, title) in enumerate(turns_by_chat):
                child = node + "-chat-" + str(index)
                self.recent.insert(node, "end", iid=child, text=title)
                self.chat_nodes[child] = ("session", path)

    def open_recent(self, _event=None):
        if self.busy or not self.recent.selection():
            return
        node = self.recent.selection()[0]
        if node in self.chat_nodes:
            kind, path = self.chat_nodes[node]
            self.open_chat(path) if kind == "chat" else self.open_session(path)
        elif node in self.project_nodes:
            root = self.project_nodes[node]
            if root != CHAT_GROUP:
                self.repo.set(self.projects[root])

    def open_file(self):
        path = filedialog.askopenfilename(title="Open saved task", initialdir=str(self.runs),
                                          filetypes=[("Agent session", "session.json")], parent=self.root)
        if path:
            self.open_session(Path(path))

    def open_chat(self, path: Path):
        """Reopen a project-free conversation."""
        try:
            chat = load_chat(path)
        except (AgentError, OSError) as exc:
            self.status.set(friendly_error(exc))
            return
        self.chat = chat
        self.chat_id = chat["id"]
        self.session = self.session_path = None
        if self.repo.get().strip():
            # A project-free chat must not keep editing the previously selected project.
            self._loading_session = True
            try:
                self.repo.set("")
            finally:
                self._loading_session = False
        self.clear_review()
        self.title.set(title_for(chat))
        self.clear_conversation()
        for turn in chat["turns"]:
            if turn.get("role") == "user":
                self.messages.append(("You", turn.get("content", "")))
            elif turn.get("role") == "assistant":
                self.messages.append(("AI Code Engineer", turn.get("content", "")))
        self.render_messages()
        self._idle(lambda: self.conversation.yview_moveto(1))
        self.select_view("task")
        self.status.set("Chat reopened — still no project attached.")

    def open_session(self, path: Path):
        try:
            self.display_session(path)
            self.status.set(status_text("task_opened", arabic=self.arabic))
        except (AgentError, OSError, ValueError) as exc:
            self.status.set(friendly_error(exc))

    def clear_review(self):
        self.state_label.set("No proposal yet")
        self.artifact.set("No proposal yet")
        self.run_info.set("No command has run yet.")
        self.artifact_detail.set("This chat has no project attached, so nothing is proposed."
                                 if not self.repo.get().strip() else
                                 "Choose a project to turn a request into reviewed file changes.")
        text_set(self.summary, "Start a task to see proposed changes here.")
        self.files.delete(*self.files.get_children())
        for widget in self.code_views.values():
            text_set(widget, "")

    def display_session(self, path: Path, *, select_tab=True):
        session = load_session(path)
        self.session, self.session_path = session, path
        self.state_label.set(state_label(session["state"], arabic=self.arabic))
        changes = session.get("changes", [])
        self.artifact.set(state_label(session["state"], arabic=self.arabic))
        self.artifact_detail.set(
            f"{len(changes)} proposed file(s) in {Path(session['root']).name}" if changes
            else "No applicable proposal was created for this task.")
        summary = session.get("summary", "No applicable proposal was created.")
        if session.get("plan_reference"):
            summary += "\nAttached plan: " + session["plan_reference"]["path"]
            if session.get("plan_step") is not None:
                summary += f" — step {session['plan_step']}, gated on a passing command run"
        text_set(self.summary, summary + "\n\nProject: " + session["root"])
        self.files.delete(*self.files.get_children())
        for index, change in enumerate(session.get("changes", [])):
            self.files.insert("", "end", iid=str(index), text=change["path"],
                              values=("Removed" if change.get("delete")
                                      else "Added" if change["before"] is None else "Modified",))
        checks = "Proposed checks (not execution results):\n" + "\n".join("• " + c for c in session.get("checks", []))
        result = session.get("verification")
        if result:
            checks += "\n\nLatest check: " + result.get("status", "unknown")
            for item in result.get("static", []):
                checks += "\n" + item["path"] + ": " + item["status"]
            if result.get("reason"):
                checks += "\n" + result["reason"]
        runs = session.get("runs") or []
        if runs:
            last = runs[-1]
            checks += (f"\n\nCommand runs ({len(runs)}): last was {last.get('command', '')}\n"
                       f"→ {last.get('status')} · exit {last.get('exit_code')} · {last.get('seconds')}s")
            checks += "\n" + "\n".join("  " + str(row)[:200] for row in (last.get("failures") or [])[:8])
            self.run_info.set(f"{len(runs)} run(s). Last: {last.get('label')} — {last.get('status')} "
                              f"(exit {last.get('exit_code')}, {last.get('seconds')}s)")
        else:
            self.run_info.set("No command has run yet.")
        text_set(self.code_views["checks"], checks)
        if session.get("changes"):
            self.files.selection_set("0")
            self.show_change()
        else:
            for key in ("diff", "before", "after"):
                text_set(self.code_views[key], "No changes.")
        if select_tab:
            self.title.set(session.get("task", "Saved task").replace("\n", " ")[:45])
            self.chat_id = session.get("chat_id", session["id"])
            self._loading_session = True
            try:
                self.repo.set(session["root"])
            finally:
                self._loading_session = False
            self.chat_id = session.get("chat_id", session["id"])
            self.cloud_ok.set(False)
            text_set(self.log, "")
            for entry in session.get("events", []):
                self.append_log(entry.get("at", "") + "  " + entry.get("kind", ""))
            reference = session.get("plan_reference")
            self.plan_file.set(str(Path(session["root"]) / reference["path"]) if reference else "")
            self.clear_conversation()
            turns = chat_sessions(self.runs, session["root"], self.chat_id)
            if not any(turn["id"] == session["id"] for _, turn in turns):
                turns.append((path, session))
            for _, turn in turns:
                asked = turn.get("task", "Saved task")
                # Per row, not per window: a reopened history can hold an Arabic task and an English
                # one, and each line was written in the language its own task asked in.
                asked_in_arabic = is_arabic(asked)
                body = (turn.get("summary") or turn.get("error")
                        or status_text("no_proposal", arabic=asked_in_arabic))
                self.messages.append(("You", asked))
                self.messages.append(("AI Code Engineer",
                                      state_label(turn["state"], arabic=asked_in_arabic) +
                                      "\n\n" + body))
            self.render_messages()
            if session.get("changes"):
                self.chat_message("Changes", self.removal_notice(session)
                                  + "\n".join(change["path"] for change in session["changes"])
                                  + "\n\nOpen the Changes tab to review and approve.")
            self.task.configure(state="normal")
            self.task.delete("1.0", "end")
            self.select_view("task")
            self.refresh_recent()
        self.refresh_recipes()
        self.update_buttons()

    def show_change(self, _event=None):
        if not self.session or not self.files.selection():
            return
        change = self.session["changes"][int(self.files.selection()[0])]
        # A delete has no `after`, and the "after" pane showing nothing is the true picture.
        before, after = change["before"] or "", change["after"] or ""
        text_set(self.code_views["before"], before)
        text_set(self.code_views["after"], after)
        lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile="before/" + change["path"],
                                         tofile="after/" + change["path"], lineterm=""))
        view = self.code_views["diff"]
        text_set(view, "\n".join(lines))
        for line, value in enumerate(lines, 1):
            tag = "header" if value.startswith(("---", "+++", "@@")) else "add" if value.startswith("+") else "remove" if value.startswith("-") else None
            if tag:
                view.tag_add(tag, f"{line}.0", f"{line}.end")

    def removal_notice(self, session: dict | None = None) -> str:
        """The one sentence both windows show; the copy lives in ``repair``."""
        return repair.removal_notice(session if session is not None else self.session)

    def apply(self):
        if self.busy or not self.session or self.session.get("state") != "WAITING_APPROVAL":
            return
        again = ""
        if self._auto_fix and self.selected_recipe():
            again = ("\nIt will then run " + runner.RECIPES[self.selected_recipe()]["label"] +
                     " again in that folder, which executes the project's own build and test code.")
        # The same dialog the web window shows, from the same builder. This window used to assemble
        # its own and had drifted: it never passed `must_ask`'s reason in, so a proposal that emptied
        # a file warned about it here only because the removal notice happened to say the same thing.
        prompt = host.apply_prompt(self.session, notice=self.removal_notice(),
                                   reason=repair.must_ask(self.session), again=again)
        if not messagebox.askyesno(prompt["title"], prompt["message"], parent=self.root):
            return
        path, approved = self.session_path, self.session["proposal_hash"]
        self.run_job(lambda: apply_proposal(path, approved), lambda _: self.applied(path), "Applying the changes you reviewed…")

    def applied(self, path):
        self.display_session(path)
        if self._auto_fix:
            self.status.set(status_text("applied_rerun", arabic=self.arabic))
            self.run_tests(False)
            return
        self.status.set(status_text("applied_idle", arabic=self.arabic))

    def refresh_recipes(self):
        """Detect the commands this project folder actually answers to."""
        repo = self.repo.get().strip()
        try:
            self.recipes = runner.detect(Path(repo)) if repo and Path(repo).is_dir() else []
        except OSError:
            self.recipes = []
        labels = [runner.RECIPES[name]["label"] for name in self.recipes]
        self.recipe_box.configure(values=labels)
        if self.recipe.get() not in labels:
            self.recipe.set(labels[0] if labels else "")
        if labels:
            if not self.checks_frame.winfo_manager():
                self.checks_frame.pack(anchor="w", pady=(0, 12), before=self.sources_label)
        else:
            self.checks_frame.pack_forget()
            self.run_info.set("No command has run yet.")
        self.update_buttons()

    def selected_recipe(self):
        labels = [runner.RECIPES[name]["label"] for name in self.recipes]
        try:
            return self.recipes[labels.index(self.recipe.get())]
        except ValueError:
            return None

    def request_timeout_seconds(self) -> int:
        try:
            value = self.request_timeout.get()
        except tk.TclError:
            value = None                    # the widget is gone; the default is the honest answer
        return config.clamp_request_timeout(value)

    def provider_args(self):
        """Validate the composer selection; returns settings for one more request."""
        model = self.model.get().strip()
        entry = self.model.get() and self.selected_model()
        if entry is None:
            self.status.set("Select a model from the list first, then try again.")
            return None
        openrouter = self.mode.get().startswith("OpenRouter")
        cloud = openrouter or bool(entry.get("cloud"))
        if cloud and not self.cloud_ok.get():
            self.status.set(status_text("consent_project", arabic=self.arabic))
            return None
        settings = replace(Settings(), provider="openrouter" if openrouter else "ollama",
                           model=model, max_turns=8 if cloud else 12,
                           timeout_seconds=self.request_timeout_seconds())
        return settings, cloud, self.mode.get() == "OpenRouter · Paid", (self.key.get().strip() or None)

    def run_tests(self, auto_fix: bool = False):
        """Run one allowlisted command in the approved folder and record the result."""
        if self.busy:
            return
        if not self.session or self.session.get("state") not in MUTABLE_STATES:
            self.status.set(status_text("apply_first", arabic=self.arabic))
            return
        recipe = self.selected_recipe()
        if recipe is None:
            self.status.set(status_text("no_recipe", arabic=self.arabic))
            return
        self._auto_fix = bool(auto_fix)
        if auto_fix:
            self._fix_round = 0
        repo, path = self.session["root"], self.session_path
        label = runner.RECIPES[recipe]["label"]

        def work():
            result = runner.run(Path(repo), recipe, timeout=runner.timeout_for(recipe),
                                progress=lambda line: self.events.put(("progress", line)))
            return repair.record_run(path, result), result

        def done(pair):
            self.display_session(path)
            self.report_run(pair[1])
        self.run_job(work, done, f"Running {label} in {Path(repo).name}…")

    def report_run(self, result):
        summary = runner.summarize(result)
        self.run_info.set(summary)
        self.chat_message("Checks", summary)
        if result["status"] == "passed":
            self._auto_fix = False
            self.status.set(status_text("command_passed", arabic=self.arabic) + summary)
        elif self._auto_fix and result["status"] in {"failed", "timeout"}:
            self.ask_for_fix(result)
            return
        else:
            self._auto_fix = False
            self.status.set(summary + " — the captured output is in the Checks tab.")
        self.advance_plan(result)

    def ask_for_fix(self, run):
        """Feed the failure back as one more reviewable proposal, up to a bound."""
        if self._fix_round >= repair.MAX_FIX_ROUNDS:
            self._auto_fix = False
            self.status.set(f"Stopped after {repair.MAX_FIX_ROUNDS} fix rounds and the command still fails. "
                            "Try a narrower task, another model, or inspect the output in Checks.")
            return
        args = self.provider_args()
        if args is None:
            self._auto_fix = False
            return
        settings, cloud, paid, key = args
        self._fix_round += 1
        repo, chat_id = self.session["root"], self.chat_id
        # A fix session still implements the same plan step, so it has to carry the plan
        # and the step number: the step completes on whichever session makes the command pass.
        reference, step_id = self.session.get("plan_reference"), self.session.get("plan_step")
        plan_file = str(Path(repo) / reference["path"]) if reference and step_id is not None else None
        task, evidence = repair.fix_task(run), repair.evidence(run)
        notes = self.project_notes()
        status = (f"Fix round {self._fix_round}/{repair.MAX_FIX_ROUNDS}: asking {settings.model} "
                  "for the smallest change that makes the command pass…")

        def work():
            provider = make_provider(settings, allow_cloud=cloud,
                                     data_class="public" if cloud else "restricted",
                                     api_key=key, allow_paid=paid)
            return plan(Workspace(Path(repo)), task, provider, settings, self.runs,
                        progress=lambda line: self.events.put(("progress", line)),
                        cancelled=self.cancel_event.is_set, chat_id=chat_id, extra_context=evidence,
                        plan_file=plan_file, plan_step=step_id if plan_file else None, memory=notes)

        def done(path):
            self.display_session(path)
            if plan_file:
                try:
                    pair = self.ledger_for(self.session)
                    if pair:
                        planbook.record_session(pair[0], pair[1], step_id, path.parent.name)
                except (AgentError, OSError) as exc:
                    self.chat_message("Tool", "The plan ledger was not updated: " + friendly_error(exc))
            self.select_view("review")
            self.status.set(status_text("fix_ready", arabic=self.arabic))
        self.run_job(work, done, status, cancellable=True)

    def check_changes(self):
        if self.busy or not self.session or self.session.get("state") not in MUTABLE_STATES:
            return
        path = self.session_path

        def done(result):
            self.display_session(path)
            self.status.set("Syntax check found a problem. Open the Checks tab for details." if result["status"] == "failed" else
                            "Syntax checks finished. Project tests have not run; verification remains incomplete.")
        self.run_job(lambda: verify(path), done, "Checking syntax in changed files…")

    def undo(self):
        if self.busy or not self.session or self.session.get("state") not in MUTABLE_STATES | {"PARTIAL_APPLY", "APPLYING"}:
            return
        if not messagebox.askyesno("Roll back changes", "Restore this task's files to their previous contents?\nLater edits will block rollback to protect your work.", parent=self.root):
            return
        path, approved = self.session_path, self.session["proposal_hash"]

        def done(_):
            self.display_session(path)
            self.status.set(status_text("rolled_back", arabic=self.arabic))
            step_id = self.session.get("plan_step") if self.session else None
            pair = self.ledger_for(self.session) if step_id is not None and self.session else None
            if pair:
                try:
                    planbook.reopen(pair[0], pair[1], step_id)
                except (AgentError, OSError) as exc:
                    self.status.set(status_text("ledger_needs_look", arabic=self.arabic) + friendly_error(exc))
                    return
                self.refresh_plan_status()
                self.chat_message("Tool", "Reopened plan step " + str(step_id) +
                                  ": the files that passed are gone, so the step must be implemented again.")
        self.run_job(lambda: rollback(path, approved), done, "Rolling back changes…")

    def close(self):
        if self.busy:
            if self.cancellable:
                if messagebox.askyesno("Close application", "Stop the task and close the window after the current request finishes?", parent=self.root):
                    self.closing = True
                    self.stop()
            else:
                messagebox.showinfo("Operation in progress", "Wait for the current operation to finish before closing the window.", parent=self.root)
            return
        self.cancel_timers()
        self.key.set("")
        self.root.destroy()


def main(app_dir: Path | None = None):
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
    root = tk.Tk()
    AgentWindow(root, app_dir or Path(__file__).resolve().parents[2])
    root.mainloop()
