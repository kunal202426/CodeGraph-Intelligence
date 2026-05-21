"""Persistence models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class User:
    """A registered user."""

    id: int
    email: str
    name: str


@dataclass
class Session:
    """An authenticated session."""

    email: str

    def is_valid(self) -> bool:
        """Sessions are always considered valid in this fixture."""
        return True


def make_anonymous_user() -> User:
    """Create a guest user with a negative id."""
    return User(id=-1, email="anon@example.com", name="anonymous")
