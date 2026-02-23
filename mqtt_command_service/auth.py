import contextlib
import logging
import threading
import time
from dataclasses import dataclass

import requests

from config import HubAuthSettings

logger = logging.getLogger("hub_auth")


@dataclass
class TokenInfo:
    access_token: str
    expires_at: float


class HubAuthManager:
    """
    Retrieves robot credentials from the local hub API and exchanges them for JWT tokens.
    """

    def __init__(self, settings: HubAuthSettings) -> None:
        self._settings = settings
        self._session = requests.Session()
        self._token: TokenInfo | None = None
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._cached_robot_id: str | None = settings.robot_id

    def is_enabled(self) -> bool:
        return bool(self._settings and self._settings.base_url)

    def robot_id(self) -> str | None:
        with self._lock:
            return self._cached_robot_id

    def auth_headers(self) -> dict[str, str]:
        """
        Returns {"Authorization": "Bearer ..."} if auth is enabled and a token is available.
        """
        if not self.is_enabled():
            return {}
        token = self._ensure_token()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token.access_token}"}

    def has_valid_token(self) -> bool:
        with self._lock:
            if self._token and not self._is_expiring(self._token):
                return True
        return False

    def _ensure_token(self) -> TokenInfo | None:
        with self._lock:
            if self._token and not self._is_expiring(self._token):
                return self._token
        # Release _lock before doing network I/O;  _refresh_lock
        # serialises concurrent refresh attempts.
        with self._refresh_lock:
            # Double-check after acquiring refresh lock
            with self._lock:
                if self._token and not self._is_expiring(self._token):
                    return self._token
            new_token = self._refresh_token()
            if new_token:
                with self._lock:
                    self._token = new_token
            return new_token

    def _refresh_token(self) -> TokenInfo | None:
        """Fetch a new JWT token.  Called *without* holding _lock."""
        credentials = self._resolve_credentials()
        if not credentials:
            logger.warning("Cannot refresh JWT: credentials are unavailable.")
            return None
        robot_id, api_key = credentials
        payload = {"robot_id": robot_id, "api_key": api_key}
        url = f"{self._settings.base_url}/auth"
        try:
            resp = self._session.post(url, json=payload, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            access_token = data.get("access_token")
            expires_in = float(data.get("expires_in", 3600))
            if not access_token:
                logger.error("Hub auth response lacks access_token.")
                return None
            token = TokenInfo(
                access_token=access_token,
                expires_at=time.time() + max(1.0, expires_in),
            )
            logger.info("Hub auth token refreshed (expires_in=%s).", expires_in)
            return token
        except Exception:
            logger.exception("Failed to refresh JWT from %s", url)
            return None

    def _resolve_credentials(self) -> tuple[str, str] | None:
        """Resolve robot credentials.  Called *without* holding _lock."""
        if self._settings.robot_id and self._settings.api_key:
            with self._lock:
                self._cached_robot_id = self._settings.robot_id
            return self._settings.robot_id, self._settings.api_key
        url = f"{self._settings.base_url}/credentials"
        try:
            resp = self._session.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            robot_id = data.get("robot_id")
            api_key = data.get("api_key")
            if robot_id and api_key:
                with self._lock:
                    self._cached_robot_id = robot_id
                return robot_id, api_key
            logger.error("Hub credentials endpoint did not return robot_id/api_key.")
            return None
        except Exception:
            logger.exception("Failed to fetch hub credentials from %s", url)
            return None

    def close(self) -> None:
        """Close the underlying HTTP session."""
        with contextlib.suppress(Exception):
            self._session.close()

    def _is_expiring(self, token: TokenInfo) -> bool:
        margin = max(0.0, self._settings.refresh_margin)
        return (token.expires_at - margin) <= time.time()

