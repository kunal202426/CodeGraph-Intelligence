"""Tests for analysis/brief.py -- the project_brief session-start summary."""

from __future__ import annotations

from pathlib import Path

from codegraph.analysis.brief import build_project_brief
from codegraph.cli import app
from codegraph.graph.store import GraphStore
from typer.testing import CliRunner


def _index(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    db = tmp_path / "graph.duckdb"
    result = CliRunner().invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.stdout
    return db


_REPO = {
    "app.py": (
        "from flask import Flask\n"
        "from services.auth import authenticate\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/login')\n"
        "def login():\n"
        "    authenticate()\n"
    ),
    "services/auth.py": "def authenticate():\n    return True\n",
    "services/other.py": (
        "from services.auth import authenticate\ndef check():\n    authenticate()\n"
    ),
    "models/user.py": "class User:\n    pass\n",
}


def test_brief_reports_file_and_entity_counts(tmp_path: Path) -> None:
    db = _index(tmp_path, _REPO)
    store = GraphStore(db)
    try:
        brief = build_project_brief(store.conn)
    finally:
        store.close()
    assert brief.file_count == 4
    assert brief.entity_count > 0


def test_brief_reports_language_breakdown(tmp_path: Path) -> None:
    db = _index(tmp_path, _REPO)
    store = GraphStore(db)
    try:
        brief = build_project_brief(store.conn)
    finally:
        store.close()
    assert brief.languages.get("python") == 4


def test_brief_classifies_layers(tmp_path: Path) -> None:
    db = _index(tmp_path, _REPO)
    store = GraphStore(db)
    try:
        brief = build_project_brief(store.conn)
    finally:
        store.close()
    assert "services" in brief.layers.get("service", [])
    assert "models" in brief.layers.get("data", [])


def test_brief_finds_hot_path_by_fan_in(tmp_path: Path) -> None:
    """`authenticate` is called from both app.py and services/other.py --
    the highest fan-in entity in this fixture, so it must appear as a hot
    path with callers == 2."""
    db = _index(tmp_path, _REPO)
    store = GraphStore(db)
    try:
        brief = build_project_brief(store.conn)
    finally:
        store.close()
    auth = next((h for h in brief.hot_paths if h.name == "authenticate"), None)
    assert auth is not None
    assert auth.callers == 2
    assert auth.file == "services/auth.py"


def test_brief_finds_flask_entry_point(tmp_path: Path) -> None:
    db = _index(tmp_path, _REPO)
    store = GraphStore(db)
    try:
        brief = build_project_brief(store.conn)
    finally:
        store.close()
    routes = {e.route: e.handler for e in brief.entry_points}
    assert routes.get("GET /login") == "login"


def test_brief_on_empty_repo_does_not_crash(tmp_path: Path) -> None:
    db = _index(tmp_path, {"a.py": "x = 1\n"})
    store = GraphStore(db)
    try:
        brief = build_project_brief(store.conn)
    finally:
        store.close()
    assert brief.file_count == 1
    assert brief.hot_paths == []
    assert brief.entry_points == []


def test_brief_caps_dirs_per_layer_and_reports_overflow(tmp_path: Path) -> None:
    # 8 distinct top-level dir names that each independently match the
    # "service" layer's keyword set -- so this produces 8 distinct dirs
    # under one layer, not 8 files under one dir.
    service_dirs = [
        "services",
        "service",
        "logic",
        "core",
        "domain",
        "usecases",
        "managers",
        "business",
    ]
    files = {f"{d}/handler.py": "def f(): pass\n" for d in service_dirs}
    db = _index(tmp_path, files)
    store = GraphStore(db)
    try:
        brief = build_project_brief(store.conn)
    finally:
        store.close()
    assert len(brief.layers.get("service", [])) <= 6
    assert brief.layer_more.get("service", 0) == len(service_dirs) - 6
