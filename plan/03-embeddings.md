# Phase 3 — Local Embeddings + Semantic Search

> Per-phase plan. Read this + STATUS.md + AGENTS.md.

**Goal:** `codegraph search "payment retry"` returns `retryBilling()` even though words don't match literally.
**Estimated:** 4 sessions, ~10h
**Exit:** Semantic search returns relevant results not found by literal search.

## Tasks

### T3.1 — sentence-transformers wrapper
**Files:** `packages/codegraph/embeddings/pipeline.py` (~80 LOC), `tests/test_embeddings.py`
**Steps:** Lazy-load `SentenceTransformer("all-MiniLM-L6-v2")` (cached to `~/.cache/torch/sentence_transformers/` on first use). Wrapper exposes `embed_batch(texts: list[str]) -> np.ndarray (N, 384)`. Cache the model singleton.
**Verify:** Embed 2 strings, assert shape `(2, 384)`, dtype `float32`.
**Commit:** `T3.1: sentence-transformers embedding wrapper`

### T3.2 — Embedding storage in DuckDB
**Files:** `embeddings/pipeline.py` (extend). No schema change needed (column added in T1.4).
**Steps:** `store_embeddings(entity_ids, vectors)` upserts into `entities.embedding`. Also store `embedding_hash` = SHA-256 of input text used (for drift detection in T3.5).
**Verify:** Insert + cosine-search round-trip (DuckDB `array_cosine_similarity`).
**Commit:** `T3.2: store entity embeddings in DuckDB FLOAT[384] column`

### T3.3 — Chunking strategy + batch embed during index
**Files:** `embeddings/chunking.py` (~50 LOC), `cli.py index` (extend)
```python
def build_embed_input(e: UIREntity) -> str:
    parts = [f"{e.type.value} {e.qualified_name}"]
    if e.signature: parts.append(e.signature)
    if e.docstring: parts.append(e.docstring)
    body = e.raw_source[:1500]
    parts.append(body)
    return "\n".join(parts)
```
After T1.7's main parse+write loop, batch-collect new/changed entities and embed in chunks of 32. Show separate progress bar.
**Verify:** Re-index fixture; `SELECT count(*) FROM entities WHERE embedding IS NOT NULL` == entity count.
**Commit:** `T3.3: embed entities during index pass`

### T3.4 — Hybrid search (literal + vector + RRF)
**Files:** `cli.py search` (extend), `graph/queries.py` (extend)
**Steps:**
1. Add `--semantic` and `--hybrid` flags. Default = hybrid.
2. Literal: existing ILIKE on name + docstring (top 20).
3. Vector: embed query, DuckDB `array_cosine_similarity` ordered DESC (top 20).
4. Fuse via Reciprocal Rank Fusion: `score = sum(1/(60+rank_i))` across both lists.
5. Return top K, annotated with which retrievers found each result.
**Verify:** Query "user authentication" returns `authenticate` even if function only has docstring "validates credentials".
**Commit:** `T3.4: hybrid search with literal + vector RRF`

### T3.5 — Incremental re-embed
**Files:** `cli.py index` (extend)
**Steps:** Before embedding an entity, compute `embed_input_hash`. Skip if equal to existing `embedding_hash`. Re-embed otherwise. Print count of re-embeddings at end.
**Verify:** Index twice in a row → second run reports "0 re-embeddings". Edit one file → only that file's entities re-embed.
**Commit:** `T3.5: incremental embedding via input-hash check`
