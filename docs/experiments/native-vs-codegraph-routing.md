# Experiment: exp/native-vs-codegraph-routing

## Hypothesis

CodeGraph tool calls are additive to native grep/Read, not substitutive, per this
repo's own historical round 8 finding. If the guide explicitly tells the agent to
keep locating every new symbol via CodeGraph (not just the first one, and not
gated to editing tasks only), native call count should drop and CodeGraph should
stop paying for exploration work it isn't actually replacing.

## Baseline

Branch point: `baseline/cost-control` at `e0fb37fc05660df58f14adec31530002b21ae30d`
(this experiment is isolated from `exp/get-context-batching`, per the git rule that
each branch starts from the same baseline and changes one mechanism).

## Live evidence that motivated this branch (Task 1, before any fix)

Real A/B, same task, three fresh Claude Code sessions against the same Grafana
index (107,382 entities, harness commit `a87adee4`):

| | Cost | Active | Cache read | CodeGraph calls | Native calls |
|---|---:|---:|---:|---:|---:|
| Baseline (`baseline/cost-control`) | $0.67 | 2m10s | 1.1M | 5 | ~14 |
| `exp/get-context-batching` | $0.54 | 1m | 855k | 3 | ~18 |
| **No CodeGraph at all** | **$0.44** | **42s** | **523.6k** | **0** | **20** |

Native call count did not drop when CodeGraph was available (14 to 18 to 20, if
anything it rose). CodeGraph calls stacked on top of essentially the same native
exploration workload the no-codegraph run did unassisted. Full detail and the raw
transcripts: `docs/experiments/live-sessions/task1-repo-exploration.md`.

Quality check: no regression from dropping CodeGraph. The no-codegraph run was
arguably the most complete of the three (only one to name the frontend rotation
timer, `context_srv.ts`, and to point at exactly where to change settings,
`setting_authn.go`).

## Root cause, confirmed by reading the guide, not assumed

`packages/codegraph/installer/guide.py`'s managed `CLAUDE.md` block already had a
rule for this exact failure mode, added after a prior, different live finding (an
*editing* task where the agent called `get_context` once then chased half a dozen
more symbols via grep). That rule read: "Editing? Locate via `get_context`/
`search_code` for EACH symbol, not just the first." Task 1 above is a pure
exploration question, not an editing task, so this rule never applied to it. The
guide's own coverage had a real gap, not a vague "the agent should know better"
problem.

## Implementation

`packages/codegraph/installer/guide.py`: removed the `Editing?` gate from the
EACH-symbol rule, so it applies to any task. Added `impact_analysis` to the list
of locate-tools it names, and an explicit fallback condition ("grep only when a
tool call returns nothing for that symbol") instead of leaving "when to grep" 
implicit. Trimmed the reference ("Which tool") section to stay under the existing,
tested 400-token budget (`tests/test_installer_guide.py::
test_write_block_is_under_400_tokens`), since the rule addition alone pushed the
block over budget.

Not changed: `get_context`'s own code, ranking, or response content. This branch
is a guide/prompt-text change only, the cheapest possible instantiation of the
"routing" hypothesis, before building anything code-level (a deterministic router
or a Laya-based one), per this plan's own stated order: measure, then try the
cheapest fix, before anything more complex.

## Benchmark

**Local, verified:**
- All 24 tests in `tests/test_installer_guide.py` pass, including a new regression
  test (`test_guide_each_symbol_rule_applies_to_exploration_not_just_editing`)
  encoding this exact live finding, matching this repo's own convention of turning
  every real finding into a permanent test.
- `ruff check` / `ruff format --check`: clean.
- Full suite (`uv run pytest -q`): 1374 passed, 1 skipped, 1 failed
  (`test_git_hooks.py::test_cli_hooks_install_not_a_git_repo`). Confirmed
  unrelated: reproduced identically on `baseline/cost-control` with no changes
  from this branch, root-caused to a pre-existing Windows-specific test fragility
  (Rich/Typer console output word-wrapping the CLI's error message once the
  pytest temp-dir path gets long enough, splitting the asserted substring across
  a newline), not something this experiment touched or caused. Flagged as a
  separate background task, not fixed here (out of scope for this branch's one
  mechanism).
- Guide block re-measured: 1583 characters (about 396 tokens by the test's own
  4-chars/token heuristic), under the 400-token budget.

**Live session (round 4), not yet run:** same Task 1, this branch, fresh session,
Grafana harness reset to commit `9a1c9c83` (updated guide baked into CLAUDE.md).
Expect: native call count closer to the no-codegraph run's 20 only where CodeGraph
genuinely can't answer, and a meaningfully lower CodeGraph call count relative to
native calls than round 1/2, if the rule actually changes behavior rather than
being read and ignored (a real risk, per this repo's own round 8/10/11 history of
guide nudges that didn't fire).

## Quality impact

Not yet measured live. No content/schema change on CodeGraph's own side, so no
mechanism for this branch to change *what* CodeGraph returns, only what the guide
tells the agent to do with it.

## Latency impact

Not directly targeted by this branch (that's `exp/get-context-batching`, already
KEEP). Indirect effect possible if fewer total tool calls happen, tracked as part
of round 4's totals.

## Token impact

The mechanism this branch targets directly. If the guide fix works, fewer
redundant native searches for symbols CodeGraph already surfaced should show up
as fewer total tool calls and lower cumulative cache-read tokens, the same lever
round 9's historical win (batching) worked through.

## Failure cases

None yet, pending round 4. Real risk flagged above: this repo's own history
(rounds 10-11) shows guide nudges that were shipped, measured, and found to have
zero effect because the agent didn't act on them, a possible outcome here too,
not assumed away.

## Live result (round 4, Task 1, n=1)

Behavior changed as intended: native calls fell from 14-20 (rounds 1-3) to 5,
codegraph calls rose to 10. Cost did not improve enough: $0.59 vs $0.67 baseline,
$0.54 batching-only, $0.44 no-codegraph. Transcript analysis (see
`live-sessions/task1-repo-exploration.md`) shows why: cost tracks sequential model
turns (10 here, about 100k cache-read tokens each), and CodeGraph lookups are
inherently sequential where grep/Read fan out in parallel. One turn was a failed
call (fixed separately on `exp/batch-overflow-nonfatal`), one was redundant.

## Decision

**KEEP as a component, REJECT as a standalone cost fix.** The guide change does
what it claims (substitution) and costs nothing to keep, but on its own it moves the
agent from "greps a lot" to "queries CodeGraph a lot", each in sequential turns, so
total cost barely moves. It only pays off combined with changes that cut the number
of turns.
