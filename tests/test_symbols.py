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


PROVIDER = """package com.example.auth;

import com.example.shared.Token;

public class JwtTokenProvider {
    private final Token token;

    public JwtTokenProvider(Token token) { this.token = token; }

    public String login(String user) { return token.sign(user); }
}
"""

CONSUMER = """package com.example.web;

import com.example.auth.JwtTokenProvider;

class LoginEndpoint {
    String go() { return new JwtTokenProvider(null).login("a"); }

    // JwtTokenProvider was renamed in 2.0, so do not use it here
    String note = "JwtTokenProvider is only the old name";
}
"""


class SymbolLookupTests(unittest.TestCase):
    """`find_symbol`: the index can already answer "where is this declared", and nothing asked it."""

    def rows(self):
        return [row_for("auth/JwtTokenProvider.java", PROVIDER),
                row_for("web/LoginEndpoint.java", CONSUMER)]

    def test_a_class_is_found_with_the_file_and_the_line_it_is_declared_on(self):
        hits = symbols.find_symbol(self.rows(), "JwtTokenProvider")
        types = [hit for hit in hits if hit["kind"] == "class"]
        self.assertEqual([hit["path"] for hit in types], ["auth/JwtTokenProvider.java"])
        self.assertGreater(types[0]["line"], 0)
        self.assertIn("member", [hit["kind"] for hit in hits],
                      "the constructor is declared under the same name, and a lookup that hid it "
                      "would send a model to edit the type while its own file disagrees")

    def test_a_method_is_found_with_the_type_that_owns_it(self):
        hits = [hit for hit in symbols.find_symbol(self.rows(), "login") if hit["kind"] == "member"]
        self.assertEqual([(hit["name"], hit["in"]) for hit in hits],
                         [("login", "JwtTokenProvider")])
        self.assertIn("login(String user)", hits[0]["signature"])

    def test_a_name_that_is_declared_nowhere_returns_nothing(self):
        """The first version of this function collected every row and filtered afterwards, so an
        unanswered lookup came back as the whole project — the worst possible answer for a small model
        deciding what to read next."""
        self.assertEqual(symbols.find_symbol(self.rows(), "NoSuchService"), [])
        self.assertEqual(symbols.find_symbol(self.rows(), ""), [])

    def test_an_exact_name_is_offered_before_a_name_thatmerely_contains_it(self):
        rows = self.rows() + [row_for("web/TokenProviderFactory.java",
                                      "class TokenProviderFactory {\n}\n")]
        hits = symbols.find_symbol(rows, "TokenProvider")
        self.assertEqual(hits[0]["name"], "JwtTokenProvider", "a substring match outranked a real one")
        self.assertTrue(all("tokenprovider" in hit["name"].casefold() for hit in hits), hits)

    def test_a_type_declared_inside_another_is_found_by_its_own_name(self):
        rows = [row_for("web/Outer.java", "class Outer {\n  static class Inner {\n  }\n}\n")]
        hits = symbols.find_symbol(rows, "Inner")
        self.assertEqual([hit["name"] for hit in hits], ["Inner"],
                         "the JVM parser records a nested type by the name the source uses")


