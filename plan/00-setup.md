# Phase 0 — Project Setup

> Per-phase plan. Read this + STATUS.md + AGENTS.md in your session. No need to load BUILD_PLAN.md unless you need full rationale (linked from this file).

**Goal:** `uv run codegraph --help` prints; `pytest` runs zero tests successfully; CI lint passes.
**Estimated:** 1 session, ~3h
**Exit criteria:** `uv run codegraph --help` works. CI green. STATUS.md shows Phase 1 next.

> **Status:** This phase is COMPLETE as of the initial commit. Files kept for reference; T0.1–T0.5 already shipped in commit `0f052a8`. Only **T0.6** (this file's creation) is in-flight.

## Tasks

### T0.1 — Init repo + pyproject.toml
**Files:** `pyproject.toml`, `.gitignore`, `.python-version`, `uv.lock`
**Steps:**
1. `git init && git branch -M main`
2. Write `pyproject.toml` (deps locked per BUILD_PLAN §1)
3. Write `.gitignore`: Python + node + `.codegraph/` + `*.duckdb`
4. `uv sync --extra dev` to create venv and lockfile (the `--extra dev` is required to pull pytest/ruff/httpx)
**Verify:** `uv run python -c "import codegraph"` works (after T0.3)
**Commit:** `T0.1: init Python project with uv`

### T0.2 — AGENTS.md, STATUS.md, README stub
**Files:** `AGENTS.md`, `STATUS.md`, `README.md`, `BUILD_PLAN.md`, source spec at repo root
**Steps:** AGENTS.md = boot doc (~2KB). STATUS.md = progress tracker. README is a 5-line placeholder until T8.1. All long-lived MD trackers live at repo root for easy in-place updates.
**Commit:** `T0.2: add AGENTS.md, STATUS.md, README stub`

### T0.3 — Scaffold package layout
**Files:** All `__init__.py` files per BUILD_PLAN §2 layout; empty stubs for the `.py` files
**Steps:** Create directory tree. Every Python module gets `__init__.py`. Every `.py` file gets a 1-line docstring naming the task that populates it. After scaffolding, run `uv pip install -e . --force-reinstall --no-deps` to refresh the editable install against now-existing source.
**Verify:** `python -c "import codegraph; import codegraph.parsers; ..."` for all 25 modules
**Commit:** `T0.3: scaffold package layout`

### T0.4 — CLI entry point with command stubs
**Files:** `packages/codegraph/cli.py`
**Steps:** typer app with stubs for: `index`, `search`, `ask`, `impact`, `cycles`, `smells`, `summarize`, `serve`. Each prints a "lands at TX.Y" message. Add `--version`. `[project.scripts]` already wired in pyproject.toml.
**Note:** Ruff lints `typer.Option()` in defaults as `B008` — globally ignored in `[tool.ruff.lint]` since the pattern is intentional for typer + FastAPI.
**Verify:** `uv run codegraph --help` shows all 8 commands
**Commit:** `T0.4: add CLI stubs for all 8 commands`

### T0.5 — Pytest + CI
**Files:** `tests/test_smoke.py`, `.github/workflows/ci.yml`
**Steps:** 3 smoke tests: package version, all 25 modules import, CLI exposes exactly 8 commands (Typer stores `name=None` for inferred names — fall back to `cmd.callback.__name__`). CI runs `uv sync --frozen --extra dev` + `ruff check` + `ruff format --check` + `pytest -v` + `codegraph --version` on push/PR to main.
**Verify:** Local: `uv run pytest -v` passes (3 tests). CI green after push.
**Commit:** `T0.5: add pytest skeleton and GitHub Actions CI`

### T0.6 — Per-phase plan files
**Files:** `plan/00-setup.md` ... `plan/09-stretch.md`
**Steps:** Split BUILD_PLAN.md's phase-by-phase content into 10 self-contained files in `plan/`. Each file ≤ 8KB. Future sessions only load STATUS.md + AGENTS.md + the current phase file (≤15KB total) — never the full BUILD_PLAN.md.
**Verify:** `ls plan/*.md | wc -l` == 10; each file ≤ 8KB
**Commit:** `T0.6: add per-phase plan files`
