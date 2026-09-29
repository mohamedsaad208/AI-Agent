"""Find string literals that ASI silently cut off from the line above them.

`x = 'a'\n  'b';` parses as two statements, not as one concatenation, so the sentence that reaches the
screen stops half way — and no syntax check complains, because the second line is a legal expression
statement. `node --check` cannot see this class at all.

A line is a suspect when it is only a string literal (optionally terminated) and the line before it
also ends on a string literal with no `+` or `,` after it. Usage: python tools/scan_asi_strings.py PATH…
"""
import re
import sys
from pathlib import Path

SOLO = re.compile(r"""^\s*(?P<quote>['"`])(?P<body>(?:\\.|[^\\])*?)(?P=quote)\s*;?\s*$""")
ENDS = re.compile(r"""['"`]\s*$""")


def scan(path: Path) -> list[str]:
    hits = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines[1:], start=2):
        m = SOLO.match(line)
        if not m or "+" in line or not m.group("body").strip():
            continue
        prev = lines[i - 2].rstrip()
        if ENDS.search(prev) and not prev.rstrip(";").endswith(("+", ",")):
            hits.append(f"{path.name}:{i}: {m.group('body')[:78]}")
    return hits


for name in sys.argv[1:]:
    for hit in scan(Path(name)):
        print(hit)
