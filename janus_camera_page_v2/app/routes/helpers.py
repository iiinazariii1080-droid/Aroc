"""Shared route helpers to avoid copy-paste across depth.py and system.py."""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response


def proxy_frame_response(
    resp,
    format: str,
    default_dtype: str = "float32",
    include_timestamp: bool = False,
) -> Response | JSONResponse:
    """Build a Response from a proxied frame endpoint.

    Raises HTTPException on non-200 upstream status.  For ``format="raw"``
    returns raw bytes with X-Width / X-Height / X-Dtype headers; otherwise
    returns the parsed JSON.
    """
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    if format == "raw":
        headers = {
            "X-Width": resp.headers.get("X-Width", ""),
            "X-Height": resp.headers.get("X-Height", ""),
            "X-Dtype": resp.headers.get("X-Dtype", default_dtype),
        }
        if include_timestamp:
            headers["X-Timestamp"] = resp.headers.get("X-Timestamp", "")
        return Response(
            content=resp.content,
            media_type="application/octet-stream",
            headers=headers,
        )
    return JSONResponse(content=resp.json())
