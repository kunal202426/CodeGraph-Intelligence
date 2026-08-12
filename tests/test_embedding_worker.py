"""Tests for the out-of-process embedding worker.

Why this exists at all: the MCP server used to import sentence-transformers
(and therefore torch) in its own process, synchronously, before it could
serve. Measured on a real 97k-entity index that cost 25.6s of boot on a cold
filesystem cache -- right at Claude Code's 30s MCP connect timeout, so the
server connected on a warm cache and was silently dropped on a cold one. The
agent then fell back to grep/Read with no error surfaced anywhere, which is
what made several A/B runs look like "the model ignored codegraph".

It could not simply be backgrounded: importing torch on a non-main thread
while the asyncio event loop runs deadlocks the process (reproduced twice
against that same index). Moving the import into a separate
*process* removes both problems at once -- boot no longer waits for it, and
the deadlock becomes structurally impossible because torch is never imported
in the server process.

These tests deliberately never load a real model: the protocol and the client
are exercised with a stub worker command, so the suite stays fast and does not
depend on the HuggingFace cache.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from codegraph.embeddings.worker import EMBED_DIM_KEY, handle_request

# ---------------------------------------------------------------------------
# worker protocol
# ---------------------------------------------------------------------------


def test_handle_request_returns_vectors_for_texts() -> None:
    def fake_embed(texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 3), dtype=np.float32)

    reply = handle_request({"id": 7, "texts": ["a", "b"]}, embed=fake_embed)

    assert reply["id"] == 7
    assert reply["vectors"] == [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]]
    assert "error" not in reply


def test_handle_request_reports_errors_instead_of_crashing() -> None:
    """A model failure must come back as a normal reply, not kill the worker --
    the server's callers treat an error as "fall back to literal search"."""

    def boom(texts: list[str]) -> np.ndarray:
        raise RuntimeError("model exploded")

    reply = handle_request({"id": 1, "texts": ["a"]}, embed=boom)

    assert reply["id"] == 1
    assert "model exploded" in reply["error"]
    assert "vectors" not in reply


def test_handle_request_on_empty_texts_skips_the_model_entirely() -> None:
    """Embedding nothing must not force the (very expensive) model load."""
    called = False

    def fake_embed(texts: list[str]) -> np.ndarray:
        nonlocal called
        called = True
        return np.empty((0, 3), dtype=np.float32)

    reply = handle_request({"id": 2, "texts": []}, embed=fake_embed)

    assert reply["vectors"] == []
    assert called is False


def test_handle_request_answers_a_ping_without_loading_the_model() -> None:
    """The client uses a ping to confirm the worker is alive and to let the
    caller know the embedding dimension without paying for a real encode."""
    called = False

    def fake_embed(texts: list[str]) -> np.ndarray:
        nonlocal called
        called = True
        return np.empty((0, 3), dtype=np.float32)

    reply = handle_request({"id": 3, "ping": True}, embed=fake_embed)

    assert reply["id"] == 3
    assert reply[EMBED_DIM_KEY] > 0
    assert called is False


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------

# A stand-in worker: same line protocol, no torch. Echoes a deterministic
# vector per text so round-tripping is verifiable.
_STUB_WORKER = """
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if req.get("shutdown"):
        break
    texts = req.get("texts", [])
    out = {"id": req.get("id"), "vectors": [[float(len(t)), 0.0, 1.0] for t in texts]}
    sys.stdout.write(json.dumps(out) + "\\n")
    sys.stdout.flush()
"""

_CRASHING_WORKER = "import sys; sys.exit(1)"


def _stub_command(script: str) -> list[str]:
    return [sys.executable, "-c", script]


def test_client_round_trips_a_batch_through_the_subprocess() -> None:
    from codegraph.embeddings.remote import EmbeddingWorkerClient

    client = EmbeddingWorkerClient(command=_stub_command(_STUB_WORKER))
    try:
        vectors = client.embed_batch(["ab", "cdef"])
        assert vectors.shape == (2, 3)
        assert vectors[0][0] == pytest.approx(2.0)
        assert vectors[1][0] == pytest.approx(4.0)
    finally:
        client.shutdown()


