from dataclasses import dataclass
from typing import Any

from ..cache import AsyncTTLCache
from ..config import Settings
from ..exceptions import EnergyDataError, ErrorCode
from ..http_client import ResilientHTTPClient
from ..parsers.common import normalize_key


@dataclass(frozen=True, slots=True)
class Workbook:
    content: bytes
    source_url: str
    source_format: str
    name: str
    gallery_url: str


class TeiasClient:
    """Client for the JSON catalog and files used by TEİAŞ's official website."""

    def __init__(
        self,
        settings: Settings,
        http: ResilientHTTPClient,
        cache: AsyncTTLCache,
    ) -> None:
        self.settings = settings
        self.http = http
        self.cache = cache

    def gallery_url(self, slug: str) -> str:
        return f"{self.settings.teias_base_url}/api/gallery?locale=tr-TR&slug={slug}"

    def file_url(self, media_slug: str) -> str:
        return f"{self.settings.teias_file_base_url}/file/{media_slug}?download"

    async def gallery(self, slug: str) -> dict[str, Any]:
        async def load() -> dict[str, Any]:
            payload = await self.http.get_json(
                f"{self.settings.teias_base_url}/api/gallery",
                params={"locale": "tr-TR", "slug": slug},
            )
            if payload.get("success") is not True or not isinstance(payload.get("payload"), dict):
                raise EnergyDataError(
                    ErrorCode.DATA_NOT_AVAILABLE,
                    "TEİAŞ galerisinde istenen veri seti bulunamadı.",
                    source="TEİAŞ",
                    details={"gallery": slug},
                )
            return payload["payload"]

        return await self.cache.get_or_set(
            f"teias:gallery:{slug}",
            load,
            self.settings.cache_historical_ttl_seconds,
        )

    async def workbook(
        self,
        gallery_slug: str,
        *,
        title_prefix: str | None = None,
        extensions: tuple[str, ...] = ("xlsx", "xls"),
        ttl_seconds: int | None = None,
    ) -> Workbook:
        gallery = await self.gallery(gallery_slug)
        media = gallery.get("media")
        if not isinstance(media, list):
            raise EnergyDataError(
                ErrorCode.PARSING_ERROR,
                "TEİAŞ galeri şeması beklenen medya listesini içermiyor.",
                source="TEİAŞ",
            )

        candidates = [
            item
            for item in media
            if isinstance(item, dict)
            and normalize_key(str(item.get("extension", ""))) in extensions
            and (
                title_prefix is None
                or normalize_key(str(item.get("title", ""))).startswith(
                    normalize_key(title_prefix)
                )
            )
        ]
        if not candidates:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "TEİAŞ galerisinde eşleşen Excel dosyası bulunamadı.",
                source="TEİAŞ",
                details={"gallery": gallery_slug, "title_prefix": title_prefix},
            )
        item = max(candidates, key=lambda candidate: str(candidate.get("created_at", "")))
        media_slug = item.get("slug")
        if not isinstance(media_slug, str) or not media_slug:
            raise EnergyDataError(
                ErrorCode.PARSING_ERROR,
                "TEİAŞ medya kaydında dosya kimliği yok.",
                source="TEİAŞ",
            )

        source_url = self.file_url(media_slug)

        async def download() -> bytes:
            content = await self.http.get_bytes(source_url)
            if len(content) < 100:
                raise EnergyDataError(
                    ErrorCode.PARSING_ERROR,
                    "TEİAŞ dosyası beklenenden kısa.",
                    source="TEİAŞ",
                )
            return content

        content = await self.cache.get_or_set(
            f"teias:file:{media_slug}",
            download,
            ttl_seconds or self.settings.cache_historical_ttl_seconds,
        )
        return Workbook(
            content=content,
            source_url=source_url,
            source_format=str(item.get("extension", "xls")).lower(),
            name=str(item.get("name") or item.get("title") or media_slug),
            gallery_url=self.gallery_url(gallery_slug),
        )

    async def annual_workbook(self, gallery_prefix: str, title_prefix: str) -> Workbook:
        year = self.settings.teias_annual_report_year
        return await self.workbook(f"{gallery_prefix}-{year}", title_prefix=title_prefix)

    async def monthly_workbook(self, year: int) -> Workbook:
        return await self.workbook(
            f"{year}-yili-aylik-elektrik-uretim-tuketim-raporlari",
            ttl_seconds=self.settings.cache_monthly_ttl_seconds,
        )
