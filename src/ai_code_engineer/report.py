"""One session's record, handed over as something usable outside this tool.

The session file is already the complete ledger — `.agent-runs/<id>/session.json` keeps the task text
verbatim, the model that answered, every tool call with the digest of what was read, the proposal hash,
what was written, each command run with its exit code and test counts, and every refusal with its
reason. What it is not is *readable*: it is 200 KB of interleaved events, and the interesting question
("what did this task actually do, and why did it stop") requires walking them in order.

Two rules the exporters keep:

- **Redact at the boundary.** The stored record is redacted where it was captured, and this re-redacts
  anything that came from a command's output. A report is the one artefact that leaves the machine —
  it gets pasted into an issue, mailed to a teammate, attached to a PR — so the guarantee cannot be
  "the log happened to be clean".
- **Say nothing that was not recorded.** No estimated token counts, no inferred cause. Where the record
  is silent — a run that produced no test evidence, a stage that never emitted a line — the report says
  that, in the same words the windows use.
"""
from __future__ import annotations

import json
from pathlib import Path

from .engine import load_session
from .errors import AgentError
from .repair import classify
from .runner import run_folder
from .labels import STATES, state_label
from .redaction import redact

# Events that mark a stage a person asks about later. Anything else is bookkeeping.
STAGE_KINDS = ("plan_attached", "memory_attached", "proposal", "approved", "written", "removed",
               "run", "verification", "rolled_back", "rolled_back_file", "stopped",
               "rejected_action", "blocked_retried", "file_not_found", "step", "tool")
# The kinds that answer "what did it read" — the model's own tool calls and the reads the loop did
# for it, which are different acts and stay listed apart.
READ_KINDS = ("tool", "auto_read")


def find_session(runs: Path, ident: str) -> Path:
    """The session file for an id, a prefix, or a path to the folder or the file itself.

    Ids are 32 hex characters, which nobody types. A unique prefix is accepted, an ambiguous one is
    refused with the count, because guessing which task a person meant is the one thing an audit tool
    must not do.
    """
    runs = Path(runs)
    direct = Path(ident)
    if direct.is_file():
        return direct
    if direct.is_dir() and (direct / "session.json").is_file():
        return direct / "session.json"
    wanted = ident.lower().removeprefix("runs/").removesuffix("/").removesuffix("/session.json")
    if not wanted:
        raise AgentError("Name a session by its id, its id prefix, or its folder.")
    matches = sorted(path for path in runs.glob("*/session.json")
                     if path.parent.name.lower().startswith(wanted))
    if not matches:
        raise AgentError("No session starts with " + wanted + " in " + str(runs))
    if len(matches) > 1:
        raise AgentError(str(len(matches)) + " sessions start with " + wanted +
                         " — give more characters.")
    return matches[0]


def _clock(session: dict) -> list[dict]:
    """The event list, in recorded order, each with the seconds since the one before it."""
    from datetime import datetime, timezone

    previous = None
    rows = []
    for entry in session.get("events") or []:
        at = entry.get("at") or ""
        try:
            stamp = datetime.fromisoformat(at)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except ValueError:
            stamp = None
        gap = None
        if stamp is not None:
            if previous is not None:
                gap = round((stamp - previous).total_seconds(), 2)
            previous = stamp
        # The detail is the event's own fields, and some of them are text a build tool or a model
        # wrote: `rejected_action` carries the refusal reason, `stopped` carries an error string. A
        # report is the artefact that leaves this machine, so it is redacted here as well as there.
        detail = {key: (_clean(redact(str(value)))[:200] if isinstance(value, str) else value)
                  for key, value in entry.items() if key not in {"at", "kind"}}
        rows.append({"at": at, "kind": entry.get("kind", ""), "seconds_since_previous": gap,
                     "detail": detail})
    return rows


def reads(session: dict) -> dict:
    """What was opened, with the digest that identifies which version of it."""
    asked, automatic = [], []
    for entry in session.get("events") or []:
        if entry.get("kind") not in READ_KINDS:
            continue
        row = {"path": entry.get("path", ""), "sha256": entry.get("sha256", "")}
        if entry.get("name") == "list_files" or "count" in entry:
            row["listed"] = entry.get("count")
        if "matches" in entry:
            row["search_matches"] = entry.get("matches")
        if row["path"]:
            (automatic if entry["kind"] == "auto_read" else asked).append(row)
    return {"by_model": asked, "by_tool": automatic,
            "distinct_files": len({row["path"] for row in asked + automatic if row["path"]})}


