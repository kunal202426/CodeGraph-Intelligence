"""Tests for receiver-type inference on Python and TypeScript method calls
(Phase 26).

`obj.method()` only resolves to the right `method` if `obj`'s type is known.
Unit tests check the provisional `<lang>:?methodcall:<Type>.<name>` edge the
parser emits; integration tests check the resolver closes it to the correct
entity_id, including disambiguating between two same-named methods on
different classes -- the exact case plain name-matching gets wrong.
"""

from __future__ import annotations

from pathlib import Path

from codegraph.cli import app
from codegraph.graph.store import GraphStore
from codegraph.parsers.java import JavaParser
from codegraph.parsers.python import PythonParser
from codegraph.parsers.typescript import TypeScriptParser
from typer.testing import CliRunner

# ---------- pure parser unit tests (no DB) ----------


def _call_edges(source: str, src_suffix: str = ""):
    result = PythonParser().parse(Path("app.py"), source)
    edges = [e for e in result.edges if e.type == "calls"]
    if src_suffix:
        edges = [e for e in edges if e.src_id.endswith(src_suffix)]
    return edges


def test_self_call_infers_enclosing_class_as_receiver_type() -> None:
    edges = _call_edges(
        "class Widget:\n"
        "    def render(self):\n"
        "        pass\n"
        "    def draw(self):\n"
        "        self.render()\n",
        src_suffix="Widget.draw",
    )
    assert len(edges) == 1
    assert edges[0].dst_id == "py:?methodcall:Widget.render"


