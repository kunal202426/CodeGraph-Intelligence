"""Tests for the local embedding wrapper (T3.1).

These require the `all-MiniLM-L6-v2` model. If it can't be loaded (no network +
not cached), the whole module is skipped rather than failing — embeddings are
an external dependency we don't want gating CI on a flaky HF download.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest
from codegraph.embeddings.pipeline import (
    EMBEDDING_DIM,
    _acquire_with_breadcrumb,
    _hf_cache_has_model,
    embed_batch,
    embed_one,
)


def test_hf_cache_has_model_detects_presence(tmp_path, monkeypatch) -> None:
    """The filesystem cache probe (used to decide offline mode) finds a model
    folder when present and reports absence otherwise."""
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(tmp_path))
    monkeypatch.delenv("HF_HOME", raising=False)

    assert _hf_cache_has_model("all-MiniLM-L6-v2") is False
    (tmp_path / "models--sentence-transformers--all-MiniLM-L6-v2").mkdir()
    assert _hf_cache_has_model("all-MiniLM-L6-v2") is True


# ---------- lock-wait visibility ----------
#
# Regression: a concurrent caller used to block on `_model_lock` with zero
# feedback while another thread (the background boot warmup, or a second
# concurrent MCP request) was already loading the model -- indistinguishable
# from a hang from the caller's side. Confirmed live: the first real
# `get_context` after server startup can wait on this lock for 40+ seconds
# with nothing printed anywhere. `_acquire_with_breadcrumb` makes that wait
# observable on stderr instead of silent.


def test_acquire_with_breadcrumb_uncontended_acquires_silently(capsys) -> None:
    lock = threading.Lock()
    _acquire_with_breadcrumb(lock, "should not print", poll_timeout=0.05)
    try:
        assert capsys.readouterr().err == ""
    finally:
        lock.release()


def test_acquire_with_breadcrumb_prints_while_waiting_on_a_held_lock(capsys) -> None:
    lock = threading.Lock()
    lock.acquire()  # simulate another thread already holding it

    done = threading.Event()

    def _waiter() -> None:
        _acquire_with_breadcrumb(lock, "CodeGraph: waiting for it", poll_timeout=0.05)
        lock.release()
        done.set()

    t = threading.Thread(target=_waiter, daemon=True)
    t.start()
    time.sleep(0.2)  # well past poll_timeout, before the main thread releases

    assert not done.is_set(), "waiter should still be blocked on the held lock"
    assert "CodeGraph: waiting for it" in capsys.readouterr().err

    lock.release()
    t.join(timeout=2.0)
    assert done.is_set()


@pytest.fixture(scope="module", autouse=True)
def _model_available() -> None:
    """Skip the module if the embedding model can't be loaded."""
    try:
        embed_one("warmup")
    except Exception as exc:  # noqa: BLE001 - any load/download failure → skip, don't fail
        pytest.skip(f"embedding model unavailable: {exc}")


# ---------- shape / dtype ----------


def test_embed_batch_shape_and_dtype() -> None:
    vecs = embed_batch(["user authentication", "validate credentials"])
    assert vecs.shape == (2, EMBEDDING_DIM)
    assert vecs.dtype == np.float32


def test_embed_one_shape() -> None:
    vec = embed_one("a single function")
    assert vec.shape == (EMBEDDING_DIM,)
    assert vec.dtype == np.float32


def test_empty_batch_returns_empty_array_without_model() -> None:
    vecs = embed_batch([])
    assert vecs.shape == (0, EMBEDDING_DIM)
    assert vecs.dtype == np.float32


# ---------- properties ----------


def test_vectors_are_unit_normalized() -> None:
    vecs = embed_batch(["alpha", "beta gamma", "the quick brown fox"])
    norms = np.linalg.norm(vecs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4)


def test_embedding_is_deterministic() -> None:
    a = embed_one("def authenticate(email, password): ...")
    b = embed_one("def authenticate(email, password): ...")
    assert np.allclose(a, b, atol=1e-5)


def test_similar_texts_more_similar_than_unrelated() -> None:
    auth = embed_one("validate user credentials and create a session")
    login = embed_one("authenticate a user and start a login session")
    banana = embed_one("a recipe for a tropical fruit smoothie")
    # Normalized vectors → cosine similarity == dot product.
    assert float(auth @ login) > float(auth @ banana)
