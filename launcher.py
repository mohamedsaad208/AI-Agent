"""Interactive launcher. All input stays in Python; never interpolate it into shell."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from ai_code_engineer.cli import main as run_cli, safe_print


def choose_session() -> str | None:
    sessions = sorted((ROOT / ".agent-runs").glob("*/session.json"),
                      key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    if not sessions:
        print("No saved sessions yet. Start a local task first.")
        return None
    print("\nRecent sessions (newest first):")
    for index, path in enumerate(sessions, 1):
        print(f"  {index}. {path.parent.name}")
    choice = input("Session number [1], or 0 to cancel: ").strip() or "1"
    if choice == "0":
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(sessions):
        print("Invalid session number.")
        return None
    return str(sessions[int(choice) - 1])


def main() -> int:
    print("\nAI Code Engineer | Python + Ollama")
    safe_print("Project: " + str(ROOT))
    print("New tasks use the local model. No cloud connection is selected here.")
    while True:
        print("\n1. Start a local task (plan + proposed diff)")
        print("2. Quick offline demo")
        print("3. Check setup / available models")
        print("4. Review a saved proposal")
        print("5. Apply a proposal (requires its approval hash)")
        print("6. Static check of applied changes (does not run tests)")
        print("7. Roll back an applied proposal (requires approval hash)")
        print("0. Exit")
        choice = input("Choose: ").strip()
        if choice == "0":
            return 0
        if choice == "1":
            repo = input("Repository folder [examples/demo_repo]: ").strip().strip('"')
            repo_path = Path(repo).expanduser() if repo else ROOT / "examples" / "demo_repo"
            if not repo_path.is_absolute():
                repo_path = ROOT / repo_path
            task = input("What should the agent change? ").strip()
            if not task:
                print("Task cancelled: no description entered.")
                continue
            args = ["plan", task, "--repo", str(repo_path), "--config",
                    str(ROOT / "profiles" / "local.toml"), "--runs", str(ROOT / ".agent-runs")]
        elif choice in {"2", "3"}:
            args = ["demo" if choice == "2" else "doctor"]
        elif choice in {"4", "5", "6", "7"}:
            session = choose_session()
            if session is None:
                continue
            args = [{"4": "review", "5": "apply", "6": "verify", "7": "rollback"}[choice], session]
        else:
            print("Please choose a number from the menu.")
            continue
        code = run_cli(args)
        if code == 2:
            print("Verification is incomplete. Read the result above.")
        elif code:
            print("Operation did not complete. Read the message above; you can try again.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nClosed.")
