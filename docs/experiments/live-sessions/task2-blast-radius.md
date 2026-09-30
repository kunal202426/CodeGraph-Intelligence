# Live session log: Task 2 (blast radius, read-only)

Why this task: Task 1 (find where auth lives) is a name-guessable subsystem, close
to the best case for plain grep. This repo's own cost log says CodeGraph's wins came
on structural questions (round 7: blast radius of a shared type, -58%). This task is
that shape, with a ground truth computed from the Grafana clone so quality is graded
on recall, not impression.

Task text (fixed, same every round, read-only so no Grafana reset is needed):

> I want to add a new required parameter to ValidateRuleGroupInterval in the
> alerting backend. List every place that calls it and every test that would be
> affected, so I know the full blast radius before I change it. Don't change any
> code.

## Ground truth (git grep on the Grafana clone at harness commit 9a1c9c83)

Direct call sites of `ValidateRuleGroupInterval` (3):

1. `pkg/services/ngalert/models/alert_rule.go:801`, inside `ValidateAlertRule`
2. `pkg/services/ngalert/provisioning/alert_rules.go:629`, in `UpdateRuleGroup`
3. `pkg/services/ngalert/provisioning/alert_rules.go:695`, in `ReplaceRuleGroup`

The funnel: `ValidateAlertRule` (which contains call site 1) is called from
`pkg/services/ngalert/store/alert_rule.go:1796` and from three tests in
`pkg/services/ngalert/models/alert_rule_test.go` (lines 1192, 1242, 1289).

There are no direct test references to `ValidateRuleGroupInterval` itself. Test
impact is transitive, through `ValidateAlertRule` and the two provisioning methods.

## Rubric (score each answer)

| Item | Points |
|---|---|
| Finds all 3 direct call sites | 3 (1 each) |
| Identifies `ValidateAlertRule` as the funnel for call site 1 | 1 |
| Finds the store caller (`store/alert_rule.go:1796`) | 1 |
| Finds the `alert_rule_test.go` callers of `ValidateAlertRule` | 1 |
| Names the two provisioning entry points (`UpdateRuleGroup`, `ReplaceRuleGroup`) | 1 |
| Says test impact is transitive, not a direct reference | 1 |
| Invents a caller that does not exist | -2 each |
| **Max** | **8** |

## Rounds

Not yet run. Planned order: (A) no CodeGraph, (B) the combined branch. Both fresh
sessions, same task text, Grafana at `9a1c9c83`.

### Session 1: combo/turn-reduction (batching + overflow fix + each-symbol guide)

```
cost: $0.39   api: 40s   active: 1m16s   cache_hit: 89%
input: 12   output: 33   cache_read: 516.6k   cache_write: 61.2k
turns: 6   codegraph calls: 3 (project_brief, impact_analysis, get_context, all in ONE turn)
native calls: ~13 (Grep/Glob, fanned out 2-3 per turn)   failed calls: 0
```

Turn breakdown (from the transcript, totals match the panel exactly):
T1 ToolSearch + Grep, T2 the three CodeGraph calls in parallel, T3 Grep x2 + Glob,
T4 Grep x3, T5 Grep x2, T6 final answer.

**Rubric score: 8/8.** All 3 direct call sites, `ValidateAlertRule` as the funnel,
the store caller (`store/alert_rule.go:1795`, reached via `InsertAlertRules:567` and
`UpdateAlertRules:660`), the three `alert_rule_test.go` callers (1192, 1242, 1289),
both provisioning entry points, and an explicit note that test impact is
transitive. **Zero invented callers**: every extra location it cited was verified
against the clone (`rules_provisioner.go:68`, `api_provisioning.go:529`,
`api_convert_prometheus.go:547`, `rulesync/syncer.go:497`,
`rule_validator_test.go:148/168`, `snapshot_mgmt_alerts_test.go:473`, and 39 calls in
`alert_rules_test.go`). It also flagged its own limits (enterprise repo not
searched, indirect tests not enumerated, CodeGraph's caller list was truncated so it
cross-checked with grep).

Behavior note: unlike round 4, it issued its three CodeGraph calls in parallel in
a single turn, and used CodeGraph first with grep as verification, which is the
substitution-plus-fan-out pattern the earlier analysis said was missing.

### Session 2: no CodeGraph (control)

```
cost: $0.34   api: 32s   active: 57s   cache_hit: 88%
input: 10   output: 22   cache_read: 395.6k   cache_write: 55.3k
turns: 5   codegraph calls: 0   native calls: 15 (3 Read, 12 Grep), fanned out 2-3 per turn
```

Turns: T1 Grep x2, T2 Read x3, T3 Grep x3, T4 Grep x2, T5 final answer.