def refusals(session: dict) -> list[dict]:
    """Every action the tool declined, and the sentence that says why.

    A report that lists only the successes is how a small model's flailing reads as progress. These are
    the rows that make the difference between "three rounds" and "three rounds, two of them rejected".
    """
    out = []
    for entry in session.get("events") or []:
        kind = entry.get("kind")
        if kind == "rejected_action":
            out.append({"at": entry.get("at", ""), "what": "action", "why": entry.get("reason", "")})
        elif kind == "blocked_retried":
            out.append({"at": entry.get("at", ""), "what": "blocked answer",
                        "why": "retried once (attempt " + str(entry.get("attempt", "")) + ")"})
        elif kind == "file_not_found":
            out.append({"at": entry.get("at", ""), "what": "read " + str(entry.get("path", "")),
                        "why": "not found" + ("" if entry.get("can_create") else " (creation protected)")})
        elif kind == "stopped":
            out.append({"at": entry.get("at", ""), "what": "the task",
                        "why": entry.get("reason") or "the record does not say why it stopped"})
    return [{"at": row["at"], "what": _clean(redact(row["what"]))[:200],
             "why": _clean(redact(row["why"]))[:200]} for row in out]


def runs_of(session: dict) -> list[dict]:
    """Each command, as stored: what ran, what it returned, and how much proof it produced."""
    rows = []
    for run in session.get("runs") or []:
        proof = run.get("proof") or {}
        rows.append({"recipe": run.get("recipe", ""), "label": run.get("label", ""),
                     # Which module of a multi-project folder ran, "" when it was the folder itself.
                     "folder": run_folder(run),
                    "command": run.get("command", ""), "status": run.get("status", ""),
                    "exit_code": run.get("exit_code"), "seconds": run.get("seconds"),
                    "tests": proof.get("tests") or 0, "failures": proof.get("failures") or 0,
                    "errors": proof.get("errors") or 0, "proof_source": proof.get("source", ""),
                    # The same reading the loop acts on, so the report cannot tell a different story
                    # about a run the agent already classified.
                    "looks_like": classify(run),
                    "truncated": bool(run.get("truncated")), "timed_out": bool(run.get("timed_out")),
                    "reported_problems": [_clean(redact(str(line))[:200])
                                          for line in (run.get("failures") or [])[:12]],
                    "end_of_output": _clean(redact(str(run.get("tail") or ""))[-1200:],
                                            keep_newlines=True)})
    return rows


def summary(session: dict) -> dict:
    """The machine-readable export: the record plus what only a walk over it can tell you."""
    state = session.get("state", "")
    return {
        "id": session.get("id", ""),
        "project": session.get("root", ""),
        "chat": session.get("chat_id", ""),
        "created": session.get("created", ""),
        "state": state,
        "state_label": state_label(state) if state in STATES else state,
        "model": session.get("model", ""),
        "request": session.get("task", ""),
        "plan": ({"file": session["plan_reference"]["path"], "step": session.get("plan_step")}
                 if session.get("plan_reference") else None),
        "project_notes_characters": len(session.get("memory") or ""),
        "proposal": {"hash": session.get("proposal_hash", ""),
                     "summary": redact(session.get("summary") or ""),
                     "files": [{"path": change.get("path", ""),
                                "delete": bool(change.get("delete")),
                                "bytes_before": len(change.get("before") or ""),
                                "bytes_after": len(change.get("after") or "")}
                               for change in session.get("changes") or []]},
        "checks": list(session.get("checks") or []),
        "written": [entry.get("path", "") for entry in session.get("events") or []
                    if entry.get("kind") == "written"],
        "removed": [entry.get("path", "") for entry in session.get("events") or []
                    if entry.get("kind") == "removed"],
        "rolled_back": [entry.get("path", "") for entry in session.get("events") or []
                        if entry.get("kind") == "rolled_back_file"],
        "verification": session.get("verification") or None,
        "error": _clean(redact(session.get("error", "")))[:300],
        "runs": runs_of(session),
        "reads": reads(session),
        "refusals": refusals(session),
        "timeline": _clock(session),
        "fix_rounds": sum(1 for entry in session.get("events") or []
                          if entry.get("kind") == "run"),
    }


