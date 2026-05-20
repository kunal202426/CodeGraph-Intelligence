# Phase 5 — GraphRAG + Anthropic LLM

> Per-phase plan. Read this + STATUS.md + AGENTS.md.

**Goal:** `codegraph ask "How does authentication work?"` returns a coherent grounded answer.
**Estimated:** 5 sessions, ~14h
**Exit:** `ask` and `summarize` work end-to-end on fixture and a real repo.

**Setup:** Requires `ANTHROPIC_API_KEY` env var. Model: `claude-sonnet-4-6` (locked in BUILD_PLAN §1).

## Tasks

### T5.1 — LLM wrapper
**Files:** `packages/codegraph/ai/llm.py` (~120 LOC), `tests/test_llm.py`
```python
import anthropic, os
from typing import Iterator

class LLM:
    def __init__(self, model="claude-sonnet-4-6"):
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = model

    def stream(self, system: str, user: str, max_tokens=2000) -> Iterator[str]:
        with self.client.messages.stream(
            model=self.model, max_tokens=max_tokens,
            system=[{"type":"text","text":system,"cache_control":{"type":"ephemeral"}}],
            messages=[{"role":"user","content":user}],
        ) as stream:
            for text in stream.text_stream: yield text
```
- Prompt caching on system message (saves cost across repeated queries on same repo).
- Retries with exponential backoff via SDK's built-in retry config.
- Surface API errors clearly.
**Verify:** Mock test that wrapper composes request correctly. (Live API call only in T5.4.)
**Commit:** `T5.1: Anthropic SDK wrapper with prompt caching`

### T5.2 — Hybrid retrieval for AI
**Files:** `ai/graphrag.py` (~150 LOC), `tests/test_graphrag.py`
**Steps:**
1. `retrieve(query: str, k=15) -> list[UIREntity]`
2. Vector search top-30 by query embedding
3. Expand: for each candidate, fetch graph neighbors (1-hop) via `calls`/`imports`
4. Deduplicate by entity_id
5. Re-rank by combined score: `0.6 * cosine_sim + 0.3 * graph_degree_log + 0.1 * is_recently_modified`
6. Truncate to top K
**Verify:** Snapshot test: given a fixture query, assert specific entity IDs appear in top K.
**Commit:** `T5.2: hybrid graph + vector retrieval`

### T5.3 — Prompt template
**Files:** `ai/prompts/ask_system.md`, `ai/graphrag.py` (extend)
**System prompt (~600 tokens):**
```
You are a code architecture analyst. You answer questions about a codebase using ONLY the
provided context. Cite specific entities by their entity_id when relevant, using the format
[py:src/auth/login.py:authenticate]. If the context does not contain enough information to
answer confidently, say so explicitly — do not invent details. Prefer concrete file:line
references over vague descriptions. Be concise; prefer 2-3 paragraphs over walls of text.
```
**User message assembly (~3000 tokens budget for context):**
```
QUESTION: {query}

REPOSITORY CONTEXT:
{for each top-k entity}
--- [{entity_id}] {type} ({file}:{start_line}-{end_line})
{signature or first 20 LOC of raw_source}
{docstring if present}
Calls: {neighbor entity_ids, comma-sep}
{end for}
```
**Verify:** Snapshot test asserting prompt structure for a fixture query.
**Commit:** `T5.3: prompt template and context assembly for ask`

### T5.4 — CLI `ask` with streaming
**Files:** `cli.py ask` (extend)
```python
@app.command()
def ask(query: str, db: Path = ".codegraph/graph.duckdb"):
    store = GraphStore(db)
    graphrag = GraphRAG(store, LLM())
    for token in graphrag.ask_stream(query):
        rich.print(token, end="", flush=True)
    rich.print()  # final newline
```
**Verify:** Manual — `uv run codegraph ask "What does the auth module do?"` returns coherent answer citing fixture entities.
**Commit:** `T5.4: end-to-end ask command with streaming`

### T5.5 — Repo summary
**Files:** `cli.py summarize` (extend), `ai/prompts/summarize_system.md`
**Steps (multi-pass):**
1. Per top-level directory, retrieve representative entities (sample, not all).
2. LLM call per directory → subsystem summary.
3. Final LLM call combining subsystem summaries → top-level architecture summary.
4. Write to `.codegraph/SUMMARY.md`.
**Verify:** `uv run codegraph summarize` writes a coherent markdown file with subsystem descriptions.
**Commit:** `T5.5: generate repository architecture summary`
