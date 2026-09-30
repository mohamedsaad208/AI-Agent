"""git_integration is decoration with a process boundary, so both have to be provable
without a repository, without a network, and without depending on the git version on
this machine.

The scripted tests replace `subprocess.Popen` itself: what is asserted is the argv handed
to it (a list, never a shell string), the environment that reaches the child, what the
child is asked to do when it does not answer, and the shape that comes back when git
answers, refuses, or never answers at all. The live tests
at the end run against a real temporary repository, because a flag that only exists in a
newer git would pass every scripted test.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import git_integration as git
from ai_code_engineer import runner

GIT = r"C:\Program Files\Git\bin\git.exe"


class Completed:
    def __init__(self, out="", err="", code=0):
        self.stdout, self.stderr, self.returncode = out.encode(), err.encode(), code


class ScriptedChild:
    """The one thing `_done` asks of a child: two pipes and a wait that answers."""

    def __init__(self, reply):
        self.reply = reply
        self.pid = 4321
        self.waited = None
        self.returncode = reply.returncode if isinstance(reply, Completed) else None

    def communicate(self, timeout=None):
        self.waited = timeout
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply.stdout, self.reply.stderr


class Scripted(unittest.TestCase):
    """Drives the module through a fake git so every branch is reachable offline."""

    def setUp(self):
        self.calls = []
        self.children = []
        self.killed = []
        self.replies = {}
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        git.forget()
        self.addCleanup(git.forget)
        for item in (patch.object(git, "git_program", return_value=GIT),
                     patch.object(subprocess, "Popen", side_effect=self._popen),
                     patch.object(runner, "kill_tree",
                                  side_effect=lambda child: self.killed.append(child.pid))):
            self.addCleanup(item.stop)
            item.start()

    def answer(self, subcommand, out="", err="", code=0):
        """Register one reply. `err` starting with an exception class name is raised."""
        self.replies[subcommand] = Completed(out, err, code)

    def fail_with(self, subcommand, exc):
        self.replies[subcommand] = exc

    def _popen(self, command, **options):
        self.calls.append((list(command), options))
        key = " ".join(command[1 + len(git.PREFIX):])
        if key not in self.replies:
            raise AssertionError("unexpected git invocation: " + key)
        child = ScriptedChild(self.replies[key])
        self.children.append(child)
        return child

    def info(self):
        return git.inspect(self.root)

    def asked(self):
        return [" ".join(command[1 + len(git.PREFIX):]) for command, _ in self.calls]

    def a_repository(self, porcelain=""):
        self.answer("rev-parse --is-inside-work-tree", out="true\n")
        self.answer("rev-parse --show-toplevel", out=str(self.root).replace("\\", "/") + "\n")
        self.answer("rev-parse --short HEAD", out="abc1234\n")
        self.answer("symbolic-ref --short -q HEAD", out="main\n")
        self.answer("status --porcelain", out=porcelain)


class ArgvSafetyTests(Scripted):
    def test_every_invocation_is_a_list_argv_with_no_shell(self):
        self.a_repository(porcelain=" M a.py\n")
        self.info()
        self.assertTrue(self.calls)
        for command, options in self.calls:
            self.assertIsInstance(command, list)
            self.assertEqual(command[0], GIT)
            joined = " ".join(command)
            self.assertNotIn("&&", joined)
            self.assertNotIn(";", joined)
            self.assertIs(options.get("shell", False), False)
            self.assertEqual(Path(options["cwd"]), self.root)
            self.assertEqual(options["stdin"], subprocess.DEVNULL)
        # The bound is a wait on the child, not a Popen option: a `run(timeout=)` would have
        # killed only git itself, and a credential helper it spawned would have outlived it.
        self.assertTrue(self.children)
        for child in self.children:
            self.assertGreater(child.waited, 0)

    def test_the_child_gets_a_scrubbed_non_interactive_environment(self):
        self.a_repository()
        import os
        with patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "leak-me", "GIT_DIR": "/elsewhere"}):
            self.info()
        env = self.calls[0][1]["env"]
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("GIT_DIR", env)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")

    def test_a_repo_cannot_turn_the_reader_into_a_writer(self):
        """The whole module is allowed to ask git five questions. Anything that could move
        a branch, touch the index or reach the network would not belong here."""
        self.a_repository()
        self.info()
        self.assertEqual(self.asked(), ["rev-parse --is-inside-work-tree",
                                        "rev-parse --show-toplevel", "rev-parse --short HEAD",
                                        "symbolic-ref --short -q HEAD", "status --porcelain"])

    def test_branch_show_current_is_never_asked_for(self):
        """`git branch --show-current` needs git 2.22. This runs against whatever is
        installed — the machine this was built on has 2.15, where it is a hard error."""
        self.a_repository()
        self.info()
        self.assertEqual([part for part in self.asked() if "--show-current" in part], [])


class ShapeTests(Scripted):
    def test_a_repository_reports_branch_head_and_dirty_count(self):
        self.a_repository(porcelain=" M src/a.py\n?? src/b.py\n")
        info = self.info()
        self.assertTrue(info["known"])
        self.assertTrue(info["repo"])
        self.assertEqual(info["branch"], "main")
        self.assertEqual(info["head"], "abc1234")
        self.assertEqual(info["dirty"], 2)
        self.assertEqual(info["paths"], ["src/a.py", "src/b.py"])
        self.assertEqual(info["reason"], "")

    def test_untracked_files_count_because_a_checkpoint_commit_must_carry_them(self):
        self.a_repository(porcelain="?? only-new.py\n")
        self.assertEqual(self.info()["dirty"], 1)

    def test_a_rename_reports_the_new_name(self):
        self.a_repository(porcelain="R  old/name.py -> new/name.py\n")
        self.assertEqual(self.info()["paths"], ["new/name.py"])

    def test_a_quoted_path_is_unquoted(self):
        self.a_repository(porcelain='?? "spaced name.py"\n')
        self.assertEqual(self.info()["paths"], ["spaced name.py"])

    def test_a_detached_head_is_detached_and_not_a_branch_name(self):
        self.a_repository()
        self.answer("symbolic-ref --short -q HEAD", err="", code=1)
        info = self.info()
        self.assertEqual(info["branch"], "")
        self.assertTrue(info["detached"])
        self.assertEqual(info["head"], "abc1234")

    def test_a_repository_with_no_commits_yet_is_reported_without_a_head(self):
        self.a_repository()
        self.answer("rev-parse --short HEAD", err="fatal: ambiguous argument\n", code=128)
        info = self.info()
        self.assertTrue(info["repo"])
        self.assertEqual(info["head"], "")
        self.assertFalse(info["detached"])

    def test_a_folder_that_is_not_a_repository_is_no_information(self):
        self.answer("rev-parse --is-inside-work-tree", err="fatal: Not a git repository\n",
                    code=128)
        info = self.info()
        self.assertTrue(info["known"])
        self.assertFalse(info["repo"])
        self.assertEqual(info["branch"], "")
        self.assertIsNone(info["dirty"])
        self.assertIn("Not a git repository", info["reason"])
        self.assertEqual(len(self.calls), 1, "a non-repo must not be interrogated further")

    def test_a_status_that_fails_leaves_dirty_unknown_rather_than_clean(self):
        self.a_repository()
        self.answer("status --porcelain", err="fatal: index.lock exists\n", code=128)
        info = self.info()
        self.assertTrue(info["repo"])
        self.assertIsNone(info["dirty"])
        self.assertIn("index.lock", info["reason"])

    def test_a_reason_from_git_is_redacted_before_it_is_shown(self):
        self.a_repository()
        self.answer("status --porcelain",
                    err="fatal: could not read password for https://u:hunter2token@x/\n",
                    code=128)
        self.assertNotIn("hunter2token", self.info()["reason"])

    def test_no_folder_and_no_git_binary_both_answer_the_empty_shape(self):
        self.assertFalse(git.status(None)["repo"])
        self.assertFalse(git.status(None)["known"])
        with patch.object(git, "git_program", return_value=None):
            info = git.inspect(self.root)
        self.assertFalse(info["known"])
        self.assertFalse(info["repo"])
        self.assertEqual(info["reason"], "git is not installed")
        self.assertEqual(self.calls, [])

    def test_a_folder_that_vanished_is_a_reason_not_a_crash(self):
        info = git.inspect(self.root / "not-here")
        self.assertFalse(info["repo"])
        self.assertEqual(info["reason"], "that folder is not on this disk")
        self.assertEqual(self.calls, [])


class TimeoutTests(Scripted):
    def test_a_git_that_never_answers_has_its_whole_tree_stopped(self):
        self.a_repository()
        self.fail_with("status --porcelain", subprocess.TimeoutExpired(cmd="git", timeout=1))
        info = self.info()
        self.assertTrue(info["repo"])
        self.assertIsNone(info["dirty"])
        self.assertIn("too long", info["reason"])
        # Killing only the git child would leave a credential helper it spawned running, so the
        # bounded call uses the same tree kill a build gets.
        self.assertEqual(self.killed, [4321])

    def test_a_hang_on_the_first_question_does_not_stop_the_turn(self):
        self.fail_with("rev-parse --is-inside-work-tree",
                       subprocess.TimeoutExpired(cmd="git", timeout=1))
        info = self.info()
        self.assertFalse(info["repo"])
        self.assertIn("too long", info["reason"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.killed), 1)

    def test_a_failing_git_is_a_reason_not_an_exception(self):
        self.fail_with("rev-parse --is-inside-work-tree", OSError("no process table"))
        info = self.info()
        self.assertFalse(info["repo"])
        self.assertIn("no process table", info["reason"])
        self.assertEqual(self.killed, [4321], "a child that failed after spawning is stopped too")


class CacheTests(Scripted):
    def test_repeated_snapshots_ask_git_once_and_forget_asks_again(self):
        self.a_repository()
        git.status(self.root)
        asked = len(self.calls)
        self.assertTrue(asked)
        git.status(self.root)
        self.assertEqual(len(self.calls), asked, "the cache is the point of status()")
        git.forget(self.root)
        git.status(self.root)
        self.assertEqual(len(self.calls), asked * 2)

    def test_the_cached_paths_list_is_not_the_one_a_caller_can_sort(self):
        self.a_repository(porcelain="?? b.py\n?? a.py\n")
        git.status(self.root)["paths"].sort()
        self.assertEqual(git.status(self.root)["paths"], ["b.py", "a.py"])

    def test_expired_entries_are_replaced_rather_than_served(self):
        self.a_repository()
        git.status(self.root)
        asked = len(self.calls)
        with patch.object(git, "CACHE_TTL", 0.0):
            git.status(self.root)
        self.assertGreater(len(self.calls), asked)


class BoundedOutputTests(Scripted):
    def test_a_sixty_thousand_file_repo_is_a_count_not_a_dump(self):
        self.a_repository()
        self.answer("status --porcelain", out="".join("?? f%d.py\n" % i for i in range(60_000)))
        info = self.info()
        self.assertEqual(info["dirty"], 60_000)
        self.assertEqual(len(info["paths"]), git.MAX_PATHS)


@unittest.skipUnless(git.git_program(), "git is not installed on this machine")
class RelativePathTests(unittest.TestCase):
    """checkpoint() hands these strings to git, so they are filtered before that."""

    def test_a_path_inside_the_folder_survives_in_gits_own_form(self):
        self.assertEqual(git.relative("src//main.py"), "src/main.py")
        self.assertEqual(git.relative("./ok.py"), "ok.py")
        self.assertEqual(git.relative("a\\b.py"), "a/b.py")
        self.assertEqual(git.relative('"quoted name.py"'), "quoted name.py")

    def test_a_path_that_leaves_the_folder_is_refused(self):
        for name in ("../outside.py", "/etc/passwd", "C:\\Windows\\win.ini", "a/../../b",
                     "\\\\server\\share\\x.py", "", "   ", ".."):
            self.assertEqual(git.relative(name), "", name)

    def test_the_git_directory_is_never_a_target(self):
        self.assertEqual(git.relative(".git/config"), "")
        self.assertEqual(git.relative("src/.git/x"), "")


class CheckpointArgvTests(Scripted):
    """What the checkpoint asks git to do, checked before any repository exists."""

    def a_repository(self):
        self.answer("rev-parse --is-inside-work-tree", out="true\n")
        self.answer("rev-parse --show-toplevel", out=str(self.root).replace("\\", "/") + "\n")
        self.answer("rev-parse --short HEAD", out="abc1234\n")
        self.answer("symbolic-ref --short -q HEAD", out="main\n")
        self.answer("status --porcelain", out="")
        self.answer("rev-parse HEAD", out="0123456789abcdef0123456789abcdef01234567\n")
        self.answer("add -- src/a.py src/b.py", out="")
        self.answer("add -- src/a.py", out="")
        self.answer("commit --no-verify -m agent: fix it [session-s-1]", out="")

    def test_a_checkpoint_stages_named_paths_and_commits_them(self):
        self.a_repository()
        out = git.checkpoint(self.root, "fix it", "s-1", ["src/a.py", "src/b.py"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["hash"], "abc1234")
        self.assertEqual(out["before"], "0123456789abcdef0123456789abcdef01234567")
        self.assertIn("add -- src/a.py src/b.py", self.asked())
        self.assertIn("commit --no-verify -m agent: fix it [session-s-1]", self.asked())

    def test_the_commit_never_says_add_all(self):
        """`commit -am` would sweep whatever else the developer had open into this commit."""
        self.a_repository()
        git.checkpoint(self.root, "fix it", "s-1", ["src/a.py"])
        for command, _options in self.calls:
            self.assertNotIn("-a", command)
            self.assertNotIn("-am", command)
            self.assertNotIn("--all", command)
            self.assertNotIn("--no-verify", command[:command.index("commit")] if "commit" in command else [])

    def test_the_only_git_verbs_this_program_asks_for_are_add_checkout_and_commit(self):
        """The documentation promises no network git and no rewritten history. That promise is
        exactly this set, so a fourth verb has to fail here rather than in someone's repository.
        `checkout` is allowed only in its two non-destructive forms — `-b <new>`, and switching to
        a local branch without `--force` — which TaskBranchArgvTests below pins."""
        self.a_repository()
        git.checkpoint(self.root, "fix it", "s-1", ["src/a.py"])
        self.assertEqual(sorted({line.split()[0] for line in self.asked()}),
                         ["add", "commit", "rev-parse", "status", "symbolic-ref"])

    def test_the_commit_skips_the_repositories_hooks(self):
        self.a_repository()
        git.checkpoint(self.root, "fix it", "s-1", ["src/a.py"])
        self.assertIn("commit --no-verify -m agent: fix it [session-s-1]", self.asked())

    def test_a_subject_is_one_line_and_names_the_session(self):
        self.a_repository()
        self.answer("commit --no-verify -m agent: fix the thing now [session-sess-1234567]",
                    out="")
        git.checkpoint(self.root, "fix\n  the\u00a0thing   now", "sess-1234567890", ["src/a.py"])
        self.assertIn("commit --no-verify -m agent: fix the thing now [session-sess-1234567]",
                      self.asked())

    def test_a_path_outside_the_folder_is_dropped_before_git_sees_it(self):
        self.a_repository()
        self.answer("add -- src/ok.py", out="")
        self.answer("commit --no-verify -m agent: t [session-x]", out="")
        out = git.checkpoint(self.root, "t", "x", ["../evil.py", "C:\\evil", ".git/config",
                                                   "src/ok.py"])
        self.assertTrue(out["ok"], out)
        self.assertEqual([c for c, _ in self.calls if c[-2:] == ["--", "src/ok.py"]][0][-1],
                         "src/ok.py")
        self.assertNotIn("-- ../evil.py", " ".join(" ".join(c) for c, _ in self.calls))

    def test_a_folder_with_nothing_to_stage_is_a_reason_not_a_crash(self):
        self.answer("rev-parse --is-inside-work-tree", err="fatal: Not a git repository\n",
                    code=128)
        out = git.checkpoint(self.root, "t", "x", ["a.py"])
        self.assertFalse(out["ok"])
        self.assertIn("Not a git repository", out["reason"])

    def test_a_refused_commit_reports_gits_own_line(self):
        self.a_repository()
        self.answer("add -- src/a.py", out="")
        self.answer("commit --no-verify -m agent: fix it [session-s-1]",
                    err="fatal: Unable to create '/repo/.git/index.lock': File exists.\n", code=128)
        out = git.checkpoint(self.root, "fix it", "s-1", ["src/a.py"])
        self.assertFalse(out["ok"])
        self.assertIn("index.lock", out["reason"])


class LiveRepositoryTests(unittest.TestCase):
    """Once against a real git, to prove the scripted argv is the argv git accepts."""

    IDENTITY = ["-c", "user.email=agent@example.invalid", "-c", "user.name=Agent",
                "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.addCleanup(self.temp.cleanup)
        git.forget()
        self.addCleanup(git.forget)
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args):
        finished = subprocess.run([git.git_program(), *self.IDENTITY, *args],
                                  cwd=str(self.root), env=git.env(), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        self.assertEqual(finished.returncode, 0, finished.stdout.decode("utf-8", "replace"))
        return finished.stdout.decode("utf-8", "replace").strip()

    def write(self, name, body="x = 1\n"):
        (self.root / name).write_text(body, encoding="utf-8", newline="\n")

    def commit(self, name="a.py"):
        self.write(name)
        self.git("add", "--", name)
        self.git("commit", "-q", "-m", "one")

    def test_the_checkpoint_commits_a_removal_it_was_given(self):
        """D31 planned a `git rm` step; measurement says `git add -- <gone path>` already stages the
        deletion, so the checkpoint keeps one verb. This pins the fact rather than the guess."""
        self.write("a.py")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed")
        (self.root / "a.py").unlink()
        out = git.checkpoint(self.root, "retire a.py", "s-9", ["a.py"])
        self.assertTrue(out["ok"], out["reason"])
        self.assertIn("1 deletion", self.git("show", "--stat", "--format=", "HEAD"))

    def test_a_real_repository_reports_its_branch_and_its_dirty_files(self):
        self.commit()
        info = git.inspect(self.root)
        self.assertTrue(info["repo"])
        self.assertIn(info["branch"], ("master", "main"))
        self.assertEqual(len(info["head"]), 7)
        self.assertEqual(info["dirty"], 0)
        self.write("a.py", "x = 2\n")
        self.write("b.py")
        git.forget()
        info = git.inspect(self.root)
        self.assertEqual(info["dirty"], 2)
        self.assertEqual(sorted(info["paths"]), ["a.py", "b.py"])

    def test_a_real_folder_that_is_not_a_repository_degrades(self):
        outside = self.root.parent / "plain"
        outside.mkdir()
        info = git.inspect(outside)
        self.assertTrue(info["known"])
        self.assertFalse(info["repo"])
        self.assertTrue(info["reason"])

    def test_a_repository_with_no_commits_has_a_branch_but_no_head(self):
        info = git.inspect(self.root)
        self.assertTrue(info["repo"])
        self.assertEqual(info["head"], "")
        self.assertIn(info["branch"], ("master", "main"))

    def test_a_detached_real_head_reports_no_branch(self):
        self.commit()
        self.write("a.py", "x = 2\n")
        self.git("commit", "-qa", "-m", "two")
        self.git("checkout", "-q", "--detach", "HEAD~1")
        info = git.inspect(self.root)
        self.assertEqual(info["branch"], "")
        self.assertTrue(info["detached"])

    def test_a_checkpoint_commits_the_named_file_and_leaves_the_rest_alone(self):
        self.commit()                            # a.py is now tracked and clean
        self.write("keep.py", "y = 1\n")         # someone else's work in progress
        self.write("a.py", "x = 2\n")            # the change this task wrote
        info = git.checkpoint(self.root, "make  add()  add", "session-1", ["a.py"])
        self.assertTrue(info["ok"], info["reason"])
        self.assertEqual(self.git("log", "-1", "--pretty=%s"),
                         "agent: make add() add [session-session-1]")
        left = self.git("status", "--porcelain")
        self.assertIn("keep.py", left, "a checkpoint must not steal an unrelated file")
        self.assertNotIn("a.py", left)

    def test_a_checkpoint_in_a_plain_folder_creates_nothing(self):
        outside = self.root.parent / "plain"
        outside.mkdir()
        (outside / "a.py").write_text("x = 1\n", encoding="utf-8")
        info = git.checkpoint(outside, "t", "s", ["a.py"])
        self.assertFalse(info["ok"])
        self.assertTrue(info["reason"])
        self.assertFalse((outside / ".git").exists())

    def test_a_checkpoint_that_only_named_outside_paths_stages_nothing(self):
        self.commit()
        self.write("a.py", "x = 2\n")
        info = git.checkpoint(self.root, "t", "s", ["../outside.py", ".git/config"])
        self.assertFalse(info["ok"])
        self.assertIn("no file inside this folder", info["reason"])
        self.assertIn("a.py", self.git("status", "--porcelain"), "still uncommitted")


class TaskBranchNameTests(unittest.TestCase):
    def test_the_task_words_become_the_branch_and_the_session_keeps_it_unique(self):
        self.assertEqual(git.task_branch_name("Add the login endpoint", "abcdef123456"),
                         "agent/task-add-the-login-endpoint-abcdef12")

    def test_an_arabic_task_slugs_to_nothing_so_the_session_carries_the_name(self):
        """Arabic words in a refname would be legal git and unreadable in the chip; the fallback
        still has to stay unique, or every Arabic task lands on one branch."""
        self.assertEqual(git.task_branch_name("ابني نقطة تسجيل دخول", "zz99887766"),
                         "agent/task-zz998877")

    def test_a_generated_name_is_always_a_legal_branch_name(self):
        for task in ("", "   ", "!!", "-" * 90, "fix/../etc", "a" * 400, "Merge #4 (v2)"):
            name = git.task_branch_name(task, "s1")
            self.assertTrue(git.BRANCH_NAME.match(name), name)
            self.assertNotIn("..", name)
            self.assertTrue(name.startswith(git.TASK_BRANCH_PREFIX))
            self.assertLessEqual(len(name), len(git.TASK_BRANCH_PREFIX) + git.MAX_SLUG + 9)

    def test_no_task_text_can_escape_the_agent_prefix(self):
        for task in ("../main", "refs/heads/main", "-b evil", "; rm -rf /"):
            name = git.task_branch_name(task, "s1")
            self.assertTrue(name.startswith(git.TASK_BRANCH_PREFIX), name)
            self.assertNotIn("/", name[len(git.TASK_BRANCH_PREFIX):], name)


class TaskBranchArgvTests(Scripted):
    """What moving HEAD asks git to do, checked before any repository exists."""

    def a_repository(self, branch="main", porcelain=""):
        self.answer("rev-parse --is-inside-work-tree", out="true\n")
        self.answer("rev-parse --show-toplevel", out=str(self.root).replace("\\", "/") + "\n")
        self.answer("rev-parse --short HEAD", out="abc1234\n")
        self.answer("symbolic-ref --short -q HEAD", out=branch + "\n")
        self.answer("status --porcelain", out=porcelain)

    def a_free_name(self, name="agent/task-fix-login-s1"):
        self.answer("rev-parse --verify --quiet refs/heads/" + name, err="", code=1)
        self.answer("checkout -b " + name, out="Switched to a new branch '" + name + "'\n")
        return name

    def test_a_task_branch_is_created_and_head_moves_onto_it(self):
        self.a_repository()
        name = self.a_free_name()
        out = git.start_task_branch(self.root, "fix login", "s1")
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["created"])
        self.assertEqual(out["branch"], name)
        self.assertEqual(out["from"], "main",
                         "the caller has to know which branch to offer to go back to")
        self.assertIn("checkout -b " + name, self.asked())

    def test_a_name_being_taken_is_refused_rather_than_joined(self):
        """Two sessions sharing a branch would mix their commits, and neither rollback could tell
        the other's files from its own."""
        self.a_repository()
        self.answer("rev-parse --verify --quiet refs/heads/agent/task-fix-login-s1",
                    out="0123456789abcdef0123456789abcdef01234567\n")
        out = git.start_task_branch(self.root, "fix login", "s1")
        self.assertFalse(out["ok"])
        self.assertIn("already exists", out["reason"])
        self.assertNotIn("checkout -b agent/task-fix-login-s1", self.asked())

    def test_being_already_on_the_branch_is_okay_and_creates_nothing(self):
        self.a_repository(branch="agent/task-fix-login-s1")
        self.answer("rev-parse --verify --quiet refs/heads/agent/task-fix-login-s1",
                    out="0123456789abcdef0123456789abcdef01234567\n")
        out = git.start_task_branch(self.root, "fix login", "s1")
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["created"])
        self.assertFalse(any(line.startswith("checkout") for line in self.asked()))

    def test_a_detached_head_refuses_because_there_is_nothing_to_return_to(self):
        self.answer("rev-parse --is-inside-work-tree", out="true\n")
        self.answer("rev-parse --show-toplevel", out=str(self.root).replace("\\", "/") + "\n")
        self.answer("rev-parse --short HEAD", out="abc1234\n")
        self.answer("symbolic-ref --short -q HEAD", out="", code=1)
        self.answer("status --porcelain", out="")
        out = git.start_task_branch(self.root, "fix login", "s1")
        self.assertFalse(out["ok"])
        self.assertIn("detached", out["reason"])
        self.assertFalse(any(line.startswith("checkout") for line in self.asked()))

    def test_a_plain_folder_is_a_reason_and_no_write_beyond_the_probe(self):
        self.answer("rev-parse --is-inside-work-tree", err="fatal: Not a git repository\n",
                    code=128)
        out = git.start_task_branch(self.root, "t", "s")
        self.assertFalse(out["ok"])
        self.assertIn("not a git repository", out["reason"].lower())
        self.assertFalse(any(line.startswith("checkout") for line in self.asked()))

    def test_a_checkout_git_refused_is_reported_in_gits_own_words(self):
        self.a_repository()
        name = self.a_free_name()
        self.replies["checkout -b " + name] = Completed(
            "", "fatal: a branch named '" + name + "' already exists\n", 128)
        out = git.start_task_branch(self.root, "fix login", "s1")
        self.assertFalse(out["ok"])
        self.assertIn("already exists", out["reason"])
        self.assertEqual(out["from"], "", "a branch that was not reached is not a way back")

    def test_returning_to_a_branch_never_forces(self):
        """`checkout -f` would throw away uncommitted work the agent never wrote."""
        self.a_repository()
        self.answer("rev-parse --verify --quiet refs/heads/main",
                    out="0123456789abcdef0123456789abcdef01234567\n")
        self.answer("checkout main", out="Switched to branch 'main'\n")
        out = git.switch_branch(self.root, "main")
        self.assertTrue(out["ok"], out)
        for command, _ in self.calls:
            self.assertNotIn("-f", command)
            self.assertNotIn("--force", command)
            self.assertNotIn("--hard", command)

    def test_a_branch_name_from_the_ui_cannot_smuggle_an_option(self):
        """The name arrives from a browser. Anything git would parse as a flag, or use to pick its
        own namespace, dies before an argv is built."""
        for wanted in ("-b evil", "--force", "", "  ", "a\nb", "../elsewhere",
                       ".git/config", "refs/heads/main", "x" * 200, "UPPER", "main; rm",
                       "-x", "--", "@{", ".hidden"):
            out = git.switch_branch(self.root, wanted)
            self.assertFalse(out["ok"], wanted)
            self.assertIn("not a branch name", out["reason"])
        self.assertEqual(self.asked(), [], "no git process at all for a rejected name")

    def test_a_name_that_reads_like_a_verb_stays_a_positional_argument(self):
        """`checkout` is a legal branch name, and it must reach git where it cannot be read as the
        subcommand. The name is scoped to refs/heads/ and passed after the verb, never as argv[1]."""
        self.a_repository()
        self.answer("rev-parse --verify --quiet refs/heads/checkout", err="", code=1)
        out = git.switch_branch(self.root, "checkout")
        self.assertFalse(out["ok"])
        self.assertIn("no local branch", out["reason"])
        self.assertEqual([command[1 + len(git.PREFIX):] for command, _ in self.calls][-1],
                          ["rev-parse", "--verify", "--quiet", "refs/heads/checkout"])

    def test_a_missing_branch_is_named_rather_than_created(self):
        self.a_repository()
        self.answer("rev-parse --verify --quiet refs/heads/develop", err="", code=1)
        out = git.switch_branch(self.root, "develop")
        self.assertFalse(out["ok"])
        self.assertIn("no local branch", out["reason"])
        self.assertFalse(any(line.startswith("checkout ") for line in self.asked()))

    def test_already_on_the_named_branch_is_a_success_with_no_switch(self):
        self.a_repository()
        out = git.switch_branch(self.root, "main")
        self.assertTrue(out["ok"])
        self.assertIn("already on", out["reason"])
        self.assertFalse(any(line.startswith("checkout ") for line in self.asked()))


