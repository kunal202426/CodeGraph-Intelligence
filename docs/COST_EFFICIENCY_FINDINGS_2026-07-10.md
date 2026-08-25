# Cost/efficiency A/B findings — and what it means for the product

**What this is:** a running log of controlled cost/quality A/B tests comparing CodeGraph on
vs. off, across multiple codebases and dates — **newest first.** Every round reports real
`/usage` cost, not estimated tokens, including the rounds where it came out worse.

## Post-round-11 (2026-08-25): the nudges were never the problem — `impact_analysis` was returning the wrong number

Rounds 9-11 spent three iterations trying to get an agent to call `impact_analysis`. While
grounding a brainstorm about *why* that kept failing, a single sanity check invalidated the
entire premise:

```
impact_analysis(ValidateRuleGroupInterval)  ->  total: 1
grep -rn "ValidateRuleGroupInterval("       ->  3 real call sites
```

**Even a perfectly obeyed nudge would have handed the agent "1 caller" — which reads as
"safe to change."** Every round since 9 edited this exact function believing it had one
caller. Three fixes optimized the *delivery* of an answer that was itself wrong.

Two stacked bugs, found by digging rather than theorizing:

**Layer 1 — qualified cross-package calls fell through to `external:`.** Every parser drops
the qualifier (`models.Foo()` → a bare `?call:Foo` edge), then `_resolve_call` checked
same-file, then the file's import table (keyed by *module* name for a package import, so it
can never match the function), then a Java-only same-package branch, then gave up. Fixed by
resolving a bare callee against the directories a file imports — only when exactly one
imported package exports that name, since the qualifier is gone and a wrong edge is worse
than a missing one.

**Layer 2 (the root) — Go import resolution matched only the import path's LAST segment**,
then took the alphabetically-first `.go` file in any directory with that name. On Grafana:

| Import | Resolved to | Correct? |
|---|---|---|
| `time` | `pkg/tsdb/azuremonitor/time/` | stdlib hijacked |
| `context` | `pkg/services/grpcserver/context/` | stdlib hijacked |
| `.../ngalert/models` | `pkg/cmd/grafana-cli/models/` | wrong package |

Common package names (`models`, `types`, `util`, `api`, `store`) repeat constantly on a real
repo, so a large share of cross-package edges were wired to the **wrong** package — not
missing, *wrong*. Present since Go support shipped. It also explains why the layer-1 fix did
nothing on Grafana despite passing its own tests: it resolves against the directories a file
imports, and those directories were wrong. Fixed by treating a single-segment path as stdlib
by definition, then matching the longest import-path suffix that is a real directory.

**Verified on the real repo, before/after a `--force` reparse:**

| Metric | Before | After |
|---|---|---|
| `impact_analysis` on `ValidateRuleGroupInterval` | 1 caller | **11** |
| Direct call sites resolved | 1 of 3 | **3 of 3** |
| Repo-wide resolved imports | 172,786 | **175,369** (+2,583) |
| `import "time"` | `pkg/tsdb/azuremonitor/time/` | `external:time` |

The 11 includes `DBstore.InsertAlertRules`, `RoutePutAlertRuleGroup` and the provisioning
path — the actual blast radius, invisible for every prior round.

**Method note worth keeping:** this was found by checking a tool's output against `grep`
ground truth, not by reasoning about agent behavior. Three rounds of behavioral theorizing
produced three fixes to the wrong layer; one verification pass found the real bug. When a
tool's answer and the agent's behavior disagree, **verify the tool first** — the cheaper
check, and here the correct one.

**Consequence for the earlier rounds:** rounds 9-11's cost figures stand, but their
conclusions about nudge effectiveness are uninterpretable — they measured agent response to
incorrect data. Any re-test has to run on a correctly-resolved index.

## Rounds 10-11 (2026-08-25): two nudges shipped, neither fired — and the reason is an axis mismatch, not a threshold

Same editing prompt on Grafana, same protocol, run after shipping two structural fixes aimed
squarely at round 8's finding (`impact_analysis` never being called):

1. `impact_analysis`'s tool description rewritten to "prefer this over grep for who
   calls/uses this" (round 8, commit `33368b5`).
2. `get_context` emits a warning naming the exact entity_id when its caller list is
   truncated at `_NEIGHBOR_CAP` (round 8, same commit).
3. `search_code` attaches a per-hit `hint` field when a hit's real caller count exceeds the
   same cap (round 11 prep, commit `ca682ea`) — added specifically because round 10 stayed
   in `search_code`/`Read` the whole session and so never saw fix #2 at all.

| Round | Cost | codegraph calls | `impact_analysis` calls | Entry points | Correctness |
|---|---|---|---|---|---|
| 10 | $2.91 | 1 batched `get_context` | **0** | 2 of 3 (App-Platform skipped, flagged as follow-up) | Safe by design — made the floor opt-in (`minInterval > 0`), sidestepping the bug class |
| 11 | **$1.20** | 1 batched `get_context` + `search_code` | **0** | **3 of 3** (best coverage of any round, resolved the old `TODO`) | **Two separate flaky-test regressions** |

**Round 11 is the cheapest round yet AND the most complete fix AND the most broken.** All
three of those are true simultaneously, which is the whole finding.

