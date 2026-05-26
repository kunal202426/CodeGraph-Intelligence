# Phase 9 — Stretch (Optional, only after MVP shipped)

> Pick-from-the-menu nice-to-haves. Do NOT start until Phase 8 has SHIPPED.

These are 1–3 sessions each. Each gets its own task block below when picked up.

## Backlog

- **T9.1 Git blame integration** — per-entity ownership via `git blame` + ownership in entity panel
- **T9.2 Bug-density heatmap overlay** — git log + commit-message keyword classifier ("fix", "bug")
- **T9.3 Architecture pattern detection** — MVC / Layered / Microservices heuristics (per source spec §12.1 list)
- **T9.4 D3 graph overlays** — risk heatmap, complexity heatmap, ownership coloring
- **T9.5 Background file-watching daemon** — `codegraph watch` re-indexes on save
- **T9.6 Refactor suggestions** — feature-envy + dead-code detection from call graph
- **T9.7 Solidity parser** — if the smart-contract angle interests you
- **T9.8 Cross-language HTTP edges** — TS `fetch()` ↔ FastAPI route matching

## Picked up

### T9.6 — Dead-code detection (refactor suggestions, part 1)
**Files:** `packages/codegraph/analysis/refactor.py` (new), `cli.py deadcode` (new command), `tests/test_refactor.py`
**Steps:** `find_dead_code(conn, include_methods=False)` returns top-level functions/classes
that are never the `dst` of a `calls`/`imports` edge (SQL `NOT EXISTS`). Excludes entrypoints
(`main`/`__main__`), `test_*`, and dunders; methods opt-in via `--methods` (self.x() resolution
is weak). New `codegraph deadcode` command renders a Rich table + false-positive caveat.
**Verify:** `tests/test_refactor.py` (orphan flagged, callers/entrypoints/tests/dunders excluded,
methods opt-in, 3 CLI cases); live demo on sample_repo_py. Updated test_smoke command set.
**Commit:** `T9.6: dead-code detection (deadcode command)`
**Note:** feature-envy (the other half of T9.6) deferred — needs method↔attribute access data
the current UIR doesn't capture.

### T9.1 — Git-blame ownership
**Files:** `packages/codegraph/analysis/ownership.py` (new), `cli.py owner` (new command), `tests/test_ownership.py`
**Steps:** `entity_ownership(repo_root, file, start, end)` runs `git blame --line-porcelain -L start,end`
and tallies `author ` records (one per source line → accurate). Returns `[Ownership(author, lines)]`
sorted desc; `primary_owner()` helper. Empty on non-git / untracked / bad range. New `codegraph owner
<entity> --repo <root>` resolves the entity, fetches its span, prints an ownership table + primary owner.
**Verify:** `tests/test_ownership.py` (single/two-author, non-git, untracked, bad range, 3 CLI cases) —
throwaway git repo with GIT_AUTHOR_* env (no global config touched); live demo on this repo. Updated
test_smoke command set.
**Commit:** `T9.1: git-blame ownership (owner command)`
**Note:** `--repo` must match the indexed root (entity `file` paths are root-relative). Web entity-panel
ownership display deferred.

## Task block template

When you pick one up, write a block in this file using the same format as earlier phase files:

```
### T9.N — <Title>
**Files:** ...
**Steps:** ...
**Verify:** ...
**Commit:** `T9.N: <imperative summary>`
```

Then update STATUS.md to add a Phase 9 progress section.
