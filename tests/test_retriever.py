"""Retrieval must fuse the signals honestly: agreement wins, absent layers cost nothing, failures are silent."""
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import retriever, symbols

AUTH_SRC = "def login(user):\n    return check(user)\n"
BILLING_SRC = "def invoice(total):\n    return total\n"

ROWS = [row for row in (symbols.parse("src/auth.py", AUTH_SRC),
                        symbols.parse("src/billing.py", BILLING_SRC)) if row]


def chunk_row(path, symbol, start, end, text):
    head = text.splitlines()[0] if text else ""
    return {"path": path, "kind": "python", "symbol": symbol, "parent": "",
            "start_line": start, "end_line": end, "header": head,
            "text": text, "id": f"{path}:{start}:{symbol or 'block'}"}


CHUNKS = [
    chunk_row("src/auth.py", "login", 1, 2, AUTH_SRC),
    chunk_row("src/billing.py", "invoice", 1, 2, BILLING_SRC),
    chunk_row("src/empty.py", "", 1, 1, ""),
]


class TestTokenize(unittest.TestCase):
    def test_camel_and_snake_and_acronyms_are_split(self):
        self.assertEqual(retriever.tokenize("JwtTokenProvider"), ["jwt", "token", "provider"])
        self.assertEqual(retriever.tokenize("token_ttl"), ["token", "ttl"])
        self.assertEqual(retriever.tokenize("HTTPServer"), ["http", "server"])

    def test_three_spellings_of_one_idea_share_tokens(self):
        self.assertEqual(set(retriever.tokenize("TOKEN_TTL_MS")),
                         set(retriever.tokenize("tokenTtlMs")))

    def test_digits_survive_single_letters_do_not(self):
        words = retriever.tokenize("a1 x 42 ab")
        self.assertEqual(set(words), {"a1", "42", "ab"})

    def test_empty_and_odd_inputs(self):
        self.assertEqual(retriever.tokenize(""), [])
        self.assertEqual(retriever.tokenize(None), [])
        self.assertEqual(retriever.tokenize("/// --"), [])

    def test_output_is_capped(self):
        self.assertLessEqual(len(retriever.tokenize("ab " * (retriever.MAX_DOC_TOKENS + 100))),
                             retriever.MAX_DOC_TOKENS)


class TestBm25(unittest.TestCase):
    def test_empty_inputs_score_zero_without_error(self):
        docs = [["login"], ["invoice"]]
        self.assertEqual(retriever._bm25([], docs), [0.0, 0.0])
        self.assertEqual(retriever._bm25(["login"], []), [])

    def test_no_overlap_scores_zero(self):
        self.assertEqual(retriever._bm25(["zzz"], [["login"], ["invoice"]]), [0.0, 0.0])

    def test_rare_terms_outrank_common_ones(self):
        docs = [["widget", "zzz"], ["widget", "qqq"], ["widget", "qqq"]]
        scores = retriever._bm25(["widget", "zzz"], docs)
        # The document holding the rare term beats the others on that term alone.
        self.assertGreater(scores[0], max(scores[1:]))

    def test_term_frequency_saturates(self):
        once = retriever._bm25(["alpha"], [["alpha"]])[0]
        twice = retriever._bm25(["alpha"], [["alpha", "alpha"]])[0]
        self.assertGreater(twice, once)
        self.assertLess(twice, 2 * once, "BM25 must saturate, not scale linearly")

    def test_longer_documents_are_penalised(self):
        scores = retriever._bm25(["alpha"], [["alpha"], ["alpha"] + ["beta"] * 50])
        self.assertGreater(scores[0], scores[1])


