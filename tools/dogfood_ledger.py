"""Build docs/DOGFOOD-PROMPTS.md: every prompt the ecommerce run sent, verbatim, in order.

The sessions on disk are the authoritative record (`.agent-runs/<id>/session.json` keeps the whole task
text), so this is a rendering of them, not a second copy kept by hand.
"""
import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(REPO, ".agent-runs")
ROOT = os.path.join(REPO, sys.argv[1] if len(sys.argv) > 1 else "ecommerce")
OUT = os.path.join(REPO, "docs", "DOGFOOD-PROMPTS.md")

rows = []
for path in glob.glob(os.path.join(RUNS, "*", "session.json")):
    try:
        with open(path, encoding="utf-8") as handle:
            session = json.load(handle)
    except (OSError, ValueError):
        continue
    if session.get("root") != ROOT:
        continue
    rows.append(session)

rows.sort(key=lambda s: s.get("created", ""))

parts = ["""# Every prompt the `ecommerce` run sent, verbatim

Rendered from `.agent-runs/<id>/session.json` on 2026-09-28 — the task text is what the window was
given, character for character, including the parts that were the operator's own mistake. The
narrative, the defects and the fixes live in `DOGFOOD-ECOMMERCE-RUN.md`; this file is the evidence
that the work was done by the tool and not by the person watching it.

Nothing here was written into `ecommerce` by hand. Each row is one task the agent planned, wrote and
committed itself; `state` is where that session ended, and `run` is the build the tool ran afterwards.

"""]

parts.append("| # | session | state | files | run | prompt starts with |\n")
parts.append("| --- | --- | --- | --- | --- | --- |\n")
for index, session in enumerate(rows, 1):
    changes = session.get("changes") or []
    runs = session.get("runs") or []
    run = runs[-1].get("status") if runs else "-"
    first = (session.get("task", "").splitlines() or [""])[0][:52]
    parts.append("| {} | `{}` | {} | {} | {} | {} |\n".format(
        index, session.get("id", "")[:12], session.get("state", ""), len(changes), run,
        first.replace("|", "\\|")))

parts.append("\n---\n\n")
for index, session in enumerate(rows, 1):
    runs = session.get("runs") or []
    parts.append("## {} — `{}` — {} ({} file(s), run: {})\n\n".format(
        index, session.get("id", "")[:12], session.get("created", "")[:19],
        len(session.get("changes") or []), (runs[-1].get("status") if runs else "none")))
    parts.append("````text\n")
    parts.append(session.get("task", "").rstrip("\n"))
    parts.append("\n````\n\n")
    if session.get("summary"):
        parts.append("*Model's summary:* " + session["summary"].strip() + "\n\n")
    for run in runs:
        parts.append("*Run:* {} — {} in {}s\n\n".format(run.get("label"), run.get("status"),
                                                        run.get("seconds")))

with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
    handle.write("".join(parts))

print("wrote", OUT, "tasks:", len(rows), "bytes:", os.path.getsize(OUT))
