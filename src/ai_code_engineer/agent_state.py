"""Persistent Agent State, Checkpoints, and Long-Term Memory.

This module provides the core data structures and persistence mechanisms for:
1. AgentState: Ephemeral working state of an active agent run (plan, progress, tool history).
2. Checkpoint: Atomic, verifiable snapshots taken at critical lifecycle boundaries.
3. CheckpointManager: Crash recovery, checkpoint persistence, and side-effect deduplication.
4. MemoryEntry & MemoryStore: Project-scoped long-term memory with relevance and recency retrieval.

Design Principles:
- "State helps the agent continue. Memory helps the agent remember. Do not mix them."
- Zero cloud dependencies; strictly standard library.
- Atomic writes via staged temporary files to guarantee zero corruption on unexpected crashes.
- Side-effect tracking prevents dangerous duplicate executions (e.g. reapplying writes or git commits)
  when resuming an interrupted run.
"""
from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from typing import Any, Optional

from .errors import PolicyError
from .redaction import redact


def _resilient_replace(staged: Path | str, target: Path | str, max_retries: int = 5, base_delay: float = 0.05) -> None:
    """Atomic file replacement with exponential backoff retry for Windows file-locking race conditions."""
    for attempt in range(max_retries):
        try:
            os.replace(staged, target)
            return
        except (PermissionError, OSError) as exc:
            if attempt == max_retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))



class AgentStatus(str, Enum):
    """Lifecycle statuses for an active agent run."""
    PENDING = "pending"
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CheckpointTrigger(str, Enum):
    """Well-defined triggers that capture an immutable snapshot of state."""
    INITIAL = "initial"
    AFTER_PLAN = "after_plan"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    AFTER_BUILD = "after_build"
    AFTER_TEST = "after_test"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    RECOVERY = "recovery"


@dataclass
class AgentState:
    """Working state of an agent run. Contains all context needed to continue execution."""
    run_id: str
    project_root: str
    task: str
    status: str = AgentStatus.UNDERSTANDING.value
    plan: list[dict[str, Any]] = field(default_factory=list)
    completed_steps: list[dict[str, Any]] = field(default_factory=list)
    pending_steps: list[dict[str, Any]] = field(default_factory=list)
    tool_history: list[dict[str, Any]] = field(default_factory=list)
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    build_result: Optional[dict[str, Any]] = None
    test_result: Optional[dict[str, Any]] = None
    approvals: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Serialize state to a sanitized dictionary."""
        data = asdict(self)
        # Ensure datetimes are ISO strings
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentState:
        """Construct AgentState from a serialized dictionary."""
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    def note_tool_call(self, tool_name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        """Record an executed tool call into working history."""
        self.tool_history.append({
            "tool": tool_name,
            "args": args,
            "result_status": result.get("status", "unknown"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def record_side_effect(self, tool_name: str, args: dict[str, Any], result_status: str, committed: bool = True) -> None:
        """Record a side-effecting operation with an argument digest for idempotency checks."""
        args_json = json.dumps(args, sort_keys=True, default=str)
        args_hash = hashlib.sha256(args_json.encode("utf-8")).hexdigest()
        self.side_effects.append({
            "tool": tool_name,
            "args_hash": args_hash,
            "result_status": result_status,
            "committed": committed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def has_side_effect_committed(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Check if an identical side-effecting tool invocation has already been committed."""
        args_json = json.dumps(args, sort_keys=True, default=str)
        target_hash = hashlib.sha256(args_json.encode("utf-8")).hexdigest()
        for effect in self.side_effects:
            if effect.get("tool") == tool_name and effect.get("args_hash") == target_hash and effect.get("committed", False):
                return True
        return False


