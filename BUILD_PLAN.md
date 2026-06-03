# CodeGraph v2 — MVP Build Plan (Session-Resilient, Detailed Edition)

## 0. Context & Why This Plan Exists

The source spec [CodeGraph_Intelligence_Platform_Detailed.md](Desktop/CodeGraph/CodeGraph_Intelligence_Platform_Detailed.md) describes a multi-year enterprise platform: 10+ language parsers, Neo4j cluster, GraphRAG, IDE plugins, monetization tiers, RBAC, air-gap deployment, runtime-trace augmentation, distributed Celery workers, etc. A solo dev cannot ship that in 6 months — and shouldn't try.

**This plan is the MVP carve-out.** A portfolio-grade local-first project that captures the *coolest* parts of the vision (UIR + semantic graph + GraphRAG + MCP-integrated AI chat) and is reachable in ~30 atomic agent sessions.

**Hard constraint from user:** plan must survive token-exhaustion mid-session without losing progress. The cleverness of this plan is concentrated in §4 (Session Resilience Protocol). Read it twice.

### What we are building (MVP scope)

A local CLI + minimal web UI + MCP server that:
1. Indexes a Python or TypeScript repository into a unified graph stored in DuckDB
2. Answers natural-language questions about the codebase via GraphRAG over local embeddings + the Anthropic API
3. Shows an interactive D3 graph (modules + calls) in a browser
4. Exposes itself as an MCP server so **the host agent itself can call CodeGraph as a tool** — the killer demo

### What we are NOT building (deferred, possibly forever)

Rust/Java/Solidity/C++/PHP/Kotlin parsers, JetBrains plugin, 3D Three.js viz, time-travel viz, runtime trace ingestion, RBAC, JWT, billing, multi-tenancy, Helm/Kubernetes, distributed workers, Kafka, Celery, Redis, Neo4j cluster, Qdrant, MLflow, OpenTelemetry, secret detection, PII flow mapping, GDPR reports, SOC 2, auto-test-gen, auto-refactor, auto-ADRs.

If any of these become genuinely necessary during MVP build, that is a sign the scope has crept — push back and defer.

---

## 1. Locked Tech Stack (no mid-session debates)

These are decisions; do not relitigate per session.

| Layer | Choice | Version | Why this, not the alternative |
|---|---|---|---|
| Language (everything) | **Python** | 3.11+ | Single language for parsers, API, AI. MD's polyglot stack (Rust parsers + Go watcher + TS frontend) is 3× the maintenance for a solo dev. |
| Package manager | **uv** | latest | 10× faster than pip; replaces poetry. Native lockfile. |
| Parser | **tree-sitter-languages** | latest pypi | Bundles 100+ grammars including Python + TypeScript + JS + Go. One install, many languages. |
| Deep TS types | **deferred** | — | Skipped for MVP. Tree-sitter gets us 90% of what we need; full `tsc` integration is a v2 stretch. |
| Graph store | **DuckDB** | 1.x | Single file, SQL, JSON columns, vector ops, FTS. Zero infra. Neo4j is overkill for <1M entities. |
| Vector storage | **DuckDB native** | — | DuckDB has `array_cosine_similarity`. No separate Qdrant container. |
| Embeddings | **sentence-transformers** | latest | `all-MiniLM-L6-v2` (384d, 80MB, fast on CPU) for MVP. Switchable to `microsoft/codebert-base` later. |
| LLM | **Anthropic SDK** | `anthropic` pypi, latest | Model: `claude-sonnet-4-6`. Prompt-cache enabled. User runs the host agent → key is available. |
| CLI | **typer** | latest | Type-hinted, prettier than click. Rich output via `rich`. |
| Web API | **FastAPI + uvicorn** | latest | Standard. Async-friendly for streaming LLM responses. |
| Frontend | **React 18 + Vite + TypeScript + D3 v7** | latest | Matches MD's choice; minimal surface. |
| MCP server | **`mcp` Python SDK** | latest pypi | Official Anthropic MCP SDK. Lets the host agent call CodeGraph as a tool. |
| Test | **pytest + pytest-asyncio** | latest | Standard. |
| Lint/format | **ruff** | latest | One tool replaces black + isort + flake8 + pylint. |
| Git library | **pygit2** | latest | For Phase 9 git blame (deferred). Phase 1–8 uses `subprocess.run(["git", ...])`. |

### Python package dependencies (final, write into `pyproject.toml` in T0.1)

```toml
[project]
name = "codegraph"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "tree-sitter==0.21.*",
  "tree-sitter-languages>=1.10",
  "duckdb>=1.0",
  "typer[all]>=0.12",
  "rich>=13",
  "pathspec>=0.12",          # .gitignore parsing
  "pydantic>=2.6",
  "fastapi>=0.110",
  "uvicorn[standard]>=0.27",
  "sentence-transformers>=2.7",  # pulls torch
  "anthropic>=0.39",
  "mcp>=1.0",
  "numpy>=1.26",
  "pandas>=3.0.3",
  "watchdog>=3.0",   # Phase 11: filesystem watcher for incremental index freshness
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.4", "httpx>=0.27"]

[project.scripts]
codegraph = "codegraph.cli:app"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

## 2. Repository Structure (locked)

Create exactly this layout in T0.3. The shape is part of the plan — do not improvise.

```
codegraph/
├── AGENTS.md                       # Boot doc, auto-loaded by the host agent
├── STATUS.md                       # Progress tracker, last-thing-touched each session
├── README.md                       # User-facing docs (final form at T8.1)
├── pyproject.toml                  # uv config + deps (T0.1)
├── uv.lock                         # locked dep versions
├── .gitignore                      # standard Python + node + data files
├── .ruff.toml                      # if needed beyond pyproject inline
├── .github/workflows/ci.yml        # lint + test on push
│
├── plan/                           # Per-phase task lists (this plan ported in T0.6)
│   ├── 00-setup.md                 # Archived once Phase 0 done
│   ├── 01-slice.md
│   ├── 02-multifile.md
│   ├── 03-embeddings.md
│   ├── 04-callgraph.md
│   ├── 05-graphrag.md
│   ├── 06-webui.md
│   ├── 07-mcp.md
│   ├── 08-polish.md
│   └── 09-stretch.md               # Optional: git intel, antipatterns, viz overlays
│
├── packages/codegraph/             # Main Python package
│   ├── __init__.py
│   ├── cli.py                      # typer entry point with all commands
│   ├── config.py                   # XDG paths, env var loading
│   ├── uir.py                      # UIREntity + Edge dataclasses (single source of truth)
│   ├── walker.py                   # File discovery + .gitignore + language detection
│   │
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── base.py                 # IParser Protocol + ParseResult dataclass
│   │   ├── python.py               # Python parser (Phase 1)
│   │   ├── typescript.py           # TS/JS parser (Phase 2)
│   │   └── queries/                # Tree-sitter query files (.scm)
│   │       ├── python.scm
│   │       └── typescript.scm
│   │
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── store.py                # DuckDB connection + writes + reads
│   │   ├── schema.sql              # DDL: tables, indexes, FTS
│   │   ├── queries.py              # Canned graph queries (impact, cycles, deps)
│   │   └── resolver.py             # Symbol resolution pass (Phase 2)
│   │
│   ├── embeddings/
│   │   ├── __init__.py
│   │   ├── pipeline.py             # Embed entities, store, search (Phase 3)
│   │   └── chunking.py             # Build embeddable input from UIREntity
│   │
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── llm.py                  # Anthropic SDK wrapper (Phase 5)
│   │   ├── graphrag.py             # Retrieval + prompt assembly + generation
│   │   └── prompts/                # Prompt templates as .md files
│   │       ├── ask_system.md
│   │       └── summarize_system.md
│   │
│   ├── server/
│   │   ├── __init__.py
│   │   ├── api.py                  # FastAPI app (Phase 6)
│   │   ├── mcp_server.py           # MCP server (Phase 7)
│   │   └── static/                 # Vite build output goes here
│   │
│   └── analysis/
│       ├── __init__.py
│       ├── cycles.py               # Tarjan SCC (Phase 4)
│       └── smells.py               # God-class, fan-out, fan-in heuristics
│
├── packages/web/                   # Vite + React frontend (Phase 6)
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/                    # fetch wrappers
│       ├── components/
│       │   ├── Graph.tsx           # D3 force-directed
│       │   ├── SearchBar.tsx
│       │   ├── EntityPanel.tsx
│       │   └── ChatPanel.tsx
│       └── hooks/
│
├── tests/
│   ├── fixtures/
│   │   ├── sample_repo_py/         # tiny Python repo
│   │   │   ├── auth/login.py
│   │   │   ├── auth/session.py
│   │   │   ├── api/users.py
│   │   │   └── main.py
│   │   └── sample_repo_ts/         # tiny TS repo
│   │       ├── src/auth/login.ts
│   │       └── src/index.ts
│   ├── test_uir.py
│   ├── test_walker.py
│   ├── test_python_parser.py
│   ├── test_typescript_parser.py
│   ├── test_graph_store.py
│   ├── test_resolver.py
│   ├── test_embeddings.py
│   ├── test_cycles.py
│   ├── test_smells.py
│   ├── test_graphrag.py
│   ├── test_api.py
│   ├── test_mcp.py
│   └── test_e2e_index.py           # The full pipeline smoke test
│
└── .codegraph/                     # Runtime data (gitignored)
    ├── graph.duckdb                # The actual graph database
    ├── embeddings/                 # Cached model files
    └── runs/                       # Index run logs
