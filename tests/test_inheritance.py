"""Tests for inheritance-aware receiver-type resolution (Phase 27).

`obj.method()` resolves to `Type.method` when `Type` declares it directly
(Phase 26). This closes the gap where `method` is only declared on a base
class: the parser emits a provisional `<lang>:?inherits:<Base>` edge per
declared base, the resolver closes it to a real class entity, and method-call
resolution walks those resolved bases when the exact type doesn't declare
the method itself.
"""

from __future__ import annotations

from pathlib import Path

from codegraph.cli import app
from codegraph.graph.store import GraphStore
from codegraph.parsers.java import JavaParser
from codegraph.parsers.php import PHPParser
from codegraph.parsers.python import PythonParser
from codegraph.parsers.ruby import RubyParser
from codegraph.parsers.typescript import TypeScriptParser
from typer.testing import CliRunner

# ---------- pure parser unit tests (no DB) ----------


def _inherits_edges(source: str):
    result = PythonParser().parse(Path("app.py"), source)
    return [e for e in result.edges if e.type == "inherits"]


def test_single_base_class_produces_inherits_edge() -> None:
    edges = _inherits_edges("class Base:\n    pass\n\nclass Foo(Base):\n    pass\n")
    assert len(edges) == 1
    assert edges[0].dst_id == "py:?inherits:Base"


def test_multiple_bases_produce_one_edge_each() -> None:
    edges = _inherits_edges(
        "class Base:\n    pass\nclass Mixin:\n    pass\nclass Foo(Base, Mixin):\n    pass\n"
    )
    assert {e.dst_id for e in edges} == {"py:?inherits:Base", "py:?inherits:Mixin"}


def test_metaclass_keyword_argument_is_not_a_base_class() -> None:
    edges = _inherits_edges("class Meta:\n    pass\nclass Foo(metaclass=Meta):\n    pass\n")
    assert edges == []


def test_class_with_no_bases_produces_no_inherits_edges() -> None:
    edges = _inherits_edges("class Foo:\n    pass\n")
    assert edges == []


# ---------- integration: resolver walks the inheritance chain ----------


