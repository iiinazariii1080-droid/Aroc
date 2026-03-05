"""
Базовый HTTP клиент для работы с внешними API.
Содержит общую логику для всех HTTP операций.
"""
import aiohttp
import ssl
import json
import asyncio
from typing import Optional, Dict, Any, Union
from abc import ABC, abstractmethod

from exceptions import DeviceConnectionError, DeviceError


class BaseHttpClient(ABC):
    """Базовый класс для HTTP клиентов с общей логикой."""
    
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 10,
        allow_invalid_certs: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ):
        self.base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._allow_invalid_certs = allow_invalid_certs
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Создает сессию если она не существует."""
        async with self._session_lock:
            if self._session is None or self._session.closed:
                connector = self._create_connector()
                self._session = aiohttp.ClientSession(
                    timeout=self._timeout,
                    connector=connector
                )
            return self._session

    def _create_connector(self) -> aiohttp.TCPConnector:
        """Создает коннектор с нужными SSL настройками."""
        kwargs = dict(limit=30, limit_per_host=20, enable_cleanup_closed=True)
        if self._allow_invalid_certs:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            return aiohttp.TCPConnector(ssl=ssl_ctx, **kwargs)
        return aiohttp.TCPConnector(**kwargs)

    async def _read_payload(self, resp: aiohttp.ClientResponse) -> Any:
        """Читает payload из ответа с обработкой ошибок."""
        try:
            return await resp.json(content_type=None)
        except Exception:
            try:
                text = await resp.text()
            except Exception:
                return {"error": "No content"}
            try:
                return json.loads(text)
            except Exception:
                return {"text": text}

    def _extract_error_message(self, data: Any) -> str:
        """Извлекает сообщение об ошибке из ответа."""
        if isinstance(data, dict):
            if "detail" in data:
                detail = data.get("detail")
                if isinstance(detail, dict) and "error" in detail:
                    err = detail.get("error")
                    if err is not None:
                        return str(err)
                if detail is not None:
                    return str(detail)
            elif "error" in data:
                err = data.get("error")
                if err is not None:
                    return str(err)
            # Check for common error response shapes
            if "text" in data:
                return str(data.get("text", ""))
            if "message" in data:
                return str(data.get("message", ""))
        if data is None:
            return "Empty response"
        return str(data) if data else "Unknown error"

    async def _make_request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[aiohttp.ClientTimeout] = None,
        op_timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Базовый метод для выполнения HTTP запросов с retry логикой.

        Args:
            max_retries: Override instance-level ``_max_retries``.  Pass ``0``
                         for long-poll / one-shot calls that should not be
                         retried internally (the caller handles retries).
        """
        session = await self._ensure_session()
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        effective_max_retries = self._max_retries if max_retries is None else max_retries

        # Apply operation-level timeout if provided.
        # Many call sites pass op_timeout expecting it to cap total request time.
        effective_timeout: aiohttp.ClientTimeout = timeout or self._timeout
        if op_timeout is not None:
            effective_timeout = aiohttp.ClientTimeout(
                total=float(op_timeout),
                connect=getattr(effective_timeout, "connect", None),
                sock_connect=getattr(effective_timeout, "sock_connect", None),
                sock_read=getattr(effective_timeout, "sock_read", None),
            )

        last_exception: Optional[BaseException] = None

        # Retry only transient failures (do NOT retry logical/device errors).
        retriable_statuses = {408, 429, 500, 502, 503, 504}

        for attempt in range(effective_max_retries + 1):
            try:
                async with session.request(
                    method=method,
                    url=url,
                    json=json_data,
                    params=params,
                    timeout=effective_timeout
                ) as resp:
                    data = await self._read_payload(resp)
                    
                    if 200 <= resp.status < 300:
                        return data if isinstance(data, dict) else {"result": data}
                    
                    # Обработка ошибок
                    error_msg = self._extract_error_message(data)
                    # Include status/method/url for easier debugging of controllers with empty bodies.
                    text = f"{self.__class__.__name__}: HTTP {resp.status} {method} {url}: {error_msg}"
                    
                    if resp.status in retriable_statuses:
                        raise DeviceConnectionError(text)
                    raise DeviceError(text)
                    
            except DeviceConnectionError as e:
                last_exception = e
                if attempt < effective_max_retries:
                    await asyncio.sleep(self._retry_delay * (2 ** attempt))
                    continue
                raise
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                # Transport-level failures are transient.
                last_exception = e
                if attempt < effective_max_retries:
                    await asyncio.sleep(self._retry_delay * (2 ** attempt))
                    continue
                raise DeviceConnectionError(f"{self.__class__.__name__}: request timeout/connection error: {e}") from e
            except DeviceError:
                # Non-transient error; do not retry.
                raise
            except Exception:
                # Programming errors (TypeError, KeyError, etc.) are not transient.
                # Don't waste time retrying — re-raise immediately.
                raise
        
        # Should be unreachable, but keep a safe fallback.
        if last_exception:
            raise last_exception
        raise DeviceError("Request failed: unknown error")

    async def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[aiohttp.ClientTimeout] = None,
        op_timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> Dict[str, Any]:
        """GET запрос."""
        return await self._make_request(
            "GET", path, params=params, timeout=timeout, op_timeout=op_timeout,
            max_retries=max_retries,
        )

    async def post(
        self,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        timeout: Optional[aiohttp.ClientTimeout] = None,
        op_timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """POST запрос."""
        return await self._make_request(
            "POST", path, json_data=json_data, timeout=timeout, op_timeout=op_timeout
        )

    async def put(
        self,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        timeout: Optional[aiohttp.ClientTimeout] = None,
        op_timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """PUT запрос."""
        return await self._make_request(
            "PUT", path, json_data=json_data, timeout=timeout, op_timeout=op_timeout
        )

    async def delete(
        self,
        path: str,
        timeout: Optional[aiohttp.ClientTimeout] = None,
        op_timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """DELETE запрос."""
        return await self._make_request(
            "DELETE", path, timeout=timeout, op_timeout=op_timeout
        )

    async def get_raw(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        op_timeout: Optional[float] = None,
    ) -> bytes:
        """GET запрос, возвращающий сырые байты (для изображений, бинарных файлов и т.д.)."""
        session = await self._ensure_session()
        url = path if path.startswith("http") else f"{self.base_url}{path}"

        effective_timeout: aiohttp.ClientTimeout = self._timeout
        if op_timeout is not None:
            effective_timeout = aiohttp.ClientTimeout(total=float(op_timeout))

        try:
            async with session.request(
                method="GET",
                url=url,
                params=params,
                timeout=effective_timeout,
            ) as resp:
                if 200 <= resp.status < 300:
                    return await resp.read()
                error_msg = f"{self.__class__.__name__}: HTTP {resp.status} GET {url}"
                raise DeviceError(error_msg)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            raise DeviceConnectionError(
                f"{self.__class__.__name__}: request timeout/connection error GET {url}: {e}"
            ) from e

    async def close(self):
        """Закрывает HTTP сессию."""
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
