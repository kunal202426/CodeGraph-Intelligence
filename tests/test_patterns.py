"""Tests for T9.3 — layered-architecture analysis."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.analysis.patterns import analyze_layers, classify_layer
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
    assert runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code == 0


# api (presentation) -> services (service) -> models (data) is healthy.
# models -> api is a violation (data importing presentation).
_LAYERED = {
    "api/__init__.py": "",
    "services/__init__.py": "",
    "models/__init__.py": "",
    "api/routes.py": "from services.logic import handle\n\n\ndef route():\n    return handle()\n",
    "services/logic.py": "from models.user import User\n\n\ndef handle():\n    return User()\n",
    "models/user.py": "from api.routes import route\n\n\nclass User:\n    pass\n",
}


# ---------- classify_layer ----------


def test_classify_layer_keywords() -> None:
    assert classify_layer("api") == "presentation"
    assert classify_layer("controllers") == "presentation"
    assert classify_layer("routers") == "presentation"  # FastAPI convention
    assert classify_layer("services") == "service"
    assert classify_layer("models") == "data"
    assert classify_layer("repository") == "data"
    assert classify_layer("utils") == "other"
    assert classify_layer(".") == "other"


# ---------- analyze_layers ----------


def test_layers_detected(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _LAYERED)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        report = analyze_layers(store.conn)
    finally:
        store.close()
    assert "api" in report.layers_present["presentation"]
    assert "services" in report.layers_present["service"]
    assert "models" in report.layers_present["data"]


def test_layers_detected_when_nested_under_app_dir(runner: CliRunner, tmp_path: Path) -> None:
    """Regression test: layer dirs nested under a project/workspace folder
    (`app/backend/routers/...`) must be found, not just ones at the repo
    root -- the common case for a monorepo with multiple sub-apps."""
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "app/backend/routers/__init__.py": "",
            "app/backend/services/__init__.py": "",
            "app/backend/routers/auth.py": (
                "from app.backend.services.logic import handle\n\n\n"
                "def route():\n    return handle()\n"
            ),
            "app/backend/services/logic.py": "def handle():\n    return 1\n",
        },
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        report = analyze_layers(store.conn)
    finally:
        store.close()
    assert "app/backend/routers" in report.layers_present["presentation"]
    assert "app/backend/services" in report.layers_present["service"]


def test_violation_flagged(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _LAYERED)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        report = analyze_layers(store.conn)
    finally:
        store.close()
    # The only violation is models/user.py -> api/routes.py (data -> presentation).
    assert any(v.src_layer == "data" and v.dst_layer == "presentation" for v in report.violations)
    # Healthy downward flows are not violations.
    assert all(
        not (v.src_layer == "presentation" and v.dst_layer == "service") for v in report.violations
    )


def test_no_violations_when_only_downward(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(
        repo,
        {
            "api/__init__.py": "",
            "services/__init__.py": "",
            "api/routes.py": "from services.logic import handle\n\n\ndef route():\n    return handle()\n",
            "services/logic.py": "def handle():\n    return 1\n",
        },
    )
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    store = GraphStore(db)
    try:
        report = analyze_layers(store.conn)
    finally:
        store.close()
    assert report.violations == []
    assert report.flows.get(("presentation", "service"), 0) >= 1


# ---------- CLI ----------


def test_cli_layers_reports_violation(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, _LAYERED)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    result = runner.invoke(app, ["layers", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "Layers detected" in result.stdout
    assert "violation" in result.stdout.lower()


def test_cli_layers_none_detected(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo, {"utils/helpers.py": "def h():\n    return 1\n", "utils/__init__.py": ""})
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)
    result = runner.invoke(app, ["layers", "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "No recognizable layers" in result.stdout


def test_cli_layers_missing_db(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["layers", "--db", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1
    assert "No graph database" in result.stdout
