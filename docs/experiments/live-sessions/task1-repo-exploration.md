# Live session log: Task 1 (repository exploration)

Task text (fixed, same every round):

> Find and explain where session/authentication handling is implemented in this
> repo. I want to know the main files and the flow, not a full audit.

Target: Grafana clone, `C:\Users\HP\Desktop\Projects\grafana`, index built once
(107,382 entities, 744,087 edges, 17,460 files, with embeddings), harness reset
point `a87adee4`. Same index reused across every branch since none of the
experiments so far change the graph schema or index content.

## Round 1: baseline/cost-control

**Verdict:** pending your explicit pass/fail. Content looks like a strong pass on
inspection (accurate, well-cited, honest about what it didn't check), but per your
own protocol this is your call, not mine, recording it as reported until you confirm.

**Session numbers (from the `/usage` panel):**

```
cost:           $0.67
api_time:       46s
active_time:    2m10s
model:          Sonnet, 100%
cache_hit:      91%
input:          20
output:         ~1k
cache_read:     1.1M
cache_write:    100.3k
codegraph share of session limits: 5%
```

**Tool calls, from the transcript:**

```
codegraph calls (5): project_brief x1, get_context x2, list_files x2
native calls (~14): Read session.go, Read contexthandler.go, Read service.go,
                     Read auth_token.go (x2, second time scoped to specific line
                     ranges 214-243 and 342-506), Read priority_queue.go,
                     Read authn.go, Read user_token.go, Read auth.go,
                     Searched (grep-style, regex over auth/login/session
                     function names) x2, Searched pkg/services/contexthandler/*.go,
                     Searched Priority\(\) uint, Searched grafana_session_expiry
total tool calls: ~19
```

**Additive/substitutive read, per the taxonomy in
`systems-level-cost-latency-investigation.md` section 4:** native reads/searches
outnumber codegraph calls roughly 3:1 in this session. Not necessarily a problem,
per that document's own framing (round 9's cheapest run still followed a batched
`get_context` with 2-3 *targeted* greps, which is complementary, not additive).
Whether this round's native calls are targeted follow-ups (complementary) or
exploratory duplication of what `get_context`/`project_brief` already surfaced
(additive) needs the actual transcript, not just the tool-call list, to judge
properly. Flagged for a closer look if this becomes a live comparison point
against `exp/get-context-batching`, not resolved here.

**Answer quality, at a glance:** correct architecture (cookie-based session token,
`user_auth_token` table, priority-ordered auth clients, rotation via
`TokenNeedsRotationError`), specific file:line citations, and an explicit
"what I did not check" section (OAuth/SAML callback path, `Logout` internals,
`authnserver`/`apps/iam`). This kind of honesty about scope is exactly what this
project's own testing discipline (`docs/MANUAL_TEST_PROMPT.md`) asks for, real
signal, not an artifact of the task being easy.

## Round 2: exp/get-context-batching

**Verdict:** pending your explicit pass/fail, same as round 1. Content looks at
least as strong as round 1: same core architecture correctly identified, plus one
detail round 1 missed (the `ExtJWT` client at priority 15, round 1's priority list
only had 6 clients, round 2 found 7), and the same honest "what I didn't check"
closing section. No visible quality regression.

**Session numbers (from the `/usage` panel):**

```
cost:           $0.54   (round 1: $0.67, -19.4%)
api_time:       44s     (round 1: 46s, about even)
active_time:    1m      (round 1: 2m10s, -54%)
model:          Sonnet, 100%
cache_hit:      92%     (round 1: 91%)
input:          18      (round 1: 20)
output:         76      (round 1: ~1k, see caveat below)
cache_read:     855k    (round 1: 1.1M, -22%)
cache_write:    78.5k   (round 1: 100.3k, -21.7%)
codegraph share of session limits: 3% (round 1: 5%)
```

**Tool calls, from the transcript:**

```
codegraph calls (3): project_brief x1, get_context x2
native calls (~18): Read session.go, service.go, contexthandler.go,
                     registration.go, login.go, auth.go, user_token.go, authn.go,
                     auth_token.go (9 reads); Searched authnimpl/*.go,
                     contexthandler/*.go, two function-name regexes,
                     Priority() regex, a failed search, a return-statement regex,
                     a rotateToken/WriteSessionCookie regex, a LookupTokenErr
                     regex (9 searches, one of which failed outright)
total tool calls: ~21
```

