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

import re
from dataclasses import dataclass

from codegraph.graph.store import GraphStore

_REL_RE = re.compile(r"^py:\?rel(\d+):(.*)$")


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
    by_file_name, by_module_qname = _build_indexes(store)

    rows = store.conn.execute(
        "SELECT src_id, dst_id, type, line FROM edges WHERE dst_id LIKE 'py:?%'"
    ).fetchall()

    resolved = external = wildcard = 0

    for src_id, dst_id, edge_type, line in rows:
        new_dst, new_conf = _resolve_one(dst_id, src_id, by_file_name, by_module_qname)
        if new_dst == dst_id:
            external += 1  # nothing changed but we still count it
            continue

        # DELETE + INSERT OR IGNORE rather than UPDATE: on re-index, the parser
        # re-emits the provisional edge and the resolved counterpart already
        # exists in the table, so a naive UPDATE would hit the composite PK.
        store.conn.execute(
            "DELETE FROM edges WHERE src_id = ? AND dst_id = ? AND type = ? AND line = ?",
            [src_id, dst_id, edge_type, line],
        )
        store.conn.execute(
            """
            INSERT OR IGNORE INTO edges (src_id, dst_id, type, line, confidence, is_dynamic)
            VALUES (?, ?, ?, ?, ?, FALSE)
            """,
            [src_id, new_dst, edge_type, line, new_conf],
        )

        if new_dst.startswith("wildcard:"):
            wildcard += 1
        elif new_dst.startswith("external:"):
            external += 1
        else:
            resolved += 1

    return ResolutionStats(
        inspected=len(rows),
        resolved=resolved,
        external=external,
        wildcard=wildcard,
    )


# ----------------------------------------------------------------------
# Indexes


def _build_indexes(store: GraphStore) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """Build the two lookups the resolver needs.

    Returns:
        by_file_name: (file_path, simple_name) → entity_id
        by_module_qname: module_qualified_name → file_path
    """
    by_file_name: dict[tuple[str, str], str] = {}
    for entity_id, file, name in store.conn.execute(
        "SELECT entity_id, file, name FROM entities"
    ).fetchall():
        by_file_name[(file, name)] = entity_id

    by_module_qname: dict[str, str] = {}
    for (path,) in store.conn.execute("SELECT path FROM files").fetchall():
        module_qname = path.removesuffix(".py").removesuffix(".pyi").replace("/", ".")
        by_module_qname[module_qname] = path

    return by_file_name, by_module_qname


# ----------------------------------------------------------------------
# Resolution dispatch


def _resolve_one(
    dst_id: str,
    src_id: str,
    by_file_name: dict[tuple[str, str], str],
    by_module_qname: dict[str, str],
) -> tuple[str, float]:
    rel_match = _REL_RE.match(dst_id)
    if rel_match:
        depth = int(rel_match.group(1))
        rest = rel_match.group(2)
        return _resolve_relative(rest, depth, src_id, by_file_name, by_module_qname)

    if dst_id.startswith("py:?:"):
        return _resolve_absolute(dst_id[len("py:?:") :], by_file_name, by_module_qname)

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
        return f"py:{file}:{qname}", 1.0

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
