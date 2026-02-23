from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import HTTPException, Request

from app.application.drive_service import ServiceError


def raise_service_error_http(
    exc: ServiceError,
    *,
    request: Request | None = None,
    operation: str | None = None,
) -> NoReturn:
    if request is not None and operation is not None:
        metrics = getattr(request.app.state, "metrics", None)
        if metrics is not None:
            try:
                metrics.observe_drive_operation_error(
                    operation=operation,
                    code=exc.code,
                    status_code=exc.status_code,
                )
            except Exception:
                logging.getLogger(__name__).exception("Failed to record drive operation metric")

    raise HTTPException(status_code=exc.status_code, detail=exc.to_error_detail())