**Regression 1 — a straight repeat of round 9's.** Hardcoded, unconditional
`MinRuleEvaluationIntervalSeconds = 10` on `ValidateRuleGroupInterval`, while
`models/testing.go:124`'s shared generator still draws `IntervalSeconds: rand.Int63n(60) + 1`
and `store/testing.go:142` still configures `BaseInterval: 1s`. ~15% of randomized store
tests become newly flaky, exactly as in round 9.

**Regression 2 — new, and worse, because the agent explicitly checked for it and got it
wrong.** The transcript states: *"Good, all tests use interval ≥ 10s, so a 10s floor is
safe."* Verified by hand against the real file, that is false:
`api_ruler_validation_test.go`'s `TestValidateRuleNode_NoUID` builds
`interval := cfg.BaseInterval * time.Duration(rand.Int64N(10)+1)` where `config()` draws
`BaseInterval` from `[3, 99]` seconds — so `interval` can be as low as 3s, and that value
feeds a `ValidateRuleNode(...)` call asserted with `require.NoError`. A real, seed-dependent
failure in a test the agent specifically claimed to have cleared.

**The actual lesson, and why it invalidates the current fix direction:** both nudges key on
**caller count exceeding a cap**. Across rounds 9, 10, and 11, caller count has *never* been
the failing axis — every function involved has a handful of callers at most. The real,
thrice-repeated failure is **transitive reachability through randomized test scaffolding**,
several hops from the edit site, which a bounded grep-and-read verification gets wrong *even
when explicitly attempted*. High-fan-in and "reachable from a fuzzing test generator" are
orthogonal properties; three rounds of tuning a high-fan-in signal produced zero
`impact_analysis` calls because the tool was answering a question nobody was asking.

Also worth recording: **cost is now clearly decoupled from correctness.** Round 11 was
cheapest *and* most broken; round 10 was priciest *and* safest. Any framing that treats
falling cost as evidence the tooling is working would be reading noise as signal.

**Not fixed this pass** — logged deliberately without a same-day fix, because three
consecutive rounds of "ship a plausible heuristic, then discover it addressed the wrong
axis" is itself the strongest argument against shipping a fourth one un-designed.

## Round 9 (2026-08-14, same day): the structural fix worked — cheapest round yet, and it exposed a real verification gap

Same prompt, same repo, one session after Round 8's structural fix shipped (`impact_analysis`'s
tool description now says "prefer this over grep"; `get_context` now points at it by name when
an entity's caller list is truncated).

**Cost: $1.14 — the cheapest of all four editing rounds by a wide margin** (round 1: $2.36,
without-codegraph: $2.48, round 2: $3.69). Session was the shortest too: 3m4s active vs. 13m35s+
for round 2.

**What actually drove it down:** one `get_context` call, batched with 3 queries in a single
round-trip (`["MIN_TIME_RANGE_STEP_S", "alert rule evaluation interval validation",
"IntervalSeconds validate"]`) — the multi-query batching feature the guide has documented since
early in this report, unused in every prior round. That one call surfaced enough (a test mutator
named `WithIntervalSeconds`, an `IntervalSeconds` field) to let 2-3 targeted native regex
searches pin down the exact validation function directly. Still zero `impact_analysis` calls —
but this round didn't need many follow-ups, because the first call was information-dense instead
of narrow.

**The fix itself was also the best of all four rounds, architecturally.** Every previous round
duplicated the check at 2-3 entry points. This one found `ValidateRuleGroupInterval` in
`alert_rule.go` — the single choke point `AlertRule.ValidateAlertRule` and both
`UpdateRuleGroup`/`ReplaceRuleGroup` already funnel through — and fixed it there, once. Six
lines, one function, no duplication.

**But that choke point was avoided by name in round 8.** Round 8's agent explicitly identified
this exact function as too risky to touch — "exercised pervasively by internal store tests
configured with a 1-second base interval for speed" — and deliberately added the check at
higher-level entry points instead, specifically to avoid that blast radius. Round 9's agent
found the better location but skipped the verification that earned round 8's caution.

**Confirmed real, not hypothetical, by direct inspection of the call graph:**
- `pkg/services/ngalert/store/testing.go:142` sets the store test suite's config to
  `BaseInterval: 1 * time.Second`.
- `pkg/services/ngalert/models/testing.go:124` — the shared random alert-rule generator used
  throughout that suite — sets `IntervalSeconds: rand.Int63n(60) + 1`, uniform over [1, 60].
- `pkg/services/ngalert/store/alert_rule.go:1717` calls `alertRule.ValidateAlertRule(cfg)` inside
  `InsertAlertRules`, which 15+ store tests call directly.

With the new `< 10s` floor and no change to the generator, **~15% of randomly generated test
rules now fail insertion** — not a deterministic break, a newly introduced *flaky* test, which is
worse: it passes most CI runs and fails unpredictably on whichever random seed draws a value
under 10.

**Honest conclusion, not spun either direction:** this is the strongest cost result of the whole
investigation, and the batching + fewer-but-denser-calls pattern is real, reproducible signal
worth keeping. But it is not evidence that codegraph makes an agent more careful — the cheapest
run was cheap partly *because* it verified less than the priciest one did. Cost and correctness
moved independently this round, and conflating them would be exactly the kind of self-serving
reporting this document has tried not to do. None of the four MCP tools touched this session
(`search_code`, `get_context`, `impact_analysis`, and the guide/description nudges from round 8)
answer "what existing test fixtures would violate a constraint I'm about to tighten" — a
materially different question from "who calls this." **Not fixed this pass — logged as a real
product gap, next up.**