class LiveBranchTests(unittest.TestCase):
    """Against a real git, because what `checkout` does to a dirty working tree is exactly the
    part a scripted argv cannot prove."""

    IDENTITY = LiveRepositoryTests.IDENTITY

    def setUp(self):
        if git.git_program() is None:
            self.skipTest("git is not installed")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.addCleanup(self.temp.cleanup)
        git.forget()
        self.addCleanup(git.forget)
        self.git("init", "-q")
        self.write("a.py")
        self.git("add", "--", "a.py")
        self.git("commit", "-q", "-m", "one")
        self.base = self.git("rev-parse", "--abbrev-ref", "HEAD")

    def git(self, *args):
        finished = subprocess.run([git.git_program(), *self.IDENTITY, *args],
                                  cwd=str(self.root), env=git.env(), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        self.assertEqual(finished.returncode, 0, finished.stdout.decode("utf-8", "replace"))
        return finished.stdout.decode("utf-8", "replace").strip()

    def write(self, name, body="x = 1\n"):
        (self.root / name).write_text(body, encoding="utf-8", newline="\n")

    def head(self):
        return self.git("symbolic-ref", "--short", "HEAD")

    def test_a_real_task_branch_carries_the_uncommitted_work_across(self):
        self.write("a.py", "x = 2\n")
        out = git.start_task_branch(self.root, "make a.py two", "sess-1")
        self.assertTrue(out["ok"], out["reason"])
        self.assertEqual(self.head(), out["branch"])
        self.assertEqual(out["from"], self.base)
        self.assertIn("x = 2", (self.root / "a.py").read_text(encoding="utf-8"),
                      "creating a branch must not touch the work in progress")
        self.assertIn("a.py", self.git("status", "--porcelain"))

    def test_a_real_return_moves_head_and_leaves_the_tree_alone(self):
        self.write("b.py", "y = 1\n")
        out = git.start_task_branch(self.root, "add b", "sess-2")
        self.assertTrue(out["ok"], out["reason"])
        back = git.switch_branch(self.root, out["from"])
        self.assertTrue(back["ok"], back["reason"])
        self.assertEqual(self.head(), self.base)
        self.assertTrue((self.root / "b.py").exists(), "an untracked file rides through a switch")

    def test_two_tasks_with_the_same_words_do_not_share_a_branch(self):
        first = git.start_task_branch(self.root, "add b", "sess-3")
        self.assertTrue(first["ok"], first["reason"])
        self.write("c.py", "z = 1\n")
        second = git.start_task_branch(self.root, "add b", "sess-4")
        self.assertTrue(second["ok"], second["reason"])
        self.assertNotEqual(first["branch"], second["branch"])

    def test_a_real_detached_head_still_refuses(self):
        self.write("a.py", "x = 2\n")
        self.git("add", "--", "a.py")
        self.git("commit", "-q", "-m", "two")
        self.git("checkout", "-q", "--detach", "HEAD~1")
        out = git.start_task_branch(self.root, "t", "s")
        self.assertFalse(out["ok"])
        self.assertIn("detached", out["reason"])
        # `symbolic-ref` exits non-zero on a detached HEAD, which is the proof nothing moved.
        stayed = subprocess.run([git.git_program(), "--no-optional-locks", "symbolic-ref",
                                 "--short", "HEAD"], cwd=str(self.root), env=git.env(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(stayed.returncode, 0, "a refusal must not create or move a branch")


class RestoreArgvTests(Scripted):
    """`checkout <hash> -- <path>` is the one write here that throws bytes away, so its argv is
    the whole safety argument and it is checked before any repository exists."""

    def a_repository(self):
        self.answer("rev-parse --is-inside-work-tree", out="true\n")
        self.answer("rev-parse --show-toplevel", out=str(self.root).replace("\\", "/") + "\n")
        self.answer("rev-parse --short HEAD", out="abc1234\n")
        self.answer("symbolic-ref --short -q HEAD", out="main\n")
        self.answer("status --porcelain", out="")
        self.answer("rev-parse --verify --quiet 9f3c21a^{commit}",
                    out="9f3c21a11111111111111111111111111111111\n")

    def test_each_named_file_is_restored_on_its_own(self):
        self.a_repository()
        self.answer("checkout 9f3c21a -- src/a.py", out="")
        self.answer("checkout 9f3c21a -- src/b.py", out="")
        out = git.restore_paths(self.root, "9f3c21a", ["src/a.py", "src/b.py"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["restored"], ["src/a.py", "src/b.py"])
        self.assertIn("checkout 9f3c21a -- src/a.py", self.asked())
        self.assertIn("checkout 9f3c21a -- src/b.py", self.asked())

    def test_nothing_in_this_call_can_discard_more_than_it_named(self):
        """The commands that would are the ones the documentation refuses to run."""
        self.a_repository()
        self.answer("checkout 9f3c21a -- src/a.py", out="")
        git.restore_paths(self.root, "9f3c21a", ["src/a.py", "src/a.py", ""])
        for line in self.asked():
            self.assertNotIn("reset", line)
            self.assertNotIn("clean", line)
            self.assertNotIn("--force", line)
            self.assertNotIn(" -- .", line)
        asked_for_a = [command for command, _ in self.calls if command[-1] == "src/a.py"]
        self.assertEqual(len(asked_for_a), 1, "a repeated path is asked for once")

    def test_a_word_instead_of_a_hash_never_reaches_git(self):
        """`HEAD~3` and a branch name are both claims about history this program cannot see, and a
        name could point somewhere else by the time the click lands."""
        for commit in ("HEAD", "HEAD~3", "main", "9f3c21a^{tree}", "", "  ", "gghh"):
            out = git.restore_paths(self.root, commit, ["a.py"])
            self.assertFalse(out["ok"], commit)
            self.assertIn("not a commit", out["reason"])
        self.assertEqual(self.asked(), [])

    def test_a_path_that_leaves_the_folder_is_dropped_before_git_sees_it(self):
        self.a_repository()
        self.answer("checkout 9f3c21a -- src/ok.py", out="")
        out = git.restore_paths(self.root, "9f3c21a",
                               ["../outside.py", "C:\\evil.py", ".git/config", "src/ok.py"])
        self.assertEqual(out["restored"], ["src/ok.py"])
        self.assertEqual(out["skipped"], [])
        joined = " ".join(" ".join(command) for command, _ in self.calls)
        self.assertNotIn("../outside.py", joined)
        self.assertNotIn(".git/config", joined)

    def test_a_commit_git_does_not_know_is_named_and_changes_nothing(self):
        self.a_repository()
        self.answer("rev-parse --verify --quiet 9f3c21a^{commit}", err="", code=1)
        out = git.restore_paths(self.root, "9f3c21a", ["src/a.py"])
        self.assertFalse(out["ok"])
        self.assertIn("no commit 9f3c21a", out["reason"])
        self.assertFalse(any(line.startswith("checkout ") for line in self.asked()))

    def test_one_file_git_cannot_restore_does_not_stop_the_others(self):
        self.a_repository()
        self.answer("checkout 9f3c21a -- src/a.py", out="")
        self.answer("checkout 9f3c21a -- src/gone.py",
                    err="error: pathspec 'src/gone.py' did not match any file(s) known to git\n",
                    code=1)
        out = git.restore_paths(self.root, "9f3c21a", ["src/a.py", "src/gone.py"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["restored"], ["src/a.py"])
        self.assertEqual(out["skipped"], ["src/gone.py"])

    def test_a_request_with_no_usable_path_is_a_reason_not_a_checkout(self):
        self.a_repository()
        out = git.restore_paths(self.root, "9f3c21a", ["../x.py"])
        self.assertFalse(out["ok"])
        self.assertIn("no file inside this folder", out["reason"])
        self.assertFalse(any(line.startswith("checkout ") for line in self.asked()))

    def test_a_plain_folder_is_a_reason(self):
        self.answer("rev-parse --is-inside-work-tree", err="fatal: Not a git repository\n",
                    code=128)
        out = git.restore_paths(self.root, "9f3c21a", ["a.py"])
        self.assertFalse(out["ok"])
        self.assertIn("not a git repository", out["reason"].lower())


class LiveRestoreTests(unittest.TestCase):
    """Against a real git, because whether a later edit really is replaced is the promise under test."""

    IDENTITY = LiveRepositoryTests.IDENTITY

    def setUp(self):
        if git.git_program() is None:
            self.skipTest("git is not installed")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / "repo"
        (self.root / "src").mkdir(parents=True)
        self.addCleanup(self.temp.cleanup)
        git.forget()
        self.addCleanup(git.forget)
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
        (self.root / "src" / "keep.py").write_text("k = 1\n", encoding="utf-8", newline="\n")
        self.git("add", "--", "src/a.py", "src/keep.py")
        self.git("commit", "-q", "-m", "one")
        self.before = self.git("rev-parse", "--short", "HEAD")
        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8", newline="\n")
        self.git("add", "--", "src/a.py")
        self.git("commit", "-q", "-m", "two")

    def git(self, *args):
        finished = subprocess.run([git.git_program(), *self.IDENTITY, *args],
                                  cwd=str(self.root), env=git.env(), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        self.assertEqual(finished.returncode, 0, finished.stdout.decode("utf-8", "replace"))
        return finished.stdout.decode("utf-8", "replace").strip()

    def read(self, name):
        return (self.root / "src" / name).read_text(encoding="utf-8")

    def test_a_restored_file_goes_back_and_its_neighbour_is_left_alone(self):
        (self.root / "src" / "keep.py").write_text("k = 99\n", encoding="utf-8", newline="\n")
        out = git.restore_paths(self.root, self.before, ["src/a.py"])
        self.assertTrue(out["ok"], out["reason"])
        self.assertEqual(self.read("a.py"), "x = 1\n")
        self.assertEqual(self.read("keep.py"), "k = 99\n",
                         "someone else's uncommitted edit is not this call's business")
        status = self.git("status", "--porcelain").splitlines()
        # `checkout <hash> -- <path>` writes the bytes and the index, so a restore that moves a file
        # back behind HEAD shows as staged. That is git's contract, and the chip has to say so rather
        # than promise a clean tree.
        self.assertTrue(any(line.startswith("M") and line.endswith("src/a.py") for line in status), status)
        self.assertIn(" M src/keep.py", status, "the neighbour's edit stays unstaged")

    def test_a_file_that_did_not_exist_in_that_commit_is_skipped_not_fatal(self):
        (self.root / "src" / "new.py").write_text("n = 1\n", encoding="utf-8", newline="\n")
        self.git("add", "--", "src/new.py")
        self.git("commit", "-q", "-m", "three")
        out = git.restore_paths(self.root, self.before, ["src/new.py", "src/a.py"])
        self.assertEqual(out["skipped"], ["src/new.py"], out)
        self.assertEqual(out["restored"], ["src/a.py"])
        self.assertTrue(out["ok"])

    def test_a_head_that_is_not_here_names_the_commit_and_moves_nothing(self):
        out = git.restore_paths(self.root, "deadbeef", ["src/a.py"])
        self.assertFalse(out["ok"])
        self.assertIn("no commit deadbeef", out["reason"])
        self.assertEqual(self.read("a.py"), "x = 2\n")


class ControllerChipTests(unittest.TestCase):
    def test_the_snapshot_carries_only_the_fields_the_chip_draws(self):
        import os
        os.environ.pop("GIT_DIR", None)
        with tempfile.TemporaryDirectory() as temp:
            from ai_code_engineer.webapp.controller import AgentController
            controller = AgentController(Path(temp))
            self.addCleanup(controller.close)
            self.assertEqual(controller.snapshot()["git"], {}, "no folder, nothing to report")
            controller.repo = str(Path(temp))
            answer = {"known": True, "repo": True, "branch": "main", "detached": False,
                      "head": "abc1234", "toplevel": "", "dirty": 3,
                      "paths": ["a.py", "b.py", "c.py", "d.py"], "reason": ""}
            with patch("ai_code_engineer.git_integration.status", return_value=answer):
                info = controller.snapshot()["git"]
            self.assertEqual(info, {"repo": True, "branch": "main", "detached": False,
                                    "head": "abc1234", "dirty": 3})
            self.assertNotIn("paths", str(info), "the file list stays on the server")


if __name__ == "__main__":
    unittest.main()
