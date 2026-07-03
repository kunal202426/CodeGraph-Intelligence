"""Tests for Phase 22 -- git hook fallback for `codegraph watch`."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.cli import app
from codegraph.installer import registry as _registry
from codegraph.sync.git_hooks import (
    HOOK_NAMES,
    has_git_hooks,
    install_git_hooks,
    uninstall_git_hooks,
)
from typer.testing import CliRunner


@pytest.fixture
def patched_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Replace the 'claude' target with a tmp_path-backed config, so `init`
    never touches the real ~/.claude.json. Mirrors test_cli_init.py."""
    from codegraph.installer.targets.claude_code import ClaudeCodeTarget

    t = ClaudeCodeTarget()
    cfg = tmp_path / ".claude.json"
    monkeypatch.setattr(t, "global_config_path", lambda: cfg)
    monkeypatch.setattr(t, "local_config_path", lambda: tmp_path / ".mcp.json")
    orig = _registry._REGISTRY.copy()
    _registry._REGISTRY["claude"] = t
    yield t, cfg
    _registry._REGISTRY.clear()
    _registry._REGISTRY.update(orig)


def _init_git_repo(root: Path) -> Path:
    """A bare-minimum fake git repo: just `.git/hooks/`, no real git binary needed."""
    repo = root / "repo"
    (repo / ".git" / "hooks").mkdir(parents=True)
    return repo


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------- install_git_hooks / uninstall_git_hooks / has_git_hooks ----------


def test_install_not_a_git_repo_returns_empty(tmp_path: Path) -> None:
    repo = tmp_path / "not_a_repo"
    repo.mkdir()
    assert install_git_hooks(repo) == []
    assert has_git_hooks(repo) is False


def test_install_writes_all_three_hooks(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    touched = install_git_hooks(repo)
    assert {p.name for p in touched} == set(HOOK_NAMES)
    for name in HOOK_NAMES:
        content = (repo / ".git" / "hooks" / name).read_text(encoding="utf-8")
        assert content.startswith("#!/bin/sh")
        assert "codegraph index" in content


def test_install_is_idempotent(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    install_git_hooks(repo)
    install_git_hooks(repo)
    content = (repo / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")
    assert content.count("codegraph index") == 1


def test_install_preserves_existing_hook_content(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho existing-hook-line\n", encoding="utf-8")

    install_git_hooks(repo)

    content = hook.read_text(encoding="utf-8")
    assert "echo existing-hook-line" in content
    assert "codegraph index" in content


def test_has_git_hooks_true_after_install(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    assert has_git_hooks(repo) is False
    install_git_hooks(repo)
    assert has_git_hooks(repo) is True


def test_uninstall_removes_snippet(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    install_git_hooks(repo)
    touched = uninstall_git_hooks(repo)
    assert {p.name for p in touched} == set(HOOK_NAMES)
    assert has_git_hooks(repo) is False


def test_uninstall_deletes_hook_left_with_only_a_shebang(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    install_git_hooks(repo)
    uninstall_git_hooks(repo)
    # Nothing but our snippet was ever in these hooks, so they should be gone
    # entirely rather than left as dead bare-shebang files.
    for name in HOOK_NAMES:
        assert not (repo / ".git" / "hooks" / name).exists()


def test_uninstall_preserves_other_hook_content(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho existing-hook-line\n", encoding="utf-8")
    install_git_hooks(repo)

    uninstall_git_hooks(repo)

    content = hook.read_text(encoding="utf-8")
    assert "echo existing-hook-line" in content
    assert "codegraph index" not in content


def test_uninstall_does_not_touch_unrelated_hooks(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    other_hook = repo / ".git" / "hooks" / "pre-push"
    other_hook.write_text("#!/bin/sh\necho unrelated\n", encoding="utf-8")
    install_git_hooks(repo)

    uninstall_git_hooks(repo)

    assert other_hook.read_text(encoding="utf-8") == "#!/bin/sh\necho unrelated\n"


def test_uninstall_no_hooks_returns_empty(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    assert uninstall_git_hooks(repo) == []


# ---------- CLI: `codegraph hooks install` / `codegraph hooks uninstall` ----------


def test_cli_hooks_install(runner: CliRunner, tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    result = runner.invoke(app, ["hooks", "install", str(repo)])
    assert result.exit_code == 0, result.output
    assert "Installed" in result.output
    assert has_git_hooks(repo) is True


def test_cli_hooks_install_not_a_git_repo(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()
    result = runner.invoke(app, ["hooks", "install", str(repo)])
    assert result.exit_code == 1
    assert "not" in result.output.lower()


def test_cli_hooks_uninstall(runner: CliRunner, tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    install_git_hooks(repo)
    result = runner.invoke(app, ["hooks", "uninstall", str(repo)])
    assert result.exit_code == 0, result.output
    assert "Removed" in result.output
    assert has_git_hooks(repo) is False


def test_cli_hooks_uninstall_nothing_to_remove(runner: CliRunner, tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    result = runner.invoke(app, ["hooks", "uninstall", str(repo)])
    assert result.exit_code == 0
    assert "No CodeGraph" in result.output


# ---------- `codegraph init --install-hooks` ----------


def test_init_install_hooks_flag(runner: CliRunner, tmp_path: Path, patched_claude) -> None:
    repo = tmp_path / "proj"
    (repo / ".git" / "hooks").mkdir(parents=True)
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    result = runner.invoke(
        app, ["init", str(repo), "--no-embed", "--install-hooks", "--target", "claude"]
    )
    assert result.exit_code == 0, result.output
    assert has_git_hooks(repo) is True


def test_init_without_install_hooks_flag_skips_hooks(
    runner: CliRunner, tmp_path: Path, patched_claude
) -> None:
    repo = tmp_path / "proj"
    (repo / ".git" / "hooks").mkdir(parents=True)
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(repo), "--no-embed", "--target", "claude"])
    assert result.exit_code == 0, result.output
    assert has_git_hooks(repo) is False
