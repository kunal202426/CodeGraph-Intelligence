"""User-facing HTTP endpoints."""

from __future__ import annotations

from auth.login import authenticate
from db.models import Session, User


class UserController:
    """Routes user-related HTTP requests."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def start(self) -> None:
        """Bind routes; called once at boot."""

    def get_user_by_id(self, user_id: int) -> User | None:
        """Return the user with the given ID, or None if not found."""
        return None

    def list_users(self) -> list[User]:
        """Return all registered users."""
        return []

    async def login_handler(self, email: str, password: str) -> Session | None:
        """Handle a POST /login request asynchronously."""
        if authenticate(email, password):
            return Session(email=email)
        return None
