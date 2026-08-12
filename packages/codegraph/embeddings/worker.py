# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Out-of-process embedding worker: newline-delimited JSON over stdin/stdout.

Exists so the MCP server never imports torch. Two separate problems both go
away once the import lives in another process:

1. **Boot time.** Loading sentence-transformers cost ~12.5s warm and ~23s cold,
   and the server blocked on it before it could serve. Measured end-to-end boot
   on a real 97k-entity index: 25.6s cold, against Claude Code's 30s MCP connect
   timeout. Warm cache connected, cold cache got dropped -- and a dropped MCP
   server is invisible to the user, so the agent just silently used grep
   instead.
2. **The deadlock.** Importing torch on a non-main thread while an asyncio loop
   runs on the main thread hangs the process indefinitely (reproduced directly:
   ~31s synchronous vs 280s+ with zero progress via anyio.to_thread). That is
   why the load could not simply be moved to a background thread. In a
   subprocess it runs on that process's own main thread, so the hazard cannot
   arise at all.

Protocol (one JSON object per line, both directions):

    -> {"id": 1, "texts": ["some text", ...]}   embed a batch
    -> {"id": 2, "ping": true}                  liveness + dimension, no model load
    -> {"shutdown": true}                       exit cleanly
    <- {"id": 1, "vectors": [[...], ...]}
    <- {"id": 2, "embedding_dim": 384}
    <- {"id": 1, "error": "..."}                failure, worker stays alive

Errors are replies, never crashes: the server's callers treat an error as
"fall back to literal-only search", which is strictly better than a tool
handler raising.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

EMBED_DIM_KEY = "embedding_dim"

EmbedFn = Callable[[list[str]], "np.ndarray"]


def _default_embed(texts: list[str]) -> np.ndarray:
    """Real embedding path. Imported lazily so a ping never loads the model."""
    from codegraph.embeddings.pipeline import embed_batch

    return embed_batch(texts)


def handle_request(request: dict[str, Any], embed: EmbedFn = _default_embed) -> dict[str, Any]:
    """Turn one decoded request into one reply dict.

    Pure and injectable so the protocol is testable without torch anywhere
    near the test suite.
    """
    reply: dict[str, Any] = {"id": request.get("id")}
    if request.get("ping"):
        from codegraph.embeddings.pipeline import EMBEDDING_DIM

        reply[EMBED_DIM_KEY] = EMBEDDING_DIM
        return reply
    texts = request.get("texts") or []
    if not texts:
        # No model load for an empty batch -- the expensive import is the whole
        # reason this process exists, so never pay it for nothing.
        reply["vectors"] = []
        return reply
    try:
        vectors = embed(list(texts))
        reply["vectors"] = [[float(x) for x in row] for row in vectors]
    except Exception as exc:  # noqa: BLE001 — reported to the client, worker survives
        reply["error"] = f"{type(exc).__name__}: {exc}"
    return reply


def main() -> None:
    """Read requests from stdin until EOF or an explicit shutdown."""
    # Don't outlive the server. Closing stdin is the normal signal, but that
    # is exactly the signal known not to arrive when a process tree is killed
    # abruptly on Windows -- and an orphan here holds a fully loaded torch
    # model in memory indefinitely.
    from codegraph.proc import start_parent_watchdog

    start_parent_watchdog(os.getppid())

    # sentence-transformers, HuggingFace and tqdm all write progress noise to
    # whatever stdout happens to be. That would corrupt the line protocol, so
    # claim the real stdout for framing and point sys.stdout at stderr for
    # everything else in this process.
    protocol_out = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout = sys.stderr

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if request.get("shutdown"):
            break
        reply = handle_request(request)
        protocol_out.write(json.dumps(reply) + "\n")
        protocol_out.flush()


if __name__ == "__main__":
    main()
