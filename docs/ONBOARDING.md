# Onboarding: CodeGraph-Intelligence

## What this project is

"Kortex", published on GitHub as CodeGraph-Intelligence. A local-first AI memory layer for
codebases: index a repo once into a DuckDB graph (entities, call/import edges, embeddings),
then serve it to AI coding agents over MCP so they look things up instead of re-reading
files, plus a CLI, a web UI, and an optional GraphRAG `ask` command. Solo project, public,
PolyForm-Noncommercial license. See README.md for the full pitch and docs/DECISIONS.md for
the changelog.

## How to run it

Install uv first: https://github.com/astral-sh/uv

```
uv sync --extra dev          # one time, ~2 minutes, also downloads pytest/ruff
uv run pytest -q             # confirm a green baseline (STATUS.md claims 1300 passing, not
                              # independently re-verified this session, see Gotchas below)
uv run codegraph init        # once per target project you want indexed
uv run codegraph doctor      # PASS/FAIL check on index, MCP config, guide, freshness
```

Then restart your agent (MCP tools only load on agent restart) and just ask it questions
normally. Full command list: `uv run codegraph --help`.

Web UI dev loop lives separately in `packages/web` (npm, not uv): `npm install && npm run dev`.

## Tour of the layout

See docs/ARCHITECTURE.md for the full breakdown. Short version: `packages/codegraph` is
the Python engine (walker, tree-sitter parsers, DuckDB graph store, resolver, embeddings,
GraphRAG, CLI, FastAPI server, MCP server); `packages/web` is the React/Vite/D3 frontend.
`uir.py` and `graph/schema.sql` are the two contracts everything else is built against.

## Decisions that matter

Full history lives in docs/DECISIONS.md and STATUS.md's `## Current` section (both tracked,
both the source of truth, don't duplicate here). The highlights worth knowing before you
touch anything:

- **MCP boot must stay fast and non-blocking.** Claude Code drops a slow MCP connection
  silently after a 30s timeout, with no error shown, so the agent just falls back to grep.
  This actually happened here (2026-08-12): a 25.6s cold boot from synchronously importing
  torch, plus a 225s synchronous staleness walk, plus three concurrent walks that could
  crash the interpreter outright (`PyEval_SaveThread` fatal error). All fixed: embeddings
  moved to a worker subprocess, staleness made single-flight and non-blocking. If MCP tools
  ever seem to silently not get used, suspect this class of bug before suspecting the model.
- **A "1 caller" answer is worse than no answer.** `impact_analysis` reported 1 caller for a
  function that actually had 3 (later found to actually have 11), because of a stacked
  qualified-call and Go-import-resolution bug. Three rounds of prompt/guide tuning tried to
  fix "the agent doesn't call `impact_analysis` enough" before anyone checked the tool's own
  answer against `grep` ground truth. Lesson kept in docs/PROJECT_RULES.md: verify the tool's
  output before theorizing about agent behavior.
- **The token-savings number is not the same as dollar cost.** A real controlled A/B first
  showed CodeGraph costing 34% more in actual `/usage` cost on a 47-file repo, despite a
  good-looking "Nx fewer tokens" estimate, because round-trip count (not per-call size)
  dominates Claude Code's cache-read cost. Closed to near-parity on that repo by removing a
  mandatory `index_status` call and slimming responses ~36%. On a much larger repo (Grafana,
  97k entities) it later won clearly (-58%) on the hardest cross-cutting question. The
  honest claim is scale- and question-shape-dependent, not a flat "Nx cheaper."
- **Default-import calls in JS/TS silently didn't resolve** (`export default function Foo`
  then `import Foo from './Foo'`), which is the single most common React pattern, so this
  quietly broke call resolution for most components in any React codebase tested. Fixed by
  guessing the default-export target when a file has exactly one exported entity.

## Gotchas


- **`.internal/` is gone and unrecoverable from git.** AGENTS.md references it as holding
  the original MVP build plan and detailed platform spec, gitignored by design. It doesn't
  exist on the maintainer's machine after an SSD replacement, and never touched GitHub. If a backup
  exists elsewhere, it needs to be restored manually.
- **DuckDB is single-writer.** Don't run `codegraph watch` and a heavy `reindex` (or the MCP
  server) against the same database file from two terminals at once; it used to crash a
  watcher thread outright, now retries with backoff, but the underlying limitation is real.
- **The embedding model reloads on every CLI invocation** (each `codegraph` call is a fresh
  process, so the in-memory singleton can't persist). A few seconds per call, worse (about
  27s) on a cold first `watch`/`serve` load. Partial fix (offline-mode load when cached)
  shipped; a full fix needs a persistent local model service, not yet built.
- **Framework-dispatched and non-call-expression invocations need explicit parser support,
  language by language.** JSX tags, Java `new Foo()` / field initializers, FastAPI
  `Depends()`, Spring `@Service`-style DI stereotypes were each invisible to call-graph
  analysis until specifically taught to the relevant parser or resolver. If a real component
  or class looks like dead code, check whether it's actually one of these known shapes before
  trusting the deadcode/impact report. See docs/PATTERN_AUDIT_PLAN_2026-07-07.md for the
  full list of confirmed-but-not-yet-fixed candidates in other languages (Rust macros,
  Kotlin/C#/Scala call edges, Ruby paren-less calls, and others).
- **A module entity and its sole top-level class/function can collide on the same
  `entity_id`** when a file sits at a repo root with no directory prefix (confirmed in both
  Java and TypeScript). Rare in practice (most build tooling requires nested package
  layouts) but not fixed; a genuinely flat single-class file at a repo root can silently
  lose one of its two entities.
- **The maintainer's own dev workflow has hit orphaned-process and interpreter-alias issues**
  before (a stray MCP server process holding file locks during a `uv tool install --force`;
  a `.venv` accidentally bound to the Microsoft Store Python alias). Documented as
  environment gotchas in docs/REAL_WORLD_STRESS_TEST_2026-07-07-ledgerguard.md, not product
  bugs, but worth knowing if `uv run codegraph` or `codegraph` (as a global tool) misbehaves
  in a way that looks like a broken install.

## How to contribute

Follow AGENTS.md (tracked, source of truth for this repo) plus docs/PROJECT_RULES.md (repo-specific conventions and testing discipline).
Short version: uv-managed, ruff + pytest green before every commit, conventional-commit
messages, no AI attribution anywhere in git for this repo specifically, push after every
commit, update STATUS.md when status/tests/next-steps change.

When testing manually with the user rather than just running the automated suite, follow
docs/MANUAL_TEST_PROMPT.md's protocol: one checklist item at a time, explicit PASS or ISSUE
FOUND verdict, wait for "next," never silently fix, hide, or retry a failure.
