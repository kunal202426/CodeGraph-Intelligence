# Fixed benchmark suite

Every experiment branch runs this same suite, unchanged, against the same target
repositories. Do not edit task wording between branches, per the experiment plan's rule 3.

## Target repositories

Reused from this project's own prior cost-efficiency testing (see
`docs/COST_EFFICIENCY_FINDINGS_2026-07-10.md`) so results are comparable to existing
history rather than starting from an unfamiliar baseline:

- **LedgerGuard** (small, 47 files, 244 entities): near-parity/break-even regime.
- **JobHuntPro** (medium, 187 files, 1321 entities, cross-language): first repo where
  CodeGraph won on total cost.
- **Grafana** (large, ~16,000 files, ~97,000 entities): where the effect size is
  largest, and where every latency/correctness bug in this project's history was
  actually found.

Status: LedgerGuard and JobHuntPro were lost in the maintainer's SSD replacement (see
`docs/ONBOARDING.md`). Grafana was re-cloned (shallow, 23,552 files, indexed to 107,382
entities and 744,087 edges with embeddings) for the 2026-09-29/30 sessions and deleted
afterwards. To reproduce, re-clone the repo and run `codegraph init` on it; the index
took about 48 minutes to build on the maintainer's laptop.

## Tasks

Each task is run once per branch, same wording, same target repo, fresh session (not
continued from a prior task) unless the experiment specifically tests cross-turn reuse
(`exp/context-reuse`, which deliberately uses the 4-turn continuation shape below).

1. **Repository exploration.** "Find and explain where authentication/session handling
   is implemented in this repo." (LedgerGuard or JobHuntPro)
2. **Small bug fix.** A specific, real, reproducible small defect (pick one verified by
   running the existing test suite red before the fix).
3. **Multi-file feature.** A feature spanning 3-5 files touching an existing subsystem.
4. **Refactor.** A component with multiple real callers (verify caller count against
   `impact_analysis` AND `grep` ground truth before selecting the target, per this
   project's own rule: verify tool output before trusting it).
5. **Test failure.** A genuinely failing test, diagnose and fix.
6. **API modification.** Change a function/interface signature, update every real caller
   and its tests.
7. **Unfamiliar subsystem.** A subsystem the test session has not touched yet this run.

## Metrics recorded per task, per branch

```
task_success (pass/fail against a defined acceptance check, not vibes)
tests_passed / tests_failed
frontier_input_tokens
frontier_output_tokens
total_frontier_tokens
model_turns
codegraph_calls
native_tool_calls
redundant_calls        (same information fetched twice, classified per the additive/
                         substitutive/redundant/complementary taxonomy already used in
                         docs/experiments/systems-level-cost-latency-investigation.md §4)
total_tool_calls
codegraph_latency_ms    (sum across calls, from CodeGraph's own timing, not estimated)
total_task_latency
local_cpu / local_ram   (qualitative unless a real regression appears; not the focus)
local_disk              (scratch DB size, for experiments that touch storage)
qualitative_failures    (wrong implementation, missed dependency, unnecessary
                         retrieval, duplicate search, premature answer, incorrect
                         verification)
```

## What can be measured without a live Claude Code session

Not everything above needs a live session. Split explicitly, per experiment:

- **Local-only, fully measurable now:** `codegraph_latency_ms`, DB query counts,
  `local_disk`, byte-for-byte response equivalence checks. These can be verified with a
  script calling the MCP handler functions directly, the same method used for the
  `get_context` N+1 audit.
- **Requires a live session:** everything involving the frontier model directly
  (tokens, turns, task success, tool-selection behavior). These need the
  `docs/MANUAL_TEST_PROMPT.md` protocol: one task at a time, explicit verdict, real
  `/usage` numbers, run with the user.

Each experiment report states which rows it could fill in locally and which are marked
`NEEDS_LIVE_SESSION` rather than guessed.