## Round 8 (2026-08-14): a real editing task on Grafana — three MCP infra bugs found and fixed, then codegraph got *more expensive* the more it was used

A live A/B on an actual editing task (add a missing minimum-interval validation check to
Grafana's alert-rule backend), not a Q&A session — the first one this report has run.

**Phase 1 found the tools weren't reachable at all, not a model-behavior problem.** Five
reproductions of "the agent ignores codegraph on editing tasks" turned out to be three
infrastructure bugs, none of them the model's fault: MCP boot loaded torch synchronously and
took 25.6s cold against Claude Code's 30s connect timeout (warm cache connected, cold cache got
dropped *silently*, zero error shown); a staleness check walked the full repo synchronously,
twice, per call (225s on 16k files); and concurrent walks crashed the interpreter outright. All
three fixed (embeddings moved to a worker subprocess, staleness made single-flight and
non-blocking). Full detail in the README/DECISIONS changelog and commit history — not repeated
here, this section is the cost result once the tools actually worked.

**Phase 2, with the infra fixed, ran the real A/B — three rounds, same prompt each time:**

| Round | Cost | codegraph calls | Entry points fixed |
|---|---|---|---|
| With-codegraph #1 | $2.36 | 1 (`get_context`) | 2 |
| Without-codegraph | $2.48 | 0 | 2 (+ wrote real tests) |
| With-codegraph #2 (after a guide fix) | $3.69 | 5 (`search_code` x2, `get_context` x3) | **3** |

Round 1 barely used codegraph (one call, then grep for everything else) — cost landed close to
the without-side, which is the expected outcome when a tool is available but not actually used.
The guide's edit rule was rewritten ("locate via `get_context`/`search_code` for **each**
symbol, not just the first") specifically to fix that, and it worked: round 2 called codegraph
5x instead of 1x.

