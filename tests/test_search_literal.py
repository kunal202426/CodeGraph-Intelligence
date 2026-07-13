"""Tests for graph/queries.py::search_literal -- ranking + re-ranking behaviour."""

from __future__ import annotations

from codegraph.graph.queries import search_literal
from codegraph.graph.store import GraphStore
from codegraph.uir import EntityType, Language, UIREntity, hash_source


def _entity(entity_id: str, name: str, file: str, docstring: str | None = None) -> UIREntity:
    return UIREntity(
        entity_id=entity_id,
        type=EntityType.FUNCTION,
        name=name,
        qualified_name=name,
        language=Language.PYTHON,
        file=file,
        start_line=1,
        end_line=2,
        raw_source=f"def {name}(): ...\n",
        docstring=docstring,
        hash=hash_source(name),
    )


def _store(tmp_path, entities: list[UIREntity]) -> GraphStore:
    store = GraphStore(tmp_path / "g.duckdb")
    store.init_schema()
    for file in {e.file for e in entities}:
        store.upsert_file(file, Language.PYTHON, "h", loc=1)
    store.upsert_entities(entities)
    return store


# ---------- unchanged single-term tiering ----------


def test_exact_name_match_ranks_first(tmp_path) -> None:
    store = _store(
        tmp_path,
        [
            _entity("py:a.py:authenticate", "authenticate", "a.py"),
            _entity("py:a.py:authenticate_user", "authenticate_user", "a.py"),
        ],
    )
    try:
        hits = search_literal(store.conn, "authenticate", limit=10)
        assert hits[0].name == "authenticate"
    finally:
        store.close()


def test_query_with_no_terms_returns_empty(tmp_path) -> None:
    store = _store(tmp_path, [_entity("py:a.py:foo", "foo", "a.py")])
    try:
        assert search_literal(store.conn, "", limit=10) == []
    finally:
        store.close()


def test_single_underscored_identifier_query_no_false_positives(tmp_path) -> None:
    """Regression: a single identifier-like query must not fragment into
    words that match unrelated entities (e.g. 'a' matching almost anything)."""
    store = _store(tmp_path, [_entity("py:a.py:render", "render", "a.py")])
    try:
        hits = search_literal(store.conn, "zzz_not_a_real_symbol", limit=10)
        assert hits == []
    finally:
        store.close()


# ---------- multi-term co-occurrence ----------


def test_multi_term_query_finds_camel_case_compound_match(tmp_path) -> None:
    """'state machine' must find `OrderStateMachine` even though the words
    aren't a literal substring -- only visible via identifier segmentation."""
    store = _store(
        tmp_path,
        [
            _entity("py:a.py:OrderStateMachine", "OrderStateMachine", "a.py"),
            _entity("py:a.py:unrelated_thing", "unrelated_thing", "a.py"),
        ],
    )
    try:
        hits = search_literal(store.conn, "state machine", limit=10)
        names = [h.name for h in hits]
        assert "OrderStateMachine" in names
    finally:
        store.close()


def test_multi_term_full_corroboration_outranks_partial(tmp_path) -> None:
    store = _store(
        tmp_path,
        [
            _entity("py:a.py:OrderStateMachine", "OrderStateMachine", "a.py"),
            _entity("py:a.py:StateHolder", "StateHolder", "a.py"),
        ],
    )
    try:
        hits = search_literal(store.conn, "state machine", limit=10)
        # OrderStateMachine matches both terms; StateHolder matches only "state".
        assert hits[0].name == "OrderStateMachine"
    finally:
        store.close()


# ---------- test-file / generated-file down-ranking ----------


def test_test_file_ranked_below_implementation_on_equal_name(tmp_path) -> None:
    store = _store(
        tmp_path,
        [
            _entity("py:tests/test_widget.py:Widget", "Widget", "tests/test_widget.py"),
            _entity("py:src/widget.py:Widget", "Widget", "src/widget.py"),
        ],
    )
    try:
        hits = search_literal(store.conn, "widget", limit=10)
        assert hits[0].file == "src/widget.py"
    finally:
        store.close()


def test_test_query_does_not_demote_test_files(tmp_path) -> None:
    """A query that's explicitly about tests shouldn't penalize test files."""
    store = _store(
        tmp_path,
        [
            _entity(
                "py:tests/test_widget.py:test_widget_renders",
                "test_widget_renders",
                "tests/test_widget.py",
            ),
        ],
    )
    try:
        hits = search_literal(store.conn, "widget test", limit=10)
        assert any(h.name == "test_widget_renders" for h in hits)
    finally:
        store.close()


def test_generated_file_ranked_below_hand_written_on_equal_name(tmp_path) -> None:
    store = _store(
        tmp_path,
        [
            _entity("py:api/service_pb2.py:Send", "Send", "api/service_pb2.py"),
            _entity("py:api/service.py:Send", "Send", "api/service.py"),
        ],
    )
    try:
        hits = search_literal(store.conn, "send", limit=10)
        assert hits[0].file == "api/service.py"
    finally:
        store.close()
