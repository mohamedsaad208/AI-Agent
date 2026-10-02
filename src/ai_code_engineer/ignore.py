"""One owner for the question "is this path worth looking at".

Three lists used to answer it — `workspace.BLOCKED_PARTS`, `runner.PROJECT_SKIP`,
`controller.SKIP_DIRS` — copied from each other by hand, so they drifted: the build directories the
planner refused were hidden in the picker for a different reason, and nothing in any of them knew that
this tool writes its own chat transcripts and its own preview folder into the workspace it is asked to
index. Measured on this repository before the split was closed: 400 of 591 files the map walked were
`.agent-webview/`, `.agent-chats/` and `.design-preview/`, and the repository map came back at 12 206
characters against a 12 000 budget — the model's view of this project was mostly this project's own
output.

The three questions are genuinely different, so this module keeps three compositions over one set of
names. What was wrong was not the difference; it was that a name had to be edited in three places to
have an effect in one.

Nothing here is a security boundary on its own. `refused_dir` is the predicate the read and write gates
use, and it must stay a **superset** of what `BLOCKED_PARTS` held, so moving a build directory from the
policy list to the tidiness list cannot quietly make it readable.
"""
from __future__ import annotations

import re
from pathlib import Path

# Credentials, VCS internals, and the surfaces an agent could leave a hook on. Refusing these is a
# policy decision, not a tidiness one, and it does not change because a repository asked.
PROTECTED_DIRS = {
    ".git", ".env", ".ssh", ".aws", ".azure", ".gnupg", ".codex", ".agents", ".agent-runs",
    ".agent-projects.json", ".agent-modes.json", ".agent-plans", ".agent-memory",
    # The signed rows and the key that signs them. The signature is what actually refuses a row a model
    # wrote, but a file the operator set with their own hands is not the model's to rewrite either.
    ".agent-overrides.json", ".agent-overrides.key",
    # The folder's answers to the policy table, for the same reason: a proposal that can edit the rules
    # it is being checked against is not being checked against anything. Its real home is beside the
    # app's own records, outside every approved folder; this entry is the belt.
    ".agent-permissions.json",
    ".venv", "venv", ".gradle", ".m2",
    ".idea",
}
# Matches files that *are* credentials, not source that merely names one — `token.json` yes,
# `JwtTokenProvider.java` no. The pattern is the one `workspace.py` always used, moved rather than
# copied so the read gate and the walk cannot disagree about what `.env` means.
SECRET_NAME = re.compile(
    r"(^\.env($|\.)|(^|[-_.])(credentials?|secrets?|passwords?|tokens?|keys?|private[-_]?key)s?($|[-_.]))",
    re.I)
# NTFS keeps an 8.3 alias for every name it had to shorten, and the alias opens the same bytes.
# "TOKEN~1.JSON" would otherwise be a shape the checks above never saw, so it is refused by pattern.
SHORT_NAME = re.compile(r"~\d{1,2}(?:\.[^.]+)?$", re.I)

# Built, vendored, or written by this tool itself. Out of the walk, because a map full of them is how a
# real source file gets truncated out of the model's context. Not out of reach: a caller that names one
# explicitly can still read it — these are noise, not secrets.
GENERATED_DIRS = {
    "node_modules", "target", "build", "dist", "site-packages", "eggs", "bower_components",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".next", ".nuxt",
    ".turbo", ".sass-cache", "coverage", "htmlcov", ".terraform",
    # The tool's own output. `.agent-runs` is protected above; these three were in no list at all.
    ".agent-webview", ".agent-chats", ".design-preview",
}
# A directory whose name says it holds generated code: Maven's `generated-sources`, protobuf's `gen`,
# a hand-rolled `src/generated`. Matched against the whole segment, so `chargeback-api` is safe.
GENERATED_SEGMENT = re.compile(r"^(gen|generated|generated[-_].*|.*[-_]generated)$", re.I)
# Files that are a minified or compiled form of something else. Matched against the whole name, so
# `app.min.js` goes and `admin.js` stays.
GENERATED_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".lock", ".map", ".iml", ".g.cs",
                      ".g.i.cs", ".designer.cs", ".generated.cs", "_pb2.py", "_pb2_grpc.py")

