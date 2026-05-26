"""Typer CLI entry point. Commands populated by their target phase."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.tree import Tree

from codegraph import __version__
from codegraph.graph.queries import (
    CallerNode,
    DepNode,
    DepTree,
    ImpactTree,
    find_callers,
    find_dependencies,
    find_entity_by_name_or_id,
    hybrid_search,
)
from codegraph.graph.resolver import resolve_symbols
from codegraph.graph.store import GraphStore
from codegraph.parsers.python import PythonParser
from codegraph.parsers.typescript import TypeScriptParser
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

# Map Language → parser instance. Parsers are stateless; one instance each.
# TypeScriptParser handles TS / TSX / JS / JSX via per-file grammar selection.
_TS_PARSER = TypeScriptParser()
_LANGUAGE_PARSERS = {
    Language.PYTHON: PythonParser(),
    Language.TYPESCRIPT: _TS_PARSER,
    Language.JAVASCRIPT: _TS_PARSER,
}


def _stub(name: str, lands_at: str) -> None:
    console.print(
        f"[yellow]codegraph {name}[/yellow] is not implemented yet — lands at [bold]{lands_at}[/bold]."
    )
    console.print("Run `codegraph --help` to see all commands and their target phases.")


def _embed_changed(store: GraphStore, batch_size: int = 256) -> tuple[int, str | None]:
    """(Re-)embed entities whose embedding input changed (T3.5).

    An entity needs embedding when it has no vector yet OR its stored
    `embedding_hash` differs from the hash of its freshly-built embedding input.
    This makes embeddings self-healing: editing a docstring re-embeds just that
    entity, and changing the `build_embed_input` recipe re-embeds everything,
    while an unchanged re-index re-embeds nothing.

    Returns (count_reembedded, error_message). Model load/encode failures are
    returned (not raised) so indexing still succeeds with literal search.
    """
    from codegraph.embeddings.chunking import build_embed_input_from_fields, embed_input_hash

    rows = store.conn.execute(
        "SELECT entity_id, type, qualified_name, signature, docstring, raw_source, "
        "embedding_hash, embedding IS NOT NULL "
        "FROM entities"
    ).fetchall()

    # (entity_id, embed_input_text, input_hash) for entities that need (re-)embedding.
    pending: list[tuple[str, str, str]] = []
    for eid, etype, qname, sig, doc, raw, stored_hash, has_embedding in rows:
        text = build_embed_input_from_fields(etype, qname, sig, doc, raw)
        input_hash = embed_input_hash(text)
        if not has_embedding or stored_hash != input_hash:
            pending.append((eid, text, input_hash))

    if not pending:
        return 0, None

    try:
        from codegraph.embeddings.pipeline import embed_batch
    except Exception as exc:  # noqa: BLE001 - import/torch failure → skip
        return 0, f"{type(exc).__name__}: {exc}"

    embedded = 0
    progress_cols = (
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    )
    try:
        with Progress(*progress_cols, console=console, transient=True) as progress:
            task = progress.add_task("Embedding", total=len(pending))
            for start in range(0, len(pending), batch_size):
                chunk = pending[start : start + batch_size]
                vectors = embed_batch([c[1] for c in chunk])
                store.update_embeddings(
                    [(chunk[i][0], vectors[i].tolist(), chunk[i][2]) for i in range(len(chunk))]
                )
                embedded += len(chunk)
                progress.advance(task, len(chunk))
    except Exception as exc:  # noqa: BLE001 - model download/encode failure mid-run
        return embedded, f"{type(exc).__name__}: {exc}"
    return embedded, None


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
    no_embed: bool = typer.Option(
        False,
        "--no-embed",
        help="Skip computing semantic embeddings (faster, literal search only).",
    ),
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
            prev_hash = store.get_file_hash(rel_path)

            # T2.3: skip re-parse when hash hasn't changed.
            if prev_hash == current_hash:
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

            # Drop stale rows only when the file was indexed before (changed
            # content). On first index there is nothing to clear — skipping the
            # DELETE scans avoids O(files * edges) work on a cold index.
            if prev_hash is not None:
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

    # Semantic embeddings (T3.3/T3.5): (re-)embed entities whose input changed.
    embedded = 0
    embed_error: str | None = None
    if not no_embed:
        embedded, embed_error = _embed_changed(store)

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
    if not no_embed and not embed_error:
        if embedded:
            console.print(f"[dim]Embedded {embedded} entities for semantic search.[/dim]")
        else:
            console.print("[dim]Embeddings up to date (0 re-embedded).[/dim]")
    if embed_error:
        console.print(
            f"[yellow]Embeddings skipped ({embed_error}). "
            f"Literal search still works; re-run to add semantic search.[/yellow]"
        )
    if skipped_lang:
        console.print(f"[dim]Skipped {skipped_lang} files with unsupported languages.[/dim]")
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
    """Search the indexed codebase. Default is hybrid literal + semantic. [T3.4]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    # Mode: --semantic (vector only) > --no-hybrid (literal only) > default (hybrid).
    mode = "semantic" if semantic else ("literal" if not hybrid else "hybrid")

    query_vector: list[float] | None = None
    if mode in ("semantic", "hybrid"):
        try:
            from codegraph.embeddings.pipeline import embed_one

            query_vector = embed_one(query).tolist()
        except Exception as exc:  # noqa: BLE001 - model unavailable → degrade to literal
            console.print(
                f"[yellow]Semantic search unavailable ({type(exc).__name__}); "
                f"using literal search.[/yellow]"
            )
            mode = "literal"
            query_vector = None

    # All three modes route through hybrid_search; the args decide which
    # retrievers actually run (empty text skips literal, None vector skips vector).
    text_arg = "" if mode == "semantic" else query
    with GraphStore(db) as store:
        hits = hybrid_search(store.conn, text_arg, query_vector, limit=limit)
        # Likely indexed with --no-embed: no vectors to search semantically.
        if (
            mode == "semantic"
            and query_vector is not None
            and not hits
            and store.count_embedded() == 0
        ):
            console.print(
                "[yellow]No embeddings in this index. Re-run "
                "[bold]codegraph index[/bold] without --no-embed.[/yellow]"
            )
            return

    if not hits:
        console.print(f"[yellow]No results for {query!r}.[/yellow]")
        return

    table = Table(title=f"Results for [bold]{query}[/bold]  ({mode}, {len(hits)} match)")
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold", no_wrap=True)  # keep names intact (no wrap)
    table.add_column("Location", style="dim", no_wrap=True)
    table.add_column("Via", style="magenta", no_wrap=True)
    table.add_column("Doc", overflow="fold", max_width=50)

    for hit in hits:
        loc = f"{hit.file}:{hit.start_line}"
        via = "+".join(hit.retrievers)
        doc = (hit.docstring or "").split("\n", 1)[0].strip()
        table.add_row(hit.type, hit.name, loc, via, doc)

    console.print(table)


