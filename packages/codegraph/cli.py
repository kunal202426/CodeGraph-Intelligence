"""Typer CLI entry point. Commands are stubs until their target phase populates them."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from codegraph import __version__

app = typer.Typer(
    name="codegraph",
    help="Local AI memory layer for codebases — graph + semantic search + GraphRAG + MCP.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

DEFAULT_DB = Path(".codegraph/graph.duckdb")


def _stub(name: str, lands_at: str) -> None:
    console.print(
        f"[yellow]codegraph {name}[/yellow] is not implemented yet — lands at [bold]{lands_at}[/bold]."
    )
    console.print("Run `codegraph --help` to see all commands and their target phases.")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"codegraph {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """Local AI memory layer for codebases."""


@app.command()
def index(
    repo: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Repository root to index.",
    ),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """Index a repository into the graph database. [T1.7]"""
    _stub("index", "T1.7")


@app.command()
def search(
    query: str = typer.Argument(
        ..., help="Search query — name substring, docstring text, or natural language."
    ),
    semantic: bool = typer.Option(
        False, "--semantic", help="Vector search only (no literal). [T3.4]"
    ),
    hybrid: bool = typer.Option(
        True, "--hybrid/--no-hybrid", help="Hybrid literal+vector with RRF fusion (default). [T3.4]"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results."),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """Search the indexed codebase. [T1.8 literal / T3.4 hybrid]"""
    _stub("search", "T1.8 (literal) / T3.4 (semantic)")


@app.command()
def ask(
    query: str = typer.Argument(..., help="Natural-language question about the codebase."),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """Ask a natural-language question. Streams a grounded answer via GraphRAG. [T5.4]"""
    _stub("ask", "T5.4")


@app.command()
def impact(
    entity: str = typer.Argument(..., help="Entity ID or name to analyze."),
    depth: int = typer.Option(3, "--depth", "-d", help="Max BFS depth over reverse-call edges."),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """Show blast radius — which entities would break if this one changes. [T4.3]"""
    _stub("impact", "T4.3")


@app.command()
def cycles(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """List import cycles (strongly connected components of size >= 2). [T4.4]"""
    _stub("cycles", "T4.4")


@app.command()
def smells(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """Detect code smells: god-class, high coupling, complex functions. [T4.5]"""
    _stub("smells", "T4.5")


@app.command()
def summarize(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
    out: Path = typer.Option(Path(".codegraph/SUMMARY.md"), "--out", help="Output markdown path."),
) -> None:
    """Generate an AI architecture summary of the repo via multi-pass GraphRAG. [T5.5]"""
    _stub("summarize", "T5.5")


@app.command()
def serve(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    dev: bool = typer.Option(
        False, "--dev", help="Assume Vite dev server is running; skip frontend bundle."
    ),
) -> None:
    """Start FastAPI server + open browser to the web UI. [T6.6]"""
    _stub("serve", "T6.6")


if __name__ == "__main__":
    app()
