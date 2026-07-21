"""Tests for graph/ranking.py -- identifier segmentation and path classifiers."""

from __future__ import annotations

from codegraph.graph.ranking import (
    bounded_edit_distance,
    extract_search_terms,
    is_generated_path,
    is_test_path,
    significant_terms,
    split_identifier_segments,
)

# ---------- split_identifier_segments ----------


def test_splits_camel_case() -> None:
    assert split_identifier_segments("OrderStateMachine") == ["order", "state", "machine"]


def test_splits_snake_case() -> None:
    assert split_identifier_segments("get_user_id") == ["get", "user", "id"]


def test_splits_acronym_boundary() -> None:
    assert split_identifier_segments("XMLHttpRequest") == ["xml", "http", "request"]


def test_splits_kebab_case() -> None:
    assert split_identifier_segments("user-profile-view") == ["user", "profile", "view"]


def test_empty_name_returns_empty_list() -> None:
    assert split_identifier_segments("") == []


# ---------- extract_search_terms / significant_terms ----------


def test_extract_terms_splits_on_whitespace_only() -> None:
    assert extract_search_terms("state machine") == ["state", "machine"]


def test_extract_terms_keeps_underscored_identifier_as_one_term() -> None:
    """A single identifier-like query must not explode into fragments --
    that was a real regression: 'zzz_not_a_real_symbol' splitting into
    ['zzz','not','a','real','symbol'] made 'a' match almost every name."""
    assert extract_search_terms("zzz_not_a_real_symbol") == ["zzz_not_a_real_symbol"]


def test_significant_terms_drops_stopwords_and_short_tokens() -> None:
    terms = extract_search_terms("how does the state machine work")
    assert significant_terms(terms) == ["state", "machine", "work"]


def test_significant_terms_keeps_meaningful_short_terms() -> None:
    assert significant_terms(["id", "db"]) == ["id", "db"]


# ---------- is_test_path ----------


def test_python_test_file_detected() -> None:
    assert is_test_path("tests/test_login.py")
    assert is_test_path("src/auth/login_test.py")


def test_js_spec_file_detected() -> None:
    assert is_test_path("src/components/Button.spec.tsx")
    assert is_test_path("src/__tests__/Button.test.tsx")


def test_java_test_suffix_detected() -> None:
    assert is_test_path("src/test/java/com/app/UserServiceTest.java")
    assert is_test_path("com/app/UserServiceIT.java")


def test_go_test_file_detected() -> None:
    assert is_test_path("pkg/server/handler_test.go")


def test_regular_source_file_not_flagged_as_test() -> None:
    assert not is_test_path("src/auth/login.py")
    assert not is_test_path("com/app/UserService.java")
    assert not is_test_path("src/components/Button.tsx")


# ---------- is_generated_path ----------


def test_protobuf_generated_files_detected() -> None:
    assert is_generated_path("api/v1/service.pb.go")
    assert is_generated_path("api/v1/service_pb2.py")
    assert is_generated_path("api/v1/service_grpc.pb.go")


def test_dart_freezed_generated_file_detected() -> None:
    assert is_generated_path("lib/models/user.freezed.dart")
    assert is_generated_path("lib/models/user.g.dart")


def test_hand_written_file_not_flagged_as_generated() -> None:
    assert not is_generated_path("api/v1/service.go")
    assert not is_generated_path("lib/models/user.dart")


# ---------- bounded_edit_distance ----------


def test_identical_strings_have_zero_distance() -> None:
    assert bounded_edit_distance("authenticate", "authenticate", 2) == 0


def test_one_missing_character_has_distance_one() -> None:
    assert bounded_edit_distance("authentcate", "authenticate", 2) == 1


def test_one_substitution_has_distance_one() -> None:
    assert bounded_edit_distance("authenticata", "authenticate", 2) == 1


def test_distance_exceeding_max_returns_none() -> None:
    assert bounded_edit_distance("cat", "dog", 2) is None


def test_length_difference_alone_can_exceed_max() -> None:
    assert bounded_edit_distance("", "abc", 2) is None


def test_two_substitutions_within_bound() -> None:
    assert bounded_edit_distance("ab", "ba", 2) == 2
