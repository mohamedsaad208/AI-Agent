"""Hybrid retrieval: several search layers, one ranked answer.

The layers that exist today each answer a different question. `symbols.rank` and
`repo_scanner.index_boost` score shared *tokens* against the symbol index; `semantic.rank`
scores meaning against embeddings; `chunker` proved the file is the wrong grain and ships
chunks nobody ranks yet. The engine's current merge is "lexical first, semantic fills the
leftovers" — a file both layers love arrives once, from the lexical list, and the semantic
vote is thrown away. This module fuses the lists instead: a document's score is the sum of
1/(k + rank) it earns in each list, so agreement between layers is a stronger signal than
either layer's top rank alone, and a layer with nothing to say costs nothing.

Fusion is rank-based (Cormack et al.'s reciprocal rank fusion) precisely because the layers
do not speak a common scale: lexical scores are integers from ad-hoc boost rules, cosine
similarity lives in [-1, 1], BM25 is unbounded. Trying to linearly combine those numbers is
a tuning exercise with no ground truth to tune against; ranks are comparable everywhere.

Everything here is optional-by-construction: each signal is gathered under its own guard, so
a missing embedder, an absent architecture index, or a caller with no chunks yields fewer
lists to fuse and never an exception. The output shape matches `symbols.rank` entries
(`path`, `symbol`, `why`, `layer`, `score`) so a caller can slot it into the same
five-file budget the engine has always used, plus a `signals` list naming what contributed.

No workspace reads, no model calls of our own: the caller hands in the index rows it already
has and, for chunk-level ranking, the chunks it already cut. Same offline, same graceful-
absence rules as every other retrieval layer.
"""
from __future__ import annotations

import math
import re

from . import chunker, semantic, symbols

RRF_K = 60                # standard damping: top rank ≈ 1/61, the fortieth ≈ 1/100
WEIGHT_LEXICAL = 1.0
WEIGHT_SEMANTIC = 0.9     # strong, but meaning alone should not bury a name-on-name hit
WEIGHT_CHUNKS = 0.8       # evidence at chunk grain about a file one level coarser

BM25_K1 = 1.2             # term-frequency saturation, the Okavango defaults that fit code text
BM25_B = 0.75             # length normalisation
MAX_QUERY_TOKENS = 64
MAX_DOC_TOKENS = 2048
MAX_ENTRIES = 40          # a hard ceiling on any list leaving this module

_TOKEN_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|\d+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def tokenize(text: str) -> list[str]:
    """Lowercased identifier parts, camelCase and snake_case both split.

    Code text does not obey the prose assumption that a space ends a word: `JwtTokenProvider`,
    `token_ttl`, and `TOKEN_TTL_MS` are three spellings of overlapping ideas, and a scorer that
    sees them as three tokens matches none of them reliably. Splitting once here keeps the
    query side and the document side in the same dialect.
    """
    words: list[str] = []
    for raw in _TOKEN_RE.findall(str(text or "")):
        for part in _CAMEL_RE.split(raw):
            for seg in part.split("_"):
                word = seg.casefold()
                if len(word) > 1 or word.isdigit():
                    words.append(word)
    return words[:MAX_DOC_TOKENS]


def _bm25(query_tokens: list[str], docs: list[list[str]]) -> list[float]:
    """Okapi BM25 over tokenised documents, no external search library and no index to persist.

    A full inverted index would be the fast shape for a repository, but the lists reaching here
    are already capped (chunk counts, per-file chunk caps) and correctness that a reader can
    check line by line is worth more at this size than an optimisation the budget hides anyway.
    """
    if not query_tokens or not docs:
        return [0.0] * len(docs)
    counts = [dict() for _ in docs]
    lengths = []
    df: dict[str, int] = {}
    for i, doc in enumerate(docs):
        lengths.append(len(doc) or 1)
        for token in doc:
            counts[i][token] = counts[i].get(token, 0) + 1
        for token in counts[i]:
            df[token] = df.get(token, 0) + 1
    avg_len = sum(lengths) / len(lengths)
    total = len(docs)
    scores: list[float] = []
    for i, doc in enumerate(docs):
        score = 0.0
        for token in set(query_tokens):
            freq = counts[i].get(token)
            if not freq:
                continue
            idf = math.log(1.0 + (total - df[token] + 0.5) / (df[token] + 0.5))
            norm = BM25_K1 * (1.0 - BM25_B + BM25_B * lengths[i] / avg_len)
            score += idf * freq * (BM25_K1 + 1.0) / (freq + norm)
        scores.append(score)
    return scores


def fuse(ranked_lists: list[tuple[str, list[dict]]],
         weights: dict[str, float] | None = None,
         k: int = RRF_K) -> list[dict]:
    """Reciprocal rank fusion over `(name, entries)` pairs; entries need `path`, may repeat.

    The winning entry for a path is the one from the heaviest-weighted signal that listed it —
    that entry's `why`, `symbol`, and `layer` survive into the output, because the reason text
    belongs to the layer that earned it. `signals` names every layer that listed the path,
    heaviest first, so the thread line can say "lexical + semantic" honestly.
    """
    weights = weights or {}
    acc: dict[str, dict] = {}
    for name, entries in ranked_lists:
        weight = weights.get(name, 1.0)
        seen: set[str] = set()
        for rank, entry in enumerate(entries, 1):
            path = str(entry.get("path", "")).replace("\\", "/")
            if not path or path in seen:
                continue
            seen.add(path)
            row = acc.setdefault(path, {"path": path, "score": 0.0, "by": {}})
            row["score"] += weight / (k + rank)
            row["by"].setdefault(name, entry)
    out: list[dict] = []
    for row in acc.values():
        signals = sorted(row["by"], key=lambda n: -weights.get(n, 1.0))
        primary = row["by"][signals[0]]
        merged = {"path": row["path"], "score": round(row["score"], 6),
                  "symbol": primary.get("symbol", ""), "why": primary.get("why", ""),
                  "layer": primary.get("layer", ""), "signals": signals}
        for name in signals[1:]:
            entry = row["by"][name]
            merged["symbol"] = merged["symbol"] or entry.get("symbol", "")
            merged["layer"] = merged["layer"] or entry.get("layer", "")
        if len(signals) > 1:
            merged["why"] = merged["why"] + " (+" + ", ".join(signals[1:]) + ")"
        out.append(merged)
    out.sort(key=lambda r: (-r["score"], r["path"]))
    return out[:MAX_ENTRIES]


