"""Conservative file access. Not a replacement for OS sandboxing."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile

from . import runner, symbols
from .errors import MissingFileError, PolicyError

MAX_FILE_BYTES = 128 * 1024
TEXT_SUFFIXES = {".py", ".java", ".kt", ".kts", ".xml", ".gradle", ".md", ".txt",
                 ".json", ".yaml", ".yml", ".toml", ".properties", ".sql", ".js",
                 ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".css", ".html",
                 ".sh", ".bat", ".cmd"}
# The suffix gate is a text-or-binary check, and a few real text files have no suffix at all.
# Refusing them does not protect anything: it stops an agent that scaffolds projects from
# writing the one file that keeps its own `target/` and `.venv/` out of the first commit.
# Credentials stay refused by the gates that run before this one (BLOCKED_PARTS, SECRET_NAME),
# and none of these names is in AGENT_RULE_FILES, so none is a hook an agent could leave behind.
TEXT_NAMES = {".gitignore", ".gitattributes", ".editorconfig", ".dockerignore",
              "dockerfile", "makefile", "jenkinsfile", "license", "readme"}
BLOCKED_PARTS = {".git", ".env", ".ssh", ".aws", ".azure", ".gnupg", ".codex",
                 ".agents", ".agent-runs", ".agent-projects.json", ".agent-plans", ".agent-memory",
                 ".venv", "venv", "node_modules", "target",
                 "build", "dist", "__pycache__", ".idea", ".gradle", ".m2"}
# Matches files that *are* credentials (.env, token.json, aws-secret.txt) rather than
# source files that merely contain the word in an identifier (JwtTokenProvider.java).
SECRET_NAME = re.compile(
    r"(^\.env($|\.)|(^|[-_.])(credentials?|secrets?|passwords?|tokens?|keys?|private[-_]?key)s?($|[-_.]))",
    re.I)
# NTFS keeps an 8.3 alias for every name whose long form it had to shorten, and opening
# the alias reads the same bytes. "TOKEN~1.JSON" would otherwise be a name the checks
# above never saw, so it is rejected by shape before it is ever resolved.
SHORT_NAME = re.compile(r"~\d{1,2}(?:\.[^.]+)?$", re.I)
# Instruction, rules and auto-launch config for the agents and editors that may open this
# folder next. A model that writes one gains a hook that outlives the session — read back
# as instructions, or executed on open — so none of them is ever writable. They are readable
# only where the suffix itself is one of the text kinds above: `mcp.json` yes, `cursorrules`
# not, and that asymmetry is the suffix gate doing its job, not this list's promise.
AGENT_RULE_FILES = {"agents.md", "agent.md", "skill.md", "skills.md", "qoder.md",
                    "claude.md", "cursor.md", "cursorrules", ".cursorrules",
                    "windsurfrules", ".windsurfrules", "gemini.md", "devin.md",
                    "aider.conf.yml", "codex.md",
                    "copilot-instructions.md", "mcp.json", ".mcp.json", "opencode.json"}
# Directories whose contents tools act on: .vscode/tasks.json runs programs,
# .mvn/jvm.config hands JVM options (including -javaagent) to every later build. Every one of
# these names begins with a dot, because that is what makes it a tool's own folder rather than
# a package somebody wrote — see ROOT_POLICY_DIRS for the plain-spelled half of the rule.
AGENT_CONFIG_DIRS = {".github", ".gitlab", ".circleci", ".vscode", ".zed", ".trae",
                     ".qoder", ".claude", ".cursor", ".gemini", ".devin", ".aider",
                     ".factory", ".continue", ".junie", ".kiro", ".opencode", ".agent",
                     ".githooks", ".mvn"}
# An agent's own policy and skill folders are spelled plainly and sit at the top of the
# workspace. Anywhere deeper they are somebody's package: `com.acme.policies` is source code,
# and refusing to write it would refuse the task.
ROOT_POLICY_DIRS = {"policies", "skills"}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# Parsed declarations for files that have not changed since the last map, keyed by the
# resolved root so two projects never share rows. Advisory only: the map is context for a
# model, and the bytes on disk stay the authority for every hash check.
INDEX_CACHE: dict[tuple[str, str], tuple[int, int, dict]] = {}
INDEX_CACHE_LIMIT = symbols.MAX_FILES


def clear_index_cache(root: Path | str | None = None) -> int:
    """Forget cached rows, for one project or for all of them. Returns how many went."""
    key = str(Path(root).resolve()) if root is not None else None
    doomed = [item for item in INDEX_CACHE if key is None or item[0] == key]
    for item in doomed:
        del INDEX_CACHE[item]
    return len(doomed)


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def ensure_project_dir(path: Path | str) -> Path:
    """Create the empty folder the user just chose for a new project.

    Only a missing or empty folder qualifies, so this can never become a way to
    start writing into an existing project without looking at it first.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        raise PolicyError("Choose an absolute project folder.")
    if candidate.parent == candidate or len(str(candidate).rstrip("\\/")) <= 3:
        raise PolicyError("Choose a folder inside a drive, not the drive itself.")
    if candidate.exists():
        if not candidate.is_dir():
            raise PolicyError("That path is a file. Choose a folder instead.")
        if any(candidate.iterdir()):
            raise PolicyError("That folder already contains files. Use Browse… to open it as an existing project.")
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PolicyError("Could not create the project folder: " + str(exc)[:160]) from None
    return candidate.resolve()


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise PolicyError("Workspace must be a directory.")
        self._cache_key = str(self.root)

    def path(self, relative: str, *, writable: bool = False) -> Path:
        if not isinstance(relative, str) or not relative or len(relative) > 400:
            raise PolicyError("Invalid workspace path.")
        if "\\" in relative or ":" in relative or "\x00" in relative:
            raise PolicyError("Use a relative path with forward slashes.")
        parts = relative.split("/")
        if PurePosixPath(relative).is_absolute() or any(
            p in {"", ".", ".."} or p.rstrip(" .") != p for p in parts
        ):
            raise PolicyError("Absolute paths, traversal and ambiguous paths are blocked.")
        if any(p.casefold() in BLOCKED_PARTS or SECRET_NAME.search(p) or SHORT_NAME.search(p)
               for p in parts):
            raise PolicyError("Protected path.")
        if any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in parts):
            raise PolicyError("Device paths are blocked.")
        current = self.root
        for part in parts:
            current = current / part
            if current.exists() or current.is_symlink():
                if is_link(current):
                    raise PolicyError("Symlinks and reparse points are blocked.")
        resolved = current.resolve()
        if not resolved.is_relative_to(self.root):
            raise PolicyError("Path escapes workspace.")
        # The name the model typed and the name NTFS finally opens are not always the
        # same (case, aliases, mount points), so the resolved form is checked too.
        if any(p.casefold() in BLOCKED_PARTS or SECRET_NAME.search(p)
               for p in resolved.relative_to(self.root).parts):
            raise PolicyError("Protected path.")
        if (current.suffix.lower() not in TEXT_SUFFIXES
                and current.name.casefold() not in TEXT_NAMES):
            # The name goes into the sentence because the model wrote it seconds earlier and is
            # about to be told only that something was refused. Without it, a small model repeats
            # the same action — measured twice in the ecommerce run, where two 5-minute turns
            # ended BLOCKED on a path this gate had rejected by spelling.
            raise PolicyError("Only supported text files are accessible: " + relative)
        if writable and (current.name.casefold() in AGENT_RULE_FILES or
                         any(p.casefold() in AGENT_CONFIG_DIRS for p in parts) or
                         parts[0].casefold() in ROOT_POLICY_DIRS):
            raise PolicyError("Instructions and policy files are read-only.")
        return current

    def read(self, relative: str) -> dict:
        path = self.path(relative)
        try:
            info = path.stat()
        except FileNotFoundError:
            raise MissingFileError("File does not exist: " + relative) from None
        if not stat.S_ISREG(info.st_mode):
            raise PolicyError("Path is not a regular file: " + relative)
        if info.st_size > MAX_FILE_BYTES:
            raise PolicyError("File exceeds the 128 KiB read limit: " + relative)
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES or b"\x00" in raw:
            raise PolicyError("File is too large or binary.")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PolicyError("Only UTF-8 files are supported.") from exc
        return {"path": relative, "sha256": digest(raw), "content": content}

    def files(self, limit: int = 2000) -> list[str]:
        found = []
        for base, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d.casefold() not in BLOCKED_PARTS
                             and not SECRET_NAME.search(d) and not is_link(Path(base) / d))
            for name in sorted(names):
                relative = (Path(base) / name).relative_to(self.root).as_posix()
                try:
                    self.path(relative)
                except PolicyError:
                    continue
                found.append(relative)
                if len(found) >= limit:
                    return found
        return found

    def search(self, query: str) -> list[dict]:
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise PolicyError("Search query must contain 1–200 characters.")
        results = []
        for name in self.files():
            try:
                content = self.read(name)["content"]
            except PolicyError:
                continue
            for line, value in enumerate(content.splitlines(), 1):
                if query.casefold() in value.casefold():
                    results.append({"path": name, "line": line, "text": value[:300]})
                    if len(results) >= 40:
                        return results
        return results

    def repo_map(self) -> str:
        files = self.files(limit=symbols.MAX_FILES)
        rows = []
        indexed = set()
        for name in files:
            if not symbols.indexable(name):
                continue
            row = self._index_row(name)
            if row is not None:
                indexed.add(name)
                rows.append(row)
        for stale in [key for key in list(INDEX_CACHE)
                      if key[0] == self._cache_key and key[1] not in indexed]:
            del INDEX_CACHE[stale]
        # A folder with several builds in it gets its map spread across them: the budget is the same
        # 12 000 characters either way, and in a reactor alphabetical order spends all of it on the
        # first three modules.
        return symbols.render(rows, files, spread_files=len(runner.projects(self.root)) > 1)

    def _index_row(self, relative: str) -> dict | None:
        """This file's declarations, reusing the last parse when the file still fits.

        mtime and size are a heuristic: an equal-size edit inside one clock tick can be
        missed. A stale row only makes the map slightly out of date — every write is still
        checked against the hash of the bytes on disk.
        """
        key = (self._cache_key, relative)
        try:
            info = (self.root / relative).stat()
        except OSError:
            return None
        if len(INDEX_CACHE) >= INDEX_CACHE_LIMIT:
            INDEX_CACHE.clear()             # bounded convenience, not an LRU
        cached = INDEX_CACHE.get(key)
        if cached and cached[:2] == (info.st_mtime_ns, info.st_size):
            return cached[2]
        try:
            content = self.read(relative)["content"]
        except PolicyError:
            return None
        row = symbols.parse(relative, content)
        if row is not None:
            INDEX_CACHE[key] = (info.st_mtime_ns, info.st_size, row)
        return row

    def write(self, relative: str, content: str, expected: str | None) -> str:
        path = self.path(relative, writable=True)
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise PolicyError("Invalid or oversized replacement.")
        actual = self.read(relative)["sha256"] if path.exists() else None
        if actual != expected:
            raise PolicyError("File changed since review; regenerate the proposal.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path = self.path(relative, writable=True)
        raw = content.encode("utf-8")
        fd, tmp = tempfile.mkstemp(prefix=".agent-write-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                os.chmod(tmp, path.stat().st_mode)
            # Recheck before replacement; concurrent hostile filesystem access still
            # requires OS isolation, and is outside this developer-local MVP.
            self.path(relative, writable=True)
            latest = self.read(relative)["sha256"] if path.exists() else None
            if latest != expected:
                raise PolicyError("Concurrent edit detected; replacement cancelled.")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return digest(raw)

    def remove(self, relative: str, expected: str | None) -> None:
        """Delete one file a proposal named, under the same guards `write` runs.

        The hash check is the reason this is a workspace verb and not an `unlink()` call: removing a
        file that changed since review is the same race a write refuses, and the bytes nobody looked
        at would be gone either way.
        """
        path = self.path(relative, writable=True)
        if not path.exists():
            raise MissingFileError("File does not exist: " + relative)
        if self.read(relative)["sha256"] != expected:
            raise PolicyError("File changed since review; regenerate the proposal.")
        self.path(relative, writable=True)          # re-run the guards after the race window
        if self.read(relative)["sha256"] != expected:
            raise PolicyError("Concurrent edit detected; removal cancelled.")
        path.unlink()
