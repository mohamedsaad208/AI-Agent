"""The path policy is the safety boundary: every file a model names passes through here,
so what this file pins is which requests become paths and which never leave the root."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import workspace
from ai_code_engineer.errors import MissingFileError, PolicyError
from ai_code_engineer.workspace import (INDEX_CACHE, MAX_FILE_BYTES, Workspace,
                                        clear_index_cache, digest, ensure_project_dir)


JAVA = ("public class AuthController {\n"
        "    String login(String user) { return user; }\n"
        "}\n")
SLASH = chr(92)          # a literal backslash, spelled so the file stays readable on Windows


class PathPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "repo"
        (self.root / "src" / "main" / "java").mkdir(parents=True)
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        (self.root / "src/main/java/AuthController.java").write_text(JAVA, encoding="utf-8")
        self.ws = Workspace(self.root)

    def refuse(self, name, reason, **kwargs):
        """Refuse it *and* say why: five different gates answer to one PolicyError, and a
        request that falls through to the wrong one has stopped being protected by design."""
        with self.assertRaises(PolicyError) as caught:
            self.ws.path(name, **kwargs)
        self.assertIn(reason, str(caught.exception), msg=name)

    def test_an_admitted_source_file_resolves_to_a_path_inside_the_root(self):
        """The returned Path is what read and write act on, so containment is the whole deal."""
        found = self.ws.path("src/main/java/AuthController.java")
        self.assertEqual(found, self.root / "src" / "main" / "java" / "AuthController.java")
        self.assertTrue(found.is_relative_to(self.ws.root))

    def test_only_the_allowlisted_text_suffixes_are_admitted(self):
        for name in ("setup.exe", "image.png", "notes", "core.dll", "a.py.bak"):
            with self.subTest(name=name):
                self.refuse(name, "Only supported text files are accessible:")
        for name in ("build.gradle", "pom.xml", "schema.sql", "run.sh", "App.kt"):
            with self.subTest(allowed=name):
                self.ws.path(name)

    def test_a_suffix_less_text_name_is_admitted_because_the_gate_is_about_bytes(self):
        """.gitignore is the file this run's agent was asked for and could not write: the gate
        exists to stop binary blobs being read as source, not to veto names without a dot."""
        for name in (".gitignore", ".gitattributes", ".editorconfig", ".dockerignore",
                     "Dockerfile", "DOCKERFILE", "Makefile", "Jenkinsfile", "LICENSE", "README",
                     "src/main/docker/Dockerfile"):
            with self.subTest(name=name):
                self.ws.path(name)
                self.ws.path(name, writable=True)
        # A name that is neither a text suffix nor a known suffix-less text file stays out:
        # the allowance is a closed list, not "anything without an extension".
        for name in ("setup", "notes.txt.bak", "gradlew", "LICENSE.exe", "sub/x"):
            with self.subTest(refused=name):
                self.refuse(name, "Only supported text files are accessible:")
        # The credential gates run before this one, so a suffix-less secret never reaches it.
        self.refuse(".env", "Protected path.")
        self.refuse(".env.production", "Protected path.")

    def test_the_suffix_gate_folds_case_the_way_the_read_gate_folds_it(self):
        """workspace.py:132 admits any case, and the syntax gates in verification and engine
        fold it too; a second rule here would make APP.PY an uncheckable file."""
        for name in ("APP.PY", "README.MD", "data.JSON", "AuthController.JAVA"):
            with self.subTest(name=name):
                self.ws.path(name)

    def test_a_backslash_spelling_is_refused_rather_than_normalised(self):
        # The web UI's Apply-to-File button refuses backslashes for exactly this reason, so the
        # two halves of the promise have to refuse the same text.
        for name in ("src\\app.py", "C:" + SLASH + "Windows\\win.ini", ".." + SLASH + "x.py"):
            with self.subTest(name=name):
                self.refuse(name, "Use a relative path with forward slashes.")

    def test_a_drive_letter_or_stream_spelling_is_refused(self):
        for name in ("C:/app.py", "d:/other/x.py", "app.py:Stream", ":app.py"):
            with self.subTest(name=name):
                self.refuse(name, "Use a relative path with forward slashes.")

    def test_an_absolute_posix_path_is_refused(self):
        self.refuse("/app.py", "Absolute paths, traversal and ambiguous paths are blocked.")
        self.refuse("//server/share/app.py", "Absolute paths, traversal and ambiguous paths")

    def test_traversal_and_ambiguous_segments_are_refused(self):
        # "a./" and "a /" are the segments Windows folds away for us; leaving them in means the
        # name the reviewer read is not the name the filesystem opens.
        for name in ("../app.py", "src/../app.py", "./app.py", "sub//app.py",
                     "a./b.py", "a /b.py", "app.py.", "app.py "):
            with self.subTest(name=name):
                self.refuse(name, "Absolute paths, traversal and ambiguous paths are blocked.")

    def test_a_request_outside_the_path_budget_or_not_a_string_is_refused(self):
        for name in ("", "a" * 401, None, 7, b"app.py"):
            with self.subTest(name=repr(name)):
                self.refuse(name, "Invalid workspace path.")

    def test_a_build_or_tool_directory_is_protected_even_spelled_differently(self):
        # These are where a checkout keeps other people's bytes; case folding is what stops
        # ".GIT/config" being a different answer from ".git/config".
        for name in (".git/config.txt", "NODE_MODULES/lib/index.js", "Target/out.py",
                     ".venv/lib/site.py", "build/libs/Text.java", "__pycache__/m.py",
                     ".idea/workspace.xml", "dist/bundle.js"):
            with self.subTest(name=name):
                self.refuse(name, "Protected path.")

    def test_a_credential_shaped_name_is_refused_but_a_source_file_naming_one_is_not(self):
        for name in ("token.json", "my-secrets.json", "credentials.yml", "id_rsa.key",
                     "prod-passwords.txt", ".env"):
            with self.subTest(refused=name):
                self.refuse(name, "Protected path.")
        # The same words inside an identifier are ordinary source; refusing them would make
        # half of any auth codebase unreadable.
        for name in ("JwtTokenProvider.java", "PasswordValidator.kt", "keystore.py"):
            with self.subTest(allowed=name):
                self.ws.path(name)

    def test_an_alias_shaped_name_is_refused_by_shape_before_it_is_resolved(self):
        """NTFS opens an 8.3 alias for the same bytes, so a name that looks like one is never
        a file anyone reviewed — even one that does not exist yet."""
        for name in ("TOKEN~1.JSON", "GIT~1/config.txt", "NODE_M~1/index.js", "src/App~2.java"):
            with self.subTest(name=name):
                self.refuse(name, "Protected path.")

    def test_a_windows_device_name_is_refused(self):
        for name in ("NUL.txt", "CON", "com1.py", "lpt9.txt", "sub/AUX.md"):
            with self.subTest(name=name):
                self.refuse(name, "Device paths are blocked.")
        self.ws.path("config.py")      # the same prefix, an ordinary file

    def test_writable_requests_refuse_the_files_an_agent_would_read_back(self):
        # A model that rewrites these gains a hook that outlives the session, so they stay
        # readable and never writable.
        for name in ("AGENTS.md", "CLAUDE.md", "QODER.md", "mcp.json", ".mcp.json", "skills.md"):
            with self.subTest(name=name):
                self.ws.path(name)                                # reading is allowed
                self.refuse(name, "read-only", writable=True)
        # The suffix-less ones never reach the write gate at all: the suffix gate refuses them
        # for reading too, which is why only the text-shaped rule files are readable above.
        for name in (".cursorrules", "cursorrules", "windsurfrules"):
            with self.subTest(name=name):
                self.refuse(name, "Only supported text files are accessible:")

    def test_a_policy_folder_blocks_the_root_and_not_somebodys_package(self):
        for name in ("policies/README.md", "skills/drafts.md", "Policies/x.json"):
            with self.subTest(name=name):
                self.ws.path(name)                                 # readable, as everything is
                self.refuse(name, "read-only", writable=True)
        for name in ("src/main/java/com/acme/policies/TokenPolicy.java",
                     "app/skills/loader.py"):
            with self.subTest(name=name):
                self.ws.path(name, writable=True)

    def test_the_rule_file_gate_folds_the_name_the_way_the_suffix_gate_does(self):
        for name in ("AGENTS.MD", "Agents.Md", "MCP.JSON", "qoder.Md"):
            with self.subTest(name=name):
                self.refuse(name, "read-only", writable=True)

    def test_a_directory_a_tool_acts_on_is_read_only(self):
        # .vscode/tasks.json runs programs and .github/workflows/ci.yml runs them on a push, so
        # nothing under those names may be written by a proposal.
        for name in (".vscode/tasks.json", ".github/workflows/ci.yml", ".zed/keymap.json",
                     ".claude/settings.json", "policies/README.md"):
            with self.subTest(name=name):
                self.ws.path(name)
                self.refuse(name, "read-only", writable=True)

    def test_an_ordinary_source_file_stays_writable(self):
        for name in ("src/main/java/AuthController.java", "docs/rules.md", "app.py",
                     "NewFile.PY", "settings.gradle"):
            with self.subTest(name=name):
                self.ws.path(name, writable=True)

    def test_a_link_is_refused_before_it_is_ever_resolved(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "plain.py").write_text("x = 1\n", encoding="utf-8")
        try:
            (self.root / "link.py").symlink_to(outside / "plain.py")
            (self.root / "dirlink").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable on this Windows account")
        for name in ("link.py", "dirlink/plain.py"):
            with self.subTest(name=name):
                self.refuse(name, "Symlinks and reparse points are blocked.")

    def test_a_link_the_reparse_check_misses_still_cannot_leave_the_root(self):
        """The containment test runs on the resolved path as well, so one missed reparse type
        — Windows keeps adding them — does not turn into a read outside the project."""
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "plain.py").write_text("x = 1\n", encoding="utf-8")
        try:
            (self.root / "dirlink").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable on this Windows account")
        with patch("ai_code_engineer.workspace.is_link", return_value=False):
            self.refuse("dirlink/plain.py", "Path escapes workspace.")

    def test_a_workspace_must_be_a_directory_that_is_there(self):
        with self.assertRaises(PolicyError):
            Workspace(self.root / "app.py")
        with self.assertRaises(OSError):
            Workspace(self.root / "nowhere")


class ReadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        self.root.mkdir()
        self.file = self.root / "app.py"
        self.file.write_bytes(b"answer = 1\n")       # exact bytes: the content is what is hashed
        self.ws = Workspace(self.root)

    def test_a_read_returns_the_hash_of_the_bytes_on_disk(self):
        """Every write guard compares against this value, so it has to be the bytes, now."""
        read = self.ws.read("app.py")
        self.assertEqual(read["sha256"], digest(self.file.read_bytes()))
        self.assertEqual(read["path"], "app.py")
        self.assertEqual(read["content"], "answer = 1\n")

    def test_a_missing_file_is_a_different_answer_from_a_refused_one(self):
        # The window offers "create this file" for the first case only; the second must never
        # look like an empty file waiting to be written.
        with self.assertRaises(MissingFileError):
            self.ws.read("nope.py")
        with self.assertRaises(PolicyError) as caught:
            self.ws.read("../app.py")
        self.assertNotIsInstance(caught.exception, MissingFileError)

    def test_a_file_over_the_read_cap_is_refused_rather_than_truncated(self):
        # Half a file is what a model would rewrite silently; the cap is stated in the message.
        (self.root / "big.py").write_bytes(b"# " + b"x" * MAX_FILE_BYTES)
        with self.assertRaises(PolicyError) as caught:
            self.ws.read("big.py")
        self.assertIn("128 KiB", str(caught.exception))
        self.assertEqual(MAX_FILE_BYTES, 128 * 1024)
        (self.root / "exact.py").write_bytes(b"# " + b"x" * (MAX_FILE_BYTES - 2))
        self.assertEqual(len(self.ws.read("exact.py")["content"]), MAX_FILE_BYTES)

    def test_a_binary_or_non_utf8_file_is_refused(self):
        # A NUL is a binary file arriving whole into a diff view; text that is not UTF-8 would
        # decode into mojibake the model then writes back. Each one has to say which it is.
        (self.root / "blob.py").write_bytes(b"a=1\x00b=2")
        with self.assertRaises(PolicyError) as caught:
            self.ws.read("blob.py")
        self.assertIn("binary", str(caught.exception))
        # A UTF-16 file from a Windows tool carries NULs, so it is refused here and never
        # reaches the decode below.
        (self.root / "utf16.txt").write_bytes("value = 1\n".encode("utf-16"))
        with self.assertRaises(PolicyError) as caught:
            self.ws.read("utf16.txt")
        self.assertIn("binary", str(caught.exception))
        (self.root / "latin.py").write_bytes(b"a = caf\xe9\n")
        with self.assertRaises(PolicyError) as caught:
            self.ws.read("latin.py")
        refusal = str(caught.exception)
        # The refusal has to be actionable: the file it names, the exact byte, and its offset, so the
        # developer can open the editor at that position instead of guessing which line is cp1252.
        self.assertIn("UTF-8", refusal)
        self.assertIn("latin.py", refusal)
        self.assertIn("0xe9", refusal)
        self.assertIn("offset 7", refusal)
        self.assertIn("Convert", refusal)
        # And a file that IS valid UTF-8 with non-ASCII in it must still read whole — the guard against
        # "fixing" this by decoding leniently, which is what corrupts a later write. (Code points rather
        # than an Arabic literal, the way this suite keeps its source ASCII.)
        hello = "".join(map(chr, [0x0645, 0x0631, 0x062D, 0x0628, 0x0627]))
        (self.root / "arabic.properties").write_text("greeting=" + hello + "\n", encoding="utf-8",
                                                     newline="\n")
        self.assertEqual(self.ws.read("arabic.properties")["content"], "greeting=" + hello + "\n")

    def test_a_directory_named_like_a_source_file_is_not_one(self):
        # The suffix gate is text, not shape: without the regular-file check a read would hand
        # back a directory and the write path would try to replace it.
        (self.root / "pkg.py").mkdir()
        with self.assertRaises(PolicyError) as caught:
            self.ws.read("pkg.py")
        self.assertIn("not a regular file", str(caught.exception))


class WriteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_bytes(b"answer = 1\n")
        self.ws = Workspace(self.root)

    def test_a_write_needs_the_hash_the_reviewer_saw_and_returns_the_new_one(self):
        current = self.ws.read("app.py")["sha256"]
        written = self.ws.write("app.py", "answer = 2\n", current)
        self.assertEqual(written, digest(b"answer = 2\n"))
        self.assertEqual((self.root / "app.py").read_bytes(), b"answer = 2\n")

    def test_a_write_over_a_moved_file_is_cancelled_and_the_file_is_left_alone(self):
        self.ws.write("app.py", "answer = 2\n", self.ws.read("app.py")["sha256"])
        with self.assertRaises(PolicyError) as caught:
            self.ws.write("app.py", "answer = 3\n", "stale-hash")
        self.assertIn("changed since review", str(caught.exception))
        self.assertEqual((self.root / "app.py").read_bytes(), b"answer = 2\n")

    def test_a_new_file_needs_no_expected_hash_and_makes_its_own_folders(self):
        self.ws.write("src/main/java/New.java", "class New {}\n", None)
        self.assertEqual((self.root / "src/main/java/New.java").read_bytes(), b"class New {}\n")

    def test_an_oversized_replacement_is_refused_before_the_file_is_touched(self):
        current = self.ws.read("app.py")["sha256"]
        with self.assertRaises(PolicyError):
            self.ws.write("app.py", "x" * (MAX_FILE_BYTES + 1), current)
        self.assertEqual((self.root / "app.py").read_bytes(), b"answer = 1\n")

    def test_a_cancelled_write_leaves_no_temporary_file_behind(self):
        # The stage-and-replace buffer sits inside the project: abandoned, it is both a leak
        # and a file the next listing would hand to the model.
        with self.assertRaises(PolicyError):
            self.ws.write("app.py", "answer = 9\n", "wrong")
        self.ws.write("app.py", "answer = 2\n", self.ws.read("app.py")["sha256"])
        self.assertEqual([p.name for p in self.root.rglob(".agent-write-*")], [])

    def test_an_instruction_file_cannot_be_written_even_through_the_write_path(self):
        with self.assertRaises(PolicyError):
            self.ws.write("AGENTS.md", "# obey me\n", None)
        self.assertFalse((self.root / "AGENTS.md").exists())


class ListingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src/app.py").write_text("answer = 1\n", encoding="utf-8")
        (self.root / "src/util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        (self.root / "notes.md").write_text("# notes\n", encoding="utf-8")
        (self.root / "image.png").write_bytes(b"\x89PNG\r\n")
        (self.root / ".git").mkdir()
        (self.root / ".git/app.py").write_text("hidden = 1\n", encoding="utf-8")
        (self.root / "token.json").write_text('{"aws": "SHOULD_NOT_APPEAR"}', encoding="utf-8")
        self.ws = Workspace(self.root)

    def test_the_listing_is_forward_slash_relative_paths_that_the_sandbox_can_rebuild(self):
        # verification.snapshot() joins these onto a temp folder, so a separator change here
        # becomes a wrong file inside the container.
        self.assertEqual(self.ws.files(), ["notes.md", "src/app.py", "src/util.py"])

    def test_a_protected_directory_is_not_walked_at_all(self):
        # Pruning is a different guarantee from filtering afterwards: a walk into .git would
        # list object paths the policy never admits.
        asked = []
        real = self.ws.path

        def spy(relative, **kwargs):
            asked.append(relative)
            return real(relative, **kwargs)

        self.ws.path = spy
        self.ws.files()
        self.assertNotIn(".git/app.py", asked, "the directory was never even offered")
        self.assertNotIn("token.json", self.ws.files())

    def test_the_listing_honours_the_limit_it_is_given(self):
        self.assertEqual(len(self.ws.files(limit=2)), 2)

    def test_a_search_reports_the_line_it_matched_and_caps_both_the_text_and_the_results(self):
        (self.root / "long.py").write_text("needle " + "z" * 400 + "\n", encoding="utf-8")
        hit = self.ws.search("NEEDLE zzz")[0]
        self.assertEqual(hit["path"], "long.py")
        self.assertEqual(hit["line"], 1)
        self.assertEqual(len(hit["text"]), 300, "one long line is not a context window")
        (self.root / "many.py").write_text("needle\n" * 100, encoding="utf-8")
        self.assertEqual(len(self.ws.search("needle")), 40)

    def test_a_search_query_outside_the_allowed_size_is_refused(self):
        for query in ("", "x" * 201, None):
            with self.subTest(query=repr(query)):
                with self.assertRaises(PolicyError):
                    self.ws.search(query)

    def test_a_repo_map_row_is_cached_per_project_and_can_be_forgotten(self):
        # The cache is keyed by resolved root so two projects never share declaration rows;
        # clear_index_cache exists for the caller that moved a folder on disk.
        clear_index_cache()
        self.addCleanup(clear_index_cache)
        roots = []
        bodies = {"p1": "def one():\n    return 1\n", "p2": "def two():\n    return 2\n"}
        for name, body in bodies.items():
            project = self.root / name
            project.mkdir()
            (project / "m.py").write_text(body, encoding="utf-8")
            Workspace(project).repo_map()
            roots.append(project.resolve())
        self.assertEqual({Path(key[0]) for key in INDEX_CACHE}, set(roots))
        self.assertEqual(workspace.INDEX_CACHE_LIMIT, 300, "the cap that bounds the cache")
        self.assertEqual(clear_index_cache(roots[0]), 1)
        self.assertEqual({Path(key[0]) for key in INDEX_CACHE}, {roots[1]})
        self.assertEqual(clear_index_cache(), 1)
        self.assertEqual(INDEX_CACHE, {})


class ProjectFolderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def test_only_an_absolute_folder_inside_a_drive_can_be_chosen(self):
        for name in ("relative/dir", "C:/", "C:" + SLASH, "/"):
            with self.subTest(name=repr(name)):
                with self.assertRaises(PolicyError):
                    ensure_project_dir(name)

    def test_an_existing_non_empty_folder_is_not_a_new_project(self):
        # Choosing it must never turn into writing into a project nobody inspected.
        taken = self.base / "full"
        taken.mkdir()
        (taken / "x.py").write_text("a = 1\n", encoding="utf-8")
        with self.assertRaises(PolicyError) as caught:
            ensure_project_dir(taken)
        self.assertIn("already contains files", str(caught.exception))
        afile = self.base / "file.py"
        afile.write_text("a = 1\n", encoding="utf-8")
        with self.assertRaises(PolicyError) as caught:
            ensure_project_dir(afile)
        self.assertIn("is a file", str(caught.exception))

    def test_a_chosen_folder_is_created_and_the_path_comes_back_resolved(self):
        fresh = self.base / "brand new"
        self.assertEqual(ensure_project_dir(fresh), fresh.resolve())
        self.assertTrue(fresh.is_dir())
        empty = self.base / "empty"
        empty.mkdir()
        self.assertEqual(ensure_project_dir(empty), empty.resolve())


if __name__ == "__main__":
    unittest.main()
