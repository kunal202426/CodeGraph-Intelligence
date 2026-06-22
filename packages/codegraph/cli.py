# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Typer CLI entry point. Commands populated by their target phase."""

from __future__ import annotations

import contextlib
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
    read_baseline_tokens,
)
from codegraph.graph.resolver import resolve_symbols
from codegraph.graph.store import GraphStore
from codegraph.parsers.bash import BashParser
from codegraph.parsers.c_cpp import CParser, CppParser
from codegraph.parsers.csharp import CSharpParser
from codegraph.parsers.css import CSSParser
from codegraph.parsers.elixir import ElixirParser
from codegraph.parsers.go import GoParser
from codegraph.parsers.haskell import HaskellParser
from codegraph.parsers.html import HTMLParser
from codegraph.parsers.java import JavaParser
from codegraph.parsers.julia import JuliaParser
from codegraph.parsers.kotlin import KotlinParser
from codegraph.parsers.ocaml import OCamlParser
from codegraph.parsers.php import PHPParser
from codegraph.parsers.python import PythonParser
from codegraph.parsers.r import RParser
from codegraph.parsers.ruby import RubyParser
from codegraph.parsers.rust import RustParser
from codegraph.parsers.scala import ScalaParser
from codegraph.parsers.sql import SQLParser
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
    Language.GO: GoParser(),
    Language.RUST: RustParser(),
    Language.JAVA: JavaParser(),
    Language.RUBY: RubyParser(),
    Language.PHP: PHPParser(),
    Language.C: CParser(),
    Language.CPP: CppParser(),
    Language.KOTLIN: KotlinParser(),
    Language.CSHARP: CSharpParser(),
    Language.SCALA: ScalaParser(),
    Language.BASH: BashParser(),
    Language.ELIXIR: ElixirParser(),
    Language.R: RParser(),
    Language.JULIA: JuliaParser(),
    Language.HASKELL: HaskellParser(),
    Language.OCAML: OCamlParser(),
    Language.HTML: HTMLParser(),
    Language.CSS: CSSParser(),
    Language.SQL: SQLParser(),
}


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
        "summary, embedding_hash, embedding IS NOT NULL "
        "FROM entities"
    ).fetchall()

    # (entity_id, embed_input_text, input_hash) for entities that need (re-)embedding.
    pending: list[tuple[str, str, str]] = []
    for eid, etype, qname, sig, doc, raw, summary, stored_hash, has_embedding in rows:
        text = build_embed_input_from_fields(etype, qname, sig, doc, raw, summary)
        input_hash = embed_input_hash(text)
        if not has_embedding or stored_hash != input_hash:
            pending.append((eid, text, input_hash))

    if not pending:
        return 0, None

    try:
        from codegraph.embeddings.pipeline import embed_batch, model_is_cached
    except Exception as exc:  # noqa: BLE001 - import/torch failure → skip
        return 0, f"{type(exc).__name__}: {exc}"

    # First-run legibility: the model is a ~80 MB download that otherwise begins
    # silently, making `index` look frozen. Tell the user before it starts.
    if not model_is_cached():
        console.print(
            "[dim]Downloading embedding model (~80 MB, first run only)... "
            "this can take a minute.[/dim]"
        )

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
        # Network/SSL failures are the common first-run culprit (corporate proxies,
        # offline). Point the user at the offline path so they aren't stuck.
        low = embed_error.lower()
        if any(k in low for k in ("ssl", "connection", "network", "timeout", "proxy", "http")):
            console.print(
                "[dim]Looks network-related. To work fully offline, run "
                "[bold]codegraph index <repo> --no-embed[/bold] (literal search only).[/dim]"
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

    # Staleness check (T11.3): warn if any source files changed since last index.
    try:
        from codegraph.sync.watcher import count_stale_files

        stale = count_stale_files(Path("."), db)
        if stale > 0:
            noun = "file" if stale == 1 else "files"
            console.print(
                f"[yellow]Warning: {stale} {noun} changed since last index.[/yellow] "
                "Run [bold]codegraph index <repo>[/bold] or "
                "[bold]codegraph watch <repo>[/bold] to keep the index current."
            )
    except Exception:  # noqa: BLE001 — staleness check is best-effort
        pass

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
    # Ctrl+C: uvicorn already tears down the server; suppress the resulting
    # KeyboardInterrupt so it exits cleanly instead of unwinding as a traceback.
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(application, host=host, port=port, log_level="warning")
    console.print("Server stopped.")


@app.command()
def context(
    query: str = typer.Argument(..., help="Search query or symbol name."),
    limit: int = typer.Option(5, "--limit", "-n", help="Max entities to return (1-10)."),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """Retrieve context: hybrid search + callers/callees in one shot. [T12.4]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    limit = max(1, min(limit, 10))
    query_vector: list[float] | None = None

    with GraphStore(db) as store:
        if store.count_embedded() > 0:
            try:
                from codegraph.embeddings.pipeline import embed_one

                query_vector = embed_one(query).tolist()
            except Exception:  # noqa: BLE001
                pass
        hits = hybrid_search(store.conn, query, query_vector, limit=limit)

        if not hits:
            console.print(f"[yellow]No results for {query!r}.[/yellow]")
            return

        table = Table(
            title=f"Context for [bold]{query}[/bold]  "
            f"({len(hits)} entit{'y' if len(hits) == 1 else 'ies'})",
            show_header=True,
            header_style="bold",
        )
        table.add_column("Type", style="cyan", no_wrap=True)
        table.add_column("Name", style="bold", no_wrap=True)
        table.add_column("Location", style="dim", no_wrap=True)
        table.add_column("Via", style="magenta", no_wrap=True)
        table.add_column("Callers", justify="right")
        table.add_column("Callees", justify="right")
        table.add_column("Doc", overflow="fold", max_width=40)

        from codegraph.ai.tokens import estimate_tokens

        files_seen: list[str] = []
        returned_tokens = 0
        for hit in hits:
            eid = hit.entity_id
            row = store.conn.execute(
                "SELECT type, name, file, start_line, signature, docstring "
                "FROM entities WHERE entity_id = ?",
                [eid],
            ).fetchone()
            if row is None:
                continue
            etype, name, file_, start_line, signature, doc = row
            n_called_by = store.conn.execute(
                "SELECT COUNT(DISTINCT src_id) FROM edges WHERE dst_id = ? AND type = 'calls'",
                [eid],
            ).fetchone()[0]
            n_depends_on = store.conn.execute(
                "SELECT COUNT(DISTINCT dst_id) FROM edges "
                "WHERE src_id = ? AND type IN ('calls', 'imports')",
                [eid],
            ).fetchone()[0]
            loc = f"{file_}:{start_line}"
            via = "+".join(hit.retrievers)
            first_doc = (doc or "").split("\n", 1)[0].strip()
            table.add_row(
                etype,
                name,
                loc,
                via,
                str(n_called_by),
                str(n_depends_on),
                first_doc,
            )
            # Lean summary an agent would consume for this entity.
            returned_tokens += estimate_tokens(f"{name} {signature or ''} {doc or ''} {loc}")
            if file_:
                files_seen.append(file_)

        tokens_if_read = read_baseline_tokens(store.conn, files_seen)

    console.print(table)
    if returned_tokens and tokens_if_read > returned_tokens:
        ratio = tokens_if_read / returned_tokens
        console.print(
            f"[green]~{returned_tokens:,} tokens returned[/green] vs "
            f"[yellow]~{tokens_if_read:,} to read these files[/yellow] "
            f"-- ~{ratio:.1f}x less [dim](estimate)[/dim]"
        )
    console.print(
        "[dim]Use [bold]codegraph deps <name>[/bold] or "
        "[bold]codegraph impact <name>[/bold] to explore further.[/dim]"
    )


@app.command()
def trace(
    from_id: str = typer.Argument(..., help="Source entity_id (start of the call chain)."),
    to_id: str = typer.Argument(..., help="Destination entity_id (end of the call chain)."),
    max_hops: int = typer.Option(7, "--max-hops", help="BFS hop limit (default 7, max 20)."),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
) -> None:
    """Find the shortest call path between two entity_ids (BFS). [T12.4]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    from codegraph.analysis.traversal import find_shortest_path

    with GraphStore(db) as store:
        path = find_shortest_path(store.conn, from_id, to_id, max_hops=max(1, min(max_hops, 20)))

    if path is None:
        console.print(
            f"[yellow]No call path from {from_id!r} to {to_id!r} within {max_hops} hop(s).[/yellow]"
        )
        raise typer.Exit(code=1)

    hops = len(path) - 1
    hop_word = "hop" if hops == 1 else "hops"
    if hops == 0:
        console.print(f"[green]Same entity[/green] (0 hops):  [bold]{path[0]}[/bold]")
        return

    console.print(f"[green]Path[/green] ({hops} {hop_word}):")
    for i, eid in enumerate(path):
        if i == 0:
            console.print(f"  [bold]{eid}[/bold]")
        else:
            console.print(f"  [cyan]->[/cyan] [bold]{eid}[/bold]")


@app.command()
def status(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repo root for staleness check (default: CWD)."
    ),
) -> None:
    """Show index statistics: file, entity, edge counts, and staleness. [T12.4]"""
    if not db.exists():
        console.print(
            f"[red]No graph database at {db}.[/red] Run [bold]codegraph index <repo>[/bold] first."
        )
        raise typer.Exit(code=1)

    with GraphStore(db) as store:
        n_files = store.count_files()
        n_entities = store.count_entities()
        n_edges = store.count_edges()
        n_embedded = store.count_embedded()

    stale_files = 0
    try:
        from codegraph.sync.watcher import count_stale_files

        stale_files = count_stale_files(repo, db)
    except Exception:  # noqa: BLE001 — best-effort
        pass

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold")
    table.add_column("Value")

    table.add_row("Database", str(db))
    table.add_row("Files", str(n_files))
    table.add_row("Entities", str(n_entities))
    table.add_row("Edges", str(n_edges))
    emb_pct = f"{100 * n_embedded // n_entities}%" if n_entities else "n/a"
    table.add_row("Embedded", f"{n_embedded} / {n_entities}  ({emb_pct})")
    if stale_files == 0:
        stale_val = "[green]up to date[/green]"
    else:
        noun = "file" if stale_files == 1 else "files"
        stale_val = f"[yellow]{stale_files} {noun} changed -- re-index recommended[/yellow]"
    table.add_row("Staleness", stale_val)

    console.print(table)


@app.command()
def watch(
    repo: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Repository root to watch for changes.",
    ),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="DuckDB graph file path."),
    no_embed: bool = typer.Option(
        False,
        "--no-embed",
        help="Skip semantic embeddings on incremental re-index.",
    ),
    debounce: float = typer.Option(
        0.3,
        "--debounce",
        help="Seconds to wait after a change before re-indexing (debounce).",
        min=0.0,
    ),
) -> None:
    """Watch a repo and re-index changed source files automatically. [T11.2]"""
    from codegraph.sync.watcher import ChangeEvent, RepoWatcher

    if not db.exists():
        console.print(
            f"[yellow]No index at {db}.[/yellow] "
            "Changed files will be indexed as they are saved. "
            "Run [bold]codegraph index <repo>[/bold] first for a full initial index."
        )

    def _on_change(evt: ChangeEvent) -> None:
        ms = f"{evt.elapsed_ms:.0f}ms"
        if evt.error is not None:
            console.print(f"[yellow]skipped[/yellow]  {evt.path}  [dim]{evt.error}[/dim]")
        elif evt.action == "deleted":
            console.print(f"[red]deleted[/red]  {evt.path}  [dim]{ms}[/dim]")
        else:
            n = evt.n_entities
            noun = "entity" if n == 1 else "entities"
            console.print(f"[green]{evt.action}[/green]  {evt.path}  [dim]{n} {noun}  {ms}[/dim]")

    watcher = RepoWatcher(
        repo=repo,
        db=db,
        no_embed=no_embed,
        debounce_sec=debounce,
        on_change=_on_change,
    )
    watcher.start()
    console.print(
        f"Watching [bold]{repo}[/bold]  "
        f"[dim](db: {db}, debounce: {debounce:.2f}s, Ctrl-C to stop)[/dim]"
    )

    try:
        watcher.join()
    except KeyboardInterrupt:
        console.print("\nStopping watcher...")
        watcher.stop()
        watcher.join(timeout=5)
        console.print("Watcher stopped.")


