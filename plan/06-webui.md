# Phase 6 — Minimal Web UI

> Per-phase plan. Read this + STATUS.md + AGENTS.md.

**Goal:** `codegraph serve` opens browser to interactive D3 graph + search + AI chat.
**Estimated:** 5 sessions, ~14h
**Exit:** Fully functional web UI demo on a real repo.

## Tasks

### T6.1 — FastAPI server skeleton
**Files:** `packages/codegraph/server/api.py` (~150 LOC), `tests/test_api.py`
**Endpoints:**
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
**Verify:** Manual — run server with indexed fixture, see ~4 nodes connected.
**Commit:** `T6.3: D3 force-directed module graph`

### T6.4 — Search bar + results panel
**Files:** `src/components/SearchBar.tsx`, `src/components/EntityPanel.tsx`
**Steps:** Debounced search input (250ms). Hits `/api/search`. Results panel shows top-10 entities with name + file + snippet. Click → highlight in graph + fetch full entity into right panel.
**Verify:** Manual — search "authenticate" → result clickable → highlights node.
**Commit:** `T6.4: search bar and entity details panel`

### T6.5 — AI chat panel with streaming
**Files:** `src/components/ChatPanel.tsx`
**Steps:** Right sidebar with text input + scrolling message list. POST to `/api/ask`, consume SSE stream, append tokens to UI in real time. Parse `[entity_id]` citations and render as clickable spans that highlight the graph node.
**Verify:** Manual — ask question → tokens stream in → citation clicks work.
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
