import math
import re
import unicodedata
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..exceptions import EnergyDataError, ErrorCode

ISTANBUL = ZoneInfo("Europe/Istanbul")
NULL_VALUES = {"", "-", "—", "n/a", "na", "null", "yok"}


def normalize_turkish_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)

    text = str(value).strip()
    if text.casefold() in NULL_VALUES:
        return None
    text = re.sub(r"[^\d,.\-+]", "", text)
    if not text:
        return None

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")

    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y"):
        try:
            parsed = datetime.strptime(value.strip(), fmt)
            return parsed.date()
        except ValueError:
            continue
    raise EnergyDataError(
        ErrorCode.INVALID_PARAMETER,
        "Tarih YYYY-MM-DD veya GG.AA.YYYY biçiminde olmalıdır.",
    )


def parse_year_range(start_year: int, end_year: int) -> tuple[int, int]:
    if not 1900 <= start_year <= 2100 or not 1900 <= end_year <= 2100:
        raise EnergyDataError(ErrorCode.INVALID_PARAMETER, "Yıl 1900-2100 aralığında olmalıdır.")
    if start_year > end_year:
        raise EnergyDataError(
            ErrorCode.INVALID_PARAMETER,
            "Başlangıç yılı bitiş yılından büyük olamaz.",
        )
    return start_year, end_year


def normalize_key(value: str) -> str:
    text = value.replace("İ", "I").replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char)).casefold().strip()


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def number(value: Any, digits: int = 6) -> float | None:
    parsed = normalize_turkish_number(value)
    return None if parsed is None else round(parsed, digits)


def normalize_energy(value: float, from_unit: str, to_unit: str) -> float:
    """Convert energy units; power units are deliberately rejected."""
    factors_to_mwh = {"MWh": 1.0, "GWh": 1_000.0, "TWh": 1_000_000.0}
    if from_unit not in factors_to_mwh or to_unit not in factors_to_mwh:
        raise EnergyDataError(
            ErrorCode.INVALID_PARAMETER,
            "Yalnız enerji birimleri MWh, GWh ve TWh dönüştürülebilir; MW güç birimidir.",
        )
    return value * factors_to_mwh[from_unit] / factors_to_mwh[to_unit]
