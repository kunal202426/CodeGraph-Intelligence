# Experiment: exp/prefetch-hook

## Hypothesis

Cost in a Claude Code session is roughly (sequential model turns x the ~85k-token
prefix re-read each turn) plus a one-time prefix write. Native exploration takes
about 5 turns because it fans out parallel greps; CodeGraph tool calls are
sequential (each answer decides the next query) and were added on top of that
grep workflow, so every CodeGraph variant measured so far cost more than no
CodeGraph. A local hook that runs before the model turn, resolves the symbols in
the prompt against the index, and injects the definition, call sites, callers and
tests removes those lookup turns from the loop instead of speeding them up.

Expected on a Task 2 style question: 2 to 3 turns instead of 5 to 6, with no
ToolSearch turn because no MCP tool is needed. From the measured prices ($0.30/M
cache read, $3.75/M cache write) that is roughly $0.20 to $0.25 against $0.34
(no CodeGraph) and $0.39 (CodeGraph MCP). This is an estimate from the cost model,
not a measurement.

## Baseline

`baseline/cost-control` at `e0fb37f`, isolated from every other experiment branch.
Live comparison targets on Task 2 (Grafana, read-only blast radius): no CodeGraph
$0.34 / 5 turns / 8 of 8, CodeGraph combo $0.39 / 6 turns / 8 of 8.

## Implementation

`packages/codegraph/prefetch.py`, run as `python -m codegraph.prefetch` so the hook
skips the CLI's imports. Wired through Claude Code's documented `UserPromptSubmit`
hook (stdin has `prompt` and `cwd`; `hookSpecificOutput.additionalContext` is added
to the model's context; 10,000 character cap per hook).

1. Extract code-shaped identifiers (camelCase, PascalCase, snake_case, length 5+).
   Ordinary English matches nothing, so most prompts cost nothing.
2. Resolve each name to exactly one callable entity. Ambiguous names are skipped
   rather than guessed.
3. Build per symbol: definition and signature, callers to depth 3 with exact
   call-site lines (recovered from each caller's indexed source), indirect callers,
   and the test functions that call the symbol or its direct callers (text match,
   because test calls go through variables and never become graph edges).
4. Cap under 9,000 characters. Any failure prints nothing.

Rules only. No Laya, no model. Classification of question type and a
confidence check on the result are the decisions where a small decision model could
later replace a rule, and only if the rule measurably fails.

## Local verification (done)

- 13 new tests in `tests/test_prefetch.py`, all pass. Ruff clean.
- On the real Grafana index with the exact Task 2 prompt: about 500 tokens injected,
  1.2 s per invocation (0.6 s of it is Python imports).
- Correctness against the grep ground truth: the 3 direct call sites at exactly
  lines 801, 629 and 695; the funnel through `DBstore.validateAlertRule`,
  `InsertAlertRules`, `UpdateAlertRules`; tests in `alert_rule_test.go` (3 call
  sites, exact), `rule_validator_test.go` (2, exact), `snapshot_mgmt_alerts_test.go`
  (1, exact), `alert_rules_test.go` (38, grep says 39 including one commented line).
- Two defects found and fixed by running it on real data, both covered by tests:
  the graph alone listed no tests (test calls do not resolve to edges), and a
  fake's own declaration matched its own name.

## Known limits

- Only helps when the prompt names a resolvable identifier. Prompts like "find where
  authentication is handled" (Task 1) match nothing and get no help. That case needs
  a keyword or semantic retrieval mode, which is where a decision model to judge
  "is this result set confident enough to inject" earns its keep.
- 1.2 s added to every prompt that names a symbol. A persistent local server behind
  an HTTP hook would remove the Python start-up.
- Static call graph: dynamic dispatch and reflection can hide callers. The injected
  header says so.

## Live benchmark (Task 2, Grafana, hook only, n=1)

$0.36, 5 turns, rubric 8/8 on the fixed items (extra citations not source-checked). The hook
fired and its content was accurate, but the model re-verified everything with grep
and took the same 5 turns as the no-CodeGraph control ($0.34). The injected
~550 tokens cost about $0.01 and saved no turns. Full detail:
`live-sessions/task2-blast-radius.md`.

## Decision

**REJECT as a cost reducer on completeness-style questions.** The mechanism works
(fires, accurate, fail-open, cheap) but the model's verification turns are its own
policy and did not shrink. Keep the module only if a later experiment finds a
prompt class where the model does trust injected context.