class ReferenceTests(unittest.TestCase):
    """`find_references`: the roles are the answer, not the count — `search_code` already gives counts."""

    def sources(self):
        return [("auth/JwtTokenProvider.java", PROVIDER), ("web/LoginEndpoint.java", CONSUMER)]

    def rows(self):
        return [row_for("auth/JwtTokenProvider.java", PROVIDER),
                row_for("web/LoginEndpoint.java", CONSUMER)]

    def sites(self, name="JwtTokenProvider", **kwargs):
        return symbols.find_references(name, self.sources(), self.rows(), **kwargs)

    def test_the_declaration_the_import_and_the_call_are_told_apart(self):
        kinds = {(site["path"], site["kind"]) for site in self.sites()}
        self.assertIn(("auth/JwtTokenProvider.java", "declaration"), kinds)
        self.assertIn(("web/LoginEndpoint.java", "import"), kinds)
        self.assertIn(("web/LoginEndpoint.java", "call"), kinds)

    def test_a_comment_and_a_string_are_not_uses(self):
        """The line exists in the file and the name appears in it. Both are still not a reference: the
        parsers already had to learn this, and a lookup that had not would send a model to edit prose."""
        paths = " ".join(site["text"] for site in self.sites())
        self.assertNotIn("was renamed", paths)
        self.assertNotIn("only the old name", paths)

    def test_the_ceilings_are_the_numbers_the_answer_is_named_with(self):
        """`engine` says these two numbers back to the model when it reports a capped lookup, so a
        silent change here changes a sentence there — pinned as the contract it is, not a preference."""
        self.assertEqual((symbols.MAX_HITS, symbols.PER_FILE_LIMIT), (40, 6))

    def test_the_default_answer_is_bounded_at_the_ceiling(self):
        sites = symbols.find_references("C", [("pkg/B%d.java" % n,
                                               "class B {\n  void m() {\n    C.x();\n  }\n}\n")
                                              for n in range(50)], [])
        self.assertEqual(len(sites), symbols.MAX_HITS,
                         "asking with no limit still cannot return an unbounded answer")

    def test_the_method_call_through_an_object_still_counts_as_a_use(self):
        sites = [site for site in self.sites("login") if site["kind"] == "call"]
        self.assertEqual([(site["path"], site["text"][:20]) for site in sites][0][0],
                         "web/LoginEndpoint.java")

    def test_one_busy_file_cannot_eat_the_answer(self):
        """A site is a line, not an occurrence, and the cap counts lines: nine uses spread over nine
        lines is exactly the case where the cap earns its place, because the tenth file would otherwise
        never be shown at all."""
        body = "\n".join(["class B {", "  void m() {"]
                         + ["    C.x();" for _ in range(9)]
                         + ["  }", "}"]) + "\n"
        rows = [row_for("pkg/B.java", body)]
        sites = symbols.find_references("C", [("pkg/B.java", body)], rows, per_file=3)
        self.assertEqual([site["line"] for site in sites], [3, 4, 5])
        every = symbols.find_references("C", [("pkg/B.java", body)], rows, per_file=99)
        self.assertEqual(len(every), 9, "the cap is a cap, not a bug: without it all nine come back")

    def test_the_total_cap_holds_and_a_name_used_nowhere_is_answered_with_nothing(self):
        self.assertEqual(symbols.find_references("NothingHere", self.sources(), self.rows()), [])
        many = [("pkg/M%d.java" % n, "class M%d {\n  void m() { C.x(); }\n}\n" % n) for n in range(60)]
        self.assertEqual(len(symbols.find_references("C", many, [], limit=40)), 40)


