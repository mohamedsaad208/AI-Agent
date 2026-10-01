"""What a proposal is about to break, read off the repository rather than guessed at.

The reviewer sees the diff of the file being changed, and since the shrink warning the lines that
disappear from it. What no card has shown is the *other* files: who calls the method being removed,
which URL stops answering, which test names it. This module computes those from the symbol index and
the sources the caller already has open — deterministically, with no model in the loop and no new verb
— and reports what it could not check as an unknown, because a silent "nothing else uses it" reads to
a reviewer as a guarantee.

One pass over the candidate files, not one pass per name: `symbols.find_references` re-blanks a file
for every query, and an impact answer asks a dozen names of a hundred files. The line roles are the
same four the verb answers with, and they are built from the same two patterns it uses, so "call"
means one thing in this repository.
"""
from __future__ import annotations

import re

from . import symbols

MAX_NAMES = 6           # declarations named for one file
MAX_CANDIDATE_FILES = 120   # other files opened, ranked by how likely they are to name the change
MAX_LISTED = 5          # files named in one finding
MAX_UNKNOWN = 4         # admissions, so they cannot outvote the findings

# The same two shapes `symbols.find_references` reads a line with, reused rather than restated: a call
# site and a declaration site have to mean the same thing on the review card and in the tool's answer.
IMPORT_LINE = symbols.IMPORT_LINE
DECLARE_LINE = symbols.DECLARE_LINE
TEST_DIR = re.compile(r"(^|/)(?:tests?|it|spec)/", re.I)
TEST_NAME = re.compile(r"(?:Test|Tests|Spec|IT|TestCase)\.[A-Za-z]{1,5}$")
SQL_TEXT = re.compile(r"^\s*(?:select|update|delete\s+from|insert\s+into)\b|@Query\b", re.I)


def _name(key: str) -> str:
    """`member:LoginService.login` and `type:auth.LoginService` both answer with `LoginService`."""
    return key.split(":", 1)[-1].rsplit(".", 1)[-1]


def _units(row: dict | None) -> dict[str, str]:
    """Every declaration one parsed file holds, keyed so the two sides of a change can be compared.

    Members are keyed by their owning type because two classes in one file may both declare `login`;
    the value is the signature, which is what turns "the same name survived" into "the same call still
    works".
    """
    units: dict[str, str] = {}
    if not row:
        return units
    for item in row["types"]:
        units["type:" + item["name"]] = item["kind"]
        for signature in item["members"]:
            units["member:" + item["name"] + "." + symbols._member_name(signature)] = signature
    for signature in row["functions"]:
        units["function:" + symbols._member_name(signature)] = signature
    return units


def _routes(row: dict | None) -> dict[str, str]:
    """The endpoints one parsed file answers, as `"POST /login" -> "LoginController.login"`."""
    return {route["verb"] + " " + route["path"]: route["handler"]
            for route in ((row or {}).get("endpoints") or [])}


def candidate_files(rows: list[dict], changed: list[str],
                    limit: int = MAX_CANDIDATE_FILES) -> tuple[list[str], int]:
    """Which files are worth opening to look for a caller, and how many exist.

    Three ranks, cheapest signal first: a file that imports one of the changed files, a file in the
    same top-level folder (Java names a same-package class with no import at all), and a test file,
    which in a Maven or Gradle layout lives in a mirror tree no dependency edge and no folder
    comparison reaches — `src/test/java/a/LoginServiceTest.java` covers `a/LoginService.java` and is
    related to it by nothing but a name. The limit cuts from the end, so a project with 400 tests
    still loses tests before it loses the files that import the change.

    The second answer is how many files *other than the proposed ones* exist in the index, which is
    what lets the report say how much of the project it did not read rather than implying it read all
    of it. The proposed files were read as both sides of the diff, so counting them as unread would
    admit a gap that is not there.
    """
    edges = symbols.dependencies(rows)
    touched = {str(path) for path in changed}
    modules = {symbols.module_of(path) for path in touched}
    ranked: list[tuple[int, str]] = []
    for row in rows:
        path = row["path"]
        if path in touched:
            continue
        if set(edges.get(path, [])) & touched:
            rank = 0
        elif symbols.module_of(path) in modules:
            rank = 1
        elif _is_test(path):
            rank = 2
        else:
            continue
        ranked.append((rank, path))
    ordered = [path for _rank, path in sorted(ranked)]
    return ordered[:limit], sum(1 for row in rows if row["path"] not in touched)


