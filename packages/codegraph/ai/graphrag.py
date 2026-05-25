"""GraphRAG retrieval: vector seeds + 1-hop graph expansion, re-ranked (T5.2).

The retrieval core for `ask` / `summarize`. Pure-text vector search alone misses
code that's *structurally* central but lexically dissimilar; pure graph walks
miss semantically relevant code that isn't directly wired to the seed. We combine
both:

  1. Vector-search the query embedding for the top `pool` semantic seeds.
  2. Expand each seed by its 1-hop `calls` / `imports` neighbours (both directions).
  3. Dedupe the seed ∪ neighbour set by entity_id.
  4. Re-rank every candidate by a blend of semantic similarity, graph centrality
     (log degree), and recency, then keep the top `k`.

`retrieve()` takes a *precomputed* query vector so it's independent of the
embedding model (and unit-testable with hand-crafted vectors). `GraphRAG.retrieve`
is the convenience wrapper that embeds the query string first.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import duckdb

from codegraph.ai.llm import LLMError

# Re-rank weights (see module docstring). Semantic similarity dominates; graph
# centrality breaks ties toward well-connected code; recency is a light nudge.
_W_SIMILARITY = 0.6
_W_DEGREE = 0.3
_W_RECENCY = 0.1

_EMBEDDING_DIM = 384
_EDGE_TYPES = ("calls", "imports")

# Prompt assembly (T5.3).
_PROMPTS_DIR = Path(__file__).with_name("prompts")
_ASK_SYSTEM_PATH = _PROMPTS_DIR / "ask_system.md"
_SUMMARIZE_SYSTEM_PATH = _PROMPTS_DIR / "summarize_system.md"
_BODY_PREVIEW_LINES = 20  # show signature, else first N lines of raw_source
_CONTEXT_CHAR_BUDGET = 12000  # ~3000 tokens of repository context
_DEFAULT_PER_DIR = 10  # representative entities sampled per top-level directory


@dataclass(frozen=True)
class RetrievedEntity:
    """One ranked retrieval result with the fields the prompt assembler needs."""

    entity_id: str
    type: str
    name: str
    qualified_name: str
    file: str
    start_line: int
    end_line: int
    signature: str | None
    docstring: str | None
    raw_source: str | None
    similarity: float  # cosine sim to the query (0 if the entity has no embedding)
    degree: int  # total in + out edges (graph centrality)
    score: float  # final combined rank score
    via: str  # "vector" (semantic seed) or "graph" (neighbour expansion)
    neighbors: tuple[str, ...] = field(default_factory=tuple)  # outbound call/import targets


def _in_clause(ids: list[str]) -> tuple[str, list[str]]:
    """Return a ``(?, ?, …)`` placeholder clause and the matching params."""
    return "(" + ",".join(["?"] * len(ids)) + ")", list(ids)


def _seed_ids(
    conn: duckdb.DuckDBPyConnection, query_vector: list[float], pool: int
) -> dict[str, float]:
    """Top-`pool` semantic seeds → {entity_id: similarity}."""
    rows = conn.execute(
        f"""
        SELECT entity_id, array_cosine_similarity(embedding, ?::FLOAT[{_EMBEDDING_DIM}]) AS sim
        FROM entities
        WHERE embedding IS NOT NULL
        ORDER BY sim DESC
        LIMIT ?
        """,
        [query_vector, pool],
    ).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def _expand_neighbors(conn: duckdb.DuckDBPyConnection, seeds: list[str]) -> set[str]:
    """1-hop `calls`/`imports` neighbours (both directions) that are real entities."""
    if not seeds:
        return set()
    clause, params = _in_clause(seeds)
    type_clause, type_params = _in_clause(list(_EDGE_TYPES))
    rows = conn.execute(
        f"""
        SELECT DISTINCT
          CASE WHEN e.src_id IN {clause} THEN e.dst_id ELSE e.src_id END AS neighbor
        FROM edges e
        WHERE e.type IN {type_clause}
          AND (e.src_id IN {clause} OR e.dst_id IN {clause})
        """,
        [*params, *type_params, *params, *params],
    ).fetchall()
    candidates = {r[0] for r in rows if r[0] is not None}
    if not candidates:
        return set()
    # Keep only neighbours that are real indexed entities (drop external:/wildcard:).
    real_clause, real_params = _in_clause(list(candidates))
    real_rows = conn.execute(
        f"SELECT entity_id FROM entities WHERE entity_id IN {real_clause}",
        real_params,
    ).fetchall()
    return {r[0] for r in real_rows}


def _outbound_neighbors(conn: duckdb.DuckDBPyConnection, ids: list[str]) -> dict[str, list[str]]:
    """Map each id to its outbound `calls`/`imports` targets (for prompt context)."""
    if not ids:
        return {}
    clause, params = _in_clause(ids)
    type_clause, type_params = _in_clause(list(_EDGE_TYPES))
    rows = conn.execute(
        f"""
        SELECT src_id, dst_id FROM edges
        WHERE type IN {type_clause} AND src_id IN {clause}
        ORDER BY src_id, line, dst_id
        """,
        [*type_params, *params],
    ).fetchall()
    out: dict[str, list[str]] = {}
    for src, dst in rows:
        bucket = out.setdefault(src, [])
        if dst not in bucket:
            bucket.append(dst)
    return out


def _combined_score(sim: float, degree: int, recency: float, max_degree: int) -> float:
    """Weighted blend of similarity (clamped ≥0), log-degree, and recency — all in [0,1]."""
    sim_c = max(0.0, sim)
    deg_c = math.log1p(degree) / math.log1p(max_degree) if max_degree > 0 else 0.0
    return _W_SIMILARITY * sim_c + _W_DEGREE * deg_c + _W_RECENCY * recency


def _as_epoch(value: object) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
    return None


def retrieve(
    conn: duckdb.DuckDBPyConnection,
    query_vector: list[float],
    k: int = 15,
    pool: int = 30,
) -> list[RetrievedEntity]:
    """Hybrid retrieval over a precomputed query vector. Returns top-`k` ranked.

    Empty when there are no embedded entities (e.g. indexed with --no-embed) or
    the query vector is empty.
    """
    if not query_vector:
        return []

    seed_sims = _seed_ids(conn, query_vector, pool)
    if not seed_sims:
        return []
    seeds = list(seed_sims)
    neighbors = _expand_neighbors(conn, seeds)
    candidate_ids = list(dict.fromkeys([*seeds, *sorted(neighbors)]))  # stable, deduped

    clause, params = _in_clause(candidate_ids)
    rows = conn.execute(
        f"""
        SELECT e.entity_id, e.type, e.name, e.qualified_name, e.file,
               e.start_line, e.end_line, e.signature, e.docstring, e.raw_source,
               COALESCE(array_cosine_similarity(e.embedding, ?::FLOAT[{_EMBEDDING_DIM}]), 0.0) AS sim,
               (SELECT COUNT(*) FROM edges g
                 WHERE g.src_id = e.entity_id OR g.dst_id = e.entity_id) AS degree,
               f.indexed_at AS indexed_at
        FROM entities e
        LEFT JOIN files f ON f.path = e.file
        WHERE e.entity_id IN {clause}
        """,
        [query_vector, *params],
    ).fetchall()
    if not rows:
        return []

    outbound = _outbound_neighbors(conn, candidate_ids)

    # Normalize recency across the candidate set (min-max on index timestamp).
    epochs = [e for e in (_as_epoch(r[12]) for r in rows) if e is not None]
    t_min, t_max = (min(epochs), max(epochs)) if epochs else (0.0, 0.0)
    span = t_max - t_min

    def recency_of(value: object) -> float:
        ep = _as_epoch(value)
        if ep is None or span <= 0:
            return 0.0
        return (ep - t_min) / span

    max_degree = max((int(r[11]) for r in rows), default=0)

    results: list[RetrievedEntity] = []
    for r in rows:
        eid = r[0]
        sim = float(r[10])
        degree = int(r[11])
        score = _combined_score(sim, degree, recency_of(r[12]), max_degree)
        results.append(
            RetrievedEntity(
                entity_id=eid,
                type=r[1],
                name=r[2],
                qualified_name=r[3],
                file=r[4],
                start_line=r[5],
                end_line=r[6],
                signature=r[7],
                docstring=r[8],
                raw_source=r[9],
                similarity=sim,
                degree=degree,
                score=score,
                via="vector" if eid in seed_sims else "graph",
                neighbors=tuple(outbound.get(eid, [])),
            )
        )

    results.sort(key=lambda e: (-e.score, e.entity_id))
    return results[:k]


# ----------------------------------------------------------------------
# Prompt assembly (T5.3)


def load_system_prompt() -> str:
    """Read the `ask` system prompt template from disk."""
    return _ASK_SYSTEM_PATH.read_text(encoding="utf-8").strip()


def load_summarize_prompt() -> str:
    """Read the `summarize` system prompt template from disk."""
    return _SUMMARIZE_SYSTEM_PATH.read_text(encoding="utf-8").strip()


def _top_dir(file: str) -> str:
    """Top-level directory of a repo-relative path ('.' for root-level files)."""
    head, sep, _ = file.partition("/")
    return head if sep else "."


def select_representatives(
    conn: duckdb.DuckDBPyConnection,
    per_dir: int = _DEFAULT_PER_DIR,
) -> dict[str, list[RetrievedEntity]]:
    """Pick the most graph-central entities per top-level directory.

    Representative selection for `summarize` uses graph degree (in + out edges)
    rather than vector search, so it works without embeddings and is fully
    deterministic. Returns an ordered map {top_dir: [entities]} — directories
    sorted by name, entities within a directory by descending degree.
    """
    rows = conn.execute(
        """
        SELECT e.entity_id, e.type, e.name, e.qualified_name, e.file,
               e.start_line, e.end_line, e.signature, e.docstring, e.raw_source,
               (SELECT COUNT(*) FROM edges g
                 WHERE g.src_id = e.entity_id OR g.dst_id = e.entity_id) AS degree
        FROM entities e
        """
    ).fetchall()
    if not rows:
        return {}

    grouped: dict[str, list[RetrievedEntity]] = {}
    for r in rows:
        entity = RetrievedEntity(
            entity_id=r[0],
            type=r[1],
            name=r[2],
            qualified_name=r[3],
            file=r[4],
            start_line=r[5],
            end_line=r[6],
            signature=r[7],
            docstring=r[8],
            raw_source=r[9],
            similarity=0.0,
            degree=int(r[10]),
            score=float(r[10]),
            via="graph",
        )
        grouped.setdefault(_top_dir(entity.file), []).append(entity)

    result: dict[str, list[RetrievedEntity]] = {}
    for d in sorted(grouped):
        ents = sorted(grouped[d], key=lambda e: (-e.degree, e.entity_id))
        result[d] = ents[:per_dir]
    return result


def _entity_body(entity: RetrievedEntity) -> str:
    """The code preview for one entity: its signature, else the first N source lines."""
    if entity.signature:
        return entity.signature.strip()
    if entity.raw_source:
        lines = entity.raw_source.splitlines()
        preview = "\n".join(lines[:_BODY_PREVIEW_LINES])
        if len(lines) > _BODY_PREVIEW_LINES:
            preview += "\n    ..."
        return preview
    return "(source unavailable)"


def format_entity_block(entity: RetrievedEntity) -> str:
    """Render one retrieved entity as a context block for the prompt."""
    header = (
        f"--- [{entity.entity_id}] {entity.type} "
        f"({entity.file}:{entity.start_line}-{entity.end_line})"
    )
    parts = [header, _entity_body(entity)]
    if entity.docstring:
        parts.append(entity.docstring.strip())
    if entity.neighbors:
        parts.append("Calls: " + ", ".join(entity.neighbors))
    return "\n".join(parts)


def build_user_message(
    query: str,
    entities: list[RetrievedEntity],
    char_budget: int = _CONTEXT_CHAR_BUDGET,
) -> str:
    """Assemble the user message: the question + a token-budgeted context section.

    Entities are added in rank order until the character budget is reached, so
    the most relevant context survives truncation. With no entities, the context
    section says so explicitly (the system prompt tells the model to admit gaps).
    """
    lines = [f"QUESTION: {query}", "", "REPOSITORY CONTEXT:"]
    if not entities:
        lines.append("(no relevant entities were retrieved for this query)")
        return "\n".join(lines)

    used = 0
    # The loop only ever appends-then-continues or breaks, so the enumerate index
    # equals the number of blocks already included.
    for i, entity in enumerate(entities):
        block = format_entity_block(entity)
        if i > 0 and used + len(block) > char_budget:
            break
        lines.append("")
        lines.append(block)
        used += len(block)
    return "\n".join(lines)


class GraphRAG:
    """Convenience wrapper binding a store + embedder (+ optional LLM for later tasks).

    `embedder` is a callable str -> list[float]; defaults to the local
    sentence-transformers `embed_one`. Inject a fake in tests to avoid the model.
    """

    def __init__(self, store, llm=None, embedder=None) -> None:
        self.store = store
        self.llm = llm
        self._embedder = embedder

    def _embed_query(self, query: str) -> list[float]:
        if self._embedder is not None:
            return list(self._embedder(query))
        from codegraph.embeddings.pipeline import embed_one

        return embed_one(query).tolist()

    def retrieve(self, query: str, k: int = 15, pool: int = 30) -> list[RetrievedEntity]:
        return retrieve(self.store.conn, self._embed_query(query), k=k, pool=pool)

    def ask_stream(
        self,
        query: str,
        k: int = 15,
        max_tokens: int = 2000,
    ) -> Iterator[str]:
        """Retrieve context, assemble the prompt, and stream the grounded answer.

        Lazy: retrieval + embedding run when the generator is first consumed, so
        errors (embedding model / API) surface at iteration time for the caller
        to handle. Raises `LLMError` if no LLM is configured.
        """
        if self.llm is None:
            raise LLMError("No LLM configured — construct GraphRAG(store, LLM()).")
        entities = self.retrieve(query, k=k)
        system = load_system_prompt()
        user = build_user_message(query, entities)
        yield from self.llm.stream(system, user, max_tokens=max_tokens)

    def summarize(self, per_dir: int = _DEFAULT_PER_DIR, max_tokens: int = 1000) -> str:
        """Generate a multi-pass architecture summary as a markdown string.

        Pass 1: summarize each top-level directory from its most graph-central
        entities. Pass 2: synthesize those subsystem summaries into one overview.
        Needs the graph only — no embeddings — so it runs without the embedding
        model. Raises `LLMError` if no LLM is configured.
        """
        if self.llm is None:
            raise LLMError("No LLM configured — construct GraphRAG(store, LLM()).")

        groups = select_representatives(self.store.conn, per_dir=per_dir)
        if not groups:
            return "# Architecture Summary\n\n(No indexed entities to summarize.)\n"

        system = load_summarize_prompt()
        subsystems: dict[str, str] = {}
        for directory, entities in groups.items():
            context = "\n\n".join(format_entity_block(e) for e in entities)
            user = (
                f"SUBSYSTEM: {directory}\n\n"
                f"ENTITIES:\n{context}\n\n"
                "Write a concise (2-4 sentence) summary of what this part of the "
                "codebase is responsible for and how its pieces relate."
            )
            subsystems[directory] = self.llm.complete(system, user, max_tokens=max_tokens).strip()

        combined = "\n\n".join(f"{d}:\n{summary}" for d, summary in subsystems.items())
        overview_user = (
            "Here are per-directory summaries of a codebase:\n\n"
            f"{combined}\n\n"
            "Write a 1-2 paragraph high-level overview of the overall architecture: "
            "what the system does and how the subsystems fit together."
        )
        overview = self.llm.complete(system, overview_user, max_tokens=max_tokens).strip()

        return _render_summary(overview, subsystems)


def _render_summary(overview: str, subsystems: dict[str, str]) -> str:
    """Assemble the final SUMMARY.md from the overview + per-subsystem summaries."""
    parts = ["# Architecture Summary", "", overview, "", "## Subsystems"]
    for directory, summary in subsystems.items():
        label = directory if directory != "." else "(root)"
        parts.append("")
        parts.append(f"### {label}")
        parts.append(summary)
    return "\n".join(parts) + "\n"
