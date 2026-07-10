# Real-world stress test: JobHuntPro, second pass

**Date:** 2026-07-07
**Target:** JobHuntPro (same production project as the [2026-07-06 pass](REAL_WORLD_STRESS_TEST_2026-07-06.md)) — backend + frontend + browser extension.
**Method:** Manual, one command at a time, following the project's standard manual-test
checklist. Fresh clone of CodeGraph-Intelligence, installed as a global `uv tool`, run
against a real working copy of JobHuntPro from inside VSCode.

This session is still in progress — this report is updated after each finding rather than
written up at the end, so nothing gets lost.

## Setup notes (environment, not a product bug)

The maintainer's pre-existing `CodeGraph/.venv` was bound to the Microsoft Store Python
alias, which broke `uv run codegraph` with a `ModuleNotFoundError: No module named
'python'` on interpreter probing. Worked around by installing via
`uv tool install --editable .` (its own isolated env, unaffected). The original `.venv`
was separately fixed by recreating it against `uv`'s own managed CPython 3.11.15. Noted
here for completeness; not a CodeGraph code defect.

## Bugs found and fixed this session

### 1. The walker doesn't stop at a nested git repository's boundary

**Repro:** `CodeGraph-Intelligence` was cloned *inside* `JobHuntPro` (a convenience of this
test setup, not a normal layout). `codegraph search`/`context` against JobHuntPro returned
results from `CodeGraph-Intelligence/tests/fixtures/...` — the tool's own test fixtures —
ahead of JobHuntPro's real code:

```
codegraph context "authentication"
# top result: CodeGraph-Intelligence/tests/fixtures/sample_repo_py/auth/login.py
# (a fixture whose module docstring literally says "Authentication module")
# none of JobHuntPro's real cold/backend/auth.py entities appeared in the top 5
```

Root cause: `walker.py` only excludes directories by name (`.git`, `node_modules`, etc.)
and honours the *root's* `.gitignore` — it never recognized a nested `.git` as a separate
repository's boundary, so a tool or vendored project checked out inside the repo being
indexed gets walked and indexed as if it were part of it. The fixture's coincidental
literal "Authentication" docstring match then won a Reciprocal-Rank-Fusion tie-break over
JobHuntPro's real, only-semantically-similar functions.

**Fix:** [`walker.py`](../packages/codegraph/walker.py) now prunes any subdirectory that
itself contains a `.git` entry (directory or file, so submodule/worktree checkouts count
too) — the same boundary rule `git` itself uses. 2 new tests in `test_walker.py`.

### 2. `codegraph index` never purges entities for files that leave the walk entirely

