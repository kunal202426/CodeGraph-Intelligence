# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Build the text that represents an entity for embedding.

The embedding input combines an entity's structural identity (type + qualified
name), its signature, its docstring, and a slice of its body. This gives the
sentence-transformer enough signal to place semantically-similar code near each
other in vector space without blowing past the model's context window.

`embed_input_hash` lets the incremental re-embed pass skip entities whose
embedding input is unchanged.
"""

from __future__ import annotations

from codegraph.uir import UIREntity, hash_source

# Cap body length so a giant function doesn't dominate / overflow the model.
_MAX_BODY_CHARS = 1500


def build_embed_input_from_fields(
    entity_type: str,
    qualified_name: str,
    signature: str | None,
    docstring: str | None,
    raw_source: str | None,
    summary: str | None = None,
) -> str:
    """Compose the embedding text from raw entity fields (DB-row friendly).

    `summary` (an agent-written natural-language description, when present) is
    appended so concept words that don't appear in the code itself still land
    near it in vector space. It is optional and defaults to None: an entity
    without a summary produces byte-identical text to before this field existed,
    so the embed-input hash is unchanged and no re-embed is triggered.
    """
    parts: list[str] = [f"{entity_type} {qualified_name}"]
    if signature:
        parts.append(signature)
    if docstring:
        parts.append(docstring)
    if raw_source:
        parts.append(raw_source[:_MAX_BODY_CHARS])
    if summary:
        parts.append(summary)
    return "\n".join(parts)


def build_embed_input(entity: UIREntity) -> str:
    """Compose the embedding text for a UIREntity."""
    return build_embed_input_from_fields(
        entity.type.value,
        entity.qualified_name,
        entity.signature,
        entity.docstring,
        entity.raw_source,
        entity.summary,
    )


def embed_input_hash(text: str) -> str:
    """SHA-256 of the embedding input — used to skip re-embedding unchanged text."""
    return hash_source(text)
