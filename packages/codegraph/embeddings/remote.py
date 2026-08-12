# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Client for the out-of-process embedding worker.

Drop-in replacement for `pipeline.embed_batch` / `pipeline.embed_one` in the
MCP server, with one critical difference: nothing here imports torch, so
starting it costs milliseconds instead of seconds and can never deadlock the
event loop. See `worker.py` for why that matters.

The CLI indexing path keeps calling `pipeline` directly -- it is a plain
synchronous program with no event loop and no connect timeout, so paying the
import cost in-process is fine (and avoids a pointless pipe hop for millions
of vectors).
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
from typing import Any

import numpy as np

from codegraph.embeddings.pipeline import EMBEDDING_DIM

# The model load is the long pole (~12.5s warm, ~23s cold on a first-run
# filesystem cache). A request that arrives while that is still in flight has
# to wait it out, so the ceiling is generous -- but bounded, because a hung
# worker must degrade to literal-only search rather than hang a tool call
# forever, which is exactly the failure mode this whole module exists to kill.
_REQUEST_TIMEOUT_SEC = 180.0


def _worker_command() -> list[str]:
    return [sys.executable, "-m", "codegraph.embeddings.worker"]


class EmbeddingWorkerClient:
    """Talks to one embedding worker subprocess over a JSON line protocol.

    Thread-safe: MCP tool handlers run on anyio worker threads, so two embeds
    can arrive concurrently, and a single stdin/stdout pair cannot interleave
    them. Every exchange is serialized under one lock.
    """

    def __init__(self, command: list[str] | None = None) -> None:
        self._command = command or _worker_command()
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._failed = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Spawn the worker if it isn't running. Returns as soon as the process
        exists -- deliberately does NOT wait for the model to finish loading.
        """
        with self._lock:
            self._ensure_started_locked()

    def _ensure_started_locked(self) -> bool:
        if self._process is not None and self._process.poll() is None:
            return True
        if self._failed:
            return False
        try:
            self._process = subprocess.Popen(  # noqa: S603 — fixed, internal command
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,  # inherit: worker progress/errors land in the MCP log
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except Exception:  # noqa: BLE001 — spawn failed: degrade, never raise
            self._process = None
            self._failed = True
            return False
        return True

    def is_running(self) -> bool:
        proc = self._process
        return proc is not None and proc.poll() is None

    def shutdown(self) -> None:
        """Stop the worker. Safe to call repeatedly and when never started."""
        with self._lock:
            proc = self._process
            self._process = None
            if proc is None:
                return
            try:
                if proc.poll() is None and proc.stdin is not None:
                    proc.stdin.write(json.dumps({"shutdown": True}) + "\n")
                    proc.stdin.flush()
            except Exception:  # noqa: BLE001 — already dead/closed pipe
                pass
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 — refused to exit; kill it
                with contextlib.suppress(Exception):  # already gone
                    proc.kill()

    # -- requests ----------------------------------------------------------

    def _exchange(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Send one request, read one reply. None on any transport failure."""
        with self._lock:
            if not self._ensure_started_locked():
                return None
            proc = self._process
            if proc is None or proc.stdin is None or proc.stdout is None:
                return None
            self._next_id += 1
            payload = {**payload, "id": self._next_id}
            try:
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
            except Exception:  # noqa: BLE001 — worker died mid-write
                self._reap_locked()
                return None

            reply_line: list[str] = []

            def _read() -> None:
                try:
                    line = proc.stdout.readline()  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001 — pipe broke
                    return
                if line:
                    reply_line.append(line)

            reader = threading.Thread(target=_read, daemon=True)
            reader.start()
            reader.join(timeout=_REQUEST_TIMEOUT_SEC)
            if not reply_line:
                # Timed out or the worker exited without answering.
                self._reap_locked()
                return None
            try:
                return json.loads(reply_line[0])
            except json.JSONDecodeError:
                return None

    def _reap_locked(self) -> None:
        proc = self._process
        self._process = None
        if proc is None:
            return
        with contextlib.suppress(Exception):  # already gone
            proc.kill()

    # -- public API (mirrors pipeline) -------------------------------------

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        """Embed a batch. Raises on failure -- callers that prefer a graceful
        fallback should use `embed_batch_or_none`.
        """
        vectors = self.embed_batch_or_none(texts)
        if vectors is None:
            raise RuntimeError("embedding worker unavailable")
        return vectors

    def embed_batch_or_none(self, texts: list[str]) -> np.ndarray | None:
        """Embed a batch, or None if the worker is unavailable or errored.

        None is the "fall back to literal-only search" signal the server's
        existing handlers already know how to deal with.
        """
        if not texts:
            return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        reply = self._exchange({"texts": list(texts)})
        if reply is None or "vectors" not in reply:
            return None
        return np.asarray(reply["vectors"], dtype=np.float32)

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed_batch([text])[0]

    def embed_one_or_none(self, text: str) -> np.ndarray | None:
        vectors = self.embed_batch_or_none([text])
        if vectors is None or len(vectors) == 0:
            return None
        return vectors[0]


_shared_client: EmbeddingWorkerClient | None = None
_shared_lock = threading.Lock()


def get_shared_client() -> EmbeddingWorkerClient:
    """Process-wide worker client (one subprocess per server, not per call)."""
    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            _shared_client = EmbeddingWorkerClient()
        return _shared_client
