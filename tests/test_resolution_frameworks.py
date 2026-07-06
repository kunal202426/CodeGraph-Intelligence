"""Tests for framework-aware call resolution (Phase 20).

Flask/FastAPI (Python, decorator-based), Express (TS/JS, call-based),
Django (Python, urlpatterns list-based), Spring (Java, annotation-based),
and Rails (Ruby, routes-DSL-based).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.analysis.refactor import find_dead_code
from codegraph.cli import app
from codegraph.graph.queries import find_callers, find_entity_by_name_or_id
from codegraph.graph.store import GraphStore
from codegraph.parsers.java import JavaParser
from codegraph.parsers.python import PythonParser
from codegraph.parsers.ruby import RubyParser
from codegraph.parsers.typescript import TypeScriptParser
from typer.testing import CliRunner

# ---------- pure parser unit tests (no DB) ----------


def _route_edges(source: str):
    result = PythonParser().parse(Path("app.py"), source)
    return [e for e in result.edges if e.type == "calls" and e.src_id.startswith("route:")]


def test_fastapi_get_shortcut_produces_route_edge() -> None:
    edges = _route_edges(
        "from fastapi import FastAPI\napp = FastAPI()\n\n"
        '@app.get("/users")\ndef list_users():\n    pass\n'
    )
    assert len(edges) == 1
    assert edges[0].src_id == "route:GET /users"
    assert edges[0].dst_id == "py:app.py:list_users"
    assert edges[0].is_dynamic is True
    assert 0.0 < edges[0].confidence < 1.0


def test_fastapi_post_shortcut_uses_post_method() -> None:
    edges = _route_edges(
        "from fastapi import FastAPI\napp = FastAPI()\n\n"
        '@app.post("/users")\ndef create_user():\n    pass\n'
    )
    assert edges[0].src_id == "route:POST /users"


def test_flask_route_defaults_to_get() -> None:
    edges = _route_edges(
        "from flask import Flask\napp = Flask(__name__)\n\n"
        '@app.route("/health")\ndef health():\n    pass\n'
    )
    assert edges[0].src_id == "route:GET /health"


def test_flask_route_methods_kwarg_produces_one_edge_per_method() -> None:
    edges = _route_edges(
        "from flask import Flask\napp = Flask(__name__)\n\n"
        '@app.route("/items", methods=["POST", "PUT"])\ndef handle_items():\n    pass\n'
    )
    dsts = {(e.src_id) for e in edges}
    assert dsts == {"route:POST /items", "route:PUT /items"}
    assert all(e.dst_id == "py:app.py:handle_items" for e in edges)


def test_undecorated_function_has_no_route_edge() -> None:
    edges = _route_edges("def plain():\n    return 1\n")
    assert edges == []


def test_unrelated_decorator_has_no_route_edge() -> None:
    """A decorator that isn't a call (e.g. @staticmethod) must not match."""
    edges = _route_edges("class C:\n    @staticmethod\n    def m():\n        pass\n")
    assert edges == []


def test_non_http_method_call_decorator_ignored() -> None:
    """@cache.memoize(...) or similar unrelated `.method(...)` decorators
    must not be mistaken for a route -- only known HTTP verbs + `route`."""
    edges = _route_edges(
        "cache = object()\n\n@cache.memoize(timeout=60)\ndef compute():\n    pass\n"
    )
    assert edges == []


# ---------- Express (TS/JS) pure parser unit tests ----------


def _express_route_edges(source: str):
    result = TypeScriptParser().parse(Path("app.ts"), source)
    return [e for e in result.edges if e.type == "calls" and e.src_id.startswith("route:")]


def test_express_get_with_named_handler() -> None:
    edges = _express_route_edges(
        'function listUsers(req, res) {\n    return [];\n}\n\napp.get("/users", listUsers);\n'
    )
    assert len(edges) == 1
    assert edges[0].src_id == "route:GET /users"
    assert edges[0].dst_id == "ts:app.ts:listUsers"
    assert edges[0].is_dynamic is True


def test_express_router_post_with_named_handler() -> None:
    edges = _express_route_edges(
        "function createUser(req, res) {\n    return {};\n}\n\nrouter.post('/users', createUser);\n"
    )
    assert edges[0].src_id == "route:POST /users"
    assert edges[0].dst_id == "ts:app.ts:createUser"