def _make_repo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _index(tmp_path: Path, repo_files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    _make_repo(repo, repo_files)
    db = tmp_path / "graph.duckdb"
    result = CliRunner().invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    return db


def _edges(store: GraphStore) -> list[tuple[str, str, float]]:
    rows = store.conn.execute(
        "SELECT src_id, dst_id, confidence FROM edges ORDER BY src_id, dst_id"
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def test_inherits_edge_resolves_to_real_base_class_entity(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {"app.py": "class Base:\n    pass\n\nclass Foo(Base):\n    pass\n"},
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("py:app.py:Foo", "py:app.py:Base") in resolved


def test_method_only_on_base_class_resolves_through_inheritance(tmp_path: Path) -> None:
    """`Derived.method()` where `method` is declared only on `Base`, not
    `Derived` itself -- the exact case receiver-type resolution alone (Phase
    26, no inheritance awareness) would have missed."""
    db = _index(
        tmp_path,
        {
            "app.py": (
                "class Base:\n"
                "    def save(self):\n"
                "        pass\n\n"
                "class Derived(Base):\n"
                "    def run(self):\n"
                "        self.save()\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("py:app.py:Derived.run", "py:app.py:Base.save") in resolved


def test_two_level_inheritance_chain_resolves(tmp_path: Path) -> None:
    """`method` is on the grandparent -- the walk must go two hops."""
    db = _index(
        tmp_path,
        {
            "app.py": (
                "class Grandparent:\n"
                "    def save(self):\n"
                "        pass\n\n"
                "class Parent(Grandparent):\n"
                "    pass\n\n"
                "class Child(Parent):\n"
                "    def run(self):\n"
                "        self.save()\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("py:app.py:Child.run", "py:app.py:Grandparent.save") in resolved


def test_method_overridden_on_derived_class_prefers_derived(tmp_path: Path) -> None:
    """When `Derived` declares its own `save`, that (not the base's) is what
    `self.save()` resolves to -- Phase 26 behavior must still win over the
    inheritance walk, since the walk only fires when the direct lookup fails."""
    db = _index(
        tmp_path,
        {
            "app.py": (
                "class Base:\n"
                "    def save(self):\n"
                "        pass\n\n"
                "class Derived(Base):\n"
                "    def save(self):\n"
                "        pass\n"
                "    def run(self):\n"
                "        self.save()\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("py:app.py:Derived.run", "py:app.py:Derived.save") in resolved
    assert ("py:app.py:Derived.run", "py:app.py:Base.save") not in resolved


def test_method_found_on_correct_base_when_two_files_share_class_names(tmp_path: Path) -> None:
    """Two unrelated `Base` classes in different files, each with a `save`
    method -- `Derived(Base)` in file_a must walk to file_a's Base, not
    file_b's, exactly like the same-file preference for direct calls."""
    db = _index(
        tmp_path,
        {
            "file_a.py": (
                "class Base:\n"
                "    def save(self):\n"
                "        pass\n\n"
                "class Derived(Base):\n"
                "    def run(self):\n"
                "        self.save()\n"
            ),
            "file_b.py": ("class Base:\n    def save(self):\n        pass\n"),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("py:file_a.py:Derived.run", "py:file_a.py:Base.save") in resolved
    assert ("py:file_a.py:Derived.run", "py:file_b.py:Base.save") not in resolved


def test_method_not_found_anywhere_in_chain_falls_back_to_plain_resolution(
    tmp_path: Path,
) -> None:
    db = _index(
        tmp_path,
        {
            "app.py": (
                "class Base:\n"
                "    pass\n\n"
                "class Derived(Base):\n"
                "    def run(self):\n"
                "        self.missing()\n\n"
                "def missing():\n"
                "    pass\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("py:app.py:Derived.run", "py:app.py:missing") in resolved


# ---------- TypeScript: pure parser unit tests (no DB) ----------


def _ts_inherits_edges(source: str):
    result = TypeScriptParser().parse(Path("app.ts"), source)
    return [e for e in result.edges if e.type == "inherits"]


def test_ts_extends_clause_produces_inherits_edge() -> None:
    edges = _ts_inherits_edges("class Base {}\nclass Foo extends Base {}\n")
    assert len(edges) == 1
    assert edges[0].dst_id == "ts:?inherits:Base"


def test_ts_implements_only_produces_no_inherits_edge() -> None:
    edges = _ts_inherits_edges("interface IFoo {}\nclass Foo implements IFoo {}\n")
    assert edges == []


def test_ts_class_with_no_heritage_produces_no_inherits_edges() -> None:
    edges = _ts_inherits_edges("class Foo {}\n")
    assert edges == []


# ---------- TypeScript: integration ----------


def test_ts_method_only_on_base_class_resolves_through_inheritance(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "app.ts": (
                "class Base {\n"
                "  save() {}\n"
                "}\n"
                "class Derived extends Base {\n"
                "  run() {\n"
                "    this.save();\n"
                "  }\n"
                "}\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("ts:app.ts:Derived.run", "ts:app.ts:Base.save") in resolved


def test_ts_method_overridden_on_derived_class_prefers_derived(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "app.ts": (
                "class Base {\n"
                "  save() {}\n"
                "}\n"
                "class Derived extends Base {\n"
                "  save() {}\n"
                "  run() {\n"
                "    this.save();\n"
                "  }\n"
                "}\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("ts:app.ts:Derived.run", "ts:app.ts:Derived.save") in resolved
    assert ("ts:app.ts:Derived.run", "ts:app.ts:Base.save") not in resolved


# ---------- Java: pure parser unit tests (no DB) ----------


def _java_inherits_edges(source: str):
    result = JavaParser().parse(Path("T.java"), source)
    return [e for e in result.edges if e.type == "inherits"]


def test_java_extends_produces_inherits_edge() -> None:
    edges = _java_inherits_edges("class Base {}\nclass Foo extends Base {}\n")
    assert len(edges) == 1
    assert edges[0].dst_id == "java:?inherits:Base"


def test_java_implements_produces_inherits_edges() -> None:
    edges = _java_inherits_edges(
        "interface IFoo {}\ninterface IBar {}\nclass Foo implements IFoo, IBar {}\n"
    )
    assert {e.dst_id for e in edges} == {"java:?inherits:IFoo", "java:?inherits:IBar"}


def test_java_extends_and_implements_both_captured() -> None:
    edges = _java_inherits_edges(
        "class Base {}\ninterface IFoo {}\nclass Foo extends Base implements IFoo {}\n"
    )
    assert {e.dst_id for e in edges} == {"java:?inherits:Base", "java:?inherits:IFoo"}


# ---------- Java: integration ----------


def test_java_method_only_on_base_class_resolves_through_inheritance(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "T.java": (
                "class Base {\n"
                "    void save() {}\n"
                "}\n"
                "class Derived extends Base {\n"
                "    void run() {\n"
                "        this.save();\n"
                "    }\n"
                "}\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("java:T.java:Derived.run", "java:T.java:Base.save") in resolved


def test_java_default_interface_method_resolves_through_implements(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "T.java": (
                "interface Greeter {\n"
                "    default void greet() {}\n"
                "}\n"
                "class Foo implements Greeter {\n"
                "    void run() {\n"
                "        this.greet();\n"
                "    }\n"
                "}\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("java:T.java:Foo.run", "java:T.java:Greeter.greet") in resolved


# ---------- PHP: pure parser unit tests (no DB) ----------


def _php_inherits_edges(source: str):
    result = PHPParser().parse(Path("T.php"), source)
    return [e for e in result.edges if e.type == "inherits"]


def test_php_extends_produces_inherits_edge() -> None:
    edges = _php_inherits_edges("<?php\nclass Base {}\nclass Foo extends Base {}\n")
    assert len(edges) == 1
    assert edges[0].dst_id == "php:?inherits:Base"


def test_php_implements_only_produces_no_inherits_edge() -> None:
    edges = _php_inherits_edges("<?php\ninterface IFoo {}\nclass Foo implements IFoo {}\n")
    assert edges == []


# ---------- PHP: integration ----------


def test_php_method_only_on_base_class_resolves_through_inheritance(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "T.php": (
                "<?php\n"
                "class Base {\n"
                "    function save() {}\n"
                "}\n"
                "class Derived extends Base {\n"
                "    function run() {\n"
                "        $this->save();\n"
                "    }\n"
                "}\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("php:T.php:Derived.run", "php:T.php:Base.save") in resolved


# ---------- Ruby: pure parser unit tests (no DB) ----------


def _rb_inherits_edges(source: str):
    result = RubyParser().parse(Path("t.rb"), source)
    return [e for e in result.edges if e.type == "inherits"]


def test_rb_superclass_produces_inherits_edge() -> None:
    edges = _rb_inherits_edges("class Base\nend\nclass Foo < Base\nend\n")
    assert len(edges) == 1
    assert edges[0].dst_id == "rb:?inherits:Base"


def test_rb_class_with_no_superclass_produces_no_inherits_edges() -> None:
    edges = _rb_inherits_edges("class Foo\nend\n")
    assert edges == []


def test_rb_module_produces_no_inherits_edges() -> None:
    # Modules can't have a superclass in Ruby.
    edges = _rb_inherits_edges("module Foo\nend\n")
    assert edges == []


# ---------- Ruby: integration ----------


def test_rb_method_only_on_base_class_resolves_through_inheritance(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "t.rb": (
                "class Base\n"
                "  def save\n"
                "  end\n"
                "end\n"
                "class Derived < Base\n"
                "  def run\n"
                "    self.save\n"
                "  end\n"
                "end\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("rb:t.rb:Derived.run", "rb:t.rb:Base.save") in resolved
