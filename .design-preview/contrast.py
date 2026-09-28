"""WCAG contrast for the token pairs that carry text, across all six style/theme combos.

Reads the real tokens.css so the numbers cannot drift from the file.

Run:  python .design-preview/contrast.py
"""
import re
from pathlib import Path

CSS = Path("src/ai_code_engineer/webapp/static/tokens.css").read_text(encoding="utf-8")

SELECT = re.compile(r':root(?:\[data-style="([a-z]+)"\]\[data-theme="([a-z]+)"\])?\s*\{(.*?)\}', re.S)
VAR = re.compile(r'--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})')

PAIRS = [
    ("ink", "bg", "body text"),
    ("ink-2", "bg", "secondary text"),
    ("faint", "bg", "muted text / timestamps"),
    ("faint", "side", "sidebar labels"),
    ("faint", "panel", "card labels"),
    ("accent-ink", "accent", "button label on primary"),
    ("ok-ink", "ok-bg", "pass pill"),
    ("warn-ink", "warn-bg", "warn pill"),
    ("bad-ink", "bad-bg", "fail pill"),
    ("add-ink", "add", "diff added line"),
    ("del-ink", "del", "diff removed line"),
    ("kbd-ink", "kbd", "shortcut chip"),
]


def rgb(hexc):
    value = hexc.lstrip("#")
    out = []
    for channel in (value[0:2], value[2:4], value[4:6]):
        linear = int(channel, 16) / 255
        out.append(linear / 12.92 if linear <= 0.03928 else ((linear + 0.055) / 1.055) ** 2.4)
    return out


def luminance(color):
    red, green, blue = color
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def ratio(one, two):
    a, b = sorted((luminance(rgb(one)), luminance(rgb(two))), reverse=True)
    return (a + 0.05) / (b + 0.05)


def grade(value, large=False):
    need = 3.0 if large else 4.5
    return "AA" if value >= need else ("AA-large" if value >= 3.0 else "FAIL")


combos = {}
for style, theme, body in SELECT.findall(CSS):
    tokens = dict(VAR.findall(body))
    if not tokens:
        continue
    combos[(style or "claude", theme or "light")] = tokens

worst = []
for key in sorted(combos):
    tokens = combos[key]
    print(f"\n=== {key[0]} / {key[1]} ===")
    for fore, back, label in PAIRS:
        if fore not in tokens or back not in tokens:
            continue
        value = ratio(tokens[fore], tokens[back])
        verdict = grade(value)
        line = f"  {value:5.2f}  {verdict:9} {label:28} ({fore} on {back})"
        print(line)
        if verdict == "FAIL":
            worst.append((key, label, round(value, 2)))

print("\n" + "=" * 60)
print("FAILING PAIRS:", len(worst))
for combo, label, value in sorted(worst, key=lambda row: row[2]):
    print(f"  {combo[0]:7}/{combo[1]:5} {value:5.2f}  {label}")
