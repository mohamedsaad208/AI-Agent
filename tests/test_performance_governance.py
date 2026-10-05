"""Unit tests for ResourceGovernor, IncrementalCache, and CancellationToken."""
from pathlib import Path
import tempfile
import time
import unittest

from ai_code_engineer.errors import PolicyError
from ai_code_engineer.governance import (
    CancellationToken,
    IncrementalCache,
    ResourceGovernor,
    ResourceLimits,
)


class GovernanceTests(unittest.TestCase):
    def test_cancellation_token(self):
        token = CancellationToken()
        self.assertFalse(token.is_cancelled)
        token.check()  # should not raise

        token.cancel()
        self.assertTrue(token.is_cancelled)
        with self.assertRaises(PolicyError):
            token.check()

    def test_resource_governor_step_limit(self):
        gov = ResourceGovernor(ResourceLimits(max_step_count=3))
        gov.record_step()  # 1
        gov.record_step()  # 2
        gov.record_step()  # 3
        with self.assertRaises(PolicyError):
            gov.record_step()  # 4 exceeds

    def test_resource_governor_context_budget(self):
        gov = ResourceGovernor(ResourceLimits(max_context_chars=100))
        short_text = "Short text under limit"
        self.assertEqual(gov.enforce_context_budget(short_text), short_text)

        long_text = "x" * 250
        truncated = gov.enforce_context_budget(long_text)
        self.assertIn("[TRUNCATED", truncated)
        self.assertLessEqual(len(truncated), 100)

    def test_incremental_cache(self):
        cache = IncrementalCache()
        with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as f:
            f.write("initial content")
            f_path = f.name

        try:
            calls = [0]

            def parse_fn(p):
                calls[0] += 1
                return Path(p).read_text(encoding="utf-8").upper()

            # First compute
            val1 = cache.get_or_compute(f_path, parse_fn)
            self.assertEqual(val1, "INITIAL CONTENT")
            self.assertEqual(calls[0], 1)

            # Second call without modifying file: cache hit!
            val2 = cache.get_or_compute(f_path, parse_fn)
            self.assertEqual(val2, "INITIAL CONTENT")
            self.assertEqual(calls[0], 1)  # No recomputation!

            # Modify file
            time.sleep(0.01)
            Path(f_path).write_text("modified content", encoding="utf-8")

            # Third call: cache miss, recomputes
            val3 = cache.get_or_compute(f_path, parse_fn)
            self.assertEqual(val3, "MODIFIED CONTENT")
            self.assertEqual(calls[0], 2)
        finally:
            Path(f_path).unlink(missing_ok=True)

    def test_incremental_cache_lru_eviction(self):
        cache = IncrementalCache(max_entries=2)
        files = []
        try:
            for i in range(3):
                f = tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8")
                f.write(f"file_{i}")
                f.close()
                files.append(f.name)

            # Insert file 0 and file 1
            cache.get_or_compute(files[0], lambda p: "A")
            cache.get_or_compute(files[1], lambda p: "B")
            self.assertEqual(cache.size, 2)

            # Insert file 2 -> should evict file 0 (oldest)
            cache.get_or_compute(files[2], lambda p: "C")
            self.assertEqual(cache.size, 2)
            stats = cache.stats()
            self.assertEqual(stats["evictions"], 1)

            # file 1 and 2 should still hit, file 0 is gone
            c_calls = [0]
            def counter(p):
                c_calls[0] += 1
                return "NEW"
            cache.get_or_compute(files[0], counter)
            self.assertEqual(c_calls[0], 1)  # was evicted so recomputed
        finally:
            for p in files:
                Path(p).unlink(missing_ok=True)

    def test_incremental_cache_ttl(self):
        cache = IncrementalCache(max_entries=10, ttl_seconds=0.05)
        with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as f:
            f.write("ttl_data")
            f_path = f.name
        try:
            calls = [0]
            def compute(p):
                calls[0] += 1
                return "DATA"

            v1 = cache.get_or_compute(f_path, compute)
            self.assertEqual(v1, "DATA")
            self.assertEqual(calls[0], 1)

            # Immediate second call -> hit
            v2 = cache.get_or_compute(f_path, compute)
            self.assertEqual(v2, "DATA")
            self.assertEqual(calls[0], 1)

            # Wait for TTL expiry
            time.sleep(0.06)
            v3 = cache.get_or_compute(f_path, compute)
            self.assertEqual(v3, "DATA")
            self.assertEqual(calls[0], 2)  # Recomputed because TTL expired!
        finally:
            Path(f_path).unlink(missing_ok=True)

