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


def _jvm(path: str, source: str) -> dict:
    """Java/Kotlin declarations, found by a brace-depth scan of blanked text.

    A method counts only at one level inside a type body, which is what separates
    `public Token login(String p)` from `tokenService.login(password)` two lines later.
    """
    row = {"path": path, "kind": "jvm", "package": "", "imports": [], "types": [],
           "functions": []}
    depth = 0
    stack: list[tuple[str, int]] = []           # (type name, depth where it opened)
    for number, line in enumerate(blank(source).splitlines(), 1):
        head = line.strip()
        opened, closed = line.count("{"), line.count("}")
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
                _declare_type(row, simple, kind, number, outer)
                # The frame opens even when the cap refused the type: without it a method
                # inside has no owner at all and lands in the file's own function list.
                stack.append((name, depth))
        elif any(head.startswith(word) for word in LINE_STARTS):
            method = None
            if re.search(r"\bfun\s", line):
                method = KOTLIN_FUN_RE.match(line)
            method = method or JAVA_METHOD_RE.match(line) or JAVA_PLAIN_METHOD_RE.match(line)
            if method and method.group(2) not in CONTROL:
                params = [part for part in method.group(3).split(",") if part.strip()][:6]
                if stack:
                    owner = next((item for item in reversed(row["types"])
                                  if item["name"] == stack[-1][0]), None)
                    if owner and depth == stack[-1][1] + 1 and len(owner["members"]) < MAX_MEMBERS:
                        owner["members"].append(_signature(method.group(2), params))
                elif len(row["functions"]) < MAX_MEMBERS:
                    # A Kotlin file-level function, which belongs to no type.
                    row["functions"].append(_signature(method.group(2), params))
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


def _declare_type(row: dict, name: str, kind: str, line: int, outer: str = "") -> dict | None:
    """The entry for a declared type, created once per name.

    `export class X` and a later `export { X }`, or a Go type with its methods spread over
    several files in the same folder, must not produce the same type twice.
    """
    full = outer + "." + name if outer else name
    for item in row["types"]:
        if item["name"] == full:
            return item
    if len(row["types"]) >= MAX_TYPES:
        return None
    item = {"name": full, "kind": kind, "line": line, "members": []}
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


def render(rows: list[dict], files: list[str], limit: int = 12000, spread_files: bool = False) -> str:
    """The repository map: every visible file, with its declarations under it."""
    indexed = {row["path"]: row for row in rows}
    edges = dependencies(rows)
    if spread_files:
        files = spread(files)
    out: list[str] = []
    used = 0
    shown = 0
    for name in files:
        row = indexed.get(name)
        block = [name]
        if row:
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
