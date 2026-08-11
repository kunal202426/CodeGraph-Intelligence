"""Validation helpers that operate on already-built forms."""

from __future__ import annotations

from auth.login import LoginForm


def validate_form(form: LoginForm) -> bool:
    """Check a LoginForm has all required fields set."""
    return bool(form.email)