def _list_available_targets() -> None:
    """Print available agent targets to the console."""
    from codegraph.installer import list_targets  # also triggers auto-registration

    targets = list_targets()
    if targets:
        console.print("Available targets: " + ", ".join(f"[bold]{t.name}[/bold]" for t in targets))


@app.command()
def install(
    target: str = typer.Argument(..., help="Agent target: claude, cursor, codex, gemini."),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Pin a specific DuckDB graph file. Omit to let the server discover "
        "the nearest .codegraph/graph.duckdb per project (recommended).",
    ),
    location: str = typer.Option(
        "global", "--location", help="Config scope: global (user) or local (project)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    print_config: bool = typer.Option(
        False, "--print-config", help="Print config JSON without writing (dry run)."
    ),
    no_guide: bool = typer.Option(
        False, "--no-guide", help="Skip writing the CLAUDE.md agent guide."
    ),
) -> None:
    """Install CodeGraph as an MCP server in a supported agent. [T13.3]"""
    from codegraph.installer import get_target  # also triggers auto-registration
    from codegraph.installer.guide import write_agent_guide

    if location not in ("global", "local"):
        console.print(f"[red]--location must be 'global' or 'local', got {location!r}.[/red]")
        raise typer.Exit(code=1)

    try:
        t = get_target(target)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        _list_available_targets()
        raise typer.Exit(code=1) from exc

    global_ = location == "global"
    config_path = t.global_config_path() if global_ else t.local_config_path()

    if print_config:
        _emit(t.config_snippet(db) + "\n")
        return

    already = t.is_configured(global_=global_)
    action = "Update" if already else "Install"

    console.print(f"\n{action} CodeGraph MCP server -> [bold]{t.display_name}[/bold]")
    console.print(f"  Config:  [dim]{config_path}[/dim]")
    entry = t.build_entry(db).to_dict()
    console.print(f"  Command: [dim]{entry['command']} {' '.join(entry['args'])}[/dim]")
    if already:
        console.print("  [yellow]Note: existing entry will be replaced.[/yellow]")
    console.print()

    if not yes and not typer.confirm("Proceed?", default=False):
        console.print("Aborted.")
        raise typer.Exit()

    t.install(db, global_=global_)
    console.print(
        f"[green]Installed.[/green] {t.display_name} can now use CodeGraph as an MCP tool."
    )
    console.print(f"[dim]Config: {config_path}[/dim]")
    if db is None:
        console.print(
            "[dim]DB: auto-discovered per project (nearest .codegraph/graph.duckdb). "
            "One entry works across all your repos.[/dim]"
        )
    else:
        console.print(f"[dim]DB: pinned to {db}.[/dim]")

    if not no_guide:
        guide_path = write_agent_guide(Path("."))
        console.print(
            f"[dim]Agent guide written to {guide_path} "
            "(tells the agent to prefer CodeGraph over reading files).[/dim]"
        )


