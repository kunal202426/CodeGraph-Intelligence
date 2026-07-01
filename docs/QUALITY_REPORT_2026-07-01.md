# Bench notes — Jul 2026

Tested CodeGraph against itself (128 files, 1,507 entities, 6,186 edges, 100% embedded).
All numbers are from the automated suite plus manual runs on the live index.

---

## Tests

778 passing, 0 failures, 1 skip (the skipped test calls the Anthropic API, needs a real key).

Added 4 tests for the new stale-index warning:

| Test | What it checks |
|---|---|
| `test_get_context_warns_when_stale` | Stale count = 5 → warning in output mentioning "5" and "reindex" |
| `test_get_context_no_stale_warning_when_fresh` | Stale count = 0 → no warning |
| `test_get_context_stale_warning_present_on_no_match` | Stale count = 3, query returns nothing → warning still there |
| `test_reindex_resets_stale_cache` | Seed cache at 7, mutate a file, call reindex → cache reads 0 |

---

## What the stale-index nudge does

Before this, `get_context` would serve results without saying anything if the index was out
of date. The only way to find out was to call `index_status` first, which agents mostly skip.

Now `get_context` calls `_get_stale_count()` internally and if anything is stale, adds a
warning to its `warnings` array: how many files changed and a prompt to call `reindex`. The
warning shows up even on a query that returns zero results, so the agent doesn't retry
blindly on stale data.

One thing worth noting: the stale check walks every source file and compares mtimes against
`max(indexed_at)` in DuckDB. On a medium repo that's 10-50ms per call, which was too slow
to run naked on every `get_context`. So there's a TTL cache (`_StalenessCache`) that holds
the count for 300 seconds. Cache hit is <1ms. After a successful `reindex` the cache is set
to 0 immediately, so the warning clears right away rather than waiting out the TTL. If
reindex fails partway through, the cache is invalidated so the next call re-checks.

---

## Token savings

`get_context` (default summary mode) vs reading the full source of the files it returns:

| Metric | Result |
|---|---|
| Average across queries | 101x |
| Worst (one small file, single function) | 12x |
| Best (multi-file query, summary mode) | 190x |
| One concrete example | 1,108 vs 10,637 tokens — 9.6x |

These are input/context tokens only. Output tokens don't change.

---

## Search accuracy

Queried 7 known symbols using `get_context`. For all 7, the function name didn't appear
anywhere in the query string, so this was a pure semantic (embedding) test.

Hit@1: 7/7. Hit@5: 7/7.

---

## Latency

| Thing | Time |
|---|---|
| `get_context` warm (model + vecs cached) | ~15ms |
| Stale check, TTL cache hit | <1ms |
| Stale check, cache miss | 10-50ms |
| `reindex` 1 file, no embed | ~300ms |
| `reindex` 25 stale files, no embed | 3-5s |

---

## Known issues

- Function-local imports (`from X import Y` inside a function body) aren't captured
  (`parsers/python.py:184-186`). Calls via those imports show as external.
- Framework-dispatched calls (Express routes, Django views, etc.) aren't resolved.
- DuckDB is single-writer. Don't run `codegraph watch` and a heavy `reindex`
  at the same time from two terminals.

---

## Is it worth using

On this codebase, yes. 101x average token reduction is real on a session with more than
a few back-and-forths. The silent-stale problem that was the main footgun is fixed now.
The three issues above are edge cases, not silent wrong answers.
