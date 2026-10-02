from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys

from .config import load_settings, validate
from .engine import apply_proposal, load_session, plan, reopen_proposal, review, rollback
from .errors import AgentError
from .labels import asked_of, is_arabic, policy_line, policy_verdicts
from .labels import note as shared_note
from .providers import make_provider
from .report import export_file, find_session
from . import config, intent, modes, overrides, permissions, policy, setup
from .verification import RECIPES, verify
from .workspace import Workspace


def safe_print(value: str) -> None:
    # Strip terminal control characters in untrusted model/repository content.
    value = "".join(c if c in "\n\t" or ord(c) >= 32 and not 127 <= ord(c) <= 159 else "?" for c in value)
    try:
        print(value)
    except UnicodeEncodeError:
        # A cp1252 console cannot carry every script this tool answers in. A half sentence that
        # survives is better than a traceback where the answer should have been.
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(value.encode(encoding, "replace").decode(encoding, "replace"))


ROBOT_BANNER = r"""
     .---------.
    /  [o] [o]  \
   |  <code/>   |
    \  .-----. /
     '---------'
  AI Code Engineer - Autonomous Pair Programmer
"""


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=f"{ROBOT_BANNER.strip()}\n\nReview-first Python developer agent"
    )
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check Python, local Ollama, Docker and key presence")
    first = sub.add_parser("setup", help="Run the first checks in order, and prove the red line offline")
    first.add_argument("--repo", type=Path, help="Check this project folder for a runnable command")
    first.add_argument("--provider", default="", help="Provider to check instead of Ollama")
    first.add_argument("--endpoint", default="", help="Endpoint for that provider")
    first.add_argument("--model", default="", help="Model the windows should land on")
    first.add_argument("--arabic", action="store_true", help="Write the rows in Arabic")
    first.add_argument("--yes", action="store_true", help="Answer every offer yes, for a script")
    first.add_argument("--no-demo", action="store_true", help="Do not offer the offline proof")
    repo = sub.add_parser("map", help="Read-only repository map: files with their parsed declarations")
    repo.add_argument("--repo", type=Path, required=True)
    seal = sub.add_parser("read-only",
                          help="Declare a folder Read-only for every surface, lift it, or list what is declared")
    seal.add_argument("--repo", type=Path, help="The folder to declare or lift; with no folder, list")
    seal.add_argument("--off", action="store_true", help="Lift the declaration instead of making it")
    seal.add_argument("--arabic", action="store_true", help="Write the rows in Arabic")
    rule = sub.add_parser("policy",
                          help="Read or set what one folder answers without asking, for every surface")
    rule.add_argument("--repo", type=Path,
                      help="The folder whose rows are read or written; with no folder, list every declared row")
    rule.add_argument("--action", choices=sorted(policy.CONFIGURABLE_ACTIONS), metavar="CLASS",
                      help="One of: " + ", ".join(sorted(policy.CONFIGURABLE_ACTIONS)))
    rule.add_argument("--verdict", choices=sorted(policy.VERDICTS),
                      help="allow, ask or deny — an override loosens a question, never opens a refusal")
    rule.add_argument("--off", action="store_true",
                      help="Remove one class's row, or the folder's whole set when no class is named")
    rule.add_argument("--arabic", action="store_true", help="Write the rows in Arabic")
    over = sub.add_parser("overrides",
                          help="Read, set or remove the configuration rows the program signs")
    over.add_argument("--list", action="store_true", help="Every row and every refusal, newest first")
    over.add_argument("--set", nargs=3, metavar=("TARGET", "KEY", "VALUE"),
                      help=f"A provider key or {overrides.EVERY}, one of "
                           f"{', '.join(sorted(overrides.TYPES))}, and the new value")
    over.add_argument("--unset", nargs=2, metavar=("TARGET", "KEY"), help="Remove one row")
    over.add_argument("--arabic", action="store_true", help="Write the rows in Arabic")
    draft = sub.add_parser("plan", help="Explore a repository and save a proposed diff; never writes code")
    draft.add_argument("task")
    draft.add_argument("--repo", type=Path, required=True)
    draft.add_argument("--config", type=Path)
    draft.add_argument("--model", help="Override model ID (provider is selected in config)")
    draft.add_argument("--plan-file", help="Markdown/text plan inside the selected project")
    draft.add_argument("--runs", type=Path, default=Path(".agent-runs"))
    draft.add_argument("--allow-cloud", action="store_true")
    draft.add_argument("--data-class", choices=["restricted", "public", "synthetic"], default="restricted")
    for name in ("review", "apply", "reopen", "rollback", "verify", "status"):
        command = sub.add_parser(name)
        command.add_argument("session", type=Path)
        if name in {"apply", "reopen", "rollback"}:
            command.add_argument("--approve", help="Full proposal SHA256; otherwise asks interactively")
        if name == "verify":
            command.add_argument("--recipe", choices=sorted(RECIPES))
            command.add_argument("--image", help="Preloaded approved Linux image@sha256:digest")
    curl_cmd = sub.add_parser("curl", help="Generate or run a cURL command for Google Gemini or another provider")
    curl_cmd.add_argument("--provider", default="gemini", help="Provider (default: gemini)")
    curl_cmd.add_argument("--endpoint", default="", help="Custom endpoint URL")
    curl_cmd.add_argument("--model", default="", help="Model name (e.g. gemini-2.0-flash)")
    curl_cmd.add_argument("--prompt", default="Hello! Please confirm you are working.", help="Prompt text")
    curl_cmd.add_argument("--run", action="store_true", help="Execute the request directly")
    sub.add_parser("demo", help="Run deterministic, offline synthetic demo without changing your repository")
    export = sub.add_parser("export-session",
                            help="Write one session's record as JSON or as a readable report")
    export.add_argument("session", help="Session id, a unique id prefix, or its folder/file")
    export.add_argument("--format", choices=("json", "markdown"), default="markdown")
    export.add_argument("--runs", type=Path, default=Path(".agent-runs"))
    export.add_argument("--out", type=Path, help="Write here instead of standard output")
    return root