Found immediately after fixing #1: re-running `codegraph index .` after the walker fix
still returned the polluted fixture entities. `index` only calls `store.clear_files()` for
a file it's currently walking *and* finds changed — a file that stops being walked
altogether (deleted, newly gitignored, or now excluded by fix #1) was never compared
against what's already in the DB, so its entities/edges/embeddings stay forever. The MCP
`reindex` tool already had this cleanup (`find_deleted_files`), but the plain CLI `index`
command never did.

**Fix:** [`cli.py`](../packages/codegraph/cli.py) `index` now diffs the walked file set
against the DB's `files` table before parsing and purges anything no longer present via
the existing `store.clear_files()` plus an explicit `files` row delete, printing `Removed
N files no longer present from the index.`. Confirmed on JobHuntPro: 182 stray fixture
files purged, file count corrected 298 → 116, and `context "authentication"` now returns
JobHuntPro's real `login`, `get_current_user`, `google_auth`. New regression test in
`test_cli.py`.

### 3. `codegraph trace` silently fails on plain entity names

**Repro:** `codegraph impact get_current_user` correctly showed a real call chain
(`list_users → get_admin_user → get_current_user`), but tracing the same two names in the
same direction reported no path at all:

```
codegraph trace list_users get_current_user
# No call path from 'list_users' to 'get_current_user' within 7 hop(s).
```

Root cause: `deps`, `impact`, and `owner` all resolve their argument through
`find_entity_by_name_or_id` (accepting a plain name, a qualified name, or an entity_id, and
listing candidates on ambiguity). `trace` was the one exception — it passed its two
arguments straight to the BFS as if they were already exact `entity_id` strings. Neither
name matched anything in the `edges` table, so the BFS legitimately found "no path" — but
the message was misleading: it implied the entities existed and just weren't connected,
when actually neither had been resolved at all.

**Fix:** `trace` now resolves both arguments the same way `deps`/`impact`/`owner` do
(factored the duplicated resolve-or-exit-with-candidates logic, previously copy-pasted
three times, into one `_resolve_entity_or_exit` helper used by all four commands). An
unresolvable name now correctly reports `No entity matching '...'` instead of a misleading
`No call path`; a real but genuinely disconnected pair still correctly reports `No call
path`. 3 new/updated tests in `test_cli_context_trace_status.py`.

### 4. JSX component tags weren't captured as call edges (React/JSX false-positive dead code)

**Repro:** `codegraph deadcode` on JobHuntPro flagged 136 candidates; spot-checking 2 by
hand (`ScoreBadge`, `LoginPage`) showed both are real, actively-used React components —
referenced only via JSX tag syntax (`<ScoreBadge score={...} />`, `<LoginPage />`), never
called as a plain function. This is a previously-documented, unfixed gap from
[yesterday's pass](REAL_WORLD_STRESS_TEST_2026-07-06.md); today's run confirmed it's the
single largest false-positive source in this codebase — the large majority of the 136
candidates were `.jsx` components, not real dead code.

Root cause: [`typescript.py`](../packages/codegraph/parsers/typescript.py)'s call-edge
extraction (`_iter_call_nodes`/`_callee_name`) only walked `call_expression` nodes. A JSX
tag (`jsx_element` / `jsx_self_closing_element`) is a structurally different AST shape and
was invisible to it entirely, so any component used only via JSX had zero inbound `calls`
edges and looked exactly like dead code.

**Fix:** a JSX tag is now treated as a call to the component function it names.
`_iter_call_nodes` also yields `jsx_self_closing_element` nodes and the `jsx_opening_element`
half of a paired `jsx_element`; `_callee_name` extracts the tag's `name` field. Conservative
by construction, matching this codebase's existing `Depends()`-resolution fix from
yesterday: a **lowercase** tag (`<div>`, `<span>`) is a host element, never a call, and is
explicitly excluded — only a capitalized tag (`<ScoreBadge />`, React's own component
convention) or a namespaced tag (`<Foo.Bar />`, resolved to `Bar`) emits an edge. 5 new
tests in `test_calls.py`, including an end-to-end index-and-resolve regression test
reproducing the exact `ScoreBadge` shape.

### 5. `codegraph index` had no way to invalidate stale parse results after a tool upgrade

Found while trying to verify bug #4's fix live: `codegraph index .` reported "116
unchanged" and `deadcode` still showed all 136 stale candidates, because the hash-based
incremental skip (T2.3) only detects *source file* edits. Fixing a bug in codegraph's own
parser doesn't touch the target repo's files, so their hash never changes and the old
parse results (missing the new JSX edges) kept being served indefinitely — with no CLI
option to force a full re-parse short of deleting the database file.

**Fix:** `codegraph index` gained a `--force` flag that re-parses every file regardless of
hash match (still upserts in place, so row counts stay correct — no duplication). New
regression test in `test_cli.py` confirms `--force` re-parses everything and is a no-op on
row counts for genuinely unchanged source.

### 6. Default-imported components never resolved to the real entity — the JSX fix's biggest real-world blocker

Verifying bug #4 live with `--force` dropped the candidate count 136 → 107, but the exact
two components spot-checked earlier (`ScoreBadge`, `LoginPage`) were still flagged.
Isolated repro: even a **plain function call** (not JSX) through a default import never
resolved:

```js
// shared/ScoreBadge.jsx
export default function ScoreBadge({ score }) { ... }

// JobCard.jsx
import ScoreBadge from "./shared/ScoreBadge";
ScoreBadge({ score: 1 });   // -> external:ScoreBadge, not a real edge
```

Root cause: the resolver has always deliberately pointed a default import at the
**module** entity, not the actual default-exported function/class ("we don't track
default-export targets explicitly" — a documented simplification, not new). That's
survivable for `import` edges alone, but it silently broke every downstream *call*
through a default import too: `_resolve_call` matches a callee name against a per-file
map of imported names, and that map is keyed by the resolved target's own name — for a
default import the target was the module (name `"shared.ScoreBadge"`), which never
matches the callee name actually used in code (`"ScoreBadge"`). Named imports happened to
dodge this because an import's target name and the local binding are (by this parser's
own encoding) always the same string. This is the change with by far the biggest real
impact of the session: `export default function Foo() {}` + `import Foo from './Foo'` is
the single most common pattern in any React codebase, so this silently broke call/JSX
resolution for the majority of components in this project specifically.

**Fix:** [`resolver.py`](../packages/codegraph/graph/resolver.py) now guesses the real
default-export target: if a file has exactly one exported (non-module) entity, a default
import resolves straight to it (confidence 0.8, heuristic) instead of the module (0.7).
Conservative by construction — a file with more than one export can't be guessed
unambiguously and keeps the existing module-entity fallback exactly as before (verified
by a dedicated regression test), so this can only improve resolution, never point at the
wrong thing. 3 new tests (`test_resolver.py` x2, `test_calls.py` x1 reproducing the exact
cross-file JSX shape above end to end).

### 7. `codegraph layers` only ever looked at a file's top-level directory

**Repro:** `codegraph layers` reported "No recognizable layers" on JobHuntPro despite it
clearly having a layered structure (`cold/backend/routers/`, `cold/backend/services/`,
`linkedin/backend/routes/`, ...).

Root cause, two compounding gaps in [`patterns.py`](../packages/codegraph/analysis/patterns.py):
1. `classify_layer` was only ever checked against a file's **first** path segment
   (`cold/backend/routers/auth.py` → `"cold"`, which matches nothing). Any repo where
   layer directories sit under a project/workspace folder — the norm for a monorepo with
   several sub-apps, exactly this repo's shape — was invisible to this command entirely.
2. Separately, JobHuntPro's FastAPI convention is `routers/` (plural); only
   `routes`/`route`/`controllers`/etc. were in the presentation keyword set.

**Fix:** added `routers`/`router` to the presentation keywords, and replaced the
top-segment-only check with one that walks every directory segment from the root down and
classifies by the first one that matches a known layer keyword (same "don't assume the
layer dir is at the root" principle as the resolver's nested-import-root fix from
yesterday). 3 new/updated tests in `test_patterns.py`, including a monorepo-shaped
regression test (`app/backend/routers/` + `app/backend/services/`, both nested two levels
deep).

### 8. `codegraph watch`'s first save silently paid the embedding model's full load cost

**Repro:** started `codegraph watch .`, saved one file. The change was picked up and
re-indexed, but took **45.6 seconds** with zero explanation — a second, unrelated save
right after took 62ms. Looked exactly like the watcher had hung.

Root cause: the embedding model (`sentence-transformers` + `torch`) loads lazily, cached
as a process-wide singleton on first use. `codegraph watch` never touches it at startup,
so that one-time cost — tens of seconds on a cold cache, worse on Windows — silently lands
on whichever file the user happens to save first, with no indication of what's actually
happening. Every save after that reuses the already-loaded model (hence 62ms). This is a
UX/confusion bug, not a correctness one — nothing was lost or wrong, it just looked broken.

**Fix:** [`cli.py`](../packages/codegraph/cli.py) `watch` now eagerly warms the model right
after starting the file watcher and before printing the "Watching ..." banner, with an
explicit `Loading embedding model (one-time)...` message — so the delay is visible and
explained up front instead of landing unexplained on an arbitrary future save.
`--no-embed` skips it entirely, as before. 2 new tests in `test_watch_command.py`
(mocking `embed_one` so the tests stay fast/deterministic rather than depending on the
real model).

## Checklist progress (updated as we go)

| # | Item | Result |
|---|---|---|
| 1 | Cold index (`codegraph init`) | PASS — 3120 entities, 13029 edges (before fix #1/#2 pollution) |
| 2 | Incremental hash-skip | PASS — 298/298 unchanged, 0.4s |
| 3 | `codegraph status` | PASS |
| 4 | `codegraph doctor` | PASS — all green |
| 5 | Literal search | PASS |
| 6 | Semantic search | PASS |
| 7 | `codegraph context` | **ISSUE FOUND → FIXED** (bugs #1, #2 above) |
| 8 | `codegraph deps` | PASS |
| 9 | `codegraph impact` | PASS |
| 10 | `codegraph trace` | **ISSUE FOUND → FIXED** (bug #3 above) |
| 11 | `codegraph cycles` | PASS — no import cycles (true negative) |
| 12 | `codegraph smells` | PASS — 16 real hits across Python/JSX/JS in one run |
| 13 | `codegraph deadcode` (spot-check) | **ISSUE FOUND → FIXED** (bugs #4, #5, #6 above) |
| 14 | `codegraph owner` | PASS |
| 15 | `codegraph layers` | **ISSUE FOUND → FIXED** (bug #7 above) |
| 16 | `codegraph watch` | **ISSUE FOUND → FIXED** (bug #8 above) |

All fixes verified with the full test suite green (1135 passed, 1 skipped, 0 regressions,
latest run) and confirmed live against JobHuntPro after each fix. Final live confirmation
of bugs #4/#6: `codegraph deadcode` dropped from 136 → 78 candidates after `--force`
re-indexing. Both originally-flagged false positives (`ScoreBadge`, `LoginPage`) now
correctly resolve; spot-checked two remaining component candidates (`CrossAppNav`,
`RegisterPage`) and confirmed both are genuinely unused nowhere else in the codebase —
true positives, not new false positives, so the fix precisely improved recall without
overcorrecting. Continuing down the checklist.