def lexical_files(rows: list[dict], query: str, index=None, limit: int = 10) -> list[dict]:
    """The token-overlap signal: layer-aware when an architecture index is at hand, plain otherwise."""
    try:
        if index is not None:
            from .repo_scanner import index_boost
            return list(index_boost(rows or [], query, index, limit=limit))[:limit]
        return list(symbols.rank(rows or [], query, limit=limit))[:limit]
    except Exception:   # noqa: BLE001 — a failed signal is a missing signal, not a failed task
        return []


def semantic_files(settings, root, query: str, rows: list[dict], limit: int = 10) -> list[dict]:
    """The embedding signal, or [] whenever the optional stack is absent or refuses."""
    try:
        if not semantic.available(settings, root):
            return []
        return list(semantic.rank(settings, root, query, rows or [], limit=limit))[:limit]
    except Exception:   # noqa: BLE001
        return []


def rank_chunks(query: str, chunks: list[dict], limit: int = 12) -> list[dict]:
    """Chunk-grain hits: BM25 over `chunker.represent`, scored so the caller can cite a span.

    The file-level index knows a file *mentions* the idea; a chunk hit knows *where* — path,
    symbol, and line range travel with the score, which is what turns "read this file" into
    "quote these forty lines". The citation line from `represent` is part of the scored text,
    so a path or symbol name in the query pulls its own chunk up without a second signal.
    """
    wanted = tokenize(query)[:MAX_QUERY_TOKENS]
    usable = [c for c in (chunks or [])[: symbols.MAX_FILES * 20] if c.get("text")]
    if not wanted or not usable:
        return []
    scores = _bm25(wanted, [tokenize(chunker.represent(c))[:MAX_DOC_TOKENS] for c in usable])
    hits = []
    for row, score in zip(usable, scores):
        if score <= 0.0:
            continue
        hits.append({"path": str(row.get("path", "")).replace("\\", "/"),
                     "symbol": row.get("symbol", ""), "kind": row.get("kind", ""),
                     "start_line": row.get("start_line", 0), "end_line": row.get("end_line", 0),
                     "id": row.get("id", ""), "header": row.get("header", ""),
                     "why": "chunk", "score": round(score, 3)})
    hits.sort(key=lambda h: (-h["score"], h["path"], h["start_line"]))
    return hits[:limit]


def chunks_to_files(chunk_hits: list[dict]) -> list[dict]:
    """Collapse chunk hits to file-grain entries, keeping the strongest chunk per file.

    A file earns its place through its best chunk — three mediocre chunk hits do not outweigh
    one exact match elsewhere — and the winning symbol rides along so the excerpt path in the
    engine snippets the right block rather than the top of the file.
    """
    best: dict[str, dict] = {}
    for hit in chunk_hits or []:
        path = hit.get("path", "")
        if not path:
            continue
        row = best.get(path)
        if row is None or hit.get("score", 0) > row["score"]:
            best[path] = {"path": path, "symbol": hit.get("symbol", ""),
                          "why": f"chunk:{hit.get('id', path)}", "layer": "",
                          "score": hit.get("score", 0.0)}
    return sorted(best.values(), key=lambda r: (-r["score"], r["path"]))[:MAX_ENTRIES]


def retrieve(settings, root, query: str, rows: list[dict], *,
             limit: int = 6, index=None, chunks: list[dict] | None = None) -> list[dict]:
    """The one ranked file list the loop reads: every available signal, fused, capped, never fatal.

    `rows` are the symbol-index rows the caller already holds; `chunks` (optional, from
    `chunker.chunk_sources`) light up the third signal. `index` is the parsed architecture
    index when the scanner produced one. The output matches `symbols.rank` entries plus a
    `signals` list, so slotting this into the existing budget is a swap, not a rewrite.
    """
    if not (query or "").strip():
        return []
    limit = max(1, min(int(limit), MAX_ENTRIES))
    lists: list[tuple[str, list[dict]]] = []
    lexical = lexical_files(rows, query, index=index, limit=limit * 3)
    if lexical:
        lists.append(("lexical", lexical))
    sem = semantic_files(settings, root, query, rows, limit=limit * 2)
    if sem:
        lists.append(("semantic", sem))
    if chunks:
        by_chunk = chunks_to_files(rank_chunks(query, chunks, limit=limit * 4))
        if by_chunk:
            lists.append(("chunk", by_chunk))
    if not lists:
        return []
    weights = {"lexical": WEIGHT_LEXICAL, "semantic": WEIGHT_SEMANTIC,
               "chunk": WEIGHT_CHUNKS}
    fused = fuse(lists, weights)
    seen: set[str] = set()
    out: list[dict] = []
    for row in fused:
        if row["path"] in seen:
            continue
        seen.add(row["path"])
        out.append(row)
        if len(out) >= limit:
            break
    return out
