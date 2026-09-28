"""Scratch benchmark for the review: how does symbols.dependencies() scale?

Delete after the review is written.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import symbols  # noqa: E402


def rows(n, depth=6, imports=10):
    """Each row lives in its own deep package and imports ten siblings, so most of the
    `_linked` comparisons get past the cheap length/first-segment tests and do real work."""
    out = []
    for i in range(n):
        mine = ".".join(["com", "acme", "app", "m%03d" % i] +
                        ["sub%d" % (j % 4) for j in range(depth)])
        out.append({
            "path": "src/main/java/com/acme/Type%03d.java" % i,
            "kind": "java",
            "types": [{"name": "Type%03d" % i, "kind": "class",
                       "methods": ["m%d" % j for j in range(6)], "bases": []}],
            "functions": [],
            "imports": ["%s.Type%03d" % (".".join(["com", "acme", "app", "m%03d" % (k % n)] +
                                                  ["sub%d" % (j % 4) for j in range(depth)]),
                                         k % n) for k in range(imports)],
        })
    return out


for n in (20, 40, 80, 160, 300):
    started = time.perf_counter()
    edges = symbols.dependencies(rows(n))
    took = time.perf_counter() - started
    print("%4d deep-package files -> %8.2f s  (%d edges)" % (n, took, sum(len(v) for v in edges.values())))
    if took > 120:
        print("     stopping here; the curve is already clear")
        break
