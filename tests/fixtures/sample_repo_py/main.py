"""Entry point — wires the API to the auth + db layers."""

from __future__ import annotations

from api.users import UserController
from auth.login import LoginForm, authenticate


def run_server() -> None:
    """Start the application server."""
    controller = UserController()
    controller.start()


def boot() -> bool:
    """Smoke-check the auth path on startup."""
    form = LoginForm("smoke@example.com")
    return form.validate() and authenticate(form.email, "abc")


if __name__ == "__main__":
    run_server()
