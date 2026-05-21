-- CodeGraph graph database — DuckDB DDL.
-- Idempotent: every CREATE uses IF NOT EXISTS, safe to re-run.
-- Mirrors UIREntity / Edge shapes from packages/codegraph/uir.py.

CREATE TABLE IF NOT EXISTS files (
  path        VARCHAR PRIMARY KEY,
  language    VARCHAR NOT NULL,
  hash        VARCHAR NOT NULL,
  loc         INTEGER,
  indexed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entities (
  entity_id       VARCHAR PRIMARY KEY,
  type            VARCHAR NOT NULL,
  name            VARCHAR NOT NULL,
  qualified_name  VARCHAR NOT NULL,
  language        VARCHAR NOT NULL,
  file            VARCHAR NOT NULL,
  start_line      INTEGER NOT NULL,
  end_line        INTEGER NOT NULL,
  start_col       INTEGER NOT NULL DEFAULT 0,
  end_col         INTEGER NOT NULL DEFAULT 0,
  raw_source      TEXT,
  docstring       TEXT,
  signature       TEXT,
  is_exported     BOOLEAN NOT NULL DEFAULT TRUE,
  is_async        BOOLEAN NOT NULL DEFAULT FALSE,
  parent_id       VARCHAR,
  hash            VARCHAR NOT NULL,
  summary         TEXT,
  embedding       FLOAT[384],   -- populated in Phase 3
  embedding_hash  VARCHAR,       -- drift detection in Phase 3 (T3.5)
  FOREIGN KEY (file) REFERENCES files(path)
);

CREATE INDEX IF NOT EXISTS idx_entities_name   ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_file   ON entities(file);
CREATE INDEX IF NOT EXISTS idx_entities_qname  ON entities(qualified_name);
CREATE INDEX IF NOT EXISTS idx_entities_parent ON entities(parent_id);

CREATE TABLE IF NOT EXISTS edges (
  src_id      VARCHAR NOT NULL,
  dst_id      VARCHAR NOT NULL,
  type        VARCHAR NOT NULL,
  line        INTEGER NOT NULL,
  confidence  FLOAT NOT NULL DEFAULT 1.0,
  is_dynamic  BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (src_id, dst_id, type, line)
);

CREATE INDEX IF NOT EXISTS idx_edges_src  ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst  ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
