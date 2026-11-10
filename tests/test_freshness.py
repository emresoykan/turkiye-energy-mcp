from datetime import date, datetime, timezone

from turkiye_energy_mcp import freshness
from turkiye_energy_mcp.clients.teias import TeiasClient
from turkiye_energy_mcp.config import Settings
from turkiye_energy_mcp.freshness import (
    MONTHLY_GALLERY_PREFIX,
    classify_annual_freshness,
    classify_monthly_freshness,
    extract_month_period,
    extract_year_period,
    select_latest_gallery,
    select_newest_media,
)


def test_extract_year_period_supports_two_digit_ends():
    assert extract_year_period("Dağılımı(1970-83)") == (1970, 1983)
    assert extract_year_period("Dağılımı(1984-94)") == (1984, 1994)
    assert extract_year_period("Gelişimi (2006-2024)") == (2006, 2024)
    assert extract_year_period("Santralları (2024)") == (2024, 2024)


def test_extract_month_period_from_monthly_title():
    assert extract_month_period("Haziran 2026 Elektrik Üretim-Tüketim Raporu", 2026) == "2026-06"
    assert extract_month_period("Aralık 2025 Elektrik Üretim-Tüketim Raporu") == "2025-12"


def test_select_latest_gallery_by_period_then_publish_date():
    galleries = [
        {
            "slug": "i-kurulu-guc-2023",
            "publish_at": "2024-10-07T08:00:00Z",
        },
        {
            "slug": "i-kurulu-guc-2024",
            "publish_at": "2025-09-30T08:00:00Z",
        },
        {
            "slug": "i-kurulu-guc-2024-draft",
            "publish_at": "2026-01-01T00:00:00Z",
        },
    ]
    selected = select_latest_gallery(galleries, "i-kurulu-guc")
    assert selected is not None
    assert selected["slug"] == "i-kurulu-guc-2024"


def test_select_newest_media_prefers_latest_and_widest_period():
    media = [
        {
            "title": "Kaynak (2006-2023)",
            "extension": "xls",
            "slug": "a",
            "created_at": "2024-01-01T00:00:00Z",
        },
        {
            "title": "Kaynak (2024)",
            "extension": "xls",
            "slug": "b",
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "title": "Kaynak (2006-2024)",
            "extension": "xls",
            "slug": "c",
            "created_at": "2025-01-01T00:00:00Z",
        },
    ]
    selected = select_newest_media(media, require_all=("kaynak",))
    assert selected is not None
    assert selected["slug"] == "c"


def test_annual_freshness_labels():
    assert (
        classify_annual_freshness(
            requested_start=2020,
            requested_end=2024,
            latest_available_year=2024,
            returned_years=[2020, 2021, 2022, 2023, 2024],
        )
        == "current"
    )
    assert (
        classify_annual_freshness(
            requested_start=2015,
            requested_end=2018,
            latest_available_year=2024,
            returned_years=[2015, 2016, 2017, 2018],
        )
        == "historical"
    )
    assert (
        classify_annual_freshness(
            requested_start=2020,
            requested_end=2027,
            latest_available_year=2024,
            returned_years=[2020, 2021, 2022, 2023, 2024],
        )
        == "partial"
    )


def test_monthly_freshness_labels():
    assert (
        classify_monthly_freshness(
            requested_start=date(2026, 1, 1),
            requested_end=date(2026, 12, 31),
            latest_available_period="2026-06",
            returned_periods=["2026-01", "2026-02", "2026-06"],
            current_date=date(2026, 8, 11),
        )
        == "partial"
    )
    assert (
        classify_monthly_freshness(
            requested_start=date(2024, 1, 1),
            requested_end=date(2024, 12, 31),
            latest_available_period="2026-06",
            returned_periods=[f"2024-{month:02d}" for month in range(1, 13)],
            current_date=date(2026, 8, 11),
        )
        == "historical"
    )


def test_monthly_freshness_uses_istanbul_calendar(monkeypatch):
    # 2026-12-31 22:30 UTC is already 2027-01-01 01:30 in Istanbul: December 2026 is
    # a closed month there, although the (UTC) container clock still says December.
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(freshness, "datetime", Clock)
    assert (
        classify_monthly_freshness(
            requested_start=date(2026, 12, 1),
            requested_end=date(2026, 12, 31),
            latest_available_period="2026-12",
            returned_periods=["2026-12"],
        )
        == "historical"
    )


class _RecordingCache:
    def __init__(self) -> None:
        self.ttls: dict[str, int] = {}

    async def get_or_set(self, key, loader, ttl_seconds):
        self.ttls[key] = ttl_seconds
        return await loader()


class _GalleryHttp:
    async def get_json(self, url, params=None):
        return {"success": True, "payload": {"slug": params["slug"], "media": []}}


async def test_monthly_gallery_listing_uses_monthly_ttl():
    settings = Settings(cache_monthly_ttl_seconds=21600, cache_historical_ttl_seconds=86400)
    cache = _RecordingCache()
    client = TeiasClient(settings, _GalleryHttp(), cache)
    await client.gallery(f"2026-{MONTHLY_GALLERY_PREFIX}")
    await client.gallery("i-kurulu-guc-2025")
    assert cache.ttls == {
        f"teias:gallery:2026-{MONTHLY_GALLERY_PREFIX}": 21600,
        "teias:gallery:i-kurulu-guc-2025": 86400,
    }
