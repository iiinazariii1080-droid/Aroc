from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI
import httpx

from app.core.auth_service import AuthContext, AuthSetupError, load_auth_context, request_robot_token
from app.core.service_metrics import record_auth_result
from app.core.utils import classify_http_error

logger = logging.getLogger(__name__)


class AuthClient:
    def __init__(
        self,
        *,
        refresh_skew_s: float = 60.0,
        min_retry_interval_s: float = 15.0,
        max_retry_interval_s: float = 300.0,
        auth_connect_timeout_s: float = 2.0,
    ) -> None:
        self._refresh_skew_s = refresh_skew_s
        self._min_retry_interval_s = min_retry_interval_s
        self._max_retry_interval_s = max_retry_interval_s
        self._auth_timeout = httpx.Timeout(
            connect=auth_connect_timeout_s, read=5.0, write=5.0, pool=auth_connect_timeout_s,
        )
        self._app: Optional[FastAPI] = None
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._refresh_task: Optional[asyncio.Task] = None
        self._last_error: Optional[str] = None
        self._last_context: Optional[AuthContext] = None
        self._last_refresh_ts: Optional[float] = None
        self._consecutive_failures: int = 0

    async def startup(self, app: FastAPI) -> None:
        self._app = app
        await self._refresh(reason="startup", raise_on_error=False)
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def shutdown(self) -> None:
        self._stop_event.set()
        if self._refresh_task:
            self._wake_event.set()
            await asyncio.sleep(0)
            await self._refresh_task

    async def get_access_token(self) -> str:
        async with self._lock:
            if self._token and not self._expires_soon_locked():
                return self._token
        await self._refresh(reason="on-demand", raise_on_error=True)
        async with self._lock:
            if not self._token:
                raise RuntimeError("AuthClient failed to obtain token")
            return self._token

    async def get_auth_header(self) -> str:
        token = await self.get_access_token()
        return f"Bearer {token}"

    async def force_refresh(self) -> None:
        self._wake_event.set()
        await self._refresh(reason="forced", raise_on_error=False)

    async def describe(self) -> Dict[str, Any]:
        async with self._lock:
            return {
                "token_present": bool(self._token),
                "expires_at": self._expires_at or None,
                "seconds_until_expiry": max(0, self._expires_at - time.time()) if self._expires_at else None,
                "last_error": self._last_error,
                "last_refresh_ts": self._last_refresh_ts,
                "consecutive_failures": self._consecutive_failures,
                "context": {
                    "base_url": self._last_context.base_url if self._last_context else None,
                    "auth_url": self._last_context.auth_url if self._last_context else None,
                    "robot_id": self._last_context.robot_id if self._last_context else None,
                },
            }

    async def _refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            sleep_for = await self._seconds_until_refresh()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=sleep_for)
                self._wake_event.clear()
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                break
            await self._refresh(reason="loop", raise_on_error=False)

    async def _seconds_until_refresh(self) -> float:
        async with self._lock:
            if not self._token:
                # Exponential backoff: 15, 30, 60, 120, 300 (capped)
                backoff = min(
                    self._min_retry_interval_s * (2 ** min(self._consecutive_failures, 8)),
                    self._max_retry_interval_s,
                )
                return backoff
            refresh_at = max(
                0.0,
                self._expires_at - time.time() - self._refresh_skew_s,
            )
        return max(refresh_at, self._min_retry_interval_s)

    async def _refresh(self, *, reason: str, raise_on_error: bool) -> None:
        async with self._lock:
            if reason != "forced" and self._token and not self._expires_soon_locked():
                return
        try:
            if not self._app:
                raise RuntimeError("AuthClient not initialized with FastAPI app")
            context = await load_auth_context()
            client = getattr(self._app.state, "http_client", None)
            if client is None:
                raise RuntimeError("HTTP client not ready")
            data = await request_robot_token(client, context, timeout=self._auth_timeout)
            expires_in = data.get("expires_in", 0)
            now = time.time()
            async with self._lock:
                self._token = data["access_token"]
                self._expires_at = now + max(float(expires_in), 0.0)
                self._last_context = context
                self._last_error = None
                self._last_refresh_ts = now
                self._consecutive_failures = 0
            logger.info(
                "Token refreshed (reason=%s, expires_in=%s)",
                reason,
                expires_in,
                extra={"reason": reason},
            )
            record_auth_result(True)
        except (AuthSetupError, httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
            error_text = str(exc)
            if isinstance(exc, AuthSetupError):
                metric_reason = "auth_setup_error"
            else:
                metric_reason = classify_http_error(exc)
            if isinstance(exc, httpx.HTTPStatusError):
                req_url = str(exc.response.request.url) if exc.response and exc.response.request else "unknown"
                error_text = f"Auth endpoint returned {exc.response.status_code} ({req_url})"

            record_auth_result(False, metric_reason)
            async with self._lock:
                self._consecutive_failures += 1
                self._last_error = error_text
                failures = self._consecutive_failures
            # Log every failure as WARNING — DEBUG hides persistent auth
            # failures in production where LOG_LEVEL=INFO is common.
            # Rate-limit: full message on first 3 and every 10th after.
            if failures <= 3 or failures % 10 == 0:
                logger.warning("Token refresh failed (attempt=%d, reason=%s): %s", failures, reason, error_text, extra={"reason": reason})
            else:
                logger.debug(
                    "Token refresh still failing (attempt=%d, reason=%s): %s",
                    failures, reason, error_text,
                    extra={"reason": reason},
                )
            if raise_on_error:
                raise

    def _expires_soon_locked(self) -> bool:
        if not self._token:
            return True
        return (self._expires_at - time.time()) <= self._refresh_skew_s


def get_auth_client(app: FastAPI) -> Optional[AuthClient]:
    return getattr(app.state, "auth_client", None)