**But round 2 cost more, not less — and the honest reason is not "it did more work."** First
pass at writing this up credited the higher cost to round 2 finding a genuinely missed third
entry point (Grafana's provisioning/Terraform path). That's true, but it's an excuse, not an
explanation: **`impact_analysis` and `trace_path` were called zero times**, in a session whose
entire task was "find every place that validates this interval" — the textbook
`impact_analysis` question. Instead, the transcript shows ~30 native `Searched`/`Read` calls
alongside the 5 codegraph calls, chasing `SchedulerBaseInterval`, `RuleLimits`,
`ValidateInterval` call sites, and provisioning entry points one grep at a time. The codegraph
calls were **additive, not substitutive** — used alongside an equally long grep chain, not
instead of it. And because this project's own cost model is dominated by cumulative cache-read
across a session (documented as far back as this doc's first entry), every extra round-trip
compounds the running total regardless of which tool it was. More tool calls of any kind, in one
continuous session, costs more — that's mechanical, not a quality tradeoff.

**Root cause, not softened this time:** the guide fix taught the agent to call codegraph more
often, but not to prefer it *over* grep for the specific shape of question it's built for
("what else calls/uses this"), and nothing in the guide or the tool descriptions currently
signals "if you're about to grep for the third related symbol, stop and use `impact_analysis`
instead." `get_context`'s multi-query batching (up to 5 names in one call) also went unused here
— five separate `get_context`/`search_code` calls where 1-2 batched calls would have covered the
same symbols in fewer round-trips.

**This round is not a codegraph win or loss — it's a genuine product gap**, logged honestly:
the tool got used more often without getting used *better*, and the guide doesn't yet teach the
difference. See "Not fixed this pass" below for the concrete fix ideas this produced.

## Round 7 (2026-08-11): Grafana (97,239 entities, 16,046 files) — a near-tie, and why

The biggest repo tested yet by a wide margin — ~73x JobHuntPro's entity count, ~344x
LedgerGuard's. Getting here required fixing two real bugs first, not just running the
comparison: a server boot sequence that blocked the MCP handshake on a large repo's staleness
scan (fixed by backgrounding it), and — more seriously — a genuine deadlock where loading the
embedding model off the main thread while the asyncio event loop was running froze the server
indefinitely (confirmed by direct reproduction: the identical warmup call completed in ~31s run
synchronously vs. hanging 280s+ with zero progress dispatched through the server's real
`anyio.to_thread` path). Both are logged in the codebase's own commit history, not repeated
here — relevant context for why this round's numbers include a real, if large, one-time
first-connect cost (70-90s+) that smaller-repo rounds never paid.

**Methodology:** same continuous-session, cumulative-delta protocol as round 6. Both sides ran
the identical 3 questions in one session each; MCP genuinely removed (`claude mcp remove
codegraph`) for the without-side, not prompt-suppressed.

| Question | With codegraph | Without codegraph | Delta |
|---|---|---|---|
| Architecture overview | $0.26 (81% cache hit) | $0.26 (84% cache hit) | Tie |
| Dashboard alerting trace | $0.57 (93% cache hit) | $0.58 (93% cache hit) | Without −$0.01 |
| Dashboard model shape/blast-radius | $1.03 (96% cache hit) | $1.06 (96% cache hit) | With −$0.03 |
| **Total** | **$1.03** | **$1.06** | **With −$0.03 (−2.8%)** |

**Honest read: this is a tie, not a win.** A 2.8% margin is inside measurement noise for a
3-question sample. Two real reasons, not excuses:

1. **Grafana's own docs are unusually strong.** Nearly every major subsystem has a
   directory-scoped `AGENTS.md` (alerting, unified storage, docs, journeys) plus a normal
   README — this repo is already pre-optimized for AI-agent navigation in a way neither
   LedgerGuard nor JobHuntPro was. Grep-on-a-well-documented-repo is a much stronger baseline
   than grep-on-an-undocumented-one; the scale-dependence advantage this report has tracked
   since round 1 assumes the baseline has to work harder than that.
2. **Question 3 was shaped exactly wrong for what `impact_analysis` tracked at the time.**
   "What would break if I changed the Dashboard *data model's shape*" is a type/struct-shape
   question; `impact_analysis` only walked the call graph (transitive callers), and a
   struct/interface has no callers — only field/param/return-type references. It correctly
   returned `total: 0` on every type query, which reads as "safe to change" but actually meant
   "invisible to this tool." The without-side's plain grep for the type name found real answers
   `impact_analysis` structurally couldn't produce, and won that specific question because of
   it — not because grep is better at scale, but because the tool was blind to the exact shape
   of the question asked.

**Fixed the same day, then re-measured:** `impact_analysis` now checks the resolved entity's
kind — functions/methods still get the call-graph blast radius; a class/interface/type_alias
gets a signature-text usage search instead (word-boundary match over the same indexed data, no
re-index required). Verified directly against this exact Grafana index before re-testing:
`DashboardDTO` went from `total: 0` to 30 real usages (`BackendSrv.getDashboardByUid`,
`getDashboardFolderTitle`, and 28 others).

### Question 3, re-run standalone (fresh session each side, not a continuation)

| | Cost | Notes |
|---|---|---|
| With codegraph | **$0.43** | 5 `impact_analysis` calls, 89% cache hit. Found two real blast radii: legacy frontend `DashboardModel` (49 usages) and backend spec types across all 5 API versions (62 usages, incl. codegen/deepcopy/OpenAPI implications). |
| Without codegraph | **$1.03** | Dispatched Claude Code's own Explore subagent (77% of session cost) running multiple background searches. Found comparable depth (schema version freeze, conversion functions, unified-storage compat contract, 38 alerting files referencing `DashboardUID`). |
| **Delta** | **With −$0.60 (−58%)** | |

**This is the win the near-tie was missing, on the exact question that exposed the gap.** Both
answers were genuinely good quality — this isn't a quality tradeoff, codegraph got comparable
depth for less than half the cost. The mechanism matches JobHuntPro round 6's pattern exactly:
the harder/deeper the question, the more the gap grows in codegraph's favor, and it grows
sharply once the without-side is forced to dispatch Claude Code's Explore subagent to compensate
for a capability the graph tool didn't have. Note this specific re-test used fresh standalone
sessions per side (not the continuous-session protocol every other number in this report uses)
to isolate the one changed variable — not directly poolable with the round's own $1.03/$1.06
continuous-session totals above, but the before/after contrast (tool blind to the question →
tool answers it directly) is the real finding here, not the exact dollar figure.

## Round 6 (2026-08-07): first test on a genuinely large repo — JobHuntPro (1321 entities, 187 files)

Every round before this one ran on LedgerGuard — 47 files, 244 entities. The scale-dependence
claim this whole report leans on (round 1's competitor research: near-parity on small/medium
repos, a clear win only once a codebase gets large) had never actually been tested with our
own data on a large repo. JobHuntPro is ~5.4x LedgerGuard's entity count and genuinely
cross-language: a Chrome MV3 extension (JS), a Node/Express backend, a separate Python/FastAPI
backend, and a React frontend — four sub-apps, two backend languages.

**Methodology, corrected mid-run (logged honestly, not smoothed over):** the original plan
called for 3 fresh, isolated sessions per side. In practice all 3 questions were asked as
follow-ups in one continuous session per side (matching how a real developer actually works,
and matching every earlier round's actual methodology) — caught when a "fresh session" cost
figure turned out to be a cumulative total including the prior question. Recomputed every
number below as per-question deltas from the cumulative total, not standalone figures.
Also confirmed via the `/usage` panel's model breakdown that both sides ran 100% Sonnet, 0%
Haiku throughout — not a confound between conditions.

**Real methodological wrinkle worth keeping:** the without-codegraph session dispatched
Claude Code's own built-in **Explore subagent** for the two harder questions (visible in the
usage panel as 71-79% of that session's cost). This is an honest, representative baseline —
Explore is a native capability every Claude Code user has with zero setup — but it means
"without codegraph" here measures against Claude Code's own agentic exploration tooling, not
a naive grep-and-read loop. Worth remembering when comparing this number to any published
benchmark that assumes a dumber baseline.

| Question | With codegraph | Without codegraph | Delta |
|---|---|---|---|
| Architecture overview | $0.46 (90% cache hit) | $0.38 (91% cache hit) | Without −$0.08 |
| Cross-service data flow (Node → Python) | $0.56 (94% cache hit) | $0.71 (93% cache hit) | With −$0.15 |
| Impact/blast-radius of a shared data shape | $0.80 (96% cache hit) | $1.03 (94% cache hit) | With −$0.23 |
| **Total** | **$1.82** | **$2.12** | **With −$0.30 (−14%)** |

**With codegraph wins overall, and the pattern is directional, not noise.** Without-codegraph
won only the easy question — and the answer's own opening line explained why: *"This
README/PROGRESS gives a very solid picture already"* — JobHuntPro has an unusually complete
hand-written README with an architecture table, so reading two docs was genuinely competitive
with walking the graph for that specific question. Codegraph won both harder questions
(cross-service trace, blast-radius) by a **growing margin** as the tracing got deeper — exactly
the shape the competitor research predicted: the advantage shows up on hard, cross-cutting
questions on a large repo, not on ones a good README already answers. Answer quality was
comparable on all 3 questions on both sides (same core findings, similarly precise file:line
citations) — this is a real cost result, not a quality tradeoff dressed up as one.

**This is the first data point at this scale, not a settled conclusion** — same caveat every
earlier round has carried: one repo, 3 questions, real but limited. It does, however, mark the
first time in this whole cost-efficiency investigation that codegraph won on total $ cost
against a real, unmodified baseline (round 5's `project_brief` win was isolated to one
feature, not the whole with/without-codegraph question) — consistent with, not just assumed
from, the scale-dependence claim this report has cited since round 1.

## Round 5 (2026-07-13, same day): properly isolated `project_brief` A/B — a real, modest win

Redid round 4 with the discipline it was missing: 3 different cold-start questions (not 1),
each run twice in a fresh session — once letting `project_brief` fire normally, once with an
explicit instruction to skip it while still allowing `get_context`/other codegraph tools.
Every session's actual tool calls were confirmed from the in-transcript tool log (not
reconstructed after the fact from a usage-panel side effect) — the first attempt at the
"without" prompt (`"Do not call project_brief for this question"`) turned out to make the
agent avoid the whole codegraph toolset, not just that one tool, which would have silently
reproduced round 3's already-answered with/without-codegraph question instead of isolating
`project_brief`; caught via the tool log showing zero MCP activity, fixed by making the
prompt explicit that other codegraph tools were still expected.

| Question | With `project_brief` | Without (codegraph tools still used) | Delta |
|---|---|---|---|
| Architecture overview | $0.37 (85% cache hit) | $0.48 (77% cache hit) | With −23% |
| Transaction flow walkthrough | $0.70 (91% cache hit) | $0.67 (90% cache hit) | Without −4% |
| Core abstractions/entry points | $0.48 (87% cache hit) | $0.52 (82% cache hit) | With −8% |
| **Total** | **$1.55** | **$1.67** | **With −7%** |

**`project_brief` wins.** 2 of 3 questions favor it on raw $ cost, the third is a near-wash
slightly against it, and — more consistently than the $ number — **cache hit rate is higher
with `project_brief` on all 3 questions**, including the one it lost on cost. A modest,
real, properly-isolated improvement: not the dramatic win a from-scratch feature sometimes
promises, but a genuine one, and importantly not a regression on any of the 3 questions
tested. Consistent with round 3's finding that codegraph is closest to break-even (not a
clear win) on a repo LedgerGuard's size — `project_brief` nudges that break-even point
further in codegraph's favor rather than transforming it.

## Round 4 (2026-07-13, same day): a single-question `project_brief` sanity check — inconclusive, not a real measurement

After shipping `project_brief`, ran one cold-start question ("what's the architecture, what
should I know before changing it") on LedgerGuard, once with codegraph (`project_brief` +
`get_context` confirmed via the `codegraph MCP` usage indicator and the tool's savings line
in the response) and once without (confirmed by the *absence* of both). **With: $0.48, 87%
cache hit. Without: $0.29, 78% cache hit** — codegraph cost ~66% more on this single
question, the opposite direction from what `project_brief` was built to achieve.

**Not treated as a real finding.** A single question is exactly the kind of sample round 3
already showed can't be trusted alone — individual questions in that 5-question run swung
2-3x in either direction while the *total* landed within noise of parity. This round also had
a session-labeling mix-up (which run was "with" vs "without" got confused mid-test and had to
be reconstructed from the `codegraph MCP` usage indicator after the fact), which is its own
signal that the test wasn't run cleanly enough to trust. Logged rather than acted on: a
proper isolation of `project_brief`'s effect needs the same discipline as round 3 (multiple
questions, verified-clean session labeling throughout, not just at the end) — deferred until
the next full validation pass rather than burning more of this session re-running it now,
since `project_brief` is a low-risk additive tool (one bounded call, not a replacement of
anything round 1-3 already validated) and not an active regression that needs urgent
confirmation either way.