def _clean(value: str, *, keep_newlines: bool = False) -> str:
    """Strip the terminal's own vocabulary from text a build tool wrote.

    A build log can contain `ESC[2J` or a bell, and a report is read in a browser, pasted into an
    issue and opened in editors. `cli.safe_print` already does this to the screen; the file the user
    asks for with `--out` never passes through the screen, so it is cleaned where it is assembled.
    """
    text = str(value)
    allowed = "\n\t" if keep_newlines else ""
    return "".join(char for char in text
                   if char in allowed or (ord(char) >= 32 and not 127 <= ord(char) <= 159))


def _line(value: str) -> str:
    """One line of a markdown table: no pipes, no newlines, no control characters."""
    flat = " ".join(_clean(value).split())
    return redact(flat.replace("|", "\\|"))[:160]


def render_markdown(session: dict) -> str:
    """The same facts as a page someone can read without this repository in front of them."""
    data = summary(session)
    parts = ["# Session " + data["id"][:12] + " — " + _line(Path(data["project"]).name or "?") + "\n\n"]
    parts.append("| | |\n| --- | --- |\n")
    for label, value in (("Project", data["project"]), ("State", data["state_label"]),
                         ("Model", data["model"]), ("Created", data["created"]),
                         ("Chat", data["chat"] or "—"),
                         ("Proposal hash", data["proposal"]["hash"] or "none"),
                         ("Files written", str(len(data["written"]))),
                         ("Files removed", str(len(data["removed"]))),
                         ("Command runs", str(len(data["runs"]))),
                         ("Refusals/retries", str(len(data["refusals"])))):
        parts.append("| " + label + " | `" + _line(value) + "` |\n")

    # The task text is what the window was given, character for character — that is the point of the
    # section, and the dogfood ledger exists because paraphrasing a prompt loses the mistake. It is
    # still cleaned of terminal control codes, because the person reading it did not write it.
    parts.append("\n## The request, verbatim\n\n````text\n"
                 + _clean(data["request"].rstrip("\n"), keep_newlines=True) + "\n````\n")
    if data["proposal"]["summary"]:
        parts.append("\n*The model's own summary:* " + _line(data["proposal"]["summary"]) + "\n")
    if data["plan"]:
        parts.append("\n*Attached plan:* `" + _line(data["plan"]["file"]) + "`"
                     + (", step " + str(data["plan"]["step"]) if data["plan"]["step"] else "") + "\n")

    if data["proposal"]["files"]:
        parts.append("\n## Proposed changes\n\n")
        parts.append("| file | action | before | after |\n| --- | --- | --- | --- |\n")
        for change in data["proposal"]["files"]:
            parts.append("| `{}` | {} | {} B | {} B |\n".format(
                _line(change["path"]), "remove" if change["delete"] else "write",
                change["bytes_before"], change["bytes_after"]))
        if data["checks"]:
            parts.append("\n*Checks the proposal asked for:* "
                         + "; ".join(_line(item) for item in data["checks"]) + "\n")

    parts.append("\n## Files read\n\n")
    parts.append("*By the model, " + str(len(data["reads"]["by_model"])) + " calls, "
                 + str(data["reads"]["distinct_files"]) + " distinct files:*\n")
    for row in data["reads"]["by_model"][:40]:
        digest = " — read as `" + row["sha256"][:8] + "`" if row["sha256"] else ""
        parts.append("- `" + _line(row["path"]) + "`" + digest + "\n")
    if data["reads"]["by_tool"]:
        parts.append("\n*Read by the tool on the model's behalf:* "
                     + ", ".join("`" + _line(row["path"]) + "`" for row in data["reads"]["by_tool"][:20])
                     + "\n")

    parts.append("\n## Command runs\n\n")
    if not data["runs"]:
        parts.append("No project command was run for this session. **Verification is incomplete** — "
                     "nothing here proves the change works beyond the static checks.\n")
    for index, run in enumerate(data["runs"], 1):
        counted = (str(run["tests"]) + " tests, " + str(run["failures"]) + " failed, "
                   + str(run["errors"]) + " errors"
                   if run["proof_source"] else "no test count was produced")
        parts.append("### {} — `{}`{} · {}\n\n".format(index, _line(run["label"] or run["recipe"]),
                                                       " in `" + run["folder"] + "`"
                                                       if run["folder"] else "", run["status"]))
        parts.append("- Command: `{}`\n".format(_line(run["command"])))
        parts.append("- Exit {} in {} s · {} · source: `{}`\n".format(
            run["exit_code"], run["seconds"], counted, _line(run["proof_source"] or "-")))
        if run["looks_like"]:
            parts.append("- What the output looks like: {}\n".format(
                "not a recognised kind of failure" if run["looks_like"] == "unknown"
                else run["looks_like"]))
        if run["timed_out"]:
            parts.append("- **Timed out.** The state after a killed run is unknown until it is re-run.\n")
        if run["truncated"]:
            parts.append("- Output was cut off: what is shown is not all the command printed.\n")
        if not run["tests"] and run["status"] == "passed":
            parts.append("- It reported success with **no test evidence**. That is recorded as "
                         "`unverified` elsewhere in this tool, and it is not a pass.\n")
        if run["reported_problems"]:
            parts.append("\n```text\n" + "\n".join(run["reported_problems"]) + "\n```\n")
        if run["end_of_output"]:
            parts.append("\n<details><summary>End of output</summary>\n\n```text\n"
                         + run["end_of_output"] + "\n```\n\n</details>\n")

    if data["verification"]:
        verification = data["verification"]
        parts.append("\n## Verification\n\n`" + _line(verification.get("status", "")) + "`")
        if verification.get("reason"):
            parts.append(" — " + _line(verification["reason"]))
        parts.append("\n")
        if verification.get("sandbox"):
            sandbox = verification["sandbox"]
            parts.append("- Sandbox: recipe `{}`, image `{}`, exit {}\n".format(
                _line(sandbox.get("recipe", "")), _line(str(sandbox.get("image", ""))[:70]),
                sandbox.get("exit_code")))
            parts.append("- Container ran with no network, read-only project mount and no secrets"
                         " in its environment.\n")

    if data["refusals"]:
        parts.append("\n## Refusals, retries and dead ends\n\n")
        parts.append("| when | what | why |\n| --- | --- | --- |\n")
        for row in data["refusals"]:
            parts.append("| {} | {} | {} |\n".format(_line(row["at"])[11:19], _line(row["what"]),
                                                     _line(row["why"])))

    parts.append("\n## Timeline\n\n")
    parts.append("| when | event | gap | detail |\n| --- | --- | --- | --- |\n")
    for row in data["timeline"]:
        if row["kind"] not in STAGE_KINDS:
            continue
        detail = ", ".join(_line(key) + "=" + _line(value)
                           for key, value in list(row["detail"].items())[:3])
        parts.append("| {} | `{}` | {} | {} |\n".format(_line(row["at"])[11:19], _line(row["kind"]),
                                                        row["seconds_since_previous"], detail))

    if data["error"]:
        parts.append("\n## Why it ended\n\n" + _line(data["error"]) + "\n")
    if data["rolled_back"]:
        parts.append("\n*Rolled back:* " + ", ".join("`" + _line(path) + "`"
                                                     for path in data["rolled_back"]) + "\n")
    parts.append("\n---\n\nGenerated from `.agent-runs/" + data["id"] + "/session.json` by "
                 "`agent export-session`. Command output in this file was redacted where it was "
                 "recorded and again here; it is still untrusted text from a project's build tool.\n")
    return "".join(parts)


def export(session: dict, fmt: str = "markdown") -> str:
    if fmt == "json":
        return json.dumps(summary(session), ensure_ascii=False, indent=2)
    if fmt == "markdown":
        return render_markdown(session)
    raise AgentError("Unknown export format: " + str(fmt) + " (json or markdown)")


def export_file(path: Path, fmt: str = "markdown") -> str:
    return export(load_session(Path(path)), fmt)
