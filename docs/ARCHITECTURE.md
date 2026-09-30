# Project architecture: CodeGraph-Intelligence

Structure: a monorepo with two packages. `packages/codegraph` is the Python engine (parsing,
graph storage, resolution, embeddings, GraphRAG, CLI, HTTP API, MCP server). `packages/web`
is a separate React/Vite/D3 frontend that talks to the FastAPI server over HTTP. The Python
package's own layout, by directory:

- `walker.py`: walks a repo respecting `.gitignore`, detects language per file.
- `parsers/`: tree-sitter parsers for 22 languages, turn source into UIR entities and edges.
- `uir.py`: the Unified Intermediate Representation, the schema every parser must emit into.
  This is the contract; if something looks wrong elsewhere, check against this first.
- `graph/`: the storage and query layer.
  - `schema.sql`: the DuckDB DDL, the other source-of-truth contract alongside uir.py.
  - `store.py`: read/write access to the DuckDB file.
  - `resolver.py`: symbol resolution, turning a bare call or import into a resolved edge.
  - `queries.py`, `ranking.py`, `locate.py`: the graph queries the CLI and MCP tools expose
    (search, deps, impact, cycles, smells, and so on).
- `resolution/`: framework and HTTP-edge resolution, cross-file/cross-language linking
  beyond what the tree-sitter parse and resolver alone can see.
- `embeddings/`: the sentence-transformers pipeline, plus (per STATUS.md, 2026-09) a worker
  subprocess split so importing torch never blocks the MCP server's asyncio event loop.
- `ai/`: the GraphRAG layer, assembles graph context into a prompt for the Anthropic API.
- `server/`: `api.py` (FastAPI, backs `codegraph serve` and the web UI) and `mcp_server.py`
  (the MCP entry point an agent actually connects to).
- `sync/`: keeps the index fresh, backs `codegraph watch`.
- `analysis/`: structural analysis (dead code, smells, layering) that does not need the LLM.
- `installer/`: `codegraph init`, wires the MCP server into an agent's config and writes a
  guide `CLAUDE.md` in the target project.
- `cli.py`, `config.py`, `proc.py`: the Typer CLI, config loading, subprocess helpers.

Data flow (see README.md's Architecture section for the diagram this restates): repo files
go through the walker into tree-sitter parsers, which emit UIR entities and edges; the
resolver turns bare names into resolved cross-file edges; everything lands in one DuckDB
file alongside sentence-transformers embeddings. From there, graph queries and GraphRAG
both read the same store, and both feed the CLI, the FastAPI/web UI, and the MCP server,
which is what an agent like Claude Code actually calls at question time.

Boundaries: UIR (`uir.py`) and the DuckDB schema (`graph/schema.sql`) are the two contracts
everything else is built against. A parser's only job is to emit valid UIR; a query's only
job is to read the DuckDB schema. Framework-specific resolution lives in `resolution/`, kept
separate from the generic `graph/resolver.py` so language-agnostic resolution does not grow
per-framework special cases inline.

Why shaped this way: the project's own thesis (README.md) is that agents re-read the same
files every session because they have no persistent memory of a codebase; the fix is to
build the graph once and serve it cheaply over MCP, which is why the MCP server is a first-
class entry point alongside the CLI, not an afterthought bolted onto a CLI tool. STATUS.md's
history (notably the 2026-08-12 entry) shows the MCP boot path was hardened specifically
around Claude Code's 30-second connect timeout and around not blocking the server's asyncio
loop with a first-time torch import, both consequences of MCP being a primary interface, not
a secondary one.
