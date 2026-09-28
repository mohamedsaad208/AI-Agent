#!/usr/bin/env bash
# Captures the running UI at a given size for design review.
# usage: shot.sh <base-url-with-token> <out-stem> [extra query]...
set -u
BASE="$1"; OUT="$2"; shift 2
CHROME="/c/Program Files/Google/Chrome/Application/chrome.exe"
[ -x "$CHROME" ] || CHROME="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
for q in "$@"; do
  name="${q//[^a-z0-9]/-}"
  "$CHROME" --headless=new --disable-gpu --no-first-run --hide-scrollbars \
    --force-device-scale-factor=1.5 --window-size=1440,900 --timeout=9000 \
    --user-data-dir="D:/AI/AI-Agent/.design-preview/.chrome" \
    --screenshot="D:\\AI\\AI-Agent\\.design-preview\\${OUT}-${name}.png" \
    "${BASE}&${q}" >/dev/null 2>&1
done
rm -rf "D:/AI/AI-Agent/.design-preview/.chrome"
ls -la "D:/AI/AI-Agent/.design-preview" | grep "${OUT}-" || echo "no shots produced"
