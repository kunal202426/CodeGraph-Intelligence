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