def run_curl(args) -> int:
    kind = config.kind_for(args.provider) or config.BY_KEY.get("gemini") or config.DEFAULT_KIND
    endpoint = (args.endpoint or kind.base or "").rstrip("/")
    model = args.model or (kind.verified[0] if kind.verified else "gemini-2.0-flash")
    prompt = args.prompt

    key = ""
    if kind.key == "gemini":
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    elif kind.key_env:
        key = os.environ.get(kind.key_env) or ""

    key_display = key or f"${kind.key_env or 'API_KEY'}"
    chat_url = f"{endpoint}/chat/completions"
    payload = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}, indent=2)

    cmd_str = (
        f"curl {chat_url} \\\n"
        f"  -H \"Content-Type: application/json\" \\\n"
        f"  -H \"Authorization: Bearer {key_display}\" \\\n"
        f"  -d '{payload}'"
    )
    safe_print(cmd_str)

    if args.run:
        if not key:
            safe_print(f"\nNote: Key not found in environment for {kind.label}. Set {kind.key_env or 'API key'} to run.")
            return 1
        safe_print("\n--- Executing request ---")
        try:
            from .providers import request_json
            body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
            res = request_json(chat_url, payload=body, key=key, timeout=30)
            safe_print(json.dumps(res, indent=2))
        except Exception as exc:
            safe_print(f"Error: {exc}")
            return 1
    return 0


def doctor() -> dict:
    result = {"python": sys.version.split()[0], "runtime_dependencies": "standard library only",
              "docker": bool(shutil.which("docker")),
              "openrouter_key_present": bool(os.environ.get("OPENROUTER_API_KEY")),
              "gemini_key_present": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
              "groq_key_present": bool(os.environ.get("GROQ_API_KEY")),
              "local_models": [], "ollama_models": []}
    # The same probe `agent setup` reads, so the two cannot disagree about whether Ollama answered.
    entries, _source, error = setup.reach(config.OLLAMA)
    if error:
        result["ollama"] = "unreachable"
    else:
        result["ollama_models"] = [item["id"] for item in entries]
        result["local_models"] = [item["id"] for item in entries if not item["cloud"]]
        result["ollama"] = "reachable"
    return result