# "Could a build be run in this folder?" is a different question, with one extra honest answer: a .NET
# tree keeps output in `bin`, a Rails app keeps executables there too, so those names are a reason not
# to *offer* a folder and not a reason to refuse a file the operator named.
PROJECT_EXTRA_DIRS = {"out", "bin", "obj", "vendor", "packages", "deps", "_build", ".direnv", ".mvn"}
# The folder picker walks a drive root, so it needs a second reason to hide a folder: it is the
# machine, not the workspace. These are `controller.SKIP_DIRS` verbatim.
OS_TREES = {"program files", "windows", "system32", "appdata", "downloads", "$recycle.bin",
            "programdata", "perflogs", "recovery", "windowsapps", "drvstore"}


def protected_dir(part: str) -> bool:
    return str(part).casefold() in PROTECTED_DIRS


def credential(part: str) -> bool:
    return bool(SECRET_NAME.search(str(part)))


def alias(part: str) -> bool:
    return bool(SHORT_NAME.search(str(part)))


def generated_dir(part: str) -> bool:
    folded = str(part).casefold()
    return folded in GENERATED_DIRS or bool(GENERATED_SEGMENT.match(folded))


def generated_file(name: str) -> bool:
    return str(name).casefold().endswith(GENERATED_SUFFIXES)


def refused_dir(part: str) -> bool:
    """A directory the walk never enters and the path gate never resolves through."""
    return protected_dir(part) or generated_dir(part) or credential(part) or alias(part)


def project_dir(part: str) -> bool:
    """`refused_dir` plus the output names that only disqualify a folder as a project root."""
    return refused_dir(part) or str(part).casefold() in PROJECT_EXTRA_DIRS


def picker_dir(part: str) -> bool:
    """`refused_dir` plus the operating system's own trees."""
    return refused_dir(part) or str(part).casefold() in OS_TREES


def _segment(pattern: str) -> str:
    """One path segment of a gitignore pattern to regex: `*` and `?` stop at a slash."""
    out = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            close = pattern.find("]", index + 1)
            if close < 0:
                out.append(re.escape(char))
            else:
                inner = pattern[index + 1:close]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                out.append("[" + inner + "]")
                index = close
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


