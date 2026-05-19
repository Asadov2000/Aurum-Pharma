"""Auth domain — identity, sessions, email codes, login attempts."""

from app.domains.auth.router import router

__all__ = ["router"]
