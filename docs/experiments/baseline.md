# Baseline: baseline/cost-control

Recorded at branch creation. Do not modify this branch except for benchmark
infrastructure fixes (per the experiment plan's rule 1).

## Environment

```
baseline_commit:    e0fb37fc05660df58f14adec31530002b21ae30d
codegraph_version:  0.1.0
python_version:     3.11.9
uv_version:         0.12.19
repository:         kunal202426/CodeGraph-Intelligence, main
working_tree:       clean at branch point
```

## Database/index state

No persistent index exists for this repo itself (`codegraph init` has not been run
against a target project, per prior session notes). Each experiment that needs an index
builds its own scratch DB from a named source tree, recorded per-experiment, so index
state is reproducible per experiment rather than shared mutable state.

## MCP configuration

Default `tool_definitions()` as of this commit: 11 tools (12 with `ask_codebase` when
`ANTHROPIC_API_KEY` is set), ~2,554 static schema tokens. See
`docs/experiments/systems-level-cost-latency-investigation.md` section 2 for the per-tool
breakdown this baseline number came from.

## Model configuration

Frontier model: Claude Sonnet (per this repo's own cost log, confirmed 100% Sonnet / 0%
Haiku in prior A/B rounds via the `/usage` panel). No local decision model in the
baseline (Laya/Jev not integrated).

## What this baseline does NOT yet have

Live task-success / frontier-token / wall-clock numbers for the fixed benchmark suite
(section 3 of the experiment plan) have not been run yet. Those require real Claude Code
sessions per this repo's own `docs/MANUAL_TEST_PROMPT.md` protocol and are not something
that can be produced without that live process. What follows in this experiments
directory separates what was measured locally (DB query counts, wall-clock latency,
purely code-level facts) from what still needs a live A/B pass.
