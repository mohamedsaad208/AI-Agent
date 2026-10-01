"""What each source file declares, so a small model stops guessing from file names.

The repo map used to be one regex over every line, which invented "symbols" out of
comments and string literals and missed structure a parser sees for free. Python is
parsed with `ast`. Java, Kotlin, TypeScript, Go and Rust get a bounded scanner over
comment/string-blanked text: enough to answer "which type owns this method, and what
does this file import" without pretending to be a compiler.

Nothing here executes project code, and every list is capped, because the result is
pasted into a model prompt whose budget is the whole point.
"""
from __future__ import annotations

import ast
import re

MAX_FILES = 300
MAX_TYPES = 40          # declared types per file
MAX_MEMBERS = 25        # methods per type
MAX_IMPORTS = 40        # imports per file
MAX_SIGNATURE = 60      # characters of one signature
MAX_ENDPOINTS = 40      # routes per file; a controller over this is a generated one
SCRIPT_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
INDEXABLE = {".py", ".java", ".kt", ".go", ".rs"} | SCRIPT_SUFFIXES
# Kinds whose declared types are importable by their simple name from another file.
NAMED_KINDS = {"jvm", "script", "go", "rust"}

# A name in this set means the line is a statement, not a declaration.
CONTROL = {"if", "for", "while", "switch", "catch", "return", "do", "else", "try",
           "finally", "throw", "new", "synchronized", "case", "when", "init", "super",
           "this", "assert", "yield", "await", "print", "lambda", "var", "val"}
LINE_STARTS = ("public ", "private ", "protected ", "static ", "final ", "abstract ",
               "default ", "synchronized ", "native ", "open ", "override ", "suspend ",
               "internal ", "inline ", "operator ", "fun ", "void ", "class ",
               "interface ", "enum ", "record ", "object ", "annotation ", "sealed ",
               "data ", "companion ", "external ", "tailrec ")
