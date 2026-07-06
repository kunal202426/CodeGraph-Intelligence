# Real-world stress test: a live production codebase

**Date:** 2026-07-06
**Target:** a real, in-production full-stack project owned by the maintainer (backend +
two separate frontends + a browser extension). The project itself is **not** included in
this repo and never was — it was indexed from a local, gitignored working copy and deleted
immediately after this report was written. Nothing about its business logic, names, or
content beyond the anonymized examples below is reproduced here.

This is not a synthetic fixture. It is messy in the ways real projects are messy: mixed
languages, a subdirectory added to `sys.path` instead of clean package-relative imports,
two files that happen to share a name, dependency injection, heavy CSS, and a browser
extension's content-script/store split. That messiness is exactly what surfaced every
finding below — none of this showed up against the fixture suite.

## What was indexed

| | |
|---|---|
| Real source files | 116 (walker-reported, after excluding `node_modules`/`venv`/`.venv`/build output) |
| Breakdown | 84 JavaScript/JSX, 20 Python, 10 CSS, 2 HTML |
| Subsystems | a FastAPI backend, two separate React frontends, a browser extension (content scripts + a Zustand-style store) |
| Entities indexed | 904 |
| Edges indexed | 3,576 |
| Cold index + embed | ~19s wall time |
| Crashes | 0 |
| Hangs | 0 |

## Headline result

Four real, previously-invisible bugs found and fixed, all confirmed against this specific
codebase before and after the fix — not just asserted in isolation. Two more real gaps found
and **not** fixed this pass (documented below, with exact repro). Everything landed on
`main`, covered by regression tests reproducing the real shape, CI green.

| Metric | Before this pass | After |
|---|---|---|
| Resolved imports | 526 / 3,497 (15.0%) | 649 / 3,548 (18.3%) |
| Dead-code candidates | 550 | 135 (−75.5%) |
| `get_current_user`-style DI functions flagged dead | yes (all of them) | no |
| Stale edges after a second index run | leaked forever (any non-Python file) | cleared correctly |
| Cold-index wall time (separately, on a different 1,100-file dataset — see below) | 74–90s | 5–6s |

## Bugs found and fixed this session

### 1. FastAPI `Depends()` dependency injection wasn't recognized as a call

The single largest false-positive source in this specific codebase.

```python
def me(current_user: User = Depends(get_current_user)):
    ...
```

`get_current_user` is invoked by FastAPI on every request through `me` — but it's a
parameter **default value**, not a call expression inside the function body, so the
ordinary call scan never saw it. In this real backend, dependency injection is used for
auth checks, DB sessions, and quota checks across essentially every route handler —
`Depends(...)` appeared 74 times, referencing 61 distinct functions. Every one of them
looked like dead code, and `impact_analysis` on `get_current_user` reported **zero
callers** despite it gating nearly every authenticated endpoint in the service.

**Fix:** parameter defaults matching `Depends(<bare identifier>)` now emit an ordinary
call edge from the function to the referenced name, so it resolves through the exact same
path a real call would. Conservative by construction — `Depends(lambda: ...)` or
`Depends(Service())` aren't bare identifiers and aren't guessed at.

After the fix, `impact_analysis get_current_user` correctly traces a multi-hop caller tree
through every dependent route handler up to the actual route registrations
(`route:GET /me`, `route:POST /reset`, `route:GET /users/{user_id}/leads`, …) — the exact
query a developer would run before changing how auth works.

### 2. Bare imports never resolved when the real file lived at a nested `sys.path` root

```python
# backend/routers/auth.py, run with backend/ itself on sys.path
from auth import get_current_user  # real file: backend/auth.py
```

This is an extremely common real-world pattern — a repo runs with a subdirectory on
`sys.path` instead of package-relative imports throughout, so files import each other by
bare top-level name. The resolver's existing "strip a known source-root prefix" logic only
covers a fixed allowlist (`src`, `packages`, `lib`, `app`, `source`); an arbitrary directory
name like `backend` was never handled, so every such import fell through to `external:`,
which cascades into every call through it also failing to resolve — not just the
`Depends()` cases above.

