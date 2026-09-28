"""Propose contrast-safe values for the tokens tools/check_contrast.py reports as failing.

Each token keeps its hue and saturation: only lightness moves, and only as far as it must to
clear the target ratio against every surface the token is actually used on. Candidates are tried
in order of smallest change from the current value, in both directions, so the result is the
least visual damage that still passes rather than whatever a search order happened to reach.

Run:  python .design-preview/solve_contrast.py         # print proposals
      python .design-preview/solve_contrast.py --apply # rewrite tokens.css
"""
from __future__ import annotations

import colorsys
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import check_contrast as audit  # noqa: E402

TARGET = 4.6                    # a hair above AA so a later nudge does not re-break it
CSS_PATH = audit.CSS

# token -> surfaces it must be readable on. A "#..." entry is a fixed ink painted on the token,
# which is how the sidebar avatar (white on --hue) is expressed.
CONSTRAINTS = {
    "faint":       ["bg", "side", "panel"],
    "accent-text": ["bg", "panel"],
    "kbd-ink":     ["kbd"],
    "pic-ink":     ["pic"],
    "hue":         ["#ffffff"],
    "pop-ink":     ["pop"],
    "accent-ink":  ["accent"],
}


def surface_rgb(tokens, surface):
    if surface.startswith("#"):
        return audit.channels(surface)[:3]
    return audit.resolve(tokens, surface)


def ratio(colour, back) -> float:
    high, low = sorted((audit.luminance(colour), audit.luminance(back)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def best(tokens, name, surfaces):
    """Return the closest lightness-only variant of tokens[name] that clears every surface."""
    current = tokens[name].strip()
    backs = [surface_rgb(tokens, s) for s in surfaces]
    if any(back is None for back in backs):
        return None

    def ok(value):
        colour = audit.channels(value)[:3]
        return all(ratio(colour, back) >= TARGET for back in backs)

    if ok(current):
        return None
    if name == "accent-ink":
        # A filled button may flip to the theme's own ink instead of tinting the accent away.
        for candidate in (tokens["ink"], tokens["bg"]):
            if ok(candidate.strip()):
                return candidate.strip()
        return None
    hue, light, saturation = colorsys.rgb_to_hls(
        *(channel / 255 for channel in audit.channels(current)[:3]))
    for step in range(1, 700):
        for delta in (-step, step):
            red, green, blue = colorsys.hls_to_rgb(
                hue, min(max(light + delta / 1000.0, 0.0), 1.0), saturation)
            candidate = "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))
            if ok(candidate):
                return candidate
    return None


def main() -> int:
    apply = "--apply" in sys.argv
    text = CSS_PATH.read_text(encoding="utf-8")
    proposals: list[str] = []

    def rewrite(match):
        head, body = match.group(1), match.group(2)
        tokens = dict(audit.TOKEN.findall(body))
        if "accent" not in tokens:
            return match.group(0)
        for name, surfaces in CONSTRAINTS.items():
            if name in tokens:
                working = tokens
            elif name == "accent-text":
                working = dict(tokens, **{"accent-text": tokens["accent"]})
            else:
                continue
            value = best(working, name, surfaces)
            if not value or value == working[name].strip():
                continue
            if "--" + name + ":" in body:
                body = re.sub(r"(--" + name + r":\s*)[^;]+(;)",
                              lambda m, value=value: m.group(1) + value + m.group(2), body, count=1)
                proposals.append("{:<44} --{:<12} -> {}".format(head, name, value))
            else:
                body = re.sub(r"(--accent-ink:\s*[^;]+;)",
                              lambda m, name=name, value=value:
                              m.group(1) + " --" + name + ": " + value + ";", body, count=1)
                proposals.append("{:<44} --{:<12} = {} (new)".format(head, name, value))
            tokens[name] = value
        return ":root" + head + " {" + body + "\n}"

    out = re.sub(r':root((?:\[data-[a-z]+="[a-z]+"\])*)\s*\{(.*?)\n\}', rewrite, text, flags=re.S)
    for line in proposals:
        print(line)
    if not proposals:
        print("nothing to change")
        return 0
    if not apply:
        print("\n{} change(s). Re-run with --apply to write them.".format(len(proposals)))
        return 0
    CSS_PATH.write_text(out, encoding="utf-8")
    print("\nwrote " + str(CSS_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
