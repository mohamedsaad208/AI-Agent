"""Start the installed package's window and read three files back out of it.

Run this from a virtualenv where `ai-code-engineer` was pip-installed and *without* `src/` reachable:
the point is proving the installed artifact serves its own UI, which is exactly what a wheel missing
its `static/` folder fails at while every source-tree test passes. `check_package.py` says the files
are in the zip; this says the running server can find them, because it resolves them from
`__file__` inside site-packages rather than next to the checkout.

Exit code 0 and one line of output on success; a non-zero exit with the failing request otherwise.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path


def fetch(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"Connection": "close"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status, response.read()


def main() -> int:
    try:
        import ai_code_engineer
    except ImportError as exc:
        print("the package did not import from the install: %s" % exc)
        return 1
    root = Path(ai_code_engineer.__file__).resolve().parent
    if "site-packages" not in str(root) and "dist-packages" not in str(root):
        print("imported from the checkout, not the install: %s" % root)
        return 1

    from ai_code_engineer.webapp import server
    from ai_code_engineer.webapp.fake import FakeController

    instance, url, token = server.serve(FakeController())
    try:
        base = url.rsplit("?", 1)[0]
        checks = [
            ("the page itself", base, b"AI Code Engineer"),
            ("the stylesheet", base + "app.css", b"--accent"),
            ("the boot script", base + "boot.js", b"dataset.style"),
        ]
        # Every script the installed package carries, asked of the running server rather than named
        # here. The front end is split across several files, and a check that names one file proves
        # that file exists -- not that the wheel the window is loading from has the other six.
        for script in sorted(server.STATIC.glob("ui-*.js")):
            checks.append(("the " + script.name, base + script.name, None))
        for label, target, marker in checks:
            status, body = fetch(target)
            if status != 200 or not body or (marker and marker not in body):
                print("%s came back %s with %d bytes" % (label, status, len(body)))
                return 1
        status, body = fetch(base + "api/bootstrap?" + url.split("?", 1)[1])
        state = json.loads(body)
        if not state.get("provider", {}).get("modes"):
            print("the window started but has no provider list")
            return 1
        print("installed package served the window from %s (%d modes)"
              % (root, len(state["provider"]["modes"])))
        return 0
    finally:
        instance.shutdown()
        instance.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
