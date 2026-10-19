"""YTBS İstatistikler sayfası istemcisi (senkron; `ytbs-sync` için).

Akış, sayfanın kendi davranışını taklit eder:
1. GET sayfa -> oturum çerezi + ViewState + form alanları
2. "Göster" butonunun PrimeFaces AJAX POST'u -> istenen günün 15 dk verisi
3. (opsiyonel) Excel image-button'ının normal form POST'u -> günlük XLSX rapor

AJAX başlıkları yalnız ilgili isteğe verilir; oturuma kalıcı yazılırsa sonraki GET'ler de
partial-response döner.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Self

import httpx

from ..exceptions import EnergyDataError, ErrorCode
from ..models import ISTANBUL
from . import YTBS_BASE_URL, YTBS_PAGE_PATH
from .parsers import SOURCE, FormState, parse_day_series, parse_form_state

logger = logging.getLogger(__name__)

AJAX_HEADERS = {"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest"}
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"
XLSX_MAGIC = b"PK"


@dataclass(slots=True)
class YtbsDay:
    day: date
    series: list[dict[str, Any]]
    excel: bytes | None
    fetched_at: datetime


class YtbsClient:
    def __init__(
        self,
        *,
        base_url: str = YTBS_BASE_URL,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        backoff_seconds: float = 2.0,
        user_agent: str = "turkiye-energy-mcp ytbs-sync",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._client.request(method, url, **kwargs)
                if response.status_code < 500:
                    response.raise_for_status()
                    return response
                logger.warning("YTBS %s döndü (deneme %s)", response.status_code, attempt)
            except httpx.TransportError as exc:
                if attempt == self.max_attempts:
                    raise EnergyDataError(
                        ErrorCode.SOURCE_UNAVAILABLE,
                        "YTBS'ye bağlanılamadı. Kaynak yalnız Türkiye IP'lerinden yanıt veriyor; "
                        "ytbs-sync Türkiye'deki bir makinede çalıştırılmalıdır.",
                        source=SOURCE,
                        details={"url": url, "error": type(exc).__name__},
                    ) from exc
                logger.warning("YTBS bağlantı hatası (deneme %s): %s", attempt, exc)
            except httpx.HTTPStatusError as exc:
                raise EnergyDataError(
                    ErrorCode.SOURCE_UNAVAILABLE,
                    f"YTBS beklenmeyen HTTP {exc.response.status_code} döndürdü.",
                    source=SOURCE,
                    details={"url": url},
                ) from exc
            if attempt < self.max_attempts:
                time.sleep(self.backoff_seconds * attempt)
        raise EnergyDataError(
            ErrorCode.SOURCE_UNAVAILABLE, "YTBS geçici olarak yanıt vermiyor.", source=SOURCE
        )

    def _open_page(self) -> FormState:
        response = self._request("GET", self.base_url + YTBS_PAGE_PATH)
        return parse_form_state(response.text)

    def _form_body(self, form: FormState, day: date) -> dict[str, str]:
        return {
            "formdash": "formdash",
            "hidden1": form.hidden1,
            "formdash:bitisTarihi2_input": day.strftime("%d-%m-%Y"),
            "formdash:yilsecim_focus": "",
            "formdash:yilsecim_input": form.year,
            "jakarta.faces.ViewState": form.view_state,
        }

    def fetch_series(self, day: date) -> list[dict[str, Any]]:
        form = self._open_page()
        body = self._form_body(form, day) | {
            "jakarta.faces.partial.ajax": "true",
            "jakarta.faces.source": "formdash:gunlukRapor",
            "jakarta.faces.partial.execute": "@all",
            "jakarta.faces.partial.render": "formdash",
            "formdash:gunlukRapor": "formdash:gunlukRapor",
        }
        response = self._request(
            "POST",
            self.base_url + form.post_path,
            data=body,
            headers=AJAX_HEADERS | {"Content-Type": FORM_CONTENT_TYPE},
        )
        return parse_day_series(response.text, day)

    def fetch_daily_excel(self, day: date) -> bytes:
        form = self._open_page()
        if not form.daily_excel_button:
            raise EnergyDataError(
                ErrorCode.PARSING_ERROR, "YTBS günlük Excel butonu bulunamadı.", source=SOURCE
            )
        body = self._form_body(form, day) | {
            f"{form.daily_excel_button}.x": "10",
            f"{form.daily_excel_button}.y": "10",
        }
        response = self._request(
            "POST",
            self.base_url + form.post_path,
            data=body,
            headers={"Content-Type": FORM_CONTENT_TYPE},
        )
        if not response.content.startswith(XLSX_MAGIC):
            raise EnergyDataError(
                ErrorCode.PARSING_ERROR,
                "YTBS günlük rapor yerine XLSX olmayan içerik döndürdü.",
                source=SOURCE,
                details={"content_type": response.headers.get("content-type")},
            )
        return response.content

    def fetch_day(self, day: date, *, with_excel: bool = True) -> YtbsDay:
        series = self.fetch_series(day)
        excel = self.fetch_daily_excel(day) if with_excel else None
        return YtbsDay(day=day, series=series, excel=excel, fetched_at=datetime.now(ISTANBUL))