## Round 3 (2026-07-13): re-measured after the guide + payload + resolver/search fixes — gap closed

Between round 2 and this measurement, three more things shipped: the guide now defaults
`get_context` to `detail="full"` on understanding questions (not just known edit targets),
plus this session's resolver fixes (tsconfig path aliases, ambiguous-name ceiling) and
search-ranking rewrite (identifier segmentation, multi-term boost, test/generated-file
down-ranking, low-confidence warning, diversity cap) — see
[COMPETITOR_ANALYSIS_2026-07-11.md](COMPETITOR_ANALYSIS_2026-07-11.md) for what those are and
why. Re-ran the same 5-question shape on LedgerGuard, clean git baseline verified before each
session, `codegraph mcp remove`/re-`add` used to get a genuinely MCP-disconnected baseline
(confirmed in-transcript: *"CodeGraph's MCP tools aren't connected in this session, so I'll
explore the source directly instead"*).

| Q | With codegraph | Without codegraph | Delta |
|---|---|---|---|
| Q1 (understanding) | $0.48 | $0.41 | without −$0.07 |
| Q2 (config lookup) | +$0.06 → $0.54 | +$0.10 → $0.51 | with −$0.04 |
| Q3 (impact analysis) | +$0.18 → $0.72 | +$0.09 → $0.60 | without −$0.09 |
| Q4 (code edit)* | +$0.51 → $1.23 | +$0.74 → $1.34 | with −$0.23 |
| Q5 (trace) | +$0.32 → $1.55 | +$0.16 → $1.50 | without −$0.16 |
| **Total** | **$1.55** | **$1.50** | **without −$0.05 (≈3%)** |

\*Q4 is confounded, not clean: the without-codegraph run independently decided to check the
Hibernate `ddl-auto: validate` constraint, add `@Transient` correctly, write and run 2 new
unit tests, and do a full `mvn compile` — real extra engineering the with-codegraph run
simply didn't do. Backing that out puts codegraph's Q4 roughly on par or ahead, and the
total flips to codegraph winning. Answer quality (correctness, depth, citing real line
numbers) was comparable across all 5 questions on both sides in both this round and round 1
— this was never a quality difference, only a cost one.

**Bottom line: the 34% cost increase from round 1 is gone — round 3 lands within ~3% either
way, statistical noise, not a real gap.** The fixes that closed it, in order of estimated
contribution: (1) removing the mandatory `index_status` round-trip [round 1], (2) the
`detail="full"`-by-default guide change removing a second round-trip on understanding
questions [round 2 guide fix, round 3 measurement], (3) the ~36% response-payload slimming
[round 2]. The round-3-specific resolver/ranking fixes (tsconfig aliases, search re-ranking)
don't directly move $ cost — they're correctness/precision fixes — but they matter for
*trusting* the numbers this report relies on: a wrong resolver edge or a test-file-polluted
search result would make `impact_analysis`/`get_context` answers wrong regardless of cost.

This does **not** mean cost parity is now permanent or repo-size-independent — round 1's own
literature review (the competitor's published 7-repo benchmark) says $ cost is genuinely
scale-dependent, roughly break-even on a repo LedgerGuard's size (47 files) and only a clear
win on much larger ones. What round 3 shows is that our *implementation* is no longer leaving
cost on the table for reasons that were fixable (redundant calls, bloated payloads) — the
remaining ~break-even result is closer to the honest floor for a repo this size, not a bug.

## Not fixed this pass — prioritized ideas for real improvement

Ranked by confidence × leverage, not implemented blind — each needs either more design
thought or its own empirical validation before landing. Written after round 2, before round 3;
some items below carry their own later shipped-date once acted on.

### 1. Multi-query `get_context` -- SHIPPED 2026-07-13
`get_context`'s `query` param now also accepts a list of up to 5 strings: each is run
through `hybrid_search` independently, merged round-robin (query1's top hit, query2's top
hit, ... before either gets a second slot) so no single query's results crowd out the
others, deduped by `entity_id`, then diversity-capped and token-budget-truncated exactly
like the single-query path. Fully backward compatible -- a plain string behaves
byte-identical to before. Motivated directly by a real transcript: the round-5
"transaction flow" question burned 7 separate `get_context` calls, one per pipeline stage,
when the agent knew all 7 names by the second call. This is the first change this session
that reduces round-trip *count* for a known multi-lookup pattern, rather than shrinking
response size or removing one fixed redundant call -- everything shipped through round 5
closed the round-1 regression back to parity; this is the first genuine attempt to push
below parity. Not yet empirically re-measured (see the open question this raises below).

### 2. Make the token-savings metric honest about what it measures (high confidence, low effort, high trust value)
`tokens_estimated`/`tokens_if_read`/`savings_ratio` should either be relabeled to make clear
they measure *retrieval size vs. a full-file-read baseline*, not *session cost*, or a
disclaimer should sit next to every reported number. Continuing to report "Nx less tokens"
in a way a reasonable person reads as "Nx cheaper" — when today's own controlled test showed
the opposite for real session cost — is a trust problem waiting to surface the moment
someone else runs this same experiment. The README's headline "101x average" claim
[README.md:62](../README.md) uses the identical estimation methodology and inherits the
same honesty gap; worth revisiting once the metric itself is fixed.

### 3. A genuinely calibrated cost model (medium confidence, high effort)
The right long-term fix isn't a smarter static formula, it's empirical: instrument real
session `/usage` deltas (opt-in, anonymized) around codegraph tool calls, and use *that* to
report real expected $ impact instead of an estimated-tokens proxy. This is what actually
closes the gap between "the tool claims savings" and "the tool has measured savings" — this
report is a first, manual instance of exactly that kind of measurement.

### 4. Teach the guide when *not* to reach for the tool (medium confidence, low effort)
Right now the guide unconditionally says "do not open a source file before calling
`get_context`." For a small, single-file, already-well-understood question, a direct read
may genuinely be cheaper than a round-trip through an MCP tool call — today's Q1 (understanding
question) actually came out *slightly cheaper with* codegraph ($0.48 vs $0.53), so this
isn't always true, but it likely depends on question shape (multi-file/cross-cutting
questions favor the tool; single-file/local questions may not). Worth a follow-up test
specifically isolating question *type* as the variable, not just question *count*.

