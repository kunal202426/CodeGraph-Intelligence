"""Smoke test: confirm the package installs and all scaffolded modules import.

Real per-module tests land at their respective tasks (T1.1 onwards).
"""

from __future__ import annotations

import importlib


def test_package_version_matches_pyproject() -> None:
    import codegraph

    assert codegraph.__version__ == "0.1.0"


def test_all_scaffolded_modules_importable() -> None:
    modules = [
        "codegraph",
        "codegraph.cli",
        "codegraph.config",
        "codegraph.uir",
        "codegraph.walker",
        "codegraph.parsers",
        "codegraph.parsers.base",
        "codegraph.parsers.python",
        "codegraph.parsers.typescript",
        "codegraph.parsers.go",
        "codegraph.parsers.rust",
        "codegraph.parsers.java",
        "codegraph.parsers.ruby",
        "codegraph.parsers.php",
        "codegraph.parsers.c_cpp",
        "codegraph.graph",
        "codegraph.graph.store",
        "codegraph.graph.queries",
        "codegraph.graph.resolver",
        "codegraph.embeddings",
        "codegraph.embeddings.pipeline",
        "codegraph.embeddings.chunking",
        "codegraph.ai",
        "codegraph.ai.llm",
        "codegraph.ai.graphrag",
        "codegraph.server",
        "codegraph.server.api",
        "codegraph.server.mcp_server",
        "codegraph.analysis",
        "codegraph.analysis.cycles",
        "codegraph.analysis.smells",
    ]
    for name in modules:
        importlib.import_module(name)


def test_cli_app_exposes_expected_commands() -> None:
    from codegraph.cli import app

    expected = {
        "index",
        "search",
        "ask",
        "deps",  # T2.6
        "impact",
        "cycles",
        "smells",
        "deadcode",  # T9.6
        "owner",  # T9.1
        "layers",  # T9.3
        "summarize",
        "serve",
    }
    # Typer stores explicit `name=` if given, else None — fall back to the function name.
    actual = {(cmd.name or cmd.callback.__name__) for cmd in app.registered_commands}
    assert actual == expected, f"unexpected CLI commands: {actual ^ expected}"