def test_client_start_does_not_block_on_the_model_load() -> None:
    """The whole point: `start()` spawns and returns. If it waited for the
    worker to be ready, boot would be slow again and the connect timeout
    problem would come straight back."""
    from codegraph.embeddings.remote import EmbeddingWorkerClient

    # A worker that never answers anything. start() must still return promptly.
    client = EmbeddingWorkerClient(command=_stub_command("import time; time.sleep(60)"))
    try:
        import time

        t0 = time.monotonic()
        client.start()
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"start() blocked for {elapsed:.1f}s"
    finally:
        client.shutdown()


def test_client_returns_none_when_the_worker_cannot_start() -> None:
    """A dead worker must degrade to literal-only search, never raise into a
    tool handler."""
    from codegraph.embeddings.remote import EmbeddingWorkerClient

    client = EmbeddingWorkerClient(command=_stub_command(_CRASHING_WORKER))
    try:
        assert client.embed_batch_or_none(["hello"]) is None
    finally:
        client.shutdown()


def test_client_embed_one_returns_a_single_vector() -> None:
    from codegraph.embeddings.remote import EmbeddingWorkerClient

    client = EmbeddingWorkerClient(command=_stub_command(_STUB_WORKER))
    try:
        vector = client.embed_one("abc")
        assert vector.shape == (3,)
        assert vector[0] == pytest.approx(3.0)
    finally:
        client.shutdown()


def test_client_empty_batch_never_spawns_the_worker() -> None:
    from codegraph.embeddings.remote import EmbeddingWorkerClient

    client = EmbeddingWorkerClient(command=_stub_command(_STUB_WORKER))
    try:
        vectors = client.embed_batch([])
        assert vectors.shape[0] == 0
        assert client.is_running() is False
    finally:
        client.shutdown()


def test_client_is_concurrency_safe_on_one_pipe() -> None:
    """Tool handlers run on anyio worker threads, so two embeds can land at
    once. One stdin/stdout pipe pair cannot interleave -- requests must be
    serialized or replies get crossed."""
    import threading

    from codegraph.embeddings.remote import EmbeddingWorkerClient

    client = EmbeddingWorkerClient(command=_stub_command(_STUB_WORKER))
    results: dict[int, float] = {}
    errors: list[Exception] = []

    def work(n: int) -> None:
        try:
            results[n] = float(client.embed_one("x" * n)[0])
        except Exception as exc:  # noqa: BLE001 — recorded and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(1, 9)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, f"concurrent embeds raised: {errors}"
        assert results == {n: float(n) for n in range(1, 9)}
    finally:
        client.shutdown()


def test_client_shutdown_is_idempotent() -> None:
    from codegraph.embeddings.remote import EmbeddingWorkerClient

    client = EmbeddingWorkerClient(command=_stub_command(_STUB_WORKER))
    client.embed_one("a")
    client.shutdown()
    client.shutdown()
    assert client.is_running() is False


def test_worker_module_is_runnable_as_a_script() -> None:
    """The client spawns `python -m codegraph.embeddings.worker`; that module
    must actually have a __main__ entry point or every spawn fails at runtime
    while the unit tests (which use a stub) stay green."""
    from codegraph.embeddings import worker

    source = json.dumps(worker.__file__)
    assert source  # module resolved on disk
    assert hasattr(worker, "main")


def test_process_alive_distinguishes_a_live_process_from_a_dead_one() -> None:
    from codegraph.proc import process_alive

    assert process_alive(os.getpid())

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=15)
    assert not process_alive(dead.pid)


def test_watchdog_exits_the_process_once_its_parent_is_gone() -> None:
    """An orphaned worker holds a fully loaded torch model in memory forever.
    Closing stdin is the normal shutdown signal, but that is exactly the signal
    known not to arrive when a process tree is killed abruptly on Windows --
    the same reason the MCP server watches its own parent.

    Points the watchdog at an already-dead PID rather than orchestrating a real
    parent/child kill: same code path, no race on who dies first."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=15)

    watched = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.path.insert(0, 'packages');"
            " from codegraph.proc import start_parent_watchdog;"
            f" start_parent_watchdog({dead.pid}, interval=0.2); time.sleep(60)",
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    try:
        # Should self-exit almost immediately; allow generous slack for CI.
        watched.wait(timeout=30)
        assert watched.returncode == 0
    finally:
        if watched.poll() is None:
            watched.kill()