def test_express_inline_arrow_handler_produces_no_edge() -> None:
    """An inline handler has no separate entity to link to -- skipped, not mis-parsed."""
    edges = _express_route_edges('app.get("/inline", (req, res) => {});\n')
    assert edges == []


def test_express_handler_not_defined_in_file_produces_provisional_edge() -> None:
    """A handler not found in this file gets a provisional edge for the
    cross-file resolution pass, not silently dropped."""
    edges = _express_route_edges('app.get("/imported", externalHandler);\n')
    assert len(edges) == 1
    assert edges[0].src_id == "route:GET /imported"
    assert edges[0].dst_id == "route:?handler:externalHandler"


def test_express_non_http_method_call_ignored() -> None:
    """`.use(...)` and other non-REST-verb calls aren't treated as routes."""
    edges = _express_route_edges("function mw(req, res, next) {\n    next();\n}\n\napp.use(mw);\n")
    assert edges == []


# ---------- Django (Python) pure parser unit tests ----------


def test_django_path_with_bare_identifier_view() -> None:
    edges = _route_edges(
        "from django.urls import path\n\n"
        "def home_view(request):\n    pass\n\n"
        'urlpatterns = [\n    path("home/", home_view),\n]\n'
    )
    assert len(edges) == 1
    assert edges[0].src_id == "route:ANY /home"
    assert edges[0].dst_id == "py:app.py:home_view"


def test_django_path_ignores_trailing_name_kwarg() -> None:
    """`name="about"` must not be mistaken for the view argument."""
    edges = _route_edges(
        "from django.urls import path\n\n"
        "def about_view(request):\n    pass\n\n"
        'urlpatterns = [\n    path("about/", about_view, name="about"),\n]\n'
    )
    assert edges[0].dst_id == "py:app.py:about_view"


def test_django_path_with_dotted_view_reference() -> None:
    """`views.home_view` resolves on the final segment, `home_view`."""
    edges = _route_edges(
        "from django.urls import path\nfrom . import views\n\n"
        "def home_view(request):\n    pass\n\n"
        'urlpatterns = [\n    path("home/", views.home_view),\n]\n'
    )
    assert edges[0].dst_id == "py:app.py:home_view"


def test_django_path_class_based_view_resolves_to_class() -> None:
    edges = _route_edges(
        "from django.urls import path\n\n"
        "class ProfileView:\n    def get(self, request):\n        pass\n\n"
        'urlpatterns = [\n    path("profile/", ProfileView.as_view()),\n]\n'
    )
    assert edges[0].dst_id == "py:app.py:ProfileView"


def test_django_re_path_also_matches() -> None:
    edges = _route_edges(
        "from django.urls import re_path\n\n"
        "def old_view(request):\n    pass\n\n"
        r"urlpatterns = ["
        "\n"
        r'    re_path(r"^old/$", old_view),'
        "\n"
        "]\n"
    )
    assert len(edges) == 1
    assert edges[0].dst_id == "py:app.py:old_view"


def test_django_unresolvable_view_produces_provisional_edge() -> None:
    """A view not found in this file (the common Django shape) gets a
    provisional edge for the cross-file resolution pass, not silently dropped."""
    edges = _route_edges(
        "from django.urls import path\n\n"
        'urlpatterns = [\n    path("missing/", not_defined_here),\n]\n'
    )
    assert len(edges) == 1
    assert edges[0].src_id == "route:ANY /missing"
    assert edges[0].dst_id == "route:?handler:not_defined_here"


# ---------- Spring (Java) pure parser unit tests ----------


def _java_route_edges(source: str):
    result = JavaParser().parse(Path("C.java"), source)
    return [e for e in result.edges if e.type == "calls" and e.src_id.startswith("route:")]


def test_spring_get_mapping_with_class_base_path() -> None:
    edges = _java_route_edges(
        '@RequestMapping("/api")\n'
        "public class C {\n"
        '    @GetMapping("/users")\n'
        "    public void listUsers() {}\n"
        "}\n"
    )
    assert len(edges) == 1
    assert edges[0].src_id == "route:GET /api/users"
    assert edges[0].dst_id == "java:C.java:C.listUsers"
    assert edges[0].is_dynamic is True


def test_spring_get_mapping_without_class_base_path() -> None:
    edges = _java_route_edges(
        'public class C {\n    @GetMapping("/users")\n    public void listUsers() {}\n}\n'
    )
    assert edges[0].src_id == "route:GET /users"