class Rules:
    """A parsed `.gitignore`: last match wins, and an ignored directory takes its children with it.

    Implemented is git's syntax as it is actually used — `*` and `?` that stop at a slash, `**` that
    crosses one, a trailing `/` for directories only, a leading or inner `/` to anchor, `[abc]` and
    `[!abc]` classes, `#` comments, and `!` to put something back — plus a nested `.gitignore` for every
    directory the walk reaches. Not implemented: git's own escapes and character-class edge cases,
    `.git/info/exclude`, and the global `core.excludesFile`, all of which live outside the folder this
    tool was handed and would make a granted folder's map depend on this machine's configuration.

    Git is never consulted. `git_program()` may return `None` and a granted folder is often not a
    repository at all, so the parser is the rule rather than the fallback — which is the difference
    between a map that is stable and one that changes when a checkout of git appears.
    """

    def __init__(self, rows: list[tuple[bool, re.Pattern, bool]] | None = None,
                 origin: str = "") -> None:
        self.rows = rows or []
        self.origin = origin

    def __bool__(self) -> bool:
        return bool(self.rows)

    def child(self, text: str) -> "Rules":
        """These rules plus one directory's own file, which refines them.

        A nested `.gitignore` wins over the one above it, and git decides that by trying patterns last
        first, so the child's lines go on the end of the list. The parent's rows are kept whole rather
        than mutated, which is also what makes a subdirectory's rules stop applying when the walk leaves
        it: each directory carries only the chain that reaches it.
        """
        return Rules(self.rows + self.parse(text), self.origin)

    @staticmethod
    def compile_pattern(pattern: str) -> re.Pattern | None:
        body = pattern.lstrip("/")
        # An inner slash anchors the pattern to the directory the file sat in; without one it may
        # match at any depth. `logs/` alone has no inner slash, so it matches every `logs` directory.
        anchored = "/" in body.rstrip("/")
        pieces: list[str] = []
        segments = body.split("/")
        for position, segment in enumerate(segments):
            last = position == len(segments) - 1
            if not segment:
                continue
            if segment == "**":
                # `**/x` reaches any depth, `x/**` takes everything below, `a/**/b` also matches `a/b`.
                pieces.append(".*" if last else "(?:[^/]*/)*")
                continue
            pieces.append(_segment(segment))
            if not last:
                pieces.append("/")
        stem = "".join(pieces)
        if not stem:
            return None
        return re.compile(("" if anchored else "^(?:[^/]*/)*") + stem + "$")

    @classmethod
    def parse(cls, text: str) -> list[tuple[bool, re.Pattern, bool]]:
        rows = []
        for line in str(text or "").splitlines():
            pattern = line.rstrip("\r")
            if not pattern.strip() or pattern.lstrip().startswith("#"):
                continue
            pattern = pattern.strip()
            negate = pattern.startswith("!")
            if negate:
                pattern = pattern[1:].strip()
            directory_only = pattern.endswith("/")
            pattern = pattern.rstrip("/")
            if not pattern:
                continue
            compiled = cls.compile_pattern(pattern)
            if compiled is not None:
                rows.append((negate, compiled, directory_only))
        return rows

    @classmethod
    def load(cls, root: Path, limit_bytes: int = 64 * 1024) -> "Rules":
        """The root rules, or none at all: a file that cannot be read is no rules, not a failed task."""
        try:
            path = Path(root) / ".gitignore"
            if not path.is_file() or path.stat().st_size > limit_bytes:
                return cls()
            return cls(cls.parse(path.read_text(encoding="utf-8", errors="replace")), ".gitignore")
        except OSError:
            return cls()

    def _matched(self, path: str, is_dir: bool) -> bool:
        """One path, all the rules, last match wins — which is git's whole precedence model."""
        decided = False
        for negate, pattern, directory_only in self.rows:
            if directory_only and not is_dir:
                continue
            if pattern.match(path):
                decided = not negate
        return decided

    def ignores(self, relative: str, is_dir: bool) -> bool:
        """Whether git would call this path uninteresting.

        An excluded directory takes everything under it, and no `!` line inside it can put a file back:
        git says so explicitly ("It is not possible to re-include a file if a parent directory of that
        file is excluded"), and a tool that disagreed with that would list files a person cannot add to
        a commit. So the ancestors are decided first, and only a path whose whole chain survived gets
        its own last-match answer.
        """
        parts = [p for p in str(relative).replace("\\", "/").split("/") if p]
        for depth in range(1, len(parts)):
            if self._matched("/".join(parts[:depth]), True):
                return True
        return self._matched("/".join(parts), is_dir)


def walk_prune(dirs, rules: Rules) -> tuple[list[str], int, int]:
    """The directories to descend into, and how many were left out for which reason.

    Shared by every caller that walks a project, so the map, the search and the count the composer
    prints cannot each disagree about what a folder holds. The counts travel out with the decision
    because they are the only way a reader can tell "there is nothing here" from "something was hidden"
    — a silent filter turns a missing file into a wrong answer from a model that believes the map.
    """
    kept, generated, ignored = [], 0, 0
    for name in dirs:
        if generated_dir(name):
            generated += 1
            continue
        if rules and rules.ignores(name, True):
            ignored += 1
            continue
        if protected_dir(name) or credential(name) or alias(name):
            continue
        kept.append(name)
    return sorted(kept), generated, ignored