class TestFuse(unittest.TestCase):
    def entry(self, path, **kw):
        return {"path": path, "symbol": f"s_{path}", "why": f"why {path}",
                 "layer": "ui", **kw}

    def test_score_is_weight_over_k_plus_rank(self):
        fused = retriever.fuse([("lexical", [self.entry("a.py"), self.entry("b.py")])])
        self.assertEqual([r["path"] for r in fused], ["a.py", "b.py"])
        self.assertAlmostEqual(fused[0]["score"], 1.0 / (retriever.RRF_K + 1), places=6)
        self.assertAlmostEqual(fused[1]["score"], 1.0 / (retriever.RRF_K + 2), places=6)

    def test_agreement_between_lists_beats_a_single_top_rank(self):
        lists = [("lexical", [self.entry("a.py"), self.entry("b.py")]),
                 ("chunk", [self.entry("b.py")])]
        weights = {"lexical": retriever.WEIGHT_LEXICAL, "chunk": retriever.WEIGHT_CHUNKS}
        fused = retriever.fuse(lists, weights)
        self.assertEqual(fused[0]["path"], "b.py", "two signals must outrank one top hit")
        self.assertEqual(fused[0]["signals"], ["lexical", "chunk"])
        self.assertIn("(+chunk)", fused[0]["why"])

    def test_primary_entry_comes_from_the_heaviest_signal(self):
        lists = [("semantic", [self.entry("a.py", why="semantic reason")]),
                 ("lexical", [self.entry("a.py", why="lexical reason")])]
        weights = {"lexical": 1.0, "semantic": 0.9}
        fused = retriever.fuse(lists, weights)
        self.assertEqual(len(fused), 1)
        self.assertTrue(fused[0]["why"].startswith("lexical reason"))
        self.assertEqual(fused[0]["symbol"], "s_a.py")
        self.assertEqual(fused[0]["signals"], ["lexical", "semantic"])

    def test_missing_fields_are_filled_from_other_signals(self):
        lists = [("lexical", [{"path": "a.py"}]),
                 ("chunk", [{"path": "a.py", "symbol": "hit", "layer": "ui"}])]
        weights = {"lexical": 1.0, "chunk": 0.8}
        fused = retriever.fuse(lists, weights)
        self.assertEqual(fused[0]["symbol"], "hit")
        self.assertEqual(fused[0]["layer"], "ui")

    def test_duplicate_paths_in_one_list_count_once(self):
        fused = retriever.fuse([("lexical", [self.entry("a.py"), self.entry("a.py")])])
        self.assertAlmostEqual(fused[0]["score"], 1.0 / (retriever.RRF_K + 1), places=6)

    def test_blank_paths_are_dropped_and_backslashes_normalised(self):
        fused = retriever.fuse([("lexical", [self.entry(""), self.entry("src\\a.py")])])
        self.assertEqual([r["path"] for r in fused], ["src/a.py"])

    def test_output_is_capped(self):
        entries = [self.entry(f"f{i}.py") for i in range(retriever.MAX_ENTRIES + 10)]
        with unittest.mock.patch.object(retriever, "MAX_ENTRIES", 3):
            fused = retriever.fuse([("lexical", entries)])
        self.assertEqual(len(fused), 3)

    def test_empty_lists_fuse_to_empty(self):
        self.assertEqual(retriever.fuse([]), [])
        self.assertEqual(retriever.fuse([("lexical", []), ("semantic", [])]), [])


class TestSignalGuards(unittest.TestCase):
    def test_lexical_ranks_real_rows(self):
        hits = retriever.lexical_files(ROWS, "login", limit=5)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["path"], "src/auth.py")
        self.assertEqual({"path", "why", "score"} <= set(hits[0]), True)

    def test_lexical_swallows_failures(self):
        self.assertEqual(retriever.lexical_files(ROWS, "login", index=object()), [])

    def test_lexical_tolerates_no_rows(self):
        self.assertEqual(retriever.lexical_files([], "login"), [])

    def test_semantic_is_skipped_when_unavailable(self):
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=False):
            self.assertEqual(retriever.semantic_files(None, ".", "q", ROWS), [])

    def test_semantic_failure_is_a_missing_signal(self):
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=True), \
             unittest.mock.patch("ai_code_engineer.semantic.rank", side_effect=RuntimeError):
            self.assertEqual(retriever.semantic_files(None, ".", "q", ROWS), [])

    def test_semantic_results_pass_through_capped(self):
        many = [{"path": f"f{i}.py", "score": i, "why": "sem"} for i in range(5)]
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=True), \
             unittest.mock.patch("ai_code_engineer.semantic.rank", return_value=many):
            hits = retriever.semantic_files(None, ".", "q", ROWS, limit=2)
        self.assertEqual(len(hits), 2)