**Honest read, not a victory lap:** this round used *fewer* codegraph calls (3 vs
5) and *more* native calls (18 vs 14) than round 1, yet came out cheaper and
faster. The `get_context` batching fix changes DB query count and wall-clock
latency inside CodeGraph's own process, it does not touch response content, token
count, or ranking (verified directly in the experiment's own report, byte-
identical output in full mode, set-equivalent in summary mode). So this session's
lower cost is not something the fix's own mechanism predicts or explains on its
own. The likely honest explanation is ordinary task-to-task variance (a different
exploration path, one fewer `get_context` call this run) rather than proof the
batching fix reduces frontier cost, this project's own cost log has flagged this
exact trap before (round 4: "a single question is exactly the kind of sample
that can't be trusted alone"). Real, reported numbers, directionally consistent
with no regression, not treated as a confirmed causal win from n=1 per side.

**Failed call:** one native search failed outright this round (regex syntax
issue in the query itself, visible in the transcript). Not a CodeGraph issue,
logged for completeness per the redundant/failed-call tracking this suite asks
for.

## Round 3: no CodeGraph (control)

```
cost: $0.44   api: 35s   active: 42s   cache_hit: 88%
input: 12   output: 98   cache_read: 523.6k   cache_write: 71.5k
codegraph calls: 0   native calls: 20 (10 Read, 10 Grep/Glob)
```

Cheapest and fastest of the first three, with an answer at least as complete
(only run to name the frontend rotation timer and where to change settings).

## Round 4: exp/native-vs-codegraph-routing (guide: EACH-symbol rule for every task)

```
cost: $0.59   api: 50s   active: 1m32s   cache_hit: 92%
input: 20   output: 1k   cache_read: 994.2k   cache_write: 84.8k
codegraph calls: 10   native calls: 5 (1 Read, 4 Grep)   1 call failed
```

**Behavior did change, cost did not fall below the control.** Native calls dropped
from 14-20 (rounds 1-3) to 5, and codegraph calls rose to 10, so the guide fix
produced real substitution. But cost ($0.59) landed between round 1 ($0.67) and
round 2 ($0.54), and above the no-codegraph run ($0.44).

### Ground truth from the session transcript (exact, not inferred from the UI)

Source: `~/.claude/projects/.../477401bc-....jsonl` (only the latest session's
transcript survives on disk, so rounds 1-3 cannot be re-analyzed this way).
Totals match the `/usage` panel exactly (cache_read 994,248, cache_write 84,759).

The session took **10 model turns**. Cache read per turn grew from 85k to 123k
tokens, so the fixed prompt prefix alone is about 85k tokens before any tool
output (system prompt, skills list, deferred tool names, MCP instructions, hooks).
Every turn therefore costs roughly 100k cache-read tokens no matter what the tool
returns. All tool results combined were only about 15k tokens.

| Turn | Calls | Verdict |
|---|---|---|
| 1 | ToolSearch to load the deferred codegraph tools | loading overhead |
| 2 | project_brief + get_context (3 queries) | useful |
| 3 | list_files + get_context (4 queries) | useful |
| 4 | get_context with 6 queries | **FAILED**: schema said maxItems 5, "is too long" |
| 5 | get_context, same queries minus one, detail=full | retry of turn 4 |
| 6 | search_code + get_entity_context | **REDUNDANT**: turn 5 already returned both in full |
| 7 | list_files x2 | marginal orientation, largest result payloads |
| 8 | Read + 2 Grep | verification |
| 9 | 2 Grep | verification |
| 10 | final answer | needed |

So cost tracks **sequential model turns**, not tool calls or bytes: 16 calls in 10
turns. Turns 4 (failed), 6 (redundant) and 7 (marginal) were avoidable: roughly
330k of the 994k cache-read tokens. The no-codegraph run needed about 5 turns
(523k / ~100k) because it fanned out 4-5 independent Read/Grep calls per turn.

Redundancy per the plan's taxonomy: 1 FAILED, 2 REDUNDANT (search_code and
get_entity_context for symbols the previous full get_context already returned),
1-3 MARGINAL (list_files), remainder NECESSARY or COMPLEMENTARY.

### What this changes

The guide made the agent use CodeGraph instead of grep, which is what the branch
set out to do. It did not make the agent use it in fewer turns: CodeGraph lookups
are inherently sequential (each result decides the next query) while grep/Read
can be fanned out in parallel within a turn. That, not tool latency or response
size, is the gap to close.

Also confirmed: turn 1 was a `ToolSearch` because this environment defers MCP
tool schemas. That one turn is paid by every codegraph session here and is not
something CodeGraph's own code controls.

Quality: comparable and broader (also covered gRPC authnserver, JWT and ID-token
packages) but shallower on token-store internals; it states it only confirmed
signatures for CreateToken/LookupToken/handleLogin.
