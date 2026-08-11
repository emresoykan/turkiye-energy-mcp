import logging
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import Settings
from .exceptions import EnergyDataError, ErrorCode

logger = logging.getLogger(__name__)


class _RetryableHTTPError(Exception):
    pass


class ResilientHTTPClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        timeout = httpx.Timeout(settings.http_timeout_seconds)
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "application/json, application/vnd.ms-excel, "
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, */*",
            },
            follow_redirects=True,
            limits=limits,
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get(self, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        attempts = self.settings.http_max_retries + 1
        retrying = AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(
                multiplier=self.settings.http_backoff_seconds,
                min=self.settings.http_backoff_seconds,
                max=8,
            ),
            retry=retry_if_exception_type((httpx.TransportError, _RetryableHTTPError)),
            reraise=True,
        )

        try:
            async for attempt in retrying:
                with attempt:
                    response = await self._client.get(url, params=params)
                    if response.status_code == 429 or response.status_code >= 500:
                        logger.warning(
                            "Retryable upstream response",
                            extra={
                                "url": str(response.url),
                                "status_code": response.status_code,
                                "attempt": attempt.retry_state.attempt_number,
                            },
                        )
                        raise _RetryableHTTPError(str(response.status_code))
                    self._validate(response)
                    return response
        except _RetryableHTTPError as exc:
            raise EnergyDataError(
                ErrorCode.RATE_LIMITED if "429" in str(exc) else ErrorCode.SOURCE_UNAVAILABLE,
                "Resmî veri kaynağı geçici olarak kullanılamıyor.",
                details={"url": url},
            ) from exc
        except httpx.TransportError as exc:
            raise EnergyDataError(
                ErrorCode.SOURCE_UNAVAILABLE,
                "Resmî veri kaynağına bağlantı kurulamadı.",
                details={"url": url},
            ) from exc

        raise EnergyDataError(ErrorCode.SOURCE_UNAVAILABLE, "Kaynak yanıt vermedi.")

    @staticmethod
    def _validate(response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise EnergyDataError(
                ErrorCode.AUTH_REQUIRED,
                "Resmî kaynak erişim için yetkilendirme istiyor.",
                details={"url": str(response.url), "status_code": response.status_code},
            )
        if response.status_code == 404:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "İstenen resmî veri dosyası bulunamadı.",
                details={"url": str(response.url)},
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise EnergyDataError(
                ErrorCode.SOURCE_UNAVAILABLE,
                "Resmî kaynak beklenmeyen bir HTTP yanıtı döndürdü.",
                details={"url": str(response.url), "status_code": response.status_code},
            ) from exc

    async def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.get(url, params=params)
        try:
            value = response.json()
        except ValueError as exc:
            raise EnergyDataError(
                ErrorCode.PARSING_ERROR,
                "Resmî kaynağın JSON yanıtı ayrıştırılamadı.",
                details={"url": str(response.url)},
            ) from exc
        if not isinstance(value, dict):
            raise EnergyDataError(ErrorCode.PARSING_ERROR, "Kaynak JSON nesnesi döndürmedi.")
        return value

    async def get_bytes(self, url: str) -> bytes:
        return (await self.get(url)).content
