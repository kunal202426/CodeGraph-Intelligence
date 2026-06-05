"""Cross-file symbol resolver — closes provisional import dst_ids to real entity_ids.

After T2.1, the graph contains import edges whose `dst_id` follows one of these
provisional encodings:

- `py:?:<module>.<name>`     — `from <module> import <name>` (absolute)
- `py:?:<module>`            — `import <module>` (the module itself)
- `py:?:<module>.*`          — `from <module> import *` (wildcard)
- `py:?rel<N>:<rest>`        — relative import, N leading dots

This pass rewrites them in-place:

- Resolved to a known entity_id:   `dst_id = py:<file>:<qualified_name>`, conf=1.0
- Resolved to a module entity:     `dst_id = py:<file>:<module_qname>`, conf=1.0
- Wildcard, module known:          `dst_id = wildcard:py:<file>`, conf=0.7
- Anything else (stdlib, 3rd-party, broken path): `dst_id = external:<dotted>`, conf=0.5

The resolver is idempotent: a second run is a no-op since nothing left starts
with `py:?`.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from codegraph.graph.store import GraphStore
from codegraph.uir import Edge

_REL_RE = re.compile(r"^py:\?rel(\d+):(.*)$")

# TS / JS module resolution candidates. Order matters: try .ts before .tsx
# before .js etc., then directory-style `index.*` fallbacks.
_TS_EXTENSIONS = (".ts", ".tsx", ".d.ts", ".js", ".mjs", ".cjs", ".jsx")
_TS_INDEX_NAMES = ("index.ts", "index.tsx", "index.js", "index.jsx")

# File extensions stripped when building the module-qname index.
_MODULE_EXT_ORDER = (
    ".tsx",
    ".ts",
    ".jsx",
    ".mjs",
    ".cjs",
    ".js",
    ".pyi",
    ".py",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".php",
    ".cpp",
    ".cc",
    ".cxx",
    ".hpp",
    ".hxx",
    ".h",
    ".c",
)

# SQL fragment selecting all provisional edge patterns.
_PROVISIONAL_WHERE = (
    "dst_id LIKE 'py:?%' OR dst_id LIKE 'ts:?%' "
    "OR dst_id LIKE 'go:?%' OR dst_id LIKE 'rs:?%' "
    "OR dst_id LIKE 'java:?%' OR dst_id LIKE 'rb:?%' "
    "OR dst_id LIKE 'php:?%' "
    "OR dst_id LIKE 'c:?%' OR dst_id LIKE 'cpp:?%'"
)

# Rust standard / core library namespace prefixes → always external.
_RUST_STDLIB_PREFIXES = (
    "std::",
    "core::",
    "alloc::",
    "proc_macro::",
    "test::",
)

# C / C++ system header names that are always external (no extension).
# Headers ending in .h are still probed against known_files first.
_C_SYSTEM_NOEXT = frozenset(
    {
        "string",
        "vector",
        "map",
        "set",
        "list",
        "array",
        "deque",
        "memory",
        "utility",
        "algorithm",
        "iostream",
        "fstream",
        "sstream",
        "functional",
        "type_traits",
        "chrono",
        "thread",
        "mutex",
        "atomic",
        "cassert",
        "cstdio",
        "cstdlib",
        "cstring",
        "cmath",
        "climits",
    }
)


@dataclass(frozen=True)
class ResolutionStats:
    """Summary returned by `resolve_symbols`."""

    inspected: int
    resolved: int
    external: int
    wildcard: int

    @property
    def unresolved(self) -> int:
        # "external" + "wildcard" both still point at something useful; the only
        # truly unresolved leftovers would be ones we couldn't even classify,
        # which we currently treat as external. Kept for log-message symmetry.
        return self.external + self.wildcard


def resolve_symbols(store: GraphStore) -> ResolutionStats:
    """Update unresolved import edges in the graph DB.

    Reads from `entities` + `files`, mutates `edges`. Returns counts.
    """
    idx = _build_indexes(store)

    rows = store.conn.execute(
        f"SELECT src_id, dst_id, type, line FROM edges WHERE {_PROVISIONAL_WHERE}"
    ).fetchall()

    # Calls are resolved after imports (they may reference imported names), so
    # partition the work. Both Python (py:?call:) and TS (ts:?call:, T4.2) use
    # the same suffix convention.
    call_rows = [r for r in rows if ":?call:" in r[1]]
    import_rows = [r for r in rows if ":?call:" not in r[1]]

    resolved = external = wildcard = 0
    resolved_edges: list[Edge] = []
    # file → {imported_name: resolved_target_id} — built from resolved imports,
    # used to resolve calls to imported symbols.
    imports_by_file: dict[str, dict[str, str]] = {}

    # Phase 1 — imports.
    for src_id, dst_id, edge_type, line in import_rows:
        new_dst, new_conf = _resolve_one(
            dst_id, src_id, idx.by_file_name, idx.by_module_qname, idx.known_files
        )
        resolved_edges.append(
            Edge(src_id=src_id, dst_id=new_dst, type=edge_type, line=line, confidence=new_conf)
        )
        if new_dst.startswith("wildcard:"):
            wildcard += 1
        elif new_dst.startswith("external:"):
            external += 1
        else:
            resolved += 1
            target_name = idx.name_by_id.get(new_dst)
            if target_name:
                file = src_id.split(":", 2)[1]
                imports_by_file.setdefault(file, {})[target_name] = new_dst

    # Phase 2 — calls (now that imports_by_file is populated).
    for src_id, dst_id, edge_type, line in call_rows:
        new_dst, new_conf = _resolve_call(dst_id, src_id, idx.entities_by_file, imports_by_file)
        resolved_edges.append(
            Edge(src_id=src_id, dst_id=new_dst, type=edge_type, line=line, confidence=new_conf)
        )
        if new_dst.startswith("external:"):
            external += 1
        else:
            resolved += 1

    if rows:
        # Two bulk statements instead of 2*N per-edge round-trips: drop every
        # provisional edge, then re-insert the resolved versions. INSERT OR
        # IGNORE dedupes when a resolved counterpart already exists (re-index).
        store.conn.execute(f"DELETE FROM edges WHERE {_PROVISIONAL_WHERE}")
        store.upsert_edges(resolved_edges)

    return ResolutionStats(
        inspected=len(rows),
        resolved=resolved,
        external=external,
        wildcard=wildcard,
    )


# ----------------------------------------------------------------------
# Indexes


@dataclass(frozen=True)
class _Indexes:
    by_file_name: dict[tuple[str, str], str]  # (file, name) → entity_id
    by_module_qname: dict[str, str]  # module qname → file path
    known_files: set[str]  # all indexed file paths (TS module probing)
    name_by_id: dict[str, str]  # entity_id → name (import-target naming)
    entities_by_file: dict[str, dict[str, str]]  # file → {name: entity_id}


def _build_indexes(store: GraphStore) -> _Indexes:
    by_file_name: dict[tuple[str, str], str] = {}
    name_by_id: dict[str, str] = {}
    entities_by_file: dict[str, dict[str, str]] = {}
    for entity_id, file, name, qname in store.conn.execute(
        "SELECT entity_id, file, name, qualified_name FROM entities"
    ).fetchall():
        by_file_name[(file, name)] = entity_id
        name_by_id[entity_id] = name
        fmap = entities_by_file.setdefault(file, {})
        # On a name collision within a file (e.g. two classes' `validate`
        # methods), prefer the top-level definition (qualified_name == name),
        # which is what a bare `validate()` call most likely targets.
        if name not in fmap or qname == name:
            fmap[name] = entity_id

    by_module_qname: dict[str, str] = {}
    known_files: set[str] = set()
    aliases: list[tuple[str, str]] = []  # (src-root-stripped qname, path)
    for (path,) in store.conn.execute("SELECT path FROM files").fetchall():
        known_files.add(path)
        qname = _path_to_module_qname(path)
        by_module_qname[qname] = path
        stripped = _strip_source_roots(qname)
        if stripped:
            aliases.append((stripped, path))
    # Register src-layout aliases after all full qnames, and never clobber a real
    # full qname (setdefault) — so `codegraph.x` resolves to `packages/codegraph/x.py`
    # without shadowing an actual top-level `codegraph/x.py`.
    for stripped, path in aliases:
        by_module_qname.setdefault(stripped, path)

    return _Indexes(
        by_file_name=by_file_name,
        by_module_qname=by_module_qname,
        known_files=known_files,
        name_by_id=name_by_id,
        entities_by_file=entities_by_file,
    )


def _resolve_call(
    dst_id: str,
    src_id: str,
    entities_by_file: dict[str, dict[str, str]],
    imports_by_file: dict[str, dict[str, str]],
) -> tuple[str, float]:
    """Resolve a `<lang>:?call:<callee>` edge.

    Order: a same-file entity named `<callee>` (conf 1.0) → a name the caller's
    file imports (conf 0.9) → external (conf 0.5). Method-call precision (typed
    receivers) is out of MVP scope; we match on the simple callee name.
    """
    _, _, callee = dst_id.partition(":?call:")
    parts = src_id.split(":", 2)
    file = parts[1] if len(parts) >= 3 else ""

    same_file = entities_by_file.get(file, {})
    if callee in same_file:
        return same_file[callee], 1.0

    imported = imports_by_file.get(file, {})
    if callee in imported:
        return imported[callee], 0.9

    return f"external:{callee}", 0.5


def _path_to_module_qname(path: str) -> str:
    """`src/auth/login.ts` → `src.auth.login`."""
    stem = path
    for ext in _MODULE_EXT_ORDER:
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem.replace("/", ".")


# Common source-root directory names. In a src-layout repo the importable package
# starts *below* one of these (e.g. `packages/codegraph/...` is imported as
# `codegraph...`). The file-derived module qname keeps the prefix, so without
# stripping it, every internal absolute import — and therefore every cross-module
# call — would fall through to `external:`, gutting impact/trace on real projects.
_SOURCE_ROOT_SEGMENTS = frozenset({"src", "packages", "lib", "app", "source"})


def _strip_source_roots(qname: str) -> str | None:
    """Drop leading source-root segments from a module qname.

    `packages.codegraph.graph.queries` → `codegraph.graph.queries`. Returns the
    stripped qname only if something was stripped (and a non-empty remainder
    survives), else ``None``.
    """
    parts = qname.split(".")
    i = 0
    while i < len(parts) - 1 and parts[i] in _SOURCE_ROOT_SEGMENTS:
        i += 1
    return ".".join(parts[i:]) if i > 0 else None


# ----------------------------------------------------------------------
# Resolution dispatch


def _resolve_one(
    dst_id: str,
    src_id: str,
    by_file_name: dict[tuple[str, str], str],
    by_module_qname: dict[str, str],
    known_files: set[str],
) -> tuple[str, float]:
    rel_match = _REL_RE.match(dst_id)
    if rel_match:
        depth = int(rel_match.group(1))
        rest = rel_match.group(2)
        return _resolve_relative(rest, depth, src_id, by_file_name, by_module_qname)

    if dst_id.startswith("py:?:"):
        return _resolve_absolute(dst_id[len("py:?:") :], by_file_name, by_module_qname)

    if dst_id.startswith("ts:?:"):
        return _resolve_typescript(dst_id[len("ts:?:") :], src_id, by_file_name, known_files)

    if dst_id.startswith("go:?:"):
        return _resolve_go(dst_id[5:], src_id, by_file_name, by_module_qname, known_files)

    if dst_id.startswith("rs:?:"):
        return _resolve_rust(dst_id[5:], src_id, by_file_name, by_module_qname, known_files)

    if dst_id.startswith("java:?:"):
        return _resolve_java(dst_id[7:], by_file_name, known_files)

    if dst_id.startswith("rb:?:"):
        return _resolve_ruby(dst_id[5:], src_id, by_file_name, known_files)

    if dst_id.startswith("php:?:"):
        return _resolve_php(dst_id[6:], src_id, by_file_name, known_files)

    if dst_id.startswith("c:?:"):
        return _resolve_c_include(dst_id[4:], src_id, by_file_name, known_files, "c")

    if dst_id.startswith("cpp:?:"):
        return _resolve_c_include(dst_id[6:], src_id, by_file_name, known_files, "cpp")

    # Already resolved (shouldn't reach here given the SQL filter, but defensive).
    return dst_id, 1.0


def _resolve_absolute(
    qname: str,
    by_file_name: dict[tuple[str, str], str],
    by_module_qname: dict[str, str],
) -> tuple[str, float]:
    # Wildcard: `from X import *`
    if qname.endswith(".*"):
        module = qname[:-2]
        file = by_module_qname.get(module)
        if file:
            return f"wildcard:py:{file}", 0.7
        return f"external:{qname}", 0.5

    # The whole path *is* a module (e.g. `import auth.login`).
    if qname in by_module_qname:
        file = by_module_qname[qname]
        # Reconstruct from the file's real module qname, not the lookup key: a
        # src-layout alias (`codegraph.graph.queries`) differs from the module
        # entity's actual qname (`packages.codegraph.graph.queries`).
        return f"py:{file}:{_path_to_module_qname(file)}", 1.0

    # Try splitting `module.name`.
    if "." in qname:
        module, name = qname.rsplit(".", 1)
        file = by_module_qname.get(module)
        if file is not None:
            hit = by_file_name.get((file, name))
            if hit is not None:
                return hit, 1.0
            # File exists in repo but the imported name isn't an indexed entity:
            # could be a constant, an `__all__` re-export, etc. Treat as external
            # with a slightly higher confidence than pure stdlib.
            return f"external:{qname}", 0.5

    # Nothing matched — stdlib or third-party.
    return f"external:{qname}", 0.5


def _resolve_relative(
    rest: str,
    depth: int,
    src_id: str,
    by_file_name: dict[tuple[str, str], str],
    by_module_qname: dict[str, str],
) -> tuple[str, float]:
    """Resolve `from <N dots><opt subpath> import <name>` against the importing file.

    Python semantics: depth-1 dots means "current package", depth-2 means
    "parent package", and so on. The source file's package is its directory.
    """
    # src_id format is "py:<file_path>:<qualified_name>". Split out the file.
    parts = src_id.split(":", 2)
    if len(parts) < 3:
        return f"external:rel{depth}.{rest}", 0.5
    src_file = parts[1]

    # `src_file` is like "pkg/sub/y.py". Drop the basename to get the package
    # path: ["pkg", "sub"]. Then go up (depth - 1) levels.
    src_no_ext = src_file.removesuffix(".py").removesuffix(".pyi")
    src_parts = src_no_ext.split("/")
    pkg_parts = src_parts[:-1]

    levels_up = depth - 1
    if levels_up > len(pkg_parts):
        return f"external:rel{depth}.{rest}", 0.5

    base_parts = pkg_parts[: len(pkg_parts) - levels_up]
    rest_parts = rest.split(".") if rest else []

    full_parts = [*base_parts, *rest_parts]
    if not full_parts:
        return f"external:rel{depth}.{rest}", 0.5

    full_qname = ".".join(full_parts)

    # Same shape as absolute: is the whole thing a module?
    if full_qname in by_module_qname:
        file = by_module_qname[full_qname]
        return f"py:{file}:{full_qname}", 1.0

    # Otherwise split last segment as name.
    if len(full_parts) >= 2:
        module_qname = ".".join(full_parts[:-1])
        name = full_parts[-1]
        file = by_module_qname.get(module_qname)
        if file is not None:
            hit = by_file_name.get((file, name))
            if hit is not None:
                return hit, 1.0
            return f"external:{module_qname}.{name}", 0.5

    return f"external:rel{depth}.{rest}", 0.5


# ----------------------------------------------------------------------
# TypeScript / JS resolution


def _resolve_typescript(
    rest: str,
    src_id: str,
    by_file_name: dict[tuple[str, str], str],
    known_files: set[str],
) -> tuple[str, float]:
    """Resolve a `ts:?:<specifier>(::<name>)?` edge against the file system.

    Behaviour:
    - Bare specifier (no leading ./ or ../) → external.
    - Relative specifier → walk extensions + index files to find the real file.
      * `::<name>` named import → look up (file, name) in entities.
      * `::default` → resolve to the module entity (we don't track default-export
        targets explicitly), conf 0.7.
      * `::*` → wildcard, conf 0.7.
      * no `::` (side-effect-only import) → module entity, conf 0.7.
    - tsconfig `paths` aliases (`@/`, etc.) are deferred — they go through the
      bare branch and end up as external for now.
    """
    if "::" in rest:
        specifier, name = rest.split("::", 1)
    else:
        specifier, name = rest, None

    is_relative = specifier.startswith("./") or specifier.startswith("../")
    if not is_relative:
        # Bare specifier: lodash / react / @scope/pkg / TS-paths alias.
        target = f"{specifier}.{name}" if name else specifier
        return f"external:{target}", 0.5

    src_parts = src_id.split(":", 2)
    if len(src_parts) < 3:
        return f"external:{specifier}{'::' + name if name else ''}", 0.5
    src_file = src_parts[1]
    src_dir = posixpath.dirname(src_file)
    joined = posixpath.normpath(posixpath.join(src_dir, specifier))

    target_file = _find_ts_file(joined, known_files)
    if target_file is None:
        return f"external:{specifier}{'::' + name if name else ''}", 0.5

    if name == "*":
        return f"wildcard:ts:{target_file}", 0.7

    if name and name != "default":
        # Named import — look up the entity directly.
        hit = by_file_name.get((target_file, name))
        if hit is not None:
            return hit, 1.0
        return f"external:{specifier}::{name}", 0.5

    # default import OR side-effect import — point at the module entity.
    module_qname = _path_to_module_qname(target_file)
    module_eid = by_file_name.get((target_file, module_qname))
    if module_eid is not None:
        return module_eid, 0.7
    return f"external:{specifier}{'::' + name if name else ''}", 0.5


# ----------------------------------------------------------------------
# New language resolvers (T10.7)


def _module_entity_for_file(
    file: str,
    by_file_name: dict[tuple[str, str], str],
) -> tuple[str, float] | None:
    """Return the module entity_id for a known file, or None if not in index."""
    module_qname = _path_to_module_qname(file)
    hit = by_file_name.get((file, module_qname))
    if hit is not None:
        return hit, 0.9
    return None


def _resolve_go(
    path: str,
    src_id: str,
    by_file_name: dict[tuple[str, str], str],
    by_module_qname: dict[str, str],
    known_files: set[str],
) -> tuple[str, float]:
    """Resolve a Go import path.

    Go imports are module-path rooted (e.g. `github.com/x/y/pkg` or just
    `fmt`). Best-effort strategy: convert the last slash-segment to a directory
    name and scan for any `.go` file inside a directory of that name.
    """
    # Standard library packages have no slash or dots — treat as external.
    last_seg = path.split("/")[-1]

    # Try exact module-qname lookup (works when the path matches our index keys
    # directly, e.g. the repo root is the module root).
    dot_path = path.replace("/", ".")
    file = by_module_qname.get(dot_path)
    if file and file.endswith(".go"):
        result = _module_entity_for_file(file, by_file_name)
        if result:
            return result

    # Heuristic: find any `.go` file whose parent directory's last segment matches.
    for known_file in sorted(known_files):
        if not known_file.endswith(".go"):
            continue
        parts = known_file.split("/")
        if len(parts) >= 2 and parts[-2] == last_seg:
            result = _module_entity_for_file(known_file, by_file_name)
            if result:
                return result

    return f"external:{path}", 0.5


def _resolve_rust(
    path: str,
    src_id: str,
    by_file_name: dict[tuple[str, str], str],
    by_module_qname: dict[str, str],
    known_files: set[str],
) -> tuple[str, float]:
    """Resolve a Rust `use` path (e.g. `serde::Serialize`, `crate::server`)."""
    # Wildcards
    if path.endswith("::*"):
        path = path[:-3]

    # Standard-library namespaces → always external.
    if any(path.startswith(p) for p in _RUST_STDLIB_PREFIXES):
        return f"external:{path}", 0.5

    # Strip `crate::` prefix — refers to the current crate's root.
    rel = path.removeprefix("crate::")

    # Convert `::` → `/` and try `<path>.rs` or `<path>/mod.rs`
    slash_path = rel.replace("::", "/")
    for candidate in (slash_path + ".rs", slash_path + "/mod.rs"):
        if candidate in known_files:
            result = _module_entity_for_file(candidate, by_file_name)
            if result:
                return result

    # Also try the module-qname index with `.` separators.
    dot_path = rel.replace("::", ".")
    file = by_module_qname.get(dot_path)
    if file and file.endswith(".rs"):
        result = _module_entity_for_file(file, by_file_name)
        if result:
            return result

    return f"external:{path}", 0.5


def _resolve_java(
    path: str,
    by_file_name: dict[tuple[str, str], str],
    known_files: set[str],
) -> tuple[str, float]:
    """Resolve a Java `import` path (e.g. `com.example.Server`, `java.util.*`)."""
    # Wildcard imports
    if path.endswith(".*"):
        return f"external:{path}", 0.5

    # Known stdlib / framework roots → external.
    if path.startswith(("java.", "javax.", "org.junit.", "org.springframework.", "android.")):
        return f"external:{path}", 0.5

    # PSR-like: com.example.server.Server → com/example/server/Server.java
    file_candidate = path.replace(".", "/") + ".java"
    if file_candidate in known_files:
        last_seg = path.rsplit(".", 1)[-1]
        hit = by_file_name.get((file_candidate, last_seg))
        if hit:
            return hit, 0.9
        result = _module_entity_for_file(file_candidate, by_file_name)
        if result:
            return result

    return f"external:{path}", 0.5


def _resolve_ruby(
    path: str,
    src_id: str,
    by_file_name: dict[tuple[str, str], str],
    known_files: set[str],
) -> tuple[str, float]:
    """Resolve a Ruby `require` / `require_relative` path."""
    src_file = src_id.split(":", 2)[1] if src_id.count(":") >= 2 else ""
    src_dir = posixpath.dirname(src_file)

    candidates: list[str] = []

    if path.startswith("./") or path.startswith("../"):
        # require_relative-style: resolve against the source file's directory.
        joined = posixpath.normpath(posixpath.join(src_dir, path))
        candidates += [joined, joined + ".rb"]
    else:
        # Bare require: probe common directory prefixes + direct file.
        for prefix in ("", "lib/", "app/"):
            candidates += [
                prefix + path,
                prefix + path + ".rb",
            ]

    for candidate in candidates:
        if candidate in known_files:
            result = _module_entity_for_file(candidate, by_file_name)
            if result:
                return result

    return f"external:{path}", 0.5


def _resolve_php(
    path: str,
    src_id: str,
    by_file_name: dict[tuple[str, str], str],
    known_files: set[str],
) -> tuple[str, float]:
    """Resolve a PHP `use` namespace path or `require`/`include` file path."""
    src_file = src_id.split(":", 2)[1] if src_id.count(":") >= 2 else ""
    src_dir = posixpath.dirname(src_file)

    # File-based includes (has .php extension)
    if path.endswith(".php"):
        candidates = [path, posixpath.normpath(posixpath.join(src_dir, path))]
        for candidate in candidates:
            if candidate in known_files:
                result = _module_entity_for_file(candidate, by_file_name)
                if result:
                    return result
        return f"external:{path}", 0.5

    # PSR-4 namespace: App\Http\Request → App/Http/Request.php
    if "\\" in path:
        file_candidate = path.replace("\\", "/") + ".php"
        if file_candidate in known_files:
            last_seg = path.rsplit("\\", 1)[-1]
            hit = by_file_name.get((file_candidate, last_seg))
            if hit:
                return hit, 0.9
            result = _module_entity_for_file(file_candidate, by_file_name)
            if result:
                return result

    return f"external:{path}", 0.5


def _resolve_c_include(
    path: str,
    src_id: str,
    by_file_name: dict[tuple[str, str], str],
    known_files: set[str],
    lang_prefix: str,
) -> tuple[str, float]:
    """Resolve a C/C++ `#include` path.

    System headers (no extension or well-known C++ stdlib names) → external.
    Local headers (relative paths or `.h`/`.hpp` names) → probe known_files.
    """
    # C++ stdlib headers with no extension are always external.
    if path in _C_SYSTEM_NOEXT:
        return f"external:{path}", 0.5

    src_file = src_id.split(":", 2)[1] if src_id.count(":") >= 2 else ""
    src_dir = posixpath.dirname(src_file)

    candidates = [
        path,
        posixpath.normpath(posixpath.join(src_dir, path)),
    ]
    for candidate in candidates:
        if candidate in known_files:
            result = _module_entity_for_file(candidate, by_file_name)
            if result:
                return result

    return f"external:{path}", 0.5


def _find_ts_file(joined_no_ext: str, known_files: set[str]) -> str | None:
    """Probe `<joined>.ext` then `<joined>/index.ext` against the indexed files."""
    if joined_no_ext in known_files:
        return joined_no_ext  # specifier included the extension explicitly
    for ext in _TS_EXTENSIONS:
        candidate = f"{joined_no_ext}{ext}"
        if candidate in known_files:
            return candidate
    for idx in _TS_INDEX_NAMES:
        candidate = f"{joined_no_ext}/{idx}"
        if candidate in known_files:
            return candidate
    return None
