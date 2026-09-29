from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .config import Settings, load_settings, validate
from .catalog import ollama_models
from .engine import apply_proposal, load_session, plan, review, rollback
from .errors import AgentError
from .providers import make_provider
from .report import export_file, find_session
from .verification import RECIPES, verify
from .workspace import Workspace


def safe_print(value: str) -> None:
    # Strip terminal control characters in untrusted model/repository content.
    value = "".join(c if c in "\n\t" or ord(c) >= 32 and not 127 <= ord(c) <= 159 else "?" for c in value)
    print(value)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="agent", description="Review-first Python developer agent (MVP)")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check Python, local Ollama, Docker and key presence")
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
    try:
        models = ollama_models()
        result["ollama_models"] = [m["id"] for m in models]
        result["local_models"] = [m["id"] for m in models if not m["cloud"]]
        result["ollama"] = "reachable"
    except AgentError:
        result["ollama"] = "unreachable"
    return result


def demo() -> dict:
    class DemoProvider:
        model = "deterministic-demo-no-llm"
        turn = 0

        def generate(self, messages):
            self.turn += 1
            if self.turn == 1:
                return json.dumps({"action": "read_file", "path": "calculator.py"})
            return json.dumps({"action": "propose", "summary": "Fix addition in a synthetic fixture.",
                               "checks": ["Run addition tests in an isolated worker."],
                               "changes": [{"path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"}]})

    with tempfile.TemporaryDirectory(prefix="ai-agent-demo-") as temp:
        root = Path(temp) / "repo"
        root.mkdir()
        (root / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        session_path = plan(Workspace(root), "Fix add", DemoProvider(), Settings(), Path(temp) / "runs", progress=lambda _: None)
        proposal = load_session(session_path)
        apply_proposal(session_path, proposal["proposal_hash"])
        result = verify(session_path)
        changed = (root / "calculator.py").read_text() == "def add(a, b):\n    return a + b\n"
        rollback(session_path, proposal["proposal_hash"])
        restored = (root / "calculator.py").read_text() == "def add(a, b):\n    return a - b\n"
        return {"proposal_apply_rollback": "passed" if changed and restored else "failed",
                "static_checks": result, "llm_used": False,
                "note": "Synthetic demo only. No project code executed. No build/test verification claimed."}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            safe_print(json.dumps(doctor(), indent=2))
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