class RankingTests(unittest.TestCase):
    """Which files a sentence is about — the ask that used to be "did you type the filename"."""

    PROVIDER = ("package com.example.auth;\n\n"
                "import com.example.shared.TokenStore;\n\n"
                "public class JwtTokenProvider {\n"
                "    public String login(String user) { return user; }\n"
                "}\n")
    CONSUMER = ("package com.example.web;\n\n"
                "import com.example.auth.JwtTokenProvider;\n\n"
                "class LoginEndpoint {\n"
                "    String go() { return new JwtTokenProvider().login(\"a\"); }\n"
                "}\n")
    UNRELATED = "package com.example.web;\n\nclass BillingReport {\n    void total() {}\n}\n"

    def rows(self):
        return [row_for("auth/JwtTokenProvider.java", self.PROVIDER),
                row_for("web/LoginEndpoint.java", self.CONSUMER),
                row_for("web/BillingReport.java", self.UNRELATED)]

    def paths(self, task, **kwargs):
        ranked = symbols.rank(self.rows(), task, **kwargs)
        return [(entry["path"], entry["why"]) for entry in ranked]

    def test_a_described_file_is_found_without_being_named(self):
        got = self.paths("why does login return the wrong token?")
        self.assertEqual(got[0], ("auth/JwtTokenProvider.java", "declares"))
        self.assertNotIn("web/BillingReport.java", [path for path, _ in got],
                         "a file with nothing in common with the sentence must not ride along")

    def test_two_words_reach_a_name_written_as_four(self):
        """A person writes "token provider"; the source spells `JwtTokenProvider`. Neither the old
        basename rule nor a whole-word comparison connects them."""
        self.assertIn("auth/JwtTokenProvider.java", [path for path, _ in self.paths("the token provider")]
                      )

    def test_the_named_file_still_outranks_every_inference(self):
        got = self.paths("edit web/LoginEndpoint.java now")
        self.assertEqual(got[0], ("web/LoginEndpoint.java", "names"))

    def test_one_hop_along_an_import_is_enough_to_be_interesting(self):
        got = self.paths("fix the login flow")
        self.assertIn(("web/LoginEndpoint.java", "imports"), got,
                      "the caller of a function being changed is the second file a fix needs")

    def test_a_verb_that_is_also_a_function_name_still_selects_the_file(self):
        """"Fix add" is this tool's own dogfood sentence, and `add` was in the stop list as a verb --
        so the one task the ranking exists for, a name typed in three letters, answered with nothing."""
        rows = [row_for("calc/calculator.py", "def add(a, b):\n    return a + b\n")]
        self.assertEqual([(entry["path"], entry["why"]) for entry in symbols.rank(rows, "Fix add")],
                         [("calc/calculator.py", "defines")])

    def test_a_task_that_names_nothing_answers_with_nothing(self):
        for task in ("", "   ", "the and for with this that", "fix it"):
            self.assertEqual(symbols.rank(self.rows(), task), [], task)

    def test_the_budget_is_spent_on_the_best_score_not_the_first_path(self):
        got = self.paths("login token provider report", limit=1)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], "auth/JwtTokenProvider.java")

    def test_a_tie_breaks_on_the_path_so_two_runs_agree(self):
        same = [row_for("pkg/A.java", "class Alpha {\n}\n"), row_for("pkg/B.java", "class Alpha {\n}\n")]
        first = [entry["path"] for entry in symbols.rank(same, "Alpha")]
        self.assertEqual(first, sorted(first))

    def test_every_reason_it_reports_is_a_reason_the_thread_can_say(self):
        """The engine sends a code and `labels` words it. A code nobody knows would print a bare
        identifier where the explanation should be, in both windows, for one file out of ten thousand."""
        from ai_code_engineer import labels
        used = {entry["why"] for entry in symbols.rank(self.rows(), "login token provider "
                                                       "web BillingReport web/LoginEndpoint.java")}
        self.assertTrue(used, "the fixture stopped producing a ranking at all")
        self.assertTrue(used <= set(labels.CONTEXT_REASON), used - set(labels.CONTEXT_REASON))


