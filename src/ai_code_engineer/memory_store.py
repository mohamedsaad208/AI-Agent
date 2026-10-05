"""Two layers of markdown memory per project: what it is for, and what each chat did.

The loop already keeps two kinds of note — the user's own standing instructions in
``.agent-memory/<project>.md`` and what the runs recorded of themselves in ``<project>.auto.json``
— and neither answers the question a *next* task actually asks: what is this project trying to be,
and how far along is it. A small local model re-derives both from a repository map on every turn,
and a person who closes the window and opens a chat an hour later re-types them. This module keeps
the answer in two human-readable files where a model can neither write nor read them as source:

* ``<project>/.agent/memory/project.md`` — the project layer: the goal, the constraints it is held
  to, the decisions already taken, the questions still open, and the progress its tasks left behind.
* ``<project>/.agent/memory/chats/<chat_id>.md`` — one chat layer per conversation: the task this
  chat was asked to do and the steps it has taken.

The location is the specification's, and it is safe for a reason that is not the obvious one.
``.agent`` is in ``workspace.AGENT_CONFIG_DIRS``, so every proposal path that touches it is refused
as policy (``workspace.path`` with ``writable=True``), and it is now in ``ignore.GENERATED_DIRS``,
so the repository map never lists it and the model never reads its own compass as a file to edit.
The layer the model cannot write is the layer that can be trusted to hold it to something.

Files are the storage and the format is markdown, because a person must be able to open
``project.md``, change a line, and have the next task obey it. That is the whole reason this store
is not another ``.json`` beside the old ones: the machine state it needs — the digest the goal is
protected by, and a proposed goal waiting for its answer — lives in a sidecar instead, so the
document a human reads stays a document.

Nothing here decides whether a goal may change, condenses a run into a sentence, or builds the text
a prompt is fed. ``memory_summarizer`` owns that policy and calls the setters below; ``compass``
reads what is stored and says it to the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .errors import PolicyError
from .memory import _resilient_replace
from .redaction import redact

LAYER_PROJECT = "project"
LAYER_CHAT = "chat"

MEMORY_SUBDIR = Path(".agent") / "memory"
PROJECT_FILE = "project.md"
CHATS_DIR = "chats"
META_FILE = "project.json"

# The same identity `engine.plan` validates a chat with, so one id names one conversation in the
# session record and in the file that remembers it. A looser rule here would let a caller write
# outside `chats/` with a "chat id" that contains a path separator.
CHAT_ID = re.compile(r"[a-f0-9]{32}")

SCALAR_CHARS = 400
ITEM_CHARS = 240

# Per-section ceilings, and the order the sections are written in. A list at its cap keeps what it
# was given first and refuses the newest item rather than dropping an early one: the constraint
# stated in the first turn of a project is the one a model is most likely to have forgotten by now.
CAPS = {"constraints": 12, "decisions": 12, "open_issues": 10, "progress": 24, "steps": 24}

PROJECT_ORDER = ("goal", "constraints", "decisions", "open_issues", "progress")
CHAT_ORDER = ("task", "steps", "decisions", "open_issues")
SHAPE = {"goal": "scalar", "task": "scalar", "constraints": "list", "decisions": "list",
         "open_issues": "list", "progress": "list", "steps": "list"}
HEADING = {"goal": "Goal", "constraints": "Constraints", "decisions": "Decisions",
           "open_issues": "Open issues", "progress": "Progress", "task": "Current task",
           "steps": "Steps"}
# One file, so a project's memory cannot grow without bound and quietly outrun the budget the
# compass is given. Over cap, the write is refused rather than cut: half a document that reads as
# a whole one is worse than no document.
MAX_DOC_CHARS = 8000

TITLE = {"project": "# Project memory", "chat": "# Chat memory"}
HAND_NOTE = ("<!-- Edited by the memory engine and safe to edit by hand: what you write here is "
             "read back to the model on the next turn. -->")

DEFAULT_META = {"goal_sha256": "", "pending_goal": "", "pending_at": "", "goal_source": "",
                "updated_at": ""}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Extra:
    """A heading this store does not know, kept so a hand-edited section survives a write."""

    heading: str
    lines: list = field(default_factory=list)


@dataclass
class MemoryDoc:
    """One memory file, read as sections rather than as a string.

    `fields` holds a ``str`` for the scalar sections and a ``list[str]`` for the bullet ones, keyed
    by the section names in :data:`SHAPE`; `extra` holds whatever headings a person added by hand.
    A section that is empty is not written, so a document that says nothing is an absent file
    rather than a page of headings — and an absent file costs a turn nothing.
    """

    layer: str
    fields: dict = field(default_factory=dict)
    extra: list = field(default_factory=list)

    def text(self, name: str) -> str:
        return str(self.fields.get(name) or "")

    def items(self, name: str) -> list:
        return list(self.fields.get(name) or [])

    def is_empty(self) -> bool:
        return not any(self.fields.get(name) for name in ORDER[self.layer]) and not self.extra


ORDER = {LAYER_PROJECT: PROJECT_ORDER, LAYER_CHAT: CHAT_ORDER}


def clean_item(value) -> str:
    """One line of memory: credentials out, one line only, bounded."""
    text = redact(str(value).replace("\r", " ").replace("\n", " ").strip())
    return text[:ITEM_CHARS]


def clean_scalar(value) -> str:
    text = redact(str(value).replace("\r", " ").strip())
    return re.sub(r"\s+", " ", text)[:SCALAR_CHARS]


class MemoryStore:
    """The two files one project keeps its memory in, and the only thing allowed to write them.

    The store is built from the project root and nothing else: where the files live is a property of
    the project, not of the surface that is asking, so the CLI, the window and the engine all reach
    the same bytes. A root that is blank is refused the way the old notes refuse it — a memory with
    no project to belong to would be shared by every project that ever opened this app folder.
    """

    def __init__(self, project_root, *, subdir: Path | str = MEMORY_SUBDIR) -> None:
        if not str(project_root or "").strip():
            raise PolicyError("Choose a project folder before using its memory.")
        self.root = Path(project_root).expanduser()
        self.dir = self.root / subdir

    # ------------------------------------------------------------------ paths

    def project_path(self) -> Path:
        return self.dir / PROJECT_FILE

    def chat_path(self, chat_id: str) -> Path:
        return self.chats_dir / _chat_file(chat_id)

    @property
    def chats_dir(self) -> Path:
        return self.dir / CHATS_DIR

    def meta_path(self) -> Path:
        return self.dir / META_FILE

    def paths(self) -> dict:
        """Every file this store owns, for a panel or a readout to name."""
        return {"dir": str(self.dir), "project": str(self.project_path()),
                "chats": str(self.chats_dir), "meta": str(self.meta_path())}

    def exists(self, layer: str = LAYER_PROJECT, chat_id: str = "") -> bool:
        return self._path(layer, chat_id).is_file()

    def _path(self, layer: str, chat_id: str = "") -> Path:
        if layer == LAYER_PROJECT:
            return self.project_path()
        if layer == LAYER_CHAT:
            return self.chat_path(chat_id)
        raise PolicyError("Memory comes in two layers: project and chat.")

    # ------------------------------------------------------------------ reading and writing

    def read(self, layer: str = LAYER_PROJECT, chat_id: str = "") -> MemoryDoc:
        return _parse(_read_text(self._path(layer, chat_id)), layer)

    def read_project(self) -> MemoryDoc:
        return self.read(LAYER_PROJECT)

    def read_chat(self, chat_id: str) -> MemoryDoc:
        return self.read(LAYER_CHAT, chat_id)

    def write(self, doc: MemoryDoc, layer: str | None = None, chat_id: str = "") -> Path:
        """Replace one layer's document, or remove the file when the document says nothing."""
        layer = layer or doc.layer
        path = self._path(layer, chat_id)
        text = _render(doc)
        if len(text) > MAX_DOC_CHARS:
            raise PolicyError(f"Memory must stay within {MAX_DOC_CHARS} characters "
                              f"(this is {len(text)}).")
        if not doc.is_empty():
            text = "\n".join([TITLE[layer], HAND_NOTE, text.lstrip("\n")])
        self._place(path, text)
        return path

    def write_project(self, doc: MemoryDoc) -> Path:
        return self.write(doc, LAYER_PROJECT)

    def write_chat(self, chat_id: str, doc: MemoryDoc) -> Path:
        return self.write(doc, LAYER_CHAT, chat_id)

    def _place(self, path: Path, text: str) -> None:
        """Stage then replace, and take the file away when the document went empty.

        Written through a temporary file in the folder itself because a crash between the two halves
        of a plain write used to leave half a goal statement on disk — and a half a goal is a
        sentence a model is told to obey.
        """
        if not text.strip():
            self._forget(path)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, staged = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            _resilient_replace(staged, path)
        except OSError as exc:
            try:
                os.unlink(staged)
            except OSError:
                pass
            raise PolicyError("Could not save the memory file: " + str(exc)[:160]) from None
        self.touch()

    def _forget(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------------ section edits

    def set_text(self, section: str, value: str, layer: str = LAYER_PROJECT,
                 chat_id: str = "") -> MemoryDoc:
        """Set one scalar section and write the layer back. Never the goal: that goes through `set_goal`."""
        if SHAPE.get(section) != "scalar":
            raise PolicyError("`" + str(section) + "` is not a section this store writes in full.")
        if section == "goal":
            raise PolicyError("The goal is protected: record it through the goal guard, which "
                              "carries the approval the file was written with.")
        doc = self.read(layer, chat_id)
        clean = clean_scalar(value)
        if clean:
            doc.fields[section] = clean
        else:
            doc.fields.pop(section, None)
        self.write(doc, layer, chat_id)
        return doc

    def add_items(self, section: str, values, layer: str = LAYER_PROJECT,
                  chat_id: str = "") -> dict:
        """Append to one bullet section, keeping it deduplicated and inside its cap.

        Returns the items with, and without, a place: a caller that just watched its note dropped
        because the section is full has to be able to say so rather than report a write that did
        nothing. The cap keeps what arrived first, which is the discipline ``taskstate`` already
        holds for a constraint the user stated.
        """
        if SHAPE.get(section) != "list":
            raise PolicyError("`" + str(section) + "` is not a list section.")
        doc = self.read(layer, chat_id)
        current = [str(item) for item in (doc.items(section))]
        kept, refused = [], []
        for value in (values if isinstance(values, (list, tuple)) else [values]):
            clean = clean_item(value)
            if not clean or clean in current:
                continue
            if len(current) >= CAPS.get(section, 12):
                refused.append(clean)
                continue
            current.append(clean)
            kept.append(clean)
        if not (kept or refused):
            return {"added": [], "refused": [], "unchanged": True}
        doc.fields[section] = current
        self.write(doc, layer, chat_id)
        return {"added": kept, "refused": refused, "unchanged": False}

    # ------------------------------------------------------------------ the goal and its digest

    def goal(self) -> str:
        return self.read_project().text("goal")

    def set_goal(self, text: str, *, source: str = "agent") -> str:
        """Write the Goal section and re-baseline the digest that protects it.

        Only the summarizer's approved paths call this, which is why it is the single place the
        digest moves: a goal that changed without the record changing with it is a goal that was
        rewritten behind somebody's back.
        """
        clean = clean_scalar(text)
        doc = self.read_project()
        if clean:
            doc.fields["goal"] = clean
        else:
            doc.fields.pop("goal", None)
        self.write(doc, LAYER_PROJECT)
        meta = self.read_meta()
        meta["goal_sha256"] = goal_digest(clean)
        meta["goal_source"] = source if clean else ""
        meta["pending_goal"] = ""
        meta["pending_at"] = ""
        self.write_meta(meta)
        return clean

    def note_pending_goal(self, text: str) -> dict:
        """Hold a proposed goal that has not been answered yet — it changes nothing else."""
        meta = self.read_meta()
        meta["pending_goal"] = clean_scalar(text)
        meta["pending_at"] = now()
        self.write_meta(meta)
        return meta

    def clear_pending_goal(self) -> dict:
        meta = self.read_meta()
        meta["pending_goal"] = ""
        meta["pending_at"] = ""
        self.write_meta(meta)
        return meta

    def goal_is_current(self) -> bool:
        """Whether the Goal section still matches the digest that was set with it.

        False means the file was edited by a hand rather than through `set_goal`. That is a user
        speaking, not an intrusion, and the caller re-baselines rather than refusing the edit.
        """
        meta = self.read_meta()
        if not meta.get("goal_sha256"):
            return True
        return meta["goal_sha256"] == goal_digest(self.goal())

    # ------------------------------------------------------------------ machine sidecar

    def read_meta(self) -> dict:
        path = self.meta_path()
        if not path.is_file():
            return dict(DEFAULT_META)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A sidecar that will not parse is a lost approval flag, not a lost document. The goal
            # itself is in the markdown, which is what the model is fed; answer with the defaults
            # and let the next write put the record back.
            return dict(DEFAULT_META)
        return {**DEFAULT_META, **data} if isinstance(data, dict) else dict(DEFAULT_META)

    def write_meta(self, data: dict) -> Path:
        clean = {**DEFAULT_META, **{key: value for key, value in (data or {}).items()
                                    if key in DEFAULT_META}}
        clean["updated_at"] = now()
        path = self.meta_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, staged = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(clean, stream, indent=2)
            _resilient_replace(staged, path)
        except OSError as exc:
            try:
                os.unlink(staged)
            except OSError:
                pass
            raise PolicyError("Could not save the memory record: " + str(exc)[:160]) from None
        return path

    def touch(self) -> dict:
        meta = self.read_meta()
        meta["updated_at"] = now()
        return self.write_meta(meta)

    # ------------------------------------------------------------------ chats and clearing

    def chat_ids(self) -> list:
        """Every conversation this project has a memory file for, newest file first."""
        if not self.chats_dir.is_dir():
            return []
        rows = [(path.stem, path.stat().st_mtime_ns) for path in
                self.chats_dir.glob("*.md") if CHAT_ID.fullmatch(path.stem)]
        return [name for name, _stamp in sorted(rows, key=lambda row: -row[1])]

    def reset(self, layer: str = LAYER_PROJECT, chat_id: str = "") -> bool:
        """Remove one layer's memory. Returns whether there was anything to remove.

        The goal digest goes with the project layer, or the next goal the engine records would be
        measured against a statement nobody can any longer read.
        """
        path = self._path(layer, chat_id)
        existed = path.is_file()
        self._forget(path)
        if layer == LAYER_PROJECT:
            self._forget(self.meta_path())
        return existed


def _chat_file(chat_id: str) -> str:
    name = str(chat_id or "").strip().casefold()
    if not CHAT_ID.fullmatch(name):
        raise PolicyError("Chat memory needs the 32-character id of one conversation.")
    return name + ".md"


def goal_digest(text) -> str:
    """What the Goal section has to say, in one number. Compared, never shown."""
    return hashlib.sha256(clean_scalar(text).encode("utf-8")).hexdigest() if str(text or "").strip() else ""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise PolicyError("Could not read the memory file: " + str(exc)[:160]) from None


def _parse(text: str, layer: str) -> MemoryDoc:
    """Sections out of markdown, tolerant of the way a person actually edits a file.

    A heading this store does not know is kept rather than dropped, and so is its body: the point of
    storing memory as markdown is that it can be opened and typed in, and a reader that quietly
    discarded a section somebody added by hand would delete their note the next time anything was
    recorded. The rest is the simple grammar — a heading, then bullets or a paragraph.
    """
    doc = MemoryDoc(layer)
    known = {HEADING[name].casefold(): name for name in ORDER[layer]}
    current = None
    stray = None
    for line in (text or "").splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            stray = None
            current = known.get(name.casefold())
            if current is None and name.casefold() not in {"project memory", "chat memory"}:
                stray = Extra(name)
                doc.extra.append(stray)
            continue
        if line.startswith("# ") or line.startswith("<!--"):
            current = None
            stray = None
            continue
        if stray is not None:
            if line.strip():
                stray.lines.append(line.rstrip())
            continue
        if current is None or not line.strip():
            continue
        body = line.strip()
        if SHAPE[current] == "list":
            if body.startswith(("-", "*", "•")):
                body = body.lstrip("-*• ").strip()
            if not body or body.startswith("<!--"):
                continue
            items = doc.fields.setdefault(current, [])
            if body not in items:
                items.append(body[:ITEM_CHARS])
        else:
            if body.startswith("<!--"):
                continue
            doc.fields[current] = (str(doc.fields.get(current, "")) + " " + body).strip()[:SCALAR_CHARS]
    for name, value in list(doc.fields.items()):
        if SHAPE[name] == "scalar":
            doc.fields[name] = re.sub(r"\s+", " ", str(value)).strip()[:SCALAR_CHARS]
        else:
            doc.fields[name] = [item for item in value if item][:CAPS.get(name, 12)]
    return doc


def _render(doc: MemoryDoc) -> str:
    """Markdown for a document: known sections in their order, then whatever a hand added."""
    parts = []
    for name in ORDER[doc.layer]:
        if SHAPE[name] == "scalar":
            value = doc.text(name)
            if value:
                parts.append("## " + HEADING[name] + "\n" + value + "\n")
            continue
        items = [item for item in doc.items(name) if item]
        if items:
            parts.append("## " + HEADING[name] + "\n"
                         + "\n".join("- " + item for item in items) + "\n")
    for extra in doc.extra:
        body = [line for line in extra.lines if line.strip()]
        if body:
            parts.append("## " + extra.heading + "\n" + "\n".join(body) + "\n")
    return ("\n" + "\n".join(parts)) if parts else ""