### 5. Reduce per-turn overhead architecturally, not just per-call count (low confidence, needs research)
If Claude Code's caching genuinely re-reads the full accumulated context on every turn
(the working hypothesis behind the 2x cache-read gap), the ceiling on how much guide/tool
tweaks alone can fix is real — the fundamental fix would be reducing turns, and there may be
a hard floor on how few turns any agentic tool-use pattern can achieve versus a
single-shot direct read. This needs someone with visibility into Claude Code's actual
caching internals to confirm or refute, not more speculation from token counts alone.

## Round 2 (same day): re-measured after the guide fix — still not worth it, so went deeper

Re-ran a 4-question subset with the updated guide: with-codegraph hit **$1.09 by Q4 vs
$0.97 without**. Better than the first round's gap, still net-negative. The guide fix was
necessary but not sufficient — so this round attacked the actual cost structure instead of
the instructions around it.

**The economics, from first principles:** an agentic session's dollar cost is dominated by
cache reads — every turn re-reads the entire accumulated context, and (per Claude Code's
own UI tooltip) *"MCP tool results stay in context for the rest of the session."* So only
two levers actually matter:

1. **Round-trips.** Each eliminated tool call saves an entire context re-read (~150k+
   tokens of cache read per turn in these sessions ≈ $0.05 each).