TYPE_RE = re.compile(r"\b(class|interface|enum|record|object|annotation)\s+([A-Za-z_$][\w$]*)")
PACKAGE_RE = re.compile(r"^\s*package\s+(?:`)?([\w.$]+)")
IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?(?:`)?([\w.$]+)")
JAVA_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|abstract|default|synchronized|native"
    r"|strictfp|open|transient|volatile)\s+)+"
    r"(?:([\w$][\w$<>\[\],.?\s]*?)\s+)?([A-Za-z_$][\w$]*)\s*\(([^()]*)\)\s*"
    r"(?:throws [\w$.,\s]+)?[{;]")
KOTLIN_FUN_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|abstract|open|override|suspend|inline"
    r"|operator|tailrec|external|annotation)\s+)*fun\s+(?:<[^<>]*>\s*)?"
    r"(?:([\w$][\w$.<>,\s]*)\s*\.\s*)?([A-Za-z_$][\w$]*)\s*\(([^()]*)\)")
# Package-private Java methods carry no modifier, which is how JUnit tests are written.
JAVA_PLAIN_METHOD_RE = re.compile(
    r"^\s*(?:@[\w.]+\s+)*(?:final\s+|static\s+|abstract\s+|default\s+)*"
    r"([\w$][\w$<>\[\],.?\s]*?)\s+([A-Za-z_$][\w$]*)\s*\(([^()]*)\)\s*"
    r"(?:throws [\w$.,\s]+)?[{;]")

# ---------------- HTTP endpoints on the JVM ----------------
# Three dialects spell the same route. Spring fuses verb and path into one annotation name, JAX-RS
# splits them (`@GET` above `@Path`), and only the Spring family is case-mixed, so its verbs are
# translated here rather than upper-cased where they are read.
MAPPING_RE = re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping(?![\w$])")
JAXRS_VERB_RE = re.compile(r"@(GET|POST|PUT|DELETE|HEAD|OPTIONS|PATCH)(?![\w$])")
JAXRS_PATH_RE = re.compile(r"@Path(?![\w$])")
JVM_VERBS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH"}
# A supers clause, `extends` or `implements`, up to the body. The angle brackets are emptied by
# `_supers` before this runs, so a generic bound (`<T extends Comparable<T>>`) is never read as one.
JVM_SUPERS_RE = re.compile(r"\b(?:extends|implements)\s+([^({;<>]+)")

# ---------------- JavaScript / TypeScript / JSX ----------------
# `export` is a modifier here, never a second declaration: the same name is recorded once.
JS_PREFIX = r"^\s*(?:export\s+)?(?:default\s+)?(?:export\s+)?(?:declare\s+)?"
# A type-parameter / type-argument list, optional as a whole. One nesting level is enough for the
# clauses real code writes, a bare `[^>]*` would stop at the inner `>` of `Map<String, T>`, and the
# fill excludes `{` so the tail of a declaration line is never swallowed by an empty match.
JS_GENERIC = r"(?:<(?:[^<{]|<[^<>]*>)*>)?"
JS_CLASS_RE = re.compile(JS_PREFIX + r"class\s+([A-Za-z_$][\w$]*)\s*" + JS_GENERIC + r"\s*"
                         r"(?:extends\s+([A-Za-z_$][\w$.]*)\s*" + JS_GENERIC + r"\s*)?"
                         r"(?:implements\s+[^{]*)?\{?")
JS_TYPE_RE = re.compile(JS_PREFIX + r"(interface|enum|type)\s+([A-Za-z_$][\w$]*)")
JS_FUNC_RE = re.compile(JS_PREFIX + r"(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*"
                        + JS_GENERIC + r"\s*\(([^()]*)\)")
JS_ARROW_RE = re.compile(JS_PREFIX + r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
                        r"(?:async\s*)?(?:\(([^()]*)\)|[A-Za-z_$][\w$]*)(?:\s*:[^=\n]+?)?\s*=>")
# A class member: `name(args) {`, with optional modifiers and an optional `: Type`.
JS_MEMBER_RE = re.compile(r"^\s*(?:(?:public|private|protected|static|readonly|abstract"
                          r"|override|async|declare|get|set|declare)\s+)*#?[A-Za-z_$][\w$]*"
                          r"\s*\(([^()]*)\)\s*(?::[^;{]+)?\{")
JS_MEMBER_NAME_RE = re.compile(r"^\s*(?:(?:public|private|protected|static|readonly|abstract"
                               r"|override|async|get|set|declare)\s+)*#?([A-Za-z_$][\w$]*)\s*\(")
JS_FROM_RE = re.compile(r"(?:^|;)\s*(?:import|export)\b[^;\n]*?\bfrom\s*['\"]([^'\"]+)['\"]")
JS_BARE_IMPORT_RE = re.compile(r"^\s*import\s*['\"]([^'\"]+)['\"]")
JS_REQUIRE_RE = re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)")

# ---------------- Go ----------------
GO_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)")
# func Name(...) and func (recv Type) Name(...) — the receiver decides the owning type.
GO_FUNC_RE = re.compile(r"^\s*func\s+(?:\(\s*(?:_\s+|\w+\s+)?\*?([\w\[\]]+)\s*\)\s*)?"
                        r"([A-Za-z_]\w*)\s*\(([^()]*)\)")
GO_TYPE_RE = re.compile(r"^\s*type\s+([A-Za-z_]\w*)\s+(struct|interface)\b")
GO_QUOTED_RE = re.compile(r'"([^"\n]+)"')
GO_IMPORT_RE = re.compile(r'^import\s+(?:[\w.]+\s+)?"([^"]+)"', re.M)
GO_IMPORT_BLOCK_RE = re.compile(r"^import\s*\(([^)]*)\)", re.M | re.S)

# ---------------- Rust ----------------
RS_TYPE_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(struct|enum|trait|union|type|mod)\s+"
                        r"([A-Za-z_]\w*)")
RS_IMPL_RE = re.compile(r"^\s*(?:unsafe\s+)?impl\s*(?:<[^<>]*>\s*)?(?:([\w:]+)\s+for\s+)?"
                        r"([A-Za-z_][\w:]*)")
RS_FN_RE = re.compile(r"^\s*(?:(?:pub|crate)(?:\([^)]*\))?|default|const|unsafe|async"
                      r"|extern\s*(?:\([^)]*\))?|\s)*fn\s+([A-Za-z_]\w*)\s*(?:<[^<>]*>)?"
                      r"\s*\(([^()]*)\)")
RS_USE_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+([^;]+);")
RS_USE_BRACE_RE = re.compile(r"^([\w:]+)::\{([^}]*)\}$")


def _signature(name: str, params: list[str]) -> str:
    text = re.sub(r"\s+", " ", name + "(" + ", ".join(p.strip() for p in params) + ")")
    return text[:MAX_SIGNATURE] + "…" if len(text) > MAX_SIGNATURE else text


def blank(source: str, backticks: bool = False) -> str:
    """Blank out comments and string contents, keeping every line where it was.

    Without this, `// class Foo` becomes a class and a log message becomes a method.
    `backticks` additionally blanks JavaScript template literals; a stray backtick in a
    regex literal is not allowed to swallow the rest of the file, so an unterminated one
    only reaches the end of its own line.
    """
    out, i, n = [], 0, len(source)
    while i < n:
        ch = source[i]
        pair = source[i:i + 2]
        if pair == "//":
            end = source.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
        elif pair == "/*":
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append("".join(c if c == "\n" else " " for c in source[i:end]))
            i = end
        elif backticks and ch == "`":
            end = source.find("`", i + 1)
            if end < 0:
                # A stray backtick — inside a regex literal, say — may not eat the file.
                end = source.find("\n", i + 1)
                end = n if end < 0 else end
                out.append(" " * (end - i))
                i = end
            else:
                out.append("".join(c if c == "\n" else " " for c in source[i:end + 1]))
                i = end + 1
        elif source.startswith('"""', i) or source.startswith("'''", i):
            quote = source[i:i + 3]
            end = source.find(quote, i + 3)
            end = n if end < 0 else end + 3
            out.append("".join(c if c == "\n" else " " for c in source[i:end]))
            i = end
        elif ch in "\"'":
            end = i + 1
            while end < n:
                if source[end] == "\\":
                    end += 2
                    continue
                if source[end] in (ch, "\n"):
                    break
                end += 1
            out.append(" " * (end - i))
            # Step over the closing quote, or it reads as the opening one of a second
            # string and swallows the rest of the line.
            i = end + 1 if end < n and source[end] == ch else end
        else:
            out.append(ch)
            i += 1
    return "".join(out)


ANNOTATION_LEAD = re.compile(r"@[A-Za-z_$][\w.$]*(?:\s*\([^()]*(?:\([^()]*\)[^()]*)*\))?\s*")


def _after_annotations(line: str) -> str:
    """The line with every leading annotation cut off, so the declaration under it can be matched.

    One nesting level of parentheses is enough: an annotation argument list that itself calls a
    method (`@Cacheable(key = "#id")`) still ends where the first unbalanced `)` is, and the rest of
    the line is the declaration the caller wants.
    """
    text = line.lstrip()
    while text.startswith("@"):
        lead = ANNOTATION_LEAD.match(text)
        if not lead or lead.end() == 0:
            break
        text = text[lead.end():]
    return text


def _jvm_annotation(head: str, raw: str, depth: int) -> tuple[str, object] | None:
    """What a mapping line says, as ("BASE"|"PATH"|"VERB", value).

    The verb is read from the blanked line and the path from the raw one: `blank()` keeps the shape of a
    line and removes the inside of every string, so `@GetMapping("/login")` survives as
    `@GetMapping("       ")` — enough to name the annotation, useless for the URL it maps.
    A class-level mapping is a prefix, not an endpoint: Spring's `@RequestMapping` on the type and
    JAX-RS's `@Path` there both join the method paths under them.
    """
    match = MAPPING_RE.match(head)
    if match:
        name = match.group(1).capitalize()
        path = _annotation_path(raw)
        if name == "Request":
            if depth == 0:
                return ("BASE", path)
            # The old spelling puts the verb in an attribute rather than the annotation name.
            method = re.search(r"RequestMethod\.([A-Z]+)", raw)
            return ("VERB", (method.group(1) if method else "ANY", path))
        return ("VERB", (JVM_VERBS.get(name, name.upper()), path))
    if JAXRS_VERB_RE.match(head):
        return ("VERB", (JAXRS_VERB_RE.match(head).group(1).upper(), _annotation_path(raw)))
    if JAXRS_PATH_RE.match(head):
        return ("PATH", _annotation_path(raw))
    return None


def _annotation_path(raw: str) -> str:
    """The path an annotation carries, whether it is quoted, bare, or named `value`/`path`."""
    quoted = re.search(r'"([^"\n]*)"', raw)
    if quoted:
        return quoted.group(1).strip()[:120]
    bare = re.match(r"^\s*@\w+\s*\(\s*([\w\-/{}.]+)", raw)
    return bare.group(1)[:120] if bare else ""


def _join_path(base: str, path: str) -> str:
    joined = "/".join(part for part in (base.strip("/"), path.strip("/")) if part)
    return "/" + joined if joined else "/"


def _supers(line: str) -> list[str]:
    """`extends` and `implements` names on a type header, generics reduced to what they wrap.

    A generic is emptied from the inside out before the clauses are read. `class A<T extends Base>
    extends Real` has two `extends` and only the second is a supertype, and the one that survives
    this is the one outside the brackets — `Base` there would otherwise be the answer the map gives.
    """
    for _ in range(4):
        stripped = re.sub(r"<[^<>]*>", "", line)
        if stripped == line:
            break
        line = stripped
    found = []
    for clause in JVM_SUPERS_RE.findall(line):
        # One clause runs to the brace, so `extends A implements B, C` arrives as a single capture and
        # the keyword inside it starts a new list of names.
        for part in re.split(r"\b(?:extends|implements)\b", clause):
            for piece in part.split(","):
                name = re.match(r"[A-Za-z_$][\w$.]*", piece.strip())
                if not name:
                    continue
                simple = name.group(0).rsplit(".", 1)[-1]
                if simple.lower() not in ("class", "interface") and simple not in CONTROL:
                    found.append(simple)
    return found[:6]


def _jvm(path: str, source: str) -> dict:
    """Java/Kotlin declarations, found by a brace-depth scan of blanked text.

    A method counts only at one level inside a type body, which is what separates
    `public Token login(String p)` from `tokenService.login(password)` two lines later.
    """
    row = {"path": path, "kind": "jvm", "package": "", "imports": [], "types": [],
           "functions": [], "endpoints": []}
    raw_lines = source.splitlines()
    pending: tuple[str, str] | None = None      # a verb waiting for the method under it
    path_wait = ""                              # a JAX-RS `@Path` above its `@GET`
    base_path = ""
    depth = 0
    stack: list[tuple[str, int]] = []           # (type name, depth where it opened)
    for number, line in enumerate(blank(source).splitlines(), 1):
        head = line.strip()
        raw = raw_lines[number - 1] if number - 1 < len(raw_lines) else line
        opened, closed = line.count("{"), line.count("}")
        annotation = _jvm_annotation(head, raw, depth)
        if annotation is not None:
            kind, value = annotation
            if kind == "BASE":
                base_path = str(value) or base_path
            elif kind == "PATH":
                if depth == 0:
                    base_path = str(value) or base_path
                elif pending is not None and not pending[1]:
                    # JAX-RS writes the verb first and the path under it, so a method-level `@Path`
                    # belongs to the mapping that is already armed rather than to the next one.
                    pending = (pending[0], str(value))
                else:
                    path_wait = str(value)
            else:
                verb, explicit = value  # type: ignore[misc]
                pending = (verb, explicit or path_wait)
                path_wait = ""
        if head.startswith("package") and not row["package"]:
            match = PACKAGE_RE.match(line)
            if match:
                row["package"] = match.group(1)
        elif head.startswith("import"):
            match = IMPORT_RE.match(line)
            if match and len(row["imports"]) < MAX_IMPORTS and match.group(1) not in row["imports"]:
                row["imports"].append(match.group(1))
        declared = TYPE_RE.findall(line)
        if declared:
            for kind, simple in declared:
                outer = next((name for name, level in reversed(stack) if level == depth), "")
                name = outer + "." + simple if outer else simple
                _declare_type(row, simple, kind, number, outer, _supers(line))
                # The frame opens even when the cap refused the type: without it a method
                # inside has no owner at all and lands in the file's own function list.
                stack.append((name, depth))
            # A type header swallows whatever verb was waiting: an annotated class is not an endpoint,
            # and leaving it armed would hand that mapping to the first method it happens to see.
            pending = None
        elif head.startswith("@") or any(head.startswith(word) for word in LINE_STARTS):
            # An annotation-led head is read as a declaration too: `@GetMapping("/health")
            # public String health() {` names the route and the method on one line, and every method
            # pattern below is anchored at the start, so the line is shortened before it is matched.
            body = _after_annotations(line)
            method = None
            if re.search(r"\bfun\s", body):
                method = KOTLIN_FUN_RE.match(body)
            method = (method or JAVA_METHOD_RE.match(body)
                      or JAVA_PLAIN_METHOD_RE.match(body))
            if method and method.group(2) not in CONTROL:
                params = [part for part in method.group(3).split(",") if part.strip()][:6]
                if stack:
                    owner = next((item for item in reversed(row["types"])
                                  if item["name"] == stack[-1][0]), None)
                    inside = bool(owner) and depth == stack[-1][1] + 1
                    if inside and len(owner["members"]) < MAX_MEMBERS:
                        owner["members"].append(_signature(method.group(2), params))
                    if inside and pending is not None:
                        if len(row["endpoints"]) < MAX_ENDPOINTS:
                            row["endpoints"].append({"verb": pending[0],
                                                     "path": _join_path(base_path, pending[1]),
                                                     "handler": f"{owner['name']}.{method.group(2)}",
                                                     "line": number})
                        else:
                            # The refused routes are recorded as refused: a later reader that asks
                            # "does anything answer this URL" has to be able to tell "no" from
                            # "the list stopped at 40".
                            row["routes_capped"] = True
                        pending = None
                elif len(row["functions"]) < MAX_MEMBERS:
                    # A Kotlin file-level function, which belongs to no type.
                    row["functions"].append(_signature(method.group(2), params))
                # A path with no verb on the method under it maps nothing, and holding it over would
                # arm the next handler in the file with this one's URL.
                path_wait = ""
        depth += opened - closed
        while stack and depth <= stack[-1][1]:
            stack.pop()
    return row


def _row(path: str, kind: str) -> dict:
    return {"path": path, "kind": kind, "package": "", "imports": [], "types": [],
            "functions": []}


def _add_import(row: dict, name: str) -> None:
    name = name.strip().strip("\"'")
    if name and name not in row["imports"] and len(row["imports"]) < MAX_IMPORTS:
        row["imports"].append(name)


def _declare_type(row: dict, name: str, kind: str, line: int, outer: str = "",
                  supers: list[str] | None = None) -> dict | None:
    """The entry for a declared type, created once per name.

    `export class X` and a later `export { X }`, or a Go type with its methods spread over
    several files in the same folder, must not produce the same type twice.
    """
    full = outer + "." + name if outer else name
    for item in row["types"]:
        if item["name"] == full:
            if supers and not item.get("extends"):
                item["extends"] = supers
            return item
    if len(row["types"]) >= MAX_TYPES:
        return None
    item = {"name": full, "kind": kind, "line": line, "members": []}
    if supers:
        item["extends"] = supers
    row["types"].append(item)
    return item


def _declare(row: dict, owner: dict | None, name: str, params: str) -> None:
    text = _signature(name, [part for part in params.split(",") if part.strip()][:6])
    bucket = owner["members"] if owner is not None else row["functions"]
    if text not in bucket and len(bucket) < MAX_MEMBERS:
        bucket.append(text)


def _specifier(raw: str) -> str:
    """A JavaScript module specifier in the dotted form `dependencies()` compares against.

    "./text" means this file's own folder, which is exactly what a single leading dot
    means to the Python rules already in place, so one dot is dropped, not kept.
    """
    text = raw.strip().strip("/")
    if text.startswith("."):
        return text.replace("/", ".")[1:]
    return text.replace("/", ".")


def _use_targets(clause: str) -> list[str]:
    """Rust `use` paths, without the crate root and with `::` read as `.`."""
    def one(text: str) -> str:
        path = text.strip().split(" as ")[0].replace("::", ".")
        for root in ("crate.", "self.", "super."):
            if path.startswith(root):
                # `super` is one level up, exactly as a leading dot reads in Python.
                return "." + path[len(root):] if root == "super." else path[len(root):]
        return path

    brace = RS_USE_BRACE_RE.match(clause.strip())
    if brace:
        return [one(brace.group(1) + "::" + item.strip())
                for item in brace.group(2).split(",") if item.strip()][:MAX_IMPORTS]
    return [one(clause)] if clause.strip() else []


def _go_imports(source: str) -> list[str]:
    """Every import path in a Go file, single-line and grouped.

    Read from the original text because the blanker erases string contents, and that is
    where the path lives; a line whose own text starts with `//` is a comment, not a path.
    """
    found = GO_IMPORT_RE.findall(source)
    for block in GO_IMPORT_BLOCK_RE.findall(source):
        for line in block.splitlines():
            text = line.strip()
            if not text or text.startswith("//"):
                continue
            match = GO_QUOTED_RE.search(text)
            if match:
                found.append(match.group(1))
    return found


def _script(path: str, source: str) -> dict:
    """TypeScript and JavaScript declarations.

    Import specifiers live inside string literals, which the blanker erases, so every line
    is read twice: the blanked copy decides whether a declaration is really there, the
    original copy supplies the module path.
    """
    row = _row(path, "script")
    blanked = blank(source, backticks=True).splitlines()
    original = source.splitlines()
    depth = 0
    stack: list[tuple[str, int]] = []
    for number, (line, raw) in enumerate(zip(blanked, original), 1):
        head = line.strip()
        opened, closed = line.count("{"), line.count("}")
        declared = None                       # a class body opened by this line
        if head.startswith(("import", "export")) or "require(" in head:
            found = JS_FROM_RE.findall(raw) + JS_BARE_IMPORT_RE.findall(raw)
            if "require(" in line:
                found += JS_REQUIRE_RE.findall(raw)
            for specifier in found:
                _add_import(row, _specifier(specifier))
        match = JS_CLASS_RE.match(line)
        if match and match.group(1) not in CONTROL:
            outer = next((name for name, level in reversed(stack) if level == depth), "")
            owner = _declare_type(row, match.group(1), "class", number, outer)
            if owner is not None:
                if match.group(2) and not owner.get("extends"):
                    owner["extends"] = [match.group(2)[:40]]
                if opened > closed:
                    declared = owner["name"]
        elif (match := JS_TYPE_RE.match(line)):
            _declare_type(row, match.group(2), match.group(1), number)
        elif (match := JS_FUNC_RE.match(line)) or (match := JS_ARROW_RE.match(line)):
            _declare(row, _owner_of(row, stack, depth), match.group(1), match.group(2) or "")
        elif "(" in line and line.rstrip().endswith("{"):
            member = JS_MEMBER_RE.match(line)
            owner = _owner_of(row, stack, depth)
            if member and owner is not None:
                _declare(row, owner, JS_MEMBER_NAME_RE.match(line).group(1), member.group(1))
        depth += opened - closed
        if declared:
            stack.append((declared, depth - (opened - closed)))
        while stack and depth <= stack[-1][1]:
            stack.pop()
    return row


def _owner_of(row: dict, stack: list[tuple[str, int]], depth: int) -> dict | None:
    """The type one level above this line, or None at file level.

    A function at that level is a method; one deeper is inside a body and is not.
    """
    if not stack or depth != stack[-1][1] + 1:
        return None
    return next((item for item in reversed(row["types"]) if item["name"] == stack[-1][0]), None)


def _go(path: str, source: str) -> dict:
    """Go declarations: a receiver picks the owning type, a package names the folder."""
    row = _row(path, "go")
    for imported in _go_imports(source)[:MAX_IMPORTS]:
        _add_import(row, imported)
    for number, line in enumerate(blank(source).splitlines(), 1):
        head = line.strip()
        if not head:
            continue                     # a commented-out func is not a declaration
        if head.startswith("package") and not row["package"]:
            match = GO_PACKAGE_RE.match(line)
            if match:
                row["package"] = match.group(1)
        elif head.startswith("type"):
            match = GO_TYPE_RE.match(line)
            if match:
                _declare_type(row, match.group(1), match.group(2), number)
        elif head.startswith("func"):
            match = GO_FUNC_RE.match(line)
            if match:
                receiver, name, params = match.groups()
                owner = (_declare_type(row, receiver.strip("*[]"), "type", number)
                         if receiver else None)
                _declare(row, owner, name, params)
    return row


def _rust(path: str, source: str) -> dict:
    """Rust declarations: `impl Trait for Type` and `trait` bodies own their functions."""
    row = _row(path, "rust")
    blanked = blank(source).splitlines()
    original = source.splitlines()
    depth = 0
    owner: dict | None = None
    owner_depth = 0
    for number, (line, raw) in enumerate(zip(blanked, original), 1):
        head = line.strip()
        opened, closed = line.count("{"), line.count("}")
        entered = None
        if head.startswith("use "):
            clause = RS_USE_RE.match(raw)
            if clause:
                for target in _use_targets(clause.group(1)):
                    _add_import(row, target)
        elif head.startswith("impl"):
            match = RS_IMPL_RE.match(line)
            if match:
                entered = _declare_type(row, match.group(2).split("::")[-1], "impl", number)
        elif "fn " in line:
            match = RS_FN_RE.match(line)
            if match:
                _declare(row, owner if depth >= owner_depth else None,
                         match.group(1), match.group(2))
        else:
            match = RS_TYPE_RE.match(line)
            if match:
                entered = _declare_type(row, match.group(2), match.group(1), number)
        depth += opened - closed
        if entered is not None and opened > closed:
            owner, owner_depth = entered, depth
        elif owner is not None and depth < owner_depth:
            owner, owner_depth = None, 0
    return row


def _params(node: ast.arguments) -> list[str]:
    names = [arg.arg for arg in node.posonlyargs + node.args]
    if names and names[0] in {"self", "cls"}:
        names = names[1:]
    names += [arg.arg for arg in node.kwonlyargs]
    if node.vararg:
        names.append("*" + node.vararg.arg)
    if node.kwarg:
        names.append("**" + node.kwarg.arg)
    return names[:6]


def _python(path: str, source: str) -> dict | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None
    row = {"path": path, "kind": "python", "package": "", "imports": [], "types": [],
           "functions": []}

    def walk(body, prefix: str, owner: dict | None) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                if len(row["types"]) >= MAX_TYPES:
                    return
                item = {"name": prefix + node.name, "kind": "class", "line": node.lineno,
                        "extends": [ast.unparse(base)[:40] for base in node.bases][:4],
                        "members": []}
                row["types"].append(item)
                walk(node.body, prefix + node.name + ".", item)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                text = _signature(node.name, _params(node.args))
                if owner is not None and len(owner["members"]) < MAX_MEMBERS:
                    owner["members"].append(text)
                elif owner is None and len(row["functions"]) < MAX_MEMBERS:
                    row["functions"].append(text)
            elif isinstance(node, ast.Import):
                row["imports"].extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = "." * node.level + (node.module or "")
                row["imports"].extend(base + "." + alias.name for alias in node.names)
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                walk(node.body, prefix, owner)

    walk(tree.body, "", None)
    row["imports"] = list(dict.fromkeys(row["imports"]))[:MAX_IMPORTS]
    return row


def fallback(path: str, source: str) -> dict:
    """The old lexical scan, kept for files a real parser refuses to read."""
    names = re.findall(r"(?m)^\s*(?:(?:public|private|protected|abstract|final|static)\s+)*"
                       r"(?:class|interface|enum|record|def|fun)\s+(\w+)", source)
    return {"path": path, "kind": "lexical", "package": "", "imports": [], "functions": [],
            "types": [{"name": name, "kind": "lexical", "line": 0, "members": []}
                      for name in names[:MAX_TYPES]]}


def suffix_of(path: str) -> str:
    parts = path.casefold().rsplit(".", 1)
    return "." + parts[-1] if len(parts) == 2 else ""


# The files that say what a project *is* and how it runs. Not source, and not everything that is not
# source: a map that listed every YAML file would be a file listing again. This is the short set a
# model needs in order to know a reactor's modules, a service's port and a package's dependencies
# without spending three of its twelve turns reading them.
CONFIG_NAMES = {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
                "settings.gradle.kts", "package.json", "go.mod", "cargo.toml", "pyproject.toml"}
CONFIG_PREFIXES = ("application", "bootstrap")
CONFIG_SUFFIXES = {".yml", ".yaml", ".properties"}
MAX_FACTS = 6
MAX_FACT_CHARS = 160
# A key's *name* decides whether its value may be shown. `server.port` is what a starting order needs;
# `spring.datasource.password` is not, and a map that carried it would also carry it into
# `agent export-session`, which is the one artefact that leaves the machine.
# `user` is here because a leaf key can be allow-listed while the path above it is a credential store:
# `spring.security.user.name` and `spring.security.user.password` both sit under a login.
CREDENTIAL_KEY = re.compile(r"(?i)(pass(word|wd)?|secret|token|key|credential|cert(ificate)?|user)")
# Matched against the whole dotted path, so a key only means what its owner says it means. A bare
# `name` under `logging.file` is a log file, not the application, and a map that said otherwise would
# be a wrong fact the model has no way to doubt.
KEY_FACTS = {"port": "port", "server.port": "port", "management.server.port": "management port",
             "name": "name", "application.name": "name", "spring.application.name": "name",
             "artifactid": "artifact"}
# The three leaves that say the same thing whoever owns them, so the flat `spring.datasource.url=…`
# spelling of a properties file reads the same as the nested YAML one.
LEAF_FACTS = {"port": "port", "defaultzone": "registry", "url": "host"}
# A URL's authority, with the user and the password taken off the front of it. The leading group is
# repeatable because JDBC nests its own scheme -- `jdbc:postgresql://user:pw@host:5432/db` is one of the
# most common lines in a Spring project, and a single-scheme pattern read it as no host at all.
URL_HOST = re.compile(r"^(?:[\w+.\-]+:)*//(?:[^@/]+@)?([\w.\-]+(?::\d+)?)")
# The same authority with no scheme in front of it (`localhost:8761/eureka`). It cannot carry a user and
# a password, because those need a scheme to sit after.
BARE_HOST = re.compile(r"^([\w.\-]+(?::\d+)?)(?:[/?].*)?$")


def noteworthy(path: str) -> bool:
    """Whether this file's *facts* belong in the map, even though it is not code."""
    name = str(path).rsplit("/", 1)[-1]
    if name.casefold() in CONFIG_NAMES:
        return True
    folded = name.casefold()
    suffix = "." + folded.rsplit(".", 1)[-1] if "." in folded else ""
    return folded.startswith(CONFIG_PREFIXES) and suffix in CONFIG_SUFFIXES


def _shorten(label: str, value: str) -> tuple[str, str] | None:
    value = re.sub(r"\s+", " ", str(value)).strip()
    if not value or CREDENTIAL_KEY.search(label):
        return None
    if len(value) > MAX_FACT_CHARS:
        # A dependency list cut mid-token leaves a name the repository does not contain, and the model
        # goes looking for it. Prefer the last whole entry, and say that the list continues. The marker
        # is inside the cap, not after it: `render()` prints these verbatim, and a fact that ran four
        # characters long would be the one thing in the map to exceed its own limit.
        room = MAX_FACT_CHARS - 5
        if (cut := value.rfind(", ", 0, room)) > 0:
            return (label, value[:cut] + ", ...")
        return (label, value[:room].rstrip() + " ...")
    return (label, value)


POM_JAVA_KEYS = ("java.version", "maven.compiler.release", "maven.compiler.source",
                 "maven.compiler.target")


def _pom_java(root) -> str:
    """The Java version a POM declares, whichever of its four spellings it used.

    Called after the namespace pass, so the tags are plain names here. A `${…}` is a pointer to
    another property rather than an answer, so only a literal counts.
    """
    holder = root.find("properties")
    for key in POM_JAVA_KEYS:
        node = None if holder is None else holder.find(key)
        value = (node.text or "").strip() if node is not None else ""
        if value and not value.startswith("${"):
            return value[:20]
    return ""


def _pom(source: str) -> list[tuple[str, str]]:
    """Maven's own answers: what this module is, what it builds, and what it needs.

    A `<!DOCTYPE` is refused outright rather than parsed. Python's ElementTree resolves no external
    entities, but it still expands an internal DTD, and this file comes from a repository the tool was
    asked to read, not from a build system it trusts — a few kilobytes of nested entities would cost
    the whole task. Real POMs carry no DOCTYPE, so nothing is lost by the rule; `engine` refuses the
    same shape when it checks a POM it is about to write.
    """
    import xml.etree.ElementTree as ET

    if re.search(r"<!\s*DOCTYPE", source, re.I):
        return []
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        return []
    # Namespaces are removed from the parsed tree instead of from the text. Stripping the `xmlns…=`
    # attributes with a regex was the first attempt and it failed on every real POM: a POM binds
    # `xmlns:xsi` only to declare `xsi:schemaLocation`, so deleting the declaration leaves the
    # attribute's prefix unbound and the parse dies before it starts.
    for node in root.iter():
        if isinstance(node.tag, str) and "}" in node.tag:
            node.tag = node.tag.rsplit("}", 1)[-1]

    def text(tag: str, node=None):
        node = root if node is None else node
        found = node.find(tag)
        return found.text if found is not None and found.text else ""

    facts = []
    parent_artifact = parent_version = ""
    if (owner := root.find("parent")) is not None:
        if named := _shorten("parent", text("artifactId", owner)):
            facts.append(named)
        parent_artifact, parent_version = text("artifactId", owner), text("version", owner)
    if named := _shorten("artifact", text("artifactId")):
        facts.append(named)
    # The two versions that decide what code compiles, taken from the build file rather than inferred
    # from the source: a 2.x project still on `javax.` imports and a 3.x one on `jakarta.` look almost
    # alike to a model that was never told which is which.
    if parent_artifact.startswith("spring-boot") and parent_version:
        facts.append(("spring boot", parent_version[:20]))
    if java := _pom_java(root):
        facts.append(("java", java))
    modules = [str(item.text).strip() for item in root.findall("modules/module")
               if item.text and str(item.text).strip()]
    if modules:
        facts.append(("modules", ", ".join(modules[:12])))
    deps = sorted({str(item.text).strip() for item in root.findall("dependencies/dependency/artifactId")
                   if item.text and str(item.text).strip()})
    if deps:
        facts.append(("needs", ", ".join(deps[:12])))
    return [fact for fact in (_shorten(label, value) for label, value in facts) if fact][:MAX_FACTS]


GRADLE_DEP = re.compile(r"""(?:implementation|api|testImplementation|compileOnly|runtimeOnly|kapt|
                             annotationProcessor|classpath)\s*\(?\s*["']([\w.\-]+:[\w.\-]+)""", re.X)
GRADLE_INCLUDE = re.compile(r"""include\s+[\s,]*(['"]([\w.\-:]+)['"](?:\s*,\s*['"]([\w.\-:]+)['"])*)""")
# A module's own siblings. `implementation project(':common-lib')` is the internal edge the map exists
# to show, and it is spelled with no group and no version, so the coordinate pattern above cannot see it.
GRADLE_PROJECT = re.compile(r"""project\s*\(\s*['"]:?([\w.\-]+)['"]""")
PACKAGE_NAME = re.compile(r"""^\s*(?:(?:const|let|var)\s+)?rootProject\.name\s*=\s*['"]([\w.\-]+)""",
                          re.M)
# Gradle has no one place to say which Java it builds with, so all three spellings are read: the
# toolchain block modern projects use, the `sourceCompatibility` line older ones kept, and the
# `JavaVersion.VERSION_1_8` enum whose underscore is a decimal point.
GRADLE_TOOLCHAIN = re.compile(r"""JavaLanguageVersion\.of\(\s*(\d+)""")
GRADLE_COMPAT = re.compile(r"""(?:source|target)Compatibility\s*=\s*(?:JavaVersion\.VERSION_)?
                             ['"]?([0-9][0-9_.]*)""", re.X)
GRADLE_BOOT = re.compile(r"""org\.springframework\.boot(?:\.gradle\.plugin)?['")\s]+version\s+
                          ['"]([\d][\w.\-]*)""", re.X)
GRADLE_BOOT_COORD = re.compile(r"""spring-boot-starter-parent[:@]([\d][\w.\-]*)""")


def _gradle_java(source: str) -> str:
    for pattern in (GRADLE_TOOLCHAIN, GRADLE_COMPAT):
        if (match := pattern.search(source)):
            return match.group(1).strip("_.").replace("_", ".")[:20]
    return ""


def _gradle_boot(source: str) -> str:
    for pattern in (GRADLE_BOOT, GRADLE_BOOT_COORD):
        if (match := pattern.search(source)):
            return match.group(1)[:20]
    return ""


def _gradle(source: str) -> list[tuple[str, str]]:
    facts = []
    if named := PACKAGE_NAME.search(source):
        facts.append(("artifact", named.group(1)))
    if boot := _gradle_boot(source):
        facts.append(("spring boot", boot))
    if java := _gradle_java(source):
        facts.append(("java", java))
    includes = set()
    for match in GRADLE_INCLUDE.finditer(source):
        for group in match.groups():
            for piece in (group or "").split(","):
                piece = piece.strip().strip("'\"").strip(":")
                if piece:
                    includes.add(piece)
    if includes:
        facts.append(("modules", ", ".join(sorted(includes)[:12])))
    deps = sorted({match.group(1).split(":")[-2] if match.group(1).count(":") >= 2
                   else match.group(1).split(":")[-1] for match in GRADLE_DEP.finditer(source)}
                  | {match.group(1) for match in GRADLE_PROJECT.finditer(source)})
    if deps:
        facts.append(("needs", ", ".join(deps[:12])))
    return [fact for fact in (_shorten(label, value) for label, value in facts) if fact][:MAX_FACTS]


def _package_json(source: str) -> list[tuple[str, str]]:
    import json

    try:
        data = json.loads(source)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    facts = []
    if isinstance(data.get("name"), str):
        facts.append(("artifact", data["name"]))
    scripts = [key for key in ("build", "test", "start", "lint") if key in (data.get("scripts") or {})]
    if scripts:
        facts.append(("scripts", ", ".join(scripts)))
    for bucket in ("dependencies", "devDependencies"):
        keys = data.get(bucket)
        if isinstance(keys, dict) and keys:
            facts.append((("needs" if bucket == "dependencies" else "dev-needs"),
                          ", ".join(sorted(keys)[:12])))
    return [fact for fact in (_shorten(label, value) for label, value in facts) if fact][:MAX_FACTS]


GO_MODULE = re.compile(r"^\s*module\s+(\S+)", re.M)
GO_REQUIRE = re.compile(r"^\s*(?:require|use)\s+(\S+)")
GO_BLOCK_ITEM = re.compile(r"^\s*(\S+)\s+\S")


def _go_mod(source: str) -> list[tuple[str, str]]:
    """The module this is and the modules it needs, in both of go.mod's two spellings.

    A `require x v1` line and a `require ( x v1 )` block are the same statement written two ways, and
    the block is the usual way for anything with more than a couple of dependencies -- reading only the
    line form left a real project's manifest claiming it needed nothing.
    """
    facts = []
    if named := GO_MODULE.search(source):
        facts.append(("artifact", named.group(1)))
    needs, block = set(), False
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        if block:
            if stripped.startswith(")"):
                block = False
                continue
            if (item := GO_BLOCK_ITEM.match(stripped)):
                needs.add(item.group(1))
            continue
        match = GO_REQUIRE.match(line)
        if not match:
            continue
        if match.group(1) == "(":
            block = True
            continue
        needs.add(match.group(1))
    keep = sorted(item for item in needs if "." in item)      # a host in front of the path, not stdlib
    if keep:
        facts.append(("needs", ", ".join(keep[:12])))
    return [fact for fact in (_shorten(label, value) for label, value in facts) if fact][:MAX_FACTS]


def _toml(source: str) -> list[tuple[str, str]]:
    """Both TOML manifests this tool meets: PEP 621's `[project]` and Cargo's `[package]`.

    They disagree in shape as well as in section name -- Python lists dependencies, Rust keys them --
    so each half is read if it is there and the file simply has fewer facts if it is not.
    """
    import tomllib

    try:
        data = tomllib.loads(source)
    except (ValueError, tomllib.TOMLDecodeError):
        return []
    tables = [data.get(key) for key in ("project", "package")
              if isinstance(data.get(key), dict)]
    facts = []
    named = next((str(table["name"]) for table in tables if isinstance(table.get("name"), str)), "")
    if named:
        facts.append(("artifact", named))
    needs: list[str] = []
    for table in tables:
        found = table.get("dependencies")
        if isinstance(found, dict):
            needs.extend(found.keys())
        elif isinstance(found, list):
            needs.extend(str(item) for item in found)
    if isinstance(data.get("dependencies"), dict):
        needs.extend(data["dependencies"].keys())
    if needs:
        names = sorted({str(item).split(";")[0].strip().split("[")[0].split("=")[0].split(">")[0]
                        .split("<")[0].split("!")[0].strip() for item in needs if str(item).strip()})
        names = [item for item in names if item]
        if names:
            facts.append(("needs", ", ".join(names[:12])))
    return [fact for fact in (_shorten(label, value) for label, value in facts) if fact][:MAX_FACTS]


# The separator is required and the value is not, because a parent line (`application:`) carries
# nothing and still owns the keys under it -- without it in the stack every child resolves against the
# last *valued* key, and `spring.application.name` arrives as `port.name`. `=` is accepted too: a
# `.properties` file writes `server.port=8080`, its keys are already dotted and all sit at indent zero,
# so one scan reads both dialects.
YAML_PAIR = re.compile(r"^\s*([A-Za-z][\w.\-]*)\s*[=:](?:\s*(\S.*?))?\s*$")


def _runtime_yml(source: str) -> list[tuple[str, str]]:
    """A port, an application name, and the host a service registers with.

    Line-oriented on purpose: YAML has no stdlib parser here, and the keys worth showing are the ones
    that appear on their own line in every Spring Boot file this tool has been pointed at. A value is
    only kept when its whole dotted key is on the allow-list, and a URL is reduced to its authority,
    so the credential that fits inside the connection string never reaches the map.
    """
    facts = {}
    stack: list[tuple[int, str]] = []
    for line in source.splitlines():
        match = YAML_PAIR.match(line)
        if not match:
            continue
        key = match.group(1)
        indent = len(line) - len(line.lstrip(" "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        # An inline comment is not part of the value; YAML only starts one after a space, so a `#` inside
        # a URL fragment survives.
        value = re.sub(r"\s+#.*$", "", (match.group(2) or "")).strip().strip("'\"")
        if not value:
            continue
        path = ".".join(part for _, part in stack).casefold()
        label = KEY_FACTS.get(path) or LEAF_FACTS.get(path.rsplit(".", 1)[-1])
        if not label or CREDENTIAL_KEY.search(path):
            continue
        if label in ("registry", "host"):
            host, plain = URL_HOST.match(value), BARE_HOST.match(value)
            kept = (host or plain).group(1) if (host or plain) else ""
            if kept:
                facts.setdefault(label, kept)
        elif len(value) <= 60:
            facts.setdefault(label, value)
    return [(label, value) for label, value in facts.items()][:MAX_FACTS]


def config_facts(path: str, source: str) -> list[tuple[str, str]]:
    """What a configuration file says about the project, with nothing secret in it.

    Failures are silent by design: an unparseable pom is a map without that line, not a task that
    cannot start. The values that survive are names and numbers — module, artifact, port, dependency —
    and any key whose *name* looks like a credential is dropped before it is considered, because the
    repository's own redaction cannot see a value that was never supposed to be in the prompt.
    """
    name = str(path).rsplit("/", 1)[-1].casefold()
    if name == "pom.xml":
        return _pom(source)
    if name.startswith("build.gradle") or name.startswith("settings.gradle"):
        return _gradle(source)
    if name == "package.json":
        return _package_json(source)
    if name == "go.mod":
        return _go_mod(source)
    if name in ("cargo.toml", "pyproject.toml"):
        return _toml(source)
    return _runtime_yml(source)


def indexable(path: str) -> bool:
    """Whether this file is worth reading at all for the map — checked before the read."""
    return suffix_of(path) in INDEXABLE


def parse(path: str, source: str) -> dict | None:
    """One file's declarations, or None when the suffix is not an indexable source."""
    suffix = suffix_of(path)
    if suffix not in INDEXABLE:
        return None
    if suffix == ".py":
        # A model-written file that does not parse yet is exactly when the map matters most,
        # so the old lexical scan covers for the parser instead of the file going blank.
        return _python(path, source) or fallback(path, source)
    if suffix in SCRIPT_SUFFIXES:
        return _script(path, source)
    if suffix == ".go":
        return _go(path, source)
    if suffix == ".rs":
        return _rust(path, source)
    return _jvm(path, source)


def module_name(path: str) -> str:
    parts = [part for part in re.split(r"[/\\.]", path.rsplit(".", 1)[0]) if part]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _keys(row: dict) -> dict[str, str]:
    """Every dotted name another file could import to reach this one."""
    module = module_name(row["path"])
    names = {module}
    if row["kind"] == "go":
        # Go is imported by folder, so the package directory addresses every file in it.
        parts = module.split(".")
        if len(parts) > 1:
            names.add(".".join(parts[:-1]))
    if row["kind"] in NAMED_KINDS:
        for item in row["types"]:
            simple = item["name"].split(".")[0]
            names.add(simple)
            if row["package"]:
                names.add(row["package"] + "." + simple)
    return {name: row["path"] for name in names}


def _linked(imported: str, key: str) -> bool:
    """True when either name is the whole tail of the other, or the other's prefix.

    Both sides are split on `.` and `/`, because a Go or JavaScript import is a path while
    the indexed module is a dotted name. A Java file lives under `src/main/java/...`, so the
    import is shorter than the indexed module by exactly that prefix; a Python or Rust
    `…::name` import is longer by the trailing symbol, which is the mirror case and only
    matches as a prefix.
    """
    left = [part for part in re.split(r"[./\\]", imported) if part]
    right = [part for part in re.split(r"[./\\]", key) if part]
    shared = min(len(left), len(right))
    if shared and left[-shared:] == right[-shared:]:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return bool(shorter) and len(shorter) < len(longer) and longer[:len(shorter)] == shorter


def dependencies(rows: list[dict]) -> dict[str, list[str]]:
    """Project-internal files each indexed file imports.

    Only edges inside the repository are kept: a model cannot act on a JDK or stdlib
    import, and listing those would crowd out the one that matters.
    """
    owners: dict[str, str] = {}
    for row in rows:
        for name, path in _keys(row).items():
            owners.setdefault(name, path)
    edges: dict[str, list[str]] = {}
    for row in rows:
        targets = set()
        for imported in row["imports"]:
            dotted = imported.lstrip(".")
            if imported.startswith("."):
                level = len(imported) - len(dotted)
                parts = module_name(row["path"]).split(".")[:-level]
                dotted = ".".join(parts + ([dotted] if dotted else []))
            for key, path in owners.items():
                if path != row["path"] and _linked(dotted, key):
                    targets.add(path)
        edges[row["path"]] = sorted(targets)[:12]
    return edges


def module_of(relative: str) -> str:
    """The folder a file belongs to at the level a monorepo is divided — `auth-service` in
    `auth-service/src/main/java/App.java`, and "." for a file loose at the root."""
    parts = [part for part in str(relative).replace("\\", "/").split("/") if part not in ("", ".")]
    return parts[0] if len(parts) > 1 else "."


def spread(files: list[str]) -> list[str]:
    """Round-robin the file list across modules, so a capped map shows every one of them.

    Alphabetical order and a 12 000-char budget mean one thing in a nine-module reactor: the first
    three modules are described in detail and the other six are not mentioned at all, and the model
    is asked to plan a cross-module change from that. Within a module the order is unchanged.
    """
    groups: dict[str, list[str]] = {}
    for name in files:
        groups.setdefault(module_of(name), []).append(name)
    ordered: list[str] = []
    depth = 0
    while any(len(items) > depth for items in groups.values()):
        for key in sorted(groups):
            if len(groups[key]) > depth:
                ordered.append(groups[key][depth])
        depth += 1
    return ordered


MAX_GRAPH_NODES = 24        # modules on screen at once
MAX_GRAPH_EDGES = 80        # lines between them
MAX_GRAPH_DEPTH = 8         # columns; a cycle is clamped here rather than followed forever


def graph(rows: list[dict]) -> dict:
    """The project as modules and the dependencies between them, in columns by build order.

    Files are the wrong unit for a picture: a reactor of a thousand files drawn as a thousand nodes is
    a picture of nothing, and `module_of()` already names the folders a person thinks in. Edges carry a
    count so a thick line is visibly a heavier dependency, and a node's column is the longest chain that
    must be built before it -- the same order the run command starts the projects in.

    A cycle has no longest path. The layering peels what it can, breaks the remainder at one named node,
    and says `cyclic` in the result: a diagram that quietly re-ordered itself would be a diagram of a
    project that does not exist, and a real cycle between two Maven modules is exactly the thing a
    reader looks at a graph to find.
    """
    files: dict[str, int] = {}
    for row in rows or []:
        if row.get("kind") == "config":
            continue                      # a pom is a fact about a module, not a dependency of one
        name = module_of(row["path"])
        files[name] = files.get(name, 0) + 1

    counts: dict[tuple[str, str], int] = {}
    for origin, targets in dependencies(rows or []).items():
        source = module_of(origin)
        for target in targets:
            dest = module_of(target)
            if dest != source:
                counts[(source, dest)] = counts.get((source, dest), 0) + 1

    # The heaviest modules are drawn; the rest are counted. A cap that dropped nodes in silence would
    # turn a missing box into a claim that the module depends on nothing.
    keep = sorted(files, key=lambda name: (-files[name], name))[:MAX_GRAPH_NODES]
    live = set(keep)
    edges = sorted(((pair, count) for pair, count in counts.items()
                    if pair[0] in live and pair[1] in live),
                   key=lambda item: (-item[1], item[0]))[:MAX_GRAPH_EDGES]
    hidden = len(files) - len(live)

    depends: dict[str, list[str]] = {}
    names: set[str] = set(live)
    for (source, dest), _count in edges:
        depends.setdefault(source, []).append(dest)
        names.update((source, dest))

    # A node's column is one past the deepest thing it needs, so the columns read as a build order.
    # Nodes are placed by peeling: everything with no unplaced dependency is at the front, and a graph
    # where nothing is ready is a cycle -- broken at a fixed node, named in the result, because a
    # diagram that silently re-ordered itself is a diagram of a project that does not exist.
    placed: dict[str, int] = {}
    remaining = set(names)
    cyclic = False
    while remaining:
        ready = sorted(name for name in remaining
                       if not set(depends.get(name, [])) & remaining)
        if not ready:
            cyclic = True
            ready = [sorted(remaining, key=lambda item: (
                -len(set(depends.get(item, [])) - remaining), item))[0]]
        for name in ready:
            # Only dependencies already placed count. Peeling guarantees that in an acyclic graph, and it
            # is what makes the layering a build order; in the node chosen to break a cycle the unplaced
            # dependency is the loop itself, and counting it would shift every column right by one to
            # draw an empty first one.
            deepest = max((placed[dep] for dep in depends.get(name, []) if dep in placed), default=-1)
            placed[name] = min(MAX_GRAPH_DEPTH, deepest + 1)
            remaining.discard(name)

    return {"nodes": [{"name": name, "files": files.get(name, 0), "column": placed.get(name, 0)}
                      for name in sorted(names, key=lambda item: (placed.get(item, 0), item))],
            "edges": [{"from": source, "to": dest, "count": count}
                      for (source, dest), count in edges],
            "columns": max(placed.values(), default=0) + 1,
            "cyclic": cyclic,
            "hidden": max(0, hidden)}


MAX_HITS = 40               # reference sites or symbol matches one query may return
MAX_SITE_TEXT = 200         # characters of the line itself
PER_FILE_LIMIT = 6          # sites from one file, so a 40-hit answer is not one file's grep

# The line kinds a reference can be. The distinction is the whole value of the verb: `search_code`
# already tells a model that a name appears 30 times, and what it cannot tell is which of those 30 is
# the declaration, which is an import, and which is somebody calling it.
IMPORT_LINE = re.compile(r"^\s*(?:from\s+[\w.]+\s+)?import\b|^\s*(?:use|using)\s|#include|^\s*require\s*\(",
                         re.I)
DECLARE_LINE = re.compile(
    r"^\s*(?:@[\w.]+\s+)*(?:public|private|protected|internal|static|final|abstract|override|open|"
    r"sealed|class|interface|enum|record|annotation|def|func|fn|type|impl|export|async|const|let|var|"
    r"pub|local|friend|virtual|override)\b", re.I)


def _member_name(signature: str) -> str:
    """`login(String email)` out of a members list, which stores signatures not names."""
    return signature.split("(", 1)[0].strip()


def find_symbol(rows: list[dict], name: str, limit: int = MAX_HITS) -> list[dict]:
    """Every declaration in the index whose name is this one.

    Types come with the line the index recorded; functions and members do not, because the parsers keep
    signatures and not one line number per member — a hit without a `line` is honest about that, and
    `read_file` on the path is the next step rather than a guessed one.
    """
    needle = str(name or "").strip()
    if not needle:
        return []
    folded = needle.casefold()
    exact: list[dict] = []
    partial: list[dict] = []

    def collect(entry: dict, whole: bool) -> None:
        (exact if whole else partial).append(entry)

    for row in rows:
        for item in row["types"]:
            last = item["name"].rsplit(".", 1)[-1]
            if folded not in last.casefold():
                continue
            entry = {"name": item["name"], "kind": item["kind"], "path": row["path"],
                     "package": row["package"], "line": item["line"]}
            if item.get("extends"):
                entry["extends"] = ", ".join(item["extends"])
            collect(entry, last.casefold() == folded)
        for signature in row["functions"]:
            member = _member_name(signature)
            if folded not in member.casefold():
                continue
            collect({"name": member, "kind": "function", "path": row["path"],
                     "package": row["package"], "signature": signature}, member.casefold() == folded)
        for item in row["types"]:
            for signature in item["members"]:
                member = _member_name(signature)
                if folded not in member.casefold():
                    continue
                collect({"name": member, "kind": "member", "path": row["path"],
                         "package": row["package"], "in": item["name"], "signature": signature},
                        member.casefold() == folded)
    hits = exact + partial
    return hits[:limit]


def find_references(name: str, sources, rows: list[dict] | None = None,
                    limit: int = MAX_HITS, per_file: int = PER_FILE_LIMIT) -> list[dict]:
    """Where a name is used, and in what role — the reverse of the forward-only `dependencies()`.

    `sources` is any iterable of `(path, text)` pairs, so the caller decides what to pay for: reading
    the workspace is the same cost as a search, and `symbols` stays free of a workspace import that
    would close a cycle. Text is matched in `blank()`ed source, which keeps every line where it was and
    removes comments and string contents — without it a log message naming a class is reported as a
    use of it, which is the exact mistake the parsers already had to be taught not to make.

    The role is the answer, not the count: `declaration` (the index says this line declares it, or the
    line opens with a keyword that only a declaration uses), then `import`, then `call`, then
    `mention`. There is no type inference here and the verb does not claim otherwise — an overloaded
    method returns every site of that name, and `read_file` on the two that matter is still cheaper
    than a model guessing which file to open.
    """
    needle = str(name or "").strip()
    if not needle:
        return []
    declared = {(hit["path"], hit.get("line")) for hit in find_symbol(rows or [], needle, limit=500)}
    site = re.compile(r"(?<!\w)" + re.escape(needle) + r"(?!\w)")
    call = re.compile(r"(?<!\w)" + re.escape(needle) + r"\s*\(")
    out: list[dict] = []
    for path, text in sources:
        if len(out) >= limit:
            break
        clean = blank(text, backticks=path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")))
        counted = 0
        for number, (raw, bare) in enumerate(zip(text.splitlines(), clean.splitlines()), 1):
            if not site.search(bare):
                continue
            if (path, number) in declared:
                kind = "declaration"
            elif IMPORT_LINE.match(bare):
                kind = "import"
            elif DECLARE_LINE.match(bare) and site.search(bare):
                kind = "declaration"
            elif call.search(bare):
                kind = "call"
            else:
                kind = "mention"
            out.append({"path": path, "line": number, "kind": kind,
                        "text": raw.strip()[:MAX_SITE_TEXT]})
            counted += 1
            if counted >= per_file or len(out) >= limit:
                break
    return out


# Words a task says that name nothing. Deliberately tiny: this is not a search engine, and a list that
# grows past the pronouns starts hiding the noun somebody meant. `add` was in here once and should not
# have been -- it is one of the most common names in code (`add(a, b)`), so a task that says "fix add"
# and means the function got nothing.
STOP_WORDS = {"the", "and", "for", "with", "that", "this", "from", "into", "your", "please", "make",
              "change", "fix", "file", "files", "code", "class", "method", "function", "test",
              "tests", "when", "then", "them", "it", "to", "of", "in", "on", "is", "are", "do", "so",
              "java", "py", "xml", "yml", "yaml", "json", "sql", "md", "gradle", "pom"}
WORD = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{2,}")
CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]+|[a-z0-9]+")


def task_words(task: str) -> set[str]:
    """The names inside a sentence, including the parts of a camel-case or snake-case name.

    "Wire the token provider into the login flow" has to reach `JwtTokenProvider.login`. The whole word
    and each of its parts are both offered because a person writes one and the source is spelled the
    other, and neither alone finds the pair.
    """
    words: set[str] = set()
    for raw in WORD.findall(str(task or "")):
        folded = raw.casefold()
        if folded in STOP_WORDS:
            continue
        words.add(folded)
        for piece in CAMEL.findall(raw):
            piece = piece.casefold()
            if len(piece) > 2 and piece not in STOP_WORDS:
                words.add(piece)
    return words


def _name_parts(name: str) -> list[str]:
    return [piece.casefold() for piece in CAMEL.findall(re.split(r"[(\s]", name)[0])]


def rank(rows: list[dict], task: str, limit: int = 6) -> list[dict]:
    """Which files the task is about, and the one-line reason each was picked.

    The rule before this function was "keep a file whose exact name appears in the sentence", which
    works when a person types `JwtTokenProvider.java` and does nothing when they describe the bug. This
    scores the index instead: a declared name the task says, a part of such a name, the folder the task
    names, and one hop along an import from any of those. It is still not understanding — it is a ranked
    guess — which is why every entry carries the reason and the window prints it: an unexplained context
    block is a claim the tool cannot defend when the wrong file turns up in the diff.
    """
    words = task_words(task)
    if not words:
        return []
    edges = dependencies(rows)
    reverse: dict[str, list[str]] = {}
    for importer, imported in edges.items():
        for target in imported:
            reverse.setdefault(target, []).append(importer)
    scores: dict[str, dict] = {}

    def offer(path: str, score: int, why: str, symbol: str = "") -> None:
        best = scores.get(path)
        if best is None or score > best["score"]:
            scores[path] = {"path": path, "score": score, "why": why, "symbol": symbol}
        elif score == best["score"] and not best["symbol"] and symbol:
            best["symbol"] = symbol

    for row in rows:
        path = row["path"]
        # Rows carry forward-slash relative paths, which is what `module_of` splits on too, so the
        # basename is a string operation here rather than a pathlib import this module never needed.
        basename = path.rsplit("/", 1)[-1]
        # The boundary test is the old rule kept verbatim: `app.py` in the sentence names `app.py`, and
        # one written inside a longer word does not. Weakening it here would spend context on a file
        # nobody asked for, which is the one thing this function is allowed to cost.
        if re.search(r"(?<![\w.])" + re.escape(basename) + r"(?![\w.])", str(task or ""), re.I):
            offer(path, 10, "names", basename)
        for item in row["types"]:
            name = item["name"].rsplit(".", 1)[-1]
            whole = name.casefold()
            parts = _name_parts(name)
            if whole in words:
                offer(path, 8, "declares", name)
            elif (hit := next((word for word in parts if word in words), "")):
                offer(path, 4, "declares", name)
            for signature in item["members"]:
                member = _member_name(signature)
                if member.casefold() in words:
                    offer(path, 7, "declares", member)
                elif (hit := next((part for part in _name_parts(member) if part in words), "")):
                    offer(path, 3, "defines", member)
        for signature in row["functions"]:
            member = _member_name(signature)
            if member.casefold() in words:
                offer(path, 7, "defines", member)
            elif (hit := next((part for part in _name_parts(member) if part in words), "")):
                offer(path, 3, "defines", member)
        folder = module_of(path)
        # A module folder is a name, spelled the way a class is: the task says "auth-service" and
        # `task_words` splits it in two exactly as it splits a camel-case class. Comparing only the
        # whole folder meant that every hyphenated module -- which is how a Spring reactor writes
        # itself -- ranked nothing at all.
        if folder != "." and (hit := next((word for word in [folder.casefold()] + _name_parts(folder)
                                           if word in words), "")):
            offer(path, 4, "module", hit)
        # The route the task named, now that the map knows the routes: a person who says "the login
        # endpoint" and never types a class name still means one file, and the handler is the answer
        # the index can give without reading it.
        for route in row.get("endpoints") or []:
            if score := names_route(task, words, route):
                offer(path, score, "route", route["verb"] + " " + route["path"])

    for path, entry in list(scores.items()):
        if entry["score"] < 4:
            continue
        for other in reverse.get(path, []):
            offer(other, min(5, entry["score"] - 2), "imports",
                  entry["symbol"] or path.rsplit("/", 1)[-1])
        # The other half of one hop. A person who names a service means the repository it calls as
        # often as the controller that calls it, and the single direction found only the second — the
        # collaborator was the file the fix needed and the map had never offered. Named by the seed's
        # file rather than its symbol, because "used by UserService.java" is the sentence that says
        # which relationship pulled this one in.
        for other in edges.get(path, []):
            offer(other, min(5, entry["score"] - 2), "used_by", path.rsplit("/", 1)[-1])

    ordered = sorted(scores.values(), key=lambda entry: (-entry["score"], entry["path"]))
    return ordered[:limit]


# A URL a person types is the strongest name of a file there is, and until the map carried routes the
# ranker could not use one. These segments appear in thousands of endpoints and identify nothing, so a
# path made only of them is matched by its literal text or not at all.
GENERIC_ROUTE = {"api", "rest", "v1", "v2", "v3", "http", "https", "web", "internal"}


def names_route(task: str, words: set[str], route: dict) -> int:
    """0 when the sentence is not about this URL, 8 when it says the path, 5 when it says part of it.

    The two strengths are the whole point. A person who types `/api/v1/orders/{id}/pay` has named the
    file, and that ranks like a filename. A person who says "the pay endpoint" or "orders" means one
    segment of a URL, and matching on one segment is a guess — so it ranks like the other guesses, at
    the weight of an import edge or a folder name, with the URL it matched on kept in the reason so the
    window can show *why* this file came to be in the prompt.
    """
    path = str(route.get("path", ""))
    if path and path in str(task or ""):
        return 8
    parts = [piece for piece in path.split("/")
             if len(piece) >= 3 and not piece.startswith("{")
             and piece.casefold() not in GENERIC_ROUTE]
    for piece in parts:
        folded = piece.casefold()
        # A URL segment is usually plural and the sentence is usually not, and neither form on its own
        # finds the pair.
        if any(word == folded or word.startswith(folded) or folded.startswith(word)
               for word in words):
            return 5
    return 0


SNIPPET_LINES = 40        # of a file too large for what is left of the retrieval budget


def snippet(source: str, symbol: str, cap_lines: int = SNIPPET_LINES) -> tuple[str, int, bool]:
    """The block around one declaration, for a file that will not fit in the budget whole.

    Answers `(text, first line, more lines follow)`. The window opens at the first line that *declares*
    the name in blanked text, so a comment or a log message naming it cannot choose what is shown. It
    closes at the next declaration at the same indent or shallower — which is the end of a method body
    for every method but the last one in the file — because finding where a brace closes needs the
    parser, and this is reading a file the parser was pointed at by name, not re-parsing it.
    """
    needle = str(symbol or "").strip()
    if not needle:
        return "", 0, False
    site = re.compile(r"(?<!\w)" + re.escape(needle.split("(", 1)[0].rsplit(".", 1)[-1]) + r"(?!\w)")
    lines = source.splitlines()
    blanked = blank(source).splitlines()
    start = next((number for number, bare in enumerate(blanked, 1) if site.search(bare)), 0)
    if not start:
        return "", 0, False
    first = lines[start - 1]
    indent = len(first) - len(first.lstrip())
    body = [first]
    for offset in range(start, min(len(lines), start - 1 + cap_lines)):
        bare = blanked[offset] if offset < len(blanked) else ""
        depth = len(lines[offset]) - len(lines[offset].lstrip())
        if depth <= indent and DECLARE_LINE.match(bare):
            break
        body.append(lines[offset])
    return "\n".join(body), start, len(body) < len(lines) - (start - 1)


def config_row(path: str, source: str) -> dict | None:
    """The map's entry for a configuration file: facts, not declarations.

    It is a row so `render()` prints it in the same block position as a source file, and it is a row
    with no types and no functions so every name-based query in this module passes over it. A model
    that learns `modules: auth-service, product-service` from a pom must not then be told the pom
    "declares" auth-service — that is the confusion this split keeps out.
    """
    facts = config_facts(path, source)
    if not facts:
        return None
    return {"path": path, "kind": "config", "package": "", "imports": [], "types": [],
            "functions": [], "facts": facts}


def render(rows: list[dict], files: list[str], limit: int = 12000, spread_files: bool = False,
           note: str = "") -> str:
    """The repository map: every visible file, with its declarations under it.

    `note` goes on the first line, not the last: it says what the map does *not* contain, and a sentence
    about missing files that is itself truncated off the bottom would be a worse answer than none.
    """
    indexed = {row["path"]: row for row in rows}
    edges = dependencies(rows)
    if spread_files:
        files = spread(files)
    out: list[str] = []
    if note:
        out.append(note)
    used = len(note) + 1 if note else 0
    shown = 0
    for name in files:
        row = indexed.get(name)
        block = [name]
        if row:
            # A configuration file's own lines: what it is, what it builds, what it needs. Printed under
            # the path like a package line, because that is the position the model already reads
            # structure from — a fact in prose elsewhere in the prompt is a fact it has to re-find.
            for label, value in row.get("facts") or []:
                block.append("  " + label + ": " + value)
            if row["package"]:
                block.append("  package " + row["package"])
            for item in row["types"]:
                text = "  " + item["kind"] + " " + item["name"]
                if item.get("extends"):
                    text += " extends " + ", ".join(item["extends"])
                if item["members"]:
                    text += ": " + ", ".join(item["members"][:8])
                    if len(item["members"]) > 8:
                        text += " +" + str(len(item["members"]) - 8) + " more"
                block.append(text[:200])
            if row["functions"]:
                block.append("  functions: " + ", ".join(row["functions"][:8]) +
                             (" …" if len(row["functions"]) > 8 else ""))
            # The routes on their own lines, above the depends-on edge: "what answers POST /login" is
            # the question that decides whether a change is local or breaks a caller, and a map that
            # only names the class leaves the model to open the file to find out.
            routes = row.get("endpoints") or []
            for route in routes[:6]:
                block.append(("  route " + route["verb"] + " " + route["path"] + " \u2192 "
                              + route["handler"])[:200])
            if len(routes) > 6:
                # The ceiling is named when it was reached, because "+34 more" out of a list that was
                # cut at 40 is a different promise than "+34 more" out of the file's real 40.
                block.append("  routes +" + str(len(routes) - 6) + " more"
                             + (" (this map lists at most " + str(MAX_ENDPOINTS) + " per file)"
                                if row.get("routes_capped") else ""))
            if edges.get(name):
                block.append("  depends on: " + ", ".join(edges[name]))
        block.append("")
        text = "\n".join(block)
        if used + len(text) > limit:
            break
        out.append(text)
        used += len(text)
        shown += 1
    if shown < len(files):
        # Which modules were left out entirely, because "300 of 1 200 files" does not tell a reader
        # whether the missing ones are a detail or the half of the project they are asking about.
        missing = sorted({module_of(name) for name in files[shown:]}
                         - {module_of(name) for name in files[:shown]})
        out.append("(index truncated: " + str(shown) + " of " + str(len(files)) + " files listed"
                   + ("; nothing shown from " + ", ".join(missing[:6])
                      + (" …" if len(missing) > 6 else "") if missing else "")
                   + "; read or search the rest on demand)")
    return "\n".join(out).strip()
