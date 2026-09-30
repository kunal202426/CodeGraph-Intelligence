# Experiment: exp/get-context-batching

## Hypothesis

`get_context`'s per-hit loop issues up to 4 sequential single-row DuckDB queries per
hit (entity row, a redundant second `raw_source` fetch, outbound deps, inbound
callers), an N+1 pattern of the same shape already fixed once in `impact_analysis`.
Batching these into 3 queries total per call (regardless of hit count), using the
pattern `_caller_counts` already proves works in this file, should cut latency and DB
round trips without changing the tool's response schema, returned information,
retrieval semantics, or ranking behavior.

## Baseline

Branch point: `baseline/cost-control` at `e0fb37fc05660df58f14adec31530002b21ae30d`.
Measured against a real scratch index (CodeGraph's own engine source,
`packages/codegraph`: 102 files, 865 entities, 4995 edges, no embeddings), not a
synthetic microbenchmark. See
`docs/experiments/systems-level-cost-latency-investigation.md` section 3 for the original
audit that found this.

## Implementation

`_get_context` in `packages/codegraph/server/mcp_server.py`: replaced the per-hit
`store.conn.execute(...)` calls (entity row, raw_source, deps, callers) with 3 batched
queries run once before the loop:

1. `SELECT {_ENTITY_COLUMNS} FROM entities WHERE entity_id IN (...)`, keyed into a dict
   by `entity_id`. `raw_source` is now always fetched here (it is part of
   `_ENTITY_COLUMNS`), so the separate raw_source-only query in summary mode is gone
   entirely, not just batched.
2. `SELECT DISTINCT src_id, dst_id FROM edges WHERE src_id IN (...) AND type IN
   ('calls', 'imports') ORDER BY src_id, dst_id`, grouped into a `deps_by_id` dict.
3. `SELECT DISTINCT dst_id, src_id FROM edges WHERE dst_id IN (...) AND type = 'calls'
   ORDER BY dst_id, src_id`, grouped into a `callers_by_id` dict.

The per-hit loop body is otherwise unchanged: same truncation, same token budgeting,
same warnings logic, same `columns` subset selection for summary vs. full mode.