2. **Permanent context growth per response.** Every byte a tool returns is re-paid on
   *every subsequent turn* — response size has a compounding cost, not a one-time one.

**Measured where the response bytes actually go** (real query, LedgerGuard index, 5-entity
summary response = 4,822 chars ≈ 1,205 tokens): per entity, `name`, `qualified_name`,
`language`, and `file` (~150 chars) are pure duplication — all derivable from `entity_id`,
whose format is literally `{lang}:{file}:{qname}`. Null fields (`"docstring": null`) and an
unused `via` retrieval-provenance tag added more. Worst of all: neighbor lists carried up
to 16 *full entity_ids* per entity at ~75+ chars each on a Java repo (the file path
repeated in every one), when the qualified name (~25 chars) is all an agent needs to
understand a neighborhood.

### Shipped: response payload slimming (`server/mcp_server.py`)

- Dropped `name`/`qualified_name`/`language`/`file`/`via` from `get_context` entities
  (derivable from `entity_id` or unused), plus all null/empty fields.
- Summary-mode neighbor lists now carry qualified names, not full ids (full ids still
  available via `detail="full"` or `impact_analysis`; the tool description explicitly
  tells the agent this so it doesn't waste a round-trip misusing a name as an id).
- Summary-mode docstrings truncate to their first line (the source preview already shows
  the opening lines; `detail="full"` keeps everything).
- Envelope: dropped the `query`/`detail` echo and the derivable `tokens_saved`.

**Measured result: the same real query's response went 4,822 → 3,100 chars (−36%)**, with
the informative content (preview, structure, neighbor names, savings fields) intact.
8 new/updated tests.

Same treatment applied to the other two hot-path tools: `get_entity_context` no longer
echoes back the four fields derivable from the id the caller just passed in, and
`impact_analysis` no longer repeats each caller's `name` and full `file` path alongside an
`entity_id` that already contains both — on a deep impact tree that duplication roughly
doubled every node.

### Shipped: edit-workflow rule in the guide

The Q4 transcripts showed the with-codegraph agent fetching full source over MCP and
*then* Reading the same file again because Claude Code's Edit tool requires a fresh Read —
paying for the source twice, plus an extra round-trip. The guide now says: for an edit,
locate with ONE `get_context`, then go straight to Read + Edit; never pull full source
over MCP first. This is the honest division of labor: **the graph's edge is locating and
relating code, not delivering bodies the file tools will re-deliver anyway.**

### Where this tool actually wins — the design direction that matters

The A/B also showed *where* the tool is genuinely better, and it's not raw retrieval on a
47-file repo (grep is nearly free there): Q1 understanding was already slightly cheaper
with codegraph, and `impact_analysis`/`trace_path` answered structural questions in one
call that grep needs several rounds to approximate. The value scales with codebase size
and with **cross-file/structural** questions — and, critically, with **cross-session
reuse**: Claude's context evaporates between sessions, but the index (and its stored
summaries) persist. A single A/B session is close to the tool's worst case; the
per-session re-exploration it can eliminate across dozens of sessions is its best.