def _emit(text: str) -> None:
    """Write a streamed chunk to stdout, tolerating legacy console encodings.

    Model output may contain characters the Windows cp1252 console can't encode;
    replace them rather than crash mid-stream. Goes through sys.stdout (which the
    test runner captures) and bypasses Rich markup so `[entity_id]` citations
    aren't mistaken for style tags.
    """
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        sys.stdout.write(text.encode(enc, errors="replace").decode(enc))
    sys.stdout.flush()


@app.command()
def ask(
    query: str = typer.Argument(..., help="Natural-language question about the codebase."),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
    k: int = typer.Option(15, "--k", help="Number of entities to retrieve as context."),
    max_tokens: int = typer.Option(2000, "--max-tokens", help="Max answer length (tokens)."),
) -> None:
    """Ask a natural-language question. Streams a grounded answer via GraphRAG. [T5.4]"""
    from codegraph.ai.graphrag import GraphRAG
    from codegraph.ai.llm import LLM, LLMError

    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    with GraphStore(db) as store:
        if store.count_embedded() == 0:
            console.print(
                "[yellow]This index has no embeddings, which GraphRAG needs to find "
                "relevant code. Re-run [bold]codegraph index[/bold] without --no-embed.[/yellow]"
            )
            raise typer.Exit(code=1)

        rag = GraphRAG(store, LLM())
        printed = False
        try:
            for token in rag.ask_stream(query, k=k, max_tokens=max_tokens):
                _emit(token)
                printed = True
        except LLMError as exc:
            if printed:
                _emit("\n")
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        except Exception as exc:  # noqa: BLE001 - embedding/model failure → friendly message
            if printed:
                _emit("\n")
            console.print(f"[red]Could not answer ({type(exc).__name__}): {exc}[/red]")
            raise typer.Exit(code=1) from exc

    if printed:
        _emit("\n")