def test_spring_request_mapping_with_method_kwarg() -> None:
    edges = _java_route_edges(
        "public class C {\n"
        '    @RequestMapping(value = "/users", method = RequestMethod.POST)\n'
        "    public void createUser() {}\n"
        "}\n"
    )
    assert edges[0].src_id == "route:POST /users"


def test_spring_request_mapping_without_method_defaults_to_any() -> None:
    edges = _java_route_edges(
        'public class C {\n    @RequestMapping("/users")\n    public void anyMethod() {}\n}\n'
    )
    assert edges[0].src_id == "route:ANY /users"


def test_spring_unmapped_method_has_no_route_edge() -> None:
    edges = _java_route_edges(
        'public class C {\n    @GetMapping("/users")\n    public void mapped() {}\n\n'
        "    public void notMapped() {}\n}\n"
    )
    assert len(edges) == 1
    assert edges[0].dst_id == "java:C.java:C.mapped"


# ---------- Rails (Ruby) pure parser unit tests ----------


def _ruby_route_edges(source: str):
    result = RubyParser().parse(Path("routes.rb"), source)
    return [e for e in result.edges if e.type == "calls" and e.src_id.startswith("route:")]


def test_rails_get_with_same_file_controller_action() -> None:
    edges = _ruby_route_edges(
        "class UsersController\n  def index\n  end\nend\n\n"
        "Rails.application.routes.draw do\n"
        "  get '/users', to: 'users#index'\n"
        "end\n"
    )
    assert len(edges) == 1
    assert edges[0].src_id == "route:GET /users"
    assert edges[0].dst_id == "rb:routes.rb:UsersController.index"
    assert edges[0].is_dynamic is True


def test_rails_post_action() -> None:
    edges = _ruby_route_edges(
        "class UsersController\n  def create\n  end\nend\n\n"
        "Rails.application.routes.draw do\n"
        "  post '/users', to: 'users#create'\n"
        "end\n"
    )
    assert edges[0].src_id == "route:POST /users"


def test_rails_controller_in_different_file_produces_provisional_edge() -> None:
    """The common real case -- controller lives in a different file -- gets a
    provisional edge for the cross-file resolution pass, not silently dropped."""
    edges = _ruby_route_edges(
        "Rails.application.routes.draw do\n  get '/users', to: 'users#index'\nend\n"
    )
    assert len(edges) == 1
    assert edges[0].src_id == "route:GET /users"
    assert edges[0].dst_id == "route:?handler:index"


def test_rails_route_without_to_pair_ignored() -> None:
    edges = _ruby_route_edges("Rails.application.routes.draw do\n  root 'welcome#index'\nend\n")
    assert edges == []


# ---------- HTTP client (TS/JS fetch/axios) pure parser unit tests ----------


def _http_edges(source: str):
    result = TypeScriptParser().parse(Path("client.ts"), source)
    return [e for e in result.edges if e.dst_id.startswith("route:?http:")]


def test_fetch_with_method_option() -> None:
    edges = _http_edges(
        'function submitForm() {\n    return fetch("/api/users", { method: "POST" });\n}\n'
    )
    assert len(edges) == 1
    assert edges[0].src_id == "ts:client.ts:submitForm"
    assert edges[0].dst_id == "route:?http:POST:/api/users"


def test_fetch_without_options_defaults_to_get() -> None:
    edges = _http_edges('function loadItems() {\n    return fetch("/api/items");\n}\n')
    assert edges[0].dst_id == "route:?http:GET:/api/items"


def test_fetch_template_literal_no_interpolation() -> None:
    edges = _http_edges("function loadItems() {\n    return fetch(`/api/items`);\n}\n")
    assert edges[0].dst_id == "route:?http:GET:/api/items"


def test_fetch_dynamic_url_produces_no_edge() -> None:
    """A URL built from interpolation can't be statically matched -- skipped."""
    edges = _http_edges("function loadUser(id) {\n    return fetch(`/api/users/${id}`);\n}\n")
    assert edges == []


def test_axios_get() -> None:
    edges = _http_edges('function loadOrders() {\n    return axios.get("/api/orders");\n}\n')
    assert edges[0].dst_id == "route:?http:GET:/api/orders"


def test_axios_unsupported_method_ignored() -> None:
    edges = _http_edges('function f() {\n    return axios.request("/api/x");\n}\n')
    assert edges == []