Not changed in this branch (kept out of scope, one primary mechanism per the
experiment plan's git rules): `_resolve_file_summary`, called once per unique file
among the hits (already deduplicated via `file_summary_cache`, so not itself N+1
across hits, but each unique file still costs its own query internally). This shows up
in the "after" numbers below as the remaining, smaller query count and is flagged as a
candidate for a separate, narrower follow-up experiment rather than folded in here.

## Benchmark

Real, not estimated. Instrumented the live DuckDB connection to count `execute()`
calls, using the same profiling script as the original audit
(`profile_mcp_tools.py`, scratchpad).

### Query count, before vs. after

| Call | Before | After |
|---|---:|---:|
| `get_context("GraphStore")`, limit=5, summary | 18 | 9 |
| `get_context("GraphStore")`, limit=10, summary | 18 | 9 |
| `get_context("GraphStore")`, detail=full | 9 | 6 |
| `search_code`, `get_entity_context`, `impact_analysis`, `trace_path` | unchanged | unchanged (not touched by this experiment) |

Both limit=5 and limit=10 land at the same query count because only 4 real matches
exist for this query term in this index, both before and after (the response's actual
hit count, not the `limit` ceiling, drives the number, exactly as the original audit
predicted).

**Hit-count scaling (4/10/20/50/100), as requested:** `get_context`'s own `limit`
parameter is hard-capped at 10 inside the tool (`max(1, min(int(args.get("limit", 5)),
10))`), so 20/50/100 cannot be exercised through the tool's public interface at all,
this is a pre-existing constraint of the tool, not something this experiment changed
or could test around honestly. What the fix demonstrably does: the 3 batched queries
run exactly once per call regardless of how many hits are in the batch, up to the
tool's own cap of 10. The structural claim (query count independent of hit count) is
supported by the query shape itself (`IN (...)` over the full `hit_ids` list in one
call each), not by a workaround that bypasses the tool's real limit.

### Remaining query count after the fix (9, not 3)

Not fully collapsed to 3 because `_resolve_file_summary` still runs once per unique
file among the hits (4 hits across enough distinct files in this index to account for
the remaining 4 to 6 queries seen). This is a second, smaller, distinct N+1-shaped
pattern in a different function, correctly left out of this experiment's scope per the
"one primary mechanism per branch" rule. Logged as a follow-up candidate, not fixed
here.

### Output equivalence

Verified directly, not assumed: captured `get_context` output before and after in
single-call, single-process invocations (to remove any within-process ordering
variability), then diffed.

- `detail=full` mode: **byte-identical**, before and after.
- Summary mode (`limit=5`, `limit=10`): same set of entities, same counts, same
  `depends_on`/`called_by` membership, but list **order** differs from the pre-fix
  run. Investigated directly: the pre-fix implementation was *already*
  non-deterministic run-to-run in this exact same way (verified by running the
  unfixed code twice in separate processes and diffing those two runs against each
  other, they also differ). Neither the old per-hit query nor the new batched query
  ever had an `ORDER BY` guaranteeing a stable sample order; this experiment adds
  `ORDER BY src_id, dst_id` / `ORDER BY dst_id, src_id` to the two edge queries, which
  is a strict improvement (deterministic instead of arbitrary) but does not, and was
  never able to, reproduce the old implementation's arbitrary order, since that order
  was never a documented or stable contract. No tool description, schema, or code
  comment anywhere promised an order for these capped sample lists.

### Wall clock

Warm-process timings: 130ms to 22ms range across calls, dominated by DuckDB
connection/query-plan warmup rather than query count at this small index size; the
query-count reduction is the load-bearing, reproducible signal here, not the
millisecond numbers, which are noisy at this scale and would need a larger index
(Grafana-scale) to show a clean latency delta.

### Tests

`uv run pytest -k "mcp_server or get_context" -q`: 43 passed, 0 failed.
`uv run ruff check` and `uv run ruff format --check`: both clean.
Full suite (`uv run pytest -q`): 1374 passed, 1 skipped in 207.54s, exit code 0.
Matches the documented pre-change baseline exactly (1374 passed, 1 skipped), no
regressions.

## Quality impact

None expected, and none observed in the equivalence check beyond the list-order
non-issue explained above (which was already present pre-fix and is now more
deterministic, not less). No response schema change, no information removed or added,
no ranking change (ranking happens in `hybrid_search`/`_merge_query_hits`, untouched
by this experiment).

## Latency impact

Real, measured, structural: query count per `get_context` call dropped from 18 to 9 in
this test case (summary mode), and from 9 to 6 in full mode. Full elimination to the
theoretical 3 requires the separate `_resolve_file_summary` fix, out of scope here.
Absolute wall-clock savings not yet meaningfully measurable at this index's small
scale; the mechanism (fewer round trips) is proven, the dollar/millisecond magnitude
at production scale needs a larger index or a live session to confirm, consistent with
this document's own stated limits on what a local script can prove.

## Token impact

None by design. This experiment deliberately does not touch response size, ranking,
or schema, per the experiment plan's explicit instruction not to change retrieval
semantics.

## Failure cases

None found. The one caveat (list order in summary mode's `depends_on`/`called_by`)
is documented above and judged not to be a real behavior change, since no contract
ever guaranteed order for these fields.

## Decision

**KEEP.** Full test suite green (1374 passed, 1 skipped, matches documented baseline
exactly), lint and format clean, output equivalence verified directly (byte-identical
in full mode, set-equivalent with a documented and justified order caveat in summary
mode), real measured query-count reduction (18 to 9, 9 to 6). Lowest-risk,
highest-confidence item across the whole experiment set: proven pattern already used
elsewhere in the same file, zero information change, zero token change. Live-session
confirmation on a larger index (per `benchmark-suite.md`) is the one thing still
outstanding, marked `NEEDS_LIVE_SESSION` in the results matrix, not required to accept
this branch since the mechanism itself (fewer round trips, identical data) is already
proven locally.