```

---

## 3. UIR Schema (locked, this is the contract)

The MD spec has 30+ UIR fields. We start with a trimmed MVP set and add fields as needed. Single source of truth lives in `packages/codegraph/uir.py`. Every parser emits this; the graph stores this; the AI reads this.

### UIREntity (Pydantic model, written in T1.1)

```python
from __future__ import annotations
from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field

class EntityType(str, Enum):
    MODULE = "module"
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    INTERFACE = "interface"     # TS only
    TYPE_ALIAS = "type_alias"
    VARIABLE = "variable"

class Language(str, Enum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"

class UIREntity(BaseModel):
    # Identity
    entity_id: str                  # "py:src/auth/login.py:authenticate" — globally unique
    type: EntityType
    name: str                       # "authenticate"
    qualified_name: str             # "auth.login.authenticate"
    language: Language

    # Location
    file: str                       # relative to repo root, forward slashes
    start_line: int                 # 1-indexed
    end_line: int
    start_col: int = 0
    end_col: int = 0

    # Source
    raw_source: str                 # full source text
    docstring: Optional[str] = None
    signature: Optional[str] = None # function/method only

    # Metadata
    is_exported: bool = True        # Python: not underscore-prefixed; TS: has `export`
    is_async: bool = False
    parent_id: Optional[str] = None # for methods: parent class entity_id

    # Hash for incremental indexing
    hash: str                       # SHA-256 of raw_source

    # AI metadata (populated in later phases)
    summary: Optional[str] = None              # phase 5: AI-generated 1-line summary
    embedding_id: Optional[int] = None         # phase 3: rowid in embeddings table

class Edge(BaseModel):
    src_id: str                     # source entity_id
    dst_id: str                     # destination entity_id (may be unresolved string in phase 1)
    type: Literal["imports", "calls", "inherits", "implements", "contains"]
    line: int                       # line number where edge originates
    confidence: float = 1.0         # 1.0 = compiler-confirmed, lower = inferred
    is_dynamic: bool = False        # e.g. dynamic dispatch
```

### Entity ID Convention (DO NOT CHANGE after T1.1)

Format: `<lang>:<file>:<qualified_name>`

Examples:
- `py:src/auth/login.py:authenticate`
- `py:src/auth/login.py:LoginForm`
- `py:src/auth/login.py:LoginForm.validate`   (method on class)
- `ts:src/auth/login.ts:default`              (default export)
- `ts:src/auth/login.ts:authenticate`

Why this format: stable across runs, debuggable by humans, unique even when names collide across files.

---

## 4. Session Resilience Protocol (THE CLEVER PART — read twice)

Token-exhaustion mid-session is the #1 risk. Five-layer defense:

### Layer 1: `AGENTS.md` at repo root — auto-loaded by every new agent session

the host agent automatically reads `AGENTS.md` from the cwd. This is our entry point for any session. Keep under 2KB so it loads instantly. Exact content to write in **T0.2** (verbatim):

```markdown
# CodeGraph — Local AI Memory Layer for Codebases

This is a local-first MVP. CLI + minimal web UI + MCP server. See README.md for user docs.

## Stack (LOCKED — do not relitigate per session)
Python 3.11, uv, tree-sitter-languages, DuckDB, sentence-transformers (`all-MiniLM-L6-v2`),
Anthropic SDK (`claude-sonnet-4-6`), FastAPI, typer+rich, React+Vite+D3, mcp SDK.

## Resume protocol (do this first in every session)
1. Read STATUS.md — find current phase and next atomic task.
2. Read plan/<phase>-*.md — find the task definition (files, verify cmd, commit msg).
3. Run `uv run pytest -x` to confirm green baseline.
4. Execute exactly that one task.
5. Update STATUS.md (mark task done, set next task, note blockers).
6. Commit: message MUST start with task ID (e.g. "T2.4: ...").

## Conventions
- **One atomic task = one commit.** `git log --oneline` is the progress log.
- **Never break the CLI on main.** `uv run codegraph --help` must always work.
- **Verify before commit:** `uv run ruff check && uv run pytest`.
- **No new dependencies without updating §1 of plan/00-setup.md.**
- **No new top-level packages.** Stick to the §2 layout.

## Where to look
- UIR schema (the contract): packages/codegraph/uir.py
- DuckDB schema (DDL): packages/codegraph/graph/schema.sql
- Active phase tasks: plan/<NN>-*.md
- Progress: STATUS.md
- Original full spec (89KB, DO NOT re-read unless replanning): ../CodeGraph_Intelligence_Platform_Detailed.md

## If something seems wrong
- Compare against UIR schema and DuckDB schema first. They are source of truth.
- Check git log for prior task implementation patterns.
- DO NOT add features outside current task scope. File a note in STATUS.md "Future:" section.
```

### Layer 2: `STATUS.md` — single source of truth for progress

Updated as the **last action before commit** in every session. Exact template (write skeleton in T0.2, update every session thereafter):

```markdown
# CodeGraph — Status

## Current
- **Phase:** 1 — Thin Vertical Slice
- **Next task:** T1.3 — Python parser emits UIREntity
- **Last session:** 2026-05-21
- **Last commit:** abc1234 ("T1.2: add IParser protocol")

## Phase progress

### Phase 0 — Setup [DONE]
- [x] T0.1 — Init repo + pyproject.toml (commit aaa1111)
- [x] T0.2 — AGENTS.md, STATUS.md, README stub (commit aaa2222)
- [x] T0.3 — Scaffold package layout (commit aaa3333)
- [x] T0.4 — CLI stubs (commit aaa4444)
- [x] T0.5 — Pytest skeleton + CI (commit aaa5555)
- [x] T0.6 — Plan files copied (commit aaa6666)

### Phase 1 — Thin Vertical Slice [IN PROGRESS 2/9]
- [x] T1.1 — UIR dataclasses (commit bbb1111)
- [x] T1.2 — IParser protocol (commit bbb2222)
- [ ] T1.3 — Python parser entity extraction      ← NEXT
- [ ] T1.4 — DuckDB schema
- [ ] T1.5 — Graph writer (bulk + idempotent)
- [ ] T1.6 — Walker with .gitignore
- [ ] T1.7 — Wire CLI `index` end-to-end
- [ ] T1.8 — Wire CLI `search` (literal)
- [ ] T1.9 — E2E smoke test

### Phase 2 — Multi-file + TS [PENDING]
### Phase 3 — Embeddings [PENDING]
### Phase 4 — Call Graph [PENDING]
### Phase 5 — GraphRAG [PENDING]
### Phase 6 — Web UI [PENDING]
### Phase 7 — MCP [PENDING]
### Phase 8 — Polish [PENDING]

## Blockers / Notes
- (none)

## Future (defer until MVP shipped)
- (nothing yet)

## Metrics (filled at end of each phase)
- Phase 1 fixture index time: TBD
- Phase 2 real repo (fastapi/) index time: TBD
- Phase 3 embedding throughput: TBD
- Phase 5 ask latency (p50): TBD
```

### Layer 3: `plan/NN-*.md` — phase-by-phase atomic task definitions

Each phase has one file. Each task is **atomic = completable in 30–90 min = exactly 1 commit**.

Standard task block format (use this exactly, every task):

```markdown
### T1.3 — Python parser emits UIREntity
**Estimated:** 60 min
**Depends on:** T1.1, T1.2
**Files:**
- packages/codegraph/parsers/python.py (new, ~120 LOC)
- packages/codegraph/parsers/queries/python.scm (new, ~30 LOC)
- tests/test_python_parser.py (new, ~80 LOC)
- tests/fixtures/sample_repo_py/auth/login.py (new, 30 LOC sample)

**Inputs:** Python source file path + content string
**Outputs:** `list[UIREntity]` — at least one entity per top-level function/class/method

**Implementation sketch:**
1. Load tree-sitter Python grammar via `tree_sitter_languages.get_language("python")`
2. Parse source → root node
3. Use S-expr query (`.scm`) to capture `function_definition`, `class_definition`, decorated variants
4. For each capture: extract name, signature (line slice), docstring (first child string in body), span, hash
5. Build entity_id `py:<relpath>:<qualified_name>`
6. Return list

**Verify:**
- `uv run pytest tests/test_python_parser.py -v` (3+ assertions pass)
- Required test cases: top-level function, class with method, async function, decorated function
- `uv run ruff check packages/codegraph/parsers/python.py`

**Commit:** `T1.3: emit UIREntity from Python parser`

**Failure-mode recovery:**
- If tree-sitter import fails: ensure `tree-sitter-languages` installed (`uv add tree-sitter-languages`)
- If captures empty: print `tree.root_node.sexp()[:500]` to inspect grammar node names
- If qualified_name wrong for methods: ensure parent class name prepended
```

### Layer 4: Commit-per-task discipline

Every atomic task = exactly one commit. Commit message format:
```
<TASK_ID>: <imperative summary>

[optional body — what changed and why, in 1-3 sentences]
```

Examples:
- `T1.3: emit UIREntity from Python parser`
- `T3.2: add DuckDB cosine similarity vector search`
- `T5.4: stream Anthropic responses in ask CLI`

**Why:** `git log --oneline | head -30` IS the progress log. If a session crashes mid-task, `git status` reveals what's WIP and `git diff` shows the half-finished work. Either finish it next session or `git stash` and restart cleanly.

### Layer 5: "Resume in 30 seconds" guarantee

Any new session resumes by reading <10KB total:

```bash
cat AGENTS.md         # ~2KB
cat STATUS.md          # ~3KB
cat plan/01-slice.md   # ~5KB (current phase only)
```

That fits comfortably in context with massive room to spare. **Never re-read** the 89KB source spec mid-build. If a question about scope arises, the answer is in this plan, not the source spec.

---

## 5. Verification Strategy (run these any time)

### Per-task gate (must pass before commit)

```bash
uv run ruff check                                  # zero lint errors
uv run ruff format --check                          # zero format diffs
uv run pytest -x                                    # all tests green, stop on first fail
uv run codegraph --help                             # CLI still works (must always pass)
```

### Per-phase smoke test (run at end of phase)

```bash
# Phase 1 onwards
uv run codegraph index tests/fixtures/sample_repo_py/
uv run codegraph search "login"

# Phase 2 onwards (add TS)
uv run codegraph index tests/fixtures/sample_repo_ts/

# Phase 3 onwards (semantic)
uv run codegraph search "authentication flow" --semantic

# Phase 4 onwards (graph queries)
uv run codegraph impact authenticate
uv run codegraph cycles
uv run codegraph smells

# Phase 5 onwards (AI)
uv run codegraph ask "how does login work?"

# Phase 6 onwards (web)
uv run codegraph serve   # opens browser

# Phase 7 onwards (MCP — test inside the host agent)
claude mcp add codegraph python -m codegraph.server.mcp_server
```

### MVP shipped checklist (T8.2)

- [ ] All 8 phases marked DONE in STATUS.md
- [ ] All 6 CLI commands above work on `tests/fixtures/sample_repo_py/`
- [ ] All 6 CLI commands above work on a real OSS repo (default: `tiangolo/fastapi`)
- [ ] MCP integration demonstrably works inside the host agent (60-sec demo GIF recorded)
- [ ] README has install + 3 example queries + architecture diagram
- [ ] Benchmarks recorded in README (index time, search latency, ask latency)
- [ ] `git log --oneline | wc -l` shows ~30 commits, one per task

---

# PHASE-BY-PHASE DETAIL

## Phase 0 — Project Setup (1 session, ~3h)

**Goal:** `uv run codegraph --help` prints; `pytest` runs zero tests successfully; CI lint passes.

### T0.1 — Init repo + pyproject.toml
**Files:** `pyproject.toml`, `.gitignore`, `.python-version`
**Steps:**
1. `git init && git branch -M main`
2. Write `pyproject.toml` (copy from §1 of this plan verbatim)
3. Write `.gitignore`: standard Python + node + `.codegraph/` + `*.duckdb`
4. `uv sync` to create venv and lockfile
**Verify:** `uv run python -c "import codegraph"` works (after T0.3)
**Commit:** `T0.1: init Python project with uv`

### T0.2 — AGENTS.md, STATUS.md, README stub
**Files:** `AGENTS.md`, `STATUS.md`, `README.md`
**Steps:** Copy AGENTS.md content from §4 Layer 1 verbatim. Copy STATUS.md skeleton from §4 Layer 2. README.md is a 5-line placeholder until T8.1.
**Verify:** files exist, are readable
**Commit:** `T0.2: add AGENTS.md, STATUS.md, README stub`

### T0.3 — Scaffold package layout
**Files:** All `__init__.py` files per §2 layout; empty stubs for the .py files
**Steps:** Create directory tree per §2. Every Python module gets `__init__.py`. Every .py file referenced in §2 gets a 1-line docstring stub.
**Verify:** `python -c "import codegraph; import codegraph.parsers; import codegraph.graph; import codegraph.embeddings; import codegraph.ai; import codegraph.server; import codegraph.analysis"`
**Commit:** `T0.3: scaffold package layout`

### T0.4 — CLI entry point with command stubs
**Files:** `packages/codegraph/cli.py`
**Steps:** typer app with stubs for: `index`, `search`, `ask`, `impact`, `cycles`, `smells`, `summarize`, `serve`. Each prints "not implemented yet". Wire `[project.scripts]` in pyproject.toml.
**Verify:** `uv run codegraph --help` shows all 8 commands
**Commit:** `T0.4: add CLI stubs for all 8 commands`

### T0.5 — Pytest + CI
**Files:** `tests/test_smoke.py` (one test: `assert True`), `.github/workflows/ci.yml`
**Steps:** CI runs `uv sync && uv run ruff check && uv run pytest -v` on push to main + PRs.
**Verify:** `uv run pytest` passes (1 test); push to GitHub, CI green
**Commit:** `T0.5: add pytest skeleton and GitHub Actions CI`

### T0.6 — Plan files into repo
**Files:** `plan/00-setup.md` ... `plan/09-stretch.md`
**Steps:** Split this plan into 10 per-phase files (one per phase). Each file contains the §6–§13 phase detail for that phase only. Discard `plan/00-setup.md` from active reading once Phase 0 done (keep in repo for history).
**Verify:** `ls plan/*.md` shows 10 files; each is <8KB
**Commit:** `T0.6: add per-phase plan files`

**Phase 0 exit:** `uv run codegraph --help` works. CI green. STATUS.md shows Phase 1 next.

---

## Phase 1 — Thin Vertical Slice (5–6 sessions, ~12h)

**Goal:** End-to-end Python-only pipeline. Index a directory, get rows in DuckDB, search literally by name.

**Phase exit smoke test:**
```bash
uv run codegraph index tests/fixtures/sample_repo_py/
# → "Indexed 8 entities across 4 files in 0.6s"
uv run codegraph search "authenticate"
# → table showing the authenticate function with file:line and docstring
```

### T1.1 — UIR dataclasses
**Files:** `packages/codegraph/uir.py` (~80 LOC), `tests/test_uir.py` (~30 LOC)
**Steps:** Copy `UIREntity` and `Edge` from §3 verbatim. Add `make_entity_id(language, file, qualified_name) -> str` helper.
**Verify:** `uv run pytest tests/test_uir.py` — tests check serialization round-trip, ID format, hash determinism
**Commit:** `T1.1: define UIREntity and Edge schemas`

### T1.2 — IParser protocol
**Files:** `packages/codegraph/parsers/base.py` (~30 LOC)
**Steps:**
```python
from typing import Protocol
from pathlib import Path
from codegraph.uir import UIREntity, Edge

class ParseResult(BaseModel):
    entities: list[UIREntity]
    edges: list[Edge]
    errors: list[str] = []

class IParser(Protocol):
    language: str
    def parse(self, path: Path, source: str) -> ParseResult: ...
```
**Verify:** type-check passes, no runtime tests yet
**Commit:** `T1.2: add IParser protocol and ParseResult`

### T1.3 — Python parser emits entities
**Files:** `packages/codegraph/parsers/python.py` (~150 LOC), `parsers/queries/python.scm`, `tests/test_python_parser.py` (~100 LOC), `tests/fixtures/sample_repo_py/auth/login.py`
**Steps:**
1. Tree-sitter query file `python.scm`:
```scheme
(function_definition name: (identifier) @function.name) @function.def
(class_definition name: (identifier) @class.name) @class.def
(decorated_definition (decorator) @decorator (function_definition name: (identifier) @function.name)) @function.def
```
2. `PythonParser.parse(path, source)`:
   - `lang = tree_sitter_languages.get_language("python")`
   - `parser = Parser(); parser.set_language(lang); tree = parser.parse(source.encode())`
   - Run query, iterate matches
   - For each function/class node: extract name, span, signature (first line of body slice), docstring (first string literal in body if present)
   - Build qualified_name by walking parent class_definition chain
   - Emit UIREntity with `hash = hashlib.sha256(raw_source.encode()).hexdigest()`
3. Test cases:
   - Top-level function `def authenticate(email, password):` — assert entity emitted
   - Class with method — assert both entities, method has `parent_id`
   - Async function — assert `is_async=True`
   - Decorated function — assert decorator captured in raw_source
   - Docstring extraction — Google-style triple-quoted block
**Verify:** `uv run pytest tests/test_python_parser.py -v` — all 5+ tests pass
**Commit:** `T1.3: emit UIREntity from Python parser via tree-sitter`

### T1.4 — DuckDB schema
**Files:** `packages/codegraph/graph/schema.sql` (~50 LOC), `packages/codegraph/graph/store.py` (~60 LOC), `tests/test_graph_store.py` (~50 LOC)
**Steps:** Write SQL:
```sql
CREATE TABLE IF NOT EXISTS files (
  path VARCHAR PRIMARY KEY,
  language VARCHAR NOT NULL,
  hash VARCHAR NOT NULL,
  loc INTEGER,
  indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entities (
  entity_id VARCHAR PRIMARY KEY,
  type VARCHAR NOT NULL,
  name VARCHAR NOT NULL,
  qualified_name VARCHAR NOT NULL,
  language VARCHAR NOT NULL,
  file VARCHAR NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  raw_source TEXT,
  docstring TEXT,
  signature TEXT,
  is_exported BOOLEAN DEFAULT TRUE,
  is_async BOOLEAN DEFAULT FALSE,
  parent_id VARCHAR,
  hash VARCHAR NOT NULL,
  summary TEXT,
  embedding FLOAT[384],          -- populated in phase 3
  embedding_hash VARCHAR,         -- to detect drift in phase 3
  FOREIGN KEY (file) REFERENCES files(path)
);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_file ON entities(file);
CREATE INDEX IF NOT EXISTS idx_entities_qname ON entities(qualified_name);
CREATE INDEX IF NOT EXISTS idx_entities_parent ON entities(parent_id);

CREATE TABLE IF NOT EXISTS edges (
  src_id VARCHAR NOT NULL,
  dst_id VARCHAR NOT NULL,
  type VARCHAR NOT NULL,
  line INTEGER NOT NULL,
  confidence FLOAT DEFAULT 1.0,
  is_dynamic BOOLEAN DEFAULT FALSE,
  PRIMARY KEY (src_id, dst_id, type, line)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
```
Wire `GraphStore` class in `store.py` with `__init__(db_path)`, `init_schema()`, `upsert_file(...)`, `upsert_entities(list[UIREntity])`, `upsert_edges(list[Edge])`, `close()`.

**Verify:** `pytest tests/test_graph_store.py` — schema initializes, insert + select round-trip
**Commit:** `T1.4: define DuckDB schema and GraphStore`

### T1.5 — Bulk + idempotent writer
**Files:** `packages/codegraph/graph/store.py` (extend), `tests/test_graph_store.py` (extend)
**Steps:** Use DuckDB `INSERT OR REPLACE` for entities (key on entity_id). For edges use `INSERT OR IGNORE` (composite PK handles dup). Batch with `executemany`.
**Verify:** Test inserts 100 entities twice, asserts count = 100 not 200; asserts updated raw_source overwrites.
**Commit:** `T1.5: bulk idempotent graph writes`

### T1.6 — Walker with .gitignore
**Files:** `packages/codegraph/walker.py` (~80 LOC), `tests/test_walker.py` (~50 LOC)
**Steps:**
1. `walk(root: Path) -> Iterator[tuple[Path, Language]]`
2. Use `pathspec.PathSpec.from_lines("gitwildmatch", gitignore_text)` if `.gitignore` exists
3. Always-exclude: `.git/`, `.codegraph/`, `node_modules/`, `__pycache__/`, `.venv/`, `venv/`, `.mypy_cache/`, `.pytest_cache/`, `dist/`, `build/`
4. Language detection by extension: `.py → PYTHON`, `.ts/.tsx → TYPESCRIPT`, `.js/.jsx → JAVASCRIPT`
5. Skip binary files (`if b'\0' in chunk: skip`)
**Verify:** Test walks fixture, gets exactly expected file count; .gitignored files excluded
**Commit:** `T1.6: file walker with gitignore support`

### T1.7 — Wire CLI `index`
**Files:** `packages/codegraph/cli.py` (extend index command), no new test (e2e in T1.9)
**Steps:**
```python
@app.command()
def index(repo: Path = typer.Argument(...), db: Path = typer.Option(".codegraph/graph.duckdb")):
    store = GraphStore(db); store.init_schema()
    parsers = {Language.PYTHON: PythonParser()}
    with Progress() as progress:
        files = list(walk(repo))
        task = progress.add_task("Indexing", total=len(files))
        for path, lang in files:
            if lang not in parsers: continue
            source = path.read_text(errors="replace")
            result = parsers[lang].parse(path.relative_to(repo), source)
            store.upsert_file(path.relative_to(repo), lang, sha256(source))
            store.upsert_entities(result.entities)
            store.upsert_edges(result.edges)
            progress.advance(task)
    rich.print(f"[green]Indexed {n_entities} entities across {n_files} files in {elapsed:.1f}s")
```
**Verify:** `uv run codegraph index tests/fixtures/sample_repo_py/` writes a `.duckdb` file with non-zero entity count.
**Commit:** `T1.7: wire end-to-end index command`

### T1.8 — Wire CLI `search` (literal)
**Files:** `packages/codegraph/cli.py` (extend), `packages/codegraph/graph/queries.py` (new)
**Steps:**
```python
def search_literal(store, query, limit=20):
    return store.conn.execute("""
      SELECT entity_id, name, file, start_line, docstring
      FROM entities
      WHERE name ILIKE '%' || ? || '%'
         OR docstring ILIKE '%' || ? || '%'
      LIMIT ?
    """, [query, query, limit]).fetchall()
```
Pretty-print results in a Rich Table.
**Verify:** `uv run codegraph search authenticate` returns ≥1 row from fixture index.
**Commit:** `T1.8: implement literal search command`

### T1.9 — E2E smoke test
**Files:** `tests/test_e2e_index.py` (~60 LOC), expand `tests/fixtures/sample_repo_py/`
**Steps:** Fixture has ≥4 files, ≥8 entities (mix of functions, classes, methods). Test:
1. Index fixture into temp DuckDB
2. Assert entity count ≥ 8
3. Assert specific entity (e.g. `py:auth/login.py:authenticate`) exists with correct file + line range
4. Literal search returns it
**Verify:** `uv run pytest tests/test_e2e_index.py -v`
**Commit:** `T1.9: add E2E indexing smoke test`

**Phase 1 exit:** All 6 verification commands above pass. Update STATUS.md: Phase 1 DONE, Phase 2 NEXT.

---

## Phase 2 — Multi-file + Symbol Resolution + TypeScript (5 sessions, ~12h)

**Goal:** Index real Python and TypeScript repos. Cross-file imports resolve. `codegraph deps <entity>` traces dependencies.

### T2.1 — Python import statement extraction
**Files:** `parsers/python.py` (extend), `parsers/queries/python.scm` (extend), test additions
**Steps:** Tree-sitter query captures `import_statement` and `import_from_statement`. Emit one `Edge` per imported name with type=`"imports"`, dst_id provisionally = `py:??:<imported_name>` (unresolved; resolver fixes in T2.2). Also emit a placeholder Module entity for the file.
**Verify:** Parse a file with `from auth.login import authenticate`; assert edge `(file_entity)-[imports]->(py:??:authenticate)`.
**Commit:** `T2.1: extract Python import statements`

### T2.2 — Symbol resolver
**Files:** `packages/codegraph/graph/resolver.py` (~120 LOC), `tests/test_resolver.py`
**Steps:**
1. After all files parsed and inserted, run a resolution pass.
2. Build name → entity_id index from `qualified_name` and `(file, name)`.
3. For each edge with unresolved `dst_id` (matching `?:??:*`), attempt resolution:
   - From Python `from x.y import z`: candidate qnames are `x.y.z`, `x/y.py::z`
   - From relative imports (`from . import foo`): resolve relative to source file's package
4. Update edge `dst_id` if resolved; lower `confidence` for heuristic matches
5. Edges that stay unresolved get a final dst_id like `external:numpy.array` and confidence 0.5
**Verify:** Test creates 3 files in fixture importing each other; resolver pass closes all `?` placeholders.
**Commit:** `T2.2: cross-file symbol resolution pass`

### T2.3 — Incremental hash-based skip
**Files:** `cli.py` (extend index), `tests/test_e2e_index.py` (extend)
**Steps:** Before parsing a file, compute its SHA-256. Look up `files.hash` in DB. If unchanged, skip parse + write. Print summary at end: "Re-parsed 3 of 312 files".
**Verify:** Test indexes fixture twice, asserts second run is significantly faster and writes zero new rows.
**Commit:** `T2.3: incremental indexing via file hashing`

### T2.4 — TypeScript parser
**Files:** `parsers/typescript.py` (~150 LOC), `parsers/queries/typescript.scm`, `tests/test_typescript_parser.py`, `tests/fixtures/sample_repo_ts/`
**Steps:** Use `tree_sitter_languages.get_language("typescript")`. Capture `function_declaration`, `class_declaration`, `interface_declaration`, `arrow_function` (when assigned to const), `method_definition`. JSX-aware (`.tsx`) — tree-sitter handles it.
**Verify:** Parse fixture TS file with exported function, default export, class with method, interface. All 4 entities emit correctly.
**Commit:** `T2.4: TypeScript/JSX parser via tree-sitter`

### T2.5 — TypeScript import resolution
**Files:** `parsers/typescript.py` (extend), `resolver.py` (extend)
**Steps:** Capture `import_statement`. Relative paths resolve against source file directory; add `.ts/.tsx/.js/.jsx/index.ts` extensions. Reading `tsconfig.json` `paths` is **deferred**; leave a TODO.
**Verify:** TS fixture has `import { authenticate } from './auth/login'`; resolver creates correct edge.
**Commit:** `T2.5: TypeScript import resolution`

### T2.6 — CLI `deps`
**Files:** `cli.py` (extend), `graph/queries.py` (extend)
**Steps:** BFS from a starting entity following `imports`/`calls` edges, depth-limited (default 3). Render with `rich.tree.Tree`.
**Verify:** `uv run codegraph deps authenticate --depth 2` prints tree of dependencies.
**Commit:** `T2.6: add deps command (transitive dependency tree)`

### T2.7 — Real-repo smoke
**Files:** None new. Update STATUS.md with metrics.
**Steps:** `git clone tiangolo/fastapi /tmp/fastapi-smoke; uv run codegraph index /tmp/fastapi-smoke`. Record: # files, # entities, # edges, elapsed time. Expected target: <30s for fastapi at MVP quality.
**Verify:** Manual: search a known fastapi function (`get_swagger_ui_html`) and find it.
**Commit:** `T2.7: smoke test on real repo (fastapi)`

**Phase 2 exit:** TS + Python indexed in same DB; cross-file imports resolve; incremental skip works.

---

## Phase 3 — Local Embeddings + Semantic Search (4 sessions, ~10h)

**Goal:** `codegraph search "payment retry"` returns `retryBilling()` even though words don't match literally.

### T3.1 — sentence-transformers wrapper
**Files:** `packages/codegraph/embeddings/pipeline.py` (~80 LOC), `tests/test_embeddings.py`
**Steps:** Lazy-load `SentenceTransformer("all-MiniLM-L6-v2")` (downloaded to `~/.cache/torch/sentence_transformers/` on first use). Wrapper exposes `embed_batch(texts: list[str]) -> np.ndarray (N, 384)`. Cache the model singleton.
**Verify:** Unit test: embed 2 strings, assert shape `(2, 384)`, dtype `float32`.
**Commit:** `T3.1: sentence-transformers embedding wrapper`

### T3.2 — Embedding storage in DuckDB
**Files:** `embeddings/pipeline.py` (extend), no schema change needed (column added in T1.4)
**Steps:** `store_embeddings(entity_ids, vectors)` upserts into `entities.embedding`. Also store `embedding_hash` = SHA-256 of input text used (for drift detection in T3.5).
**Verify:** Insert + cosine-search round-trip (DuckDB `array_cosine_similarity`).
**Commit:** `T3.2: store entity embeddings in DuckDB FLOAT[384] column`

### T3.3 — Chunking strategy + batch embed during index
**Files:** `embeddings/chunking.py` (~50 LOC), `cli.py index` (extend)
**Steps:**
```python
def build_embed_input(e: UIREntity) -> str:
    parts = [f"{e.type.value} {e.qualified_name}"]
    if e.signature: parts.append(e.signature)
    if e.docstring: parts.append(e.docstring)
    body = e.raw_source[:1500]
    parts.append(body)
    return "\n".join(parts)
```
After T1.7's main parse+write loop, batch-collect new/changed entities and embed in chunks of 32. Show separate progress bar.
**Verify:** Re-index fixture; check `SELECT count(*) FROM entities WHERE embedding IS NOT NULL` == entity count.
**Commit:** `T3.3: embed entities during index pass`

### T3.4 — Hybrid search (literal + vector + RRF)
**Files:** `cli.py search` (extend), `graph/queries.py` (extend)
**Steps:**
1. Add `--semantic` and `--hybrid` flags. Default = hybrid.
2. Literal: existing ILIKE on name + docstring (top 20).
3. Vector: embed query, DuckDB `array_cosine_similarity` ordered DESC (top 20).
4. Fuse via Reciprocal Rank Fusion: `score = sum(1/(60+rank_i))` across both lists.
5. Return top K, annotated with which retrievers found each result.
**Verify:** Query "user authentication" returns `authenticate` even if function only has docstring "validates credentials".
**Commit:** `T3.4: hybrid search with literal + vector RRF`

### T3.5 — Incremental re-embed
**Files:** `cli.py index` (extend)
**Steps:** Before embedding an entity, compute `embed_input_hash`. Skip if equal to existing `embedding_hash`. Re-embed otherwise. Print count of re-embeddings at end.
**Verify:** Index twice in a row → second run says "0 re-embeddings". Edit one file → only that file's entities re-embed.
**Commit:** `T3.5: incremental embedding via input-hash check`

**Phase 3 exit:** Semantic search returns relevant results not found by literal search.

---

## Phase 4 — Call Graph + Impact + Smells (4 sessions, ~10h)

**Goal:** `codegraph impact <function>` shows blast radius. Detect cycles + god classes.

### T4.1 — Python call extraction
**Files:** `parsers/python.py` (extend)
**Steps:** Inside each `function_definition`, walk the body subtree, find `call` nodes. For each call: extract callee identifier chain. Best-effort local resolution: look up in same-file qnames; if not found, mark as unresolved string with confidence 0.7.
Tree-sitter query addition:
```scheme
(call function: [(identifier) @callee.name (attribute attribute: (identifier) @callee.attr)]) @call
```
**Verify:** Test function `def login(): authenticate(...)` produces edge `login → authenticate` of type `calls` with correct line.
**Commit:** `T4.1: extract Python call edges`

### T4.2 — TypeScript call extraction
**Files:** `parsers/typescript.py` (extend)
**Steps:** Same idea: capture `call_expression`. Method calls captured as `obj.method` qualified name attempts.
**Verify:** TS test for `function login() { authenticate(); }` produces correct edge.
**Commit:** `T4.2: extract TypeScript call edges`

### T4.3 — CLI `impact`
**Files:** `cli.py` (extend), `graph/queries.py` (extend)
**Steps:** Reverse-BFS from target on `calls` edges. Show direct callers (depth 1), then transitive (up to depth 5). Group by file. Add `--depth N` flag.
**Verify:** `uv run codegraph impact authenticate` lists callers across multiple files in tree form.
**Commit:** `T4.3: impact analysis (reverse call BFS)`

### T4.4 — Cycle detection
**Files:** `packages/codegraph/analysis/cycles.py` (~60 LOC), `cli.py cycles` (new), `tests/test_cycles.py`
**Steps:** Load all `imports` edges. Build adjacency list of files. Run Tarjan SCC. Report SCCs of size ≥ 2.
**Verify:** Fixture with intentional cycle: `a.py → b.py → c.py → a.py`. Assert cycle detected.
**Commit:** `T4.4: detect import cycles via Tarjan SCC`

### T4.5 — God-class heuristic
**Files:** `analysis/smells.py` (~80 LOC), `cli.py smells` (new), `tests/test_smells.py`
**Steps:** Heuristics (configurable thresholds):
- Class with >15 children (methods) → flag "god-class"
- Class spanning >500 LOC → flag "large-class"
- Module with fan-out >20 imports → flag "high-coupling"
- Function with cyclomatic complexity >15 → flag "complex-function" (count if/while/for/elif/and/or in body)
Output sorted by severity.
**Verify:** Test fixture with intentional god class; `codegraph smells` finds it.
**Commit:** `T4.5: detect god classes and coupling smells`

**Phase 4 exit:** Five graph-analysis commands available: search, deps, impact, cycles, smells.

---

## Phase 5 — GraphRAG + Anthropic LLM (5 sessions, ~14h)

**Goal:** `codegraph ask "How does authentication work?"` returns a coherent grounded answer.

### T5.1 — LLM wrapper
**Files:** `packages/codegraph/ai/llm.py` (~120 LOC), `tests/test_llm.py`
**Steps:**
```python
import anthropic, os
from typing import Iterator

class LLM:
    def __init__(self, model="claude-sonnet-4-6"):
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = model

    def stream(self, system: str, user: str, max_tokens=2000) -> Iterator[str]:
        with self.client.messages.stream(
            model=self.model, max_tokens=max_tokens,
            system=[{"type":"text","text":system,"cache_control":{"type":"ephemeral"}}],
            messages=[{"role":"user","content":user}],
        ) as stream:
            for text in stream.text_stream: yield text
```
- Prompt caching on system message (saves cost across repeated queries on same repo).
- Retries with exponential backoff via SDK's built-in retry config.
- Surface API errors clearly.
**Verify:** Mock test that wrapper composes request correctly. (Live API call only in T5.4.)
**Commit:** `T5.1: Anthropic SDK wrapper with prompt caching`

### T5.2 — Hybrid retrieval for AI
**Files:** `ai/graphrag.py` (~150 LOC), `tests/test_graphrag.py`
**Steps:**
1. `retrieve(query: str, k=15) -> list[UIREntity]`
2. Vector search top-30 by query embedding
3. Expand: for each candidate, fetch graph neighbors (1-hop) via `calls`/`imports`
4. Deduplicate by entity_id
5. Re-rank by combined score: `0.6 * cosine_sim + 0.3 * graph_degree_log + 0.1 * is_recently_modified`
6. Truncate to top K
**Verify:** Snapshot test: given a fixture query, assert specific entity IDs appear in top K.
**Commit:** `T5.2: hybrid graph + vector retrieval`

### T5.3 — Prompt template
**Files:** `ai/prompts/ask_system.md`, `ai/graphrag.py` (extend)
**Steps:** System prompt (~600 tokens):
```
You are a code architecture analyst. You answer questions about a codebase using ONLY the
provided context. Cite specific entities by their entity_id when relevant, using the format
[py:src/auth/login.py:authenticate]. If the context does not contain enough information to
answer confidently, say so explicitly — do not invent details. Prefer concrete file:line
references over vague descriptions. Be concise; prefer 2-3 paragraphs over walls of text.
```
User message assembly (~3000 tokens budget for context):
```
QUESTION: {query}

REPOSITORY CONTEXT:
{for each top-k entity}
--- [{entity_id}] {type} ({file}:{start_line}-{end_line})
{signature or first 20 LOC of raw_source}
{docstring if present}
Calls: {neighbor entity_ids, comma-sep}
{end for}
```
**Verify:** Snapshot test asserting prompt structure for a fixture query.
**Commit:** `T5.3: prompt template and context assembly for ask`

### T5.4 — CLI `ask` with streaming
**Files:** `cli.py ask` (extend)
**Steps:**
```python
@app.command()
def ask(query: str, db: Path = ".codegraph/graph.duckdb"):
    store = GraphStore(db)
    graphrag = GraphRAG(store, LLM())
    for token in graphrag.ask_stream(query):
        rich.print(token, end="", flush=True)
    rich.print()  # final newline
```
**Verify:** Manual: `uv run codegraph ask "What does the auth module do?"` returns coherent answer citing fixture entities.
**Commit:** `T5.4: end-to-end ask command with streaming`

### T5.5 — Repo summary
**Files:** `cli.py summarize` (extend), `ai/prompts/summarize_system.md`
**Steps:** Run multi-pass:
1. Per top-level directory, retrieve representative entities (sample, not all)
2. LLM call per directory → subsystem summary
3. Final LLM call combining subsystem summaries → top-level architecture summary
4. Write to `.codegraph/SUMMARY.md`
**Verify:** `uv run codegraph summarize` writes a coherent markdown file with subsystem descriptions.
**Commit:** `T5.5: generate repository architecture summary`

**Phase 5 exit:** `ask` and `summarize` work end-to-end on fixture and a real repo.

---

## Phase 6 — Minimal Web UI (5 sessions, ~14h)

**Goal:** `codegraph serve` opens browser to interactive D3 graph + search + AI chat.

### T6.1 — FastAPI server skeleton
**Files:** `packages/codegraph/server/api.py` (~150 LOC), `tests/test_api.py`
**Steps:** Endpoints:
- `GET /api/health` → 200
- `GET /api/graph?type=module` → nodes + edges (modules only, all `imports` edges)
- `GET /api/graph?type=entity&file=<path>` → entities in that file + edges
- `GET /api/search?q=<query>&semantic=false&limit=20`
- `GET /api/entity/{entity_id}` → full UIR record
- `POST /api/ask` → streaming SSE response from GraphRAG
- `GET /api/impact/{entity_id}?depth=3`
CORS allow `http://localhost:5173` (Vite dev). Mount `static/` at `/`.
**Verify:** `pytest tests/test_api.py` covers each endpoint with httpx client.
**Commit:** `T6.1: FastAPI endpoints (search/graph/entity/ask/impact)`

### T6.2 — Vite + React + Tailwind scaffold
**Files:** `packages/web/` (everything)
**Steps:** `npm create vite@latest packages/web -- --template react-ts`. Add Tailwind via `@tailwindcss/postcss`. Install `d3` types. Single `App.tsx` with empty layout: left (graph), top (search bar), right (chat panel), bottom (entity detail).
**Verify:** `cd packages/web && npm run dev` opens at localhost:5173.
**Commit:** `T6.2: Vite + React + Tailwind frontend scaffold`

### T6.3 — D3 force-directed module graph
**Files:** `packages/web/src/components/Graph.tsx`, `src/api/index.ts`
**Steps:** Fetch `/api/graph?type=module`. D3 force simulation: `forceManyBody(-200) + forceLink(distance=80) + forceCenter`. Nodes = circles, edges = lines. Click node → fire `onSelect(entity_id)`. Drag + zoom enabled.
**Verify:** Manual: run server with indexed fixture, see ~4 nodes connected.
**Commit:** `T6.3: D3 force-directed module graph`

### T6.4 — Search bar + results panel
**Files:** `src/components/SearchBar.tsx`, `src/components/EntityPanel.tsx`
**Steps:** Debounced search input (250ms). Hits `/api/search`. Results panel shows top-10 entities with name + file + snippet. Click → highlight in graph + fetch full entity into right panel.
**Verify:** Manual: search "authenticate" → result clickable → highlights node.
**Commit:** `T6.4: search bar and entity details panel`

### T6.5 — AI chat panel with streaming
**Files:** `src/components/ChatPanel.tsx`
**Steps:** Right sidebar with text input + scrolling message list. POST to `/api/ask`, consume SSE stream, append tokens to UI in real time. Parse `[entity_id]` citations and render as clickable spans that highlight the graph node.
**Verify:** Manual: ask question → tokens stream in → citation clicks work.
**Commit:** `T6.5: AI chat panel with SSE streaming and citation links`

### T6.6 — `codegraph serve` command
**Files:** `cli.py serve` (extend)
**Steps:**
1. Build frontend: `npm run build` outputs to `packages/codegraph/server/static/`
2. CLI runs uvicorn with FastAPI app, mounts static at `/`
3. Opens `webbrowser.open("http://localhost:8765")` after 1s delay
4. Provide `--dev` flag that skips build and assumes Vite dev server running separately
**Verify:** `uv run codegraph serve` opens browser, app loads, all features work end-to-end.
**Commit:** `T6.6: codegraph serve packages frontend and opens browser`

**Phase 6 exit:** Fully functional web UI demo on a real repo.

---

## Phase 7 — MCP Server (3 sessions, ~6h) — THE KILLER DEMO

**Goal:** the host agent itself calls CodeGraph as a tool. This sells the entire vision.

### T7.1 — MCP server skeleton
**Files:** `packages/codegraph/server/mcp_server.py` (~150 LOC), `tests/test_mcp.py`
**Steps:** Using `mcp` Python SDK:
```python
from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("codegraph")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="search_code", description="Hybrid literal+semantic search over the indexed codebase.",
             inputSchema={"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer","default":10}},"required":["query"]}),
        Tool(name="get_entity_context", description="Get full source + immediate neighbors for an entity_id.",
             inputSchema={"type":"object","properties":{"entity_id":{"type":"string"}},"required":["entity_id"]}),
        Tool(name="impact_analysis", description="Find what would break if this entity changed.",
             inputSchema={"type":"object","properties":{"entity_id":{"type":"string"},"depth":{"type":"integer","default":3}},"required":["entity_id"]}),
        Tool(name="ask_codebase", description="Ask a natural-language question about the codebase. Returns a grounded answer.",
             inputSchema={"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}),
    ]
```
Run via `python -m codegraph.server.mcp_server`.
**Verify:** `mcp dev` (CLI from mcp SDK) connects and lists 4 tools.
**Commit:** `T7.1: MCP server skeleton with 4 tools`

### T7.2 — Wire MCP tools to library
**Files:** `server/mcp_server.py` (extend)
**Steps:**
```python
@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    store = GraphStore(get_db_path())
    if name == "search_code":
        results = hybrid_search(store, arguments["query"], limit=arguments.get("limit",10))
        return [TextContent(type="text", text=json.dumps(results, default=str))]
    if name == "get_entity_context":
        ent = store.get_entity(arguments["entity_id"])
        neighbors = store.get_neighbors(arguments["entity_id"], depth=1)
        return [TextContent(type="text", text=json.dumps({"entity":ent,"neighbors":neighbors}, default=str))]
    if name == "impact_analysis":
        results = impact_query(store, arguments["entity_id"], depth=arguments.get("depth",3))
        return [TextContent(type="text", text=json.dumps(results, default=str))]
    if name == "ask_codebase":
        full = "".join(GraphRAG(store, LLM()).ask_stream(arguments["query"]))
        return [TextContent(type="text", text=full)]
    raise ValueError(f"unknown tool {name}")
```
**Verify:** From `mcp dev` REPL, call each tool and verify JSON response.
**Commit:** `T7.2: wire MCP tools to graph and AI engine`

### T7.3 — Install + record demo
**Files:** `README.md` (extend with MCP install section)
**Steps:** Document:
```bash
claude mcp add codegraph -- uv run python -m codegraph.server.mcp_server --db /path/to/.codegraph/graph.duckdb
```
Then inside the host agent, ask: "Use codegraph to explain how authentication works in this repo." Record a 60-second screencap. Save as `docs/demo.gif`. Reference it in README hero.
**Verify:** Demo recording in repo; reproducible install instructions.
**Commit:** `T7.3: document MCP install and record demo GIF`

**Phase 7 exit:** the host agent can call CodeGraph tools live. Demo GIF in README.

---

## Phase 8 — Polish & Demo Readiness (2 sessions, ~5h)

### T8.1 — README rewrite
**Files:** `README.md`
**Steps:** Sections in order:
1. Hero: `docs/demo.gif` + tagline ("Local AI memory layer for your codebase")
2. What it does — 3 bullets
3. Quickstart — 5 commands max
4. Example queries — 3 with expected output
5. Architecture — Mermaid diagram of pipeline
6. Stack — bulleted table
7. Roadmap — what was cut from MVP
8. Acknowledgments — tree-sitter, DuckDB, sentence-transformers, Anthropic
**Verify:** README renders on GitHub correctly; demo GIF plays.
**Commit:** `T8.1: rewrite README with demo and quickstart`

### T8.2 — Benchmark + final STATUS update
**Files:** `STATUS.md`, optionally `benchmarks/results.md`
**Steps:** Index `tiangolo/fastapi`, record:
- Files indexed, entities, edges
- Index time (cold)
- Index time (warm = incremental, no changes)
- Embedding time
- Semantic search latency p50/p95
- Ask latency p50/p95 (full GraphRAG)
- DuckDB file size
Append to README. Mark STATUS as "Shipped".
**Verify:** Numbers in README; STATUS shows all phases DONE.
**Commit:** `T8.2: benchmark on fastapi and mark MVP shipped`

**Phase 8 exit:** MVP shipped. STATUS.md says SHIPPED.

---

## Phase 9 — Stretch (Optional, only after MVP shipped)

These are nice-to-haves the user can pick from after the MVP demo works. Each is 1–3 sessions.

- **T9.1** Git blame integration — per-entity ownership via `git blame` + ownership in entity panel
- **T9.2** Bug-density heatmap overlay — git log + commit keyword classifier ("fix", "bug")
- **T9.3** Architecture pattern detection — MVC/Layered/Microservices heuristics (the MD §12.1 list)
- **T9.4** D3 graph overlays — risk heatmap, complexity heatmap, ownership coloring
- **T9.5** Background file-watching daemon — `codegraph watch` re-indexes on save
- **T9.6** Refactor suggestions — feature-envy + dead-code detection from call graph
- **T9.7** Solidity parser — if the smart-contract angle interests you
- **T9.8** Cross-language HTTP edges — TS fetch() ↔ FastAPI route matching

Each gets its own task block in `plan/09-stretch.md` if/when picked up.

---

## 6. Critical Files Inventory (the files you'll edit most)

The 90/10 rule: ~10 files account for ~90% of edits.

| File | Phase introduced | Why critical |
|---|---|---|
| `packages/codegraph/uir.py` | 1 | UIR contract. Schema changes ripple everywhere. |
| `packages/codegraph/parsers/python.py` | 1 | Largest parser; extended in 2, 4. |
| `packages/codegraph/parsers/typescript.py` | 2 | Second largest; extended in 4. |
| `packages/codegraph/graph/schema.sql` | 1 | DB shape. Migration if changed. |
| `packages/codegraph/graph/store.py` | 1 | Read/write API. Touched in nearly every phase. |
| `packages/codegraph/graph/queries.py` | 1 | Canned queries; grows with each phase. |
| `packages/codegraph/ai/graphrag.py` | 5 | Retrieval + prompt assembly. Tuning lives here. |
| `packages/codegraph/cli.py` | 0 | One entry point; new commands appended. |
| `packages/codegraph/server/mcp_server.py` | 7 | The killer demo. Small but critical. |
| `STATUS.md` | 0 | Updated every single session. |

If you're editing files outside this list >50% of the time, you're probably scope-creeping.

---

## 7. Failure Modes & Recovery

Common issues and exact fixes:

| Symptom | Cause | Fix |
|---|---|---|
| `tree-sitter` import error | Missing native build | `uv add tree-sitter tree-sitter-languages` (rebuilds wheel) |
| Embeddings download stuck | Network / proxy | Set `HF_ENDPOINT=https://hf-mirror.com` or pre-download model |
| DuckDB locked | Multiple processes opened same file | Close all; use `read_only=True` for read paths |
| `claude-sonnet-4-6` 404 | Old SDK version or wrong model ID | `uv add anthropic --upgrade`; double-check model ID is exactly `claude-sonnet-4-6` |
| `mcp dev` doesn't see tools | Stdio framing broken | Ensure server runs without printing to stdout (logs to stderr only) |
| Search returns nothing semantic | Embeddings column NULL | Re-run index; check `SELECT count(embedding) FROM entities` |
| CLI hangs on large repo | Synchronous embedding | Batch size too small; increase to 64; or process in background |
| `ruff` flags new file every commit | `ruff format` not run | Add `ruff format` to pre-commit; configure VS Code format-on-save |
| Pytest can't find packages | `src` layout vs `packages/` | Ensure `packages/codegraph/__init__.py` exists; check `pyproject.toml` `[tool.setuptools.packages.find]` |
| Vite dev server CORS error | API and frontend on different ports | FastAPI `CORSMiddleware` already configured in T6.1 |

---

## 8. Estimated Budget

| Phase | Sessions | Hours | Cumulative weeks (at 3 sessions/wk × 2h) |
|---|---|---|---|
| 0 — Setup | 1 | 3 | 0.5 |
| 1 — Slice | 5–6 | 12 | 2.5 |
| 2 — Multi-file + TS | 5 | 12 | 4.5 |
| 3 — Embeddings | 4 | 10 | 6.0 |
| 4 — Call graph | 4 | 10 | 7.5 |
| 5 — GraphRAG | 5 | 14 | 9.5 |
| 6 — Web UI | 5 | 14 | 11.5 |
| 7 — MCP | 3 | 6 | 12.5 |
| 8 — Polish | 2 | 5 | 13.0 |
| **MVP TOTAL** | **~34** | **~86** | **~13 weeks** |
| 9 — Stretch (each item) | 1–3 | 3–8 | optional |

These are upper bounds. With the host agent doing the heavy lifting, multiple atomic tasks often fit in one session. Realistic completion: **6–12 weeks** of part-time work.

---

## 9. Definition of "Shipped"

All of the following must be true:

1. STATUS.md shows all 8 phases DONE
2. `git log --oneline` shows 30+ commits, one per task ID
3. `uv run pytest` shows green (target: 60+ tests passing)
4. `uv run codegraph index tests/fixtures/sample_repo_py/` succeeds in <2s
5. `uv run codegraph index /path/to/fastapi` succeeds in <60s
6. `uv run codegraph ask "what does this codebase do?"` returns coherent grounded answer
7. `uv run codegraph serve` opens browser, all features work
8. the host agent with codegraph MCP installed can answer codebase questions via tool calls
9. README has demo GIF, quickstart, architecture diagram, benchmarks
10. CI green on main

If any are false, you're not shipped yet. If all are true: ship it, post to HN/Show HN, get feedback, then consider Phase 9 stretches.

---

## 10. Why This Plan Is Resilient (recap)

Five-layer defense against mid-session token-exhaustion:

1. **`AGENTS.md` (<2KB, auto-loaded)** — every new session has full context in seconds without re-reading 89KB spec
2. **`STATUS.md` (updated every session)** — single source of truth for "where we are"
3. **`plan/NN-*.md` (per-phase, ~5KB each)** — atomic task definitions, only current phase loaded
4. **Commit-per-task discipline** — `git log` IS the progress log; mid-task crashes recoverable via `git status` + `git diff`
5. **Vertical slicing** — Phase 1 (≈5 sessions) gives a working tool; every later phase thickens, none are load-bearing for earlier phases to function

If you stop the build at any phase boundary, what you have still works as a useful tool. The MD's original "build everything horizontally then ship" pattern gives you nothing usable until month 3.