def test_local_variable_constructor_call_infers_type() -> None:
    edges = _call_edges(
        "class Logger:\n"
        "    def log(self):\n"
        "        pass\n\n"
        "def use():\n"
        "    lg = Logger()\n"
        "    lg.log()\n",
        src_suffix=":use",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "py:?methodcall:Logger.log"


def test_annotated_local_variable_infers_type() -> None:
    edges = _call_edges(
        "class Logger:\n"
        "    def log(self):\n"
        "        pass\n\n"
        "def use():\n"
        "    lg: Logger = get_logger()\n"
        "    lg.log()\n",
        src_suffix=":use",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "py:?methodcall:Logger.log"


def test_typed_parameter_infers_type() -> None:
    edges = _call_edges(
        "class Service:\n"
        "    def notify(self):\n"
        "        pass\n\n"
        "class Caller:\n"
        "    def use(self, svc: Service):\n"
        "        svc.notify()\n",
        src_suffix="Caller.use",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "py:?methodcall:Service.notify"


def test_self_attribute_set_in_init_resolves_from_other_method() -> None:
    edges = _call_edges(
        "class Service:\n"
        "    def save(self):\n"
        "        pass\n\n"
        "class Caller:\n"
        "    def __init__(self):\n"
        "        self.svc = Service()\n"
        "    def run(self):\n"
        "        self.svc.save()\n",
        src_suffix="Caller.run",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "py:?methodcall:Service.save"


def test_untyped_receiver_falls_back_to_plain_call_edge() -> None:
    edges = _call_edges(
        "def use():\n    result = get_thing()\n    result.process()\n",
        src_suffix=":use",
    )
    dst_ids = {e.dst_id for e in edges}
    assert "py:?call:process" in dst_ids
    assert not any("?methodcall:" in dst for dst in dst_ids)


def test_generic_type_annotation_is_not_treated_as_a_receiver_type() -> None:
    # `x: List[Logger]` isn't a single-object receiver type -- don't guess.
    edges = _call_edges(
        "def use():\n    items: List[Logger] = get_items()\n    items.append(1)\n",
        src_suffix=":use",
    )
    dst_ids = {e.dst_id for e in edges}
    assert "py:?call:append" in dst_ids
    assert not any("?methodcall:" in dst for dst in dst_ids)


# ---------- integration: resolver closes the edge to the right entity ----------


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


def test_resolves_to_correct_class_when_two_files_share_a_method_name(tmp_path: Path) -> None:
    """The exact case plain same-file/name matching gets wrong: two unrelated
    `Logger` classes, each with `log()`, in different files. A locally-typed
    `lg = Logger()` in file_a must resolve to file_a's Logger.log, not file_b's."""
    db = _index(
        tmp_path,
        {
            "file_a.py": (
                "class Logger:\n"
                "    def log(self):\n"
                "        pass\n\n"
                "def use():\n"
                "    lg = Logger()\n"
                "    lg.log()\n"
            ),
            "file_b.py": ("class Logger:\n    def log(self):\n        pass\n"),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("py:file_a.py:use", "py:file_a.py:Logger.log") in resolved
    assert ("py:file_a.py:use", "py:file_b.py:Logger.log") not in resolved


def test_self_call_resolves_across_two_same_named_methods_in_one_file(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "app.py": (
                "class Widget:\n"
                "    def render(self):\n"
                "        return 1\n"
                "    def draw(self):\n"
                "        self.render()\n\n"
                "class Panel:\n"
                "    def render(self):\n"
                "        return 2\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("py:app.py:Widget.draw", "py:app.py:Widget.render") in resolved
    assert ("py:app.py:Widget.draw", "py:app.py:Panel.render") not in resolved


def test_self_attr_type_resolves_from_a_different_method_than_init(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "app.py": (
                "class Service:\n"
                "    def save(self):\n"
                "        pass\n\n"
                "class Caller:\n"
                "    def __init__(self):\n"
                "        self.svc = Service()\n"
                "    def run(self):\n"
                "        self.svc.save()\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("py:app.py:Caller.run", "py:app.py:Service.save") in resolved


def test_method_not_found_on_inferred_type_falls_back_to_plain_resolution(tmp_path: Path) -> None:
    """`x = Logger(); x.helper()` where `helper` isn't a Logger method but does
    exist as a plain same-file function -- the wrong-type guess must not block
    the fallback that a plain `?call:helper` edge would already have found."""
    db = _index(
        tmp_path,
        {
            "app.py": (
                "class Logger:\n"
                "    def log(self):\n"
                "        pass\n\n"
                "def helper():\n"
                "    pass\n\n"
                "def use():\n"
                "    lg = Logger()\n"
                "    lg.helper()\n"
            ),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("py:app.py:use", "py:app.py:helper") in resolved


# ---------- TypeScript: pure parser unit tests (no DB) ----------


def _ts_call_edges(source: str, src_suffix: str = ""):
    result = TypeScriptParser().parse(Path("app.ts"), source)
    edges = [e for e in result.edges if e.type == "calls"]
    if src_suffix:
        edges = [e for e in edges if e.src_id.endswith(src_suffix)]
    return edges


def test_ts_this_call_infers_enclosing_class_as_receiver_type() -> None:
    edges = _ts_call_edges(
        "class Widget {\n  render() {}\n  draw() {\n    this.render();\n  }\n}\n",
        src_suffix="Widget.draw",
    )
    assert len(edges) == 1
    assert edges[0].dst_id == "ts:?methodcall:Widget.render"


def test_ts_new_expression_local_variable_infers_type() -> None:
    edges = _ts_call_edges(
        "class Logger {\n"
        "  log() {}\n"
        "}\n"
        "function use() {\n"
        "  const lg = new Logger();\n"
        "  lg.log();\n"
        "}\n",
        src_suffix=":use",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "ts:?methodcall:Logger.log"


def test_ts_annotated_local_variable_infers_type() -> None:
    edges = _ts_call_edges(
        "class Logger {\n"
        "  log() {}\n"
        "}\n"
        "function use() {\n"
        "  const lg: Logger = getLogger();\n"
        "  lg.log();\n"
        "}\n",
        src_suffix=":use",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "ts:?methodcall:Logger.log"


def test_ts_typed_parameter_infers_type() -> None:
    edges = _ts_call_edges(
        "class Service {\n"
        "  notify() {}\n"
        "}\n"
        "class Caller {\n"
        "  use(svc: Service) {\n"
        "    svc.notify();\n"
        "  }\n"
        "}\n",
        src_suffix="Caller.use",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "ts:?methodcall:Service.notify"


def test_ts_field_declaration_type_resolves_from_other_method() -> None:
    edges = _ts_call_edges(
        "class Service {\n"
        "  save() {}\n"
        "}\n"
        "class Caller {\n"
        "  private svc: Service;\n"
        "  constructor() {\n"
        "    this.svc = new Service();\n"
        "  }\n"
        "  run() {\n"
        "    this.svc.save();\n"
        "  }\n"
        "}\n",
        src_suffix="Caller.run",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "ts:?methodcall:Service.save"


def test_ts_generic_type_annotation_is_not_treated_as_a_receiver_type() -> None:
    edges = _ts_call_edges(
        "function use() {\n  const items: Array<Logger> = getItems();\n  items.push(1);\n}\n",
        src_suffix=":use",
    )
    dst_ids = {e.dst_id for e in edges}
    assert "ts:?call:push" in dst_ids
    assert not any("?methodcall:" in dst for dst in dst_ids)


# ---------- TypeScript: integration ----------


def test_ts_resolves_to_correct_class_when_two_files_share_a_method_name(tmp_path: Path) -> None:
    db = _index(
        tmp_path,
        {
            "file_a.ts": (
                "export class Logger {\n"
                "  log() {}\n"
                "}\n"
                "function use() {\n"
                "  const lg = new Logger();\n"
                "  lg.log();\n"
                "}\n"
            ),
            "file_b.ts": ("export class Logger {\n  log() {}\n}\n"),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("ts:file_a.ts:use", "ts:file_a.ts:Logger.log") in resolved
    assert ("ts:file_a.ts:use", "ts:file_b.ts:Logger.log") not in resolved


# ---------- Java: pure parser unit tests (no DB) ----------


def _java_call_edges(source: str, src_suffix: str = ""):
    result = JavaParser().parse(Path("T.java"), source)
    edges = [e for e in result.edges if e.type == "calls"]
    if src_suffix:
        edges = [e for e in edges if e.src_id.endswith(src_suffix)]
    return edges


def test_java_this_call_infers_enclosing_class_as_receiver_type() -> None:
    edges = _java_call_edges(
        "class Widget {\n  void render() {}\n  void draw() {\n    this.render();\n  }\n}\n",
        src_suffix="Widget.draw",
    )
    assert len(edges) == 1
    assert edges[0].dst_id == "java:?methodcall:Widget.render"


def test_java_object_creation_local_variable_infers_type() -> None:
    edges = _java_call_edges(
        "class Logger {\n"
        "  void log() {}\n"
        "}\n"
        "class T {\n"
        "  void use() {\n"
        "    Logger lg = new Logger();\n"
        "    lg.log();\n"
        "  }\n"
        "}\n",
        src_suffix="T.use",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "java:?methodcall:Logger.log"


def test_java_typed_parameter_infers_type() -> None:
    edges = _java_call_edges(
        "class Service {\n"
        "  void notify() {}\n"
        "}\n"
        "class Caller {\n"
        "  void use(Service svc) {\n"
        "    svc.notify();\n"
        "  }\n"
        "}\n",
        src_suffix="Caller.use",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "java:?methodcall:Service.notify"


def test_java_field_declaration_type_resolves_from_other_method() -> None:
    edges = _java_call_edges(
        "class Service {\n"
        "  void save() {}\n"
        "}\n"
        "class Caller {\n"
        "  private Service svc;\n"
        "  Caller() {\n"
        "    this.svc = new Service();\n"
        "  }\n"
        "  void run() {\n"
        "    this.svc.save();\n"
        "  }\n"
        "}\n",
        src_suffix="Caller.run",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "java:?methodcall:Service.save"


def test_java_var_local_infers_type_from_object_creation() -> None:
    edges = _java_call_edges(
        "class Logger {\n"
        "  void log() {}\n"
        "}\n"
        "class T {\n"
        "  void use() {\n"
        "    var lg = new Logger();\n"
        "    lg.log();\n"
        "  }\n"
        "}\n",
        src_suffix="T.use",
    )
    method_calls = [e for e in edges if "?methodcall:" in e.dst_id]
    assert len(method_calls) == 1
    assert method_calls[0].dst_id == "java:?methodcall:Logger.log"


# ---------- Java: integration ----------


def test_java_resolves_to_correct_class_when_two_files_share_a_method_name(
    tmp_path: Path,
) -> None:
    db = _index(
        tmp_path,
        {
            "FileA.java": (
                "class Logger {\n"
                "  void log() {}\n"
                "}\n"
                "class T {\n"
                "  void use() {\n"
                "    Logger lg = new Logger();\n"
                "    lg.log();\n"
                "  }\n"
                "}\n"
            ),
            "FileB.java": ("class Logger {\n  void log() {}\n}\n"),
        },
    )
    store = GraphStore(db)
    try:
        edges = _edges(store)
    finally:
        store.close()
    resolved = {(src, dst) for src, dst, _ in edges}
    assert ("java:FileA.java:T.use", "java:FileA.java:Logger.log") in resolved
    assert ("java:FileA.java:T.use", "java:FileB.java:Logger.log") not in resolved