def analyze(changes: list[dict], rows: list[dict], sources, *, visible: int | None = None) -> dict:
    """Compare each proposed file against the repository that uses it.

    `changes` is the session's own list (path / before / after / delete), `rows` the symbol index for
    the folder, and `sources` the `(path, text)` pairs that could name a changed symbol — see
    `candidate_files`. Both sides of every change are re-parsed here rather than trusted as the model
    described them: the answer is worth what the bytes say.

    `visible` is how many files a caller search could have covered. The caller knows — it asked
    `candidate_files` for the list — so it is passed in rather than guessed from the index, which
    counts the proposed files themselves.
    """
    site_list = list(sources)
    out: dict = {"files": [], "unknown": [], "searched": len(site_list),
                 "visible": len(rows) if visible is None else visible}
    if not changes:
        return out
    if not rows:
        out["unknown"].append({"key": "impact_unknown_no_index"})
    for change in changes:
        out["files"].append(_one(change, site_list))
    _admit(out, rows, changes)
    return out


def _one(change: dict, site_list: list) -> dict:
    path = str(change.get("path", ""))
    before_text = change.get("before") or ""
    after_text = "" if change.get("delete") else (change.get("after") or "")
    old, new = symbols.parse(path, before_text), symbols.parse(path, after_text)
    entry: dict = {"path": path, "removed": [], "changed": [], "added": [], "endpoints": [],
                   "callers": [], "tests": [], "modules": [], "flags": []}
    if old is None and new is None:
        # No grammar for this file type. Said on the card rather than left out: an empty answer from a
        # file the tool cannot read is not the same claim as an empty answer from one it can.
        entry["unread"] = True
        if SQL_TEXT.search(before_text) or SQL_TEXT.search(after_text):
            entry["flags"].append("impact_text_changed")
        return entry

    before_units, after_units = _units(old), _units(new)
    gone = [key for key in before_units if key not in after_units]
    altered = [key for key in before_units
               if key in after_units and before_units[key] != after_units[key]]
    born = [key for key in after_units if key not in before_units]
    entry["removed"] = [_name(key) for key in gone][:MAX_NAMES]
    entry["changed"] = [_name(key) for key in altered][:MAX_NAMES]
    entry["added"] = [_name(key) for key in born][:MAX_NAMES]
    entry["removed_more"] = max(0, len(gone) - MAX_NAMES)
    entry["changed_more"] = max(0, len(altered) - MAX_NAMES)

    routes_before, routes_after = _routes(old), _routes(new)
    for label, handler in routes_before.items():
        if label not in routes_after:
            entry["endpoints"].append({"route": label, "handler": handler, "gone": True})
    for label, handler in routes_after.items():
        if label not in routes_before:
            entry["endpoints"].append({"route": label, "handler": handler, "gone": False})

    lost_lines = [line for line in before_text.splitlines()
                  if line.strip() and line.strip() not in after_text]
    if any(SQL_TEXT.search(line) for line in lost_lines):
        entry["flags"].append("impact_query")

    names = {_name(key) for key in gone + altered}
    names |= {_name(route["handler"]) for route in entry["endpoints"] if route["gone"]}
    strong_files = _use_sites(entry, names, path, site_list)
    if strong_files:
        entry["modules"] = sorted({symbols.module_of(caller) for caller in strong_files}
                                  | {symbols.module_of(path)})
    return entry


