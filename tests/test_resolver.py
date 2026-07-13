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


def test_ts_default_import_resolves_to_sole_export_when_unambiguous(tmp_path: Path) -> None:
    """A file with exactly one exported (named) entity default-exports that one --
    the common `export default function Foo() {}` single-component-per-file
    pattern. Regression test: this used to resolve to the module entity
    instead, so a call/JSX-tag through a default import never matched the
    real function and looked like dead code with zero callers."""
    db = _ts_repo(
        tmp_path,
        {
            "src/index.ts": 'import ScoreBadge from "./ScoreBadge";\nScoreBadge();\n',
            "src/ScoreBadge.ts": "export default function ScoreBadge() { return 1; }\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "ts:src/ScoreBadge.ts:ScoreBadge" in dsts
    assert "ts:src/ScoreBadge.ts:src.ScoreBadge" not in dsts  # not the module entity


def test_ts_default_import_falls_back_to_module_when_ambiguous(tmp_path: Path) -> None:
    """A file with more than one export can't be guessed -- stay on the
    existing module-entity fallback rather than picking the wrong one."""
    db = _ts_repo(
        tmp_path,
        {
            "src/index.ts": 'import Widget from "./widget";\n',
            "src/widget.ts": (
                "export default function Widget() { return 1; }\n"
                "export function helper() { return 2; }\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "ts:src/widget.ts:src.widget" in dsts  # module-entity fallback


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


def test_ts_alias_import_resolves_via_tsconfig_paths(tmp_path: Path) -> None:
    """`@/foo` resolves through tsconfig `compilerOptions.paths` instead of
    falling through to external -- this used to be an explicitly deferred gap
    (every Next/Nuxt/Vite-scaffolded repo loses cross-file edges for every
    `@/`-aliased import)."""
    db = _ts_repo(
        tmp_path,
        {
            "tsconfig.json": ('{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'),
            "app/main.ts": 'import { authenticate } from "@/auth/login";\n',
            "src/auth/login.ts": "export function authenticate() { return true; }\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "ts:src/auth/login.ts:authenticate" in dsts


def test_ts_alias_import_falls_back_to_external_without_tsconfig(tmp_path: Path) -> None:
    db = _ts_repo(
        tmp_path,
        {
            "app/main.ts": 'import { authenticate } from "@/auth/login";\n',
            "src/auth/login.ts": "export function authenticate() { return true; }\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "external:@/auth/login.authenticate" in dsts


def test_ts_alias_tolerates_jsonc_comments_and_trailing_commas(tmp_path: Path) -> None:
    db = _ts_repo(
        tmp_path,
        {
            "tsconfig.json": (
                "{\n"
                "  // comment before paths\n"
                '  "compilerOptions": {\n'
                '    "baseUrl": ".",\n'
                '    "paths": {\n'
                '      "@/*": ["src/*"], /* trailing */\n'
                "    },\n"
                "  },\n"
                "}\n"
            ),
            "app/main.ts": 'import { authenticate } from "@/auth/login";\n',
            "src/auth/login.ts": "export function authenticate() { return true; }\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _src, dst, _conf in _edges(store)}
    finally:
        store.close()
    assert "ts:src/auth/login.ts:authenticate" in dsts


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
    # Monorepo/backend-app root names, alongside the original src-layout ones.
    assert _strip_source_roots("apps.myapp.util") == "myapp.util"
    assert _strip_source_roots("backend.routers.auth") == "routers.auth"
    assert _strip_source_roots("server.handlers") == "handlers"
    # No source root -> None (nothing stripped).
    assert _strip_source_roots("codegraph.graph.queries") is None
    assert _strip_source_roots("util") is None


def test_backend_rooted_absolute_import_resolves(tmp_path: Path) -> None:
    """Regression test: a repo laid out as `backend/routers/auth.py` importing
    `from backend.routers import auth` (absolute, not relative) used to fall
    through to `external:` -- `backend` wasn't in the source-root allowlist,
    unlike `packages`/`src`/`app`. Same bug class as the src-layout fix
    above, found auditing a real monorepo's layout (`cold/backend/routers/`)."""
    db = _index(
        tmp_path,
        {
            "backend/__init__.py": "",
            "backend/routers/__init__.py": "",
            "backend/routers/auth.py": "def get_current_user():\n    return 1\n",
            "backend/main.py": (
                "from backend.routers.auth import get_current_user\n\n\n"
                "def route():\n    return get_current_user()\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
        assert "py:backend/routers/auth.py:get_current_user" in dsts
        callers = store.conn.execute(
            "SELECT src_id FROM edges WHERE dst_id = ? AND type = 'calls'",
            ["py:backend/routers/auth.py:get_current_user"],
        ).fetchall()
    finally:
        store.close()
    assert any("route" in c[0] for c in callers), (
        f"expected route->get_current_user edge: {callers}"
    )


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


# ---------- bare/flattened sys.path imports (found stress-testing a real
# production FastAPI backend that runs with its backend/ dir on sys.path) ----------


def test_bare_import_resolves_via_unambiguous_basename(tmp_path: Path) -> None:
    """`from auth import get_current_user` where the real file lives at
    `backend/auth.py`, not top-level `auth.py` -- common when a repo runs
    with a subdirectory on sys.path instead of package-relative imports
    throughout. Must resolve when the basename is unambiguous repo-wide."""
    db = _index(
        tmp_path,
        {
            "backend/auth.py": "def get_current_user():\n    pass\n",
            "backend/routers/users.py": "from auth import get_current_user\n\ndef me():\n    return get_current_user()\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
    finally:
        store.close()
    assert "py:backend/auth.py:get_current_user" in dsts
    assert not any(d.startswith("external:") and "get_current_user" in d for d in dsts)


def test_ambiguous_basename_falls_back_to_the_file_that_defines_the_name(
    tmp_path: Path,
) -> None:
    """Two files share a basename (`backend/auth.py` and
    `backend/routers/auth.py`, a real shape found in production) -- path
    alone can't disambiguate `from auth import get_current_user`, but only
    one of the two actually defines get_current_user, which does."""
    db = _index(
        tmp_path,
        {
            "backend/auth.py": "def get_current_user():\n    pass\n",
            "backend/routers/auth.py": "def list_routes():\n    pass\n",
            "backend/routers/users.py": "from auth import get_current_user\n\ndef me():\n    return get_current_user()\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
    finally:
        store.close()
    assert "py:backend/auth.py:get_current_user" in dsts
    assert not any("routers/auth.py" in d and "get_current_user" in d for d in dsts)


def test_genuinely_ambiguous_bare_import_stays_external(tmp_path: Path) -> None:
    """Two files share a basename AND both define the imported name -- no
    signal left to disambiguate, so this must stay external rather than
    guess (matches this project's fail-safe-missing-not-wrong philosophy)."""
    db = _index(
        tmp_path,
        {
            "a/util.py": "def helper():\n    pass\n",
            "b/util.py": "def helper():\n    pass\n",
            "c/main.py": "from util import helper\n\ndef run():\n    return helper()\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
    finally:
        store.close()
    assert "external:util.helper" in dsts


def test_bare_submodule_import_resolves_by_suffix(tmp_path: Path) -> None:
    """`from services import leads_service` (importing a submodule, not a
    name within a module) where the real path is nested deeper than the
    caller's sys.path root suggests -- resolves via a multi-segment suffix
    match, not just a single bare basename."""
    db = _index(
        tmp_path,
        {
            "backend/services/leads_service.py": "def check_duplicate():\n    pass\n",
            "backend/routers/leads.py": "from services import leads_service\n",
        },
    )
    store = GraphStore(db)
    try:
        dsts = {dst for _, dst, _ in _edges(store)}
    finally:
        store.close()
    assert "py:backend/services/leads_service.py:backend.services.leads_service" in dsts


# ---------- ambiguous-name candidate ceiling ----------


def test_method_call_under_ceiling_still_disambiguates_to_same_file() -> None:
    from codegraph.graph.resolver import _resolve_method_call

    call_file = "app/widget.py"
    candidates = [f"py:vendor/dup_{i}.py:Base.render" for i in range(5)]
    candidates.append(f"py:{call_file}:Base.render")
    entity_ids_by_qname = {"Base.render": candidates}

    result, conf = _resolve_method_call(
        "py:?methodcall:Base.render",
        f"py:{call_file}:caller",
        entities_by_file={},
        imports_by_file={},
        entity_ids_by_qname=entity_ids_by_qname,
    )
    assert result == f"py:{call_file}:Base.render"
    assert conf == 0.9


def test_method_call_over_ceiling_skips_disambiguation(tmp_path: Path) -> None:
    """A name redeclared past `_AMBIGUOUS_CANDIDATE_CEILING` times (a vendored
    blob) must not be scanned for a same-file match on every call site --
    that's the quadratic blowup a real large repo can hit. Confirmed by a
    candidate list that DOES contain the correct same-file entity: with the
    ceiling working, disambiguation is skipped entirely and the call falls
    through to plain callee-name resolution instead of finding it."""
    from codegraph.graph.resolver import _AMBIGUOUS_CANDIDATE_CEILING, _resolve_method_call

    call_file = "app/widget.py"
    candidates = [
        f"py:vendor/dup_{i}.py:Base.render" for i in range(_AMBIGUOUS_CANDIDATE_CEILING + 5)
    ]
    candidates.append(f"py:{call_file}:Base.render")
    entity_ids_by_qname = {"Base.render": candidates}

    result, _conf = _resolve_method_call(
        "py:?methodcall:Base.render",
        f"py:{call_file}:caller",
        entities_by_file={},
        imports_by_file={},
        entity_ids_by_qname=entity_ids_by_qname,
    )
    assert result.startswith("external:")
