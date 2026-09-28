"""WCAG contrast for every token pair that carries text, across all style/theme combinations.

Reads the real tokens.css, so a number in the docs cannot drift from the file.

Two rules this enforces, because both were quietly wrong when measured by eye:

* Every pair below is small text. The smallest is an 8.5px avatar mark and the largest is a
  13px button label, so none of them qualifies for the 3.0:1 large-text allowance and all of
  them are graded against 4.5:1.
* A pair whose token does not exist is reported and fails the run. A typo in a token name
  otherwise drops that measurement silently and leaves the audit looking clean -- that is how
  `--kbd`, an rgba() overlay, went unmeasured through a whole "0 failures" report.

Translucent tokens (rgba) have no contrast of their own, so they are composited over the
surface named in OVERLAY_BASE before being measured, the way the browser paints them.

Run:  python tools/check_contrast.py
Exit: 0 clean, 1 if any pair is below AA or a named token is missing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "src/ai_code_engineer/webapp/static/tokens.css"
AA = 4.5

BLOCK = re.compile(r':root((?:\[data-[a-z]+="[a-z]+"\])*)\s*\{(.*?)\n\}', re.S)
ATTR = re.compile(r'\[data-[a-z]+="([a-z]+)"\]')
TOKEN = re.compile(r'--([a-z0-9-]+):\s*([^;]+);')
RGB = re.compile(r'rgba?\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:[,/]\s*([\d.]+))?\s*\)'
                 r'|rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+))?\s*\)')

# (foreground, background, label). A spec is a token name, or a literal colour in "#rgb" form.
PAIRS = [
    ("ink", "bg", "body text"),
    ("ink-2", "bg", "secondary text"),
    ("faint", "bg", "muted text / timestamps"),
    ("faint", "side", "sidebar labels"),
    ("faint", "panel", "card labels"),
    ("accent-ink", "accent", "primary button label"),
    ("accent-text", "bg", "link / active pill on page"),
    ("accent-text", "panel", "link / active pill on card"),
    ("ok-ink", "ok-bg", "pass pill"),
    ("warn-ink", "warn-bg", "warn pill"),
    ("bad-ink", "bad-bg", "fail pill"),
    ("add-ink", "add", "diff added line"),
    ("del-ink", "del", "diff removed line"),
    ("kbd-ink", "kbd", "shortcut chip"),
    ("pop-ink", "pop", "tooltip / toast body"),
    ("me-ink", "me", "own message bubble"),
    ("pic-ink", "pic", "project avatar mark"),
    ("on-ok", "ok", "plan step check mark"),
    # UI 3.1's chips and rail rows sit on the field/panel surfaces rather than on a pill, so they
    # are their own pairs — measured, not assumed to inherit the pill's numbers.
    ("ink-2", "field", "file chip label"),
    ("faint", "field", "file chip view action"),
    ("ink-2", "panel", "rail file row"),
]

# Not audited here: the per-project chip in the sidebar is coloured from the folder name by
# avatar() in app.js, not by a token. Its lightness is pinned to an end of the scale so that the
# ink returned with it clears AA for all 360 hues -- at the mid lightness it used to sit at, a
# name hashing to yellow reached 2.5:1 under white and no ink passed.

# Translucent overlays are painted on this surface, so that is what they must be measured on.
OVERLAY_BASE = {"kbd": "panel", "ok-bg": "panel", "warn-bg": "panel", "bad-bg": "panel",
                "add": "panel", "del": "panel", "me": "bg", "pic": "bg"}


def channels(value: str) -> tuple[float, float, float, float]:
    """Return (r, g, b, a) with 0-255 colour and a 0-1 alpha, for either token syntax."""
    value = value.strip()
    if value.startswith("#"):
        digits = value.lstrip("#")
        if len(digits) == 3:
            digits = "".join(char * 2 for char in digits)
        if len(digits) not in (6, 8):
            raise ValueError("Unreadable colour: " + value)
        parts = [int(digits[i:i + 2], 16) for i in (0, 2, 4)]
        alpha = 1.0 if len(digits) == 6 else int(digits[6:8], 16) / 255
        return (*parts, alpha)
    match = RGB.match(value)
    if match is None:
        raise ValueError("Unreadable colour: " + value)
    groups = [group for group in match.groups() if group is not None]
    red, green, blue = (float(value) for value in groups[:3])
    alpha = 1.0 if len(groups) < 4 else float(groups[3])
    return red, green, blue, alpha


def composite(top: str, bottom: str) -> tuple[float, float, float]:
    red, green, blue, alpha = channels(top)
    back = channels(bottom)
    def blend(near, far):
        return near * alpha + far * (1 - alpha)
    return blend(red, back[0]), blend(green, back[1]), blend(blue, back[2])


def luminance(color) -> float:
    def channel(value):
        linear = value / 255
        return linear / 12.92 if linear <= 0.03928 else ((linear + 0.055) / 1.055) ** 2.4
    red, green, blue = (channel(value) for value in color)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def ratio(one, two) -> float:
    high, low = sorted((luminance(one), luminance(two)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def read_blocks(text: str) -> dict[tuple[str, str], dict[str, str]]:
    combos: dict[tuple[str, str], dict[str, str]] = {}
    for attributes, body in BLOCK.findall(text):
        names = ATTR.findall(attributes)
        # :root alone is the default family and theme, so the labels have to be filled in.
        style = names[0] if len(names) > 1 else "claude"
        theme = names[1] if len(names) > 1 else ("dark" if names else "light")
        combos[(style, theme)] = {name: value for name, value in TOKEN.findall(body)}
    return combos


def resolve(tokens: dict[str, str], spec: str) -> tuple | None:
    """Return an (r, g, b) triple for a token name or a literal colour, or None if unresolvable."""
    if spec.startswith("#"):
        return channels(spec)[:3]
    value = tokens.get(spec)
    if value is None:
        return None
    if not value.strip().startswith("#"):
        base = OVERLAY_BASE.get(spec)
        backdrop = tokens.get(base) if base else None
        if backdrop is None:
            return None
        return composite(value, backdrop)
    return channels(value)[:3]


def main() -> int:
    if not CSS.is_file():
        print("Cannot find " + str(CSS))
        return 2
    combos = read_blocks(CSS.read_text(encoding="utf-8"))
    problems: list[str] = []
    measured = 0
    lowest = (99.0, "")
    for key in sorted(combos):
        tokens = combos[key]
        print("\n=== {} / {} ===".format(*key))
        for fore, back, label in PAIRS:
            front, behind = resolve(tokens, fore), resolve(tokens, back)
            if front is None or behind is None:
                missing = fore if front is None else back
                print("    --   MISSING  {:<28} ({} is undefined)".format(label, missing))
                problems.append("{} {}: undefined token --{}".format(key[0], key[1], missing))
                continue
            value = ratio(front, behind)
            verdict = "AA" if value >= AA else "FAIL"
            shown = fore if not fore.startswith("#") else "white"
            print("  {:5.2f}  {:<8} {:<28} ({} on {})".format(value, verdict, label, shown, back))
            measured += 1
            if value < lowest[0]:
                lowest = (value, "{} {}: {} ({} on {})".format(key[0], key[1], label, shown, back))
            if verdict == "FAIL":
                problems.append("{} {}: {:.2f} -- {}".format(key[0], key[1], value, label))

    print("\n" + "=" * 64)
    print("{} combinations, {} pairs measured".format(len(combos), measured))
    if lowest[1]:
        print("tightest: " + lowest[1])
    if not problems:
        print("PASS: every text pair reaches {} against its own background.".format(AA))
        return 0
    print("PROBLEMS:", len(problems))
    for line in sorted(problems):
        print("  " + line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
