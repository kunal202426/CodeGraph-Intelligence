"""Local embedding pipeline using sentence-transformers.

The model (`all-MiniLM-L6-v2`, 384-dim, ~80 MB) is downloaded to the HuggingFace
cache on first use and loaded as a process-wide singleton. Vectors are L2-
normalized so cosine similarity reduces to a dot product — which is what the
DuckDB `array_cosine_similarity` search in T3.2/T3.4 expects.

`sentence_transformers` (and its heavy torch dependency) is imported lazily
inside `_get_model`, so importing this module stays cheap until the first
embedding call.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

_model_lock = threading.Lock()
_model_cache: dict[str, SentenceTransformer] = {}


def _get_model(model_name: str = DEFAULT_MODEL) -> SentenceTransformer:
    """Lazy-load + cache the SentenceTransformer (thread-safe double-checked lock)."""
    model = _model_cache.get(model_name)
    if model is not None:
        return model
    with _model_lock:
        model = _model_cache.get(model_name)
        if model is None:
            # Quiet the Windows "symlinks unsupported" cache warning from HF.
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(model_name)
            _model_cache[model_name] = model
    return model


def embed_batch(texts: list[str], model_name: str = DEFAULT_MODEL) -> np.ndarray:
    """Embed a batch of texts.

    Returns an ``(N, EMBEDDING_DIM)`` float32 array of L2-normalized vectors.
    An empty input yields an empty ``(0, EMBEDDING_DIM)`` array without loading
    the model.
    """
    if not texts:
        return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
    model = _get_model(model_name)
    vectors = model.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


def embed_one(text: str, model_name: str = DEFAULT_MODEL) -> np.ndarray:
    """Embed a single text → ``(EMBEDDING_DIM,)`` float32 vector."""
    return embed_batch([text], model_name)[0]
