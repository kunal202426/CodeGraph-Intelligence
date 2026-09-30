# Project context: CodeGraph-Intelligence (Kortex)

Rebuilt 2026-09-27 after an SSD replacement wiped the prior Claude Code session data for
this repo. Re-derived entirely from the GitHub clone (`kunal202426/CodeGraph-Intelligence`,
branch `main`, commit `c2df714` at clone time) plus the tracked docs below, not from memory.

What this is: "Kortex", a local-first AI memory layer for codebases. It indexes a repo once
into a DuckDB graph (entities, call/import edges, embeddings), then serves that graph to AI
coding agents over MCP so they look things up instead of re-reading files. It also ships a
CLI, a FastAPI plus React/D3 web UI, and a GraphRAG `ask` command backed by the Anthropic
API. Public, solo-owned, license PolyForm-Noncommercial-1.0.0.

Kind: git repo (monorepo: a Python package plus a separate web frontend package).


Current state (per STATUS.md's `## Current` section, not independently re-verified this
session; see Gotchas in onboarding.md): status WRAPPED as of 2026-09-30 (owner decision, no
longer under active development, no further work planned; claims and logged results are
left as they were). Last phase was "Maintenance and hardening" (post-audit fixes,
usability, repo hygiene). Only `main` remains (three experiment fixes were merged into it
and the experiment branches deleted); the Grafana test clone and index were deleted;
local experiment logs are in `docs/experiments/`. STATUS.md
claims 1300 tests passing, 1 live-skip (needs `ANTHROPIC_API_KEY`), 0 failing, verified both
locally and on GitHub Actions as of the last commit. Working tree was clean at clone time,
`main` up to date with `origin/main`.

Entry points:
- CLI: `packages/codegraph/cli.py`, installed as the `codegraph` script (see `pyproject.toml`,
  `[project.scripts]`). Commands: `init`, `doctor`, `index`, `search`, `deps`, `impact`,
  `cycles`, `smells`, `deadcode`, `owner`, `layers`, `ask`, `summarize`, `context`, `trace`,
  `status`, `watch`, `serve`, `install`, `uninstall`.
- MCP server: `packages/codegraph/server/mcp_server.py`, what an agent (Claude Code, Cursor,
  etc.) connects to after `codegraph init` registers it.
- HTTP API: `packages/codegraph/server/api.py` (FastAPI), backing `codegraph serve`.
- Web UI: `packages/web` (React 19, Vite, D3, TypeScript), talks to the FastAPI server.

External systems:
- Anthropic API (`claude-sonnet-4-6`), only for `ask` / GraphRAG and one live-skipped test.
  Everything else (indexing, search, graph queries) runs fully offline.
- `sentence-transformers` (`all-MiniLM-L6-v2`), downloads about 80MB locally on first index.
- DuckDB, a single local file per indexed project (`.codegraph/graph.duckdb`), no server.
- GitHub Actions CI, smoke-checks `uv run codegraph --version` and runs the test suite.

Detected stack: Python 3.11 (`.python-version`), managed with uv, tree-sitter (22 languages), DuckDB,
FastAPI plus uvicorn, Typer plus Rich, the `mcp` Python SDK, `sentence-transformers`, the
`anthropic` SDK. Web side: React 19, Vite, D3, TypeScript, npm.

Test and analysis logs in docs/ (all tracked, read in full 2026-09-27; see onboarding.md for
the highlights that matter and rules.md for the testing discipline they establish):
- MANUAL_TEST_PROMPT.md: the protocol for a manual test session with the user (one item at
  a time, explicit verdict, wait for "next").
- MANUAL_TEST_REPORT.md, VERIFICATION.md, QUALITY_REPORT_2026-07-01.md: early dogfooding on
  this repo itself, mostly quality-of-life fixes, no correctness bugs in the graph itself.
- REAL_WORLD_STRESS_TEST_2026-07-06.md, _07.md, _07-ledgerguard.md: real bugs found and
  fixed against real external codebases (JobHuntPro, LedgerGuard), the source of most of
  the parser/resolver hardening in this repo's history.
- PATTERN_AUDIT_PLAN_2026-07-07.md: generalizes those bugs into recurring shapes and lists
  unfixed candidates elsewhere in the codebase, ordered by confidence.
- COMPETITOR_ANALYSIS_2026-07-11.md: a full-source read of a mature competing tool, what it
  does differently, and which ideas were ported in (tsconfig path aliases, search ranking).
- COST_EFFICIENCY_FINDINGS_2026-07-10.md: the running $ cost A/B log (newest first), 11+
  rounds, including honestly-reported rounds where the tool cost more, not less.

Tracked docs: AGENTS.md (repo conventions), README.md (user docs), STATUS.md (current
phase, test count, this repo's status log), docs/DECISIONS.md (full changelog and
reasoning). Working notes and experiment logs are in docs/ARCHITECTURE.md,
docs/ONBOARDING.md, docs/PROJECT_RULES.md and docs/experiments/.
