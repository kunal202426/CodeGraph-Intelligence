"""Tests for cross-language import/call resolution (T10.7).

Verifies that after indexing, no provisional `<lang>:?%` edges remain and
that in-repo symbols resolve to real entity_ids.
"""

from __future__ import annotations

from pathlib import Path

from codegraph.cli import app
from typer.testing import CliRunner


def _make_repo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _index(tmp_path: Path, repo_files: dict[str, str]) -> Path:
    """Materialize a fake repo, index it via CLI, return the DB path."""
    repo = tmp_path / "repo"
    _make_repo(repo, repo_files)
    db = tmp_path / "graph.duckdb"
    result = CliRunner().invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.stdout
    return db


def _provisional_edges(db: Path, prefix: str) -> list[tuple]:
    """Return all edges whose dst_id still starts with the provisional prefix."""
    import duckdb

    conn = duckdb.connect(str(db), read_only=True)
    rows = conn.execute(
        "SELECT src_id, dst_id FROM edges WHERE dst_id LIKE ?",
        [f"{prefix}:?%"],
    ).fetchall()
    conn.close()
    return rows


def _resolved_dsts(db: Path) -> set[str]:
    import duckdb

    conn = duckdb.connect(str(db), read_only=True)
    rows = conn.execute("SELECT dst_id FROM edges").fetchall()
    conn.close()
    return {r[0] for r in rows}


# ---------- Go ----------


def test_go_provisional_edges_cleared(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "server/server.go": (
                'package server\nimport "fmt"\n'
                "type Server struct{}\n"
                "func New() *Server { return &Server{} }\n"
                "func (s *Server) Start() { fmt.Println() }\n"
            ),
            "cmd/main.go": (
                'package main\nimport "sample/server"\n'
                "func main() { s := server.New(); s.Start() }\n"
            ),
        },
    )
    assert not _provisional_edges(db, "go"), "Go provisional edges should be resolved"


def test_go_inrepo_import_resolved(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "server/server.go": (
                "package server\ntype Server struct{}\nfunc New() *Server { return &Server{} }\n"
            ),
            "main.go": ('package main\nimport "mymod/server"\nfunc main() { server.New() }\n'),
        },
    )
    dsts = _resolved_dsts(db)
    # The "server" import should resolve to the server module entity (not external)
    assert any("server" in d and not d.startswith("external:") for d in dsts)


# ---------- Rust ----------


def test_rust_provisional_edges_cleared(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "src/server.rs": (
                "pub struct Server {}\n"
                "impl Server {\n    pub fn start(&self) { self.listen(); }\n"
                "    fn listen(&self) {}\n}\n"
            ),
            "src/main.rs": (
                "use std::fmt;\nfn main() {\n    let s = crate::server::Server {};\n}\n"
            ),
        },
    )
    assert not _provisional_edges(db, "rs"), "Rust provisional edges should be resolved"


def test_rust_call_resolves_same_file(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "src/lib.rs": ("pub fn greet() {}\npub fn run() { greet(); }\n"),
        },
    )
    dsts = _resolved_dsts(db)
    # greet() call from run() should resolve to the greet entity, not external
    assert any("greet" in d and not d.startswith("external:") for d in dsts)


# ---------- Java ----------


def test_java_provisional_edges_cleared(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "src/Server.java": (
                "package com.example;\n"
                "import java.util.List;\n"
                "public class Server {\n"
                "    public void start() { listen(); }\n"
                "    private void listen() {}\n"
                "}\n"
            ),
        },
    )
    assert not _provisional_edges(db, "java"), "Java provisional edges should be resolved"


def test_java_stdlib_import_external(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {"Main.java": "import java.util.List;\npublic class Main {}\n"},
    )
    dsts = _resolved_dsts(db)
    assert any(d.startswith("external:java.util.List") for d in dsts)


def test_java_inrepo_import_resolved(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "com/example/Server.java": "package com.example;\npublic class Server {}\n",
            "com/example/Main.java": (
                "package com.example;\nimport com.example.Server;\npublic class Main {}\n"
            ),
        },
    )
    dsts = _resolved_dsts(db)
    # com.example.Server should resolve to the Server entity or module
    assert any("Server" in d and not d.startswith("external:") for d in dsts)