@app.command()
def uninstall(
    target: str = typer.Argument(..., help="Agent target: claude, cursor, codex, gemini."),
    location: str = typer.Option(
        "global", "--location", help="Config scope: global (user) or local (project)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    no_guide: bool = typer.Option(
        False, "--no-guide", help="Leave the CLAUDE.md agent guide in place."
    ),
) -> None:
    """Remove CodeGraph MCP entry from a supported agent config. [T13.3]"""
    from codegraph.installer import get_target  # also triggers auto-registration
    from codegraph.installer.guide import remove_agent_guide

    if location not in ("global", "local"):
        console.print(f"[red]--location must be 'global' or 'local', got {location!r}.[/red]")
        raise typer.Exit(code=1)

    try:
        t = get_target(target)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        _list_available_targets()
        raise typer.Exit(code=1) from exc

    global_ = location == "global"
    config_path = t.global_config_path() if global_ else t.local_config_path()

    if not t.is_configured(global_=global_):
        console.print(
            f"[yellow]CodeGraph is not configured in {t.display_name} "
            f"({config_path}). Nothing to remove.[/yellow]"
        )
        return

    if not yes and not typer.confirm(f"Remove CodeGraph from {t.display_name}?", default=False):
        console.print("Aborted.")
        raise typer.Exit()

    t.uninstall(global_=global_)
    console.print(f"[green]Uninstalled.[/green] CodeGraph entry removed from {config_path}.")

    if not no_guide and remove_agent_guide(Path(".")):
        console.print("[dim]Removed the CodeGraph block from CLAUDE.md.[/dim]")