**Shipped (2026-07-13): `project_brief`.** The biggest unbuilt lever flagged in this
section — a small pre-computed session-start summary (architecture/layers, hot paths by
call fan-in, HTTP entry points, language/size stats), replacing the multi-call
re-orientation every fresh session currently performs — is now a real tool
(`analysis/brief.py`, `server/mcp_server.py::_project_brief`), computed on demand from
existing indexed tables (no new storage), wired into the guide as "call once, first,
before anything else." Not yet re-measured in its own controlled A/B — the honest next
step is a round-4 test isolating this specific tool's effect on a fresh-session cold-start
question, the way round 3 isolated the resolver/ranking fixes.

## Round 1 (2026-07-10): initial A/B on LedgerGuard — CodeGraph cost 34% more

A real, controlled A/B test — same 5 questions (understanding, search, impact, a code edit,
and a follow-up), same codebase (LedgerGuard), same clean git baseline, one Claude Code
session with the CodeGraph MCP tool connected and one without — comparing actual `/usage`
cost, not estimated tokens.

**Bottom line, stated plainly: in this test, using CodeGraph cost 34% *more* than not using
it, for equivalent-quality output.** That's the opposite of the tool's stated value
proposition, and it deserves to be reported exactly this bluntly, not softened.

### The data

| | Without codegraph ($) | With codegraph ($) | Difference |
|---|---|---|---|
| Q1 (understanding) | $0.53 | $0.48 | −$0.05 |
| Q2 (understanding) | $0.68 | $0.71 | +$0.03 |
| Q3 (search) | $0.80 | $0.91 | +$0.11 |
| Q4 (code edit) | $0.97 | $1.28 | **+$0.31** |
| Q5 (impact question) | $1.05 | $1.41 | **+$0.36** |
| **Total** | **$1.05** | **$1.41** | **+$0.36 (+34%)** |

Both sessions produced a **correct, functionally-equivalent edit** at Q4 (verified by diff:
both added `WelfordStats.relativeDeviation(x)` and routed `AnomalyScorer.zScore()` through
it, same `Z_CAP` behavior preserved). So this isn't "cheaper but worse" on either side —
quality was a wash. The cost difference is real, not a proxy for a quality difference.

Cumulative cache-read tokens by Q5: without-codegraph 1.9M, with-codegraph 4.4M — more than
double. That's the number that actually explains the gap, not the individual retrieval
sizes CodeGraph reports about itself.

### Root cause: the tool's own "Nx less tokens" metric measures the wrong thing

`get_context` reports something like *"~1288 vs ~4519 tokens (3.5x less)"* — comparing what
it returned against a hypothetical "read the whole file" baseline. That comparison is
internally correct, but it silently ignores:

1. **MCP tool-schema overhead.** Measured directly: all 11 tool schemas together are ~1.6k
   tokens, present in context the moment the server connects, regardless of how many you
   actually use. Small on its own, but non-zero on every message once connected.
2. **Round-trip count, not round-trip size, is what drives cost here.** Every tool call is
   a separate turn; Claude Code's caching re-reads the *entire accumulated context* on each
   turn. A session that makes more, smaller tool calls pays a compounding cache-read cost
   that a session making fewer, larger direct file reads doesn't — even if each individual
   codegraph call is "more efficient" in isolation.
3. **The agent guide itself was mandating an unnecessary round-trip.** Confirmed in code:
   `get_context` already calls `_get_stale_count()` internally and returns a `warnings`
   field if the index is stale. But the guide's Rule 1 said *"Call `index_status` once"* as
   an unconditional first step on every task — a guaranteed extra round-trip providing
   information `get_context` was already going to give for free. This is the single most
   concrete, provable contributor found in this pass.

### Fixed this pass

[`installer/guide.py`](../packages/codegraph/installer/guide.py) — the managed `CLAUDE.md`
block:
- Rule 1 no longer mandates a separate `index_status` call. The agent now goes straight to
  `get_context` and only calls `reindex` if that call's own `warnings` field flags
  staleness.
- Rule 2 (new): if the agent already knows it needs full source for a small, specific set
  of entities (an edit task, not exploration), call `get_context(..., detail="full")`
  directly instead of a summary call followed by a second full-detail call — collapsing a
  common 2-round-trip pattern (confirmed happening in the transcripts: "let me search" then
  "let me get the full source") into one.
- Kept the block under its existing ~400-token budget (had to trim wording twice to fit —
  worth noting the budget constraint itself is in tension with wanting to explain *why* a
  rule exists; ended up shorter and more directive instead).

1 new regression test in `test_installer_guide.py`. This fix has **not yet been
re-measured empirically** — the honest next step is rerunning the same 5-question A/B with
the updated guide to see how much of the 34% gap it actually closes. Don't claim it's fixed
until that's done.

## What this report is NOT saying

- Not saying CodeGraph's core graph/search/analysis features are wrong — cycles, smells,
  dead-code, impact, trace, cross-language resolution all held up under real testing today
  and yesterday, independent of this cost finding.
- Not saying MCP tool use is inherently worse than direct file reads in general — this is
  one 5-question sample on one codebase, not a broad claim. The methodology here (report the
  real numbers, verify quality was actually equivalent, ground every hypothesis in
  code before asserting it) is exactly what should be repeated before trusting any
  "the tool saves money" claim going forward, including this one's own fix.
