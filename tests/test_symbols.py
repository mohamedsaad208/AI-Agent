"""The symbol index has to be true about the code, not merely detailed."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import symbols, workspace
from ai_code_engineer.workspace import Workspace, clear_index_cache

PYTHON = '''
"""Module docstring mentioning class NotAThing."""
import os
from pathlib import Path
from .auth import tokens

class Base:
    pass

class Service(Base):
    def __init__(self, store):
        self.store = store

    def login(self, user, password):
        return self.store.check(user, password)

    class Inner:
        def helper(self):
            return 1

def build(config, *, dry_run=False):
    return None

async def fetch(session):
    return None

if os.environ:
    def conditional():
        pass
'''

JAVA = '''
package com.acme.auth;

import com.acme.db.UserStore;
import java.util.List;

/** A class in a comment: NotReal. */
public class AuthService {
    private static final String NAME = "class InAString {";
    // public void commentedOut() {
    public AuthService(UserStore users) {
        this.users = users;
    }

    public String login(String user, String password) {
        List<String> rows = store.find(user);
        return rows.toString();
    }

    void audit(String user) {
        log(user);
    }

    private static String hash(String value) {
        return value;
    }
}

record Credentials(String user, String password) {}
'''

KOTLIN = '''
package com.acme.web

import com.acme.auth.AuthService

data class LoginRequest(val email: String, val password: String)

object Registry {
    fun of(name: String): AuthService = TODO()
}

fun main(args: Array<String>) {
    println("hi")
}
'''


TYPESCRIPT = '''import { Repo } from "./repo"
import type { Token } from "../auth/token"
import express from "express"
const legacy = require("./legacy")

// class Commented {}
const TEXT = "interface InAString {}"
const MULTI = `
class InATemplate {}
`

export const login = async (user: string, pass: string): Promise<Token> => {
  return repo.find(user)
}

export default class AuthService extends Repo {
  constructor(store: Repo) {
    super(store)
  }

  async login(user: string, password: string): Promise<string> {
    return this.store.find(user)
  }
}

export interface Session { id: string }
enum Role { Admin, User }
type Id = string

function helper(a, b) { return a + b }
'''

GO_SOURCE = '''package auth

import (
	"errors"
	custom "github.com/acme/internal/token"
	// "commented"
)

import "strings"

// func Hidden() {}

type Store struct {
	users map[string]string
}

type Reader interface {
	Read(id string) error
}

func (s *Store) Save(id string, val string) error {
	if strings.Contains(id, "x") {
		return errors.New("bad")
	}
	return custom.Parse(id)
}

func Load(path string) (*Store, error) {
	return nil, nil
}

func main() {}
'''

RUST_SOURCE = '''use std::collections::HashMap;
use crate::auth::token::{Token, Claims as C};
use super::error::AppError;

pub struct Registry {
    items: HashMap<String, Token>,
}

pub trait Store {
    fn load(&self, key: &str) -> Option<String>;
}

impl Registry {
    pub fn new() -> Self {
        Registry { items: HashMap::new() }
    }

    pub async fn save(&mut self, key: String) -> Result<(), AppError> {
        Ok(())
    }
}

impl Store for Registry {
    fn load(&self, key: &str) -> Option<String> {
        let unused = |x: u32| x + 1;
        None
    }
}

// fn commented() {}

fn free(x: i32) -> i32 { x }

mod tests {
    fn inner() {}
}
'''


def row_for(path: str, source: str) -> dict:
    return symbols.parse(path, source)


def at_the_cap(tail: str) -> str:
    """A Java file of `MAX_TYPES` top-level classes plus `tail`, which lands past the cap."""
    return "".join("public class C%d {\n    public void inside%d() {\n    }\n}\n\n" % (i, i)
                   for i in range(symbols.MAX_TYPES)) + tail


class PythonIndexTests(unittest.TestCase):
    def setUp(self):
        self.row = row_for("pkg/service.py", PYTHON)

    def test_classes_methods_and_bases_come_from_the_ast(self):
        names = [item["name"] for item in self.row["types"]]
        self.assertEqual(names, ["Base", "Service", "Service.Inner"])
        service = self.row["types"][1]
        self.assertEqual(service["extends"], ["Base"])
        self.assertEqual(service["members"], ["__init__(store)", "login(user, password)"])
        self.assertEqual(self.row["functions"][:2], ["build(config, dry_run)", "fetch(session)"])

    def test_a_string_and_a_comment_cannot_declare_a_class(self):
        self.assertNotIn("NotAThing", [item["name"] for item in self.row["types"]])

    def test_imports_are_kept_as_written(self):
        self.assertEqual(self.row["imports"],
                         ["os", "pathlib.Path", ".auth.tokens"])

    def test_a_file_that_does_not_parse_falls_back_to_the_lexical_scan(self):
        broken = "def ok(:\n\nclass HalfWritten:\n    def method(self):\n"
        row = row_for("broken.py", broken)
        self.assertEqual(row["kind"], "lexical")
        self.assertIn("HalfWritten", [item["name"] for item in row["types"]])


class JvmIndexTests(unittest.TestCase):
    def setUp(self):
        self.row = row_for("src/main/java/com/acme/auth/AuthService.java", JAVA)

    def test_package_imports_and_types(self):
        self.assertEqual(self.row["package"], "com.acme.auth")
        self.assertEqual(self.row["imports"], ["com.acme.db.UserStore", "java.util.List"])
        self.assertEqual([(item["name"], item["kind"]) for item in self.row["types"]],
                         [("AuthService", "class"), ("Credentials", "record")])

    def test_only_declarations_become_methods(self):
        self.assertEqual(self.row["types"][0]["members"],
                         ["AuthService(UserStore users)", "login(String user, String password)",
                          "audit(String user)", "hash(String value)"])

    def test_comments_and_string_contents_cannot_open_a_block(self):
        # The comment line and the string constant both contain a brace and a signature.
        self.assertNotIn("commentedOut", " ".join(self.row["types"][0]["members"]))
        self.assertEqual(self.row["types"][1]["members"], [])

    def test_a_call_inside_a_method_body_is_not_a_method(self):
        text = " ".join(self.row["types"][0]["members"])
        self.assertNotIn("find(", text)
        self.assertNotIn("toString(", text)

    def test_kotlin_types_and_file_level_functions(self):
        row = row_for("src/main/kotlin/Registry.kt", KOTLIN)
        self.assertEqual(row["package"], "com.acme.web")
        self.assertEqual([item["name"] for item in row["types"]], ["LoginRequest", "Registry"])
        self.assertEqual(row["types"][1]["members"], ["of(name: String)"])
        self.assertEqual(row["functions"], ["main(args: Array<String>)"])

    def test_a_type_past_the_cap_does_not_become_a_file_level_function(self):
        # The cap refuses the type; that must not hand the type's own methods to `functions`,
        # because a small model reads that bucket as "this file has a free function here".
        row = row_for("Many.java", at_the_cap(
            "public class After {\n    public String afterCapMethod() {\n        return null;\n"
            "    }\n}\n"))
        self.assertEqual(len(row["types"]), symbols.MAX_TYPES)
        self.assertEqual(row["types"][-1]["name"], "C%d" % (symbols.MAX_TYPES - 1))
        self.assertEqual(row["functions"], [])
        self.assertNotIn("afterCapMethod", str(row["types"]))
        text = symbols.render([row], ["Many.java"])
        self.assertNotIn("afterCapMethod", text)
        self.assertNotIn("functions:", text)

    def test_a_record_past_the_cap_is_not_mistaken_for_a_function(self):
        # A record header has parentheses, so a scanner that lets the declaration line fall
        # through to the method branch invents a free function named after the record.
        row = row_for("Late.java", at_the_cap("public record Late(int a, int b) {}\n"))
        self.assertEqual(row["functions"], [])
        self.assertNotIn("Late", [item["name"] for item in row["types"]])

    def test_a_type_under_the_cap_keeps_every_method_it_declared(self):
        row = row_for("Many.java", at_the_cap(""))
        self.assertEqual(len(row["types"]), symbols.MAX_TYPES)
        self.assertEqual([item["members"] for item in row["types"]][:2],
                         [["inside0()"], ["inside1()"]])
        self.assertEqual(row["functions"], [])

    def test_a_type_declared_twice_is_listed_once(self):
        row = row_for("Two.java", "class One {\n    void one() {\n    }\n}\n"
                                  "class One {\n    void other() {\n    }\n}\n")
        self.assertEqual([item["name"] for item in row["types"]], ["One"])


class DependencyTests(unittest.TestCase):
    def test_only_project_internal_edges_survive(self):
        rows = [row_for("src/main/java/com/acme/auth/AuthService.java", JAVA),
                row_for("src/main/java/com/acme/db/UserStore.java",
                        "package com.acme.db;\npublic class UserStore {}\n")]
        edges = symbols.dependencies(rows)
        self.assertEqual(edges[rows[0]["path"]], [rows[1]["path"]])
        self.assertEqual(edges[rows[1]["path"]], [])

    def test_a_relative_python_import_resolves_to_its_package(self):
        rows = [row_for("pkg/service.py", PYTHON), row_for("pkg/auth/tokens.py", "X = 1\n")]
        edges = symbols.dependencies(rows)
        self.assertEqual(edges[rows[0]["path"]], [rows[1]["path"]])

    def test_an_unrelated_name_match_does_not_create_an_edge(self):
        rows = [row_for("a/service.py", "import json\n")]
        self.assertEqual(symbols.dependencies(rows)["a/service.py"], [])


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.rows = [row_for("src/main/java/com/acme/auth/AuthService.java", JAVA),
                     row_for("pkg/service.py", PYTHON)]
        self.files = ["pom.xml", "src/main/java/com/acme/auth/AuthService.java",
                      "README.md", "pkg/service.py"]

    def test_every_visible_file_appears_and_sources_carry_their_symbols(self):
        text = symbols.render(self.rows, self.files)
        for name in self.files:
            self.assertIn(name, text)
        self.assertIn("class AuthService: AuthService(UserStore users), login(", text)
        self.assertIn("class Service extends Base: __init__(store), login(user, password)", text)
        lines = text.splitlines()
        self.assertEqual(lines[lines.index("pom.xml") + 1], "")   # nothing invented under it

    def test_a_member_list_that_would_run_away_says_so(self):
        many = "class Big {\n" + "".join("    public void m%d() {\n    }\n" % i
                                         for i in range(40)) + "}\n"
        row = row_for("Big.java", many)
        text = symbols.render([row], ["Big.java"])
        self.assertIn("+17 more", text)
        self.assertLessEqual(len(row["types"][0]["members"]), symbols.MAX_MEMBERS)

    def test_the_map_stops_at_its_budget_and_names_what_it_dropped(self):
        files = ["f%03d.py" % i for i in range(200)]
        rows = [row_for(name, "class %s:\n    pass\n" % name.replace("0", "o"))
                for name in files]
        text = symbols.render(rows, files, limit=2000)
        self.assertLessEqual(len(text), 2400)
        self.assertIn("index truncated", text)

    def test_a_non_source_file_is_not_indexed_at_all(self):
        self.assertIsNone(symbols.parse("pom.xml", "<project/>"))
        self.assertIsNone(symbols.parse("notes.md", "# class Fake"))


class ScriptIndexTests(unittest.TestCase):
    def setUp(self):
        self.row = row_for("src/auth/service.ts", TYPESCRIPT)
        self.text = " ".join(str(item) for item in self.row.values())

    def test_class_with_its_base_and_methods(self):
        auth = next(item for item in self.row["types"] if item["name"] == "AuthService")
        self.assertEqual(auth["kind"], "class")
        self.assertEqual(auth["extends"], ["Repo"])
        self.assertIn("login(user: string, password: string)", auth["members"])
        self.assertIn("constructor(store: Repo)", auth["members"])

    def test_types_interfaces_and_enums_are_named(self):
        found = {item["name"]: item["kind"] for item in self.row["types"]}
        self.assertEqual(found["Session"], "interface")
        self.assertEqual(found["Role"], "enum")
        self.assertEqual(found["Id"], "type")

    def test_top_level_and_arrow_functions(self):
        self.assertIn("helper(a, b)", self.row["functions"])
        self.assertIn("login(user: string, pass: string)", self.row["functions"])

    def test_comments_strings_and_templates_declare_nothing(self):
        for hidden in ("Commented", "InAString", "InATemplate"):
            self.assertNotIn(hidden, self.text)

    def test_a_call_in_a_body_is_not_a_method(self):
        self.assertNotIn("find(", self.text)

    def test_specifiers_are_read_from_the_original_line(self):
        # The blanker erases string contents, and a module path is a string.
        self.assertEqual(self.row["imports"], [".repo", "..auth.token", "express", ".legacy"])

    def test_plain_javascript_indexes_too(self):
        row = row_for("lib/legacy.js", "function go() {}\nmodule.exports = { go }\n")
        self.assertEqual(row["functions"], ["go()"])

    def test_a_generic_class_keeps_its_base_and_its_methods(self):
        """`class MyRepo<T extends Entity> extends BaseRepo<T> implements IRepo<T>`."""
        row = row_for("src/repo.ts",
                      "export class MyRepo<T extends Entity> extends BaseRepo<T> implements IRepo<T> {\n"
                      "  findById(id: string): Promise<T> {\n"
                      "    return this.db.get(id);\n"
                      "  }\n"
                      "}\n")
        repo = next(item for item in row["types"] if item["name"] == "MyRepo")
        self.assertEqual(repo["extends"], ["BaseRepo"])
        self.assertEqual(repo["members"], ["findById(id: string)"])

    def test_a_generic_function_is_not_dropped_with_its_type_parameter(self):
        row = row_for("src/factory.ts",
                      "export function make<T>(x: T): T {\n  return x;\n}\n")
        self.assertEqual(row["functions"], ["make(x: T)"])

    def test_a_nested_type_argument_costs_neither_name_nor_base(self):
        row = row_for("src/cache.ts",
                      "class Nested<K, V extends Map<String, V>> extends Other<K> {\n"
                      "  get(k: K): V {\n    return null;\n  }\n"
                      "}\n"
                      "class Plain {\n  run() {\n  }\n}\n")
        names = {item["name"]: item for item in row["types"]}
        self.assertEqual(names["Nested"]["extends"], ["Other"])
        self.assertEqual(names["Nested"]["members"], ["get(k: K)"])
        self.assertIn("Plain", names, "a class with no generics at all must still be indexed")


class GoIndexTests(unittest.TestCase):
    def setUp(self):
        self.row = row_for("internal/auth/store.go", GO_SOURCE)

    def test_the_package_names_the_folder(self):
        self.assertEqual(self.row["package"], "auth")

    def test_grouped_and_single_imports_and_an_alias(self):
        self.assertIn("errors", self.row["imports"])
        self.assertIn("strings", self.row["imports"])
        self.assertIn("github.com/acme/internal/token", self.row["imports"])
        self.assertNotIn("commented", self.row["imports"])

    def test_a_receiver_decides_the_owning_type(self):
        store = next(item for item in self.row["types"] if item["name"] == "Store")
        self.assertEqual(store["kind"], "struct")
        self.assertEqual(store["members"], ["Save(id string, val string)"])
        self.assertNotIn("Save(id string, val string)", self.row["functions"])

    def test_functions_without_a_receiver(self):
        self.assertEqual(self.row["functions"], ["Load(path string)", "main()"])

    def test_a_commented_func_is_not_a_declaration(self):
        self.assertNotIn("Hidden", str(self.row))


class RustIndexTests(unittest.TestCase):
    def setUp(self):
        self.row = row_for("src/auth/registry.rs", RUST_SOURCE)

    def test_use_paths_become_dotted_and_lose_their_root(self):
        self.assertEqual(self.row["imports"], ["std.collections.HashMap", "auth.token.Token",
                                               "auth.token.Claims", ".error.AppError"])

    def test_structs_traits_and_modules(self):
        found = {item["name"]: item["kind"] for item in self.row["types"]}
        self.assertEqual(found["Registry"], "struct")
        self.assertEqual(found["Store"], "trait")
        self.assertEqual(found["tests"], "mod")

    def test_impl_for_attaches_its_functions_to_the_type(self):
        registry = next(item for item in self.row["types"] if item["name"] == "Registry")
        self.assertIn("new()", registry["members"])
        self.assertIn("save(&mut self, key: String)", registry["members"])
        self.assertIn("load(&self, key: &str)", registry["members"])

    def test_a_trait_keeps_its_own_required_method(self):
        store = next(item for item in self.row["types"] if item["name"] == "Store")
        self.assertEqual(store["members"], ["load(&self, key: &str)"])

    def test_free_functions_and_module_bodies(self):
        self.assertEqual(self.row["functions"], ["free(x: i32)"])
        self.assertNotIn("commented", str(self.row))


class MapDependencyTests(unittest.TestCase):
    def test_a_relative_script_import_finds_the_file(self):
        rows = [row_for("src/auth/service.ts", TYPESCRIPT),
                row_for("src/auth/repo.ts", "export class Repo {}\n"),
                row_for("src/auth/token.ts", "export interface Token { id: string }\n"),
                row_for("src/express/index.ts", "export const express = 1\n")]
        edges = symbols.dependencies(rows)
        self.assertEqual(edges["src/auth/service.ts"], ["src/auth/repo.ts", "src/auth/token.ts"])

    def test_a_package_import_finds_the_folder_it_names(self):
        rows = [row_for("internal/auth/store.go", GO_SOURCE),
                row_for("internal/token/parse.go", "package token\n\nfunc Parse(s string) {}\n")]
        self.assertEqual(symbols.dependencies(rows)["internal/auth/store.go"],
                         ["internal/token/parse.go"])

    def test_a_use_path_finds_the_module_that_declares_the_type(self):
        rows = [row_for("src/auth/registry.rs", RUST_SOURCE),
                row_for("src/auth/token.rs", "pub struct Token;\n"),
                row_for("src/std/collections.rs", "pub fn grow() {}\n")]
        edges = symbols.dependencies(rows)["src/auth/registry.rs"]
        self.assertIn("src/auth/token.rs", edges)
        # Nothing in the project declares `std::collections::HashMap`, so that edge is not kept.
        self.assertNotIn("src/std/collections.rs", edges)

    def test_from_x_import_name_still_resolves_for_python(self):
        rows = [row_for("pkg/service.py", "from pkg.util.text import slug\n"),
                row_for("pkg/util/text.py", "def slug(x):\n    return x\n")]
        self.assertEqual(symbols.dependencies(rows)["pkg/service.py"], ["pkg/util/text.py"])


class WorkspaceMapTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        (self.repo / "src" / "main" / "java" / "com" / "acme").mkdir(parents=True)
        (self.repo / "src/main/java/com/acme/AuthService.java").write_text(JAVA, encoding="utf-8")
        (self.repo / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        self.ws = Workspace(self.repo)

    def test_the_repo_map_is_the_rendered_index(self):
        text = self.ws.repo_map()
        self.assertIn("package com.acme.auth", text)
        self.assertIn("login(String user, String password)", text)
        self.assertIn("pom.xml", text)
        self.assertNotIn("symbols (lexical)", text)

    def test_protected_files_stay_out_of_the_map(self):
        (self.repo / "token.json").write_text('{"aws": "SHOULD_NOT_APPEAR"}', encoding="utf-8")
        self.assertNotIn("SHOULD_NOT_APPEAR", self.ws.repo_map())

    def test_go_and_rust_sources_are_readable_text(self):
        (self.repo / "main.go").write_text(GO_SOURCE, encoding="utf-8")
        (self.repo / "registry.rs").write_text(RUST_SOURCE, encoding="utf-8")
        ws = Workspace(self.repo)
        self.assertIn("func (s *Store) Save", ws.read("main.go")["content"])
        self.assertIn("pub struct Registry", ws.read("registry.rs")["content"])
        text = ws.repo_map()
        self.assertIn("struct Store: Save(id string, val string)", text)
        self.assertIn("struct Registry: new(), save(&mut self, key: String), load(&self, key: &str)",
                      text)
        self.assertIn("trait Store", text)
        self.assertIn("package auth", text)


class IndexCacheTests(unittest.TestCase):
    def setUp(self):
        clear_index_cache()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "project"
        self.repo.mkdir()
        (self.repo / "src").mkdir()
        self.file = self.repo / "src" / "service.ts"
        self.file.write_text(TYPESCRIPT, encoding="utf-8")
        (self.repo / "notes.md").write_text("# notes\n", encoding="utf-8")
        self.reads: list[str] = []
        self.ws = self._workspace(self.repo)

    def _workspace(self, root: Path) -> Workspace:
        ws = Workspace(root)
        real = ws.read

        def counted(relative):
            self.reads.append(relative)
            return real(relative)

        ws.read = counted
        return ws

    def test_the_second_map_parses_nothing_again(self):
        self.assertIn("class AuthService", self.ws.repo_map())
        self.assertEqual(self.reads, ["src/service.ts"])
        self.reads.clear()
        self.assertIn("class AuthService", self.ws.repo_map())
        self.assertEqual(self.reads, [])

    def test_a_file_that_is_not_source_is_never_read_for_the_map(self):
        self.ws.repo_map()
        self.assertNotIn("notes.md", self.reads)

    def test_an_edited_file_is_parsed_again(self):
        self.ws.repo_map()
        self.reads.clear()
        self.file.write_text(TYPESCRIPT + "\nclass Added {}\n", encoding="utf-8")
        text = self.ws.repo_map()
        self.assertEqual(self.reads, ["src/service.ts"])
        self.assertIn("class Added", text)

    def test_a_touch_with_the_same_size_still_rereads(self):
        self.ws.repo_map()
        self.reads.clear()
        info = self.file.stat()
        os.utime(self.file, (info.st_atime + 10, info.st_mtime + 10))
        self.ws.repo_map()
        self.assertEqual(self.reads, ["src/service.ts"])

    def test_a_deleted_file_leaves_the_map_and_the_cache(self):
        self.ws.repo_map()
        self.file.unlink()
        self.assertNotIn("AuthService", self.ws.repo_map())
        self.assertEqual([key for key in workspace.INDEX_CACHE], [])

    def test_two_projects_never_share_rows(self):
        self.assertIn("class AuthService", self.ws.repo_map())
        other = self.base / "other"
        (other / "src").mkdir(parents=True)
        (other / "src" / "service.ts").write_text("export class Different {}\n", encoding="utf-8")
        text = self._workspace(other).repo_map()
        self.assertIn("class Different", text)
        self.assertNotIn("AuthService", text)
        self.assertIn("class AuthService", self.ws.repo_map())

    def test_clearing_one_root_leaves_the_other(self):
        self.ws.repo_map()
        other = self.base / "second"
        (other / "src").mkdir(parents=True)
        (other / "src" / "service.ts").write_text(TYPESCRIPT, encoding="utf-8")
        self._workspace(other).repo_map()
        self.assertEqual(len(workspace.INDEX_CACHE), 2)
        removed = clear_index_cache(self.repo)
        self.assertEqual(removed, 1)
        self.assertEqual([key[0] for key in workspace.INDEX_CACHE], [str(other.resolve())])


if __name__ == "__main__":
    unittest.main()
