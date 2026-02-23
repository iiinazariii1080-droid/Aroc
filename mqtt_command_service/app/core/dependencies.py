"""FastAPI dependencies."""
from fastapi import Request

from app.core.security import Role


def get_current_role(request: Request) -> Role | None:
    """Get current user role from request state (set by auth middleware)."""
    return getattr(request.state, "auth_role", None)

