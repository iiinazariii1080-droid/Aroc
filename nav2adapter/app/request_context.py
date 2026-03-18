"""Request-scoped context variables for correlation/tracing.

Usage in any module::

    from app.request_context import get_request_id
    rid = get_request_id()  # returns current request's ID or "-"
"""
import contextvars

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def get_request_id() -> str:
    return _request_id_var.get()


def set_request_id(rid: str) -> contextvars.Token:
    return _request_id_var.set(rid)
