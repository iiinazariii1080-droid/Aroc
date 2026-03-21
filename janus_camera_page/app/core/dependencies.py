import hmac

from fastapi import HTTPException, Request

from app.core.settings import get_settings


def require_api_key(request: Request) -> None:
    settings = get_settings()
    if not settings.api_key:
        return

    key = request.headers.get("x-api-key")
    if key is None or not hmac.compare_digest(key, settings.api_key):
        raise HTTPException(status_code=401, detail="invalid api key")

