import logging
import math
import re
import unicodedata
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..exceptions import EnergyDataError, ErrorCode

ISTANBUL = ZoneInfo("Europe/Istanbul")
NULL_VALUES = {"", "-", "—", "n/a", "na", "null", "yok"}
logger = logging.getLogger(__name__)


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


def normalize_plant_name(value: str | None) -> str | None:
    """Normalize plant names for equality joins across TEİAŞ label variants."""
    if not value:
        return None
    text = normalize_key(value)
    text = text.replace("hes", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def annual_energy_ceiling_gwh(capacity_mw: float | None) -> float | None:
    if capacity_mw is None or capacity_mw <= 0:
        return None
    return capacity_mw * 8760.0 / 1000.0


def guard_project_generation(
    *,
    plant_name: str | None,
    capacity_mw: float | None,
    gross_generation_gwh: float | None,
    average_gwh: float | None,
    firm_gwh: float | None,
) -> tuple[float | None, float | None, list[str], list[str]]:
    """Drop an entire suspicious project-generation record.

    TEİAŞ hydro workbooks sometimes publish average/firm project GWh cells that
    are inconsistent with the same row's MW or gross generation. If either
    project field fails a check, both are nulled to avoid returning a partial,
    internally inconsistent project-generation pair.
    """
    ceiling = annual_energy_ceiling_gwh(capacity_mw)
    drop_reasons: list[str] = []
    suspect_reasons: list[str] = []

    for field, value in (
        ("average_project_generation_gwh", average_gwh),
        ("firm_project_generation_gwh", firm_gwh),
    ):
        if value is not None and ceiling is not None and value > ceiling:
            drop_reasons.append(f"{field}_exceeds_capacity_ceiling")
            logger.warning(
                "Suspicious project generation for %s: %s=%s GWh exceeds "
                "ceiling=%.3f GWh at %.3f MW",
                plant_name,
                field,
                value,
                ceiling,
                capacity_mw,
            )

    if (
        gross_generation_gwh is not None
        and gross_generation_gwh >= 0
        and average_gwh is not None
        and average_gwh >= 0
    ):
        if gross_generation_gwh == 0 or average_gwh == 0:
            ratio = float("inf") if gross_generation_gwh != average_gwh else 1.0
        else:
            ratio = max(
                gross_generation_gwh / average_gwh,
                average_gwh / gross_generation_gwh,
            )
        if ratio > 3:
            suspect_reasons.append("average_vs_gross_ratio_exceeds_3x")
            logger.warning(
                "Marking project generation suspect for %s without dropping it: "
                "average=%s GWh and gross=%s GWh differ by %.3fx",
                plant_name,
                average_gwh,
                gross_generation_gwh,
                ratio,
            )

    if drop_reasons:
        logger.warning(
            "Dropping project-generation pair for %s: reasons=%s",
            plant_name,
            ",".join(drop_reasons),
        )
        return None, None, drop_reasons, suspect_reasons
    return average_gwh, firm_gwh, drop_reasons, suspect_reasons


def guard_single_project_generation(
    *,
    plant_name: str | None,
    capacity_mw: float | None,
    gross_generation_gwh: float | None,
    project_generation_gwh: float | None,
) -> tuple[float | None, list[str], list[str]]:
    """Validate a thermal plant's single project-generation field."""
    if project_generation_gwh is None:
        return None, [], []

    drop_reasons: list[str] = []
    suspect_reasons: list[str] = []
    ceiling = annual_energy_ceiling_gwh(capacity_mw)
    if ceiling is not None and project_generation_gwh > ceiling:
        drop_reasons.append("project_generation_gwh_exceeds_capacity_ceiling")
        logger.warning(
            "Suspicious thermal project generation for %s: value=%s GWh "
            "exceeds ceiling=%.3f GWh at %.3f MW",
            plant_name,
            project_generation_gwh,
            ceiling,
            capacity_mw,
        )

    if (
        gross_generation_gwh is not None
        and gross_generation_gwh >= 0
        and project_generation_gwh >= 0
    ):
        if gross_generation_gwh == 0 or project_generation_gwh == 0:
            ratio = float("inf") if gross_generation_gwh != project_generation_gwh else 1.0
        else:
            ratio = max(
                gross_generation_gwh / project_generation_gwh,
                project_generation_gwh / gross_generation_gwh,
            )
        if ratio > 3:
            suspect_reasons.append("project_vs_gross_ratio_exceeds_3x")
            logger.warning(
                "Marking thermal project generation suspect for %s without "
                "dropping it: project=%s GWh and gross=%s GWh differ by %.3fx",
                plant_name,
                project_generation_gwh,
                gross_generation_gwh,
                ratio,
            )

    if drop_reasons:
        logger.warning(
            "Dropping thermal project generation for %s: reasons=%s",
            plant_name,
            ",".join(drop_reasons),
        )
        return None, drop_reasons, suspect_reasons
    return project_generation_gwh, drop_reasons, suspect_reasons


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
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
