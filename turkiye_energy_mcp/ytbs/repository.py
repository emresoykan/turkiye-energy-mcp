"""MCP sunucusu için YTBS CSV deposu okuyucusu.

Railway'deki sunucu YTBS'ye erişemez; `ytbs-sync` çıktısını ya yerel klasörden
(`YTBS_DATA_DIR`) ya da private GitHub veri reposundan (`YTBS_DATA_REPO`, Contents API,
yalnız okuma yetkili `YTBS_DATA_TOKEN`) okur.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from typing import Any

from ..cache import AsyncTTLCache
from ..config import Settings
from ..exceptions import EnergyDataError, ErrorCode
from ..http_client import ResilientHTTPClient
from .parsers import SOURCE
from .store import MANIFEST, CsvStore

GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"


def _months(start: date, end: date) -> list[str]:
    months = []
    current = date(start.year, start.month, 1)
    while current <= end:
        months.append(current.isoformat()[:7])
        current = date(current.year + current.month // 12, current.month % 12 + 1, 1)
    return months


class YtbsRepository:
    def __init__(self, settings: Settings, http: ResilientHTTPClient, cache: AsyncTTLCache):
        self.settings = settings
        self.http = http
        self.cache = cache
        self._local = CsvStore(settings.ytbs_data_dir) if settings.ytbs_data_dir else None

    @property
    def backend(self) -> str | None:
        if self._local is not None:
            return f"yerel klasör ({self._local.root})"
        if self.settings.ytbs_data_repo:
            return f"GitHub {self.settings.ytbs_data_repo}@{self.settings.ytbs_data_ref}"
        return None

    def _require_backend(self) -> None:
        if self.backend is None:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "YTBS veri deposu yapılandırılmamış (YTBS_DATA_DIR veya YTBS_DATA_REPO).",
                source=SOURCE,
            )

    async def _github_text(self, path: str) -> str | None:
        """Depodaki dosya metni; dosya yoksa None.

        Token varsa Contents API (private repo, 5000 istek/saat); yoksa public repo için
        raw.githubusercontent.com (token'sız API'nin IP başına 60 istek/saat sınırına takılmaz).
        """
        repo = self.settings.ytbs_data_repo
        ref = self.settings.ytbs_data_ref
        if self.settings.ytbs_data_token:
            url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
            params: dict[str, str] | None = {"ref": ref}
            headers = {
                "Accept": "application/vnd.github.raw+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {self.settings.ytbs_data_token.get_secret_value()}",
            }
        else:
            url = f"{GITHUB_RAW}/{repo}/{ref}/{path}"
            params, headers = None, {}

        async def load() -> str:
            try:
                response = await self.http.get(url, params=params, headers=headers)
            except EnergyDataError as exc:
                if exc.code == ErrorCode.DATA_NOT_AVAILABLE:
                    return ""  # 404: bu ay için dosya yok
                raise
            return response.text

        text = await self.cache.get_or_set(
            f"ytbs:{repo}:{ref}:{path}", load, self.settings.cache_daily_ttl_seconds
        )
        return text or None

    async def manifest(self) -> dict[str, Any]:
        self._require_backend()
        if self._local is not None:
            manifest = self._local.load_manifest()
        else:
            text = await self._github_text(MANIFEST)
            manifest = json.loads(text) if text else {}
        if not manifest:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "YTBS veri deposunda manifest.json bulunamadı; ytbs-sync henüz çalışmamış olabilir.",
                source=SOURCE,
                details={"backend": self.backend},
            )
        return manifest

    async def read(self, table: str, start: date, end: date) -> list[dict[str, str]]:
        self._require_backend()
        if self._local is not None:
            return self._local.read(table, start, end)
        rows: list[dict[str, str]] = []
        for month in _months(start, end):
            text = await self._github_text(f"{table}/{month[:4]}/{month}.csv")
            if not text:
                continue
            for r in csv.DictReader(io.StringIO(text)):
                if start.isoformat() <= r.get("tarih", "") <= end.isoformat():
                    rows.append(r)
        return rows
