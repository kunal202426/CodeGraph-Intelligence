# Manual test walkthrough — paste this as your first message in a new session

I want to manually test CodeGraph/Kortex end-to-end, one surface at a time. Follow these
rules exactly — do not deviate:

1. **One test at a time.** Pick the next untested item from the checklist below, run it,
   show me the exact command and its real output (or a representative excerpt if it's
   long).
2. **Stop after every single test.** Give a one-line verdict — `PASS` or `ISSUE FOUND` —
   then **wait for me to say "next" (or similar) before moving on.** Do not chain
   multiple tests in one turn, and do not run ahead to "save time."
3. **If something breaks or looks wrong, do not fix it, hide it, retry it silently, or
   talk yourself out of it.** Mark it clearly as `ISSUE FOUND`, show the exact repro
   (command + full error/output), and give me a one-sentence guess at severity. Then
   stop and wait — I'll decide whether to fix now, log it, or skip it.
4. **Don't summarize ahead or skip items** because they seem similar to one already
   passed. Each checklist item gets its own explicit run and its own verdict.
5. Use the real project (this repo, or a project I point you at) — not a synthetic
   toy fixture — since that's what surfaces real bugs.
6. Before starting, run `codegraph status` once to show me the current index state, then
   begin at item 1.

## Checklist

### Core indexing
1. `codegraph index .` (or `codegraph init` if not yet set up) — cold index
2. Re-run the same index command immediately — confirm incremental hash-skip (fast, "N
   unchanged")
3. `codegraph status` — file/entity/edge/embedding counts + staleness line
4. `codegraph doctor` — PASS/FAIL lines for index, MCP config, agent guide, freshness

### Search & retrieval
5. `codegraph search "<some literal term that appears in code>"`
6. `codegraph search "<a concept described in plain English, no matching keywords>"` —
   semantic search proving it
7. `codegraph context "<some symbol or concept>"`

### Graph analysis
8. `codegraph deps <some_function>`
9. `codegraph impact <some_function>` — blast radius
10. `codegraph trace <entity_a> <entity_b>` — pick two you know are connected
11. `codegraph cycles`
12. `codegraph smells`
13. `codegraph deadcode` — spot-check a couple of flagged candidates by hand: are they
    really dead, or a false positive (framework route, JSX usage, type annotation, etc.)?
14. `codegraph owner <some_function>` — git-blame ownership
15. `codegraph layers`

### Freshness / watch
16. `codegraph watch .` — edit a file while it's running, confirm it re-indexes in
    real time, then stop it
17. `codegraph hooks install` — check `.git/hooks/post-commit` etc. got the managed
    block; make a commit and confirm it re-indexes in the background; then
    `codegraph hooks uninstall` and confirm the block is cleanly removed

### AI features (needs `ANTHROPIC_API_KEY`, skip gracefully if you don't have one set)
18. `codegraph ask "<a real question about this codebase>"`
19. `codegraph summarize`

### Web UI
20. `codegraph serve` — open it, try the graph view, try search, try any chat feature

### MCP — install
21. `codegraph install claude --print-config` — dry run, inspect the JSON
22. `codegraph install claude -y` (or whichever agent you actually use) — real install,
    then confirm the agent picks up the tools after a restart
23. `codegraph uninstall claude -y` — confirm clean removal, then reinstall if you want
    to keep using it afterward

### MCP — the 11 tools, from inside your agent
24. `index_status`
25. `search_code`
26. `get_context`
27. `get_entity_context`
28. `impact_analysis`
29. `trace_path`
30. `list_files`
31. `reindex` — make a small code change first, then call this, confirm it picks it up
32. `get_unsummarized_entities` + `store_summaries` — run the pair together, confirm a
    summary gets written and re-embedded
33. `ask_codebase` (needs API key)

### Recently-added resolution features — worth extra scrutiny, least manually verified
34. Pick a real `obj.method()` call in this codebase where two *different* classes
    define a method with the *same name* — confirm `impact_analysis`/`trace_path` on one
    doesn't wrongly pull in callers of the other (receiver-type inference)
35. Pick a method only declared on a *base class*, called via a subclass instance —
    confirm it resolves through the inheritance walk instead of showing as unreachable
36. If this codebase (or another project you point me at) uses Flask/FastAPI/Express/
    Django/Spring/Rails routing — confirm a route handler with no direct static caller
    shows up as **called** (via the route registration), not as dead code
37. If there's a frontend making `fetch`/`axios` calls to a backend route with a static
    URL — confirm `trace_path` finds the cross-language edge between them

### Comparative A/B test — with vs. without CodeGraph (run after everything above)
38. Two separate Claude Code sessions against the same codebase, same prompts, one with
    the CodeGraph MCP tool wired in and one without. Compare: tokens spent (reading
    tokens especially), $ cost, response latency, and — critically — answer *quality*
    (does the token/cost saving come at the expense of correctness or completeness?).
    Setup to be detailed in full once the checklist above is complete.

Start now: run `codegraph status`, then item 1.