def test_fetch_call_attributed_to_containing_function_not_module() -> None:
    edges = _http_edges(
        "function unrelated() {\n    return 1;\n}\n\n"
        'function actuallyFetches() {\n    return fetch("/api/data");\n}\n'
    )
    assert len(edges) == 1
    assert edges[0].src_id == "ts:client.ts:actuallyFetches"


def test_top_level_fetch_attributed_to_module() -> None:
    edges = _http_edges('fetch("/api/data");\n')
    assert len(edges) == 1
    assert edges[0].src_id == "ts:client.ts:client"


# ---------- integration: real graph edges, not just deadcode exclusion ----------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_repo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _index(runner: CliRunner, repo: Path, db: Path) -> None:
    result = runner.invoke(app, ["index", str(repo), "--db", str(db), "--no-embed"])
    assert result.exit_code == 0, result.stdout


_FASTAPI_APP = {
    "app.py": (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        '@app.get("/users")\n'
        "def list_users():\n"
        "    return []\n"
        "\n"
        "def really_dead():\n"
        "    return 1\n"
    ),
}


def test_route_handler_has_a_real_caller_in_impact_analysis(
    runner: CliRunner, tmp_path: Path
) -> None:
    """The route registration shows up via find_callers, not just a deadcode
    exclusion -- impact_analysis/get_context now see a real inbound edge."""
    repo = tmp_path / "repo"
    _make_repo(repo, _FASTAPI_APP)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)

    store = GraphStore(db)
    try:
        matches = find_entity_by_name_or_id(store.conn, "list_users")
        assert matches
        handler_id = matches[0].entity_id
        tree = find_callers(store.conn, handler_id)
        callers = tree.callers.get(handler_id, [])
        assert any(c.entity_id == "route:GET /users" for c in callers)
    finally:
        store.close()


def test_route_handler_not_flagged_dead_via_real_edge(runner: CliRunner, tmp_path: Path) -> None:
    """deadcode excludes the handler because it has a real inbound edge now,
    not only via the pre-existing decorator-name heuristic."""
    repo = tmp_path / "repo"
    _make_repo(repo, _FASTAPI_APP)
    db = tmp_path / "g.duckdb"
    _index(runner, repo, db)

    store = GraphStore(db)
    try:
        names = {d.name for d in find_dead_code(store.conn)}
    finally:
        store.close()
    assert "list_users" not in names
    assert "really_dead" in names


# ---------- FastAPI Depends() dependency injection (found stress-testing a
# real production FastAPI backend) ----------


def test_fastapi_depends_produces_call_edge() -> None:
    """`current_user: User = Depends(get_current_user)` invokes get_current_user
    on every request -- but it's a parameter default, not a call expression in
    the body, so the ordinary call scan never sees it. Without this, every
    FastAPI dependency (auth checks, DB sessions, quota checks) looks unused;
    confirmed as the dominant false-positive source in a real backend."""
    source = (
        "from fastapi import Depends\n"
        "def get_db():\n"
        "    pass\n"
        "def get_current_user(db=Depends(get_db)):\n"
        "    pass\n"
        "def me(current_user=Depends(get_current_user)):\n"
        "    pass\n"
    )
    result = PythonParser().parse(Path("app.py"), source)
    call_edges = [e for e in result.edges if e.type == "calls"]
    me_edges = [e for e in call_edges if e.src_id.endswith(":me")]
    assert any(e.dst_id == "py:?call:get_current_user" for e in me_edges)


def test_fastapi_depends_is_not_dead_code(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from fastapi import Depends\n"
        "def get_current_user():\n"
        "    pass\n"
        "def me(current_user=Depends(get_current_user)):\n"
        "    pass\n",
        encoding="utf-8",
    )
    db = tmp_path / "g.duckdb"
    result = CliRunner().invoke(app, ["index", str(repo), "--db", str(db)])
    assert result.exit_code == 0, result.stdout
    store = GraphStore(db)
    try:
        names = {d.name for d in find_dead_code(store.conn)}
    finally:
        store.close()
    assert "get_current_user" not in names


def test_depends_with_non_identifier_argument_is_not_guessed() -> None:
    # `Depends(lambda: Service())` -- not a bare identifier, don't guess.
    source = "def me(db=Depends(lambda: Service())):\n    pass\n"
    result = PythonParser().parse(Path("app.py"), source)
    call_edges = [e for e in result.edges if e.type == "calls" and e.src_id.endswith(":me")]
    assert not any("Service" in e.dst_id or "lambda" in e.dst_id for e in call_edges)
