"""Run without installing packages: python agent.py --help."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from ai_code_engineer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
