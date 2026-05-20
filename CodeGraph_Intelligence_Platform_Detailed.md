# CodeGraph Intelligence Platform v2
## Full Research, Architecture & Build Plan — Detailed Edition

> **Strategic Positioning:** AI Infrastructure for Software Understanding — a Graph Memory System for AI Coding Agents

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement — Deep Analysis](#2-problem-statement)
3. [System Architecture — Complete Design](#3-system-architecture)
4. [Unified Intermediate Representation (UIR)](#4-unified-intermediate-representation)
5. [Parsing & AST Engine](#5-parsing--ast-engine)
6. [Symbol Resolution & Type Inference](#6-symbol-resolution--type-inference)
7. [Call Graph & Data Flow Engine](#7-call-graph--data-flow-engine)
8. [Knowledge Graph Layer](#8-knowledge-graph-layer)
9. [AI & Semantic Intelligence Layer](#9-ai--semantic-intelligence-layer)
10. [GraphRAG Engine](#10-graphrag-engine)
11. [Multi-Language Support](#11-multi-language-support)
12. [Architecture Intelligence](#12-architecture-intelligence)
13. [Git Intelligence & Temporal Graph](#13-git-intelligence--temporal-graph)
14. [AI Agent APIs](#14-ai-agent-apis)
15. [IDE Integrations](#15-ide-integrations)
16. [Frontend Visualization](#16-frontend-visualization)
17. [Scaling & Infrastructure](#17-scaling--infrastructure)
18. [Security & Enterprise Features](#18-security--enterprise-features)
19. [Advanced Research Features](#19-advanced-research-features)
20. [Complete Tech Stack](#20-complete-tech-stack)
21. [Repository Structure](#21-repository-structure)
22. [Development Phases & Milestones](#22-development-phases--milestones)
23. [API Specifications](#23-api-specifications)
24. [Database Schemas](#24-database-schemas)
25. [Competitive Analysis](#25-competitive-analysis)
26. [Monetization & GTM Strategy](#26-monetization--gtm-strategy)
27. [Research Directions](#27-research-directions)
28. [Risk Register](#28-risk-register)

---

## 1. Executive Summary

### What CodeGraph Is Today

The current CodeGraph repository implements a static code visualizer:

- **Lexical + syntax parsing** for dependency extraction
- **Interactive D3-based graph** for dependency exploration
- **Search and highlighting** of nodes and edges
- **Node analytics** — fan-in/fan-out, massive object detection
- **CSV export** and basic graph metrics
- **Lightweight local install** — no database, no cloud dependency

This solves a real problem and has traction. The graph exploration UX is intuitive. The zero-infrastructure model lowers the barrier to entry.

### What CodeGraph v2 Must Become

Not a better code visualizer. A fundamentally different category of product:

> **A semantic, AI-native repository intelligence platform that serves as the memory layer for AI coding agents, engineering teams, and autonomous engineering workflows.**

The analogy:

| Current | Target |
|---|---|
| Street map (static picture) | GPS + routing engine (live, queryable, reasoned) |
| Code viewer | Code reasoner |
| Graph renderer | Intelligence platform |
| Dev tool | AI infrastructure |

### Why This Matters Now

The AI coding agent market is exploding — Cursor, Claude Code, Devin, OpenHands, Copilot. Every one of these tools shares a critical bottleneck:

> **They re-read entire repositories on every query, burning tokens, adding latency, and losing architectural context.**

CodeGraph v2 becomes the **persistent architectural memory layer** that agents query instead of re-reading code. This is infrastructure, not tooling — and infrastructure commands enterprise pricing and deep integrations.

---

## 2. Problem Statement

### 2.1 Current System Limitations — Deep Analysis

#### 2.1.1 Syntax-Only Understanding

The current parser extracts syntactic relationships — which file imports which file. It does not understand:

- **Semantic meaning** of functions (what they do, not just where they are)
- **Type flow** — what types travel between modules
- **Inheritance resolution** — which class actually implements which interface
- **Runtime behavior estimation** — which code paths are likely hot
- **Data flow** — how variables transform through the system
- **Side effects** — which functions mutate global state

Without semantic understanding, the graph is topologically accurate but intellectually shallow. You can see the edges but you can't answer "what happens when X changes?"

#### 2.1.2 Single-Language, Single-Paradigm Limitation

Most modern repositories are polyglot:

| Layer | Language |
|---|---|
| Frontend | TypeScript + React |
| API | Python FastAPI or Go |
| Infrastructure | Terraform + YAML |
| Database | SQL migrations |
| CI/CD | YAML + Bash |
| Containers | Dockerfile |
| Schema | GraphQL or Protobuf |
| Smart contracts | Solidity |

A Python-only graph misses 70%+ of what actually defines system behavior. Cross-language dependency resolution — e.g., a TypeScript component calling a Python REST endpoint defined in OpenAPI — requires a unified IR that transcends language boundaries.

#### 2.1.3 No Incremental Indexing

Full re-parse on every run is acceptable for a 10K LOC repository. At 500K LOC (a mid-size enterprise monorepo) it becomes unusable:

- A full Python parse of 500K LOC takes 45–120 seconds
- With AST + embeddings + graph writes, this scales to 10–30 minutes
- Teams need updates in <5 seconds on file save

Incremental indexing requires:
- **File content hashing** (SHA-256) to detect changes
- **Dependency invalidation** — when A changes, everything that imports A must be re-analyzed
- **Partial graph patching** — update only affected nodes and edges
- **Watch mode** — inotify/FSEvents-based file watching

#### 2.1.4 No AI-Native Architecture

The system cannot answer natural language questions. It cannot:

- Explain what a module does
- Summarize an architectural subsystem
- Find semantically similar code (not just textually similar)
- Predict what breaks if you delete a function
- Generate onboarding documentation

These capabilities require embeddings, vector search, and LLM integration — none of which exist today.

#### 2.1.5 Visualization-Only Output

The output is an HTML page with an interactive graph. This is useful for human exploration but not for:

- **AI agent consumption** — agents need structured JSON, not SVG
- **CI/CD integration** — pipelines need programmatic impact reports
- **IDE integration** — editors need real-time hover intelligence
- **Architecture compliance checks** — automated enforcement of layering rules

---

## 3. System Architecture

### 3.1 Full System Architecture Diagram

```
┌──────────────────────────────────────────────────────────┐
│                      INPUT LAYER                         │
│  Git Repos │ Local Files │ GitHub/GitLab APIs │ Archives │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│                  FILE DISCOVERY ENGINE                    │
│  Language Detection │ .gitignore filtering │ File hashing │
│  Incremental change detection │ Watch mode daemon        │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│               PARSING ORCHESTRATOR                        │
│  Language router │ Parser pool management                │
│  Parallel parse workers │ Error recovery                 │
├──────────┬──────────┬──────────┬──────────┬─────────────┤
│ Python   │TypeScript│  Go      │  Rust    │  Java/Kotlin │
│ Parser   │ Parser   │  Parser  │  Parser  │  Parser      │
│(tree-sit)│(babel/ts)│(go/ast)  │(ra)      │(javaparser)  │
└──────────┴──────────┴──────────┴──────────┴─────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│          UNIFIED INTERMEDIATE REPRESENTATION (UIR)       │
│    Language-agnostic entity model — all parsers emit    │
│    the same structure regardless of source language     │
└────────────────────────┬────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   Symbol Resolver   Call Graph     Data Flow
   Engine            Engine         Tracker
   (cross-file,       (static +     (mutation,
    inheritance,       dynamic       side-effect
    generics)          estimation)    analysis)
          └──────────────┼──────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│                 KNOWLEDGE GRAPH STORE                    │
│  Neo4j / Memgraph │ Nodes, edges, metadata, history     │
│  Versioned graph │ Temporal snapshots │ Graph queries   │
└────────────────────────┬────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   Embedding         Graph Query     Git Intelligence
   Pipeline          Engine          Engine
   (CodeBERT,        (Cypher/        (temporal graph,
    Voyage AI)        GraphQL DSL)    ownership map)
          └──────────────┼──────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│               AI INTELLIGENCE LAYER                      │
│  GraphRAG │ Semantic search │ Architecture inference    │
│  Repo memory │ Natural language querying │ Agents API  │
└────────────────────────┬────────────────────────────────┘
                         │
     ┌───────────────────┼───────────────────┐
     ▼                   ▼                   ▼
VSCode Extension    Agent APIs          Web Platform
JetBrains Plugin    (Cursor, Claude     (3D viz, AI
(IDE integrations)   Code, OpenHands)    overlay, time-travel)
```

### 3.2 Data Flow — Request Lifecycle

#### Query: "What breaks if I delete the `authenticate()` function?"

```
1. User submits natural language query to AI Query API
2. Query is embedded using the same embedding model as codebase
3. Vector search finds semantically related UIR nodes (top-K)
4. Graph traversal: MATCH (f:Function {name:"authenticate"})-[:CALLED_BY*1..5]->(caller)
5. Impact subgraph extracted — all direct + transitive callers
6. Architecture context fetched from repo memory
7. LLM receives: query + impact subgraph + architectural summary + dependency list
8. LLM generates: impact report with risk scores, affected systems, suggested migration path
9. Result returned as structured JSON + optional human-readable markdown
```

---

## 4. Unified Intermediate Representation

### 4.1 Design Goals

The UIR is the most critical design decision in the entire system. Every parser must emit the same structure. This enables:

- Cross-language dependency resolution
- Language-agnostic graph queries
- Uniform embedding pipeline
- Single AI reasoning layer

### 4.2 Full UIR Schema

```typescript
interface UIREntity {
  // Identity
  entity_id: string;          // globally unique: "function:src/auth/login.ts:authenticate"
  entity_type: UIREntityType; // "function" | "class" | "module" | "interface" | "variable" | "type"
  name: string;
  qualified_name: string;     // full dotted path: "auth.login.authenticate"

  // Location
  language: Language;
  file: string;               // relative path from repo root
  start_line: number;
  end_line: number;
  start_col: number;
  end_col: number;

  // Source
  raw_source: string;         // full source text of this entity
  docstring: string | null;   // extracted documentation comment
  signature: string | null;   // function/method signature only

  // Relationships
  dependencies: DependencyRef[];    // entities this one depends on
  dependents: DependencyRef[];      // entities that depend on this one
  calls: CallRef[];                 // direct function calls made
  called_by: CallRef[];             // direct callers
  imports: ImportRef[];             // import statements
  exports: ExportRef[];             // exported symbols
  inherits_from: string[];          // parent classes/interfaces
  implements: string[];             // interfaces implemented
  overrides: string[];              // parent methods overridden

  // Semantic
  complexity: number;               // cyclomatic complexity
  halstead_volume: number;          // code complexity metric
  maintainability_index: number;    // composite score
  has_side_effects: boolean;        // estimated from analysis
  is_pure: boolean;                 // deterministic, no side effects
  is_async: boolean;
  is_exported: boolean;
  is_deprecated: boolean;
  visibility: "public" | "private" | "protected" | "internal";

  // Type system
  return_type: TypeRef | null;
  parameter_types: TypedParam[];
  inferred_types: InferredTypeRef[];  // from type inference engine

  // AI metadata
  embedding_id: string | null;       // vector DB reference
  semantic_cluster: string | null;   // which architectural domain
  architectural_role: string | null; // "controller" | "service" | "repository" | etc.
  summary: string | null;            // AI-generated 1-sentence summary

  // Ownership & history
  git_blame: GitBlame | null;
  ownership: OwnershipRef | null;
  last_modified: string;            // ISO 8601
  change_frequency: number;         // commits per month (30-day rolling)
  bug_density: number;              // bug-related commits per LOC

  // Index metadata
  indexed_at: string;               // ISO 8601
  parser_version: string;
  hash: string;                     // SHA-256 of raw_source for incremental detection
}

interface DependencyRef {
  target_id: string;
  dependency_type: "import" | "call" | "inheritance" | "type_use" | "instantiation" | "decorator";
  is_dynamic: boolean;      // dynamic import / eval
  confidence: number;       // 0.0–1.0 for inferred deps
  line: number;
}

interface TypeRef {
  name: string;
  is_generic: boolean;
  generic_params: TypeRef[];
  is_nullable: boolean;
  is_array: boolean;
  origin: "declared" | "inferred" | "any";
  resolved_entity_id: string | null;
}

interface GitBlame {
  author: string;
  email: string;
  commit_hash: string;
  commit_date: string;
  commit_message: string;
}
```

### 4.3 UIR Versioning & Migration

UIR schema changes must be versioned. Every entity carries a `schema_version` field. The graph database maintains backward-compatible reads across minor versions and migration scripts for major version bumps.

```
UIR Version: MAJOR.MINOR.PATCH
MAJOR — breaking schema changes (requires re-index)
MINOR — additive fields (backward compatible)
PATCH — metadata corrections
```

---

## 5. Parsing & AST Engine

### 5.1 Parser Architecture

Each language parser is an isolated module implementing the `IParser` interface:

```typescript
interface IParser {
  language: Language;
  version: string;

  // Core methods
  parse(file: FileContent): ParseResult;
  extractEntities(ast: AST): UIREntity[];
  resolveLocalSymbols(entities: UIREntity[]): UIREntity[];
  extractImports(ast: AST): ImportRef[];
  extractExports(ast: AST): ExportRef[];

  // Incremental support
  canPatchAST(oldAST: AST, diff: FileDiff): boolean;
  patchAST(oldAST: AST, diff: FileDiff): AST;

  // Diagnostics
  validate(file: FileContent): ParseDiagnostic[];
}
```

### 5.2 Language-Specific Parser Implementations

#### Python Parser

**Library:** Tree-sitter Python grammar + custom semantic layer

```
Parsing stages:
1. Tree-sitter parse → CST (concrete syntax tree)
2. CST → AST simplification (remove whitespace nodes, comments)
3. Scope analysis — build scope chain (module → class → function)
4. Import resolution:
   - Absolute imports: "from fastapi import FastAPI"
   - Relative imports: "from .utils import helper"
   - Dynamic imports: __import__(), importlib.import_module()
5. Type annotation extraction — PEP 484/526/604 hints
6. Decorator resolution — @property, @classmethod, @dataclass fields
7. Docstring extraction — Google, NumPy, reStructuredText formats
8. Async detection — async def, await, asyncio patterns
9. Complexity scoring — McCabe metric via AST walk
```

**Challenges:**
- Python's dynamic nature means many type relationships are unresolvable statically
- Star imports (`from module import *`) require heuristic resolution
- Metaclasses and dynamic class creation (`type()`) are partially supported

#### TypeScript/JavaScript Parser

**Library:** TypeScript Compiler API (full type-checker access) + Babel for JSX

```
Parsing stages:
1. TypeScript Language Service initialization (tsconfig.json aware)
2. Full program creation — compiler resolves all files
3. Type checker access — resolves types at every node
4. Symbol resolution — compiler-backed, extremely accurate
5. Declaration file (.d.ts) parsing for external type info
6. JSX handling — React component dependency extraction
7. Module resolution — CommonJS, ESM, path aliases (tsconfig paths)
8. Decorator extraction — NestJS, Angular, TypeORM patterns
9. Generic instantiation tracking
```

**Advantage:** TypeScript's compiler gives the most accurate type information of any parser in the stack.

#### Go Parser

**Library:** `go/ast` + `go/types` packages (ships with Go toolchain)

```
Parsing stages:
1. go/parser.ParseFile — produces AST per file
2. go/types.Checker — full type checking with import resolution
3. Interface satisfaction checking — which structs implement which interfaces
4. Goroutine and channel detection — async flow mapping
5. Build tag handling — conditional compilation awareness
6. CGo detection and flagging (partially supported)
7. Module-aware import resolution (go.mod)
```

#### Rust Parser

**Library:** rust-analyzer's library crate (ra_ap_syntax)

```
Parsing stages:
1. SyntaxNode tree via ra_ap_syntax
2. Macro expansion (best-effort — full expansion requires full compilation)
3. Trait implementation tracking
4. Lifetime annotation extraction (metadata only)
5. Unsafe block detection and flagging
6. pub/pub(crate)/pub(super) visibility resolution
7. Cargo.toml dependency extraction
```

#### Solidity Parser (Strategic Priority)

**Library:** @solidity-parser/parser (JavaScript) or tree-sitter-solidity

```
Parsing stages:
1. Contract hierarchy — contract, interface, library, abstract
2. Inheritance linearization (C3 algorithm)
3. Function selector computation (keccak256 of signature)
4. Event and error extraction
5. Modifier dependency graph
6. State variable access patterns (read/write per function)
7. Reentrancy risk flags (state change after external call)
8. Gas cost estimation (storage writes, external calls)
9. ABI extraction
10. OpenZeppelin pattern detection (ERC20, ERC721, Ownable, etc.)
```

**Solidity is a strategic differentiator.** No existing tool does AI-native smart contract architecture intelligence. This is a viable standalone product within the platform.

### 5.3 Incremental Indexing Engine

```
File Change Event (inotify/FSEvents/chokidar)
         │
         ▼
Hash changed? ──No──→ Skip
         │
        Yes
         ▼
Mark file as dirty
         │
         ▼
Compute dependency invalidation set
(all files that depend on changed file)
         │
         ▼
Topological sort of dirty set
         │
         ▼
Parse dirty files in dependency order
         │
         ▼
Patch graph: remove old nodes/edges, insert new ones
         │
         ▼
Re-embed changed entities
         │
         ▼
Notify subscribed clients (SSE / WebSocket)
         │
         ▼
Update repo memory summaries (async)
```

**Performance targets:**
- Single file change: <500ms graph update, <2s embedding update
- 10-file change: <2s graph update, <10s embedding update
- 100-file change: <30s full differential update

### 5.4 Error Recovery Strategy

Parsers must not fail on invalid code. Real codebases have:
- Syntax errors in WIP files
- Partial files mid-edit
- Generated files with unusual patterns

```
Recovery strategy:
1. Tree-sitter: error nodes are first-class — continue parsing around errors
2. TypeScript: use `skipLibCheck: true`, `noEmitOnError: false`
3. Any parser: if top-level parse fails, attempt line-by-line recovery
4. Mark entities from errored files with `parse_error: true`
5. Include partial results — a file with 1 error still yields 90% of entities
6. Log structured error reports for debugging
```

---

## 6. Symbol Resolution & Type Inference

### 6.1 Symbol Resolution Engine

The most technically challenging component. Symbol resolution answers: **"When this code references X, which actual entity is X?"**

```
Resolution pipeline:

1. Local scope resolution
   ├── Function-local variables
   ├── Class members
   └── Module-level names

2. Import resolution
   ├── Explicit imports: match import statement to file/package
   ├── Re-exports: follow through barrel files (index.ts)
   ├── Aliased imports: "import { foo as bar } from './baz'"
   └── Namespace imports: "import * as utils from './utils'"

3. Cross-file resolution
   ├── Build import graph per language
   ├── Topological traversal
   └── Cache resolved symbols

4. External library resolution
   ├── Type stubs (.pyi, .d.ts)
   ├── Parsed node_modules (selective)
   └── Known library signatures (pre-built database)

5. Inheritance resolution
   ├── MRO (Python C3 linearization)
   ├── Interface implementation (TypeScript, Java, Go)
   ├── Mixin resolution
   └── Abstract method tracking

6. Confidence scoring
   Every resolved symbol gets a confidence score:
   1.0 = compiler-confirmed
   0.9 = strong static inference
   0.7 = heuristic match
   0.5 = best guess
   0.0 = unresolvable (flagged as unknown)
```

### 6.2 Type Inference Engine

For languages without explicit type annotations (JavaScript, Python without hints), the type inference engine estimates types:

```
Inference strategies:

1. Return type inference
   - Trace return statements
   - Union inferred types across all branches
   Example: if both branches return string → infer string

2. Parameter type inference
   - Analyze all call sites of the function
   - Infer from how arguments are used inside function
   - Apply constraint propagation

3. Variable type narrowing
   - Track typeof, instanceof checks
   - Narrow union types after guards

4. Propagation
   - If f() returns result of g(), and g() returns string → f() returns string

5. Library type stubs
   - NumPy, pandas, React, Express have known type signatures
   - Apply from pre-built stub database

Output: TypeRef with origin="inferred" and confidence score
```

---

## 7. Call Graph & Data Flow Engine

### 7.1 Static Call Graph

```
For each function F:
  1. Find all call expressions in F's body
  2. Resolve callee via symbol resolution engine
  3. Add edge: F --[CALLS]--> callee
  4. Handle:
     - Method calls on known objects: obj.method()
     - First-class function calls: callbacks, stored refs
     - Indirect calls via dict/map dispatch (heuristic)
     - Recursive calls (self-loops)
     - Mutual recursion (cycles — detect and annotate)
```

### 7.2 Probable Dynamic Call Graph

Static analysis misses dynamic dispatch. The dynamic call graph adds:

```
Patterns detected and resolved:

1. Dictionary/map dispatch
   handlers = {"login": login_handler, "logout": logout_handler}
   handlers[action]()  → probable calls: login_handler, logout_handler

2. Event system dispatch
   emitter.on("click", handler)  → probable call edge on "click" event

3. Dependency injection containers
   (Spring, NestJS, FastAPI dependency injection)
   → Resolve injected types from DI metadata

4. Plugin systems
   → Flag as "dynamic, unresolvable" with caller site annotated

5. React component rendering
   <MyComponent />  → probable call: MyComponent render

Each dynamic edge annotated with:
- is_dynamic: true
- confidence: 0.3–0.7 depending on pattern
- pattern: "event_dispatch" | "dict_dispatch" | "di_container" | "plugin"
```

### 7.3 Data Flow Tracker

Tracks how data moves through the system:

```
Tracked flows:

1. Parameter flow
   - Value passed from caller → parameter in callee
   - Annotated with position and type

2. Return flow
   - Value returned from callee → captured in caller

3. Mutation tracking
   - Side effects on mutable objects passed as arguments
   - Global state mutations

4. I/O flows
   - Data read from: files, databases, HTTP, env vars
   - Data written to: files, databases, HTTP responses, logs

5. Secret/PII flow (security feature)
   - Track variables named: password, token, key, secret, credential
   - Flag flows that reach: logging, HTTP response, storage

Output: DataFlowGraph overlaid on the main UIR graph
Use cases: GDPR compliance mapping, security audit, debugging
```

---

## 8. Knowledge Graph Layer

### 8.1 Graph Database Design

**Primary store:** Neo4j Community / Enterprise (or Memgraph for lower infrastructure cost)

#### Node Types

```cypher
// Core entity nodes
(:Function {entity_id, name, file, complexity, ...})
(:Class {entity_id, name, file, is_abstract, ...})
(:Module {entity_id, name, file, language, ...})
(:Interface {entity_id, name, file, ...})
(:Variable {entity_id, name, type, is_global, ...})
(:TypeAlias {entity_id, name, ...})

// Repository structure nodes
(:Repository {id, name, url, default_branch, ...})
(:Package {id, name, version, language, ...})
(:File {path, hash, language, loc, ...})
(:Directory {path, ...})

// Git nodes
(:Commit {hash, message, author, date, ...})
(:Author {email, name, ...})

// Architecture nodes
(:ArchDomain {name, description, ...})
(:Subsystem {name, ...})
(:Layer {name, order, ...})
```

#### Relationship Types

```cypher
// Code relationships
(f:Function)-[:CALLS {line, confidence, is_dynamic}]->(g:Function)
(f:Function)-[:CALLED_BY]->(g:Function)
(c:Class)-[:INHERITS_FROM]->(parent:Class)
(c:Class)-[:IMPLEMENTS]->(i:Interface)
(m:Module)-[:IMPORTS {alias, is_star}]->(m2:Module)
(f:Function)-[:DEFINED_IN]->(file:File)
(f:Function)-[:BELONGS_TO]->(c:Class)
(f:Function)-[:USES_TYPE]->(t:TypeAlias)
(f:Function)-[:READS]->(v:Variable)
(f:Function)-[:WRITES]->(v:Variable)

// Architecture relationships
(f:Function)-[:PART_OF]->(domain:ArchDomain)
(m:Module)-[:IN_LAYER]->(l:Layer)
(l:Layer)-[:DEPENDS_ON {is_violation}]->(l2:Layer)

// Git relationships
(commit:Commit)-[:MODIFIES]->(file:File)
(author:Author)-[:AUTHORED]->(commit:Commit)
(f:Function)-[:LAST_MODIFIED_BY]->(commit:Commit)
(author:Author)-[:OWNS]->(domain:ArchDomain)
```

### 8.2 Graph Query Language Support

#### Cypher (Primary)

```cypher
-- Find all functions called by authenticate with their files
MATCH (auth:Function {name: "authenticate"})-[:CALLS*1..3]->(dep:Function)
RETURN dep.name, dep.file, dep.complexity
ORDER BY dep.complexity DESC

-- Find circular dependencies between modules
MATCH path = (m:Module)-[:IMPORTS*2..10]->(m)
RETURN path

-- Find god objects (classes with too many responsibilities)
MATCH (c:Class)-[:HAS_METHOD]->(f:Function)
WITH c, count(f) AS method_count
WHERE method_count > 20
RETURN c.name, c.file, method_count ORDER BY method_count DESC

-- Change blast radius: what's affected if we delete a function?
MATCH (f:Function {name: $target})<-[:CALLS*]-(caller:Function)
RETURN caller.name, caller.file, length(path) AS distance
```

#### Custom GraphQL DSL (for API consumers)

```graphql
query ImpactAnalysis($functionName: String!) {
  function(name: $functionName) {
    name
    file
    callers(depth: 5) {
      name
      file
      module { name domain }
    }
    callees {
      name
      isExported
      complexity
    }
    riskScore
    architecturalDomain
  }
}
```

### 8.3 Graph Versioning — Temporal Graph

Every graph mutation is recorded as an event:

```typescript
interface GraphEvent {
  event_id: string;
  event_type: "node_added" | "node_updated" | "node_removed" | "edge_added" | "edge_removed";
  entity_id: string;
  timestamp: string;         // ISO 8601
  commit_hash: string;       // git commit that caused this
  old_value: Partial<UIREntity> | null;
  new_value: Partial<UIREntity> | null;
}
```

This enables:
- **Architecture time-travel** — replay graph state at any commit
- **Change velocity metrics** — how fast is the graph evolving?
- **Stability scoring** — which modules are stable vs volatile?

---

## 9. AI & Semantic Intelligence Layer

### 9.1 Embedding Pipeline

#### What Gets Embedded

Every UIR entity is embedded. The embedding captures semantic meaning beyond text:

| Entity Type | Input to Embedding Model |
|---|---|
| Function | signature + docstring + body + caller context |
| Class | name + docstring + method signatures + field types |
| Module | name + description + exported API surface |
| Commit | message + changed entity names |
| Architectural domain | description + contained entity names |

#### Embedding Model Strategy

```
Tier 1 (default, local): 
  - CodeBERT (microsoft/codebert-base) — 768d, multilingual code
  - GraphCodeBERT — structure-aware variant
  - Qwen2.5-Coder embeddings — state-of-art code understanding

Tier 2 (cloud, higher quality):
  - Voyage AI voyage-code-2 — purpose-built for code
  - OpenAI text-embedding-3-large — strong general + code
  - Jina v3 — long-context, good for file-level embeddings

Selection logic:
  - Local model: developer environments, air-gapped enterprise
  - Cloud model: SaaS tier, where latency/quality tradeoff favors API
  - Hybrid: embed locally, re-embed with cloud model on semantic search miss
```

#### Embedding Storage

```
Primary: Qdrant
  - Collections: one per embedding model (for A/B testing)
  - Payload: entity_id, entity_type, language, file, semantic_cluster
  - Indexing: HNSW with m=16, ef_construction=100

Backup/fallback: pgvector (PostgreSQL extension)
  - Simpler ops, good for smaller deployments (<1M entities)
  - SQL-native, easy joins with relational metadata
```

### 9.2 Semantic Search Engine

```
Query: "Where is the payment retry logic?"

Steps:
1. Embed query: "payment retry logic" → query_vector
2. Vector search in Qdrant → top-50 similar entities
3. Re-rank using:
   a. BM25 lexical score (does "retry" appear literally?)
   b. Graph centrality (is this entity well-connected?)
   c. Recency (recently modified entities ranked up)
   d. Cross-encoder re-ranking (finer semantic judgment)
4. Return top-10 with:
   - Entity name, file, snippet
   - Why matched (semantic reason)
   - Related entities (graph neighborhood)
   - Confidence score
```

### 9.3 Semantic Chunking

For feeding codebase context to LLMs, naive chunking (split every N tokens) loses architectural context. Graph-aware chunking:

```
Chunking strategies:

1. Entity-level chunks
   Each function/class is one chunk (natural boundary)
   Size: 200–800 tokens typically

2. Dependency-aware chunks
   Core entity + its direct dependencies in one chunk
   Preserves call context for reasoning

3. Subsystem chunks
   All entities in an architectural domain as one summary chunk
   Used for high-level architectural queries

4. Hot path chunks
   Most frequently traversed call paths as contiguous chunks
   Optimized for "explain this workflow" queries

5. Diff chunks
   Changed entities + their context (for PR review agents)

Chunk metadata:
  - entity_ids contained
  - architectural domain
  - token count
  - dependency edges within chunk vs cross-chunk
```

---

## 10. GraphRAG Engine

### 10.1 Architecture

GraphRAG (Graph-augmented Retrieval Augmented Generation) combines graph traversal with vector retrieval:

```
Query: "How does authentication work in this system?"

Step 1 — Intent classification
  Model: lightweight classifier (DistilBERT)
  Classes: architectural_query | entity_lookup | impact_query | explanation_query
  Result: architectural_query

Step 2 — Entity extraction from query
  NER pass: extract "authentication" as domain keyword
  Graph lookup: find :ArchDomain {name CONTAINS "auth"}
  Found: auth_domain node

Step 3 — Subgraph retrieval
  MATCH (d:ArchDomain {id: "auth_domain"})<-[:PART_OF]-(f:Function)
  WITH f MATCH (f)-[:CALLS*0..2]-(related)
  Returns: ~40 entities

Step 4 — Vector search augmentation
  Query embedding search returns additional 20 entities not in subgraph
  Merge: deduplicate, score by (graph_centrality * semantic_similarity)

Step 5 — Context assembly
  Select top-15 entities by combined score
  Assemble: entity signatures + docstrings + key relationships
  Total tokens: ~3000 (fits in context)

Step 6 — LLM generation
  System: "You are a code architecture expert. Given the following code entities..."
  User: "How does authentication work?"
  Context: [assembled entities]

Step 7 — Response
  Structured answer with:
  - Prose explanation
  - Numbered entity references (linkable in UI)
  - Architecture diagram description (rendered in UI)
  - Follow-up questions suggested
```

### 10.2 AI Repository Memory

The repo memory system persists high-level architectural understanding so agents don't need to re-analyze from scratch:

```typescript
interface RepoMemory {
  repo_id: string;
  generated_at: string;
  commit_hash: string;           // memory is pinned to a commit

  // Architectural summary
  overview: string;              // 2-3 paragraph system description
  tech_stack: TechStackSummary;
  architecture_pattern: string;  // "microservices" | "monolith" | "event-driven" | ...

  // Subsystems
  subsystems: SubsystemMemory[];

  // Cross-cutting concerns
  auth_flow: string;            // narrative description
  data_flow: string;
  error_handling_patterns: string;
  testing_approach: string;

  // Risk signals
  fragile_modules: FragileModule[];
  circular_dependencies: CircularDep[];
  dead_code_zones: string[];

  // Statistics
  total_entities: number;
  total_files: number;
  languages: Record<Language, LanguageStats>;
  avg_complexity: number;
  test_coverage_estimate: number | null;
}

interface SubsystemMemory {
  name: string;
  description: string;           // AI-generated narrative
  files: string[];
  key_entities: string[];        // entity_ids of most important entities
  public_api: string[];          // exported interfaces this subsystem exposes
  dependencies: string[];        // which other subsystems it depends on
  owner: string | null;          // primary author by git blame
  stability_score: number;       // 0.0–1.0 (how much does it change?)
  complexity_score: number;
}
```

**Memory regeneration triggers:**
- Major structural change (>10% of entities changed in a commit)
- Explicit user request
- Scheduled: weekly regardless of changes
- New subsystem detected (new top-level directory with many files)

---

## 11. Multi-Language Support

### 11.1 Language Support Matrix

| Language | Parser | Type Inference | Symbols | Call Graph | Data Flow | Priority |
|---|---|---|---|---|---|---|
| Python | Tree-sitter + stdlib ast | Partial (hints + inference) | High | High | Medium | Tier 1 |
| TypeScript | TS Compiler API | Full | Full | Full | Medium | Tier 1 |
| JavaScript | Babel | Heuristic | High | High | Low | Tier 1 |
| Java | JavaParser + Eclipse JDT | Full | Full | Full | Medium | Tier 1 |
| Go | go/ast + go/types | Full | Full | Full | Medium | Tier 1 |
| Rust | rust-analyzer | Full | Full | Full | Medium | Tier 1 |
| Kotlin | Kotlin compiler plugin | Full | Full | Full | Medium | Tier 2 |
| C# | Roslyn | Full | Full | Full | Medium | Tier 2 |
| C++ | Clang LibTooling | Partial | Medium | Medium | Low | Tier 2 |
| PHP | PHP-Parser | Partial | Medium | Medium | Low | Tier 2 |
| Solidity | tree-sitter-solidity | Full | Full | Full | High | **Tier 1*** |
| SQL | sqlfluff | Schema only | Low | N/A | Low | Tier 3 |
| Terraform | HCL parser | N/A | Low | N/A | N/A | Tier 3 |
| GraphQL | graphql-js | Full | High | N/A | Low | Tier 3 |
| Dockerfile | Custom | N/A | Low | N/A | N/A | Tier 3 |
| YAML | js-yaml | N/A | Config only | N/A | N/A | Tier 3 |
| Protobuf | protoc | Schema | High | N/A | N/A | Tier 3 |

*Solidity elevated to Tier 1 for strategic reasons — smart contract audit market is high-value.

### 11.2 Cross-Language Dependency Resolution

```
Example: TypeScript frontend calls Python backend

TypeScript side:
  const response = await fetch('/api/users/authenticate', { method: 'POST', ... })
  → Detected: HTTP call to "/api/users/authenticate"

Python side (FastAPI):
  @app.post("/api/users/authenticate")
  async def authenticate(credentials: AuthRequest) -> AuthResponse:
  → Detected: REST endpoint "/api/users/authenticate" POST

Cross-language linker:
  Pattern match: HTTP client URL == server route path
  Create cross-language dependency edge:
    (ts:FetchCall)-[:HTTP_CALLS {method:"POST", path:"/api/users/authenticate"}]->(py:Function {name:"authenticate"})

Other cross-language patterns resolved:
  - GraphQL query → resolver function
  - Protobuf message → gRPC service handler
  - SQL table name → ORM model class
  - Event name (pub/sub) → event handler registrations
  - Environment variable → consuming code (across languages)
```

---

## 12. Architecture Intelligence

### 12.1 Architecture Pattern Detection

```
Patterns detected automatically:

MVC / MVVM
  Signals: Controllers import models, views import controllers
  Files: controllers/, models/, views/ or similar naming
  Confidence boosters: ORM usage, template rendering calls

Layered Architecture (N-tier)
  Signals: Strict import directionality (UI → Service → Repository → DB)
  Validation: check for layer violations (UI importing Repository directly)

Microservices
  Signals: Multiple top-level services, HTTP client calls between them,
           docker-compose.yml with multiple services, separate package.json per service
  Dependencies: mapped via HTTP cross-language links

Event-Driven
  Signals: Event emitters, message bus clients (Kafka, RabbitMQ, Redis pubsub)
  Graph: Events as first-class nodes connected to publishers/subscribers

Hexagonal (Ports & Adapters)
  Signals: "ports" and "adapters" naming, interface-first design,
           dependency inversion (domain doesn't import infrastructure)

Clean Architecture
  Signals: Entities → Use Cases → Interface Adapters → Frameworks & Drivers
           dependency rule compliance, domain purity

CQRS
  Signals: Separate command and query handlers, command bus, query bus

Repository Pattern
  Signals: Classes named *Repository, CRUD method signatures, data access abstraction
```

### 12.2 Anti-Pattern Detection Engine

```
Anti-patterns with detection logic and severity:

1. God Object / God Class [CRITICAL]
   Condition: class has >15 methods OR >30 fields OR >500 LOC
   OR: class is imported by >25% of other modules
   Action: Flag, suggest decomposition boundaries via clustering

2. Circular Dependencies [HIGH]
   Condition: cycle detected in import graph (DFS with cycle detection)
   Types: direct (A→B→A), indirect (A→B→C→A)
   Action: Report all cycles, suggest break points (lowest traffic edge)

3. Feature Envy [MEDIUM]
   Condition: function accesses more data from another class than its own
   Detection: count attribute accesses by class origin
   Action: Suggest moving function to the class it envies

4. Shotgun Surgery [HIGH]
   Condition: a single type of change requires modifying >5 different modules
   Detection: via git history — commits that touch many files
   Action: Flag high-coupling zones

5. Inappropriate Intimacy [MEDIUM]
   Condition: two classes reference each other's private/internal members
   Detection: access to underscore-prefixed or non-exported symbols

6. Law of Demeter Violations [LOW]
   Condition: a.b.c.d() call chains (depth >2)
   Detection: member access chain analysis

7. Layer Violations [HIGH]
   Condition: entity in high layer imports entity in lower layer
   Example: UI module imports Database module directly
   Detection: declared layer assignments + import graph check

8. Dead Code [MEDIUM]
   Condition: function/class never called/instantiated, not exported
   Exceptions: test files, main entry points, event handlers (marked dynamic)
   Detection: reverse call graph from all entry points

9. Excessive Coupling [HIGH]
   Condition: module has >20 direct import dependencies
   Detection: fan-out per module node

10. Architecture Drift [HIGH]
    Condition: current architecture deviates from declared architecture rules
    (rules defined in codegraph.config.yml)
```

### 12.3 Risk Scoring Engine

Every module and entity receives a composite risk score:

```
Risk Score = weighted sum of:

  change_frequency     × 0.25   (frequently changed = more risk)
  bug_commit_rate      × 0.25   (commits with "fix", "bug" in message)
  cyclomatic_complexity× 0.20   (high complexity = harder to maintain)
  coupling_score       × 0.15   (highly coupled = blast radius)
  test_coverage        × -0.15  (higher coverage = lower risk, if available)

Score: 0.0 (very low risk) to 1.0 (critical risk)

Additional risk flags:
  - "fragile" flag: risk > 0.8 AND change_frequency > 5/month
  - "blast radius" score: count of transitive dependents
  - "stability index" = 1 - (efferent_coupling / (afferent + efferent))
```

---

## 13. Git Intelligence & Temporal Graph

### 13.1 Git Integration

```
Data extracted from git:

Per commit:
  - Hash, message, author, date, files changed
  - Classify: feat/fix/refactor/docs/test (conventional commits OR classifier)
  - Classify: bug-related? (heuristic on "fix", "bug", "error", "crash")

Per file over history:
  - Change frequency (commits per month)
  - Churn rate (lines added + deleted per commit)
  - Bug fix rate (% of commits that are bug fixes)
  - Author count (bus factor signal)

Per function (git blame enhanced):
  - Last modified commit + author
  - Age (days since first introduction)
  - Number of times modified

Implementation:
  - libgit2 bindings (pygit2 for Python, git2go for Go)
  - Incremental: only process commits since last indexed commit
  - Blame: run `git blame -p` per file, map line ranges to entities
```

### 13.2 Ownership Mapping

```
Ownership inference algorithm:

1. For each entity E, get git blame → set of (author, commit_count) pairs
2. Primary owner = author with most commits touching E
3. Secondary owners = authors with >15% of commits
4. Domain ownership = majority owner across all entities in domain

Output:
  - Per-entity: primary_owner, secondary_owners
  - Per-domain: domain_lead, contributors
  - Bus factor alert: entities with only 1 author who hasn't committed in >6 months

Use cases:
  - Code review routing (auto-suggest reviewer = owner)
  - Impact notification ("you own auth module, this PR changes its deps")
  - Expertise mapping (who to ask about X)
```

### 13.3 Architecture Evolution Tracking

```
Evolution events tracked:

- New entity added
- Entity deleted
- Entity moved (same content, different file)
- Large entity split into multiple
- Multiple entities merged
- Dependency added/removed between entities
- Layer assignment changed
- New architectural domain emerged
- Subsystem coupling increased/decreased

Visualizable as:
  - Architecture changelog (textual)
  - Time-lapse graph animation (frontend feature)
  - Coupling trend chart (is architecture improving or degrading?)
  - Author contribution evolution
```

---

## 14. AI Agent APIs

### 14.1 REST API Specification

Base URL: `https://api.codegraph.dev/v1`

#### Context Retrieval API (Primary — for AI agents)

```http
POST /context
Authorization: Bearer {api_key}
Content-Type: application/json

{
  "query": "authentication flow",
  "repo_id": "github:org/repo",
  "strategy": "graphrag",          // "graphrag" | "semantic" | "graph_only"
  "depth": 3,                       // graph traversal depth
  "max_entities": 20,
  "include_source": true,
  "include_graph": false,
  "commit": "HEAD"                  // pin to specific commit
}

Response 200:
{
  "query": "authentication flow",
  "strategy_used": "graphrag",
  "entities": [
    {
      "entity_id": "function:src/auth/login.ts:authenticate",
      "name": "authenticate",
      "file": "src/auth/login.ts",
      "signature": "async function authenticate(credentials: Credentials): Promise<Session>",
      "docstring": "Validates user credentials and creates a session",
      "source": "...",
      "relevance_score": 0.94,
      "relevance_reason": "semantic match: authentication + function signature",
      "dependencies": ["validateCredentials", "createSession", "hashPassword"],
      "callers": ["loginController", "refreshToken"]
    }
  ],
  "architectural_context": "Authentication is handled by the auth subsystem (src/auth/). ...",
  "token_count": 2847,
  "graph_edges": [],
  "repo_memory_used": true,
  "latency_ms": 340
}
```

#### Impact Analysis API

```http
POST /impact
{
  "target": "function:src/auth/login.ts:authenticate",
  "change_type": "delete",           // "delete" | "signature_change" | "behavior_change"
  "depth": 5
}

Response:
{
  "target": {...},
  "direct_callers": [...],
  "transitive_callers": [...],
  "affected_modules": ["auth", "api", "middleware"],
  "risk_score": 0.87,
  "risk_reasons": ["Called by 3 critical path functions", "No alternative implementation"],
  "suggested_actions": [
    "Create adapter function before deletion",
    "Add deprecation warning",
    "Notify owners: alice@org.com (auth), bob@org.com (api)"
  ],
  "test_coverage_of_callers": 0.62
}
```

#### Semantic Search API

```http
GET /search?q=payment+retry+logic&repo_id=github:org/repo&limit=10

Response:
{
  "results": [
    {
      "entity_id": "...",
      "name": "retryPayment",
      "file": "src/billing/retry.ts",
      "match_score": 0.91,
      "match_type": "semantic",
      "snippet": "..."
    }
  ]
}
```

#### Architecture Query API

```http
POST /query
{
  "cypher": "MATCH (c:Class)-[:INHERITS_FROM*]->(base:Class {name: 'BaseRepository'}) RETURN c",
  "repo_id": "github:org/repo"
}
```

#### Repository Memory API

```http
GET /memory/{repo_id}
GET /memory/{repo_id}/subsystems
GET /memory/{repo_id}/subsystem/{subsystem_name}
POST /memory/{repo_id}/regenerate
```

### 14.2 Agent Integration Guides

#### Claude Code Integration

```typescript
// In Claude Code MCP server configuration
{
  "tools": [{
    "name": "codegraph_context",
    "description": "Query repository architecture and get relevant code context",
    "input_schema": {
      "type": "object",
      "properties": {
        "query": { "type": "string" },
        "depth": { "type": "number", "default": 3 }
      }
    }
  }]
}

// Usage in Claude Code system prompt
// "Before reading files, query codegraph_context to understand
//  the architectural context. This reduces unnecessary file reads."
```

#### Cursor Integration

```json
// .cursorrules
{
  "context_provider": {
    "type": "codegraph",
    "endpoint": "https://api.codegraph.dev/v1/context",
    "api_key": "${CODEGRAPH_API_KEY}",
    "auto_query_on": ["edit", "navigation", "refactor"]
  }
}
```

---

## 15. IDE Integrations

### 15.1 VSCode Extension

**Extension ID:** `codegraph.codegraph-intelligence`

#### Features

```
Sidebar: Architecture Explorer
  - Repository structure tree with entity counts
  - Architectural domains with health scores
  - Anti-pattern alerts with file links

Hover Intelligence:
  - Function: signature, callers count, callees count, complexity, owner
  - Class: method count, inheritance chain, implements
  - Module: entity count, coupling score, domain

Inline Decorations:
  - Risk score indicator (colored dot in gutter)
  - Owner initials in gutter
  - Complexity warning on high-complexity functions

Impact Preview (before save):
  - "This change affects 12 functions across 4 modules"
  - Expandable list of affected callers
  - Risk score delta

Semantic Search (Cmd+Shift+F enhanced):
  - Natural language search powered by GraphRAG
  - Results show architecture context

Command Palette:
  - "CodeGraph: Show Impact Analysis"
  - "CodeGraph: Find Similar Functions"
  - "CodeGraph: Explain This Module"
  - "CodeGraph: Show Architecture Diagram"
  - "CodeGraph: Detect Anti-Patterns"
```

#### Extension Architecture

```
VSCode Extension (TypeScript)
  ├── Language Client → CodeGraph Language Server (LSP)
  ├── Webview Panel → React app (architecture diagram)
  ├── Tree Data Provider → sidebar explorer
  └── Inline Decoration Provider → gutter indicators

CodeGraph Language Server
  ├── Connects to local CodeGraph daemon (running in repo)
  ├── OR connects to CodeGraph cloud API
  ├── Provides: hover, definition, references, diagnostics
  └── Streams: real-time graph updates via SSE
```

### 15.2 JetBrains Plugin

**Plugin ID:** `dev.codegraph.intellij`

Targeting: IntelliJ IDEA, PyCharm, WebStorm, GoLand, Rider

Same feature set as VSCode but implemented via IntelliJ Platform SDK:
- Tool Window (equivalent to Sidebar)
- Editor extensions for hover
- Inspection profiles for anti-pattern detection
- Structural Search integration

---

## 16. Frontend Visualization

### 16.1 Rendering Stack

| Scale | Renderer | Use Case |
|---|---|---|
| <1,000 nodes | D3.js (current) | Small repos, detailed labels |
| 1K–100K nodes | Sigma.js + WebGL | Medium repos, interactive |
| >100K nodes | Three.js force-directed | Large monorepos, overview |
| Architecture view | Cytoscape.js | Hierarchical, structured |

### 16.2 View Modes

```
1. File/Module Graph (current, enhanced)
   - Nodes = files/modules
   - Edges = imports
   - Clustering by directory
   - Color by language, domain, or risk score

2. Entity Graph
   - Nodes = functions/classes
   - Edges = calls/inheritance
   - Filter by: file, domain, complexity, owner

3. Domain View (new — most useful for large repos)
   - Nodes = architectural domains (clusters)
   - Edges = domain-level dependencies
   - Expandable: click domain to drill into entities
   - Overlay: coupling strength as edge thickness

4. Layer View
   - Horizontal layers: UI → Services → Repositories → Infrastructure
   - Nodes positioned by layer assignment
   - Violations highlighted (edges going "up" the stack)

5. Time-Travel View
   - Slider: move through commit history
   - Graph animates to show evolution
   - Added nodes: green flash
   - Removed nodes: red fade
   - Modified edges: animation

6. Risk Heatmap View
   - Same topology as graph
   - Node color: green (low risk) → red (critical risk)
   - Edge color: coupling strength
   - Overlay: show bug-prone clusters
```

### 16.3 AI Overlay System

```
Toggleable overlays:

Complexity Heatmap
  Node color gradient: green (low) → yellow → red (complex)

Ownership Overlay
  Nodes colored by primary author/team
  Shows ownership boundaries visually

Change Velocity
  Node size proportional to commits/month
  Highly churning modules visually prominent

Bug Density
  Color intensity by bug-fix commit rate
  Identify historically buggy modules

Semantic Clusters
  Auto-clustered by embedding similarity
  Reveals functional groupings that don't match directory structure

Dead Code
  Grayed-out unreachable nodes
  Helps identify cleanup targets
```

### 16.4 AI Chat Panel

In-app chat powered by GraphRAG:

```
Interface:
  - Chat input + response area (right sidebar)
  - Graph highlights entities mentioned in response
  - Clickable entity references jump to graph/code
  - Suggested follow-up questions
  - Export conversation as architecture decision record (ADR)

Example interactions:
  "Explain how the payment system works"
  → Graph highlights payment entities, prose explanation

  "What would break if I remove the UserCache class?"
  → Impact visualization, risk report

  "Show me all code that touches the database"
  → Graph filters to data access layer entities

  "Who owns the authentication subsystem?"
  → Shows ownership breakdown, suggests reviewers
```

---

## 17. Scaling & Infrastructure

### 17.1 Indexing Pipeline at Scale

```
Small repo (<50K LOC):
  Single process, in-memory graph build, <60 seconds full index

Medium repo (50K–500K LOC):
  Parallel parse workers (N workers = N CPU cores)
  Graph batch writes
  5–15 minutes full index

Large repo (500K–5M LOC — monorepo):
  Distributed parse workers (Celery or Ray)
  Kafka/Redis Streams for work distribution
  Graph DB with read replicas
  30–120 minutes full index
  Incremental: <5 seconds per file change

Architecture:
  ┌─────────────┐
  │ Coordinator │ — receives file change events, assigns work
  └──────┬──────┘
         │ Kafka topic: "parse_jobs"
    ┌────┴────┐
    ▼         ▼
  Worker    Worker    ... (auto-scaling pool)
  Parse → UIR → Graph write → Embed queue

  Embed queue → Embedding worker → Qdrant write
```

### 17.2 Caching Strategy

```
L1 Cache — In-process (LRU, per worker)
  - AST cache: file_hash → AST (avoid re-parse of unchanged files)
  - Symbol resolution cache: symbol_name → entity_id
  - Query result cache: query_hash → result (TTL: 30s)

L2 Cache — Redis
  - File hash → parse result (UIR JSON)
  - Embedding cache: entity_hash → embedding vector
  - Repo memory: repo_id → memory JSON (TTL: 1hr or commit-invalidated)
  - API response cache: request_hash → response (TTL: 60s)

L3 Cache — File system
  - Serialized ASTs (for cold start acceleration)
  - Snapshot graph exports
```

### 17.3 Monorepo-Specific Features

```
Lazy graph loading:
  - Load only requested subgraph, not full repo
  - Depth-limited queries with pagination
  - "Focus mode": user selects a service, only that subtree loaded in UI

Graph partitioning:
  - Partition by top-level service/package
  - Cross-partition edges tracked separately
  - Each partition independently queryable

Selective indexing:
  - codegraph.config.yml: include/exclude paths
  - Priority list: critical services indexed first
  - Background indexing: non-critical services indexed in background
```

---

## 18. Security & Enterprise Features

### 18.1 Secret & Credential Detection

```
Detection patterns:
  - API key patterns: /[A-Z0-9]{20,}/  in string literals
  - Private key blocks: BEGIN RSA PRIVATE KEY
  - Connection strings: postgres://, mongodb://, redis://
  - JWT secrets in code
  - AWS/GCP/Azure credential patterns
  - Hardcoded passwords (variable named "password" with string value)
  - Environment variable assignments with sensitive names in non-.env files

Severity levels:
  CRITICAL: Private keys, hardcoded passwords
  HIGH: API keys in source code
  MEDIUM: Connection strings without env var indirection
  LOW: Suspicious variable names

Output: Security report in API response and UI overlay
Integration: Pre-commit hook, CI/CD check, PR comment
```

### 18.2 PII & GDPR Flow Mapping

```
PII detection:
  - Variables/parameters named: email, phone, address, dob, ssn, national_id
  - Data models with PII fields (User, Customer, Patient)
  - Annotated via @pii, @sensitive decorators (configurable)

Flow tracking (using data flow engine):
  Track PII-tagged variables through the system:
  - Where is PII written to storage?
  - Where is PII logged?
  - Where is PII sent to external services?
  - Where is PII returned in API responses?

Output:
  - PII flow graph (subset of main graph)
  - GDPR compliance report:
    * "Email is logged in 3 places (auth.log, error.log, analytics.track)"
    * "User.phone flows to external analytics service — consent required"

Configuration: codegraph-privacy.yml (define PII fields, consent rules)
```

### 18.3 Air-Gapped Deployment

```
Enterprise requirement: no code leaves the premises.

Deployment options:
  1. Docker Compose (single-machine):
     - All services containerized
     - Local embedding model (ONNX format)
     - Local Neo4j / Memgraph
     - Local Qdrant
     - No external API calls

  2. Kubernetes (cluster):
     - Helm chart provided
     - Horizontal scaling
     - Air-gapped image registry (push images from outside)

  3. Offline AI models:
     - CodeBERT / GraphCodeBERT: downloadable ONNX models
     - Qwen2.5-Coder: quantized GGUF for local inference
     - All inference via ONNX Runtime or llama.cpp — no cloud

Configuration:
  CODEGRAPH_AI_MODE=offline
  CODEGRAPH_EMBEDDING_MODEL=local:codegraphbert
  CODEGRAPH_LLM=local:qwen2.5-coder-7b
```

### 18.4 Role-Based Access Control

```
Roles:
  viewer     — read graph, search, use AI chat
  developer  — all viewer + API access + IDE extensions
  architect  — all developer + architecture config, rule definitions
  admin      — full access, user management, index control

Entity-level access control (Enterprise):
  - Tag entities with sensitivity level
  - "confidential" entities not shown to viewers
  - PII-related subgraph restricted to privacy-role users
  - Audit log: who queried what and when
```

---

## 19. Advanced Research Features

### 19.1 Runtime-Augmented Static Analysis

```
Integration with runtime tracing:
  Input sources:
  - OpenTelemetry traces (from production or staging)
  - Python: sys.settrace, coverage.py data
  - JVM: JFR (Java Flight Recorder)
  - Go: pprof profiles

  Augmentation:
  - Static call graph edge + runtime hit count = "actual hot path"
  - Dynamic callee discovery (routes not found statically)
  - Actual data types observed at runtime (vs inferred)
  - Error-prone paths (traces ending in exception)

  Output:
  - Hybrid graph: static skeleton + runtime enrichment
  - Hot path visualization (most executed routes highlighted)
  - Coverage gap detection (static code never hit in production)
```

### 19.2 AI-Generated Architecture Documentation

```
Auto-generated docs:
  1. Subsystem README
     - What it does (from AI analysis of entities + docstrings)
     - Public API (exported entities)
     - Dependencies (what it needs from other subsystems)
     - Architecture diagram (Mermaid or SVG)

  2. Function/Class Docstrings
     - For undocumented entities: generate docstring from body analysis
     - Export as: inline code comments or separate doc file

  3. Onboarding Guide
     - "How to understand this codebase in 30 minutes"
     - Entry points, key modules, architectural overview
     - Common workflows with code pointers

  4. Architecture Decision Records (ADRs)
     - Infer past architectural decisions from git history + commit messages
     - Draft ADRs for significant structural changes
     - Template: context, decision, consequences

  Regeneration: triggered by significant architectural changes
  Format: Markdown, exported to /docs/architecture/
  Review: human-in-the-loop (AI draft → human approval → committed)
```

### 19.3 Autonomous Refactoring Suggestions

```
Refactoring opportunities detected and suggested:

1. Extract Module
   Condition: cluster of entities within a module has high cohesion internally
              but low coupling to rest of module
   Suggestion: "These 8 functions form a natural cache management subsystem.
               Consider extracting to src/cache/."

2. Extract Interface
   Condition: multiple classes share method signatures
   Suggestion: "DatabaseAdapter, FileAdapter, and ApiAdapter share 4 methods.
               Consider defining IStorageAdapter interface."

3. Inline Function
   Condition: function is called exactly once, body is short (<5 lines)
   Suggestion: "getFormattedDate() is called only in renderHeader(). Consider inlining."

4. Move Function
   Condition: feature envy detected (function accesses another class's data more)
   Suggestion: "processBillingEvent() uses BillingAccount fields 8x vs Order fields 2x.
               Consider moving to BillingAccount class."

5. Dependency Inversion
   Condition: high-level module imports low-level module directly
   Suggestion: "AuthService imports PostgresUserRepository directly, violating DIP.
               Introduce IUserRepository interface."

Output format:
  - Suggestion card: issue, evidence, proposed change, risk level
  - Optional: AI-generated code diff showing the refactoring applied
  - Tracking: accept/dismiss/defer, fed back into priority for future suggestions
```

### 19.4 Test Generation from Graph

```
Strategy: Use call graph + data flow to generate integration tests

For each public API endpoint / exported function:
1. Trace all code paths (static CFG)
2. Identify inputs and outputs
3. Find existing tests as examples (learn style)
4. Generate test cases covering:
   a. Happy path (normal input)
   b. Edge cases (null, empty, boundary values)
   c. Error cases (invalid input types, missing required fields)

Example:
  Function: authenticate(email: string, password: string): Session
  Generated tests:
  - test("authenticate returns session for valid credentials", ...)
  - test("authenticate throws AuthError for wrong password", ...)
  - test("authenticate throws ValidationError for empty email", ...)
  - test("authenticate handles email case-insensitively", ...)

Integration test generation:
  Trace full request path: HTTP Route → Controller → Service → Repository → DB
  Generate end-to-end test with mock at repository layer
```

---

## 20. Complete Tech Stack

### 20.1 Backend Services

```yaml
Core Parser Service:
  Language: Rust (performance) or Python (development speed)
  Crates/Libs: tree-sitter, logos (lexer), rayon (parallel), serde
  Deployment: stateless microservice, horizontally scalable
  Communication: gRPC (protobuf)

Graph Engine Service:
  Language: Python or Go
  DB: Neo4j 5.x Community/Enterprise or Memgraph
  ORM/Driver: neo4j-driver (Python), neo4j-go-driver
  Deployment: stateful, single primary + read replicas

Embedding Service:
  Language: Python
  Models: sentence-transformers, ONNX Runtime, transformers
  GPU: CUDA-optional (ONNX CPU inference acceptable for <100K entities/day)
  Vector DB: Qdrant (self-hosted or Qdrant Cloud)

AI Query Service:
  Language: Python
  Frameworks: LangChain / LlamaIndex / custom GraphRAG
  LLM backends: Anthropic API, OpenAI API, local llama.cpp
  Deployment: stateless, memory-light (LLM API calls are remote)

API Gateway:
  Framework: FastAPI (Python) + async
  Auth: JWT + API key (Bearer token)
  Rate limiting: Redis-backed sliding window
  Protocol: REST + GraphQL + WebSocket (real-time updates)

Queue / Async Pipeline:
  Primary: Redis Streams (simple deployments)
  Scale-out: Apache Kafka (monorepo scale)
  Workers: Celery (Python) or custom Go workers

Cache:
  L1: in-process LRU (per service)
  L2: Redis 7.x (shared)
  L3: S3-compatible object store (snapshots, model artifacts)

File Watching Service:
  Language: Go or Rust (low overhead daemon)
  Libraries: notify (Rust) or fsnotify (Go)
  Communication: WebSocket to indexing coordinator
```

### 20.2 Frontend

```yaml
Web Application:
  Framework: React 18 + TypeScript
  State: Zustand (lightweight, fast)
  Routing: React Router v6
  Styling: Tailwind CSS + shadcn/ui components

Graph Rendering:
  Small graphs: D3.js v7 (SVG, full control)
  Large graphs: Sigma.js 3 (WebGL, 100K+ nodes)
  Architecture diagrams: Cytoscape.js (hierarchical layouts)
  3D exploration: Three.js (force-directed 3D for huge repos)

Data Fetching:
  REST: TanStack Query (React Query) with auto-refetch
  Real-time: native WebSocket / EventSource (SSE)
  GraphQL: Apollo Client

Worker Threads:
  Graph layout computation: Web Workers (off main thread)
  Large dataset processing: SharedArrayBuffer where supported

Build:
  Bundler: Vite
  Type check: tsc strict mode
  Lint: ESLint + Prettier
  Test: Vitest + Playwright (E2E)
```

### 20.3 AI / ML Stack

```yaml
Embedding Models:
  Local primary: microsoft/codebert-base (768d)
  Local alternative: jinaai/jina-embeddings-v3 (512d, faster)
  Cloud primary: voyage-code-2 (1536d, best quality)
  Cloud alternative: text-embedding-3-large

LLM Integration:
  Provider abstraction layer (swap between providers)
  Anthropic Claude: complex reasoning, architecture explanation
  OpenAI GPT-4: alternative
  Local: Qwen2.5-Coder-7B (GGUF via llama.cpp) for air-gapped

Vector Database:
  Qdrant: primary (Rust-native, fast, good filtering)
  pgvector: fallback (PostgreSQL extension, simpler ops)

RAG Framework:
  Custom GraphRAG (purpose-built for code graphs)
  LlamaIndex: used for document indexing (READMEs, docs)
  LangGraph: agent workflow orchestration

ML Ops:
  Model versioning: DVC or MLflow
  Embedding A/B testing: shadow mode (dual embed, compare recall)
  Monitoring: embedding drift detection
```

### 20.4 Infrastructure

```yaml
Containerization:
  Docker (all services)
  Docker Compose (development, small deployments)
  Helm chart (Kubernetes, enterprise)

CI/CD:
  GitHub Actions / GitLab CI
  Stages: lint → test → build → integration test → deploy

Monitoring:
  Metrics: Prometheus + Grafana
  Tracing: OpenTelemetry → Jaeger
  Logs: structured JSON → Loki or ELK
  Alerts: PagerDuty / OpsGenie integration

Database Backups:
  Neo4j: online backup to S3 (daily full, hourly incremental)
  Qdrant: snapshot export to S3
  Redis: RDB + AOF persistence
```

---

## 21. Repository Structure

```
codegraph/                          (monorepo root)
├── packages/
│   ├── core/                       (shared types, UIR schema, utilities)
│   │   ├── src/
│   │   │   ├── uir/               (UIR TypeScript types)
│   │   │   ├── schema/            (protobuf definitions)
│   │   │   └── utils/
│   │   └── package.json
│   │
│   ├── parser-python/              (Python language parser)
│   │   ├── src/
│   │   │   ├── parser.py
│   │   │   ├── ast_walker.py
│   │   │   ├── symbol_resolver.py
│   │   │   └── type_inferrer.py
│   │   ├── tests/
│   │   └── pyproject.toml
│   │
│   ├── parser-typescript/          (TypeScript/JavaScript parser)
│   ├── parser-go/                  (Go parser)
│   ├── parser-rust/                (Rust parser)
│   ├── parser-java/                (Java/Kotlin parser)
│   ├── parser-solidity/            (Solidity parser — strategic)
│   │
│   ├── graph-engine/               (Knowledge graph service)
│   │   ├── src/
│   │   │   ├── neo4j/             (Neo4j driver + queries)
│   │   │   ├── cypher/            (Query templates)
│   │   │   ├── temporal/          (Graph versioning)
│   │   │   └── algorithms/        (cycle detection, clustering, etc.)
│   │   └── migrations/
│   │
│   ├── embedding-service/          (Embedding pipeline)
│   │   ├── src/
│   │   │   ├── models/            (model wrappers)
│   │   │   ├── chunker.py         (semantic chunking)
│   │   │   ├── pipeline.py        (embedding pipeline)
│   │   │   └── qdrant_store.py
│   │   └── models/                (ONNX model files)
│   │
│   ├── ai-engine/                  (GraphRAG, memory, AI queries)
│   │   ├── src/
│   │   │   ├── graphrag/          (GraphRAG implementation)
│   │   │   ├── memory/            (repo memory system)
│   │   │   ├── agents/            (agent API handlers)
│   │   │   └── llm/               (LLM provider abstraction)
│   │   └── prompts/               (prompt templates)
│   │
│   ├── api-gateway/                (FastAPI REST + GraphQL + WebSocket)
│   │   ├── src/
│   │   │   ├── routes/
│   │   │   ├── auth/
│   │   │   ├── middleware/
│   │   │   └── websocket/
│   │   └── openapi.yaml
│   │
│   ├── git-intelligence/           (Git analysis service)
│   │   ├── src/
│   │   │   ├── blame.py
│   │   │   ├── temporal.py
│   │   │   └── ownership.py
│   │
│   ├── architecture-engine/        (Pattern detection, anti-patterns, risk)
│   │   ├── src/
│   │   │   ├── detectors/         (MVC, microservices, hexagonal, etc.)
│   │   │   ├── antipatterns/      (god object, circular deps, etc.)
│   │   │   └── risk/              (risk scoring)
│   │
│   ├── web/                        (React frontend)
│   │   ├── src/
│   │   │   ├── views/             (page-level components)
│   │   │   ├── graph/             (D3, Sigma, Three.js renderers)
│   │   │   ├── ai-chat/           (chat panel)
│   │   │   ├── overlays/          (heatmaps, ownership, etc.)
│   │   │   └── stores/            (Zustand state)
│   │   └── vite.config.ts
│   │
│   ├── vscode-extension/           (VSCode extension)
│   │   ├── src/
│   │   │   ├── extension.ts
│   │   │   ├── language-client/
│   │   │   ├── providers/         (hover, decoration, tree)
│   │   │   └── webview/           (React embedded panel)
│   │   └── package.json
│   │
│   ├── jetbrains-plugin/           (IntelliJ platform plugin)
│   │   ├── src/main/kotlin/
│   │   └── build.gradle.kts
│   │
│   └── cli/                        (CLI tool: codegraph index, search, query)
│       ├── src/
│       └── package.json
│
├── infra/
│   ├── docker/                     (Dockerfiles per service)
│   ├── docker-compose.yml          (full stack local dev)
│   ├── docker-compose.prod.yml
│   ├── helm/                       (Kubernetes Helm chart)
│   │   └── codegraph/
│   │       ├── Chart.yaml
│   │       ├── values.yaml
│   │       └── templates/
│   └── terraform/                  (cloud infrastructure as code)
│
├── docs/
│   ├── architecture/               (ADRs, system design docs)
│   ├── api/                        (OpenAPI spec, usage guides)
│   ├── parser-specs/               (UIR spec, parser interface docs)
│   └── research/                   (research notes, papers)
│
├── benchmarks/                     (performance benchmarks)
├── examples/                       (example repos for demo/testing)
├── codegraph.config.yml            (self-referential: CodeGraph config for this repo)
└── turbo.json                      (Turborepo build orchestration)
```

---

## 22. Development Phases & Milestones

### Phase 1 — Foundation Rewrite (Months 1–3)

**Goal:** Replace current system with a production-grade, modular foundation.

**Milestone 1.1 — Core Architecture (Weeks 1–4)**
- Define UIR TypeScript types + Protobuf schema
- Implement `IParser` interface
- Set up monorepo with Turborepo
- Set up CI/CD pipeline
- Define coding standards, PR process

**Milestone 1.2 — Python Parser (Weeks 5–8)**
- Tree-sitter Python integration
- Full UIR emission
- Local symbol resolution
- Import resolution (absolute + relative)
- Docstring extraction
- Complexity scoring
- Unit tests: 200+ test cases across Python patterns

**Milestone 1.3 — TypeScript Parser (Weeks 9–12)**
- TypeScript Compiler API integration
- Full type resolution (compiler-backed)
- JSX handling
- Type alias resolution
- Integration tests against real TS repos

**Milestone 1.4 — Graph Engine v1 (Weeks 10–12, parallel)**
- Neo4j schema + constraints + indexes
- UIR → graph write pipeline
- Basic Cypher query layer
- REST API v1 (search, basic queries)
- Local dev: docker-compose stack

**Phase 1 Deliverables:**
- Multi-language parser framework (Python + TypeScript working)
- Graph database with full Python + TS graph for a real repo
- Basic REST API
- Performance: index 100K LOC in <5 minutes

---

### Phase 2 — Semantic Intelligence (Months 4–6)

**Milestone 2.1 — Symbol Resolution (Weeks 13–18)**
- Cross-file symbol resolution (Python, TypeScript)
- Inheritance resolution
- Import alias handling
- Confidence scoring

**Milestone 2.2 — Embedding Pipeline (Weeks 15–20)**
- CodeBERT integration (local ONNX)
- Qdrant setup and schema
- Entity embedding pipeline
- Semantic search API endpoint

**Milestone 2.3 — Call Graph Engine (Weeks 17–22)**
- Static call graph for Python + TypeScript
- Dynamic pattern detection (event dispatch, DI)
- Call graph API endpoint

**Milestone 2.4 — GraphRAG v1 (Weeks 20–24)**
- Basic query intent classification
- Subgraph retrieval + vector augmentation
- LLM integration (Claude API)
- "Ask your codebase" feature in web UI

**Phase 2 Deliverables:**
- Semantic search working
- "Ask your codebase" answering architectural questions accurately
- GraphRAG demo-ready for external showcase

---

### Phase 3 — AI Platform (Months 7–9)

**Milestone 3.1 — Go + Java Parsers (Weeks 25–30)**
- Go parser (go/ast + go/types)
- Java parser (JavaParser)
- 4-language support

**Milestone 3.2 — Repo Memory System (Weeks 25–32)**
- Subsystem detection (clustering + heuristics)
- AI-generated subsystem summaries
- Memory API endpoint
- Memory invalidation logic

**Milestone 3.3 — Agent APIs v1 (Weeks 29–34)**
- Context retrieval API (for AI agents)
- Impact analysis API
- MCP server implementation
- Cursor + Claude Code integration guides

**Milestone 3.4 — Architecture Intelligence v1 (Weeks 30–36)**
- Architecture pattern detection
- Anti-pattern detection (god object, circular deps, layer violations)
- Risk scoring
- Architecture report generation

**Phase 3 Deliverables:**
- Agent API live (Cursor + Claude Code integrations working)
- Architecture intelligence report generated for real repos
- VSCode extension alpha (hover, semantic search)

---

### Phase 4 — Enterprise Scale (Months 10–14)

**Milestone 4.1 — Incremental Indexing (Weeks 37–44)**
- File hashing + change detection
- Dependency invalidation graph
- Graph patching (partial updates)
- File watch daemon
- Performance target: <500ms per file change

**Milestone 4.2 — Distributed Workers (Weeks 40–48)**
- Celery + Redis Streams pipeline
- Parallel parse workers
- Kafka integration (optional, for very large scale)
- Monorepo-specific features (selective indexing, lazy loading)

**Milestone 4.3 — Git Intelligence (Weeks 42–50)**
- Full git history ingestion
- Blame-based entity ownership
- Temporal graph events
- Architecture evolution visualization

**Milestone 4.4 — Enterprise Features (Weeks 46–56)**
- RBAC implementation
- Air-gapped deployment (ONNX models, offline LLM)
- Secret detection engine
- Kubernetes Helm chart
- SOC 2 audit trail

**Phase 4 Deliverables:**
- Monorepo indexing (500K+ LOC) under 30 minutes
- Enterprise deployment option
- Git intelligence dashboard

---

### Phase 5 — Research Features (Months 15–20)

- Rust + Solidity parsers
- Runtime trace augmentation (OpenTelemetry)
- Autonomous refactoring suggestions
- Test generation engine
- JetBrains plugin
- Three.js 3D visualization for huge repos
- Time-travel visualization
- Architecture documentation auto-generation

---

## 23. API Specifications

### 23.1 Authentication

```
API Key auth: Authorization: Bearer cg_live_xxxxxxxxxxxx
JWT auth (user sessions): Authorization: Bearer eyJ...
Scopes: read / write / admin / agent
```

### 23.2 Rate Limits

| Tier | Context queries/min | Search queries/min | Index operations/day |
|---|---|---|---|
| Free | 10 | 30 | 1 |
| Developer | 60 | 200 | 10 |
| Team | 300 | 1000 | 100 |
| Enterprise | Unlimited | Unlimited | Unlimited |

### 23.3 Webhook Events

```json
// Event: index.completed
{
  "event": "index.completed",
  "repo_id": "github:org/repo",
  "commit": "abc1234",
  "stats": {
    "entities_indexed": 4821,
    "files_parsed": 312,
    "duration_ms": 45200,
    "languages": {"typescript": 280, "python": 32}
  },
  "timestamp": "2025-01-15T10:30:00Z"
}

// Event: antipattern.detected
{
  "event": "antipattern.detected",
  "pattern": "circular_dependency",
  "severity": "high",
  "entities": ["module:src/auth", "module:src/user"],
  "repo_id": "github:org/repo"
}
```

---

## 24. Database Schemas

### 24.1 PostgreSQL (Operational Metadata)

```sql
-- Repositories
CREATE TABLE repositories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  external_id VARCHAR(500) UNIQUE NOT NULL, -- "github:org/repo"
  name VARCHAR(255) NOT NULL,
  url TEXT,
  default_branch VARCHAR(100) DEFAULT 'main',
  languages JSONB,                -- {"typescript": 0.65, "python": 0.35}
  last_indexed_at TIMESTAMPTZ,
  last_indexed_commit VARCHAR(40),
  index_status VARCHAR(50),       -- 'pending' | 'indexing' | 'ready' | 'failed'
  schema_version VARCHAR(20),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index jobs
CREATE TABLE index_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  repo_id UUID REFERENCES repositories(id),
  triggered_by VARCHAR(100),      -- 'webhook' | 'manual' | 'schedule'
  commit_hash VARCHAR(40),
  status VARCHAR(50),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error TEXT,
  stats JSONB                     -- entities_indexed, files_parsed, etc.
);

-- API keys
CREATE TABLE api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id UUID,
  key_hash VARCHAR(64) UNIQUE,    -- SHA-256 of actual key
  key_prefix VARCHAR(10),         -- "cg_live_" + first 8 chars (for display)
  name VARCHAR(255),
  scopes TEXT[],
  rate_limit_tier VARCHAR(50),
  last_used_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Repo memory cache
CREATE TABLE repo_memory (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  repo_id UUID REFERENCES repositories(id),
  commit_hash VARCHAR(40),
  generated_at TIMESTAMPTZ,
  memory JSONB,                   -- full RepoMemory object
  token_count INTEGER,
  model_version VARCHAR(50)
);
```

### 24.2 Neo4j Indexes & Constraints

```cypher
// Constraints (enforce uniqueness)
CREATE CONSTRAINT function_id UNIQUE ON (f:Function) ASSERT f.entity_id IS UNIQUE;
CREATE CONSTRAINT class_id UNIQUE ON (c:Class) ASSERT c.entity_id IS UNIQUE;
CREATE CONSTRAINT module_id UNIQUE ON (m:Module) ASSERT m.entity_id IS UNIQUE;
CREATE CONSTRAINT file_path UNIQUE ON (f:File) ASSERT f.path IS UNIQUE;
CREATE CONSTRAINT commit_hash UNIQUE ON (c:Commit) ASSERT c.hash IS UNIQUE;

// Indexes (query performance)
CREATE INDEX function_name FOR (f:Function) ON (f.name);
CREATE INDEX function_file FOR (f:Function) ON (f.file);
CREATE INDEX function_complexity FOR (f:Function) ON (f.complexity);
CREATE INDEX entity_language FOR (e:Entity) ON (e.language);
CREATE INDEX entity_cluster FOR (e:Entity) ON (e.semantic_cluster);
CREATE FULLTEXT INDEX entity_fulltext FOR (f:Function|c:Class|m:Module) ON EACH [f.name, f.docstring];
```

---

## 25. Competitive Analysis

| Feature | CodeGraph v2 | Sourcegraph | CodeSee | GitHub Copilot (Workspace) | Semgrep |
|---|---|---|---|---|---|
| Code search | Semantic + graph | Lexical | Visual | Lexical | Pattern |
| Architecture viz | Advanced | None | Basic | None | None |
| AI querying | GraphRAG | Cody (RAG) | None | Chat | None |
| Agent APIs | Yes (purpose-built) | Partial | No | No | No |
| Repo memory | Yes | No | No | No | No |
| Impact analysis | Deep (graph) | Shallow | Visual | None | None |
| Anti-patterns | Yes | None | None | None | Rules |
| Git intelligence | Deep | Partial | Partial | None | None |
| Multi-language | 10+ | 40+ | Partial | Any | 30+ |
| Solidity | Yes | No | No | No | Yes |
| Air-gapped | Yes | Enterprise | No | No | Yes |
| Self-hosted | Yes | Yes | No | No | Yes |
| Open source | Partial | Partial | No | No | Yes (OSS) |
| Incremental index | Yes | Yes | Unknown | N/A | Yes |
| Price (self-hosted) | Free/Open | Expensive | SaaS only | Subscription | Free/Pro |

**CodeGraph v2's unique combination:** Graph-native architecture + semantic AI + agent APIs + repo memory. No competitor has all four.

---

## 26. Monetization & GTM Strategy

### 26.1 Pricing Tiers

```
Free (Open Source CLI):
  - Single repo, local only
  - Python + TypeScript parsers
  - Basic graph + semantic search
  - No AI querying (bring your own API key)
  - Community support

Developer ($19/month):
  - 5 repos
  - All parsers (Tier 1 languages)
  - Full AI querying (included quota)
  - VSCode extension
  - API access (developer rate limits)
  - Email support

Team ($49/user/month, min 3 users):
  - Unlimited repos
  - All languages including Solidity
  - Agent APIs (Cursor + Claude Code)
  - Architecture intelligence reports
  - Git intelligence + ownership
  - JetBrains plugin
  - Slack/Teams integration
  - Priority support

Enterprise (custom):
  - Air-gapped deployment
  - RBAC + audit logs
  - Kubernetes (Helm)
  - Custom SLA
  - Dedicated onboarding
  - GDPR/SOC2 compliance
  - Custom parser development
  - Priority roadmap influence
```

### 26.2 Go-To-Market Strategy

**Phase 1 (Months 1–6): Developer Traction**
- Open-source the CLI and core parsers (GitHub)
- Target: engineering blogs, HN Show HN, Reddit r/programming
- Benchmark: "We indexed the React codebase in 90 seconds. Here's what we found."
- Content: Architecture analysis posts of famous open-source repos

**Phase 2 (Months 6–12): AI Agent Ecosystem**
- Native Cursor + Claude Code integration
- Target: AI coding agent users (fastest-growing dev segment)
- Positioning: "Cursor knows your file. CodeGraph knows your architecture."
- Partnerships: Apply to Cursor/Anthropic partner programs

**Phase 3 (Months 12–18): Enterprise**
- Target: VP Engineering / CTO at 100–1000 engineer companies
- Pain: "Our LLM agents keep re-reading the codebase. CodeGraph is the fix."
- Proof: ROI calculator (token cost savings + agent speed improvement)
- Channel: Sales-assisted for >$50K/year deals

**Solidity Vertical:**
- Target: Web3 security firms, DeFi protocols, smart contract auditors
- Positioning: "AI-native smart contract architecture intelligence"
- Timeline: Launch after Phase 3 parser is complete

---

## 27. Research Directions

### 27.1 GraphRAG for Codebases

**Research gap:** GraphRAG is proven for text knowledge graphs (Microsoft's paper). Application to semantic code graphs is underexplored.

**Novel contributions:**
- Code-specific graph construction (call graph + type graph + dependency graph = richer than text triples)
- Hybrid retrieval: vector similarity + graph traversal + BM25 — finding the optimal fusion
- Evaluation dataset: create code QA benchmark from real repos with human-expert answers
- **Publishable:** ICSE, FSE, ASE (top software engineering venues)

### 27.2 Architecture Embeddings

**Research gap:** Embedding individual functions is well-studied. Embedding architectural subsystems (collections of related entities) is not.

**Novel contributions:**
- Hierarchical embeddings: function → class → module → subsystem
- Graph-pooling for subsystem embedding (aggregate entity embeddings via graph structure)
- Cross-repo architectural similarity: "this auth module looks like auth in 80% of Django repos"
- **Publishable:** ICLR, NeurIPS (ML venues with SE applications track)

### 27.3 Semantic Dependency Compression

**Research gap:** Efficiently representing large codebases in LLM context windows.

**Novel contributions:**
- Information-theoretic analysis: which code entities carry the most information for a given query?
- Compression algorithm: prune redundant context while preserving reasoning accuracy
- Evaluation metric: "architecture reasoning accuracy per 1000 tokens"
- Directly useful for AI coding agents
- **Publishable:** ACL, EMNLP (NLP venues)

### 27.4 Autonomous Repository Understanding

**Research gap:** Can an AI agent build accurate, persistent architectural memory of a codebase with minimal human input?

**Novel contributions:**
- Benchmark: CodeRepoQA (question bank about open-source repo architecture)
- Agent architecture: planning + iterative refinement of repo memory
- Evaluation: memory quality vs token cost vs human expert agreement
- **Publishable:** NeurIPS, ICML (agents track)

---

## 28. Risk Register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Tree-sitter grammar coverage gap (unusual language constructs) | Medium | Medium | Error recovery + fallback to line-level parsing; track coverage % per language |
| LLM API costs exceed revenue at early stage | Medium | High | Local model option from day 1; aggressive caching; per-query cost budgeting |
| Neo4j license cost at scale | Low | High | Memgraph (MIT license) as drop-in alternative; graph abstraction layer makes migration feasible |
| Competitor (Sourcegraph) ships similar AI features | High | Medium | Speed to agent API market; differentiate on repo memory + Solidity |
| Open-source forks competing with commercial tier | Medium | Medium | License: source-available (BSL 1.1) for enterprise features; core remains MIT |
| Incremental indexing correctness bugs (stale graph) | Medium | High | Comprehensive invalidation tests; hash validation; "full re-index" fallback command |
| Air-gapped enterprise deals slowing down revenue | Low | Low | Cloud tier generates revenue while enterprise sales close; separate pricing |
| Embedding model deprecation breaking semantic search | Low | Medium | Abstract embedding provider; store model version per embedding; migration tooling |
| Privacy concern: code sent to cloud for embedding | Medium | High | Local embedding model from day 1 (ONNX); clear data handling docs; self-hosted option |
| Parser for new language taking too long to build | Medium | Low | Community contributions; tree-sitter grammars exist for 100+ languages (leverage existing) |

---

## Final Vision

```
Today:          Repository → Static Graph → HTML Visualization

CodeGraph v2:
                Repository
                    ↓
            Incremental Parsing (all languages)
                    ↓
         Unified Intermediate Representation
                    ↓
         Semantic Knowledge Graph (Neo4j)
                    ↓
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
Embeddings    Git Intelligence  Architecture
(Qdrant)      (temporal graph)  Intelligence
    └───────────────┼───────────────┘
                    ▼
         GraphRAG + Repo Memory
                    ↓
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
Agent APIs      IDE Extensions   Web Platform
(Cursor,        (VSCode,         (3D viz,
 Claude Code,    JetBrains)       AI chat,
 OpenHands)                       time-travel)
                    ↓
    Autonomous Engineering Intelligence
```

> **CodeGraph v2 is not a better code graph tool. It is the memory layer that makes AI coding agents architecturally intelligent.**

---

*Document version: 2.0.0 — Detailed Build Plan*
*Status: Pre-development research*