**Rubric score: 8/8, zero invented callers.** Verified against the clone:
`alert_rules_test.go:3339/3457` (helper constructors), `alert_rule_test.go:1232`,
`store/alert_rule_test.go:56` (random BaseInterval), and the separate API-layer
interval check at `api_ruler_validation.go:265-272` all exist. Two small
inaccuracies: it says "about 35" `ReplaceRuleGroup` calls in `alert_rules_test.go`
(actual 32), and calls `alert_rules.go:758` "recursive" when it is
`ReplaceRuleGroups` calling `ReplaceRuleGroup`.

### Comparison

| | Control (no CodeGraph) | Combo (CodeGraph) |
|---|---:|---:|
| Cost | **$0.34** | $0.39 (+15%) |
| Active time | **57s** | 1m16s |
| Turns | **5** | 6 |
| Cache read / write | 395.6k / 55.3k | 516.6k / 61.2k |
| Rubric | 8/8 | 8/8 |

Quality is a tie on the rubric. Depth differs slightly: the CodeGraph run traced
one more hop of production callers (`InsertAlertRules:567`, `UpdateAlertRules:660`,
`api_convert_prometheus.go:547`, `rulesync/syncer.go:497`), all verified real. The
control instead found the test-helper construction sites, the store test's random
base interval, and the API-layer check that is unaffected. The CodeGraph run also
still spent three grep turns cross-checking (it noted the caller list was
truncated), so CodeGraph was added on top of a full grep workflow, not instead of it.

**Result: control wins on cost and time, quality tied.**

### Where the money goes (control session, from the transcript)

Pricing inferred from the panel: $0.30 per million cache-read, $3.75 per million
cache-write reproduces the $0.34 exactly (write $0.207, read $0.119).

Turn 1 wrote 41k tokens to cache and every later turn re-read a ~85k prefix. The
per-session attachments (excluding what looks like a prompt snapshot copy) total
about 26k tokens (an earlier draft said 34k and 15.7k for the deferred list; the
transcript stores each tool name twice, so that was double counted):

| Source | ~Tokens |
|---|---:|
| Deferred MCP tool names list (543 tools, Vercel alone is 47%) | 7.4k |
| Skill listing | 8.3k |
| CLAUDE.md / instructions | 3.5k |
| MCP server instructions | 3.1k |
| Hooks (session start) | 1.9k |
| Agent listing, session context, misc | 1.4k |

Measured directly: turn 1 wrote 41k tokens ($0.155) and turns 2-5 re-read an ~85k
prefix, so prefix-related cost is roughly $0.24 of the control's $0.34, identical
whether or not CodeGraph is connected. The part that config can remove (deferred
list, skill listing, MCP instructions) is about 15k tokens, worth roughly $0.08 per
5-turn session, so about 20 to 25 percent, not more.

### Session 3: hook only (exp/prefetch-hook, no MCP server, original CLAUDE.md)

```
cost: $0.36   api: 49s   active: 1m16s   cache_hit: 88%
input: 10   output: 30   cache_read: 398.9k   cache_write: 56.0k
turns: 5   native calls: ~13 (Grep x8, Read x4, Bash x1; one search failed)
```

The hook fired: the transcript has a `hook_additional_context` attachment (2,187
chars) containing the prefetch text. The model then re-ran the same investigation
as the control (T1 Grep x2, T2 Read + Grep x3, T3 Read x2 + Grep, T4 Grep x2 + Bash,
T5 answer): identical turn shape, cost within $0.02 of the control. It opened with
"I'll check the prefetched call graph against the source with grep, since static
graphs can miss callers". The injected context added about 550 tokens to every later
turn and saved no turns.

**Rubric: 8/8 on the fixed items.** The answer also cited related code the other runs
did not: `ReplaceRuleGroup` in an api interface at `api_provisioning.go:81` and
`fakeRuleService` at `syncer_test.go:50`. It corrected the prefetch's 38 test call sites
to 39 (checked against the source: 39 is right, one of them is a commented-out line).
The interface and fake citations were not source-checked before the Grafana clone was
deleted, so their accuracy is unverified. The no-CodeGraph run had already found the
hand-written duplicate check at `api_ruler_validation.go:265-272`. The re-verification
did produce one correction, so suppressing it would trade some accuracy for cost.

### Task 2 summary (n=1 each)

| Variant | Cost | Turns | Rubric |
|---|---:|---:|---|
| no CodeGraph | **$0.34** | 5 | 8/8 |
| prefetch hook only | $0.36 | 5 | 8/8 (extra citations unverified) |
| CodeGraph MCP combo | $0.39 | 6 | 8/8 |
