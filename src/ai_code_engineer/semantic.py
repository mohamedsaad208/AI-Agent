"""Optional, fully local semantic ranking that rides beside the lexical search.

The gap this fills: `symbols.rank` and `index_boost` score shared *tokens* — they answer "which file
names what the task names" and not "which file means what the task means". A description like
"the place that issues login tokens" finds nothing lexically when the code calls it
`JwtTokenProvider`. Embeddings close exactly that gap, and nothing else here was replaced.

Nothing is downloaded at run time, ever. FastEmbed is not a dependency of this application; the
operator installs it themselves and places model files in a local folder, and the model name given
to it must be that folder. If the package is missing, the folder is missing, or embedding fails at
any point, every function here answers empty and the loop continues with the search it always had.

Storage mirrors the repository scanner: one JSON file beside the project index, keyed by file and
invalidated by mtime, so vectors are computed once per changed file and reused across turns and
runs — same offline, same local, same graceful-absence rules as `project-index.json`.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile

CACHE_NAME = ".agent-semantic.json"
MAX_DOC_CHARS = 700
MIN_SCORE = 0.15
CACHE_LIMIT = 4000

_cache_embedder: dict[str, object] = {}
_cache_vectors: dict[str, dict] = {}


def configured(settings, root: str | Path | None = None) -> str:
    """The local model folder, or "" when semantic search is off.

    The setting wins; `AGENT_EMBED_MODEL_DIR` is the no-profile escape hatch. If not explicitly
    configured, auto-detects a local `models` directory in the project or application root.
    """
    where = str(getattr(settings, "semantic_model_dir", "") or "").strip()
    where = where or str(os.environ.get("AGENT_EMBED_MODEL_DIR", "")).strip()
    if str(os.environ.get("AGENT_SEMANTIC", "1")).casefold() in {"0", "off", "false"}:
        return ""
    if not where:
        for candidate in ("models", "models/fastembed_bge_small"):
            if root and (Path(root) / candidate).is_dir():
                return str((Path(root) / candidate).resolve())
            if (Path.cwd() / candidate).is_dir():
                return str((Path.cwd() / candidate).resolve())
        return ""
    try:
        p = Path(where)
        if not p.is_absolute():
            if root and (Path(root) / p).is_dir():
                return str((Path(root) / p).resolve())
            if (Path.cwd() / p).is_dir():
                return str((Path.cwd() / p).resolve())
        return str(p.resolve()) if p.is_dir() else ""
    except OSError:
        return ""


def available(settings, root: str | Path | None = None) -> bool:
    """Whether the optional stack is present: a local model folder AND an installed fastembed."""
    where = configured(settings, root)
    if not where:
        return False
    try:
        import importlib.util
        return importlib.util.find_spec("fastembed") is not None
    except (ImportError, ValueError):
        return False


def _embedder(model_dir: str):
    """One embedder per model folder for the life of the process; loading ONNX is the slow part.

    The two `*_OFFLINE` variables are set as defaults (an operator who already chose a value
    wins), so if a stray code path inside the optional dependency ever tries to phone the
    HuggingFace hub it fails immediately instead of silently pulling a model onto the machine.
    The docstring's promise — no download at run time, ever — is enforced here rather than left
    to the operator's environment.
    """
    if model_dir not in _cache_embedder:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from fastembed import TextEmbedding
        try:
            _cache_embedder[model_dir] = TextEmbedding(model_name=model_dir)
        except Exception:
            candidate_dirs = [model_dir, str(Path(model_dir).parent)]
            for cdir in candidate_dirs:
                try:
                    _cache_embedder[model_dir] = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=cdir)
                    break
                except Exception:
                    continue
            else:
                _cache_embedder[model_dir] = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=model_dir)
    return _cache_embedder[model_dir]


def _texts(rows: list[dict]) -> dict[str, str]:
    """The document embedded for each indexed file: its path and what it declares.

    A path and declaration list is what the symbol index already proved about the file — the
    embedding is a second index over facts we collected, not a guess about file contents the
    reader would have to re-open files to make.
    """
    by_path: dict[str, list[str]] = {}
    for row in rows:
        path = str(row.get("path", "")).replace("\\", "/")
        if not path:
            continue
        names = by_path.setdefault(path, [])
        for decl in (row.get("declares") or row.get("declarations") or []):
            if isinstance(decl, dict) and decl.get("name"):
                token = str(decl["name"])
                if token not in names:
                    names.append(token)
        for imp in (row.get("imports") or []):
            if isinstance(imp, str) and imp:
                token = imp.rsplit("/", 1)[-1]
                if token and token not in names:
                    names.append(token)
    return {path: (path + " — " + ", ".join(names[:40]))[:MAX_DOC_CHARS]
            for path, names in by_path.items()}


def _cosine(left, right) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    na = math.sqrt(sum(a * a for a in left))
    nb = math.sqrt(sum(b * b for b in right))
    return dot / (na * nb) if na and nb else 0.0


def _cache_file(root: Path) -> Path:
    return root / CACHE_NAME


def _load_cache(root: Path) -> dict:
    try:
        data = json.loads(_cache_file(root).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema") == 1:
            return data
    except (OSError, ValueError):
        pass
    return {"schema": 1, "model": "", "files": {}}


def _save_cache(root: Path, cache: dict) -> None:
    """Atomic, and never fatal: an unwritable folder means no semantic cache, not a failed task."""
    path = _cache_file(root)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".semantic-", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(cache, out, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _file_stamp(root: Path, path: str) -> str:
    try:
        stat = (root / path).stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return ""


def forget(root: str | Path, relative: str) -> None:
    """Drop one file's vector after a write or delete made it untrue.

    Not strictly required for correctness — `rank` re-checks every file stamp and only scores files
    the current symbol index still lists — but it keeps the cache from accreting rows for content
    that no longer exists anywhere.
    """
    where = configured_from_cache(root)
    if not where:
        return
    cache = _load_cache(Path(root))
    if cache["files"].pop(str(relative).replace("\\", "/"), None) is not None:
        _save_cache(Path(root), cache)


def configured_from_cache(root: str | Path) -> str:
    """Cheap presence check used by the invalidation path: a cache file means the layer ran here."""
    try:
        return "" if not _cache_file(Path(root)).exists() else "yes"
    except OSError:
        return ""


def rank(settings, root: str | Path, query: str, rows: list[dict],
         limit: int = 3) -> list[dict]:
    """Files whose embedding sits closest to the question, or [] whenever the stack is absent.

    Vectors are recomputed only for files whose stamp moved; the query is embedded once per call.
    The result shape matches `symbols.rank` entries so the engine can slot them into the same
    five-file budget, and each carries its own reason code for the thread line.
    """
    root = Path(root)
    model_dir = configured(settings, root)
    if not model_dir or not available(settings, root) or not (query or "").strip():
        return []
    documents = _texts(rows or [])
    if not documents:
        return []
    cache = _load_cache(root)
    if cache.get("model") != model_dir:
        cache = {"schema": 1, "model": model_dir, "files": {}}
    store = cache["files"]
    try:
        embedder = _embedder(model_dir)
        stamps = {path: _file_stamp(root, path) for path in documents}
        moved = [path for path, stamp in stamps.items()
                 if stamp and (store.get(path, {}).get("stamp") != stamp
                               or "vec" not in store.get(path, {}))]
        if len(store) > CACHE_LIMIT:
            for path in list(store)[:len(store) - CACHE_LIMIT]:
                store.pop(path, None)
        if moved:
            texts = [documents[path] for path in moved]
            for path, vector in zip(moved, list(embedder.embed(texts))):
                store[path] = {"stamp": stamps[path],
                               "vec": [round(float(x), 6) for x in vector]}
            _save_cache(root, cache)
        query_vec = next(iter(embedder.embed([query[:MAX_DOC_CHARS]])), None)
        if query_vec is None:
            return []
        scored = [(_cosine(query_vec, row["vec"]), path)
                  for path, row in store.items()
                  if path in documents and isinstance(row, dict) and row.get("vec")]
    except Exception:   # noqa: BLE001 — any failure of an optional layer means "no layer today"
        return []
    scored.sort(reverse=True)
    out = []
    for score, path in scored[:limit * 2]:
        if score < MIN_SCORE:
            continue
        out.append({"path": path, "symbol": "", "why": "semantic",
                    "layer": "", "score": round(score, 3)})
        if len(out) >= limit:
            break
    return out


def search(settings, root: str | Path, query: str, rows: list[dict],
           limit: int = 6) -> list[dict]:
    """Same ranking, widened for a direct search call (the engine's budget stays its own)."""
    return rank(settings, root, query, rows, limit=limit)