def test_java_same_package_call_resolves_without_import(tmp_path: Path) -> None:
    """Java doesn't require an `import` for a sibling class in the same
    package -- a call/constructor-call resolver that only checks "same
    file" or "an explicit import" can never find it. Regression test: found
    live against a real Java codebase, where this was the dominant cause of
    an 819/889 external-import rate."""
    import duckdb

    db = _index(
        tmp_path,
        {
            "com/example/WelfordStats.java": (
                "package com.example;\npublic class WelfordStats {\n"
                "    public WelfordStats() {}\n}\n"
            ),
            "com/example/AnomalyScorer.java": (
                "package com.example;\npublic class AnomalyScorer {\n"
                "    private final WelfordStats baseline = new WelfordStats();\n"
                "}\n"
            ),
        },
    )
    conn = duckdb.connect(str(db), read_only=True)
    calls = conn.execute("SELECT src_id, dst_id FROM edges WHERE type = 'calls'").fetchall()
    conn.close()
    assert (
        "java:com/example/AnomalyScorer.java:AnomalyScorer",
        "java:com/example/WelfordStats.java:WelfordStats",
    ) in calls


# ---------- Ruby ----------


def test_ruby_provisional_edges_cleared(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "lib/server.rb": (
                "require 'json'\n"
                "class Server\n"
                "  def start\n    self.listen\n  end\n"
                "  private\n  def listen\n  end\nend\n"
            ),
        },
    )
    assert not _provisional_edges(db, "rb"), "Ruby provisional edges should be resolved"


def test_ruby_require_relative_resolved(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "lib/server.rb": "class Server\nend\n",
            "lib/main.rb": "require_relative './server'\nclass Main\nend\n",
        },
    )
    dsts = _resolved_dsts(db)
    # require_relative './server' should resolve to the server module
    assert any("server" in d and not d.startswith("external:") for d in dsts)


def test_ruby_stdlib_require_external(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {"app.rb": "require 'json'\nclass App\nend\n"},
    )
    dsts = _resolved_dsts(db)
    assert any(d.startswith("external:json") for d in dsts)


# ---------- PHP ----------


def test_php_provisional_edges_cleared(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "src/Server.php": (
                "<?php\nuse App\\Http\\Request;\n"
                "class Server {\n"
                "    public function start(): void { $this->listen(); }\n"
                "    private function listen(): void {}\n"
                "}\n"
            ),
        },
    )
    assert not _provisional_edges(db, "php"), "PHP provisional edges should be resolved"


def test_php_namespace_import_resolved(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "App/Http/Request.php": "<?php\nnamespace App\\Http;\nclass Request {}\n",
            "App/Controller.php": ("<?php\nuse App\\Http\\Request;\nclass Controller {}\n"),
        },
    )
    dsts = _resolved_dsts(db)
    assert any("Request" in d and not d.startswith("external:") for d in dsts)


# ---------- C ----------


def test_c_provisional_edges_cleared(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "src/server.c": (
                '#include <stdio.h>\n#include "server.h"\n'
                'void server_start() { printf("hi"); server_listen(); }\n'
                "static void server_listen() {}\n"
            ),
            "src/server.h": "void server_start();\n",
        },
    )
    assert not _provisional_edges(db, "c"), "C provisional edges should be resolved"


def test_c_local_include_resolved(tmp_path: Path) -> None:
    # Header in same directory as the including source file.
    db = _index(
        tmp_path,
        {
            "src/server.h": "void server_start();\n",
            "src/main.c": '#include "server.h"\nint main() { server_start(); }\n',
        },
    )
    dsts = _resolved_dsts(db)
    # "server.h" resolved relative to src/ should point to the header module
    assert any("server" in d and not d.startswith("external:") for d in dsts)


def test_c_system_include_external(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {"main.c": "#include <stdio.h>\nint main() { return 0; }\n"},
    )
    dsts = _resolved_dsts(db)
    assert any(d.startswith("external:stdio.h") for d in dsts)


# ---------- C++ ----------


def test_cpp_provisional_edges_cleared(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "src/server.cpp": (
                '#include <string>\n#include "server.h"\n'
                "class Server {\npublic:\n"
                "    void start() { this->listen(); }\n"
                "private:\n    void listen() {}\n};\n"
            ),
            "src/server.h": "class Server;\n",
        },
    )
    assert not _provisional_edges(db, "cpp"), "C++ provisional edges should be resolved"


def test_cpp_system_include_external(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {"main.cpp": "#include <string>\nvoid greet() {}\n"},
    )
    dsts = _resolved_dsts(db)
    assert any(d.startswith("external:string") for d in dsts)
