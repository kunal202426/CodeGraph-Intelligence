# Phase 7 — MCP Server (THE KILLER DEMO)

> Per-phase plan. Read this + STATUS.md + AGENTS.md.

**Goal:** The host agent itself calls CodeGraph as a tool. This sells the entire vision.
**Estimated:** 3 sessions, ~6h
**Exit:** The host agent can call CodeGraph tools live. Demo GIF in README.

## Tasks

### T7.1 — MCP server skeleton
**Files:** `packages/codegraph/server/mcp_server.py` (~150 LOC), `tests/test_mcp.py`
**Using the `mcp` Python SDK:**
```python
from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("codegraph")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="search_code",
             description="Hybrid literal+semantic search over the indexed codebase.",
             inputSchema={"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer","default":10}},"required":["query"]}),
        Tool(name="get_entity_context",
             description="Get full source + immediate neighbors for an entity_id.",
             inputSchema={"type":"object","properties":{"entity_id":{"type":"string"}},"required":["entity_id"]}),
        Tool(name="impact_analysis",
             description="Find what would break if this entity changed.",
             inputSchema={"type":"object","properties":{"entity_id":{"type":"string"},"depth":{"type":"integer","default":3}},"required":["entity_id"]}),
        Tool(name="ask_codebase",
             description="Ask a natural-language question about the codebase. Returns a grounded answer.",
             inputSchema={"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}),
    ]
```
Run via `python -m codegraph.server.mcp_server`.
**Verify:** `mcp dev` (from the mcp SDK CLI) connects and lists 4 tools.
**Commit:** `T7.1: MCP server skeleton with 4 tools`

### T7.2 — Wire MCP tools to library
**Files:** `server/mcp_server.py` (extend)
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
**Install snippet to document:**
```bash
claude mcp add codegraph -- uv run python -m codegraph.server.mcp_server --db /path/to/.codegraph/graph.duckdb
```
Then inside the host agent, ask: "Use codegraph to explain how authentication works in this repo." Record a 60-second screencap. Save as `docs/demo.gif`. Reference it in the README hero.
**Verify:** Demo recording in repo; reproducible install instructions.
**Commit:** `T7.3: document MCP install and record demo GIF`