def _count_entities(db: Path) -> int:
    """Entity count in the index, or 0 if the DB is missing/unreadable."""
    if not db.exists():
        return 0
    try:
        with GraphStore(db) as store:
            row = store.conn.execute("SELECT count(*) FROM entities").fetchone()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001 — a broken/locked DB just reads as "no index"
        return 0


@app.command()
def doctor(
    db: Path | None = typer.Option(
        None, "--db", help="DuckDB graph file path (default: auto-discover from CWD)."
    ),
) -> None:
    """Check that CodeGraph is set up correctly for this project.

    Read-only health check: confirms the index exists, the MCP server is wired
    into an agent, the agent guide is present, and the index is fresh. Prints a
    fix command for anything that needs attention. Always exits 0.
    """
    from codegraph.graph.locate import discover_db
    from codegraph.installer import list_targets
    from codegraph.installer.guide import has_agent_guide
    from codegraph.sync.watcher import count_stale_files

    ok = "[green]PASS[/green]"
    bad = "[red]FAIL[/red]"
    console.print("[bold]CodeGraph doctor[/bold]\n")

    # 1. Index present and non-empty.
    resolved = db if db is not None else discover_db()
    n_entities = _count_entities(resolved) if resolved is not None else 0
    if n_entities > 0:
        console.print(f"{ok}  Index: {n_entities} entities [dim]({resolved})[/dim]")
    else:
        console.print(f"{bad}  Index: none found -- run [bold]codegraph index .[/bold]")

    # 2. MCP server wired into at least one agent.
    configured = [
        t.display_name
        for t in list_targets()
        if t.is_configured(global_=True) or t.is_configured(global_=False)
    ]
    if configured:
        console.print(f"{ok}  MCP server wired into: {', '.join(configured)}")
    else:
        console.print(
            f"{bad}  MCP server: not configured -- run [bold]codegraph install claude[/bold]"
        )

    # 3. Agent guide present (CLAUDE.md managed block).
    if has_agent_guide(Path(".")):
        console.print(f"{ok}  Agent guide present [dim](CLAUDE.md)[/dim]")
    else:
        console.print(
            f"{bad}  Agent guide missing -- run [bold]codegraph install claude[/bold] (writes CLAUDE.md)"
        )

    # 4. Index freshness.
    if resolved is not None and resolved.exists():
        try:
            stale = count_stale_files(Path("."), resolved)
        except Exception:  # noqa: BLE001 — freshness check is best-effort
            stale = 0
        if stale > 0:
            noun = "file" if stale == 1 else "files"
            console.print(
                f"{bad}  Index stale: {stale} {noun} changed -- "
                "run [bold]codegraph index .[/bold] or [bold]codegraph watch .[/bold]"
            )
        else:
            console.print(f"{ok}  Index is up to date")

    console.print(
        "\n[dim]Tip: restart your agent after any install so it loads the MCP server.[/dim]"
    )


