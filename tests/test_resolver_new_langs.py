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


def test_go_stdlib_import_not_hijacked_by_a_local_dir_of_the_same_name(
    tmp_path: Path,
) -> None:
    """`import "time"` must stay external even when the repo has its own
    directory named `time`.

    Found on Grafana: `time` resolved to `pkg/tsdb/azuremonitor/time/`, and
    `context` to `pkg/services/grpcserver/context/` -- both stdlib, both
    hijacked by an unrelated local directory that happened to share the last
    path segment. In Go a single-segment import path is *always* stdlib;
    anything third-party or in-repo carries a domain-ish prefix
    (`github.com/...`), so there is no ambiguity to weigh here."""
    db = _index(
        tmp_path,
        {
            "go.mod": "module example.com/app\n\ngo 1.21\n",
            "time/helper.go": "package time\n\nfunc Helper() {}\n",
            "svc/svc.go": ('package svc\n\nimport "time"\n\nfunc Run() { _ = time.Now }\n'),
        },
    )
    dsts = _resolved_dsts(db)
    assert "external:time" in dsts, f"stdlib import was hijacked, got: {sorted(dsts)}"


def test_go_import_picks_the_right_package_among_same_named_dirs(tmp_path: Path) -> None:
    """Two directories named `models`; the import path says which one.

    Found on Grafana, where every `.../ngalert/models` import resolved to
    `pkg/cmd/grafana-cli/models/` instead -- the resolver matched only the
    import path's LAST segment and then took the alphabetically-first `.go`
    file in any directory with that name. On a real repo, common package names
    (`models`, `types`, `util`, `api`, `store`) collide constantly, so this
    quietly mis-wired a large fraction of all cross-package edges. Match the
    longest path suffix that is a real directory instead."""
    db = _index(
        tmp_path,
        {
            "go.mod": "module example.com/app\n\ngo 1.21\n",
            "cmd/cli/models/model.go": "package models\n\nfunc CliOnly() {}\n",
            "services/alerting/models/rules.go": (
                "package models\n\nfunc Validate() error {\n\treturn nil\n}\n"
            ),
            "services/alerting/svc.go": (
                "package alerting\n\n"
                'import "example.com/app/services/alerting/models"\n\n'
                "func Run() error {\n\treturn models.Validate()\n}\n"
            ),
        },
    )
    dsts = _resolved_dsts(db)
    assert not any("cmd/cli/models" in d for d in dsts), (
        f"import resolved to the wrong same-named package, got: {sorted(dsts)}"
    )


def test_go_qualified_cross_package_call_resolves(tmp_path: Path) -> None:
    """`models.Validate(...)` -- a call qualified by an imported package name --
    must resolve to the real function, not `external:`.

    Found live on Grafana while grounding a brainstorm, and it invalidated three
    rounds of A/B work: impact_analysis on `ValidateRuleGroupInterval` reported
    `total: 1` when ground-truth grep showed 3 real call sites. The two it
    missed were both written `models.ValidateRuleGroupInterval(...)` across a
    package boundary. A "1 caller" answer reads as "safe to change" -- the same
    misleading-small-number failure class as the round-7 type_usages bug, but on
    the function path.

    Root cause is resolver-side, not parser-side: the parser correctly emits a
    bare-name `go:?call:Validate` edge (the `models.` qualifier is dropped
    before the resolver sees it), then `_resolve_call` checks same-file, then
    the file's import table -- which for a Go package import is keyed by the
    module name, never by the function -- and gives up. Because every parser
    degrades qualified calls to the same bare-name shape, fixing it in the
    resolver fixes every language at once (see the Python twin below)."""
    import duckdb

    db = _index(
        tmp_path,
        {
            "go.mod": "module example.com/app\n\ngo 1.21\n",
            "models/rules.go": (
                "package models\n\nfunc Validate(interval int64) error {\n\treturn nil\n}\n"
            ),
            "service/svc.go": (
                "package service\n\n"
                'import "example.com/app/models"\n\n'
                "func Run() error {\n"
                "\treturn models.Validate(10)\n"
                "}\n"
            ),
        },
    )
    conn = duckdb.connect(str(db), read_only=True)
    calls = conn.execute(
        "SELECT src_id, dst_id FROM edges WHERE type = 'calls' AND src_id LIKE '%svc.go%'"
    ).fetchall()
    conn.close()

    assert ("go:service/svc.go:Run", "go:models/rules.go:Validate") in calls, (
        f"qualified cross-package call unresolved, got: {calls}"
    )


def test_python_qualified_module_call_resolves(tmp_path: Path) -> None:
    """The Python twin of the Go case above: `helpers.compute()` after
    `import helpers`. Listed in STATUS.md's backlog since the 2026-07-06 stress
    test as "calls through an imported module namespace don't resolve" -- same
    single resolver fix covers it, because the parser degrades this to the same
    bare-name `py:?call:compute` shape."""
    import duckdb

    db = _index(
        tmp_path,
        {
            "helpers.py": "def compute(x):\n    return x + 1\n",
            "app.py": "import helpers\n\n\ndef run():\n    return helpers.compute(1)\n",
        },
    )
    conn = duckdb.connect(str(db), read_only=True)
    calls = conn.execute(
        "SELECT src_id, dst_id FROM edges WHERE type = 'calls' AND src_id LIKE '%app.py%'"
    ).fetchall()
    conn.close()

    assert ("py:app.py:run", "py:helpers.py:compute") in calls, (
        f"qualified module call unresolved, got: {calls}"
    )


def test_qualified_call_stays_external_when_ambiguous(tmp_path: Path) -> None:
    """Guard on the fix's precision: the resolver only sees a bare callee name,
    so if two *different* imported packages both export that name there is no
    information left to pick between them. Must stay `external:` rather than
    guess -- same don't-guess-when-ambiguous policy the rest of the resolver
    follows."""
    import duckdb

    db = _index(
        tmp_path,
        {
            "go.mod": "module example.com/app\n\ngo 1.21\n",
            "alpha/a.go": "package alpha\n\nfunc Shared() error {\n\treturn nil\n}\n",
            "beta/b.go": "package beta\n\nfunc Shared() error {\n\treturn nil\n}\n",
            "service/svc.go": (
                "package service\n\n"
                "import (\n"
                '\t"example.com/app/alpha"\n'
                '\t"example.com/app/beta"\n'
                ")\n\n"
                "func Run() error {\n"
                "\t_ = beta.Shared\n"
                "\treturn alpha.Shared()\n"
                "}\n"
            ),
        },
    )
    conn = duckdb.connect(str(db), read_only=True)
    dsts = {
        r[0]
        for r in conn.execute(
            "SELECT dst_id FROM edges WHERE type = 'calls' AND src_id LIKE '%svc.go%'"
        ).fetchall()
    }
    conn.close()

    assert "external:Shared" in dsts, f"ambiguous name should stay external, got: {dsts}"


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