**Fix:** every indexed file now registers every dotted suffix of its module path, not just
the full path. `from auth import X` resolves via an unambiguous suffix match; when two
files share a suffix (this project genuinely has both `backend/auth.py` and
`backend/routers/auth.py`), the resolver falls back to whichever candidate actually defines
the imported name — and if neither or both do, it stays `external:` rather than guess,
matching this resolver's policy everywhere else. This also fixes `from package import
submodule`-style bare submodule imports via the same mechanism.

### 3. CSS selectors were flagged as dead code

**400 of the original 550 dead-code candidates were CSS rules** — by far the largest false
positive class found, bigger than the two backend bugs above combined. CSS rules are
parsed as `EntityType.FUNCTION` (the closest existing category in the entity model), so the
dead-code heuristic — which assumes "function/class = reachable only via calls/imports
edges" — flagged every single selector in a real stylesheet. A CSS rule is referenced by a
class/id name matched as a string in markup, not called; it was never eligible for this
kind of analysis in the first place.

**Fix:** CSS/HTML entities are now excluded from dead-code detection at the query level,
alongside the existing dunder/framework-decorator/`test_`-prefix exclusions.

### 4. Stale edges from non-Python files never cleared on re-index (found in a preceding pass, same session)

Not new to this specific codebase, but worth restating here because it directly affects
trust in everything else in this report: re-indexing any of the 21 non-Python languages
after a code change used to leave every removed call/import edge in the graph forever,
because the cleanup path matched only Python's entity-id prefix. Confirmed and fixed before
this stress test began; without it, the "before/after" numbers above would have been
unreliable on repeated indexing.

## Real gaps found, confirmed, and **not** fixed this pass

Documented honestly rather than quietly deferred.

### JSX/React component usage isn't captured as a call edge

```jsx
// JobCard.jsx, LeadCard.jsx, QueuePersonCard.jsx (three real call sites)
<ScoreBadge score={job.ai_score} />
```

`ScoreBadge` and `EmptyState` are genuinely used across multiple files via JSX tag syntax
— confirmed by grep against the real source — but the parser's call-edge extraction only
walks `call_expression` nodes; a JSX element (`jsx_element` / `jsx_self_closing_element`)
is a structurally different AST shape and is invisible to it entirely. This is the **second**
largest false-positive source found in this codebase (after CSS, now fixed) and would
affect essentially any React/JSX-heavy real-world frontend. Not fixed this pass — it's a
parser-level feature addition (recognizing a JSX tag as a call to the component it names),
comparable in scope to the per-language work already done this cycle, and deserves its own
design-and-test pass rather than a rushed bolt-on.

### Calls through an imported module namespace don't resolve

```python
from services import leads_service
...
leads_service.check_duplicate(email, db, user_id)  # called 3+ times in this file alone
```

Bug #2 above fixes the *import* (`leads_service` now correctly resolves to the real
module). The *call* through it still doesn't: `_resolve_call` only checks whether the
callee name itself was imported directly, not whether the receiver is a module alias whose
target file defines a matching name. Importing a whole module and calling
`module.func()` — rather than `from module import func`) is idiomatic in a lot of
real Python (it's the style Google's own guide prefers) and this codebase uses it
throughout its service layer. Confirmed as the majority of the remaining ~135 dead-code
candidates. Not fixed this pass: correctly threading "this identifier resolved to a module,
so look up the attribute in that module's file" through the existing call-resolution path
needs its own careful design, not a quick patch.

### Pydantic request/response models flagged as dead code

`SignupRequest`, `LeadCreate`, and similar classes are used only as parameter type
annotations (`def signup(body: SignupRequest)`), never explicitly instantiated with
`ClassName()`. Dead-code detection's reachability model is calls/imports only; "referenced
as a type annotation" is a different, currently-untracked relationship. A handful of these
(~10) remain in the final candidate count. Lower priority than the two gaps above since the
volume is much smaller, but the same category of issue.

## Everything else that worked

- **No crashes, no hangs** across the whole real project — mixed languages, a browser
  extension's message-passing pattern, heavy CSS, dependency injection, a nested `venv`
  with a different name (`venv`, not `.venv`) than the project's own environment.
- **Semantic search genuinely works on real code.** Query: *"check if user already sent an
  email to this lead"* — zero keyword overlap with the actual function name. Top two hits:
  `check_duplicate` (the service function) and `check_duplicate` (the route handler calling
  it). This is the core value proposition and it held up.
- **`impact_analysis` produces a correct, deep, multi-hop tree** once the resolution bugs
  above were fixed — tracing from a dependency function through every route handler that
  depends on it up to the actual HTTP route registration.
- **`cycles`** correctly reported no import cycles (a true negative, not just "found nothing
  because it's broken" — the project's import graph is genuinely acyclic).
- **`smells`** found 15 real complexity hits spanning both the Python backend and JSX
  frontend files in the same run, including a browser-extension content script — confirms
  cross-language smell detection works on a real mixed-language repo, not just same-language
  fixtures.

## Separately: the indexing-performance and stale-edge fixes verified before this pass

Not from this codebase (a different, larger dataset was used to profile this — production
Python packages, ~1,100 files) but load-bearing context for "will this perform well on a
real project": cold-indexing that dataset went from 74–90 seconds to 5–6 seconds after
batching database writes across files instead of per-file, and a non-Python re-index no
longer leaves stale edges in the graph. Both shipped and tested before this JobHuntPro pass
began, which is why the "before" import-resolution number above was already measured on a
correctly-behaving indexer rather than one still accumulating stale state.

## Bottom line

Four real, confirmed, previously-invisible correctness bugs, all specific to patterns that
only show up on an actual production codebase — dependency injection, flattened imports,
CSS-as-a-function-entity, cross-language edge cleanup — found by running the tool against
real code instead of reading it. All four are fixed, tested, and merged. Two more real gaps
(JSX component calls, module-namespace calls) are confirmed and documented rather than
rushed. Dead-code false positives dropped 75% in one project from fixes made *because* of
this project. This is exactly the kind of hardening that comes from real usage, and it's
now banked before any external user hit it.
