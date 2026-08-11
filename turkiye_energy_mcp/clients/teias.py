from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..cache import AsyncTTLCache
from ..config import Settings
from ..exceptions import EnergyDataError, ErrorCode
from ..freshness import (
    ANNUAL_SELECTORS,
    MONTHLY_GALLERY_PREFIX,
    available_years,
    extract_month_period,
    extract_year_period,
    publication_timestamp,
    select_latest_gallery,
    select_newest_media,
    slug_period_year,
    title_matches,
)
from ..parsers.common import normalize_key

if TYPE_CHECKING:
    from ..http_client import ResilientHTTPClient


@dataclass(frozen=True, slots=True)
class Workbook:
    content: bytes
    source_url: str
    source_format: str
    name: str
    gallery_url: str
    gallery_slug: str
    period_start: int | None = None
    period_end: int | None = None
    published_at: str | None = None
    latest_available_period: str | None = None


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

    async def list_galleries(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            payload = await self.http.get_json(
                f"{self.settings.teias_base_url}/api/gallery",
                params={"locale": "tr-TR"},
            )
            if payload.get("success") is not True:
                raise EnergyDataError(
                    ErrorCode.SOURCE_UNAVAILABLE,
                    "TEİAŞ galeri kataloğu okunamadı.",
                    source="TEİAŞ",
                )
            data = payload.get("payload", {}).get("data")
            if not isinstance(data, list):
                raise EnergyDataError(
                    ErrorCode.PARSING_ERROR,
                    "TEİAŞ galeri kataloğu beklenen şemada değil.",
                    source="TEİAŞ",
                )
            return [item for item in data if isinstance(item, dict)]

        return await self.cache.get_or_set(
            "teias:gallery:index",
            load,
            self.settings.cache_daily_ttl_seconds,
        )

    async def gallery(self, slug: str) -> dict[str, Any]:
        async def load() -> dict[str, Any]:
            payload = await self.http.get_json(
                f"{self.settings.teias_base_url}/api/gallery",
                params={"locale": "tr-TR", "slug": slug},
            )
            if payload.get("success") is not True or not isinstance(
                payload.get("payload"), dict
            ):
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

    async def latest_annual_period(self, gallery_prefix: str) -> int:
        galleries = await self.list_galleries()
        latest = select_latest_gallery(galleries, gallery_prefix)
        if latest is None:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "TEİAŞ yıllık galeri serisi bulunamadı.",
                source="TEİAŞ",
                details={
                    "gallery_prefix": gallery_prefix,
                    "latest_available_period": None,
                    "data_freshness": "unavailable",
                },
            )
        year = slug_period_year(str(latest["slug"]))
        if year is None:
            raise EnergyDataError(
                ErrorCode.PARSING_ERROR,
                "TEİAŞ yıllık galeri döneminde yıl okunamadı.",
                source="TEİAŞ",
                details={"gallery": latest.get("slug")},
            )
        return year

    async def workbook_from_media(
        self,
        gallery_slug: str,
        item: dict[str, Any],
        *,
        ttl_seconds: int | None = None,
    ) -> Workbook:
        media_slug = item.get("slug")
        if not isinstance(media_slug, str) or not media_slug:
            raise EnergyDataError(
                ErrorCode.PARSING_ERROR,
                "TEİAŞ medya kaydında dosya kimliği yok.",
                source="TEİAŞ",
            )
        source_url = self.file_url(media_slug)
        title = str(item.get("title") or item.get("name") or media_slug)
        start_year, end_year = extract_year_period(title)
        gallery_year = slug_period_year(gallery_slug)
        if end_year is None:
            end_year = gallery_year
        if start_year is None:
            start_year = end_year
        published = publication_timestamp(item)
        published_at = None if published.year <= 1 else published.isoformat()
        is_monthly = gallery_slug.endswith(MONTHLY_GALLERY_PREFIX)
        latest_period = (
            extract_month_period(title, gallery_year)
            if is_monthly
            else (str(end_year) if end_year is not None else None)
        )

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
            gallery_slug=gallery_slug,
            period_start=start_year,
            period_end=end_year,
            published_at=published_at,
            latest_available_period=latest_period,
        )

    async def workbook(
        self,
        gallery_slug: str,
        *,
        require_all: tuple[str, ...] = (),
        require_any: tuple[str, ...] = (),
        exclude_any: tuple[str, ...] = (),
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
        item = select_newest_media(
            media,
            require_all=require_all,
            require_any=require_any,
            exclude_any=exclude_any,
            extensions=extensions,
        )
        if item is None and not require_all and not require_any and not exclude_any:
            candidates = [
                candidate
                for candidate in media
                if isinstance(candidate, dict)
                and normalize_key(str(candidate.get("extension", ""))) in extensions
            ]
            if candidates:
                item = max(candidates, key=publication_timestamp)
        if item is None:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "TEİAŞ galerisinde eşleşen Excel dosyası bulunamadı.",
                source="TEİAŞ",
                details={
                    "gallery": gallery_slug,
                    "require_all": list(require_all),
                    "require_any": list(require_any),
                    "exclude_any": list(exclude_any),
                },
            )
        return await self.workbook_from_media(
            gallery_slug, item, ttl_seconds=ttl_seconds
        )

    async def annual_workbook(self, selector_key: str) -> Workbook:
        workbooks = await self.annual_workbooks(selector_key)
        return max(
            workbooks,
            key=lambda workbook: (
                workbook.period_end or -1,
                workbook.published_at or "",
                workbook.name,
            ),
        )

    async def annual_workbooks(self, selector_key: str) -> list[Workbook]:
        selector = ANNUAL_SELECTORS[selector_key]
        gallery_prefix = selector["gallery_prefix"]
        galleries = await self.list_galleries()
        latest = select_latest_gallery(galleries, gallery_prefix)
        if latest is None:
            years = available_years(galleries, gallery_prefix)
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "İstenen TEİAŞ yıllık veri setinin güncel galerisi bulunamadı.",
                source="TEİAŞ",
                details={
                    "dataset": selector_key,
                    "gallery_prefix": gallery_prefix,
                    "latest_available_period": str(max(years)) if years else None,
                    "data_freshness": "unavailable",
                },
            )
        gallery_slug = str(latest["slug"])
        gallery = await self.gallery(gallery_slug)
        media = gallery.get("media")
        if not isinstance(media, list):
            raise EnergyDataError(
                ErrorCode.PARSING_ERROR,
                "TEİAŞ galeri şeması beklenen medya listesini içermiyor.",
                source="TEİAŞ",
            )
        require_all = tuple(selector.get("require_all", ()))
        require_any = tuple(selector.get("require_any", ()))
        exclude_any = tuple(selector.get("exclude_any", ()))
        matches = [
            item
            for item in media
            if isinstance(item, dict)
            and normalize_key(str(item.get("extension", ""))) in {"xlsx", "xls"}
            and title_matches(
                str(item.get("title") or item.get("name") or ""),
                require_all=require_all,
                require_any=require_any,
                exclude_any=exclude_any,
            )
        ]
        if not matches:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "TEİAŞ galerisinde eşleşen Excel dosyası bulunamadı.",
                source="TEİAŞ",
                details={"gallery": gallery_slug, "dataset": selector_key},
            )
        # Keep only files covering the newest end period, then all of them
        # (TEİAŞ sometimes publishes duplicate hydro tables).
        ranked = []
        for item in matches:
            title = str(item.get("title") or item.get("name") or "")
            start_year, end_year = extract_year_period(title)
            ranked.append((end_year or -1, item))
        newest_end = max(end for end, _ in ranked)
        selected = [item for end, item in ranked if end == newest_end]
        workbooks: list[Workbook] = []
        for item in selected:
            workbooks.append(await self.workbook_from_media(gallery_slug, item))
        return workbooks

    async def monthly_workbook(self, year: int) -> Workbook:
        galleries = await self.list_galleries()
        slug = f"{year}-{MONTHLY_GALLERY_PREFIX}"
        matched = next(
            (
                item
                for item in galleries
                if isinstance(item, dict) and item.get("slug") == slug
            ),
            None,
        )
        if matched is None:
            monthly_years = sorted(
                {
                    year_value
                    for item in galleries
                    if isinstance(item, dict)
                    and isinstance(item.get("slug"), str)
                    and str(item["slug"]).endswith(MONTHLY_GALLERY_PREFIX)
                    for year_value in [slug_period_year(str(item["slug"]))]
                    if year_value is not None
                }
            )
            latest = str(max(monthly_years)) if monthly_years else None
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "İstenen yıl için TEİAŞ aylık resmi serisi bulunamadı.",
                source="TEİAŞ",
                details={
                    "requested_period": str(year),
                    "latest_available_period": latest,
                    "data_freshness": "unavailable",
                    "available_years": monthly_years,
                },
            )
        return await self.workbook(
            slug,
            ttl_seconds=self.settings.cache_monthly_ttl_seconds,
        )
