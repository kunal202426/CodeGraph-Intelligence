# Experiment results matrix

Central table, updated as each experiment completes. Blank cells mean not yet measured,
not zero. A cell marked `NEEDS_LIVE_SESSION` means the number requires a real Claude Code
session (per `benchmark-suite.md`) and hasn't been run yet.

| Experiment | Frontier tokens | Turns | CG calls | Native calls | Redundant calls | CG latency | Total latency | Quality |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline | ~1k out / 1.1M cache read, $0.67, Task 1 only (n=1) | ~19 total | 5 | ~14 | not yet classified | not measured | 2m10s active, 46s API | pending your verdict, looks strong on inspection |
| get_context batching | 76 out / 855k cache read, $0.54, Task 1 only (n=1) | ~21 total | 3 | ~18 | 1 native search failed | see `get-context-batching.md` for local query-count numbers | 1m active, 44s API | pending your verdict; no visible regression vs. baseline, +1 detail found (ExtJWT client) |
| Schema compression | | | | | | | | |
| Dynamic tool exposure | | | | | | | | |
| Native vs. CodeGraph routing | pending round 4 | | | | | n/a (guide-text only) | | pending round 4; local tests green, see `native-vs-codegraph-routing.md` |
| Smart context | | | | | | | | |
| Context reuse | | | | | | | | |
| Laya routing | | | | | | | | |

## Reading this table

Per the experiment plan's own rule 5: a branch is not accepted for a token or latency win
alone. The `Quality` column is load bearing, not decorative. An experiment with a strong
cost win and a blank or regressed `Quality` cell is not ready for a KEEP decision, see each
experiment's own report for the actual decision and reasoning.

## Live results summary (Grafana, fresh session per row, n=1 each)

| Task | Variant | Cost | Turns | Cache read | Rubric / quality |
|---|---|---:|---:|---:|---|
| 1 explore auth | no CodeGraph | **$0.44** | ~5 | 523.6k | strong, most complete |
| 1 explore auth | baseline | $0.67 | ~11 | 1.1M | strong |
| 1 explore auth | get_context batching | $0.54 | ~9 | 855k | strong |
| 1 explore auth | routing guide | $0.59 | 10 | 994.2k | strong, broader/shallower |
| 2 blast radius | no CodeGraph | **$0.34** | 5 | 395.6k | 8/8 |
| 2 blast radius | prefetch hook only | $0.36 | 5 | 398.9k | 8/8 (extra citations unverified) |
| 2 blast radius | combo (batching+overflow+routing) | $0.39 | 6 | 516.6k | 8/8 |

No CodeGraph variant, including the prefetch hook, beat the no-CodeGraph control on cost in either task.