def _use_sites(entry: dict, names: set[str], path: str, site_list: list) -> set[str]:
    """Every place the disappearing names are still written, in one pass over the candidate files.

    The answer per file is which names it still holds and in what role, not the text of the line: an
    impact block that quoted a call site would print source nobody asked to see on a card that is
    exported, and a reviewer who wants the line has the path. The return value is every file that
    names something strongly, which is wider than the list shown, so "modules touched" does not
    inherit the display cap and imply a change reaches two modules when it reaches five.
    """
    keep = {name for name in names if name and name not in symbols.STOP_WORDS}
    if not keep or not site_list:
        return set()
    pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(name) for name in sorted(keep)) + r")(?!\w)")
    found: dict[str, dict[str, set[str]]] = {name: {"call": set(), "declaration": set(),
                                                    "import": set(), "mention": set()}
                                             for name in keep}
    capped: set[str] = set()
    for other, body in site_list:
        if other == path:
            continue
        clean = symbols.blank(body, backticks=other.endswith((".js", ".jsx", ".ts", ".tsx",
                                                              ".mjs", ".cjs")))
        counted: dict[str, int] = {}
        for bare in clean.splitlines():
            for match in pattern.finditer(bare):
                name = match.group(1)
                if counted.get(name, 0) >= symbols.PER_FILE_LIMIT:
                    # Named here too often to be listed truthfully, which is said once, per name.
                    capped.add(name)
                    continue
                counted[name] = counted.get(name, 0) + 1
                kind = "mention"
                if IMPORT_LINE.match(bare):
                    kind = "import"
                elif DECLARE_LINE.match(bare):
                    kind = "declaration"
                elif re.search(r"(?<!\w)" + re.escape(name) + r"\s*\(", bare):
                    kind = "call"
                found[name][kind].add(other)
    rows: list[dict] = []
    wide: set[str] = set()
    for name, sites in found.items():
        strong = sorted(sites["call"] | sites["declaration"] | sites["import"])
        wide |= set(strong)
        if strong:
            rows.append({"name": name, "files": strong[:MAX_LISTED], "count": len(strong),
                         "capped": name in capped})
        tests = sorted({file for group in sites.values() for file in group if _is_test(file)})
        if tests:
            entry["tests"].append({"name": name, "files": tests[:MAX_LISTED], "count": len(tests)})
    rows.sort(key=lambda row: (-row["count"], row["name"]))
    entry["call_capped"] = any(row["capped"] for row in rows)
    entry["callers"] = rows[:MAX_NAMES]
    return wide


def _is_test(path: str) -> bool:
    clean = str(path).replace("\\", "/")
    return bool(TEST_DIR.search(clean) or TEST_NAME.search(clean))


def _admit(out: dict, rows: list[dict], changes: list[dict]) -> None:
    """Every way this answer is smaller than the truth, recorded as a named admission.

    The unknowns are keys with their numbers, not sentences: the review card and the desktop window
    have to say these in the language the task was asked in, and a stored sentence can only ever be
    read back in the one it was written in.
    """
    unknown: list[dict] = list(out.get("unknown") or [])
    # Worth saying only when something was actually looked for: a proposal that touches one SQL file
    # and no code has no caller search to have missed, and admitting a missed search there would
    # contradict the line above it.
    looked = any(entry.get("removed") or entry.get("changed") or
                 any(route["gone"] for route in entry.get("endpoints") or [])
                 for entry in out["files"])
    if rows and looked and out["searched"] < out["visible"]:
        unknown.append({"key": "impact_unknown_searched", "searched": out["searched"],
                        "visible": out["visible"]})
    indexed = {row["path"]: row for row in rows}
    for change in changes:
        path = str(change.get("path", ""))
        if rows and path not in indexed and symbols.indexable(path):
            unknown.append({"key": "impact_unknown_not_indexed", "path": path})
        row = indexed.get(path)
        if row and row.get("routes_capped"):
            unknown.append({"key": "impact_unknown_routes", "path": path,
                            "max": symbols.MAX_ENDPOINTS})
    if any(entry.get("unread") for entry in out["files"]):
        unknown.append({"key": "impact_unknown_unread"})
    if any(entry.get("call_capped") for entry in out["files"]):
        unknown.append({"key": "impact_unknown_per_file", "limit": symbols.PER_FILE_LIMIT})
    if any(entry.get("removed_more") or entry.get("changed_more") for entry in out["files"]):
        unknown.append({"key": "impact_unknown_names", "max": MAX_NAMES})
    out["unknown"] = unknown[:MAX_UNKNOWN]
