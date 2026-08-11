"""Official-source freshness selection helpers.

Rules:
- Prefer the newest publication covering the most recent period.
- Never hard-code report years or filenames.
- Historical queries still use the official series that covers those dates
  (normally the newest cumulative annual workbook).
- When requested current data is unavailable, expose latest_available_period
  and data_freshness explicitly instead of silently substituting older data.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from .parsers.common import normalize_key

DataFreshness = Literal[
    "current",
    "provisional",
    "historical",
    "partial",
    "unavailable",
]

_PERIOD_RANGE = re.compile(r"\((\d{4})\s*[-–]\s*(\d{2,4})\)")
_PERIOD_SINGLE = re.compile(r"\((\d{4})\)")
_YEAR_IN_SLUG = re.compile(r"(?:^|-)(\d{4})(?:-|$)")
_MONTH_NAMES = {
    "ocak": 1,
    "subat": 2,
    "mart": 3,
    "nisan": 4,
    "mayis": 5,
    "haziran": 6,
    "temmuz": 7,
    "agustos": 8,
    "eylul": 9,
    "ekim": 10,
    "kasim": 11,
    "aralik": 12,
}


def parse_timestamp(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def publication_timestamp(item: dict[str, Any]) -> datetime:
    for key in ("publish_at", "updated_at", "created_at"):
        parsed = parse_timestamp(item.get(key))
        if parsed is not None:
            return parsed
    return datetime.min


def extract_year_period(text: str) -> tuple[int | None, int | None]:
    match = _PERIOD_RANGE.search(text)
    if match:
        start = int(match.group(1))
        end_raw = match.group(2)
        if len(end_raw) == 2:
            end = (start // 100) * 100 + int(end_raw)
            if end < start:
                end += 100
        else:
            end = int(end_raw)
        return start, end
    match = _PERIOD_SINGLE.search(text)
    if match:
        year = int(match.group(1))
        return year, year
    years = [int(value) for value in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", text)]
    if not years:
        return None, None
    return min(years), max(years)


def slug_period_year(slug: str) -> int | None:
    # Annual galleries end with -<year>; monthly galleries start with <year>-.
    if match := re.search(r"-(\d{4})$", slug):
        return int(match.group(1))
    if match := re.match(r"^(\d{4})-", slug):
        return int(match.group(1))
    match = _YEAR_IN_SLUG.search(slug)
    return int(match.group(1)) if match else None


def extract_month_period(text: str, fallback_year: int | None = None) -> str | None:
    key = normalize_key(text)
    year_match = re.search(r"(19\d{2}|20\d{2})", key)
    year = int(year_match.group(1)) if year_match else fallback_year
    for name, month in _MONTH_NAMES.items():
        if name in key and year is not None:
            return f"{year}-{month:02d}"
    if year is not None:
        return str(year)
    return None


def title_matches(
    title: str,
    *,
    require_all: tuple[str, ...] = (),
    require_any: tuple[str, ...] = (),
    exclude_any: tuple[str, ...] = (),
) -> bool:
    key = normalize_key(title)
    if any(token in key for token in exclude_any):
        return False
    if require_all and not all(token in key for token in require_all):
        return False
    if require_any and not any(token in key for token in require_any):
        return False
    return True


def select_newest_media(
    media: list[dict[str, Any]],
    *,
    require_all: tuple[str, ...] = (),
    require_any: tuple[str, ...] = (),
    exclude_any: tuple[str, ...] = (),
    extensions: tuple[str, ...] = ("xlsx", "xls"),
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for item in media:
        if not isinstance(item, dict):
            continue
        extension = normalize_key(str(item.get("extension", "")))
        if extension not in extensions:
            continue
        title = str(item.get("title") or item.get("name") or "")
        if not title_matches(
            title,
            require_all=require_all,
            require_any=require_any,
            exclude_any=exclude_any,
        ):
            continue
        start_year, end_year = extract_year_period(title)
        span = 0
        if start_year is not None and end_year is not None:
            span = end_year - start_year
        candidates.append(
            {
                "item": item,
                "end_year": end_year if end_year is not None else -1,
                "span": span,
                "start_year": start_year if start_year is not None else -1,
                "published": publication_timestamp(item),
            }
        )
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda row: (row["end_year"], row["span"], row["published"], row["start_year"]),
    )
    return best["item"]


def select_latest_gallery(
    galleries: list[dict[str, Any]],
    gallery_prefix: str,
) -> dict[str, Any] | None:
    pattern = re.compile(rf"^{re.escape(gallery_prefix)}-(\d{{4}})$")
    candidates: list[dict[str, Any]] = []
    for item in galleries:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str):
            continue
        match = pattern.match(slug)
        if not match:
            continue
        candidates.append(
            {
                "item": item,
                "year": int(match.group(1)),
                "published": publication_timestamp(item),
            }
        )
    if not candidates:
        return None
    best = max(candidates, key=lambda row: (row["year"], row["published"]))
    return best["item"]


def select_gallery_for_year(
    galleries: list[dict[str, Any]],
    gallery_prefix: str,
    year: int,
) -> dict[str, Any] | None:
    slug = f"{gallery_prefix}-{year}"
    for item in galleries:
        if isinstance(item, dict) and item.get("slug") == slug:
            return item
    return None


def available_years(galleries: list[dict[str, Any]], gallery_prefix: str) -> list[int]:
    prefix = f"{gallery_prefix}-"
    years: set[int] = set()
    for item in galleries:
        slug = item.get("slug") if isinstance(item, dict) else None
        if isinstance(slug, str) and slug.startswith(prefix):
            year = slug_period_year(slug)
            if year is not None:
                years.add(year)
    return sorted(years)


def classify_annual_freshness(
    *,
    requested_start: int,
    requested_end: int,
    latest_available_year: int,
    returned_years: list[int],
) -> DataFreshness:
    if not returned_years:
        return "unavailable"
    actual_start = min(returned_years)
    actual_end = max(returned_years)
    if requested_end > latest_available_year or actual_end < requested_end or actual_start > requested_start:
        return "partial"
    if requested_end < latest_available_year:
        return "historical"
    return "current"


def classify_monthly_freshness(
    *,
    requested_start: date,
    requested_end: date,
    latest_available_period: str | None,
    returned_periods: list[str],
    current_date: date | None = None,
) -> DataFreshness:
    today = current_date or date.today()
    if not returned_periods:
        return "unavailable"
    actual_start = min(returned_periods)
    actual_end = max(returned_periods)
    requested_start_period = requested_start.strftime("%Y-%m")
    requested_end_period = requested_end.strftime("%Y-%m")
    if actual_start > requested_start_period or actual_end < requested_end_period:
        return "partial"
    if latest_available_period and requested_end_period < latest_available_period:
        return "historical"
    if requested_end.year == today.year and requested_end_period >= f"{today.year}-{today.month:02d}":
        return "provisional"
    if requested_end.year < today.year:
        return "historical"
    return "provisional"


# Semantic selectors replace hard-coded filenames / table numbers.
ANNUAL_SELECTORS: dict[str, dict[str, Any]] = {
    "capacity_mix": {
        "gallery_prefix": "i-kurulu-guc",
        "require_all": ("birincil enerji kaynak", "kurulu guc"),
        "exclude_any": ("uretici kurulus",),
    },
    "capacity_by_organization": {
        "gallery_prefix": "i-kurulu-guc",
        "require_all": ("uretici kurulus", "kurulu guc", "yillar"),
        "exclude_any": ("birincil",),
    },
    "euas_thermal_plants": {
        "gallery_prefix": "i-kurulu-guc",
        "require_all": ("euas", "termik"),
    },
    "euas_hydro_plants": {
        "gallery_prefix": "i-kurulu-guc",
        "require_all": ("euas", "hidrolik"),
    },
    "generation_mix": {
        "gallery_prefix": "iii-elektrik-enerjisi-uretimi-tuketimi-kayiplar",
        "require_all": ("birincil enerji kaynak", "yillar"),
        "exclude_any": ("pay", "uretici kur", "aylik", "aylara"),
    },
    "energy_balance": {
        "gallery_prefix": "iii-elektrik-enerjisi-uretimi-tuketimi-kayiplar",
        "require_all": ("ithalat", "ihracat"),
        "require_any": ("uretim", "ur."),
        "exclude_any": ("aylik", "aylara", "uretici kur"),
    },
    "generation_by_organization": {
        "gallery_prefix": "iii-elektrik-enerjisi-uretimi-tuketimi-kayiplar",
        "require_all": ("uretici kurulus", "yillar"),
        "exclude_any": ("birincil", "aylara", "aylik", "ithalat", "ihracat"),
    },
    "euas_generation_by_source": {
        "gallery_prefix": "iii-elektrik-enerjisi-uretimi-tuketimi-kayiplar",
        "require_all": ("uretici kur", "birincil", "yillar"),
        "exclude_any": ("aylik", "aylara"),
    },
    "peak_demand": {
        "gallery_prefix": "ii-turkiye-kurulu-gucunun-kullanim-degerleri",
        "require_all": ("puant", "kurulu guc"),
        "exclude_any": ("katki", "aylara", "ozellik"),
    },
    "transmission_lines": {
        "gallery_prefix": "vi-enerji-nakil-hat-ve-trafolari",
        "require_all": ("turkiye iletim hat",),
        "exclude_any": ("yer alti", "kablo"),
    },
    "transformers": {
        "gallery_prefix": "vi-enerji-nakil-hat-ve-trafolari",
        "require_all": ("turkiye trafo",),
        "exclude_any": ("iletim trafo",),
    },
}

MONTHLY_GALLERY_PREFIX = "yili-aylik-elektrik-uretim-tuketim-raporlari"