@app.command()
def init(
    repo: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Repository root to set up (default: current directory).",
    ),
    target: str = typer.Option(
        "claude", "--target", help="Agent to wire up: claude, cursor, codex, gemini."
    ),
    no_embed: bool = typer.Option(
        False, "--no-embed", help="Skip semantic embeddings (faster, literal search only)."
    ),
    location: str = typer.Option(
        "global", "--location", help="Agent config scope: global (user) or local (project)."
    ),
) -> None:
    """One-shot setup: index the repo, wire up your agent, and write the guide. [T18.2]

    Collapses index + install + CLAUDE.md into a single command so a new user
    goes from zero to a Claude-usable index in one step.
    """
    from codegraph.installer import get_target  # also triggers auto-registration
    from codegraph.installer.guide import write_agent_guide

    if location not in ("global", "local"):
        console.print(f"[red]--location must be 'global' or 'local', got {location!r}.[/red]")
        raise typer.Exit(code=1)

    # Resolve the target up front so we fail fast on a typo before indexing.
    try:
        t = get_target(target)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        _list_available_targets()
        raise typer.Exit(code=1) from exc

    # Keep the DB inside the repo so walk-up discovery resolves it per project.
    db = repo / ".codegraph" / "graph.duckdb"
    db.parent.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold]Step 1/3[/bold] Indexing [bold]{repo}[/bold]...")
    index(repo=repo, db=db, no_embed=no_embed)

    console.print(f"\n[bold]Step 2/3[/bold] Wiring up [bold]{t.display_name}[/bold]...")
    t.install(None, global_=(location == "global"))  # None = discovery, works everywhere
    config_path = t.global_config_path() if location == "global" else t.local_config_path()
    console.print(f"[green]MCP server registered[/green] [dim]({config_path})[/dim]")

    console.print("\n[bold]Step 3/3[/bold] Writing agent guide...")
    guide_path = write_agent_guide(repo)
    console.print(f"[green]Guide written[/green] [dim]({guide_path})[/dim]")

    # Self-verify: confirm the index really resolved and is non-empty, so the
    # user gets a clear pass/fail instead of trusting three silent steps.
    n_entities = _count_entities(db)
    if n_entities > 0:
        console.print(
            f"\n[green bold]Verified:[/green bold] index has {n_entities} entities; "
            f"MCP entry written for {t.display_name}."
        )
    else:
        console.print(
            "\n[yellow]Warning:[/yellow] the index looks empty. "
            "Re-run [bold]codegraph index .[/bold] before using the agent."
        )

    console.print(
        f"\n[green bold]Done.[/green bold] {t.display_name} can now use CodeGraph on this repo.\n"
        "Next steps:\n"
        f"  - [bold]Restart {t.display_name}[/bold] -- the MCP server only loads on startup "
        "(this is the #1 step people miss).\n"
        '  - Ask it: [italic]"use codegraph to explain how X works"[/italic].\n'
        "  - Run [bold]codegraph doctor[/bold] anytime to confirm everything is wired.\n"
        "  - Keep the index fresh with [bold]codegraph watch .[/bold] (optional)."
    )


if __name__ == "__main__":
    app()
