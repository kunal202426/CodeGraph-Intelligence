# Project rules: CodeGraph-Intelligence

Repo-specific conventions and testing discipline. AGENTS.md remains the source of truth
for repo conventions.


## Repo-specific conventions (from tracked AGENTS.md, restated here for the delta view)

- Package manager is uv, not pip or poetry. `uv sync --extra dev` to install.
- Before any change: `uv run pytest -q` for a green baseline. Before any commit:
  `uv run ruff check && uv run ruff format --check && uv run pytest -q` all green.
- `uv run codegraph --version` must always pass on main; it is the CI smoke check.
- Update STATUS.md's `## Current` section when a task changes status, test count, or next
  steps. This repo uses STATUS.md plus docs/DECISIONS.md as its status and decision log.
- No half-finished features on main. Note out-of-scope ideas in STATUS.md instead of
  expanding a change's scope.

## Testing discipline (from docs/, this is how prior sessions actually worked here)

The docs/ folder holds a real, dated log of manual test sessions, stress tests, cost A/B
tests, and a competitor deep dive, all written in the same honest, no-spin style. Read them
once before doing meaningful work on this repo (already summarized in docs/ONBOARDING.md).
The pattern worth carrying forward into any new session:

- Test against real, messy repos (LedgerGuard, JobHuntPro, a live production codebase, and
  eventually Grafana), not just the fixture suite. Every real bug found in docs/ was found
  this way; the fixture suite never caught any of them on its own.
- Report negative results as plainly as positive ones. docs/COST_EFFICIENCY_FINDINGS logs a
  round where using the tool cost 34 percent more, in the same voice as the rounds where it
  won. Don't soften a bad result or round up an inconclusive one.
- Verify a tool's own output against ground truth (grep, git blame, manual inspection)
  before trusting an agent-behavior theory. The 2026-08-25 entry in COST_EFFICIENCY_FINDINGS
  is the canonical example: three rounds of guide-wording changes fixed nothing because the
  underlying `impact_analysis` answer was wrong, not because the agent wasn't nudged well.
- When testing manually with the user, follow docs/MANUAL_TEST_PROMPT.md's protocol exactly:
  one checklist item at a time, stop and report PASS or ISSUE FOUND after each, wait for the
  user to say "next," never fix or hide a failure silently mid-run.

## Lost local context (not recoverable from GitHub)

AGENTS.md references a `.internal/` folder (gitignored) holding the original MVP build plan
and detailed platform spec, kept locally "for the repo owner's own reference." That folder
does not exist on the maintainer's machine. It was never pushed to GitHub by design, so the SSD
replacement took it with it. If a backup exists elsewhere (another machine, a cloud drive,
an export), it would need to be restored manually; it cannot be pulled back from git.
