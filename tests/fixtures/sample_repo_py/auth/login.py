"""Authentication module — fixture for parser + indexer tests."""

from __future__ import annotations


_PRIVATE_TOKEN = "abc"


def authenticate(email: str, password: str) -> bool:
    """Validate user credentials and create a session.

    Returns True on success.
    """
    return password == _PRIVATE_TOKEN and "@" in email


async def fetch_user(user_id: int) -> dict:
    """Fetch a user record from the database asynchronously."""
    return {"id": user_id}


@staticmethod
def make_token() -> str:
    """Mint a one-off token. Decorated for the fixture."""
    return _PRIVATE_TOKEN


class LoginForm:
    """A form for user login."""

    def __init__(self, email: str) -> None:
        self.email = email

    def validate(self) -> bool:
        """Check the form is non-empty and well-formed."""
        return "@" in self.email

    async def submit(self) -> bool:
        """Submit the form to the auth backend."""
        return authenticate(self.email, _PRIVATE_TOKEN)


class _PrivateForm:
    """Internal-only form; underscore prefix marks it non-exported."""

    def helper(self) -> None:
        pass
