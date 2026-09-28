"""Start the local server and show it as an app window instead of a browser tab.

Edge and Chrome both take ``--app=<url>``, which opens a frameless top-level window with
no address bar, tabs or extensions. Pointing them at a private ``--user-data-dir`` is what
makes that work: an already-running browser would otherwise hand the URL to its own
window and exit immediately, so the app would close the moment it opened.
"""
from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from pathlib import Path

MIN_WIDTH, MIN_HEIGHT = 1180, 720


def find_browser() -> Path | None:
    """A Chromium browser that supports --app, or None when the machine has only Firefox."""
    roots = [os.environ.get(key) for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    candidates = [Path(root) / tail for root in roots if root
                  for tail in ("Microsoft/Edge/Application/msedge.exe",
                               "Google/Chrome/Application/chrome.exe",
                               "Chromium/Application/chrome.exe")]
    candidates += [Path(rf"{drive}\Program Files\{vendor}\{exe}")
                   for drive in "CE" for vendor, exe in
                   (("Microsoft/Edge/Application", "msedge.exe"), ("Google/Chrome/Application", "chrome.exe"))]
    return next((path for path in candidates if path.is_file()), None)


def window_size() -> tuple[int, int]:
    """Fit the work area without a scrollbar; Tk is only used to measure the screen."""
    try:
        import tkinter
        probe = tkinter.Tk()
        probe.withdraw()
        width, height = probe.winfo_screenwidth() - 120, probe.winfo_screenheight() - 140
        probe.destroy()
    except Exception:                                # noqa: BLE001 - headless is a valid answer too
        width, height = 1440, 900
    return max(MIN_WIDTH, min(width, 1800)), max(MIN_HEIGHT, min(height, 1200))


def open_window(url: str, profile: Path) -> subprocess.Popen | None:
    browser = find_browser()
    if browser is None:
        return None
    profile.mkdir(parents=True, exist_ok=True)
    arguments = [str(browser), f"--app={url}", f"--user-data-dir={profile}",
                 "--no-first-run", "--no-default-browser-check",
                 "--disable-features=Translate,MediaRouter"]
    if len(list(profile.glob("Preferences"))) == 0:
        # A fresh profile has no remembered bounds, so give it sensible ones. Once the
        # profile exists the browser restores its own size and place, and forcing these
        # flags would throw away wherever the user moved the window.
        width, height = window_size()
        arguments += [f"--window-size={width},{height}", "--window-position=60,50"]
    return subprocess.Popen(arguments,
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def run(app_dir: Path) -> int:
    """Serve the UI and block until its window closes.

    Without a Chromium browser this still serves — in an ordinary tab, with the process held
    open around it. The Tk window is not a fallback from here: it is `--tk`, or whatever
    `desktop.pyw` starts when this function raises.
    """
    from .controller import AgentController
    from .server import serve

    controller = AgentController(app_dir)
    server, url, _token = serve(controller)
    # The model list is what the window used to fetch a moment after it appeared: an
    # offline Ollama is a normal state, so it must not block the window from opening.
    controller.check_setup()
    window = open_window(url, app_dir / ".agent-webview")
    if window is None:
        # No Chromium on this machine: the page is still a real UI, just in a normal tab.
        webbrowser.open(url)
    try:
        if window is not None:
            window.wait()
        else:
            import time
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
        server.shutdown()
    return 0


def run_tk(app_dir: Path) -> int:
    from ..gui import main as tk_main
    tk_main(app_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    app_dir = Path(__file__).resolve().parents[3]
    if "--tk" in argv:
        return run_tk(app_dir)
    return run(app_dir)
