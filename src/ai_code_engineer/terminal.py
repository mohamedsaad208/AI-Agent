"""Terminal capability detection (Release 5 - Task 5.2).

One place answers the question every renderer otherwise guesses at: what can this
stream actually show? A pipe and a CI job may both look like a terminal to ``rich``
(GitHub Actions hands out a pty), yet an in-place ``Live`` redraw there writes thousands
of half-painted frames into a log file. ``NO_COLOR`` is honoured as the standard defines
it — any non-empty value means no colour — unless the caller explicitly asked for colour
with ``FORCE_COLOR``, which wins. A screen narrower than 80 columns gets the compact
renderings instead of the panel and table shapes that wrap into unreadable ribbons.

The module decides nothing on its own at print time: callers take ``capabilities()``
(or a single predicate) and choose. Everything reads injectable streams and env maps so
the answers are testable without a device.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Optional

DEFAULT_WIDTH = 80
NARROW_BELOW = 80

# Variables each of the common CI systems sets in every job. Checked for a truthy value,
# because several of them also appear with "false"-like strings in developer shells.
CI_VARIABLES = (
    "CI", "CONTINUOUS_INTEGRATION", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL",
    "BUILD_ID", "BUILD_NUMBER", "TF_BUILD", "BUILD_BUILDID", "CIRCLECI", "TRAVIS",
    "BITBUCKET_BUILD_NUMBER", "SYSTEM_COLLECTIONURI", "DRONE", "CODEBUILD_BUILD_ID",
)

_FALSY = ("", "0", "false", "no", "off")


def _environ(env: Optional[Mapping[str, str]] = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _truthy(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() not in _FALSY


def is_no_color(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether colour must be dropped from this stream's output."""
    where = _environ(env)
    if _truthy(where.get("FORCE_COLOR")):
        return False
    if str(where.get("TERM") or "").strip().lower() in ("dumb", "unknown"):
        return True
    return _truthy(where.get("NO_COLOR"))


def is_ci(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether this process is running inside a continuous-integration job."""
    where = _environ(env)
    return any(_truthy(where.get(name)) for name in CI_VARIABLES)


def is_tty(stdin: Any = None, stdout: Any = None) -> bool:
    """Whether someone is actually typing and watching, on both ends."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        return bool(stdin.isatty() and stdout.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def terminal_width(env: Optional[Mapping[str, str]] = None, stdout: Any = None) -> int:
    """Columns available for rendering. COLUMNS wins, then the device, then 80."""
    for candidate in (_environ(env).get("COLUMNS"), getattr(stdout, "width", None)):
        try:
            width = int(candidate)
        except (TypeError, ValueError):
            continue
        if width > 0:
            return width
    try:
        width = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
    except OSError:
        width = DEFAULT_WIDTH
    return width if width and width > 0 else DEFAULT_WIDTH


@dataclass(frozen=True)
class Capabilities:
    """What this terminal can be asked to show, as one decided snapshot.

    ``color`` says nothing about whether the stream is a terminal: ``rich`` pairs the
    two itself. ``spinners`` is the in-place-redraw permission — true only when a real
    screen is attached and no CI job is recording the frames.
    """

    interactive: bool
    color: bool
    spinners: bool
    width: int
    narrow: bool


def capabilities(env: Optional[Mapping[str, str]] = None,
                 stdin: Any = None, stdout: Any = None) -> Capabilities:
    where = _environ(env)
    interactive = is_tty(stdin, stdout)
    width = terminal_width(where, stdout)
    return Capabilities(
        interactive=interactive,
        color=not is_no_color(where),
        spinners=interactive and not is_ci(where),
        width=width,
        narrow=width < NARROW_BELOW,
    )
