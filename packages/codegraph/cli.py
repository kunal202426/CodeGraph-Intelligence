"""Typer CLI entry point. Commands populated by their target phase."""

from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from codegraph import __version__
from codegraph.graph.queries import search_literal
from codegraph.graph.resolver import resolve_symbols
from codegraph.graph.store import GraphStore
from codegraph.parsers.python import PythonParser
from codegraph.uir import Language, hash_source
from codegraph.walker import walk

app = typer.Typer(
    name="codegraph",
    help="Local AI memory layer for codebases — graph + semantic search + GraphRAG + MCP.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

DEFAULT_DB = Path(".codegraph/graph.duckdb")

# Map Language → parser instance. PythonParser is stateless; one instance is fine.
# TypeScript parser is added at T2.4.
_LANGUAGE_PARSERS = {
    Language.PYTHON: PythonParser(),
}


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
    start = time.monotonic()
    store = GraphStore(db)
    store.init_schema()

    files = list(walk(repo))
    skipped_lang = 0
    parse_errors = 0
    parsed_files = 0
    unchanged_files = 0  # T2.3: hash matched, skipped re-parse

    progress_cols = (
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    )
    with Progress(*progress_cols, console=console, transient=True) as progress:
        task = progress.add_task("Indexing", total=len(files))
        for path, lang in files:
            parser = _LANGUAGE_PARSERS.get(lang)
            if parser is None:
                skipped_lang += 1
                progress.advance(task)
                continue

            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                console.print(f"[red]Skipping unreadable file {path}: {exc}[/red]")
                parse_errors += 1
                progress.advance(task)
                continue

            rel_path = path.relative_to(repo).as_posix()
            current_hash = hash_source(source)

            # T2.3: skip re-parse when hash hasn't changed.
            if store.get_file_hash(rel_path) == current_hash:
                unchanged_files += 1
                progress.advance(task)
                continue

            try:
                result = parser.parse(Path(rel_path), source)
            except Exception as exc:  # noqa: BLE001 - log unexpected parser errors then continue
                console.print(f"[red]Parser error on {rel_path}: {exc}[/red]")
                parse_errors += 1
                progress.advance(task)
                continue

            # Drop stale rows for this file before writing the fresh parse so
            # deleted functions / removed imports don't linger in the graph.
            store.clear_file(rel_path)
            store.upsert_file(
                path=rel_path,
                language=lang,
                hash_=current_hash,
                loc=source.count("\n") + 1,
            )
            store.upsert_entities(result.entities)
            store.upsert_edges(result.edges)
            parsed_files += 1
            progress.advance(task)

    # Cross-file symbol resolution (T2.2): rewrites `py:?:...` edges in place.
    stats = resolve_symbols(store)

    elapsed = time.monotonic() - start
    n_entities = store.count_entities()
    n_edges = store.count_edges()
    store.close()

    parse_targets = parsed_files + unchanged_files
    re_parse_clause = (
        f"Re-parsed [bold]{parsed_files}[/bold] of {parse_targets} files "
        f"([dim]{unchanged_files} unchanged[/dim])"
        if unchanged_files
        else f"Parsed [bold]{parsed_files}[/bold] files"
    )
    console.print(
        f"[green]Indexed[/green] [bold]{n_entities}[/bold] entities, "
        f"[bold]{n_edges}[/bold] edges. {re_parse_clause} in [bold]{elapsed:.1f}s[/bold]."
    )
    if stats.inspected:
        console.print(
            f"[dim]Resolved {stats.resolved}/{stats.inspected} imports; "
            f"{stats.external} external, {stats.wildcard} wildcard.[/dim]"
        )
    if skipped_lang:
        console.print(
            f"[dim]Skipped {skipped_lang} files with unsupported languages "
            f"(TypeScript lands at T2.4).[/dim]"
        )
    if parse_errors:
        console.print(f"[yellow]{parse_errors} files had errors (see above).[/yellow]")
    if parsed_files == 0 and unchanged_files == 0 and skipped_lang == 0:
        console.print("[yellow]No indexable files found.[/yellow]")


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
    # Vector + hybrid land at T3.4. Until then, fall back to literal regardless of flags.
    if semantic:
        console.print(
            "[yellow]--semantic lands at T3.4 (embeddings); falling back to literal search.[/yellow]"
        )

    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    with GraphStore(db) as store:
        hits = search_literal(store.conn, query, limit=limit)

    if not hits:
        console.print(f"[yellow]No results for {query!r}.[/yellow]")
        return

    table = Table(title=f"Results for [bold]{query}[/bold]  ({len(hits)} match)")
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Qualified", style="dim")
    table.add_column("Location", style="dim", no_wrap=True)
    table.add_column("Doc", overflow="fold", max_width=60)

    for hit in hits:
        loc = f"{hit.file}:{hit.start_line}"
        qname = hit.qualified_name if hit.qualified_name != hit.name else ""
        doc = (hit.docstring or "").split("\n", 1)[0].strip()
        table.add_row(hit.type, hit.name, qname, loc, doc)

    console.print(table)
    _ = hybrid  # silence unused-arg lint until T3.4 wires it up


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
