# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Target registry — maps target names to Target instances.

Public API
----------
register_target(target) -- register a Target under target.name
get_target(name)        -- look up by name; raises KeyError if unknown
list_targets()          -- all registered targets sorted by name
"""

from __future__ import annotations

from codegraph.installer.base import Target

_REGISTRY: dict[str, Target] = {}


def register_target(target: Target) -> None:
    """Register *target* under its ``name`` attribute.  Replaces any existing entry."""
    _REGISTRY[target.name] = target


def get_target(name: str) -> Target:
    """Return the registered Target for *name*.

    Raises:
        KeyError: if *name* is not in the registry.
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise KeyError(f"Unknown target {name!r}. Available: {available}.") from None


def list_targets() -> list[Target]:
    """Return all registered targets sorted alphabetically by name."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]
