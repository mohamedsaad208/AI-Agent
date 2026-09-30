"""Git awareness that reads, commits, and moves HEAD only when it is asked.

A project folder on disk is not trusted code. Three of git's read commands have side
doors — `status` can start an fsmonitor daemon, any command takes hooks and configuration
from the folder it runs in, and a command that wants credentials can open a prompt and sit
there forever — so each call carries the flags that close those doors rather than relying
on the user's global configuration.

The status half reads and nothing else — `rev-parse`, `symbolic-ref`, `status --porcelain`,
and no network command exists in this file. Every failure path returns the same "no
information" shape as "this folder is not a repo", because the result feeds a status chip,
and a chip that cannot render git is a smaller loss than a turn that hangs on a git process
that never exits.

The other half writes exactly four kinds of thing:

* `checkpoint` commits the files a proposal just wrote, under its own subject line, and
  refuses to touch anything else.
* `start_task_branch` creates and switches to `agent/task-…`, on an explicit request only.
* `switch_branch` returns to a named local branch, and never forces.
* `restore_paths` puts the proposal's own files back to a hash this program wrote — the one
  call here that discards what is on disk, which is why it takes a hash, a path list, and
  nothing else.

Those limits are the point of the file. `git commit -am` would sweep a developer's
half-finished edits into an agent-authored commit, and `git reset` or `git checkout --force`
would delete work no proposal ever proposed — so no verb in here can discard a change that
is not the agent's own. Creating a branch carries the working tree with it, and switching
refuses rather than overwrites: both are reversible by the user typing the same command back.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import runner
from .redaction import redact

# git can be present and still not answer: a huge monorepo takes tens of seconds to
# stat every file on `status`. It is bounded rather than trusted.
TIMEOUT = 4.0
# A snapshot is rebuilt on every streamed event, and each inspection is four git
# processes — about 0.2 s on Windows. Eight seconds is long enough that the cost is
# invisible and short enough that a commit made in a terminal shows up on the next
# exchange; anything the agent itself changes calls forget() instead of waiting.
CACHE_TTL = 8.0
# Porcelain is one line per path; a repo with 40 000 dirty files is a count, not a list.
MAX_PATHS = 500
# A commit is a different job from a status read: a cold cache and an object write both
# take longer than the second git needs to answer a question.
COMMIT_TIMEOUT = 20.0

# Global options, in the order git expects them, before the subcommand.
PREFIX = ["--no-optional-locks", "--literal-pathspecs", "-c", "core.fsmonitor=false", "-c", "color.ui=false"]

# Environment the child gets on top of the scrubbed toolchain env. The scrub itself
# matters: GIT_DIR, GIT_INDEX_FILE and GIT_WORK_TREE in the parent's environment would
# point git at some other folder entirely.
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_ADVICE": "0",
           "GCM_INTERACTIVE": "never", "GIT_PAGER": "cat"}

_EMPTY = {"known": False, "repo": False, "branch": "", "detached": False,
          "head": "", "toplevel": "", "dirty": None, "paths": [], "reason": ""}

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def git_program() -> str | None:
    """Absolute path to the git binary, or None when it is not on PATH."""
    return shutil.which("git")


def env() -> dict:
    """The scrubbed toolchain environment, plus the switches that keep git from blocking.

    git on Windows reads USERPROFILE but warns when HOME is unset, and the scrub drops
    anything not on the allowlist, so HOME is filled from the same place git would.
    """
    child = runner.child_env()
    child.setdefault("HOME", child.get("USERPROFILE", ""))
    child.update(GIT_ENV)
    return child


def _done(root: Path, args: list[str], timeout: float) -> dict:
    """Run one fixed git argv. Never raises: a failure is an empty result plus a reason."""
    program = git_program()
    if program is None:
        return {"ok": False, "out": "", "err": "", "reason": "git is not installed"}
    command = [program, *PREFIX, *args]
    options = {"cwd": str(root), "env": env(), "stdin": subprocess.DEVNULL,
               "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "shell": False}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    process = None                      # the spawn itself may be what fails
    try:
        process = subprocess.Popen(command, **options)
        finished_out, finished_err = process.communicate(timeout=max(0.1, float(timeout)))
    except subprocess.TimeoutExpired:
        # The old `subprocess.run(timeout=…)` killed only the git child, and a credential helper
        # git started survives that. Same tree kill the build runner uses, then reap what is left.
        runner.kill_tree(process)
        try:
            process.communicate(timeout=runner.POST_KILL_GRACE)
        except subprocess.TimeoutExpired:
            pass
        return {"ok": False, "out": "", "err": "", "reason": "git took too long to answer"}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if process is not None:
            runner.kill_tree(process)   # a pipe that broke still leaves a child behind
        return {"ok": False, "out": "", "err": "", "reason": str(exc)[:200]}
    out = (finished_out or b"").decode("utf-8", "replace")
    err = (finished_err or b"").decode("utf-8", "replace")
    return {"ok": process.returncode == 0, "out": out, "err": err,
            "code": process.returncode, "reason": ""}


def _first_line(text: str) -> str:
    return redact(text.strip().splitlines()[0])[:200] if text.strip() else ""


def _porcelain(root: Path) -> dict:
    """Changed paths, worktree-relative. Untracked files count as dirty: they are the
    ones a checkpoint commit is most likely to be asked to carry."""
    result = _done(root, ["status", "--porcelain"], TIMEOUT)
    if not result["ok"]:
        # None, not 0: a repo whose status timed out is not a clean repo, and the chip
        # would say so.
        return {"dirty": None, "paths": [],
                "reason": result["reason"] or _first_line(result["err"])}
    lines = [line for line in result["out"].splitlines() if line.strip()]
    paths = []
    for line in lines[:MAX_PATHS]:
        # XY <path>, or "!! <path>" when ignored; the payload always starts at column 3.
        tail = line[3:] if len(line) > 3 else ""
        paths.append(tail.split(" -> ")[-1].strip('"'))
    return {"dirty": len(lines), "paths": paths, "reason": ""}


def inspect(root: str | Path) -> dict:
    """What git says about this folder right now — no cache, no writes."""
    info = dict(_EMPTY)
    try:
        folder = Path(root).expanduser().resolve()
    except (OSError, RuntimeError):
        info["reason"] = "that folder is not on this disk"
        return info
    if git_program() is None:
        info["reason"] = "git is not installed"
        return info
    info["known"] = True
    if not folder.is_dir():
        info["reason"] = "that folder is not on this disk"
        return info
    where = _done(folder, ["rev-parse", "--is-inside-work-tree"], TIMEOUT)
    if not where["ok"] or where["out"].strip() != "true":
        info["reason"] = where["reason"] or _first_line(where["err"]) or "not a git repository"
        return info
    info["repo"] = True
    top = _done(folder, ["rev-parse", "--show-toplevel"], TIMEOUT)
    # git prints forward slashes even on Windows; the value is compared against folder paths.
    info["toplevel"] = str(Path(top["out"].strip())) if top["out"].strip() else ""
    head = _done(folder, ["rev-parse", "--short", "HEAD"], TIMEOUT)
    if head["ok"]:
        info["head"] = head["out"].strip()
    # `branch --show-current` needs git 2.22; this has to work with whatever is installed.
    # symbolic-ref answers nothing on a detached HEAD, which is how "detached" is detected.
    ref = _done(folder, ["symbolic-ref", "--short", "-q", "HEAD"], TIMEOUT)
    if ref["ok"] and ref["out"].strip():
        info["branch"] = ref["out"].strip()
    else:
        info["detached"] = bool(info["head"])
    state = _porcelain(folder)
    info.update({"dirty": state["dirty"], "paths": state["paths"]})
    info["reason"] = state["reason"]
    if not info["branch"] and not info["head"]:
        # A repository with no commits yet: real, and it has nothing to compare against.
        info["reason"] = info["reason"] or "no commits yet"
    return info


def _copy(info: dict) -> dict:
    # A shallow copy would hand the caller the cached path list, and one sort in place
    # would then silently reorder every later chip.
    return {**info, "paths": list(info["paths"])}


def status(root: str | Path | None) -> dict:
    """Cached inspection of the granted folder. Returns the empty shape for no folder."""
    if not root:
        return _copy(_EMPTY)
    key = str(root)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return _copy(hit[1])
    info = inspect(root)
    with _lock:
        if len(_cache) > 32:
            _cache.clear()
        _cache[key] = (now, info)
    return _copy(info)


def relative(name: str) -> str:
    """The path in git's own form, or "" when it does not belong inside this folder.

    These names come from a proposal the user already approved, but they are about to
    cross a process boundary: an absolute path or a parent traversal handed to `git add`
    would make git act on somewhere the folder never granted.
    """
    raw = str(name or "").strip().strip('"')
    if not raw or raw.startswith(("/", "\\\\")) or re.match(r"^[a-zA-Z]:", raw):
        return ""
    parts = [piece for piece in raw.replace("\\", "/").split("/") if piece not in ("", ".")]
    if ".." in parts or ".git" in parts:
        return ""
    return "/".join(parts)


def checkpoint(root: str | Path, task: str, session_id: str, paths: list[str]) -> dict:
    """Commit exactly the files this task wrote, and nothing beside them.

    Two things separate this from typing `git commit -am` in a terminal:

    * Only the proposal's own paths are staged, so a developer's half-finished edits stay
      out of an agent-authored commit.
    * `--no-verify` skips the repository's hooks. This runs without anyone at the
      keyboard, and a pre-commit hook is arbitrary code the folder chose to install.

    Returns {ok, hash, before, reason}. Failure is never fatal: the change is already on
    disk and the in-session rollback still works — a checkpoint is an extra safety net,
    not a step in the write path.
    """
    result = {"ok": False, "hash": "", "before": "", "reason": ""}
    folder = Path(root)
    states = status(folder)
    if not states["repo"]:
        result["reason"] = states["reason"] or "this folder is not a git repository"
        return result
    was = _done(folder, ["rev-parse", "HEAD"], TIMEOUT)
    result["before"] = was["out"].strip() if was["ok"] else ""
    targets = [item for item in (relative(name) for name in paths) if item]
    if not targets:
        result["reason"] = "the proposal named no file inside this folder"
        return result
    staged = _done(folder, ["add", "--", *targets], COMMIT_TIMEOUT)
    if not staged["ok"]:
        result["reason"] = _first_line(staged["err"]) or "git could not stage those files"
        return result
    subject = ("agent: " + " ".join((task or "change").split())[:120]
               + " [session-" + str(session_id)[:12] + "]")
    # A plain commit also consumes unrelated paths already in the user's index. --only
    # commits the named files' current contents and preserves the other staged entries.
    # These are whole-file checkpoints, including any prior edits in the named files.
    committed = _done(folder, ["commit", "--only", "--no-verify", "-m", subject,
                               "--", *targets], COMMIT_TIMEOUT)
    if not committed["ok"]:
        result["reason"] = (_first_line(committed["err"]) or _first_line(committed["out"])
                            or "git had nothing to commit")
        return result
    after = _done(folder, ["rev-parse", "--short", "HEAD"], TIMEOUT)
    result["hash"] = after["out"].strip() if after["ok"] else ""
    result["ok"] = True
    forget(folder)
    return result


TASK_BRANCH_PREFIX = "agent/task-"
# A branch name is built here rather than taken from anyone's text, so git's refname rules —
# no spaces, no `..`, no control characters, no leading `-` that git would read as an option —
# hold by construction instead of by validation.
_SLUG_EDGE = re.compile(r"[^a-z0-9]+")
MAX_SLUG = 40
# Accepted back from the UI for the return trip. The generator above cannot produce `.` or `_`,
# but a human's own branch uses them, and `agent/task-…` is not the only branch worth returning to.
BRANCH_NAME = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,63}$")


def task_branch_name(task: str, session_id: str) -> str:
    """`agent/task-<words from the task>-<session>`.

    A task written in Arabic slugs to nothing at all, which is why the session id is part of the
    name: the fallback has to stay unique across tasks rather than pile every Arabic request onto
    one branch.
    """
    slug = _SLUG_EDGE.sub("-", str(task or "").casefold()).strip("-")[:MAX_SLUG].strip("-")
    tail = _SLUG_EDGE.sub("", str(session_id or ""))[:8]
    body = slug + ("-" + tail if tail else "") if slug else tail
    return TASK_BRANCH_PREFIX + (body or "task")


def _has_branch(folder: Path, name: str) -> bool:
    """Whether git has a local branch by this name. Never creates one."""
    check = _done(folder, ["rev-parse", "--verify", "--quiet", "refs/heads/" + name], TIMEOUT)
    return check["ok"] and bool(check["out"].strip())


def _refused(result: dict, states: dict, reason: str = "") -> dict:
    if not states["repo"]:
        result["reason"] = states["reason"] or "this folder is not a git repository"
    else:
        result["reason"] = reason
    return result


def start_task_branch(root: str | Path, task: str, session_id: str) -> dict:
    """Create `agent/task-…` and move HEAD onto it, in a folder that is already a repository.

    `checkout -b` carries the working tree with it, so nothing that is not yet committed is lost,
    and typing `git checkout <from>` puts it back. Two cases still refuse: a detached HEAD, where
    there is no named branch to return to, and an existing branch by that name, where switching
    would silently join someone else's work.

    Returns {ok, created, branch, from, reason}. `from` is what the caller offers to go back to.
    """
    result = {"ok": False, "created": False, "branch": "", "from": "", "reason": ""}
    folder = Path(root)
    states = status(folder)
    if not states["repo"]:
        return _refused(result, states)
    if states["detached"] or not states["branch"]:
        return _refused(result, states,
                        "the folder is on a detached HEAD, so there is no branch to come back to")
    result["from"] = states["branch"]
    name = task_branch_name(task, session_id)
    if _has_branch(folder, name):
        if name == states["branch"]:
            result.update({"ok": True, "branch": name,
                           "reason": "already on " + name})
            return result
        return _refused(result, states, "a branch named " + name + " already exists")
    made = _done(folder, ["checkout", "-b", name], COMMIT_TIMEOUT)
    if not made["ok"]:
        result["from"] = ""
        return _refused(result, states,
                        _first_line(made["err"]) or "git could not create that branch")
    result.update({"ok": True, "created": True, "branch": name})
    forget(folder)
    return result


def switch_branch(root: str | Path, name: str) -> dict:
    """Move HEAD back to a named local branch, unforced.

    No `--force` and no `-f`: when the working tree holds edits that would be overwritten, git
    refuses, and that refusal is the answer the user needs. Rewriting history is not on this list.
    """
    result = {"ok": False, "branch": "", "reason": ""}
    wanted = str(name or "").strip()
    # This program addresses a local branch by its bare name and supplies `refs/heads/` itself, so
    # a refpath coming back from the UI is either a mistake or something trying to pick its own
    # namespace. Neither gets handed to git.
    if (not BRANCH_NAME.match(wanted) or ".." in wanted or ".git" in wanted.split("/")
            or wanted.startswith("refs/")):
        result["reason"] = "that is not a branch name this program will hand to git"
        return result
    folder = Path(root)
    states = status(folder)
    if not states["repo"]:
        return _refused(result, states)
    result["branch"] = wanted
    if wanted == states["branch"]:
        result["ok"] = True
        result["reason"] = "already on " + wanted
        return result
    if not _has_branch(folder, wanted):
        return _refused(result, states, "no local branch named " + wanted)
    moved = _done(folder, ["checkout", wanted], COMMIT_TIMEOUT)
    if not moved["ok"]:
        return _refused(result, states,
                        _first_line(moved["err"]) or "git refused to switch branches")
    result["ok"] = True
    forget(folder)
    return result


# A restore is addressed by hash, never by a word. `HEAD~3` is a claim about history this program
# cannot see, and a branch name could point somewhere else by the moment the click arrives.
COMMIT = re.compile(r"^[0-9a-f]{4,40}$")


def _has_commit(folder: Path, commit: str) -> bool:
    check = _done(folder, ["rev-parse", "--verify", "--quiet", commit + "^{commit}"], TIMEOUT)
    return check["ok"] and bool(check["out"].strip())


def restore_paths(root: str | Path, commit: str, paths: list[str]) -> dict:
    """Put the named files back to how they were in `commit`, and touch nothing else.

    This is the only call in the file that discards what is on disk, so three limits are part of its
    shape rather than left to the caller: the commit must be a hash this program wrote, the paths come
    from a proposal the user already approved, and each file is restored on its own so a path git does
    not recognise cannot silently widen the blast radius. No `reset`, no `checkout .`, no `clean`, no
    `--force` — those are the commands that damage work nobody proposed.

    Returns {ok, restored, skipped, reason}.
    """
    result: dict = {"ok": False, "restored": [], "skipped": [], "reason": ""}
    wanted = str(commit or "").strip()
    if not COMMIT.match(wanted):
        result["reason"] = "that is not a commit this program will restore from"
        return result
    folder = Path(root)
    states = status(folder)
    if not states["repo"]:
        return _refused(result, states)
    seen, targets = set(), []
    for name in paths or []:
        one = relative(name)
        if one and one not in seen:
            seen.add(one)
            targets.append(one)
    if not targets:
        result["reason"] = "the request named no file inside this folder"
        return result
    if not _has_commit(folder, wanted):
        return _refused(result, states, "git has no commit " + wanted + " in this folder")
    first_refusal = ""
    for one in targets:
        moved = _done(folder, ["checkout", wanted, "--", one], COMMIT_TIMEOUT)
        if moved["ok"]:
            result["restored"].append(one)
        else:
            result["skipped"].append(one)
            first_refusal = first_refusal or (_first_line(moved["err"]) or "git would not restore it")
    result["ok"] = bool(result["restored"])
    if not result["ok"]:
        result["reason"] = first_refusal
    forget(folder)
    return result


def forget(root: str | Path | None = None) -> None:
    """Drop the cached answer, for after something changed the files."""
    with _lock:
        if root is None:
            _cache.clear()
            return
        _cache.pop(str(root), None)
