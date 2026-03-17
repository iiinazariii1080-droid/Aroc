"""Shared Jinja2Templates instance for route-layer template rendering."""
from __future__ import annotations

from fastapi.templating import Jinja2Templates

from app.core.settings import get_settings

_jinja: Jinja2Templates | None = None


def get_jinja() -> Jinja2Templates:
    global _jinja
    if _jinja is None:
        _jinja = Jinja2Templates(directory=str(get_settings().templates_dir))
    return _jinja
