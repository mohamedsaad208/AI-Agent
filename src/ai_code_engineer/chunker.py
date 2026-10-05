"""Code-aware chunking and representation, so retrieval quotes a whole idea and not a line window.

The retrieval layers that exist today speak in files: `semantic` embeds one document per file
built from its path and declared names, and `symbols.snippet` answers a line window around one
symbol. Neither is what a reader wants to hand a model: a file is too big to paste whole and a
window cuts the body off mid-thought. This module splits a source file along the boundaries a
programmer would name — the function, the class, the method — and each chunk carries enough
identity (path, symbol, line range, header) that a model can cite it and jump back to it.

Structure comes from `symbols`: Python is walked with `ast` and gets exact spans, the other
indexed languages get a brace-depth scan over `blank()`ed text, which is the same trick that
stops a commented-out `class Foo` from becoming a chunk boundary. Anything this module cannot
find structure in — a config, a markup file, a source the parser refused — falls back to
blank-line blocks, which are worse than real boundaries but far better than a hard character cut.

Every list here is capped and every chunk bounded, because the output is destined for a prompt
whose budget is the whole point. Nothing in this module reads the workspace or talks to an
embedder; it turns `(path, source)` pairs in and chunks out, and the caller decides what to do
with them.
"""
from __future__ import annotations

import ast
import re

from . import symbols

