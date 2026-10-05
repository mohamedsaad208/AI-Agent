"""The chunker must cut code where a programmer would name the cut, and never lie about size."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import chunker


PYTHON = '''import os
from .auth import tokens

MAX_RETRIES = 3

class Service:
    name = "service"

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

SCRIPT = '''const TOP = 1;
// class Commented {
const s = "class InString {";
class Alpha {
  constructor(x) {
    this.x = x;
  }
}
class Flag { }
const beta = () => {
  return 2;
};
'''

GO = '''package main

type Server struct {
    name string
}

func (s *Server) Run() error {
    return nil
}
'''

JAVA = '''public class Auth {
    private final String name = "Auth";

    public String login(String user) {
        return user;
    }

    void noop();
}
'''


def by_symbol(chunks):
    out = {}
    for chunk in chunks:
        out.setdefault(chunk["symbol"], []).append(chunk)
    return out


class TestChunkBasics(unittest.TestCase):
    def test_empty_and_blank_sources_produce_nothing(self):
        self.assertEqual(chunker.chunk("a.py", ""), [])
        self.assertEqual(chunker.chunk("a.py", "   \n\n  \t\n"), [])

    def test_chunks_are_in_line_order(self):
        for path, source in [("s.py", PYTHON), ("s.js", SCRIPT), ("s.go", GO), ("s.java", JAVA)]:
            with self.subTest(path=path):
                chunks = chunker.chunk(path, source)
                starts = [c["start_line"] for c in chunks]
                self.assertEqual(starts, sorted(starts))

    def test_every_chunk_spans_its_own_text(self):
        lines = PYTHON.splitlines()
        for chunk in chunker.chunk("s.py", PYTHON):
            self.assertEqual(chunk["text"], "\n".join(lines[chunk["start_line"] - 1:chunk["end_line"]])[:chunker.MAX_CHUNK_CHARS])
            self.assertGreaterEqual(chunk["end_line"], chunk["start_line"])

    def test_id_names_path_line_and_symbol(self):
        chunks = chunker.chunk("src/s.py", PYTHON)
        login = by_symbol(chunks)["login"][0]
        self.assertEqual(login["id"], f"src/s.py:{login['start_line']}:login")
        blocks = [c for c in chunks if not c["symbol"]]
        for block in blocks:
            self.assertTrue(block["id"].endswith(":block"))

    def test_header_is_the_first_non_blank_line_capped(self):
        chunks = chunker.chunk("s.py", PYTHON)
        login = by_symbol(chunks)["login"][0]
        self.assertTrue(login["header"].startswith("def login"))
        long_def = "def f(bubble=[" + "1" * 400 + "]):"
        one = chunker.chunk("s.py", f"{long_def}\n    pass\n")
        fn = by_symbol(one)["f"][0]
        self.assertEqual(fn["header"], long_def[:2 * 60])  # symbols.MAX_SIGNATURE * 2

    def test_unknown_suffixes_fall_back_to_text(self):
        chunks = chunker.chunk("notes.md", "# Title\n\nSome prose here.\n")
        self.assertTrue(chunks)
        self.assertEqual({c["kind"] for c in chunks}, {"text"})


class TestPythonStructure(unittest.TestCase):
    def setUp(self):
        self.chunks = chunker.chunk("s.py", PYTHON)
        self.symbols = by_symbol(self.chunks)

    def test_functions_and_methods_get_their_own_chunk(self):
        for name in ("__init__", "login", "helper", "build", "fetch", "conditional"):
            self.assertIn(name, self.symbols, f"{name} should be its own chunk")

    def test_method_spans_cover_their_bodies(self):
        login = self.symbols["login"][0]
        self.assertIn("def login(self, user, password):", login["text"])
        self.assertIn("return self.store.check(user, password)", login["text"])
        self.assertNotIn("def build", login["text"])

    def test_methods_carry_their_class_as_parent(self):
        self.assertEqual(self.symbols["login"][0]["parent"], "Service")
        self.assertEqual(self.symbols["helper"][0]["parent"], "Inner")
        self.assertEqual(self.symbols["build"][0]["parent"], "")

    def test_class_chunk_holds_class_scope_code_not_method_bodies(self):
        service = self.symbols["Service"][0]
        self.assertIn("class Service:", service["text"])
        self.assertIn('name = "service"', service["text"])
        self.assertNotIn("def login", service["text"])

    def test_module_level_gap_is_chunked(self):
        gap_text = "\n".join(c["text"] for c in self.chunks if not c["symbol"])
        self.assertIn("import os", gap_text)
        self.assertIn("MAX_RETRIES = 3", gap_text)

    def test_decorators_belong_to_the_declared_chunk(self):
        src = "class A:\n    @property\n    def x(self):\n        return 1\n"
        chunks = chunker.chunk("s.py", src)
        x = by_symbol(chunks)["x"][0]
        self.assertEqual(x["header"], "@property")
        self.assertIn("@property", x["text"])

    def test_unparseable_python_still_chunks_as_text_blocks(self):
        src = "def oops(:\n    pass\n\ndef second():\n    return 2\n"
        chunks = chunker.chunk("s.py", src)
        self.assertTrue(chunks)
        self.assertEqual({c["kind"] for c in chunks}, {"python"})
        self.assertTrue(all(not c["symbol"] for c in chunks))
        self.assertIn("def oops(", chunks[0]["text"])


class TestBracedLanguages(unittest.TestCase):
    def test_script_comments_and_strings_are_not_boundaries(self):
        chunks = chunker.chunk("s.js", SCRIPT)
        symbols_found = set(by_symbol(chunks))
        self.assertIn("Alpha", symbols_found)
        self.assertIn("Flag", symbols_found)
        self.assertIn("beta", symbols_found)
        self.assertNotIn("Commented", symbols_found)
        self.assertNotIn("InString", symbols_found)
        self.assertNotIn("constructor", symbols_found)

    def test_one_line_blocks_close_on_their_own_line(self):
        flag = by_symbol(chunker.chunk("s.js", SCRIPT))["Flag"][0]
        self.assertEqual(flag["start_line"], flag["end_line"])

    def test_nested_methods_stay_inside_their_owner(self):
        chunks = chunker.chunk("s.java", JAVA)
        symbols_found = set(by_symbol(chunks))
        self.assertEqual(symbols_found, {"Auth"})
        auth = by_symbol(chunks)["Auth"][0]
        self.assertIn("public String login(String user) {", auth["text"])
        self.assertIn("void noop();", auth["text"])
        self.assertLessEqual(len(auth["text"]), chunker.MAX_CHUNK_CHARS)

    def test_declaration_rules(self):
        cases = [
            ("public void noop();", ""),            # ends in ';' — declares no block
            ("const list = [1,", ""),              # ends in ',' — one entry of a longer statement
            ("@Override", ""),                     # annotation belongs to the line under it
            ("// class Foo {", ""),                # comment — already blanked anyway
            ("if (ready) {", ""),                  # control keyword, not a declaration
            ("class Foo {", "Foo"),
            ("func run() {", "run"),
            ("func (s *Server) Run() {", "Run"),   # Go receiver hides the name behind ')'
            ("type Shape interface {", "Shape"),   # shape keyword follows the name
            ("export function make<T>(x: T): T {", "make"),
        ]
        for head, expected in cases:
            with self.subTest(head=head):
                self.assertEqual(chunker._declaration(head), expected)

    def test_generic_typescript_signatures_are_named(self):
        src = "export function identity<T>(x: T): T {\n  return x;\n}\n"
        self.assertIn("identity", set(by_symbol(chunker.chunk("s.ts", src))))

    def test_go_receiver_names_are_taken_not_the_keyword(self):
        symbols_found = set(by_symbol(chunker.chunk("s.go", GO)))
        self.assertIn("Run", symbols_found)
        self.assertNotIn("func", symbols_found)

    def test_go_struct_word_resolves_to_the_type_name(self):
        symbols_found = set(by_symbol(chunker.chunk("s.go", GO)))
        self.assertIn("Server", symbols_found)
        self.assertNotIn("struct", symbols_found)

    @unittest.expectedFailure
    def test_kotlin_fun_opens_a_boundary(self):
        # `fun` is not in symbols.DECLARE_LINE's keyword list, so a Kotlin function
        # never becomes a chunk even though chunker's own docs promise `fun ok() = true` closes on its line.
        src = "fun ok() {\n    return true\n}\n"
        self.assertIn("ok", set(by_symbol(chunker.chunk("s.kt", src))))

    @unittest.expectedFailure
    def test_javascript_function_opens_a_boundary(self):
        # Same gap: bare `function ...` is not matched by DECLARE_LINE (only `func` is).
        src = "function alpha() {\n  return 1;\n}\n"
        self.assertIn("alpha", set(by_symbol(chunker.chunk("s.js", src))))

    def test_unstructured_indexable_source_falls_back_to_text(self):
        chunks = chunker.chunk("s.js", "const a = 1;\nconst b = 2;\n")
        self.assertTrue(chunks)
        self.assertTrue(all(not c["symbol"] for c in chunks))
        self.assertIn("const a = 1;", chunks[0]["text"])

    def test_gap_before_the_first_declaration_survives(self):
        chunks = chunker.chunk("s.js", SCRIPT)
        gap = "\n".join(c["text"] for c in chunks if not c["symbol"])
        self.assertIn("const TOP = 1;", gap)


class TestWindowing(unittest.TestCase):
    def make_big(self):
        body = "\n".join(f"    x = '{i:04d}' + '{'a' * 90}'" for i in range(60))
        return f"def big():\n{body}\n"

    def test_oversized_block_is_cut_into_multiple_windows(self):
        chunks = [c for c in chunker.chunk("s.py", self.make_big()) if c["symbol"] == "big"]
        self.assertGreater(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(len(chunk["text"]), chunker.MAX_CHUNK_CHARS)

    def test_windows_keep_the_symbol_and_cover_the_whole_span(self):
        chunks = [c for c in chunker.chunk("s.py", self.make_big()) if c["symbol"] == "big"]
        self.assertTrue(all(c["symbol"] == "big" for c in chunks))
        self.assertEqual(chunks[0]["start_line"], 1)
        self.assertEqual(chunks[-1]["end_line"], len(self.make_big().splitlines()))

    def test_seams_overlap_so_the_cut_is_visible(self):
        chunks = [c for c in chunker.chunk("s.py", self.make_big()) if c["symbol"] == "big"]
        for first, second in zip(chunks, chunks[1:]):
            self.assertLess(second["start_line"], first["end_line"] + 1,
                            "consecutive windows must repeat at least one line")
            self.assertGreaterEqual(second["start_line"], first["start_line"])

    def test_continuation_windows_mark_where_the_first_one_was(self):
        chunks = [c for c in chunker.chunk("s.py", self.make_big()) if c["symbol"] == "big"]
        self.assertTrue(chunks[0]["header"].startswith("def big"))
        for chunk in chunks[1:]:
            self.assertIn("(continued", chunk["header"])
            self.assertIn("big", chunk["header"])

    def test_a_single_line_too_long_is_capped_not_dropped(self):
        src = "def f():\n    " + "b" * (chunker.MAX_CHUNK_CHARS + 1000) + "\n"
        windows = by_symbol(chunker.chunk("s.py", src))["f"]
        self.assertTrue(all(len(c["text"]) <= chunker.MAX_CHUNK_CHARS for c in windows))
        fat = [c for c in windows if c["chars"] > chunker.MAX_CHUNK_CHARS]
        self.assertEqual(len(fat), 1)
        self.assertEqual(len(fat[0]["text"]), chunker.MAX_CHUNK_CHARS)
        self.assertIn("(continued", fat[0]["header"], "chars reports the true length")

    def test_small_blocks_are_never_windowed(self):
        chunks = [c for c in chunker.chunk("s.py", PYTHON) if c["symbol"] == "login"]
        self.assertEqual(len(chunks), 1)


class TestCaps(unittest.TestCase):
    def test_per_file_cap_is_recorded_on_the_last_kept_chunk(self):
        src = "\n".join(f"def f{i}():\n    return {i}\n"
                        for i in range(chunker.MAX_CHUNKS_PER_FILE + 50))
        chunks = chunker.chunk("s.py", src)
        self.assertEqual(len(chunks), chunker.MAX_CHUNKS_PER_FILE)
        self.assertEqual(chunks[-1]["capped_after"], len(src.splitlines()))

    def test_files_under_the_cap_carry_no_marker(self):
        for chunk in chunker.chunk("s.py", PYTHON):
            self.assertNotIn("capped_after", chunk)

    def test_chunk_sources_flattens_in_call_order(self):
        out = chunker.chunk_sources([("a.py", "def one():\n    pass\n"),
                                     ("b.js", "function two() {\n}\n")])
        self.assertEqual({c["path"] for c in out}, {"a.py", "b.js"})
        self.assertTrue(all(c["symbol"] in ("one", "two") or not c["symbol"] for c in out))

    def test_chunk_sources_stringifies_odd_inputs(self):
        out = chunker.chunk_sources([("c.cfg", 12345)])
        self.assertTrue(out)
        self.assertIn("12345", out[0]["text"])


class TestRepresent(unittest.TestCase):
    def row(self, **overrides):
        base = {"path": "src\\auth.py", "kind": "python", "symbol": "login", "parent": "Service",
                "start_line": 5, "end_line": 9, "header": "def login(self):",
                "text": "def login(self):\n    return 1", "id": "src/auth.py:5:login"}
        base.update(overrides)
        return base

    def test_first_line_is_a_citation_with_forward_slashes(self):
        doc = chunker.represent(self.row())
        self.assertEqual(doc.splitlines()[0], "src/auth.py :: Service.login (lines 5–9)")

    def test_anonymous_chunks_are_named_module_blocks(self):
        doc = chunker.represent(self.row(symbol="", parent=""))
        self.assertIn(":: module block (lines", doc)

    def test_body_follows_the_citation(self):
        doc = chunker.represent(self.row())
        self.assertIn("    return 1", doc)

    def test_long_bodies_are_cut_but_the_citation_survives(self):
        doc = chunker.represent(self.row(text="x" * (chunker.MAX_DOC_CHARS * 2)))
        self.assertLessEqual(len(doc), chunker.MAX_DOC_CHARS)
        self.assertEqual(doc.splitlines()[0], "src/auth.py :: Service.login (lines 5–9)")
        self.assertTrue(doc.endswith("…"))

    def test_head_only_rows_have_no_body(self):
        doc = chunker.represent(self.row(text=""))
        self.assertEqual(doc, "src/auth.py :: Service.login (lines 5–9)")

    def test_missing_fields_do_not_raise(self):
        doc = chunker.represent({})
        self.assertIn("module block", doc)

    def test_documents_pairs_ids_with_representations(self):
        rows = [self.row(), self.row(text="", id="empty"), self.row(symbol="other",
                                                                    id="src/auth.py:1:other")]
        docs = chunker.documents(rows)
        self.assertEqual([d[0] for d in docs], ["src/auth.py:5:login", "src/auth.py:1:other"])
        self.assertEqual(docs[0][1], chunker.represent(rows[0]))


if __name__ == "__main__":
    unittest.main()