def demo() -> dict:
    """The offline proof, in `setup` because the wizard and the window offer it too."""
    return setup.run_demo()


def app_dir() -> Path:
    """Where this tool keeps its own records — the folder both windows write to.

    Computed the way the launcher computes it (`webapp/launch.py`), not from the working directory: the
    declaration file is only worth having if `agent read-only` and a window opened from another folder
    are reading the same one.
    """
    return Path(__file__).resolve().parents[2]


def refuses_sealed(folder, what: str) -> None:
    """Stop a terminal write on a folder somebody told to stay read-only.

    The answer comes off the file the windows use rather than from anything this process remembers: a
    position a second process could ignore would only protect the window that set it. Naming the
    command that lifts it is part of the refusal — an error that says "not allowed" and nothing else
    sends the operator hunting through config files for a switch that is not there.
    """
    if modes.sealed(app_dir(), folder):
        raise AgentError(modes.refusal(app_dir(), folder, what) + "  " + intent.lift(str(folder)))


def refuses_policy(folder, session, interactive: bool = True) -> None:
    """Stop a terminal write that edits the file this tool takes its commands out of.

    DENY is the folder's own rule and is refused outright. ASK is refused when nobody is typing: the
    proposal hash an operator types for `apply` *is* the confirmation this class asks for, and
    `--approve` from a script carries no such keystroke — so an automation that wants it says so once,
    in the folder's policy, instead of getting it silently on every run.
    """
    runs = policy.runs_later_paths((session or {}).get("changes"))
    if not runs:
        return
    verdict = permissions.verdict(app_dir(), folder, policy.WRITE_THAT_RUNS)
    if verdict not in (policy.DENY, policy.ASK) or (verdict == policy.ASK and interactive):
        return
    arabic = is_arabic(asked_of(str((session or {}).get("task", ""))))
    raise AgentError(policy_line(arabic, policy.WRITE_THAT_RUNS, verdict, names=", ".join(runs[:3]))
                     + "  " + shared_note("policy_how", arabic=arabic, folder=str(folder),
                                          action=policy.WRITE_THAT_RUNS))


