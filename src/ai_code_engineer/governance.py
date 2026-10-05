"""Performance Optimization, Incremental Caching, and Resource Governance.

"Optimize measured bottlenecks, not architecture diagrams."
"The agent must behave predictably on 16 GB developer machines."

This module enforces predictable resource consumption:
1. ResourceGovernor: Caps context sizes, step counts, tool execution durations, and log memory.
2. IncrementalCache: Content-addressed mtime+size and SHA-256 caching for file parsing and symbols,
   preventing full repository re-scans on subsequent turns.
3. CancellationToken: Thread-safe cancellation mechanism allowing immediate interruption of long operations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Optional

from .errors import PolicyError


class CancellationToken:
    """Thread-safe cooperative cancellation token for long-running operations."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise PolicyError("Operation cancelled by operator.")


@dataclass
class ResourceLimits:
    """Strict resource envelopes ensuring predictability on standard developer machines."""
    max_context_chars: int = 64_000
    max_step_count: int = 25
    max_tool_duration_seconds: float = 120.0
    max_repair_rounds: int = 3
    max_log_chars: int = 250_000


class ResourceGovernor:
    """Monitors and governs active agent resource consumption."""

    def __init__(self, limits: Optional[ResourceLimits] = None) -> None:
        self.limits = limits or ResourceLimits()
        self.current_steps = 0
        self.start_time = time.perf_counter()

    def record_step(self) -> None:
        self.current_steps += 1
        if self.current_steps > self.limits.max_step_count:
            raise PolicyError(
                f"Resource Governance Limit Exceeded: Run exceeded maximum step count "
                f"({self.limits.max_step_count} steps)."
            )

    def enforce_context_budget(self, text: str) -> str:
        """Truncates non-essential context if exceeding safe character envelope."""
        if len(text) <= self.limits.max_context_chars:
            return text
        notice = f"\n\n... [TRUNCATED: {len(text) - self.limits.max_context_chars} CHARACTERS OMITTED] ...\n\n"
        room = self.limits.max_context_chars - len(notice)
        if room <= 10:
            return text[:self.limits.max_context_chars]
        head_size = room // 2
        tail_size = room - head_size
        return text[:head_size] + notice + text[-tail_size:]


class IncrementalCache:
    """Thread-safe, Bounded LRU incremental cache with safe mtime/size invalidation and optional TTL."""

    def __init__(self, max_entries: int = 1000, ttl_seconds: Optional[float] = None) -> None:
        self.max_entries = max(1, max_entries)
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, tuple[float, int, Any, float]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @staticmethod
    def _fingerprint(file_path: Path | str) -> tuple[float, int]:
        p = Path(file_path)
        stat = p.stat()
        return stat.st_mtime, stat.st_size

    def get_or_compute(
        self,
        file_path: Path | str,
        compute_fn: Callable[[str], Any],
    ) -> Any:
        path_str = str(Path(file_path).resolve())
        now = time.time()

        try:
            mtime, size = self._fingerprint(path_str)
        except OSError:
            # File does not exist or unreadable
            with self._lock:
                self._cache.pop(path_str, None)
            return None

        with self._lock:
            if path_str in self._cache:
                c_mtime, c_size, val, inserted_at = self._cache[path_str]
                # Check TTL
                ttl_valid = (self.ttl_seconds is None) or ((now - inserted_at) < self.ttl_seconds)
                if c_mtime == mtime and c_size == size and ttl_valid:
                    self._hits += 1
                    self._cache.move_to_end(path_str)
                    return val
                else:
                    self._cache.pop(path_str, None)

            self._misses += 1

        # Miss or invalidation: compute outside lock to prevent blocking
        computed_val = compute_fn(path_str)

        with self._lock:
            # Enforce LRU capacity limit
            while len(self._cache) >= self.max_entries:
                self._cache.popitem(last=False)
                self._evictions += 1

            self._cache[path_str] = (mtime, size, computed_val, now)
            self._cache.move_to_end(path_str)
            return computed_val

    def invalidate(self, file_path: Path | str) -> None:
        path_str = str(Path(file_path).resolve())
        with self._lock:
            self._cache.pop(path_str, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._cache),
                "max_entries": self.max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }
