# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Cross-file symbol resolver — closes provisional import dst_ids to real entity_ids.

After the parse pass, the graph contains import edges whose `dst_id` follows
provisional encodings (one per language). This module rewrites them in-place
to real entity_ids or stable external/wildcard markers.

Provisional → resolved mapping (Python example):

  py:?:<module>.<name>    from <module> import <name> (absolute)
  py:?:<module>           import <module>              (the module itself)
  py:?:<module>.*         from <module> import *       (wildcard)
  py:?rel<N>:<rest>       relative import, N leading dots
  py:?call:<name>         a call whose receiver's type wasn't inferred
  py:?methodcall:<T>.<n>  a call whose receiver was inferred to type T

Output edge categories:

  conf=1.0  → resolved to a known entity_id:  `py:<file>:<qualified_name>`
  conf=1.0  → resolved to a module entity:    `py:<file>:<module_qname>`
  conf=0.9  → resolved via import table, or an exact receiver-type match
  conf=0.7  → wildcard, module known:         `wildcard:py:<file>`
  conf=0.5  → stdlib / 3rd-party / unresolvable: `external:<dotted>`

Supported languages: Python, TypeScript/JS, Go, Rust, Java, Ruby, PHP, C, C++.

The resolver is idempotent — a second run is a no-op because no provisional
`lang:?:...` edges remain after the first pass.

