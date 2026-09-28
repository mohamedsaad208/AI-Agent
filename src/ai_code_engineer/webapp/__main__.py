"""Run the UI server: python -m ai_code_engineer.webapp [--fake] [--no-browser] [--port N]"""
from __future__ import annotations

import argparse
import time
import webbrowser
from pathlib import Path

from .server import serve


def build(fake: bool):
    if fake:
        from .fake import FakeController
        return FakeController()
    from .controller import AgentController
    return AgentController(Path(__file__).resolve().parents[3])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_code_engineer.webapp")
    parser.add_argument("--fake", action="store_true", help="drive the UI with scripted data, no engine")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    controller = build(args.fake)
    server, url, _token = serve(controller, port=args.port)
    if not args.fake:
        controller.check_setup()
    print("AI Code Engineer UI -> " + url)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
