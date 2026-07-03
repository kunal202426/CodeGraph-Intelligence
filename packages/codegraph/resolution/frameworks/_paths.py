# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Shared route-path normalization.

Every framework resolver spells paths differently: Flask/FastAPI/Express use
a leading slash (`/users`), Django's `path()` conventionally omits it and
keeps a trailing one (`users/`), Spring's combined class+method path can pick
up double slashes. Cross-file and cross-language route matching (a fetch
call's URL against a handler's registered path) needs one canonical form, so
every framework resolver -- and the HTTP client extractor that matches
against them -- normalizes through this single function.
"""

from __future__ import annotations


def normalize_path(path: str) -> str:
    """Canonicalize a route path: one leading slash, no trailing slash
    (except the bare root, which stays "/"), no duplicate slashes."""
    parts = [p for p in path.strip().split("/") if p]
    return "/" + "/".join(parts) if parts else "/"