class ConfigFactTests(unittest.TestCase):
    """The map's other half: what the build files and the runtime files say about the project.

    These files are not source, so they have no symbols to index -- but they hold the module list, the
    port each service listens on, and the registry it joins, which is what a starting order and a fix
    both need. Three rules hold the feature together: the values come from an allow-list of keys, a key
    that names a credential never yields its value, and a configuration row declares no symbols.
    """

    POM = '''<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <parent><groupId>com.acme</groupId><artifactId>shop-parent</artifactId><version>1.0</version></parent>
  <artifactId>auth-service</artifactId>
  <dependencies>
    <dependency><groupId>com.acme</groupId><artifactId>common-lib</artifactId></dependency>
    <dependency><groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId></dependency>
  </dependencies>
</project>
'''

    def facts(self, path, source):
        return symbols.config_facts(path, source)

    def test_a_pom_that_declares_its_namespaces_still_answers(self):
        """Every real pom carries the Maven namespace and an `xsi:schemaLocation`. Stripping the
        `xmlns` attributes from the text left that attribute's prefix unbound, the parse died, and the
        whole JVM half of the map came out empty."""
        got = dict(self.facts("auth-service/pom.xml", self.POM))
        self.assertEqual(got["parent"], "shop-parent")
        self.assertEqual(got["artifact"], "auth-service")
        self.assertEqual(got["needs"], "common-lib, spring-boot-starter-web")

    def test_a_reactor_pom_names_the_modules_it_builds(self):
        got = dict(self.facts("pom.xml", '<project xmlns="http://maven.apache.org/POM/4.0.0">'
                                     "<artifactId>shop</artifactId><modules>"
                                     "<module>auth-service</module><module>web</module>"
                                     "</modules></project>"))
        self.assertEqual(got["modules"], "auth-service, web")

    def test_a_pom_with_a_doctype_is_not_parsed(self):
        """An internal DTD expands inside ElementTree, and this file comes from a repository the tool
        was asked to read rather than a build system it trusts. Real poms carry none, so refusing costs
        nothing and closes the expansion."""
        self.assertEqual(self.facts("pom.xml", '<?xml version="1.0"?>\n'
                                              '<!DOCTYPE project [<!ENTITY x "A">]>\n'
                                              "<project><artifactId>&x;</artifactId></project>"), [])

    def test_a_long_dependency_list_is_cut_between_names_not_inside_one(self):
        many = ("<project><artifactId>shop</artifactId><dependencies>"
                + "".join("<dependency><artifactId>spring-boot-starter-module-%02d</artifactId>"
                          "</dependency>" % i for i in range(14)) + "</dependencies></project>")
        value = dict(self.facts("pom.xml", many))["needs"]
        self.assertLessEqual(len(value), symbols.MAX_FACT_CHARS)
        self.assertTrue(value.endswith(", ..."), value)
        for name in value[:-len(", ...")].split(", "):
            self.assertIn(name, [item for item in
                                 ("spring-boot-starter-module-%02d" % i for i in range(14))],
                          "a truncated token is a dependency the repository does not have")

    def test_gradle_names_its_root_its_modules_and_its_needs(self):
        got = dict(self.facts("settings.gradle", "rootProject.name = 'shop'\ninclude ':auth', ':web'\n"))
        self.assertEqual(got["artifact"], "shop")
        self.assertEqual(got["modules"], "auth, web")

    def test_a_gradle_dependency_is_named_by_its_artifact(self):
        """A coordinate is `group:artifact` or `group:artifact:version`, and the name a person types is
        the artifact either way -- `implementation project(':common-lib')` is the internal edge the map
        most wants, and it carries no group at all."""
        got = dict(self.facts("app/build.gradle",
                              "plugins { id 'org.springframework.boot' version '3.2.5' }\n"
                              "dependencies {\n"
                              '    implementation "org.springframework.boot:spring-boot-starter-web"\n'
                              "    testImplementation 'com.h2database:h2:2.2.0'\n"
                              "    implementation project(':common-lib')\n}\n"))
        self.assertEqual(got["needs"], "common-lib, h2, spring-boot-starter-web")

    def test_a_package_json_gives_its_name_scripts_and_dependencies(self):
        got = dict(self.facts("package.json", '{"name": "shop-web", "scripts": '
                                              '{"build": "vite build", "test": "vitest", "dev": "vite"}, '
                                              '"dependencies": {"react": "^18.2.0"}, '
                                              '"devDependencies": {"vitest": "^1.0.0"}}'))
        self.assertEqual(got["artifact"], "shop-web")
        self.assertEqual(got["scripts"], "build, test")
        self.assertEqual(got["needs"], "react")
        self.assertEqual(got["dev-needs"], "vitest")

    def test_go_requires_read_in_both_spellings(self):
        block = ("module github.com/acme/shop\n\ngo 1.22\n\nrequire (\n"
                 "\tgithub.com/gin-gonic/gin v1.9.1\n\tgolang.org/x/jwt v4.5.0 // a comment\n)\n")
        self.assertEqual(dict(self.facts("go.mod", block))["needs"],
                         "github.com/gin-gonic/gin, golang.org/x/jwt")
        self.assertEqual(dict(self.facts("go.mod", "module example.com/a\n"
                                                   "require github.com/x/y v1.0.0\n"))["needs"],
                         "github.com/x/y")

    def test_both_toml_manifests_answer(self):
        """`[package]` with a dependency table is Cargo; `[project]` with a dependency list is PEP 621.
        Cargo.toml is TOML, so routing it to the Go reader meant a Rust project arrived with no facts."""
        cargo = dict(self.facts("Cargo.toml", '[package]\nname = "shop"\nversion = "0.1.0"\n\n'
                                               "[dependencies]\n"
                                               'serde = { version = "1", features = ["derive"] }\n'
                                               'tokio = "1"\n'))
        self.assertEqual(cargo["artifact"], "shop")
        self.assertEqual(cargo["needs"], "serde, tokio")
        python = dict(self.facts("pyproject.toml", '[project]\nname = "shop-tools"\n'
                                                   'dependencies = ["requests>=2.31", '
                                                   '"flask[async]==3.0.0"]\n'))
        self.assertEqual(python["artifact"], "shop-tools")
        self.assertEqual(python["needs"], "flask, requests")

    def test_a_runtime_yaml_gives_port_name_and_the_hosts_it_talks_to(self):
        got = self.facts("auth-service/src/main/resources/application.yml",
                         "server:\n  port: 8081\nspring:\n  application:\n    name: auth-service\n"
                         "  datasource:\n    url: jdbc:postgresql://db.internal:5432/shop\n"
                         "eureka:\n  client:\n    service-url:\n"
                         "      defaultZone: http://localhost:8761/eureka/\n")
        self.assertEqual(got, [("port", "8081"), ("name", "auth-service"),
                               ("host", "db.internal:5432"), ("registry", "localhost:8761")])

    def test_a_nested_parent_owns_the_keys_under_it(self):
        """`application:` is a line with no value, so the first scanner skipped it and every child
        resolved against the last key that *had* one: `spring.application.name` arrived as `port.name`
        and the application's own name silently disappeared from the map."""
        got = dict(self.facts("application.yml", "server:\n  port: 8081\n"
                                                 "spring:\n  application:\n    name: auth-service\n"))
        self.assertEqual(got["name"], "auth-service")

    def test_a_properties_file_answers_the_same_way_as_yaml(self):
        """`server.port=8080` is the same fact with a different separator, and a Spring project written
        in properties used to arrive with no facts at all."""
        got = dict(self.facts("api-gateway/src/main/resources/application.properties",
                              "server.port=8080\nspring.application.name=api-gateway\n"
                              "eureka.client.serviceUrl.defaultZone=http://localhost:8761/eureka/\n"
                              "management.server.port=9091\n"))
        self.assertEqual(got["port"], "8080")
        self.assertEqual(got["name"], "api-gateway")
        self.assertEqual(got["registry"], "localhost:8761")
        self.assertEqual(got["management port"], "9091")

    def test_a_key_that_names_a_credential_never_yields_its_value(self):
        canary = "SHOULD_NOT_APPEAR_4f7b2c"
        cases = [
            ("application.yml", f"server:\n  port: 8080\nspring:\n  datasource:\n"
                                f"    password: {canary}\n    username: {canary}\n"
                                f"  security:\n    user:\n      name: {canary}\n"
                                f"jwt:\n  secret: {canary}\n  token-key: {canary}\n"),
            ("application.properties", f"server.port=8080\nspring.datasource.password={canary}\n"
                                       f"aws.access-key={canary}\napp.token={canary}\n"),
            ("application.yml", f"spring:\n  datasource:\n"
                                f"    url: jdbc:postgresql://admin:{canary}@db.internal:5432/shop\n"),
            ("config.gitlab-ci.yml", f"job:\n  variables:\n    SECRET_TOKEN: {canary}\n"),
        ]
        for path, source in cases:
            flat = " ".join("%s %s" % fact for fact in symbols.config_facts(path, source))
            self.assertNotIn(canary, flat, "%s leaked: %s" % (path, flat))

    def test_the_credential_guard_covers_the_labels_too(self):
        """`_shorten` is the last door: a parser that hands back a label naming a secret is refused
        even when its value is already in hand."""
        self.assertIsNone(symbols._shorten("password", "hunter2"))
        self.assertIsNone(symbols._shorten("access-key", "AKIA123"))
        self.assertEqual(symbols._shorten("artifact", "auth-service"), ("artifact", "auth-service"))

    def test_a_name_only_means_the_application_when_its_owner_says_so(self):
        """`logging.file.name` is a log file. Reported as `name`, it becomes a wrong fact about the
        project that the model has no way to doubt, so the allow-list matches whole dotted paths."""
        self.assertEqual(symbols.config_facts("application.yml",
                                              "logging:\n  file:\n    name: app.log\n"), [])

    def test_an_inline_comment_is_not_part_of_the_value(self):
        got = dict(self.facts("application.yml", "server:\n  port: 8080   # the api port\n"))
        self.assertEqual(got["port"], "8080")

    def test_only_the_files_that_describe_the_project_are_read(self):
        wanted = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "package.json",
                  "go.mod", "Cargo.toml", "pyproject.toml", "application.yml", "application.yaml",
                  "bootstrap.properties", "src/main/resources/application-dev.yml")
        noise = ("docker-compose.yml", "logback.xml", "ci.yml", "notes.md", "src/main.yml",
                 "application.py", "openapi.yaml", ".gitignore")
        for path in wanted:
            self.assertTrue(symbols.noteworthy(path), path)
        for path in noise:
            self.assertFalse(symbols.noteworthy(path), path)

    def test_a_config_row_declares_no_symbols(self):
        """A pom that lists `auth-service` must not come back as a file that *declares* auth-service:
        that is the confusion the row split keeps out of every name query in the module."""
        row = symbols.config_row("auth-service/pom.xml", self.POM)
        self.assertEqual(row["kind"], "config")
        self.assertEqual(row["types"], [])
        self.assertEqual(row["functions"], [])
        self.assertEqual(symbols.find_symbol([row], "auth-service"), [])

    def test_a_module_directory_still_outranks_its_own_pom(self):
        """The pom is not source, but it does sit in the folder the task named, and that is a true
        reason to read it -- so the ranking reaches it as `module`, never as a declaration."""
        rows = [symbols.config_row("auth-service/pom.xml", self.POM)]
        ranked = symbols.rank(rows, "auth-service")
        self.assertEqual([entry["why"] for entry in ranked], ["module"])

    def test_the_map_prints_facts_under_the_path(self):
        row = symbols.config_row("pom.xml", self.POM)
        text = symbols.render([row], ["pom.xml"])
        self.assertIn("pom.xml\n  parent: shop-parent\n  artifact: auth-service", text)

    def test_a_file_with_nothing_to_say_gets_no_block(self):
        self.assertIsNone(symbols.config_row("application.yml", "# nothing here yet\n"))

    def test_facts_are_counted_in_the_map_budget(self):
        """Facts are the map's newest spend and the file list is the oldest: a project of a thousand
        poms must still fit, and what it drops has to be named rather than quietly left out."""
        rows = [symbols.config_row("m%03d/pom.xml" % i, "<project><artifactId>m%03d</artifactId>"
                                                            "<dependencies>"
                                                            "<dependency><artifactId>lib-a"
                                                            "</artifactId></dependency>"
                                                            "<dependency><artifactId>lib-b"
                                                            "</artifactId></dependency>"
                                                            "</dependencies></project>")
                for i in range(120)]
        files = ["m%03d/pom.xml" % i for i in range(120)]
        text = symbols.render(rows, files, limit=2000)
        self.assertLessEqual(len(text), 2400)
        self.assertIn("index truncated", text)


