"""HubAuthManager — retrieves robot credentials and exchanges them for JWT tokens."""

import contextlib
import logging
import threading
import time
from dataclasses import dataclass

import requests

from shared.config_types import HubAuthSettings
from shared.metrics import auth_degradation_total

logger = logging.getLogger(__name__)


@dataclass
class TokenInfo:
    access_token: str
    expires_at: float


class HubAuthManager:
    def __init__(self, settings: HubAuthSettings) -> None:
        self._settings = settings
        self._session = requests.Session()
        self._token: TokenInfo | None = None
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._cached_robot_id: str | None = settings.robot_id
        self._auth_degraded = False

    def is_enabled(self) -> bool:
        return bool(self._settings and self._settings.base_url)

    def robot_id(self) -> str | None:
        with self._lock:
            return self._cached_robot_id

    @property
    def auth_degraded(self) -> bool:
        """True when auth fell back to empty headers due to token failure."""
        return self._auth_degraded

    def auth_headers(self) -> dict[str, str]:
        if not self.is_enabled():
            return {}
        token = self._ensure_token()
        if not token:
            self._auth_degraded = True
            auth_degradation_total.inc()
            logger.warning("[hub-auth] Auth degraded: token unavailable, sending unauthenticated request")
            return {}
        self._auth_degraded = False
        return {"Authorization": f"Bearer {token.access_token}"}

    def has_valid_token(self) -> bool:
        with self._lock:
            if self._token and not self._is_expiring(self._token):
                return True
        return False

    def token_status(self) -> dict:
        with self._lock:
            if self._token:
                remaining = self._token.expires_at - time.time()
                return {
                    "valid": remaining > 0,
                    "expires_in": max(0, remaining),
                    "robot_id": self._cached_robot_id,
                }
        return {"valid": False, "expires_in": 0, "robot_id": self._cached_robot_id}

    def _ensure_token(self) -> TokenInfo | None:
        with self._lock:
            if self._token and not self._is_expiring(self._token):
                return self._token
        with self._refresh_lock:
            with self._lock:
                if self._token and not self._is_expiring(self._token):
                    return self._token
            new_token = self._refresh_token()
            if new_token:
                with self._lock:
                    self._token = new_token
            return new_token

    def _refresh_token(self) -> TokenInfo | None:
        credentials = self._resolve_credentials()
        if not credentials:
            logger.warning("[hub-auth] Cannot refresh JWT: credentials unavailable.")
            return None
        robot_id, api_key = credentials
        url = f"{self._settings.base_url}/auth"
        try:
            resp = self._session.post(
                url,
                json={"robot_id": robot_id, "api_key": api_key},
                timeout=self._settings.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            access_token = data.get("access_token")
            expires_in = float(data.get("expires_in", 3600))
            if not access_token:
                logger.error("[hub-auth] Hub auth response lacks access_token.")
                return None
            logger.info("[hub-auth] Hub auth token refreshed (expires_in=%s).", expires_in)
            return TokenInfo(
                access_token=access_token,
                expires_at=time.time() + max(1.0, expires_in),
            )
        except Exception:
            logger.exception("[hub-auth] Failed to refresh JWT from %s", url)
            return None

    def _resolve_credentials(self) -> tuple[str, str] | None:
        if self._settings.robot_id and self._settings.api_key:
            with self._lock:
                self._cached_robot_id = self._settings.robot_id
            return self._settings.robot_id, self._settings.api_key
        url = f"{self._settings.base_url}/credentials"
        try:
            resp = self._session.get(url, timeout=self._settings.timeout)
            resp.raise_for_status()
            data = resp.json()
            robot_id = data.get("robot_id")
            api_key = data.get("api_key")
            if robot_id and api_key:
                with self._lock:
                    self._cached_robot_id = robot_id
                return robot_id, api_key
            logger.error("[hub-auth] Credentials endpoint missing robot_id/api_key.")
            return None
        except Exception:
            logger.exception("[hub-auth] Failed to fetch hub credentials from %s", url)
            return None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._session.close()

    def _is_expiring(self, token: TokenInfo) -> bool:
        margin = max(0.0, self._settings.refresh_margin)
        return (token.expires_at - margin) <= time.time()