@app.command()
def deps(
    entity: str = typer.Argument(
        ..., help="Entity name, qualified_name, or entity_id to trace dependencies from."
    ),
    depth: int = typer.Option(3, "--depth", "-d", help="Max BFS depth over imports + calls."),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """Show transitive imports + calls outgoing from <entity>. [T2.6]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    with GraphStore(db) as store:
        hits = find_entity_by_name_or_id(store.conn, entity)
        if not hits:
            console.print(f"[yellow]No entity matching {entity!r}.[/yellow]")
            raise typer.Exit(code=1)
        if len(hits) > 1:
            console.print(
                f"[yellow]{len(hits)} entities match {entity!r}. Pass an entity_id instead:[/yellow]"
            )
            for h in hits[:10]:
                console.print(f"  [dim]{h.entity_id}[/dim]  ({h.type}, {h.file}:{h.start_line})")
            raise typer.Exit(code=1)

        root_row = hits[0]
        tree_data = find_dependencies(store.conn, root_row.entity_id, depth=depth)

    root_label = (
        f"[bold]{root_row.name}[/bold] "
        f"[dim]({root_row.type}, {root_row.file}:{root_row.start_line})[/dim]"
    )
    tree = Tree(root_label)
    _add_dep_subtree(tree, root_row.entity_id, tree_data, visited={root_row.entity_id})

    if not tree_data.children:
        tree.add("[dim](no outbound imports or calls)[/dim]")

    console.print(tree)
    if tree_data.truncated:
        console.print(f"[dim]Tree truncated at depth {depth}. Use --depth to go deeper.[/dim]")


def _add_dep_subtree(
    branch: Tree,
    parent_eid: str,
    tree_data: DepTree,
    visited: set[str],
) -> None:
    for child in tree_data.children.get(parent_eid, []):
        label = _format_dep_label(child)
        if child.is_external:
            branch.add(label)
            continue
        if child.entity_id in visited:
            branch.add(f"{label}  [dim](cycle)[/dim]")
            continue
        sub = branch.add(label)
        _add_dep_subtree(sub, child.entity_id, tree_data, visited | {child.entity_id})


def _format_dep_label(node: DepNode) -> str:
    marker = f"[cyan]{node.edge_type}[/cyan]"
    if node.is_external:
        return f"{marker} [dim]{node.name}[/dim]  [dim]({node.type})[/dim]"
    loc = f"{node.file}:{node.start_line}" if node.file else "?"
    return f"{marker} [bold]{node.name}[/bold] [dim]({node.type}, {loc})[/dim]"


@app.command()
def impact(
    entity: str = typer.Argument(..., help="Entity name, qualified_name, or entity_id to analyze."),
    depth: int = typer.Option(3, "--depth", "-d", help="Max BFS depth over reverse-call edges."),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """Show blast radius — which entities would break if this one changes. [T4.3]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    with GraphStore(db) as store:
        hits = find_entity_by_name_or_id(store.conn, entity)
        if not hits:
            console.print(f"[yellow]No entity matching {entity!r}.[/yellow]")
            raise typer.Exit(code=1)
        if len(hits) > 1:
            console.print(
                f"[yellow]{len(hits)} entities match {entity!r}. Pass an entity_id instead:[/yellow]"
            )
            for h in hits[:10]:
                console.print(f"  [dim]{h.entity_id}[/dim]  ({h.type}, {h.file}:{h.start_line})")
            raise typer.Exit(code=1)

        root_row = hits[0]
        impact_data = find_callers(store.conn, root_row.entity_id, depth=depth)

    root_label = (
        f"[bold]{root_row.name}[/bold] "
        f"[dim]({root_row.type}, {root_row.file}:{root_row.start_line})[/dim]"
    )
    tree = Tree(root_label)
    _add_caller_subtree(tree, root_row.entity_id, impact_data, visited={root_row.entity_id})

    if not impact_data.callers:
        tree.add("[dim](no callers — nothing calls this entity)[/dim]")

    console.print(tree)
    summary = f"[bold]{impact_data.total}[/bold] entit{'y' if impact_data.total == 1 else 'ies'}"
    console.print(f"[dim]Blast radius: {summary} across {depth} hop(s).[/dim]")
    if impact_data.truncated:
        console.print(f"[dim]Tree truncated at depth {depth}. Use --depth to go deeper.[/dim]")


def _add_caller_subtree(
    branch: Tree,
    callee_eid: str,
    impact_data: ImpactTree,
    visited: set[str],
) -> None:
    for caller in impact_data.callers.get(callee_eid, []):
        label = _format_caller_label(caller)
        if caller.entity_id in visited:
            branch.add(f"{label}  [dim](cycle)[/dim]")
            continue
        sub = branch.add(label)
        _add_caller_subtree(sub, caller.entity_id, impact_data, visited | {caller.entity_id})


def _format_caller_label(node: CallerNode) -> str:
    marker = "[cyan]called by[/cyan]"
    loc = f"{node.file}:{node.start_line}" if node.file else "?"
    return f"{marker} [bold]{node.name}[/bold] [dim]({node.type}, {loc})[/dim]"


@app.command()
def cycles(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """List import cycles (strongly connected components of size >= 2). [T4.4]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    from codegraph.analysis.cycles import find_cycles

    with GraphStore(db) as store:
        found = find_cycles(store.conn)

    if not found:
        console.print("[green]No import cycles found.[/green]")
        return

    plural = "cycle" if len(found) == 1 else "cycles"
    console.print(f"[yellow]Found {len(found)} import {plural}:[/yellow]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="dim", no_wrap=True)
    table.add_column("Files", style="cyan", no_wrap=True)
    table.add_column("Cycle", overflow="fold")
    for i, cycle in enumerate(found, start=1):
        # Render as a closed chain to make the circularity legible.
        chain = " -> ".join(cycle) + f" -> {cycle[0]}"
        table.add_row(str(i), str(len(cycle)), chain)
    console.print(table)


@app.command()
def smells(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
    god_class: int = typer.Option(15, "--god-class", help="Max methods before god-class."),
    large_class: int = typer.Option(500, "--large-class", help="Max LOC before large-class."),
    coupling: int = typer.Option(20, "--coupling", help="Max imports before high-coupling."),
    complexity: int = typer.Option(
        15, "--complexity", help="Max cyclomatic complexity before complex-function."
    ),
) -> None:
    """Detect code smells: god-class, large-class, high coupling, complex functions. [T4.5]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    from codegraph.analysis.smells import detect_smells

    with GraphStore(db) as store:
        found = detect_smells(
            store.conn,
            god_class_methods=god_class,
            large_class_loc=large_class,
            high_coupling_imports=coupling,
            complex_function=complexity,
        )

    if not found:
        console.print("[green]No code smells detected.[/green]")
        return

    plural = "smell" if len(found) == 1 else "smells"
    console.print(f"[yellow]Found {len(found)} code {plural}:[/yellow]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Smell", style="magenta", no_wrap=True)
    table.add_column("Name", style="bold", no_wrap=True)
    table.add_column("Location", style="dim", no_wrap=True)
    table.add_column("Detail", overflow="fold")
    for s in found:
        loc = f"{s.file}:{s.line}" if s.line is not None else s.file
        table.add_row(s.kind, s.name, loc, s.detail)
    console.print(table)


@app.command()
def deadcode(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
    include_methods: bool = typer.Option(
        False, "--methods", help="Also flag methods (noisier — self.x() resolution is weak)."
    ),
) -> None:
    """List dead-code candidates: functions/classes nothing calls or imports. [T9.6]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    from codegraph.analysis.refactor import find_dead_code

    with GraphStore(db) as store:
        dead = find_dead_code(store.conn, include_methods=include_methods)

    if not dead:
        console.print("[green]No dead-code candidates found.[/green]")
        return

    plural = "candidate" if len(dead) == 1 else "candidates"
    console.print(f"[yellow]Found {len(dead)} dead-code {plural}:[/yellow]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold", no_wrap=True)
    table.add_column("Location", style="dim", no_wrap=True)
    for d in dead:
        table.add_row(d.type, d.name, f"{d.file}:{d.start_line}")
    console.print(table)
    console.print(
        "[dim]Heuristic: nothing in the indexed graph references these. "
        "Framework entrypoints, public API, and dynamic calls may be false positives.[/dim]"
    )


@app.command()
def owner(
    entity: str = typer.Argument(..., help="Entity name, qualified_name, or entity_id."),
    repo: Path = typer.Option(
        Path("."), "--repo", help="Git working tree root (must match the indexed root)."
    ),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """Show git-blame ownership for an entity's lines. [T9.1]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    from codegraph.analysis.ownership import entity_ownership

    with GraphStore(db) as store:
        hits = find_entity_by_name_or_id(store.conn, entity)
        if not hits:
            console.print(f"[yellow]No entity matching {entity!r}.[/yellow]")
            raise typer.Exit(code=1)
        if len(hits) > 1:
            console.print(
                f"[yellow]{len(hits)} entities match {entity!r}. Pass an entity_id instead:[/yellow]"
            )
            for h in hits[:10]:
                console.print(f"  [dim]{h.entity_id}[/dim]  ({h.type}, {h.file}:{h.start_line})")
            raise typer.Exit(code=1)
        row = hits[0]
        span = store.conn.execute(
            "SELECT file, start_line, end_line FROM entities WHERE entity_id = ?",
            [row.entity_id],
        ).fetchone()

    file, start_line, end_line = span
    owners = entity_ownership(repo, file, start_line, end_line)
    if not owners:
        console.print(
            f"[yellow]No git-blame data for {file}:{start_line}-{end_line}.[/yellow] "
            "Is --repo a git working tree with this file committed?"
        )
        raise typer.Exit(code=1)

    total = sum(o.lines for o in owners)
    console.print(
        f"Ownership of [bold]{row.name}[/bold] [dim]({file}:{start_line}-{end_line}, "
        f"{total} lines)[/dim]:"
    )
    table = Table(show_header=True, header_style="bold")
    table.add_column("Author", style="cyan")
    table.add_column("Lines", justify="right")
    table.add_column("%", justify="right", style="dim")
    for o in owners:
        table.add_row(o.author, str(o.lines), f"{100 * o.lines / total:.0f}%")
    console.print(table)
    console.print(f"[dim]Primary owner: [bold]{owners[0].author}[/bold].[/dim]")


@app.command()
def layers(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """Classify directories into architectural layers + flag layering violations. [T9.3]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    from codegraph.analysis.patterns import analyze_layers

    with GraphStore(db) as store:
        report = analyze_layers(store.conn)

    ranked = [
        layer for layer in ("presentation", "service", "data") if layer in report.layers_present
    ]
    if not ranked:
        console.print(
            "[yellow]No recognizable layers (api/services/models-style directories).[/yellow]"
        )
        return

    console.print("[bold]Layers detected:[/bold]")
    for layer in ranked:
        dirs = ", ".join(report.layers_present[layer])
        console.print(f"  [cyan]{layer}[/cyan]: [dim]{dirs}[/dim]")

    if report.flows:
        console.print("\n[bold]Cross-layer imports:[/bold]")
        for (src, dst), count in sorted(report.flows.items(), key=lambda kv: -kv[1]):
            arrow = "->"
            console.print(f"  {src} {arrow} {dst}  [dim]({count})[/dim]")

    if report.violations:
        console.print(
            f"\n[yellow]{len(report.violations)} layering violation(s) "
            "(a lower layer importing a higher one):[/yellow]"
        )
        table = Table(show_header=True, header_style="bold")
        table.add_column("From (lower)", style="cyan")
        table.add_column("Imports (higher)", style="magenta")
        table.add_column("Files", overflow="fold")
        for v in report.violations:
            table.add_row(v.src_layer, v.dst_layer, f"{v.src_file} -> {v.dst_file}")
        console.print(table)
    else:
        console.print("\n[green]No layering violations.[/green]")


@app.command()
def summarize(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
    out: Path = typer.Option(Path(".codegraph/SUMMARY.md"), "--out", help="Output markdown path."),
    per_dir: int = typer.Option(
        10, "--per-dir", help="Representative entities sampled per top-level directory."
    ),
) -> None:
    """Generate an AI architecture summary of the repo via multi-pass GraphRAG. [T5.5]"""
    from codegraph.ai.graphrag import GraphRAG
    from codegraph.ai.llm import LLM, LLMError

    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    with GraphStore(db) as store:
        if store.count_entities() == 0:
            console.print("[yellow]Nothing indexed yet — no entities to summarize.[/yellow]")
            raise typer.Exit(code=1)

        rag = GraphRAG(store, LLM())
        console.print("[dim]Summarizing subsystems (this calls the LLM per directory)...[/dim]")
        try:
            markdown = rag.summarize(per_dir=per_dir)
        except LLMError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        except Exception as exc:  # noqa: BLE001 - surface model/other failures cleanly
            console.print(f"[red]Could not summarize ({type(exc).__name__}): {exc}[/red]")
            raise typer.Exit(code=1) from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    console.print(f"[green]Wrote architecture summary to[/green] [bold]{out}[/bold].")


def _build_frontend() -> None:
    """Run `npm run build` in packages/web (best-effort; warn but don't fail)."""
    import subprocess

    web = Path(__file__).resolve().parents[1] / "web"
    if not (web / "package.json").exists():
        console.print(
            "[yellow]Frontend source not found; serving the existing build if present.[/yellow]"
        )
        return
    console.print("[dim]Building frontend (npm run build)...[/dim]")
    try:
        subprocess.run("npm run build", cwd=web, shell=True, check=True)  # noqa: S602,S607
    except (OSError, subprocess.CalledProcessError) as exc:
        console.print(
            f"[yellow]Frontend build failed ({exc}); serving the existing build if present.[/yellow]"
        )


@app.command()
def serve(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    dev: bool = typer.Option(
        False, "--dev", help="Assume Vite dev server is running; skip frontend bundle."
    ),
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Open a browser tab once the server starts."
    ),
) -> None:
    """Start FastAPI server + open browser to the web UI. [T6.6]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    import threading
    import webbrowser

    import uvicorn

    from codegraph.server.api import create_app

    if dev:
        url = "http://localhost:5173"
        console.print(
            "[dim]--dev: run the Vite dev server separately "
            "([bold]cd packages/web && npm run dev[/bold]); it proxies /api here.[/dim]"
        )
    else:
        _build_frontend()
        url = f"http://{host}:{port}"

    application = create_app(db)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    console.print(f"[green]Serving CodeGraph at[/green] [bold]{url}[/bold]  (Ctrl+C to stop)")
    uvicorn.run(application, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