class GraphTests(unittest.TestCase):
    """The picture: modules, the dependencies between them, and a column that means build order."""

    def java(self, folder, name, imports):
        body = "package %s;\n" % folder + "".join("import %s;\n" % item for item in imports)
        return symbols.parse("%s/%s.java" % (folder, name), body + "class %s {\n}\n" % name)

    def rows(self, *rows):
        return [row for row in rows if row is not None]

    def test_a_column_is_one_past_the_deepest_thing_a_module_needs(self):
        """The leftmost column is what compiles first. Inverting it would draw a reactor backwards, and
        the one thing the sheet claims to show — the build order — would be wrong in every detail."""
        chain = self.rows(self.java("gateway", "Gateway", ["auth.TokenStore", "auth.Jwt"]),
                          self.java("auth", "Jwt", ["core.Keys"]),
                          self.java("core", "Keys", []))
        got = {node["name"]: node["column"] for node in symbols.graph(chain)["nodes"]}
        self.assertEqual(got, {"core": 0, "auth": 1, "gateway": 2})

    def test_edges_point_at_what_a_module_needs_and_carry_a_file_count(self):
        edges = symbols.graph(self.rows(self.java("web", "A", ["svc.One"]),
                                        self.java("web", "B", ["svc.Two"]),
                                        self.java("svc", "One", []),
                                        self.java("svc", "Two", [])))["edges"]
        self.assertEqual([(edge["from"], edge["to"], edge["count"]) for edge in edges],
                         [("web", "svc", 2)])

    def test_a_module_importing_its_own_sibling_is_not_an_edge(self):
        got = symbols.graph(self.rows(self.java("svc", "One", ["svc.Two"]),
                                      self.java("svc", "Two", [])))
        self.assertEqual(got["edges"], [])
        self.assertEqual([node["name"] for node in got["nodes"]], ["svc"])

    def test_a_config_row_contributes_no_node_and_no_edge(self):
        """A pom lists dependencies, but they are Maven's words about artifacts, not an import a file
        made. Drawing it as an edge would put a library the code never named next to the modules."""
        pom = symbols.config_row("shared/pom.xml", '<project><artifactId>shared</artifactId></project>')
        code = self.java("svc", "One", ["shared.Helper"])
        got = symbols.graph([code, pom])
        self.assertNotIn("shared", [node["name"] for node in got["nodes"]])
        self.assertEqual([edge["to"] for edge in got["edges"]], [])

    def test_a_cycle_is_broken_at_one_node_and_said_out_loud(self):
        """Two Maven modules that import each other have no longest path. The layering still finishes —
        it breaks the loop somewhere — and the result says the column is approximate rather than letting
        the picture imply an order the project does not have."""
        pair = self.rows(self.java("left", "L", ["right.R"]), self.java("right", "R", ["left.L"]))
        got = symbols.graph(pair)
        self.assertTrue(got["cyclic"])
        self.assertEqual(sorted(node["name"] for node in got["nodes"]), ["left", "right"])
        self.assertEqual({node["column"] for node in got["nodes"]}, {0, 1})

    def test_the_heaviest_modules_are_drawn_and_the_rest_are_counted(self):
        rows = self.rows(*[self.java("m%02d" % i, "C%d" % i, []) for i in range(40)])
        got = symbols.graph(rows)
        self.assertLessEqual(len(got["nodes"]), symbols.MAX_GRAPH_NODES)
        self.assertEqual(got["hidden"], 40 - len(got["nodes"]))

    def test_a_dropped_module_takes_its_edges_with_it(self):
        """An edge to a box that is not on the sheet would hang an arrow in empty space, and a module
        left out but still pointed at reads as a dependency nobody has."""
        rows = self.rows(self.java("big", "A", []), self.java("big", "B", []))
        rows += self.rows(*[self.java("x%02d" % i, "C%d" % i, []) for i in range(symbols.MAX_GRAPH_NODES)])
        rows.append(self.java("x99", "Late", ["big.A"]))
        got = symbols.graph(rows)
        drawn = {node["name"] for node in got["nodes"]}
        for edge in got["edges"]:
            self.assertIn(edge["from"], drawn)
            self.assertIn(edge["to"], drawn)

    def test_two_runs_of_the_same_index_draw_the_same_picture(self):
        rows = self.rows(self.java("web", "A", ["svc.One"]), self.java("svc", "One", []),
                         self.java("auth", "Two", ["svc.One"]))
        first, second = symbols.graph(rows), symbols.graph(rows)
        self.assertEqual(first, second)

    def test_the_columns_the_sheet_is_sized_by_are_the_layers_plus_one(self):
        got = symbols.graph(self.rows(self.java("a", "A", ["b.B"]), self.java("b", "B", []),
                                      self.java("c", "C", ["d.D"]), self.java("d", "D", [])))
        self.assertEqual(got["columns"], 2, "two independent chains, each two deep")
        self.assertEqual(max(node["column"] for node in got["nodes"]), got["columns"] - 1)

    def test_an_index_of_nothing_answers_with_an_empty_picture(self):
        for rows in ([], None):
            got = symbols.graph(rows)
            self.assertEqual(got["nodes"], [])
            self.assertFalse(got["cyclic"])


