"""Tests for T9.1 — git-blame ownership.

Builds a throwaway git repo and sets authorship via GIT_AUTHOR_* env vars, so
nothing touches the user's global git config.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from codegraph.analysis.ownership import entity_ownership, primary_owner
from codegraph.cli import app
from typer.testing import CliRunner

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _git(repo: Path, *args: str, author: str | None = None) -> None:
    env = dict(os.environ)
    if author:
        env.update(
            {
                "GIT_AUTHOR_NAME": author,
                "GIT_AUTHOR_EMAIL": f"{author.lower()}@example.com",
                "GIT_COMMITTER_NAME": author,
                "GIT_COMMITTER_EMAIL": f"{author.lower()}@example.com",
            }
        )
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")


_FUNC = "def greet(name):\n    msg = 'hello ' + name\n    print(msg)\n    return msg\n"


def test_single_author_owns_all_lines(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    _init_repo(repo)
    (repo / "a.py").write_text(_FUNC, encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "add greet", author="Alice")

    owners = entity_ownership(repo, "a.py", 1, 4)
    assert owners == [type(owners[0])(author="Alice", lines=4)]
    assert primary_owner(owners) == "Alice"


def test_two_authors_split(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    _init_repo(repo)
    (repo / "a.py").write_text(_FUNC, encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "add greet", author="Alice")
    # Bob rewrites the last two lines.
    (repo / "a.py").write_text(
        "def greet(name):\n    msg = 'hi ' + name\n    logging.info(msg)\n    return msg.upper()\n",
        encoding="utf-8",
    )
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "tweak greet", author="Bob")

    owners = entity_ownership(repo, "a.py", 1, 4)
    by = {o.author: o.lines for o in owners}
    assert by.get("Bob", 0) >= 1 and by.get("Alice", 0) >= 1
    assert sum(by.values()) == 4
    # Owners are sorted by descending line count.
    assert [o.lines for o in owners] == sorted((o.lines for o in owners), reverse=True)


def test_not_a_git_repo_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(_FUNC, encoding="utf-8")
    assert entity_ownership(tmp_path, "a.py", 1, 4) == []


def test_untracked_file_returns_empty(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    _init_repo(repo)
    (repo / "tracked.py").write_text(_FUNC, encoding="utf-8")
    _git(repo, "add", "tracked.py")
    _git(repo, "commit", "-qm", "init", author="Alice")
    (repo / "untracked.py").write_text(_FUNC, encoding="utf-8")
    assert entity_ownership(repo, "untracked.py", 1, 4) == []


def test_bad_range_returns_empty(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    _init_repo(repo)
    (repo / "a.py").write_text(_FUNC, encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "init", author="Alice")
    assert entity_ownership(repo, "a.py", 0, 4) == []  # start < 1
    assert entity_ownership(repo, "a.py", 5, 2) == []  # end < start


# ---------- CLI ----------


def test_cli_owner_reports_author(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "r"
    _init_repo(repo)
    (repo / "a.py").write_text(_FUNC, encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "add greet", author="Alice")
    db = tmp_path / "g.duckdb"
    assert runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"]).exit_code == 0

    result = runner.invoke(app, ["owner", "greet", "--repo", str(repo), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    assert "Alice" in result.stdout
    assert "Primary owner" in result.stdout


def test_cli_owner_no_blame_data(runner: CliRunner, tmp_path: Path) -> None:
    # Index a non-git repo → entity exists but git blame has nothing.
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.py").write_text(_FUNC, encoding="utf-8")
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    result = runner.invoke(app, ["owner", "greet", "--repo", str(repo), "--db", str(db)])
    assert result.exit_code == 1
    assert "No git-blame data" in result.stdout


def test_cli_owner_unknown_entity(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "r"
    _init_repo(repo)
    (repo / "a.py").write_text(_FUNC, encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "init", author="Alice")
    db = tmp_path / "g.duckdb"
    runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    result = runner.invoke(app, ["owner", "does_not_exist", "--repo", str(repo), "--db", str(db)])
    assert result.exit_code == 1
    assert "No entity matching" in result.stdout


# ---------- robustness (Phase 28): a wedged git must not hang the caller ----------


def test_git_timeout_degrades_to_empty_list(monkeypatch, tmp_path: Path) -> None:
    """A git that never returns (network filesystem, stuck fsmonitor) must
    degrade to 'no ownership data' exactly like an untracked file, not hang."""

    def _hang(*args, **kwargs):
        assert kwargs.get("timeout") is not None  # the call must actually pass one
        raise subprocess.TimeoutExpired(cmd="git blame", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", _hang)
    assert entity_ownership(tmp_path, "any.py", 1, 10) == []
