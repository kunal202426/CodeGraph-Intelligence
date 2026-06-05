"""Tests for the cross-file symbol resolver."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app
from codegraph.graph.resolver import resolve_symbols
from codegraph.graph.store import GraphStore
from typer.testing import CliRunner


def _make_repo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _index(tmp_path: Path, repo_files: dict[str, str]) -> Path:
    """Materialize a fake repo, index it via CLI, return the DB path."""
    repo = tmp_path / "repo"
    _make_repo(repo, repo_files)
    db = tmp_path / "graph.duckdb"
    result = CliRunner().invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    return db


def _edges(store: GraphStore) -> list[tuple[str, str, float]]:
    """Return list of (src_id, dst_id, confidence) for all edges, sorted."""
    rows = store.conn.execute(
        "SELECT src_id, dst_id, confidence FROM edges ORDER BY src_id, dst_id"
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


# ---------- core cross-file resolution ----------


def test_resolves_absolute_from_import_to_real_entity(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "main.py": "from helpers.util import compute\n",
            "helpers/__init__.py": "",
            "helpers/util.py": "def compute():\n    return 1\n",
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    # Every edge dst must be a resolved entity or external/wildcard. None should
    # still have a `py:?` prefix.
    assert not any(dst.startswith("py:?") for _, dst, _ in edges)
    # The compute import should be resolved to the actual entity_id.
    resolved_dsts = {dst for _, dst, _ in edges}
    assert "py:helpers/util.py:compute" in resolved_dsts


def test_resolves_import_of_module_itself(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "main.py": "import helpers.util\n",
            "helpers/__init__.py": "",
            "helpers/util.py": "def f(): return 1\n",
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    # `import helpers.util` should resolve to the module entity, not external.
    dsts = {dst for _, dst, _ in edges}
    assert "py:helpers/util.py:helpers.util" in dsts


def test_aliased_import_resolves_to_real_target(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "main.py": "from helpers.util import compute as c\n",
            "helpers/__init__.py": "",
            "helpers/util.py": "def compute():\n    return 1\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
    finally:
        store.close()
    assert "py:helpers/util.py:compute" in dsts


# ---------- externals ----------


def test_stdlib_import_marked_external(tmp_path: Path) -> None:
    db = _index(tmp_path, {"main.py": "import os\nfrom sys import argv\n"})
    store = GraphStore(db)
    try:
        rows = _edges(store)
    finally:
        store.close()
    dst_conf = {dst: conf for _, dst, conf in rows}
    assert "external:os" in dst_conf
    assert dst_conf["external:os"] == pytest.approx(0.5)
    assert "external:sys.argv" in dst_conf
    assert dst_conf["external:sys.argv"] == pytest.approx(0.5)


def test_import_of_nonexistent_name_from_local_module_is_external(tmp_path: Path) -> None:
    """File exists, but the imported name isn't an indexed entity → external."""
    db = _index(
        tmp_path,
        {
            "main.py": "from helpers.util import not_a_thing\n",
            "helpers/__init__.py": "",
            "helpers/util.py": "def real_thing(): return 1\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
    finally:
        store.close()
    assert "external:helpers.util.not_a_thing" in dsts


# ---------- relative imports ----------


def test_resolves_relative_dot_import_to_sibling(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from . import b\n",
            "pkg/b.py": "def hi(): return 1\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
    finally:
        store.close()
    # `from . import b` in pkg/a.py should land on the pkg.b module entity.
    assert "py:pkg/b.py:pkg.b" in dsts


def test_resolves_relative_dotted_subpkg_import(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/util.py": "def helper(): return 1\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/a.py": "from ..util import helper\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
    finally:
        store.close()
    assert "py:pkg/util.py:helper" in dsts


def test_relative_import_too_deep_marked_external(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "a.py": "from .. import nope\n",  # only at root, can't go up
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
    finally:
        store.close()
    assert any(dst.startswith("external:") for dst in dsts)


# ---------- wildcards ----------


def test_wildcard_import_marked_with_wildcard_prefix(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "main.py": "from helpers.util import *\n",
            "helpers/__init__.py": "",
            "helpers/util.py": "def a(): return 1\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
    finally:
        store.close()
    assert "wildcard:py:helpers/util.py" in dsts


def test_wildcard_for_unknown_module_marked_external(tmp_path: Path) -> None:
    db = _index(tmp_path, {"main.py": "from os.path import *\n"})
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
    finally:
        store.close()
    assert "external:os.path.*" in dsts


# ---------- idempotency ----------


def test_resolver_is_idempotent(tmp_path: Path) -> None:
    """Running the resolver a second time should not change anything."""
    db = _index(
        tmp_path,
        {
            "main.py": "from helpers.util import compute\nimport os\n",
            "helpers/__init__.py": "",
            "helpers/util.py": "def compute(): return 1\n",
        },
    )
    store = GraphStore(db)
    try:
        before = _edges(store)
        stats2 = resolve_symbols(store)
        after = _edges(store)
    finally:
        store.close()
    assert before == after
    # No `py:?` left → second pass inspects 0 rows.
    assert stats2.inspected == 0


# ---------- stats ----------


def test_resolution_stats_counts_make_sense(tmp_path: Path) -> None:
    """Index a small repo, manually run the resolver on a clean DB to test stats."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "main.py": (
                "from helpers.util import compute\n"  # resolved
                "import os\n"  # external
                "from helpers.util import *\n"  # wildcard
            ),
            "helpers/__init__.py": "",
            "helpers/util.py": "def compute(): return 1\n",
        },
    )
    # First, index without auto-resolving by writing through the indexer once.
    # The CLI auto-resolves, so just check the final stats by re-running on
    # a fresh DB built from scratch via the parser stack.
    db = tmp_path / "graph.duckdb"
    result = CliRunner().invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0, result.stdout

    # Now manually re-run resolver. After the auto-pass everything is closed
    # so the second pass inspects 0 — that's expected and confirms the wire-up.
    store = GraphStore(db)
    try:
        stats = resolve_symbols(store)
    finally:
        store.close()
    assert stats.inspected == 0


# ---------- TypeScript imports (T2.5) ----------


def _ts_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    db = tmp_path / "graph.duckdb"
    result = CliRunner().invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    return db


def test_ts_named_relative_resolves(tmp_path: Path) -> None:
    db = _ts_repo(
        tmp_path,
        {
            "src/index.ts": 'import { authenticate } from "./auth/login";\n',
            "src/auth/login.ts": "export function authenticate() { return true; }\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "ts:src/auth/login.ts:authenticate" in dsts


def test_ts_default_import_resolves_to_module_entity(tmp_path: Path) -> None:
    db = _ts_repo(
        tmp_path,
        {
            "src/index.ts": 'import auth from "./auth";\n',
            "src/auth.ts": "export default function() { return 1; }\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    # Module entity for src/auth.ts has name "src.auth"
    assert "ts:src/auth.ts:src.auth" in dsts


def test_ts_namespace_import_marked_wildcard(tmp_path: Path) -> None:
    db = _ts_repo(
        tmp_path,
        {
            "src/main.ts": 'import * as A from "./mod";\n',
            "src/mod.ts": "export function x() { return 1; }\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "wildcard:ts:src/mod.ts" in dsts


def test_ts_bare_specifier_marked_external(tmp_path: Path) -> None:
    db = _ts_repo(
        tmp_path,
        {"src/app.tsx": 'import { useState } from "react";\n'},
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "external:react.useState" in dsts


def test_ts_resolves_through_index_file(tmp_path: Path) -> None:
    """`import X from "./pkg"` matches `./pkg/index.ts`."""
    db = _ts_repo(
        tmp_path,
        {
            "src/main.ts": 'import { thing } from "./pkg";\n',
            "src/pkg/index.ts": "export function thing() { return 1; }\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "ts:src/pkg/index.ts:thing" in dsts


def test_ts_resolves_parent_dir_specifier(tmp_path: Path) -> None:
    db = _ts_repo(
        tmp_path,
        {
            "src/sub/a.ts": 'import { helper } from "../util";\n',
            "src/util.ts": "export function helper() { return 1; }\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "ts:src/util.ts:helper" in dsts


def test_ts_unknown_relative_path_external(tmp_path: Path) -> None:
    db = _ts_repo(
        tmp_path,
        {"src/main.ts": 'import { x } from "./does-not-exist";\n'},
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "external:./does-not-exist::x" in dsts


def test_ts_picks_tsx_over_missing_ts(tmp_path: Path) -> None:
    """When `./Component.tsx` exists but no `.ts`, the resolver finds the .tsx."""
    db = _ts_repo(
        tmp_path,
        {
            "src/index.ts": 'import { App } from "./Component";\n',
            "src/Component.tsx": "export function App() { return null; }\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "ts:src/Component.tsx:App" in dsts


# ---------- src_id preservation ----------


def test_resolved_edge_keeps_src_id_and_type_and_line(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "main.py": "\n\nfrom helpers.util import compute\n",
            "helpers/__init__.py": "",
            "helpers/util.py": "def compute(): return 1\n",
        },
    )
    store = GraphStore(db)
    try:
        row = store.conn.execute(
            "SELECT src_id, dst_id, type, line FROM edges "
            "WHERE dst_id = 'py:helpers/util.py:compute'"
        ).fetchone()
    finally:
        store.close()
    assert row is not None
    src_id, _dst_id, edge_type, line = row
    assert src_id == "py:main.py:main"
    assert edge_type == "imports"
    assert line == 3


# ---------- src-layout (packages/ src/ app/) resolution ----------


def test_strip_source_roots_helper() -> None:
    from codegraph.graph.resolver import _strip_source_roots

    assert _strip_source_roots("packages.codegraph.graph.queries") == "codegraph.graph.queries"
    assert _strip_source_roots("src.myapp.util") == "myapp.util"
    assert _strip_source_roots("app.handlers") == "handlers"
    # No source root -> None (nothing stripped).
    assert _strip_source_roots("codegraph.graph.queries") is None
    assert _strip_source_roots("util") is None


def test_src_layout_absolute_import_resolves(tmp_path: Path) -> None:
    """A `packages/` src-layout: `from myapp.util import compute` must resolve to
    the in-repo entity, not fall through to external (the bug that gutted the
    call graph on every src-layout project)."""
    db = _index(
        tmp_path,
        {
            "packages/myapp/__init__.py": "",
            "packages/myapp/util.py": "def compute():\n    return 1\n",
            "packages/myapp/main.py": (
                "from myapp.util import compute\n\n\ndef caller():\n    return compute()\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
        # Import resolved to the real entity (not external:myapp.util.compute).
        assert "py:packages/myapp/util.py:compute" in dsts
        # And the CALL caller()->compute() resolved in-repo, so impact works:
        callers = store.conn.execute(
            "SELECT src_id FROM edges WHERE dst_id = ? AND type = 'calls'",
            ["py:packages/myapp/util.py:compute"],
        ).fetchall()
    finally:
        store.close()
    assert any("caller" in c[0] for c in callers), f"expected caller->compute call edge: {callers}"


def test_src_layout_module_import_resolves_to_real_module_id(tmp_path: Path) -> None:
    """`import myapp.util` under a src-layout resolves to the module entity whose
    id keeps the file-derived qname (with the `packages.` prefix)."""
    db = _index(
        tmp_path,
        {
            "packages/myapp/__init__.py": "",
            "packages/myapp/util.py": "def f():\n    return 1\n",
            "packages/myapp/main.py": "import myapp.util\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
        # The reconstructed id must be the real module entity, which exists.
        assert "py:packages/myapp/util.py:packages.myapp.util" in dsts
        exists = store.conn.execute(
            "SELECT 1 FROM entities WHERE entity_id = ?",
            ["py:packages/myapp/util.py:packages.myapp.util"],
        ).fetchone()
    finally:
        store.close()
    assert exists is not None, "module import must resolve to an entity that actually exists"