class ConfigWorkspaceTests(unittest.TestCase):
    """The same facts through the real gates, where a canary has to survive the walk, the read and the
    render to reach a prompt -- and where a config file must not become a source."""

    POM = ('<project xmlns="http://maven.apache.org/POM/4.0.0"><artifactId>auth-service</artifactId>'
           "<dependencies><dependency><artifactId>common-lib</artifactId></dependency>"
           "</dependencies></project>")
    YML = ("server:\n  port: 8081\nspring:\n  application:\n    name: auth-service\n"
           "  datasource:\n    password: SHOULD_NOT_APPEAR_4f7b2c\n    username: admin\n")

    def setUp(self):
        clear_index_cache()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        (self.repo / "auth-service/src/main/resources").mkdir(parents=True)
        (self.repo / "auth-service/src/main/java/com/acme").mkdir(parents=True)
        (self.repo / "auth-service/pom.xml").write_text(self.POM, encoding="utf-8")
        (self.repo / "auth-service/src/main/resources/application.yml").write_text(
            self.YML, encoding="utf-8")
        (self.repo / "auth-service/src/main/java/com/acme/AuthService.java").write_text(
            JAVA, encoding="utf-8")
        self.ws = Workspace(self.repo)

    def test_the_repo_map_carries_the_pom_and_the_runtime_facts(self):
        text = self.ws.repo_map()
        self.assertIn("artifact: auth-service", text)
        self.assertIn("needs: common-lib", text)
        self.assertIn("port: 8081", text)

    def test_a_credential_in_a_config_file_never_reaches_the_map(self):
        """The map is injected into every task without anyone asking for it and is copied into
        `agent export-session`, so its facts are allow-listed rather than filtered after the fact.
        A file the model reads on purpose is still its own verbatim bytes -- this is the boundary of
        the automatic half, not of reading."""
        self.assertNotIn("SHOULD_NOT_APPEAR_4f7b2c", self.ws.repo_map())
        self.assertIn("SHOULD_NOT_APPEAR_4f7b2c", self.ws.read(
            "auth-service/src/main/resources/application.yml")["content"])

    def test_a_config_file_is_never_a_source_for_a_name_query(self):
        """`register` is a Spring key in `application.yml` before it is a method anywhere, and a
        reference search that spends its hits on configuration answers the wrong question."""
        rows = self.ws.index()[1]
        paths = [path for path, _ in self.ws.sources(rows)]
        self.assertIn("auth-service/src/main/java/com/acme/AuthService.java", paths)
        self.assertFalse([path for path in paths if path.endswith((".yml", ".pom", "pom.xml"))],
                         paths)

    def test_a_config_row_survives_a_restart_of_the_index(self):
        files, rows = self.ws.index()
        again = Workspace(self.repo).index()[1]
        self.assertEqual([row.get("facts") for row in rows if row["kind"] == "config"],
                         [row.get("facts") for row in again if row["kind"] == "config"])


if __name__ == "__main__":
    unittest.main()
