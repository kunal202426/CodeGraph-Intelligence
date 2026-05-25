# CodeGraph

Local AI memory layer for codebases — graph + semantic search + GraphRAG + an MCP server that lets any MCP-compatible agent query your code as a tool.

**Status:** Under construction. See [STATUS.md](STATUS.md) for progress and [plan/](plan/) for the build roadmap. The full README (hero, architecture diagram, benchmarks) lands at T8.1.

## Quickstart

```bash
uv sync --extra dev
uv run codegraph index /path/to/repo          # build the graph (DuckDB) + embeddings
uv run codegraph search "user authentication"  # hybrid literal + semantic search
uv run codegraph ask "how does login work?"     # grounded GraphRAG answer (needs ANTHROPIC_API_KEY)
uv run codegraph serve                           # web UI: D3 graph + search + AI chat
```

Other commands: `deps`, `impact`, `cycles`, `smells`, `summarize`. Run `uv run codegraph --help`.

## MCP integration (use CodeGraph from your agent)

CodeGraph ships an [MCP](https://modelcontextprotocol.io) server, so an MCP-compatible
agent (e.g. Claude Code) can call your indexed codebase as a tool.

### 1. Index the repo

```bash
uv run codegraph index /path/to/repo
# writes /path/to/repo/.codegraph/graph.duckdb
```

### 2. Register the server

```bash
claude mcp add codegraph -- \
  uv run python -m codegraph.server.mcp_server --db /path/to/repo/.codegraph/graph.duckdb
```

The graph path can also be set via the `CODEGRAPH_DB` environment variable
(the `--db` flag takes precedence).

### 3. Ask the agent to use it

> "Use codegraph to explain how authentication works in this repo."

The agent will call the tools below and answer from your actual code.

### Exposed tools

| Tool | What it does |
|---|---|
| `search_code` | Hybrid literal + semantic search → matching entities with `file:line` |
| `get_entity_context` | Full source + immediate neighbours (`depends_on`, `called_by`) for an `entity_id` |
| `impact_analysis` | Reverse-call blast radius — what breaks if an entity changes |
| `ask_codebase` | Natural-language question answered via GraphRAG with `entity_id` citations |

> Note: `ask_codebase` requires embeddings in the index (don't pass `--no-embed`)
> and an `ANTHROPIC_API_KEY`; the other three work on any index.

### Demo

A short screencast of Claude Code calling CodeGraph lives at `docs/demo.gif` (recorded manually).
