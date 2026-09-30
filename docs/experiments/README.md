# Experiment logs: cost and latency work, 2026-09-29 to 2026-09-30

Working notes from a short round of controlled Claude Code sessions run against a
Grafana clone, plus the design notes that led to them. They are published so anyone
picking this project up has the full record, including runs that did not pan out.

This index does not restate or modify any claim made elsewhere in the repo. The
sessions used two questions of their own (Task 1 and Task 2 below) and did not re-run
the Grafana question from `docs/COST_EFFICIENCY_FINDINGS_2026-07-10.md`.

## What was measured

Fresh Claude Code session per variant, same task text, cost and tokens read from the
`/usage` panel (Sonnet 5.5), turns and tool calls read from the session transcript.
One run per variant, so treat differences of a few cents as noise.

| Task | Variant | Cost | Turns |
|---|---|---:|---:|
| 1: find and explain auth handling | no CodeGraph | $0.44 | ~5 |
| 1 | CodeGraph baseline | $0.67 | ~11 |
| 1 | + batched `get_context` queries | $0.54 | ~9 |
| 1 | + guide rule: look up every symbol through CodeGraph | $0.59 | 10 |
| 2: blast radius of a function signature (read only) | no CodeGraph | $0.34 | 5 |
| 2 | prefetch hook only (no MCP server) | $0.36 | 5 |
| 2 | CodeGraph combined (batching, overflow fix, guide rule) | $0.39 | 6 |

Task 2 answers were graded against a grep-verified rubric (8 points), and all three
scored 8 of 8 on the fixed items. Every location cited by the CodeGraph run and by the
no-CodeGraph run was checked against the source and none was invented (the latter
overstated one test call count, 35 against an actual 32). The hook-only run cited
additional locations (an interface and a fake) that were not source-checked before the
clone was deleted, so their accuracy is unverified. The CodeGraph run traced one more
hop of production callers.

Findings recorded in the logs:

- Cost tracked the number of sequential model turns, each re-reading a fixed prefix of
  about 85k tokens (system prompt, skills, deferred tool names, MCP instructions). All
  tool output combined was about 15k tokens.
- CodeGraph lookups are sequential (each result decides the next query) while grep and
  Read can be fanned out in parallel within one turn.
- Injecting correct graph context ahead of the model did not reduce its own
  verification turns.

## Caveats

- One run per variant, two tasks, one repository. Both tasks had an exact identifier or
  an obvious keyword, which is favorable to grep.
- The fixed prefix is specific to the maintainer's Claude Code setup (many MCP servers
  and plugins); a leaner setup has a smaller per-turn base.
- Nothing here measures long multi-question sessions.

## Files

| File | What it is |
|---|---|
| `systems-level-cost-latency-investigation.md` | Cost model, tool-schema token analysis, query-count profiling, architecture options, and the Laya/Jev research |
| `baseline.md`, `benchmark-suite.md` | Environment, fixed task list and metrics |
| `results-matrix.md` | Roll-up table of every live run |
| `get-context-batching.md` | Fix for the per-hit query pattern in `get_context` (merged) |
| `native-vs-codegraph-routing.md` | Guide change so lookups go through CodeGraph for every task (merged) |
| `prefetch-hook.md` | A `UserPromptSubmit` hook that injects caller context (not merged, code not retained) |
| `live-sessions/task1-repo-exploration.md` | Task 1 rounds with transcript-derived turn tables |
| `live-sessions/task2-blast-radius.md` | Task 2 rounds, rubric, ground truth and cost breakdown |

## What became of the code

Merged to `main`: batched `get_context` queries (`de13a30`), a non-fatal over-limit
query batch (`de2f337`), and the guide's each-symbol rule for every task (`dfb3d56`).
The experiment branches were deleted afterwards. The prefetch hook was not merged and
its code was not kept; `prefetch-hook.md` describes its design and result.

## Withheld

The raw session transcripts were not published. They embed the maintainer's full system
prompt, account and connector identifiers, and private instructions. Their per-turn
token tables are reproduced in the logs above.

## Reading notes

Some notes were written as a running conversation with the repo owner and address them
as "you". Paths such as `.agent/...` in older text now live under `docs/`. The Grafana
clone and its index were deleted after the sessions, so the harness commits described
there no longer exist.
