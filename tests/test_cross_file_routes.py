"""Tests for Phase 21 -- cross-file route-handler resolution and
cross-language HTTP edges.

Phase 20's framework resolvers document a same-file-only limitation for
Express/Django/Rails (the far more common real-world shape is the handler
living in a different file), and this project's own README called out
cross-language HTTP edges as deliberately deferred. Both close here: a
provisional `route:?handler:name` edge is resolved against every file's
entities repo-wide, and a frontend fetch/axios call site is matched against
whichever backend framework registered that (method, path) as a route.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app
from codegraph.graph.store import GraphStore
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_repo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _index(runner: CliRunner, repo: Path, db: Path) -> None:
    result = runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.stdout


def _edges(db: Path) -> list[tuple[str, str, str, float]]:
    store = GraphStore(db)
    try:
        rows = store.conn.execute("SELECT src_id, dst_id, type, confidence FROM edges").fetchall()
    finally:
        store.close()
    return rows


# ---------- cross-file route-handler resolution ----------


def test_django_view_in_different_file_resolves(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "urls.py": (
                "from django.urls import path\nfrom . import views\n\n"
                'urlpatterns = [\n    path("users/", views.list_users),\n]\n'
            ),
            "views.py": "def list_users(request):\n    return []\n",
        },
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)

    edges = _edges(db)
    assert any(
        src == "route:ANY /users" and "views.py:list_users" in dst
        for src, dst, _type, _conf in edges
    )


def test_express_handler_in_different_file_resolves(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "routes.ts": (
                "import { listUsers } from './handlers';\napp.get(\"/users\", listUsers);\n"
            ),
            "handlers.ts": "export function listUsers(req, res) {\n    return [];\n}\n",
        },
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)

    edges = _edges(db)
    assert any(
        src == "route:GET /users" and "handlers.ts:listUsers" in dst
        for src, dst, _type, _conf in edges
    )


def test_rails_controller_in_different_file_resolves(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "routes.rb": (
                "Rails.application.routes.draw do\n  get '/users', to: 'users#index'\nend\n"
            ),
            "users_controller.rb": ("class UsersController\n  def index\n  end\nend\n"),
        },
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)

    edges = _edges(db)
    assert any(
        src == "route:GET /users" and "users_controller.rb:UsersController.index" in dst
        for src, dst, _type, _conf in edges
    )


def test_ambiguous_handler_name_not_guessed(runner: CliRunner, tmp_path: Path) -> None:
    """Two files defining a same-named function -- the route-handler pass
    must not silently pick one; it should mark it external instead."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "routes.ts": 'app.get("/x", ambiguous);\n',
            "a.ts": "export function ambiguous() {\n    return 1;\n}\n",
            "b.ts": "export function ambiguous() {\n    return 2;\n}\n",
        },
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)

    edges = _edges(db)
    route_edges = [e for e in edges if e[0] == "route:GET /x"]
    assert len(route_edges) == 1
    assert route_edges[0][1].startswith("external:")


# ---------- cross-language HTTP edges ----------


def test_ts_fetch_resolves_to_python_fastapi_handler(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "backend.py": (
                "from fastapi import FastAPI\napp = FastAPI()\n\n"
                '@app.post("/api/users")\n'
                "def create_user():\n    return {}\n"
            ),
            "frontend.ts": (
                'function submitForm() {\n    return fetch("/api/users", { method: "POST" });\n}\n'
            ),
        },
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)

    edges = _edges(db)
    assert any(
        src == "ts:frontend.ts:submitForm" and dst == "py:backend.py:create_user"
        for src, dst, _type, _conf in edges
    )


def test_ts_fetch_resolves_to_express_handler(runner: CliRunner, tmp_path: Path) -> None:
    """Cross-language AND cross-file at once: TS caller -> TS route
    registration (different file) -> TS handler."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "routes.ts": (
                "import { listUsers } from './handlers';\napp.get(\"/users\", listUsers);\n"
            ),
            "handlers.ts": "export function listUsers(req, res) {\n    return [];\n}\n",
            "client.ts": ('function loadUsers() {\n    return fetch("/users");\n}\n'),
        },
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)

    edges = _edges(db)
    assert any(
        src == "ts:client.ts:loadUsers" and "handlers.ts:listUsers" in dst
        for src, dst, _type, _conf in edges
    )


def test_fetch_with_no_matching_route_marked_external(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "client.ts": ('function loadNothing() {\n    return fetch("/no/such/route");\n}\n'),
        },
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)

    edges = _edges(db)
    fetch_edges = [e for e in edges if e[0] == "ts:client.ts:loadNothing"]
    external = [e for e in fetch_edges if e[1].startswith("external:http_route:")]
    assert external
