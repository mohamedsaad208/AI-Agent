"""The two lines a test repository needs, written once.

`tests/` is not a package — discovery imports each module on its own — so a file like this has to be
reached by adding its directory to `sys.path` before importing it. That is also why it holds nothing
but functions: no import-time state to differ between the two ways the suite is started.
"""
from __future__ import annotations

from pathlib import Path

from doubles import CALCULATOR_BAD


def sandbox_repo(app_dir, name="repo", with_tests=True, files=None) -> Path:
    """A folder that looks like a Python project the agent can plan against.

    `with_tests` is not decoration: `runner.detect()` reads the marker files, so a fixture that
    grows a `tests/` directory can change which command a window believes is available. The Tk
    window's fixture has never had one, so it asks for the shape it has always had rather than
    inheriting a new one from a shared helper.
    """
    repo = Path(app_dir) / name
    if with_tests:
        (repo / "tests").mkdir(parents=True, exist_ok=True)
    else:
        repo.mkdir(parents=True, exist_ok=True)
    (repo / "calculator.py").write_text(files.get("calculator.py", CALCULATOR_BAD) if files
                                        else CALCULATOR_BAD, encoding="utf-8", newline="\n")
    return repo