Public API
----------
resolve_symbols(store) -> ResolutionStats
    Rewrite all provisional edges in the graph. Returns counts.
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
    "OR dst_id LIKE 'c:?%' OR dst_id LIKE 'cpp:?%' "
    "OR dst_id LIKE 'kt:?%' OR dst_id LIKE 'cs:?%' "
    "OR dst_id LIKE 'scala:?%' OR dst_id LIKE 'sh:?%' "
    "OR dst_id LIKE 'ex:?%' OR dst_id LIKE 'r:?%' "
    "OR dst_id LIKE 'jl:?%' OR dst_id LIKE 'hs:?%' "
    "OR dst_id LIKE 'ml:?%' OR dst_id LIKE 'html:?%'"
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
        # Containers
        "string",
        "string_view",
        "vector",
        "map",
        "unordered_map",
        "set",
        "unordered_set",
        "list",
        "forward_list",
        "array",
        "deque",
        "queue",
        "stack",
        "bitset",
        "span",
        # Memory / ownership
        "memory",
        "memory_resource",
        # Algorithms / ranges
        "algorithm",
        "numeric",
        "ranges",
        "execution",
        "iterator",
        # Utilities
        "utility",
        "tuple",
        "optional",
        "variant",
        "any",
        "expected",
        "functional",
        "type_traits",
        "concepts",
        "limits",
        # I/O
        "iostream",
        "fstream",
        "sstream",
        "iomanip",
        "format",
        # Concurrency
        "thread",
        "mutex",
        "shared_mutex",
        "condition_variable",
        "atomic",
        "future",
        "latch",
        "barrier",
        "semaphore",
        # Time
        "chrono",
        # Error handling
        "stdexcept",
        "exception",
        "system_error",
        # C compatibility headers
        "cassert",
        "cstdio",
        "cstdlib",
        "cstddef",
        "cstdint",
        "cstring",
        "cmath",
        "climits",
        "cerrno",
        "ctime",
        "csignal",
        # Misc
        "regex",
        "random",
        "complex",
        "valarray",
        "initializer_list",
        "typeindex",
        "typeinfo",
        "new",
        "source_location",
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
    # the same suffix convention; `?methodcall:` is the receiver-typed variant
    # (py:?methodcall:<Type>.<name>) emitted when the parser inferred the
    # receiver's type. `?inherits:` (a class's base-class list) is resolved
    # first of all, since method-call resolution needs it.
    inherits_rows = [r for r in rows if ":?inherits:" in r[1]]
    call_rows = [r for r in rows if ":?call:" in r[1] or ":?methodcall:" in r[1]]
    import_rows = [
        r
        for r in rows
        if ":?call:" not in r[1] and ":?methodcall:" not in r[1] and ":?inherits:" not in r[1]
    ]

    resolved = external = wildcard = 0
    resolved_edges: list[Edge] = []
    # file → {imported_name: resolved_target_id} — built from resolved imports,
    # used to resolve calls to imported symbols.
    imports_by_file: dict[str, dict[str, str]] = {}

    # Phase 0 — inheritance edges. Resolved before calls: receiver-typed
    # method-call resolution walks a class's resolved base classes when
    # `Type.method` isn't declared directly on `Type` itself. Same-file base
    # class preferred when the base name is ambiguous repo-wide; unresolved
    # (ambiguous or missing) stays external rather than guessed at, same
    # policy as every other name-based resolution in this module.
    #
    # dst_id carries an explicit `<index>:` prefix (`py:?inherits:0:Base`) so
    # bases_by_class can be built in the declared left-to-right order
    # (matching Python/C++'s own MRO preference for the common non-diamond
    # case) regardless of the DB's row-fetch order -- SQL result order isn't
    # guaranteed without ORDER BY, and relying on incidental fetch order was
    # a real bug caught by CI running on a different platform than local dev.
    def _inherits_sort_key(row: tuple) -> tuple[str, int]:
        _, dst, _, _ = row
        _, _, rest = dst.partition(":?inherits:")
        index_str, _, _ = rest.partition(":")
        return (row[0], int(index_str) if index_str.isdigit() else 0)

    bases_by_class: dict[str, list[str]] = {}
    for src_id, dst_id, edge_type, line in sorted(inherits_rows, key=_inherits_sort_key):
        _, _, rest = dst_id.partition(":?inherits:")
        _, _, base_name = rest.partition(":")
        parts = src_id.split(":", 2)
        file = parts[1] if len(parts) >= 3 else ""
        same_file_id = idx.entities_by_file.get(file, {}).get(base_name)
        if same_file_id:
            new_dst, new_conf = same_file_id, 0.9
        else:
            candidates = idx.entity_ids_by_name.get(base_name, [])
            new_dst, new_conf = (
                (candidates[0], 0.7) if len(candidates) == 1 else (f"external:{base_name}", 0.5)
            )
        resolved_edges.append(
            Edge(src_id=src_id, dst_id=new_dst, type=edge_type, line=line, confidence=new_conf)
        )
        if new_dst.startswith("external:"):
            external += 1
        else:
            resolved += 1
            bases_by_class.setdefault(src_id, []).append(new_dst)

    # Phase 1 — imports.
    for src_id, dst_id, edge_type, line in import_rows:
        new_dst, new_conf = _resolve_one(
            dst_id,
            src_id,
            idx.by_file_name,
            idx.by_module_qname,
            idx.known_files,
            idx.by_qname_suffix,
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
        if ":?methodcall:" in dst_id:
            new_dst, new_conf = _resolve_method_call(
                dst_id,
                src_id,
                idx.entities_by_file,
                imports_by_file,
                idx.entity_ids_by_qname,
                idx.entity_ids_by_name,
                bases_by_class,
                idx.name_by_id,
            )
        else:
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

    # Phase 3 — route-handler edges from Express/Django/Rails (Flask/FastAPI/
    # Spring never emit these -- their handler is same-file by construction,
    # resolved directly at parse time). A route registration's handler name
    # is looked up against every file's entities, not just one; only resolve
    # when the name is unambiguous repo-wide -- unlike a same-file call,
    # there's no "closest file" tiebreaker for a name that lives in a
    # different subsystem (routes.rb vs a controller file), so a wrong guess
    # here would create a false call edge instead of just missing one.
    route_rows = store.conn.execute(
        "SELECT src_id, dst_id, type, line FROM edges WHERE dst_id LIKE 'route:?handler:%'"
    ).fetchall()
    if route_rows:
        route_resolved_edges: list[Edge] = []
        for src_id, dst_id, edge_type, line in route_rows:
            name = dst_id.removeprefix("route:?handler:")
            candidates = idx.entity_ids_by_name.get(name, [])
            if len(candidates) == 1:
                new_dst = candidates[0]
                resolved += 1
            else:
                new_dst = f"external:route_handler:{name}"
                external += 1
            route_resolved_edges.append(
                Edge(
                    src_id=src_id,
                    dst_id=new_dst,
                    type=edge_type,
                    line=line,
                    confidence=0.5,
                    is_dynamic=True,
                )
            )
        store.conn.execute("DELETE FROM edges WHERE dst_id LIKE 'route:?handler:%'")
        store.upsert_edges(route_resolved_edges)

    # Phase 4 — cross-language HTTP edges. Every backend resolver above
    # (Flask/FastAPI/Express/Django/Spring/Rails) emits `route:<METHOD>
    # <path>` as an edge SOURCE pointing at its handler; a frontend
    # fetch/axios call site (extracted by http_client.py) is a `calls` edge
    # whose provisional dst_id encodes the same (method, path). Matching the
    # two turns "frontend calls this URL" + "backend handles this URL" into
    # one edge straight from the call site to the handler, regardless of
    # language -- this closes the gap this project's own README called out
    # as deliberately deferred. Read fresh from the DB rather than reusing
    # `resolved_edges`/`route_resolved_edges` in memory: those two lists
    # don't cover every route source (Flask/FastAPI/Spring's route edges were
    # never provisional, so they were written straight to the table at index
    # time and never touch either list above).
    http_rows = store.conn.execute(
        "SELECT src_id, dst_id, type, line FROM edges WHERE dst_id LIKE 'route:?http:%'"
    ).fetchall()
    if http_rows:
        route_handlers: dict[str, list[str]] = {}
        for route_src, handler_dst in store.conn.execute(
            "SELECT src_id, dst_id FROM edges WHERE src_id LIKE 'route:%' AND type = 'calls'"
        ).fetchall():
            if not handler_dst.startswith("external:"):
                route_handlers.setdefault(route_src, []).append(handler_dst)

        http_resolved_edges: list[Edge] = []
        for src_id, dst_id, edge_type, line in http_rows:
            method, _, path = dst_id.removeprefix("route:?http:").partition(":")
            route_key = f"route:{method} {path}"
            handlers = route_handlers.get(route_key, [])
            if len(handlers) == 1:
                new_dst = handlers[0]
                resolved += 1
            else:
                new_dst = f"external:http_route:{method}:{path}"
                external += 1
            http_resolved_edges.append(
                Edge(
                    src_id=src_id,
                    dst_id=new_dst,
                    type=edge_type,
                    line=line,
                    confidence=0.5,
                    is_dynamic=True,
                )
            )
        store.conn.execute("DELETE FROM edges WHERE dst_id LIKE 'route:?http:%'")
        store.upsert_edges(http_resolved_edges)

    return ResolutionStats(
        inspected=len(rows) + len(route_rows) + len(http_rows),
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
    by_qname_suffix: dict[str, list[str]]  # any dotted suffix of a module qname → matching paths
    known_files: set[str]  # all indexed file paths (TS module probing)
    name_by_id: dict[str, str]  # entity_id → name (import-target naming)
    entities_by_file: dict[str, dict[str, str]]  # file → {name: entity_id}
    entity_ids_by_name: dict[str, list[str]]  # name → every entity_id repo-wide (route handlers)
    entity_ids_by_qname: dict[
        str, list[str]
    ]  # qualified_name → every entity_id (receiver-typed calls)


def _build_indexes(store: GraphStore) -> _Indexes:
    by_file_name: dict[tuple[str, str], str] = {}
    name_by_id: dict[str, str] = {}
    entities_by_file: dict[str, dict[str, str]] = {}
    entity_ids_by_name: dict[str, list[str]] = {}
    entity_ids_by_qname: dict[str, list[str]] = {}
    for entity_id, file, name, qname in store.conn.execute(
        "SELECT entity_id, file, name, qualified_name FROM entities"
    ).fetchall():
        by_file_name[(file, name)] = entity_id
        name_by_id[entity_id] = name
        entity_ids_by_name.setdefault(name, []).append(entity_id)
        entity_ids_by_qname.setdefault(qname, []).append(entity_id)
        fmap = entities_by_file.setdefault(file, {})
        # On a name collision within a file (e.g. two classes' `validate`
        # methods), prefer the top-level definition (qualified_name == name),
        # which is what a bare `validate()` call most likely targets.
        if name not in fmap or qname == name:
            fmap[name] = entity_id

    by_module_qname: dict[str, str] = {}
    by_qname_suffix: dict[str, list[str]] = {}
    known_files: set[str] = set()
    aliases: list[tuple[str, str]] = []  # (src-root-stripped qname, path)
    for (path,) in store.conn.execute("SELECT path FROM files").fetchall():
        known_files.add(path)
        qname = _path_to_module_qname(path)
        by_module_qname[qname] = path
        # Every dotted suffix, not just the last segment: a repo with a
        # nested root the caller doesn't know about (`cold/backend/` added
        # to sys.path, so `services/leads_service.py` is imported as bare
        # `from services import leads_service`, not the full
        # `cold.backend.services.leads_service`) needs the multi-segment
        # suffix `services.leads_service` to match, not just `leads_service`.
        segments = qname.split(".")
        for i in range(len(segments)):
            by_qname_suffix.setdefault(".".join(segments[i:]), []).append(path)
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
        by_qname_suffix=by_qname_suffix,
        known_files=known_files,
        name_by_id=name_by_id,
        entities_by_file=entities_by_file,
        entity_ids_by_name=entity_ids_by_name,
        entity_ids_by_qname=entity_ids_by_qname,
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


def _resolve_method_call(
    dst_id: str,
    src_id: str,
    entities_by_file: dict[str, dict[str, str]],
    imports_by_file: dict[str, dict[str, str]],
    entity_ids_by_qname: dict[str, list[str]],
    entity_ids_by_name: dict[str, list[str]] | None = None,
    bases_by_class: dict[str, list[str]] | None = None,
    name_by_id: dict[str, str] | None = None,
) -> tuple[str, float]:
    """Resolve a `<lang>:?methodcall:<Type>.<name>` edge -- a call whose
    receiver's type the parser inferred (a local variable, `self`, or a typed
    parameter). Tries an exact `Type.name` qualified-name match first (same-file
    preferred when the type name is ambiguous repo-wide, e.g. two unrelated
    `Logger` classes); if `name` isn't declared directly on `Type`, walks
    `Type`'s resolved base classes looking for it there (inherited methods);
    falls back to plain callee-name resolution when neither finds anything,
    since a wrong type guess or a builtin/stdlib type shouldn't manufacture a
    wrong edge -- it should degrade to exactly what an untyped call would
    have done.
    """
    lang_prefix, _, rest = dst_id.partition(":?methodcall:")
    type_name, sep, callee = rest.rpartition(".")
    if sep:
        parts = src_id.split(":", 2)
        file = parts[1] if len(parts) >= 3 else ""
        candidates = entity_ids_by_qname.get(rest, [])
        if len(candidates) == 1:
            return candidates[0], 0.9
        if len(candidates) > 1:
            same_file = [c for c in candidates if c.split(":", 2)[1:2] == [file]]
            if len(same_file) == 1:
                return same_file[0], 0.9
        if entity_ids_by_name and bases_by_class and name_by_id:
            inherited = _walk_inheritance_chain(
                type_name,
                callee,
                file,
                entity_ids_by_name,
                entity_ids_by_qname,
                bases_by_class,
                name_by_id,
            )
            if inherited:
                return inherited, 0.8
    else:
        callee = rest
    return _resolve_call(f"{lang_prefix}:?call:{callee}", src_id, entities_by_file, imports_by_file)


def _walk_inheritance_chain(
    type_name: str,
    method: str,
    call_file: str,
    entity_ids_by_name: dict[str, list[str]],
    entity_ids_by_qname: dict[str, list[str]],
    bases_by_class: dict[str, list[str]],
    name_by_id: dict[str, str],
    max_depth: int = 6,
) -> str | None:
    """BFS up `type_name`'s resolved base classes for a `method` not declared
    directly on it -- `Derived.method()` where `method` lives only on `Base`.
    Same-file preferred when a class or method name is ambiguous; gives up
    (returns None) after `max_depth` hops or once every reachable base is
    exhausted, rather than guessing among ambiguous candidates.
    """
    # Only class-like entities that actually have resolved bases recorded
    # can seed the walk -- a same-named function/method isn't a base to walk.
    starting = [eid for eid in entity_ids_by_name.get(type_name, []) if eid in bases_by_class]
    if not starting:
        return None
    if len(starting) > 1:
        same_file = [eid for eid in starting if eid.split(":", 2)[1:2] == [call_file]]
        starting = same_file if len(same_file) == 1 else starting

    seen: set[str] = set()
    frontier = list(starting)
    depth = 0
    while frontier and depth < max_depth:
        next_frontier: list[str] = []
        for class_id in frontier:
            if class_id in seen:
                continue
            seen.add(class_id)
            for base_id in bases_by_class.get(class_id, []):
                base_name = name_by_id.get(base_id)
                if not base_name:
                    continue
                candidates = entity_ids_by_qname.get(f"{base_name}.{method}", [])
                if len(candidates) == 1:
                    return candidates[0]
                if len(candidates) > 1:
                    same_file = [c for c in candidates if c.split(":", 2)[1:2] == [call_file]]
                    if len(same_file) == 1:
                        return same_file[0]
                next_frontier.append(base_id)
        frontier = next_frontier
        depth += 1
    return None


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
    by_qname_suffix: dict[str, list[str]] | None = None,
) -> tuple[str, float]:
    rel_match = _REL_RE.match(dst_id)
    if rel_match:
        depth = int(rel_match.group(1))
        rest = rel_match.group(2)
        return _resolve_relative(rest, depth, src_id, by_file_name, by_module_qname)

    if dst_id.startswith("py:?:"):
        return _resolve_absolute(
            dst_id[len("py:?:") :], by_file_name, by_module_qname, by_qname_suffix or {}
        )

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
    by_qname_suffix: dict[str, list[str]] | None = None,
) -> tuple[str, float]:
    by_qname_suffix = by_qname_suffix or {}

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

    # `import auth` (or `from services import leads_service`, a submodule
    # import -- this encoding can't tell the two shapes apart, but both
    # resolve to the same target either way) where the real file lives at
    # some nested path the caller's sys.path setup hides (`backend/auth.py`
    # run with `backend/` itself on sys.path, or `services/leads_service.py`
    # imported as a bare submodule) is common in repos that don't use
    # package-relative imports throughout -- src-root stripping only covers a
    # fixed allowlist of directory names (src, packages, lib, app, source),
    # not an arbitrary one like `backend`. Only resolve when the suffix is
    # unambiguous repo-wide; two files whose qname ends the same way (e.g.
    # two subsystems each with their own auth.py) must not guess which one
    # a bare import meant.
    suffix_candidates = by_qname_suffix.get(qname, [])
    if len(suffix_candidates) == 1:
        file = suffix_candidates[0]
        return f"py:{file}:{_path_to_module_qname(file)}", 0.8

    # Try splitting `module.name`.
    if "." in qname:
        module, name = qname.rsplit(".", 1)
        file = by_module_qname.get(module)
        via_fallback = False
        if file is None:
            candidates = by_qname_suffix.get(module, [])
            if len(candidates) == 1:
                file = candidates[0]
                via_fallback = True
            elif len(candidates) > 1:
                # Two files share a qname suffix (e.g. `auth.py` and
                # `routers/auth.py`) -- path alone can't disambiguate, but
                # *which one actually defines the imported name* usually can:
                # a router file sharing the name coincidentally won't also
                # happen to define the same function the real module does.
                defining = [c for c in candidates if (c, name) in by_file_name]
                if len(defining) == 1:
                    file = defining[0]
                    via_fallback = True
        if file is not None:
            hit = by_file_name.get((file, name))
            if hit is not None:
                return hit, 0.8 if via_fallback else 1.0
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
# New language resolvers


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
