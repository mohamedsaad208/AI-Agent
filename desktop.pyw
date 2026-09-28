"""Double-click entry point; startup failures remain visible without a console."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    from ai_code_engineer.webapp import launch

    code = 0
    try:
        code = launch.main(sys.argv[1:])
    except SystemExit:
        raise
    except Exception:
        # The web shell needs a Chromium browser and a free port; the Tk window needs
        # neither, so it is the fallback rather than the error message.
        try:
            code = launch.run_tk(ROOT)
        except Exception:
            # Deliberately omit exception details: GUI fields can include API credentials.
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None, "The application could not start. Check that Python 3.11+ is installed, "
                    "then run Run-Agent.", "AI Code Engineer", 0x10)
            except Exception:
                print("The application could not start. Check that Python 3.11+ is installed.", file=sys.stderr)
            code = 1
    raise SystemExit(code)
