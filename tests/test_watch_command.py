"""Tests for T11.2 — `codegraph watch` CLI command.

All blocking tests mock RepoWatcher so the command exits immediately via
a KeyboardInterrupt raised from the first watcher.join() call.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

from codegraph.cli import app
from typer.testing import CliRunner

_RUNNER = CliRunner()


def _mock_watcher(join_side_effects=None):
    """Build a MagicMock that behaves like a short-lived RepoWatcher."""
    if join_side_effects is None:
        # First join() raises KeyboardInterrupt (simulates Ctrl-C);
        # second join(timeout=5) returns normally (shutdown cleanup).
        join_side_effects = [KeyboardInterrupt(), None]
    watcher = MagicMock()
    watcher.join.side_effect = join_side_effects
    return watcher


# --------------------------------------------------------------------------- #
# Help / argument parsing (no real watcher needed)
# --------------------------------------------------------------------------- #


def test_watch_help_exits_zero() -> None:
    result = _RUNNER.invoke(app, ["watch", "--help"])
    assert result.exit_code == 0


def test_watch_help_mentions_repo_and_db() -> None:
    result = _RUNNER.invoke(app, ["watch", "--help"])
    out = result.output.lower()
    assert "repo" in out
    assert "--db" in out


def test_watch_help_mentions_debounce_option() -> None:
    result = _RUNNER.invoke(app, ["watch", "--help"])
    assert "--debounce" in result.output


def test_watch_nonexistent_repo_exits_nonzero(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    result = _RUNNER.invoke(app, ["watch", str(missing)])
    assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# Command wiring (mocked watcher)
# --------------------------------------------------------------------------- #


def test_watch_starts_and_stops_on_keyboard_interrupt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"

    watcher = _mock_watcher()

    with patch("codegraph.sync.watcher.RepoWatcher", return_value=watcher):
        result = _RUNNER.invoke(app, ["watch", str(repo), "--db", str(db)])

    assert result.exit_code == 0
    watcher.start.assert_called_once()
    watcher.stop.assert_called_once()
    # join() called twice: once blocking, once with timeout= after stop.
    assert watcher.join.call_count == 2
    assert watcher.join.call_args_list[1] == call(timeout=5)


def test_watch_prints_watching_message(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"

    with patch("codegraph.sync.watcher.RepoWatcher", return_value=_mock_watcher()):
        result = _RUNNER.invoke(app, ["watch", str(repo), "--db", str(db)])

    # Rich may line-wrap the long path; collapse newlines before checking.
    flat = result.output.replace("\n", "")
    assert "Watching" in flat
    assert "repo" in flat


def test_watch_prints_stopped_message(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"

    with patch("codegraph.sync.watcher.RepoWatcher", return_value=_mock_watcher()):
        result = _RUNNER.invoke(app, ["watch", str(repo), "--db", str(db)])

    # Shutdown message should appear after KeyboardInterrupt.
    assert "Watcher stopped" in result.output or "Stopping" in result.output


def test_watch_warns_when_db_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"  # deliberately absent

    with patch("codegraph.sync.watcher.RepoWatcher", return_value=_mock_watcher()):
        result = _RUNNER.invoke(app, ["watch", str(repo), "--db", str(db)])

    assert "No index at" in result.output or "no index" in result.output.lower()


def test_watch_no_emit_when_db_exists(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"
    db.touch()  # DB file exists

    with patch("codegraph.sync.watcher.RepoWatcher", return_value=_mock_watcher()):
        result = _RUNNER.invoke(app, ["watch", str(repo), "--db", str(db)])

    # No "no index" warning when DB file already exists.
    assert "No index at" not in result.output


def test_watch_no_embed_flag_passes_to_watcher(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"

    watcher = _mock_watcher()
    cls_mock = MagicMock(return_value=watcher)

    with patch("codegraph.sync.watcher.RepoWatcher", cls_mock):
        _RUNNER.invoke(app, ["watch", str(repo), "--db", str(db), "--no-embed"])

    _, kwargs = cls_mock.call_args
    assert kwargs.get("no_embed") is True


def test_watch_debounce_option_passes_to_watcher(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "graph.duckdb"

    watcher = _mock_watcher()
    cls_mock = MagicMock(return_value=watcher)

    with patch("codegraph.sync.watcher.RepoWatcher", cls_mock):
        _RUNNER.invoke(app, ["watch", str(repo), "--db", str(db), "--debounce", "0.5"])

    _, kwargs = cls_mock.call_args
    assert kwargs.get("debounce_sec") == 0.5