def speak_arabic() -> None:
    """Give the console an encoding that can carry Arabic at all.

    A cp1252 terminal cannot print one Arabic letter, and the person who asked for Arabic rows is
    exactly the one who needs to read them rather than see a line of question marks.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass


def run_read_only(args) -> int:
    """Declare, lift, or list — the position a folder keeps after this terminal closes."""
    folder = str(args.repo) if args.repo else ""
    if args.arabic:
        speak_arabic()
    if not folder:
        rows = modes.listed(app_dir())
        if not rows:
            safe_print("Nothing is declared. A folder with no declaration opens on Change mode.")
            return 0
        for row in rows:
            safe_print(f"{row.get('path') or row['folder']}  {intent.label(row['mode'])}  "
                       f"{intent.source(row.get('by', ''), arabic=args.arabic)}  {row.get('at', '')}")
        return 0
    if not Path(folder).is_dir():
        raise AgentError("No such folder to declare: " + folder)
    if args.off:
        modes.forget(app_dir(), folder)
        safe_print("Nothing is declared for " + folder
                   + ". It opens on Change mode again, like any folder nobody has told otherwise.")
        return 0
    row = modes.declare(app_dir(), folder, intent.READ, by=modes.TERMINAL)
    safe_print(intent.declared(by=row["by"], at=row["at"], arabic=args.arabic,
                               project=Path(folder).name))
    safe_print(intent.lift(folder, arabic=args.arabic))
    return 0


def _policy_table(where, folder: str, arabic: bool) -> None:
    """Print the eight classes as this folder will be treated, and who said so.

    A row the folder set itself carries its surface and its minute: a verdict nobody can trace reads like
    a bug, and the operator's next move is to find whoever set it. A row nobody set is said plainly.
    """
    words = policy_verdicts(arabic)
    key = permissions.folder_key(folder)
    own = {row["action"]: row for row in permissions.listed(where) if row["folder"] == key}
    safe_print(shared_note("policy_heading", arabic=arabic))
    for name in sorted(policy.TABLE):
        tail = ""
        if name in own:
            bits = [part for part in (intent.source(own[name].get("by", ""), arabic=arabic),
                                      own[name].get("at", "")) if part]
            tail = "  (" + " · ".join(bits) + ")"
        if name in policy.MANAGED_ACTIONS:
            safe_print(f"  {name} — {shared_note('policy_managed_' + name, arabic=arabic)}")
        else:
            safe_print(f"  {name} = {words[permissions.verdict(where, folder, name)]}" + tail)
    safe_print(shared_note("policy_allow_note", arabic=arabic,
                           count=len(own), total=len(policy.CONFIGURABLE_ACTIONS)))


def run_policy(args) -> int:
    """Read or set the rows one folder answers for itself.

    The web window draws this same table and writes to this same file; this command is the half a person
    reaches from a terminal, which is where a refusal has to name a remedy that surface can actually give.
    Nothing caches a verdict in a window, so a row set here is obeyed by both windows the next time they
    look.
    """
    where = app_dir()
    if args.arabic:
        speak_arabic()
    arabic = bool(args.arabic)
    words = policy_verdicts(arabic)
    folder = str(args.repo) if args.repo else ""

    if not folder:
        rows = permissions.listed(where)
        if not rows:
            safe_print("No folder has answered any class for itself, so every action answers the table.")
            return 0
        for row in rows:
            safe_print(f"{row['path'] or row['folder']}  {row['action']} = "
                       f"{words.get(row['verdict'], row['verdict'])}  "
                       f"{intent.source(row.get('by', ''), arabic=arabic)}  {row.get('at', '')}")
        return 0

    if not Path(folder).is_dir():
        raise AgentError("No such folder to set a rule for: " + folder)

    if args.off:
        permissions.forget(where, folder, args.action or "")
        _policy_table(where, folder, arabic)
        return 0

    if not args.action and not args.verdict:
        _policy_table(where, folder, arabic)
        return 0

    if not args.action or not args.verdict:
        raise AgentError("A policy row needs both a class and a verdict (a configurable class): "
                         "--action and --verdict. "
                         "`agent policy --repo PATH` alone prints all classes and their safeguards.")

    if not permissions.declare(where, folder, args.action, args.verdict, by=permissions.TERMINAL):
        raise AgentError("Nothing was written: this folder has no record key of its own.")
    _policy_table(where, folder, arabic)
    return 0


def run_overrides(args) -> int:
    """Read, set or remove the rows the program signs.

    The file is the terminal's half of the same store both windows write, so a row set here changes a
    profile read here — and one set in a window changes this listing. Writing it through ``overrides``
    rather than editing the JSON is what makes it take effect: a row the signature does not cover is
    refused on the next read, and this command is the only routine that signs.
    """
    where = app_dir()
    if args.arabic:
        speak_arabic()
    created = overrides.ensure(where)
    if created:
        safe_print("Created " + str(overrides.path(where)) + " and its signing key "
                   + str(overrides.key_path(where)) + " — the rows live beside the other records this "
                   "tool keeps, and never inside a project it is working on.")
    if args.set:
        target, key, value = args.set
        row = overrides.put(where, target, key, value, by=modes.TERMINAL, arabic=args.arabic)
        safe_print(f"{row['key']} = {row['value']} for "
                   + (f"every provider" if row["target"] == overrides.EVERY else row["target"])
                   + ". Signed, and read by every surface from now on.")
        return 0
    if args.unset:
        target, key = args.unset
        safe_print(("Removed " if overrides.delete(where, target, key) else "Nothing to remove: no row for ")
                   + f"{key} for {target or overrides.EVERY}.")
        return 0
    listed = overrides.listed(where)
    if not listed:
        safe_print("Nothing is overridden yet. The file lists its own keys, ranges and order.")
    for row in listed:
        if row["state"] == "in force":
            safe_print(f"{row['key']} = {row['value']}  [{row['target']}]  {row['by']}  {row['at']}")
        else:
            safe_print(overrides.says(row, arabic=args.arabic) + "  refused: "
                       + overrides.why_text(row["why"], arabic=args.arabic))
    refused = overrides.refusals(where)
    if refused:
        safe_print(overrides.refused_line(refused, arabic=args.arabic))
    safe_print("")
    safe_print(overrides.scope(arabic=args.arabic))
    return 0


def ask_line(prompt: str, ask=input) -> str:
    """One line from the operator. Ctrl-D is an empty answer, not a traceback."""
    try:
        return str(ask(prompt) or "").strip()
    except EOFError:
        return ""


def wants(prompt: str, args, ask=input, interactive=True) -> bool:
    """One yes/no. A pipe gets a no and a line saying so, not a hang.

    `apply` already refuses to block on a pipe; a wizard that waited on `input()` in a CI job would be
    the same mistake with a friendlier name.
    """
    if getattr(args, "yes", False):
        return True
    if not interactive:
        safe_print("(not an interactive terminal, so that question is answered no)")
        return False
    return ask_line(prompt, ask).lower() in {"y", "yes", "ok", "نعم", "ايه", "أيوه"}


def run_setup(args, ask=input, interactive=None) -> int:
    """The first-run wizard: check, prove, explain. It writes nothing outside a temporary folder.

    Choosing the model, granting the folder and picking the write position all happen in the window,
    because that is where those preferences are stored — a second copy of the same choices kept by the
    terminal is exactly how two surfaces begin to disagree. So this run reports, offers the proof, and
    then names the three things to do there.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()
    if args.arabic:
        # A console that defaults to cp1252 cannot print Arabic at all, and the person who asked for
        # Arabic rows is exactly the one who needs to read them rather than see question marks.
        speak_arabic()
    # The wizard is the first thing a new operator runs, so it is where the file the windows and the
    # terminal later share appears — with its own keys and ranges printed inside it.
    overrides.ensure(app_dir())
    repo = str(args.repo) if args.repo else ""
    rows = setup.audit(repo=repo, provider=args.provider, endpoint=args.endpoint,
                       model=args.model, arabic=args.arabic)
    safe_print(setup.render(rows))

    if args.repo is None and not args.yes and wants(
            "Name a project folder to check it for a runnable command? (y/n): ",
            args, ask, interactive):
        typed = ask_line("Project folder: ", ask).strip('"')
        if typed:
            rows = setup.audit(repo=typed, provider=args.provider, endpoint=args.endpoint,
                               model=args.model, arabic=args.arabic)
            safe_print(setup.render([row for row in rows if row["id"] == "project"]))
        else:
            safe_print("No folder named, so nothing was checked against one.")
    elif args.repo is None:
        # A scripted run answered yes to everything, and there is nothing to guess here: the folder is
        # the one question a yes cannot answer.
        safe_print("No folder named with --repo, so nothing was checked against one.")

    if not args.no_demo and wants("Run the offline proof? It writes only to a temporary folder, and "
                                  "rolls it back. (y/n): ", args, ask, interactive):
        safe_print("Running it…")
        safe_print(setup.render([setup.demo_row(setup.run_demo(), arabic=args.arabic)]))

    # The promises are in the audit already; printing them again here put the same paragraph on the
    # screen twice three lines apart. The question points at the copy the operator has just read.
    safe_print("")
    accepted = wants("Accept the five promises printed above, before the window offers you a write? "
                     "(y/n): ", args, ask, interactive)

    counts = setup.counts(rows)
    safe_print("")
    if args.arabic:
        lines = [
            "الخطوات الجاية في النافذة: افتح `agent ui`، اختار الموديل من Settings، افتح مجلد من "
            "الشريط الجانبي، وبعدها حدد الشارة: Chat أو Read-only أو Change.",
            "الكتابة التلقائية مفتاح فوق Change، ومتشغلش غير لو كنت مستعد ترجع بـ git.",
            "لو عايز المجلد يفضل للقراءة فقط من كل السطوح ومن غير ما تعتمد على النافذة: "
            "`agent read-only --repo PATH`، والرفع بـ `--off` بنفس المسار.",
        ]
        if not accepted:
            lines.insert(0, "لم تتم الموافقة على السياسة: الأداة لسه هتشتغل، بس مش هتكتب في ملفاتك.")
    else:
        lines = [
            "Next, in the window: start it with `agent ui`, pick the model in Settings, open a folder "
            "from the sidebar, then set the badge to Chat, Read-only or Change.",
            "Auto-Apply is the switch on top of Change — leave it off unless you are ready to reverse "
            "the writes with git.",
            "A folder can also be sealed from here, where no window has to stay open for it to hold: "
            "`agent read-only --repo PATH`, lifted by the same line with `--off`.",
        ]
        if not accepted:
            lines.insert(0, "The policy was not accepted. The tool still runs, but no write should be "
                            "approved until you have read those lines.")
    safe_print("\n".join(lines))
    safe_print("\n" + setup.tally(counts, arabic=args.arabic) + ".")
    return 1 if counts["bad"] else 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            safe_print(json.dumps(doctor(), indent=2))
        elif args.command == "setup":
            return run_setup(args)
        elif args.command == "map":
            safe_print(Workspace(args.repo).repo_map())
        elif args.command == "read-only":
            return run_read_only(args)
        elif args.command == "policy":
            return run_policy(args)
        elif args.command == "overrides":
            return run_overrides(args)
        elif args.command == "plan":
            refuses_sealed(args.repo, "A proposal")
            settings = load_settings(args.config, app_dir())
            if args.model:
                settings = replace(settings, model=args.model)
            validate(settings)
            ws = Workspace(args.repo)
            provider = make_provider(settings, allow_cloud=args.allow_cloud, data_class=args.data_class)
            path = plan(ws, args.task, provider, settings, args.runs, progress=safe_print, plan_file=args.plan_file)
            safe_print(review(load_session(path)))
            safe_print("\nSession: " + str(path))
            safe_print("No project files changed. Review this proposal before applying it.")
        elif args.command == "curl":
            return run_curl(args)
        elif args.command == "demo":
            result = demo()
            safe_print(json.dumps(result, indent=2))
            return 0 if result["proposal_apply_rollback"] == "passed" else 1
        elif args.command == "export-session":
            text = export_file(find_session(args.runs, args.session), args.format)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text, encoding="utf-8", newline="\n")
                safe_print("Wrote " + str(args.out) + " (" + str(len(text)) + " characters)")
            else:
                safe_print(text)
        else:
            session = load_session(args.session)
            if args.command == "review":
                safe_print(review(session))
            elif args.command == "status":
                safe_print(json.dumps({"id": session["id"], "state": session["state"],
                                       "model": session["model"], "events": session["events"]}, indent=2))
            elif args.command in {"apply", "reopen", "rollback"}:
                # Checked before anything is asked, because a hash typed for a write that was never
                # going to happen teaches the operator that the prompts do not mean anything.
                refuses_sealed(session.get("root", ""), args.command)
                if args.command == "apply":
                    # Only the forward write is asked about. A roll back returns a file to the state the
                    # operator already reviewed, and refusing it would use a guard to block the way out.
                    refuses_policy(session.get("root", ""), session, interactive=sys.stdin.isatty())
                approved = args.approve
                if not approved:
                    safe_print(review(session))
                    if not sys.stdin.isatty():
                        raise AgentError("Non-interactive mode requires --approve with the full proposal SHA256.")
                    approved = input("Type the full proposal SHA256 to " + args.command + ": ").strip()
                operation = {"apply": apply_proposal, "reopen": reopen_proposal,
                             "rollback": rollback}[args.command]
                safe_print("State: " + operation(args.session, approved)["state"])
            elif args.command == "verify":
                result = verify(args.session, args.recipe, args.image)
                safe_print(json.dumps(result, indent=2))
                return 0 if result["status"] == "passed" else 2
        return 0
    except KeyboardInterrupt:
        safe_print("Cancelled. Inspect session status before retrying an interrupted apply.")
        return 130
    except (AgentError, OSError) as exc:
        safe_print("Error: " + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