class TestRankChunks(unittest.TestCase):
    def test_matching_chunk_is_found_with_its_span(self):
        hits = retriever.rank_chunks("login user check", CHUNKS)
        self.assertTrue(hits)
        top = hits[0]
        self.assertEqual(top["path"], "src/auth.py")
        self.assertEqual(top["symbol"], "login")
        self.assertEqual((top["start_line"], top["end_line"]), (1, 2))
        self.assertEqual(top["why"], "chunk")
        self.assertTrue(top["id"].endswith(":login"))

    def test_citation_line_makes_a_path_query_hit(self):
        hits = retriever.rank_chunks("billing", CHUNKS)
        self.assertEqual([h["path"] for h in hits], ["src/billing.py"])

    def test_query_with_no_overlap_returns_nothing(self):
        self.assertEqual(retriever.rank_chunks("zzz qqq", CHUNKS), [])

    def test_empty_query_or_chunks(self):
        self.assertEqual(retriever.rank_chunks("", CHUNKS), [])
        self.assertEqual(retriever.rank_chunks("login", []), [])
        self.assertEqual(retriever.rank_chunks("login", None), [])

    def test_textless_chunks_are_never_ranked(self):
        hits = retriever.rank_chunks("empty", CHUNKS)
        self.assertNotIn("src/empty.py", [h["path"] for h in hits])

    def test_hits_are_scored_descending_and_capped(self):
        chunks = [chunk_row(f"f{i}.py", f"login{i}", 1, 2, "def login(user):\n    return check(user)\n")
                  for i in range(20)]
        hits = retriever.rank_chunks("login user check", chunks, limit=5)
        self.assertLessEqual(len(hits), 5)
        scores = [h["score"] for h in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_query_tokens_are_capped(self):
        query = " ".join(f"login{i}" for i in range(retriever.MAX_QUERY_TOKENS * 3))
        retriever.rank_chunks(query, CHUNKS)  # must not raise


class TestChunksToFiles(unittest.TestCase):
    def hit(self, path, score, id_="x"):
        return {"path": path, "symbol": "s", "id": id_, "score": score, "why": "chunk"}

    def test_strongest_chunk_per_file_wins(self):
        out = retriever.chunks_to_files([self.hit("a.py", 1.0, "a1"),
                                         self.hit("a.py", 3.5, "a2"),
                                         self.hit("b.py", 2.0, "b1")])
        self.assertEqual([r["path"] for r in out], ["a.py", "b.py"])
        self.assertEqual(out[0]["why"], "chunk:a2")
        self.assertEqual(out[0]["score"], 3.5)

    def test_blank_paths_and_empty_input(self):
        self.assertEqual(retriever.chunks_to_files([self.hit("", 5.0)]), [])
        self.assertEqual(retriever.chunks_to_files([]), [])
        self.assertEqual(retriever.chunks_to_files(None), [])

    def test_output_is_capped(self):
        hits = [self.hit(f"f{i}.py", i) for i in range(retriever.MAX_ENTRIES + 10)]
        with unittest.mock.patch.object(retriever, "MAX_ENTRIES", 4):
            self.assertEqual(len(retriever.chunks_to_files(hits)), 4)


class TestRetrieve(unittest.TestCase):
    def test_blank_query_returns_nothing(self):
        self.assertEqual(retriever.retrieve(None, ".", "", ROWS), [])
        self.assertEqual(retriever.retrieve(None, ".", "   ", ROWS), [])

    def test_no_signals_at_all_returns_empty(self):
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=False):
            self.assertEqual(retriever.retrieve(None, ".", "zzzqqqxyz", []), [])

    def test_lexical_only_run_still_answers(self):
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=False):
            out = retriever.retrieve(None, ".", "login", ROWS)
        self.assertTrue(out)
        self.assertEqual(out[0]["path"], "src/auth.py")
        self.assertEqual(out[0]["signals"], ["lexical"])

    def test_chunk_signal_is_fused_with_lexical(self):
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=False):
            out = retriever.retrieve(None, ".", "invoice", ROWS, chunks=CHUNKS)
        billing = next(r for r in out if r["path"] == "src/billing.py")
        self.assertIn("chunk", billing["signals"])

    def test_agreement_between_signals_lifts_a_file(self):
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=False):
            alone = retriever.retrieve(None, ".", "login", ROWS)
            fused = retriever.retrieve(None, ".", "login", ROWS, chunks=CHUNKS)
        first = next(r for r in alone if r["path"] == "src/auth.py")
        lifted = next(r for r in fused if r["path"] == "src/auth.py")
        self.assertGreater(lifted["score"], first["score"])
        self.assertIn("chunk", lifted["signals"])

    def test_output_shape_matches_symbols_rank_plus_signals(self):
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=False):
            out = retriever.retrieve(None, ".", "login", ROWS, chunks=CHUNKS)
        for row in out:
            self.assertTrue({"path", "symbol", "why", "layer", "score", "signals"} <= set(row))

    def test_paths_appear_once(self):
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=False):
            out = retriever.retrieve(None, ".", "login", ROWS, chunks=CHUNKS)
        paths = [r["path"] for r in out]
        self.assertEqual(len(paths), len(set(paths)))

    def test_limit_is_clamped_into_range(self):
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=False):
            self.assertGreaterEqual(len(retriever.retrieve(None, ".", "login", ROWS, limit=0)), 1)
            out = retriever.retrieve(None, ".", "login", ROWS,
                                     limit=retriever.MAX_ENTRIES * 10)
        self.assertLessEqual(len(out), retriever.MAX_ENTRIES)

    def test_missing_chunks_argument_is_fine(self):
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=False):
            self.assertTrue(retriever.retrieve(None, ".", "login", ROWS, chunks=None))

    def test_semantic_stack_present_joins_the_fusion(self):
        sem = [{"path": "src/billing.py", "symbol": "invoice", "why": "near in meaning",
                "layer": "", "score": 0.7}]
        with unittest.mock.patch("ai_code_engineer.semantic.available", return_value=True), \
             unittest.mock.patch("ai_code_engineer.semantic.rank", return_value=sem):
            out = retriever.retrieve(None, ".", "login", ROWS)
        billing = next(r for r in out if r["path"] == "src/billing.py")
        self.assertIn("semantic", billing["signals"])


if __name__ == "__main__":
    unittest.main()