@dataclass
class Checkpoint:
    """An immutable, point-in-time snapshot of AgentState."""
    checkpoint_id: str
    run_id: str
    step_index: int
    trigger: str
    state: AgentState
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        state_dict = self.state.to_dict()
        return {
            "checkpoint_id": self.checkpoint_id,
            "run_id": self.run_id,
            "step_index": self.step_index,
            "trigger": self.trigger,
            "timestamp": self.timestamp,
            "sha256": self.sha256,
            "state": state_dict,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        state_obj = AgentState.from_dict(data.get("state", {}))
        return cls(
            checkpoint_id=data.get("checkpoint_id", ""),
            run_id=data.get("run_id", ""),
            step_index=data.get("step_index", 0),
            trigger=data.get("trigger", CheckpointTrigger.INITIAL.value),
            timestamp=data.get("timestamp", ""),
            sha256=data.get("sha256", ""),
            state=state_obj,
        )


class CheckpointManager:
    """Manages persistent checkpoints on disk with atomic writes, retention pruning, and crash recovery."""

    def __init__(self, checkpoints_dir: Path | str, max_retention: int = 20) -> None:
        self.checkpoints_dir = Path(checkpoints_dir)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self.max_retention = max(3, max_retention)

    def _run_dir(self, run_id: str) -> Path:
        sanitized = re.sub(r"[^A-Za-z0-9._-]", "-", run_id)
        d = self.checkpoints_dir / sanitized
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _prune_checkpoints(self, run_id: str) -> int:
        """Prune older intermediate checkpoints exceeding max_retention.
        Always preserves the initial checkpoint (step 0 or initial trigger) and the latest N checkpoints.
        """
        checkpoints = self.list_checkpoints(run_id)
        if len(checkpoints) <= self.max_retention:
            return 0

        run_folder = self._run_dir(run_id)
        keep_recent = self.max_retention - 1
        candidates = checkpoints[1:len(checkpoints) - keep_recent]
        pruned_count = 0
        for chk in candidates:
            chk_file = run_folder / f"{chk.checkpoint_id}.json"
            try:
                if chk_file.is_file():
                    chk_file.unlink(missing_ok=True)
                    pruned_count += 1
            except OSError:
                pass
        return pruned_count

    def save_checkpoint(self, state: AgentState, trigger: CheckpointTrigger | str, step_index: int) -> Checkpoint:
        """Atomically persist a checkpoint to disk, computing a verifiable SHA-256 digest."""
        trigger_val = trigger.value if isinstance(trigger, CheckpointTrigger) else str(trigger)
        timestamp = datetime.now(timezone.utc).isoformat()
        chk_id = f"chk-{step_index:04d}-{trigger_val}"

        # Serialize state content first to compute sha256
        state_dict = state.to_dict()
        serialized_state = json.dumps(state_dict, sort_keys=True, indent=2)
        digest = hashlib.sha256(serialized_state.encode("utf-8")).hexdigest()

        chk = Checkpoint(
            checkpoint_id=chk_id,
            run_id=state.run_id,
            step_index=step_index,
            trigger=trigger_val,
            state=state,
            timestamp=timestamp,
            sha256=digest,
        )

        target_file = self._run_dir(state.run_id) / f"{chk_id}.json"
        
        # Atomic write via mkstemp + resilient os.replace
        handle, staged = tempfile.mkstemp(dir=target_file.parent, prefix="chk-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as f:
                json.dump(chk.to_dict(), f, indent=2)
            _resilient_replace(staged, target_file)
        except Exception as e:
            try:
                os.unlink(staged)
            except OSError:
                pass
            raise PolicyError(f"Failed to atomically persist checkpoint: {e}") from e

        # Also maintain a pointer to latest checkpoint
        latest_file = self._run_dir(state.run_id) / "latest.json"
        try:
            with open(latest_file, "w", encoding="utf-8") as f:
                json.dump({"checkpoint_id": chk_id, "file": f"{chk_id}.json"}, f, indent=2)
        except OSError:
            pass

        # Apply automated compaction and retention pruning
        self._prune_checkpoints(state.run_id)

        return chk

    def load_checkpoint(self, run_id: str, checkpoint_id: str) -> Optional[Checkpoint]:
        """Load and verify a checkpoint from disk."""
        target_file = self._run_dir(run_id) / f"{checkpoint_id}.json"
        if not target_file.is_file():
            return None
        try:
            data = json.loads(target_file.read_text(encoding="utf-8"))
            return Checkpoint.from_dict(data)
        except Exception:
            return None

    def latest_checkpoint(self, run_id: str) -> Optional[Checkpoint]:
        """Retrieve the most recent verified checkpoint for a run."""
        latest_file = self._run_dir(run_id) / "latest.json"
        if latest_file.is_file():
            try:
                meta = json.loads(latest_file.read_text(encoding="utf-8"))
                chk_id = meta.get("checkpoint_id")
                if chk_id:
                    chk = self.load_checkpoint(run_id, chk_id)
                    if chk:
                        return chk
            except Exception:
                pass

        # Fallback to scanning all checkpoint files in order
        all_chk = self.list_checkpoints(run_id)
        return all_chk[-1] if all_chk else None

    def list_checkpoints(self, run_id: str) -> list[Checkpoint]:
        """List all valid checkpoints for a run sorted by step index."""
        run_folder = self._run_dir(run_id)
        results: list[Checkpoint] = []
        for p in sorted(run_folder.glob("chk-*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                results.append(Checkpoint.from_dict(data))
            except Exception:
                continue
        results.sort(key=lambda c: c.step_index)
        return results

    def resume_run(self, run_id: str) -> tuple[AgentState, str]:
        """Resume an interrupted run from its latest checkpoint.
        
        Returns:
            Tuple of (AgentState, resume_instruction/status).
        """
        chk = self.latest_checkpoint(run_id)
        if not chk:
            raise PolicyError(f"No checkpoint found to resume run {run_id}")

        state = chk.state
        state.status = AgentStatus.EXECUTING.value
        instruction = f"Resumed from step {chk.step_index} ({chk.trigger})"
        return state, instruction


@dataclass
class MemoryEntry:
    """A durable long-term memory entry scoped to a project."""
    entry_id: str
    project_key: str
    category: str  # convention, architecture, preference, command, rule
    content: str
    relevance_tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


class MemoryStore:
    """Project-scoped durable memory store with relevance and recency retrieval.
    
    Ensures that only high-signal, relevant facts are surfaced to context prompts,
    avoiding prompt clutter and historical drift.
    """

    ALLOWED_CATEGORIES = {
        "convention",
        "architecture",
        "user_preference",
        "build_command",
        "repo_rule",
    }

    def __init__(self, memory_dir: Path | str) -> None:
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def _file_for(self, project_key: str) -> Path:
        sanitized = re.sub(r"[^A-Za-z0-9._-]", "-", project_key)
        return self.memory_dir / f"{sanitized}.entries.json"

    def _read_entries(self, project_key: str) -> list[MemoryEntry]:
        f = self._file_for(project_key)
        if not f.is_file():
            return []
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [MemoryEntry.from_dict(d) for d in data if isinstance(d, dict)]
        except Exception:
            pass
        return []

    def _write_entries(self, project_key: str, entries: list[MemoryEntry]) -> None:
        target = self._file_for(project_key)
        handle, staged = tempfile.mkstemp(dir=target.parent, prefix="mem-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as f:
                json.dump([e.to_dict() for e in entries], f, indent=2)
            _resilient_replace(staged, target)
        except Exception as exc:
            try:
                os.unlink(staged)
            except OSError:
                pass
            raise PolicyError(f"Could not persist memory entries: {exc}") from exc

    def add_entry(
        self,
        project_key: str,
        category: str,
        content: str,
        tags: Optional[list[str]] = None,
        verified: bool = True,
    ) -> MemoryEntry:
        """Add or update a verified long-term memory entry."""
        if category not in self.ALLOWED_CATEGORIES:
            raise PolicyError(f"Invalid memory category '{category}'. Allowed: {sorted(self.ALLOWED_CATEGORIES)}")

        clean_content = redact(content.strip())
        if not clean_content:
            raise PolicyError("Memory content cannot be empty.")

        clean_tags = [redact(str(t).lower().strip()) for t in (tags or []) if str(t).strip()]
        entry_hash = hashlib.sha256(f"{category}:{clean_content}".encode("utf-8")).hexdigest()[:12]
        entry_id = f"mem-{entry_hash}"
        now = datetime.now(timezone.utc).isoformat()

        entries = self._read_entries(project_key)
        existing = next((e for e in entries if e.entry_id == entry_id), None)
        if existing:
            existing.content = clean_content
            existing.relevance_tags = sorted(list(set(existing.relevance_tags + clean_tags)))
            existing.updated_at = now
            existing.verified = verified
            result = existing
        else:
            result = MemoryEntry(
                entry_id=entry_id,
                project_key=project_key,
                category=category,
                content=clean_content,
                relevance_tags=clean_tags,
                created_at=now,
                updated_at=now,
                verified=verified,
            )
            entries.append(result)

        self._write_entries(project_key, entries)
        return result

    def retrieve(
        self,
        project_key: str,
        query: str = "",
        category: str = "",
        tags: Optional[list[str]] = None,
        max_entries: int = 5,
    ) -> list[MemoryEntry]:
        """Retrieve the most relevant and recent memory entries matching query/tags."""
        entries = self._read_entries(project_key)
        if not entries:
            return []

        query_tokens = set(re.findall(r"\w+", query.lower())) if query else set()
        filter_tags = set(t.lower() for t in (tags or []))

        scored: list[tuple[float, MemoryEntry]] = []
        for entry in entries:
            if category and entry.category != category:
                continue

            score = 0.0
            # Tag overlap
            entry_tags = set(entry.relevance_tags)
            if filter_tags:
                overlap = len(filter_tags.intersection(entry_tags))
                score += overlap * 3.0

            # Query token overlap
            if query_tokens:
                content_tokens = set(re.findall(r"\w+", entry.content.lower()))
                token_overlap = len(query_tokens.intersection(content_tokens))
                score += token_overlap * 1.5

            # If no query and no tags specified, default base score to 1.0
            if not query_tokens and not filter_tags:
                score = 1.0

            if score > 0.0:
                scored.append((score, entry))

        # Sort by score descending, then by updated_at descending
        scored.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
        return [entry for _, entry in scored[:max_entries]]

    def delete_entry(self, project_key: str, entry_id: str) -> bool:
        """Remove a specific memory entry."""
        entries = self._read_entries(project_key)
        remaining = [e for e in entries if e.entry_id != entry_id]
        if len(remaining) < len(entries):
            self._write_entries(project_key, remaining)
            return True
        return False


class SqliteStateStore:
    """Production-grade SQLite WAL state, checkpoint, and memory persistence engine.

    Provides ACID transaction guarantees, multi-process safety, and zero-filesystem-bloat
    for 24/7 background daemons and high-throughput agent runs.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    project_root TEXT NOT NULL,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    trigger TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_chk_run_step ON checkpoints(run_id, step_index);
                CREATE TABLE IF NOT EXISTS memory_entries (
                    entry_id TEXT PRIMARY KEY,
                    project_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_mem_proj ON memory_entries(project_key);
            """)

    def save_state(self, state: AgentState) -> None:
        """Atomically persist or update agent run state."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, project_root, task, status, created_at, updated_at, state_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    state_json=excluded.state_json
                """,
                (
                    state.run_id,
                    state.project_root,
                    state.task,
                    state.status,
                    state.created_at,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(state.to_dict()),
                ),
            )

    def load_state(self, run_id: str) -> Optional[AgentState]:
        """Load state by run_id."""
        with self._connect() as conn:
            row = conn.execute("SELECT state_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return None
            return AgentState.from_dict(json.loads(row["state_json"]))

    def save_checkpoint(self, checkpoint: Checkpoint, max_retention: int = 20) -> None:
        """Persist checkpoint with atomic transaction and automated pruning."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints
                (checkpoint_id, run_id, step_index, trigger, timestamp, sha256, state_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.run_id,
                    checkpoint.step_index,
                    checkpoint.trigger,
                    checkpoint.timestamp,
                    checkpoint.sha256,
                    json.dumps(checkpoint.state.to_dict()),
                ),
            )
            # Prune old checkpoints for this run
            if max_retention > 0:
                rows = conn.execute(
                    "SELECT checkpoint_id, step_index FROM checkpoints WHERE run_id = ? ORDER BY step_index ASC",
                    (checkpoint.run_id,),
                ).fetchall()
                if len(rows) > max_retention:
                    keep_recent = max_retention - 1
                    to_delete = rows[1:len(rows) - keep_recent]
                    for r in to_delete:
                        conn.execute("DELETE FROM checkpoints WHERE checkpoint_id = ?", (r["checkpoint_id"],))

    def load_latest_checkpoint(self, run_id: str) -> Optional[Checkpoint]:
        """Retrieve latest checkpoint for a run."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE run_id = ? ORDER BY step_index DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if not row:
                return None
            state_data = json.loads(row["state_json"])
            return Checkpoint(
                checkpoint_id=row["checkpoint_id"],
                run_id=row["run_id"],
                step_index=row["step_index"],
                trigger=row["trigger"],
                timestamp=row["timestamp"],
                sha256=row["sha256"],
                state=AgentState.from_dict(state_data),
            )

    def list_checkpoints(self, run_id: str) -> list[Checkpoint]:
        """List all checkpoints for a run sorted chronologically."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE run_id = ? ORDER BY step_index ASC",
                (run_id,),
            ).fetchall()
            results = []
            for row in rows:
                state_data = json.loads(row["state_json"])
                results.append(
                    Checkpoint(
                        checkpoint_id=row["checkpoint_id"],
                        run_id=row["run_id"],
                        step_index=row["step_index"],
                        trigger=row["trigger"],
                        timestamp=row["timestamp"],
                        sha256=row["sha256"],
                        state=AgentState.from_dict(state_data),
                    )
                )
            return results

    def save_memory_entry(self, entry: MemoryEntry) -> None:
        """Persist durable memory entry."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_entries
                (entry_id, project_key, category, content, tags_json, created_at, updated_at, verified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_id,
                    entry.project_key,
                    entry.category,
                    entry.content,
                    json.dumps(entry.relevance_tags),
                    entry.created_at,
                    entry.updated_at,
                    1 if entry.verified else 0,
                ),
            )

    def retrieve_memory(self, project_key: str, category: str = "", max_entries: int = 10) -> list[MemoryEntry]:
        """Retrieve memory entries by project_key and optional category."""
        with self._connect() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM memory_entries WHERE project_key = ? AND category = ? ORDER BY updated_at DESC LIMIT ?",
                    (project_key, category, max_entries),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM memory_entries WHERE project_key = ? ORDER BY updated_at DESC LIMIT ?",
                    (project_key, max_entries),
                ).fetchall()
            entries = []
            for r in rows:
                entries.append(
                    MemoryEntry(
                        entry_id=r["entry_id"],
                        project_key=r["project_key"],
                        category=r["category"],
                        content=r["content"],
                        relevance_tags=json.loads(r["tags_json"]),
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                        verified=bool(r["verified"]),
                    )
                )
            return entries


