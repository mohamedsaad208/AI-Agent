"""What has to be inside the wheel, and why a green suite cannot tell you it is missing.

`webapp/server.py` resolves its static folder from `__file__`, so the UI travels inside the package or
not at all. A wheel built without `[tool.setuptools.package-data]` contains every `.py` file, installs
cleanly, passes every test (the tests read the source tree, not the installed files) and opens a blank
window: an HTML page with no stylesheet and no script. This is the check that closes that gap, and it
runs in CI against the artifact rather than against the checkout.

Usage: `python tools/check_package.py [path/to.wheel]` — with no argument it takes the newest wheel in
`dist/`, which is what `python -m build --wheel` just produced.
"""
from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile

# `index.html` alone would pass with an unstyled page, so every file the window fetches is named.
REQUIRED = ("ai_code_engineer/webapp/static/index.html",
            "ai_code_engineer/webapp/static/app.js",
            "ai_code_engineer/webapp/static/app.css",
            "ai_code_engineer/webapp/static/boot.js")


def newest_wheel() -> Path:
    dist = Path(__file__).resolve().parents[1] / "dist"
    wheels = sorted(dist.glob("*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        raise SystemExit("no wheel in dist/ — run `python -m build --wheel` first")
    return wheels[-1]


def main(argv: list[str]) -> int:
    wheel = Path(argv[1]) if len(argv) > 1 else newest_wheel()
    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = [want for want in REQUIRED if want not in names]
        modules = sorted(name for name in names if name.endswith(".py") and name.startswith("ai_code_engineer"))
    if missing:
        print("%s is missing:" % wheel.name)
        for item in missing:
            print("   - " + item)
        print("The window opens with no stylesheet and no script. Check "
              "[tool.setuptools.package-data] in pyproject.toml.")
        return 1
    if not any(name == "ai_code_engineer/cli.py" for name in names):
        print("%s has no cli.py — the `agent` entry point would not exist" % wheel.name)
        return 1
    print("%s carries %d modules and all %d UI files" % (wheel.name, len(modules), len(REQUIRED)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