MAX_CHUNK_CHARS = 2000      # one chunk's body, before it is windowed
CHUNK_OVERLAP_LINES = 4     # lines repeated at a window seam, so the cut is visible
MAX_DOC_CHARS = 900         # `represent()` output, aligned with semantic.py's spirit
MAX_CHUNKS_PER_FILE = 600   # a generated file is not worth ten thousand rows
PY_DECL = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
NAME_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*(?:<[^<>]*>)?\s*[({=:]")
# Go writes `func (s *Server) Run()`, where the name sits behind the receiver parenthesis —
# the only place in the scanned languages where the keyword itself touches a `(`.
GO_RECEIVER_RE = re.compile(r"\)\s*([A-Za-z_$][\w$]*)\s*\(")
# Go and Rust put the shape keyword next to the brace (`type Server struct {`), where NAME_RE
# lands on `struct` instead of the name. The identifier before a shape word is the owner.
STRUCT_WORDS = {"struct", "interface", "enum", "union", "trait"}
WORD_RE = re.compile(r"[A-Za-z_$][\w$]*")


def _chunk(path: str, kind: str, symbol: str, parent: str, start: int, end: int,
           lines: list[str]) -> dict:
    """One chunk with its text, capped so a pathological declaration cannot ship the file."""
    body = lines[start - 1:end]
    text = "\n".join(body)
    header = next((bare.strip() for bare in body if bare.strip()), "")[:symbols.MAX_SIGNATURE * 2]
    return {"path": path, "kind": kind, "symbol": symbol, "parent": parent,
            "start_line": start, "end_line": end, "header": header,
            "text": text[:MAX_CHUNK_CHARS], "chars": len(text),
            "id": f"{path}:{start}:{symbol or 'block'}"}


def _windows(path: str, kind: str, symbol: str, parent: str, start: int, end: int,
             lines: list[str]) -> list[dict]:
    """A block too big for one chunk, cut into overlapping windows at line boundaries.

    Continuation windows keep the owning symbol and lose the header to a marker: a reader who
    finds the second window has to be able to tell that the first exists, and where to look.
    """
    if sum(len(lines[n]) + 1 for n in range(start - 1, end)) <= MAX_CHUNK_CHARS:
        return [_chunk(path, kind, symbol, parent, start, end, lines)]
    out: list[dict] = []
    window_start = start
    used = 0
    for number in range(start, end + 1):
        used += len(lines[number - 1]) + 1
        if used <= MAX_CHUNK_CHARS:
            continue
        piece = _chunk(path, kind, symbol, parent, window_start, number - 1, lines)
        if out:
            piece["header"] = f"{symbol or 'block'} (continued, lines {window_start}–{number - 1})"
        out.append(piece)
        # The next window re-reads the seam lines rather than starting flush after the cut.
        window_start = max(number - CHUNK_OVERLAP_LINES, window_start + 1)
        used = sum(len(lines[n]) + 1 for n in range(window_start - 1, number))
    if window_start <= end:
        tail = _chunk(path, kind, symbol, parent, window_start, end, lines)
        if out:
            tail["header"] = f"{symbol or 'block'} (continued, lines {window_start}–{end})"
        out.append(tail)
    return out


def _gaps(path: str, kind: str, lines: list[str], covered: set[int],
          out: list[dict]) -> None:
    """Module-level statements between the declarations: imports, constants, glue.

    A retrieval index that only holds functions can answer "where is login defined" and nothing
    about "what does this file need to exist at all", which is the question a broken import
    asks. Empty runs are skipped, because a gap of blank lines between two methods is not a
    chunk of anything.
    """
    runs: list[tuple[int, int]] = []
    start = 0
    for number in range(1, len(lines) + 2):
        closed = number in covered or number > len(lines)
        if closed and start:
            runs.append((start, number - 1))
            start = 0
        elif not closed and not start:
            start = number
    for gap_start, gap_end in runs:
        if not "\n".join(lines[gap_start - 1:gap_end]).strip():
            continue
        out.extend(_windows(path, kind, "", "", gap_start, gap_end, lines))


def _python(path: str, source: str, lines: list[str]) -> list[dict]:
    """Exact spans from `ast`: every function and method its own chunk, every class the rest.

    A method is cut out of its class so the class chunk is the header plus the fields and the
    statements between methods — the code that only exists at class scope. Nested functions and
    conditionals stay inside the chunk of the first-level declaration that owns them, because a
    boundary a reader would not name is not a boundary.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return _text(path, source, lines, kind="python")

    regions: list[dict] = []   # {symbol, parent, start, end, is_class}

    def head_line(node) -> int:
        starts = [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
        return min(starts)

    def walk(body, outer: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                regions.append({"symbol": node.name, "parent": outer,
                                "start": head_line(node),
                                "end": node.end_lineno or node.lineno, "is_class": False})
            elif isinstance(node, ast.ClassDef):
                start = head_line(node)
                regions.append({"symbol": node.name, "parent": outer, "start": start,
                                "end": node.end_lineno or start, "is_class": True})
                walk(node.body, node.name)
            elif hasattr(node, "body") and isinstance(node.body, list):
                for item in node.body:
                    if isinstance(item, PY_DECL):
                        # A class or function inside an `if`/`try` is still a declaration;
                        # its own span is taken, the guard around it belongs to the module.
                        if isinstance(item, ast.ClassDef):
                            regions.append({"symbol": item.name, "parent": outer,
                                            "start": head_line(item),
                                            "end": item.end_lineno or item.lineno,
                                            "is_class": True})
                            walk(item.body, item.name)
                        else:
                            regions.append({"symbol": item.name, "parent": outer,
                                            "start": head_line(item),
                                            "end": item.end_lineno or item.lineno,
                                            "is_class": False})

    walk(tree.body, "")

    class_regions = [r for r in regions if r["is_class"]]
    member_regions = [r for r in regions if not r["is_class"]]

    def owned_by(cls: dict, region: dict) -> bool:
        return cls["start"] <= region["start"] and region["end"] <= cls["end"]

    out: list[dict] = []
    for region in member_regions:
        out.extend(_windows(path, "python", region["symbol"], region["parent"],
                            region["start"], region["end"], lines))
    for cls in class_regions:
        holes = sorted((r["start"], r["end"]) for r in member_regions if owned_by(cls, r)
                       and r is not cls)
        number = cls["start"]
        while number <= cls["end"]:
            skip = next((h for h in holes if h[0] <= number <= h[1]), None)
            if skip:
                number = skip[1] + 1
                continue
            stop = min((h[0] - 1 for h in holes if h[0] > number), default=cls["end"])
            block = _chunk(path, "python", cls["symbol"], cls["parent"], number,
                           min(stop, cls["end"]), lines)
            if block["text"].strip():
                out.append(block)
            number = min(stop, cls["end"]) + 1
    covered: set[int] = set()
    for region in regions:
        covered.update(range(region["start"], region["end"] + 1))
    _gaps(path, "python", lines, covered, out)
    return out


def _declaration(head: str) -> str:
    """The name a declaration line owns, or "" when the line is not one.

    `symbols.DECLARE_LINE` already knows which leading keywords only open a declaration; this
    adds the boundary rules: a line ending in `;` or `,` declares without opening a block
    (`void noop();`, an entry in a `var` list), an annotation or comment head belongs to the
    declaration under it or nothing, and a one-line block (`class Flag { }`) is accepted
    because it opens and closes on the spot.
    """
    if not symbols.DECLARE_LINE.match(head):
        return ""
    if head.endswith((";", ",")) or head.startswith(("@", "//", "*")):
        return ""
    match = NAME_RE.search(head)
    if not match:
        return ""
    name = match.group(1)
    if name in ("func", "fun", "fn"):
        receiver = GO_RECEIVER_RE.search(head)
        name = receiver.group(1) if receiver else ""
    if name in STRUCT_WORDS or name in symbols.CONTROL:
        before = WORD_RE.findall(head[:match.start()])
        name = before[-1] if before and before[-1] not in STRUCT_WORDS else ""
    return "" if name in symbols.CONTROL else name


def _braced(path: str, source: str, lines: list[str], kind: str) -> list[dict]:
    """Top-level type and function blocks found by a brace scan of blanked text.

    A declaration at depth 0 opens a chunk; the chunk ends where depth returns to 0. Nested
    declarations stay inside their owner's chunk — unlike Python there is no exact span here,
    and half a class body emitted as two chunks is worse than one honest oversized one, which
    `_windows` then cuts at line boundaries anyway.
    """
    blanked = symbols.blank(source, backticks=kind in ("script", "lexical"))
    out: list[dict] = []
    covered: set[int] = set()
    depth = 0
    owner: tuple[str, int] | None = None    # (symbol, line where the block opened)
    for number, line in enumerate(blanked.splitlines(), 1):
        head = line.strip()
        opened, closed = line.count("{"), line.count("}")
        if owner is None:
            name = _declaration(head) if depth == 0 else ""
            if not name:
                depth = max(depth + opened - closed, 0)
                continue
            owner = (name, number)
        depth += opened - closed
        # The block closes when depth falls back to file level. A single-line declaration
        # (`fun ok() = true`, `void noop();`) closes on its own line; a braceless multi-line
        # one is caught by the next declaration or by the end of the file.
        if depth <= 0:
            out.extend(_windows(path, kind, owner[0], "", owner[1], number, lines))
            covered.update(range(owner[1], number + 1))
            owner = None
            depth = 0
    if owner is not None:
        out.extend(_windows(path, kind, owner[0], "", owner[1], len(lines), lines))
        covered.update(range(owner[1], len(lines) + 1))
    if not out:
        return _text(path, source, lines, kind=kind)
    _gaps(path, kind, lines, covered, out)
    return out


def _text(path: str, source: str, lines: list[str], kind: str = "text") -> list[dict]:
    """Structural-free fallback: blank-line-separated blocks, packed to the size cap.

    Chosen for YAML, SQL, markdown and every file whose parser said no. A chunk never starts
    or ends inside a paragraph or a mapping block, which is the most that can be promised
    without reading the format; `header` is the first line, which for a config is usually the
    key that owns everything under it.
    """
    blocks: list[tuple[int, int]] = []
    start = 0
    for number, line in enumerate(lines, 1):
        if line.strip() and not start:
            start = number
        elif not line.strip() and start:
            blocks.append((start, number - 1))
            start = 0
    if start:
        blocks.append((start, len(lines)))
    out: list[dict] = []
    run: list[tuple[int, int]] = []
    used = 0

    def flush() -> None:
        if run:
            out.extend(_windows(path, kind, "", "", run[0][0], run[-1][1], lines))
        run.clear()

    for block_start, block_end in blocks:
        size = sum(len(lines[n]) + 1 for n in range(block_start - 1, block_end))
        if run and used + size > MAX_CHUNK_CHARS:
            flush()
            used = 0
        if size > MAX_CHUNK_CHARS and not run:
            out.extend(_windows(path, kind, "", "", block_start, block_end, lines))
            continue
        run.append((block_start, block_end))
        used += size
    flush()
    return out


def chunk(path: str, source: str) -> list[dict]:
    """One file's chunks, in line order. Never raises: worst case is whole-file text blocks.

    The kind comes from the same suffix table the symbol index uses, so a chunk and a row for
    the same file describe the same language and cannot drift apart.
    """
    source = str(source or "")
    if not source.strip():
        return []
    lines = source.splitlines()
    suffix = symbols.suffix_of(path)
    if suffix == ".py":
        out = _python(path, source, lines)
    elif suffix in symbols.INDEXABLE:
        row = symbols.parse(path, source)
        kind = str((row or {}).get("kind", "lexical"))
        out = _braced(path, source, lines, kind)
    else:
        out = _text(path, source, lines)
    out.sort(key=lambda c: (c["start_line"], c["end_line"]))
    if len(out) > MAX_CHUNKS_PER_FILE:
        kept = out[:MAX_CHUNKS_PER_FILE]
        # The truncation is recorded on the last kept chunk, the way the symbol index records
        # refused routes: a reader who asks "is everything in this file here" must be able to
        # tell "yes" from "the list stopped", and dropping the answer in silence is a lie
        # of omission at exactly the moment an index is consulted about what it missed.
        kept[-1]["capped_after"] = out[-1]["end_line"]
        return kept
    return out


def chunk_sources(sources) -> list[dict]:
    """Chunks for an iterable of `(path, text)` pairs, the same shape `find_references` takes.

    The per-file cap is enforced in `chunk`; this only keeps the total bounded, because an
    index built over a whole repository needs a stop sign that does not depend on the caller.
    """
    out: list[dict] = []
    for path, text in sources:
        for piece in chunk(str(path), str(text)):
            out.append(piece)
            if len(out) >= MAX_CHUNKS_PER_FILE * symbols.MAX_FILES:
                return out
    return out


def represent(chunk_row: dict) -> str:
    """The document to embed or paste for one chunk: identity first, then the code.

    The first line is a citation the model can repeat — path, symbol, line range — and the
    body is the source itself, not a summary of it: `semantic._texts` already proved that an
    embedding over declared names is thin, and the names inside a *body* (a string literal, a
    route, a column) are the ones a task tends to paraphrase. The cap falls on the code and
    the citation line survives whole: a document whose first line is intact is one the
    retriever can answer with.
    """
    path = str(chunk_row.get("path", "")).replace("\\", "/")
    symbol = str(chunk_row.get("symbol", "")).strip()
    parent = str(chunk_row.get("parent", "")).strip()
    name = f"{parent}.{symbol}" if parent and symbol else (symbol or "module block")
    start = chunk_row.get("start_line", 0)
    end = chunk_row.get("end_line", start)
    head = f"{path} :: {name} (lines {start}–{end})"
    body = str(chunk_row.get("text", ""))
    doc = f"{head}\n{body}" if body else head
    if len(doc) > MAX_DOC_CHARS:
        keep = MAX_DOC_CHARS - len(head) - 2
        doc = (head + "\n" + body[:keep].rstrip() + "…") if keep > 0 and body \
            else doc[:MAX_DOC_CHARS]
    return doc


def documents(chunks: list[dict]) -> list[tuple[str, str]]:
    """`(chunk id, representation)` pairs, ready for an embedder that takes (id, text)."""
    return [(str(row.get("id", "")), represent(row)) for row in chunks if row.get("text")]
