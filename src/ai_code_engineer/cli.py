from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys

from .config import load_settings, validate
from .engine import apply_proposal, load_session, plan, review, rollback
from .errors import AgentError
from .providers import make_provider
from .report import export_file, find_session
from . import config, setup
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


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="agent", description="Review-first Python developer agent (MVP)")
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
    draft = sub.add_parser("plan", help="Explore a repository and save a proposed diff; never writes code")
    draft.add_argument("task")
    draft.add_argument("--repo", type=Path, required=True)
    draft.add_argument("--config", type=Path)
    draft.add_argument("--model", help="Override model ID (provider is selected in config)")
    draft.add_argument("--plan-file", help="Markdown/text plan inside the selected project")
    draft.add_argument("--runs", type=Path, default=Path(".agent-runs"))
    draft.add_argument("--allow-cloud", action="store_true")
    draft.add_argument("--data-class", choices=["restricted", "public", "synthetic"], default="restricted")
    for name in ("review", "apply", "rollback", "verify", "status"):
        command = sub.add_parser(name)
        command.add_argument("session", type=Path)
        if name in {"apply", "rollback"}:
            command.add_argument("--approve", help="Full proposal SHA256; otherwise asks interactively")
        if name == "verify":
            command.add_argument("--recipe", choices=sorted(RECIPES))
            command.add_argument("--image", help="Preloaded approved Linux image@sha256:digest")
    sub.add_parser("demo", help="Run deterministic, offline synthetic demo without changing your repository")
    export = sub.add_parser("export-session",
                            help="Write one session's record as JSON or as a readable report")
    export.add_argument("session", help="Session id, a unique id prefix, or its folder/file")
    export.add_argument("--format", choices=("json", "markdown"), default="markdown")
    export.add_argument("--runs", type=Path, default=Path(".agent-runs"))
    export.add_argument("--out", type=Path, help="Write here instead of standard output")
    return root


def doctor() -> dict:
    result = {"python": sys.version.split()[0], "runtime_dependencies": "standard library only",
              "docker": bool(shutil.which("docker")),
              "openrouter_key_present": bool(os.environ.get("OPENROUTER_API_KEY")),
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
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass
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
        ]
        if not accepted:
            lines.insert(0, "لم تتم الموافقة على السياسة: الأداة لسه هتشتغل، بس مش هتكتب في ملفاتك.")
    else:
        lines = [
            "Next, in the window: start it with `agent ui`, pick the model in Settings, open a folder "
            "from the sidebar, then set the badge to Chat, Read-only or Change.",
            "Auto-Apply is the switch on top of Change — leave it off unless you are ready to reverse "
            "the writes with git.",
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
        elif args.command == "plan":
            settings = load_settings(args.config)
            if args.model:
                settings = replace(settings, model=args.model)
            validate(settings)
            ws = Workspace(args.repo)
            provider = make_provider(settings, allow_cloud=args.allow_cloud, data_class=args.data_class)
            path = plan(ws, args.task, provider, settings, args.runs, progress=safe_print, plan_file=args.plan_file)
            safe_print(review(load_session(path)))
            safe_print("\nSession: " + str(path))
            safe_print("No project files changed. Review this proposal before applying it.")
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
            elif args.command in {"apply", "rollback"}:
                approved = args.approve
                if not approved:
                    safe_print(review(session))
                    if not sys.stdin.isatty():
                        raise AgentError("Non-interactive mode requires --approve with the full proposal SHA256.")
                    approved = input("Type the full proposal SHA256 to " + args.command + ": ").strip()
                operation = apply_proposal if args.command == "apply" else rollback
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
