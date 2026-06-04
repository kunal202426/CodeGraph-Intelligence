"""Tests for T12.4 — context, trace, and status CLI subcommands."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from codegraph.cli import app
from typer.testing import CliRunner

SAMPLE_REPO = Path("tests/fixtures/sample_repo_py")
runner = CliRunner()


def _plain(text: str) -> str:
    """Strip ANSI escape codes for cross-platform string assertions."""
    return re.sub(r"\x1b\[[0-9;]*[mK]", "", text)


@pytest.fixture
def indexed_db(tmp_path: Path) -> Path:
    db = tmp_path / "g.duckdb"
    result = runner.invoke(app, ["index", str(SAMPLE_REPO), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.output
    return db


# ------------------------------------------------------------------ context --


def test_context_returns_results(indexed_db: Path) -> None:
    result = runner.invoke(app, ["context", "authenticate", "--db", str(indexed_db)])
    assert result.exit_code == 0, result.output
    assert "authenticate" in _plain(result.output)


def test_context_shows_column_headers(indexed_db: Path) -> None:
    result = runner.invoke(app, ["context", "authenticate", "--db", str(indexed_db)])
    assert result.exit_code == 0
    out = _plain(result.output)
    # Type and Name are short columns reliably present even in narrow terminals.
    assert "Type" in out and "Name" in out


def test_context_no_match(indexed_db: Path) -> None:
    result = runner.invoke(app, ["context", "zzz_not_a_real_symbol_9999", "--db", str(indexed_db)])
    assert result.exit_code == 0
    assert "No results" in _plain(result.output)


def test_context_missing_db(tmp_path: Path) -> None:
    result = runner.invoke(app, ["context", "foo", "--db", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1


def test_context_limit_clamps(indexed_db: Path) -> None:
    # --limit 1 should return at most 1 entity (table title says "1 entity").
    result = runner.invoke(
        app, ["context", "authenticate", "--limit", "1", "--db", str(indexed_db)]
    )
    assert result.exit_code == 0
    assert "1 entity" in _plain(result.output)


# ------------------------------------------------------------------ trace --


def _get_auth_and_caller(indexed_db: Path) -> tuple[str, str]:
    """Return (auth_entity_id, caller_entity_id) from the fixture."""
    from codegraph.graph.queries import hybrid_search
    from codegraph.graph.store import GraphStore

    with GraphStore(indexed_db) as store:
        hits = hybrid_search(store.conn, "authenticate", None, limit=10)
        auth_id = next(h.entity_id for h in hits if h.name == "authenticate")
        callers = store.conn.execute(
            "SELECT DISTINCT src_id FROM edges WHERE dst_id = ? AND type = 'calls'",
            [auth_id],
        ).fetchall()
    assert callers, "fixture must have a caller of authenticate"
    return auth_id, callers[0][0]


def test_trace_direct_path(indexed_db: Path) -> None:
    """Direct caller -> authenticate should be found in 1 hop."""
    auth_id, caller_id = _get_auth_and_caller(indexed_db)
    result = runner.invoke(app, ["trace", caller_id, auth_id, "--db", str(indexed_db)])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert auth_id in out
    assert "1 hop" in out


def test_trace_same_entity_zero_hops(indexed_db: Path) -> None:
    auth_id, _ = _get_auth_and_caller(indexed_db)
    result = runner.invoke(app, ["trace", auth_id, auth_id, "--db", str(indexed_db)])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "0 hop" in out or "Same entity" in out


def test_trace_not_found_exits_1(indexed_db: Path) -> None:
    result = runner.invoke(
        app, ["trace", "py:nope.py:ghost", "py:nope.py:other", "--db", str(indexed_db)]
    )
    assert result.exit_code == 1
    assert "No call path" in _plain(result.output)


def test_trace_missing_db(tmp_path: Path) -> None:
    result = runner.invoke(app, ["trace", "x", "y", "--db", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1


# ------------------------------------------------------------------ status --


def test_status_shows_all_keys(indexed_db: Path) -> None:
    result = runner.invoke(app, ["status", "--db", str(indexed_db)])
    assert result.exit_code == 0
    out = _plain(result.output)
    for key in ("Files", "Entities", "Edges", "Embedded", "Staleness"):
        assert key in out, f"missing key: {key}"


def test_status_nonzero_counts(indexed_db: Path) -> None:
    result = runner.invoke(app, ["status", "--db", str(indexed_db)])
    assert result.exit_code == 0
    out = _plain(result.output)
    # The sample fixture has real entities; at least one digit should appear.
    assert any(c.isdigit() for c in out)


def test_status_missing_db(tmp_path: Path) -> None:
    result = runner.invoke(app, ["status", "--db", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1


def test_status_shows_database_key(indexed_db: Path) -> None:
    result = runner.invoke(app, ["status", "--db", str(indexed_db)])
    assert result.exit_code == 0
    # The "Database" row label must always appear regardless of terminal width.
    assert "Database" in _plain(result.output)
