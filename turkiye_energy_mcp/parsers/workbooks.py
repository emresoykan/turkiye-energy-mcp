from io import BytesIO
from typing import Any

import pandas as pd

from ..exceptions import EnergyDataError, ErrorCode
from .common import clean_text, normalize_key, number

MONTHS = [
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
    "11",
    "12",
]

SOURCE_ALIASES = {
    "hard coal + imported coal": "coal_imported_and_hard",
    "lignite": "lignite",
    "liquid fuels": "liquid_fuels",
    "naturl gas +lng": "natural_gas",
    "natural gas +lng": "natural_gas",
    "renew and wastes": "waste_and_biomass",
    "thermal": "thermal",
    "hydro": "hydro",
    "geothermal": "geothermal",
    "geoht ermal": "geothermal",
    "geo hthermal": "geothermal",
    "wind": "wind",
    "solar": "solar",
    "gross generation": "total_generation",
    "imports": "imports",
    "exports": "exports",
    "gross demand": "gross_demand",
    "hard coal": "hard_coal",
    "imported coal": "imported_coal",
    "natural gas": "natural_gas",
    "fuel oil": "fuel_oil",
    "diesel oil": "diesel_oil",
    "naphtha": "naphtha",
    "renew.+wastes + waste heat": "waste_and_biomass",
    "geotermal +wind": "geothermal_and_wind",
    "hydro+jeothermal+wind total": "hydro_geothermal_wind",
    "hydro+jeothermal+wind+solar total": "renewable_total",
    "total": "total",
}


def _frame(content: bytes, sheet_name: str | int | None = 0) -> pd.DataFrame:
    try:
        return pd.read_excel(BytesIO(content), sheet_name=sheet_name, header=None)
    except Exception as exc:
        raise EnergyDataError(
            ErrorCode.PARSING_ERROR,
            "TEİAŞ Excel çalışma kitabı ayrıştırılamadı.",
            source="TEİAŞ",
        ) from exc


def _is_year(value: Any, minimum: int, maximum: int) -> bool:
    parsed = number(value, 0)
    return parsed is not None and minimum <= parsed <= maximum and parsed.is_integer()


def parse_monthly_energy(content: bytes, year: int) -> list[dict[str, Any]]:
    df = _frame(content, "Kaynaklara Göre")
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        label = clean_text(row.iloc[1] if len(row) > 1 else None)
        if not label:
            continue
        source = SOURCE_ALIASES.get(normalize_key(label))
        if source is None:
            continue
        for index, month in enumerate(MONTHS, start=2):
            value = number(row.iloc[index] if index < len(row) else None)
            if value is not None:
                records.append(
                    {"date": f"{year}-{month}", "metric": source, "value": value}
                )
    return records


def parse_monthly_euas_generation(content: bytes, year: int) -> list[dict[str, Any]]:
    df = _frame(content, "Kuruluşlara Göre")
    records: list[dict[str, Any]] = []
    in_euas = False
    for _, row in df.iterrows():
        organization = clean_text(row.iloc[1] if len(row) > 1 else None)
        label = clean_text(row.iloc[2] if len(row) > 2 else None)
        if organization and normalize_key(organization) == "euas":
            in_euas = True
            continue
        if in_euas and organization:
            break
        if not in_euas or not label:
            continue
        source = SOURCE_ALIASES.get(normalize_key(label))
        if source not in {"thermal", "hydro", "geothermal", "wind", "total"}:
            continue
        for index, month in enumerate(MONTHS, start=3):
            value = number(row.iloc[index] if index < len(row) else None)
            if value is not None:
                records.append(
                    {"date": f"{year}-{month}", "source": source, "generation_gwh": value}
                )
    return records


def parse_capacity_mix(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    columns = {
        3: "hard_coal",
        4: "lignite",
        5: "liquid_fuels",
        6: "natural_gas",
        7: "waste_and_biomass",
        8: "single_fuel_total",
        9: "solid_and_liquid_multi_fuel",
        10: "liquid_and_gas_multi_fuel",
        11: "multi_fuel_total",
        12: "thermal",
        13: "hydro",
        14: "geothermal",
        15: "wind",
        16: "solar",
        17: "total",
    }
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if len(row) <= 17 or not _is_year(row.iloc[2], 1900, 2100):
            continue
        year = int(float(row.iloc[2]))
        for index, source in columns.items():
            value = number(row.iloc[index])
            if value is not None:
                records.append({"year": year, "source": source, "capacity_mw": value})
    return records


def parse_generation_mix(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    columns = {
        2: "hard_coal_and_asphaltite",
        3: "imported_coal",
        4: "lignite",
        5: "coal_total",
        6: "fuel_oil",
        7: "diesel_oil",
        8: "lpg",
        9: "naphtha",
        10: "liquid_fuels_total",
        11: "natural_gas",
        12: "waste_and_biomass",
        13: "thermal",
        14: "hydro",
        15: "geothermal_and_wind",
        16: "solar",
        17: "total",
    }
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if len(row) <= 17 or not _is_year(row.iloc[1], 1900, 2100):
            continue
        year = int(float(row.iloc[1]))
        for index, source in columns.items():
            value = number(row.iloc[index])
            if value is not None:
                records.append({"year": year, "source": source, "generation_gwh": value})
    return records


def parse_peak_demand(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if len(row) <= 7 or not _is_year(row.iloc[1], 1900, 2100):
            continue
        records.append(
            {
                "year": int(float(row.iloc[1])),
                "installed_capacity_mw": number(row.iloc[2]),
                "instantaneous_peak_mw": number(row.iloc[3]),
                "hourly_peak_mw": number(row.iloc[4]),
                "gross_demand_gwh": number(row.iloc[5]),
                "average_generation_capacity_gwh": number(row.iloc[6]),
                "firm_generation_capacity_gwh": number(row.iloc[7]),
            }
        )
    return records


def parse_energy_balance(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    countries_import = {
        7: "Bulgaria",
        8: "Greece",
        9: "Azerbaijan",
        10: "Georgia",
        11: "Syria",
        12: "Iran",
    }
    countries_export = {
        14: "Bulgaria",
        15: "Georgia",
        16: "Azerbaijan",
        17: "Iran",
        18: "Iraq",
        19: "Syria",
        20: "Greece",
    }
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if len(row) <= 22 or not _is_year(row.iloc[1], 1900, 2100):
            continue
        year = int(float(row.iloc[1]))
        base = {
            "year": year,
            "generation_gwh": number(row.iloc[5]),
            "imports_gwh": number(row.iloc[13]),
            "exports_gwh": number(row.iloc[21]),
            "gross_demand_gwh": number(row.iloc[22]),
            "import_by_country_gwh": {
                country: number(row.iloc[index]) or 0.0
                for index, country in countries_import.items()
            },
            "export_by_country_gwh": {
                country: number(row.iloc[index]) or 0.0
                for index, country in countries_export.items()
            },
        }
        imports = base["imports_gwh"] or 0.0
        exports = base["exports_gwh"] or 0.0
        base["net_import_gwh"] = round(imports - exports, 6)
        records.append(base)
    return records


def parse_transmission_lines(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if len(row) <= 6 or not _is_year(row.iloc[1], 1900, 2100):
            continue
        records.append(
            {
                "year": int(float(row.iloc[1])),
                "line_380kv_km": number(row.iloc[2]),
                "line_220kv_km": number(row.iloc[3]),
                "line_154kv_km": number(row.iloc[4]),
                "line_66kv_km": number(row.iloc[5]),
                "total_line_km": number(row.iloc[6]),
            }
        )
    return records


def parse_transformers(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if len(row) <= 9 or not _is_year(row.iloc[1], 1900, 2100):
            continue
        records.append(
            {
                "year": int(float(row.iloc[1])),
                "transformers_380kv": int(number(row.iloc[2]) or 0),
                "capacity_380kv_mva": number(row.iloc[3]),
                "transformers_154kv": int(number(row.iloc[4]) or 0),
                "capacity_154kv_mva": number(row.iloc[5]),
                "transformers_66kv_and_below": int(number(row.iloc[6]) or 0),
                "capacity_66kv_and_below_mva": number(row.iloc[7]),
                "total_transformers": int(number(row.iloc[8]) or 0),
                "total_capacity_mva": number(row.iloc[9]),
            }
        )
    return records


def parse_capacity_by_organization(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    years = {
        index: int(float(value))
        for index, value in enumerate(df.iloc[6].tolist())
        if _is_year(value, 1900, 2100)
    }
    result: dict[int, dict[str, Any]] = {
        year: {"year": year} for year in years.values()
    }
    row_mapping = {8: "thermal_mw", 10: "renewable_mw", 12: "total_mw"}
    for row_index, field in row_mapping.items():
        for column, year in years.items():
            result[year][field] = number(df.iloc[row_index, column])
    return list(result.values())


def parse_generation_by_organization(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if len(row) <= 11 or not _is_year(row.iloc[1], 1900, 2100):
            continue
        records.append(
            {
                "year": int(float(row.iloc[1])),
                "euas_generation_gwh": number(row.iloc[2]),
                "turkey_generation_gwh": number(row.iloc[11]),
            }
        )
    return records


def parse_euas_generation_by_source(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    years = {
        index: int(float(value))
        for index, value in enumerate(df.iloc[6].tolist())
        if _is_year(value, 1900, 2100)
    }
    result: list[dict[str, Any]] = []
    for row_index in range(8, 18):
        source_label = clean_text(df.iloc[row_index, 3])
        source = SOURCE_ALIASES.get(normalize_key(source_label or ""))
        if not source:
            continue
        for column, year in years.items():
            value = number(df.iloc[row_index, column])
            if value is not None:
                result.append({"year": year, "source": source, "generation_gwh": value})
    return result


def parse_euas_thermal_plants(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    records: list[dict[str, Any]] = []
    current_source: str | None = None
    for _, row in df.iterrows():
        if len(row) <= 18 or number(row.iloc[2], 0) is None:
            continue
        source_text = clean_text(row.iloc[4])
        if source_text and source_text not in {'"', "'"} and normalize_key(source_text) not in {
            "lignite",
            "diesel oil",
        }:
            current_source = source_text
        elif source_text and source_text not in {'"', "'"}:
            aliases = {"lignite": "Linyit", "diesel oil": "Motorin"}
            current_source = aliases.get(normalize_key(source_text), source_text)
        records.append(
            {
                "name": clean_text(row.iloc[3]),
                "plant_type": "thermal",
                "source": current_source,
                "province": clean_text(row.iloc[5]),
                "installed_capacity_mw": number(row.iloc[16]),
                "gross_generation_gwh": number(row.iloc[17]),
                "project_generation_gwh": number(row.iloc[18]),
                "reference_year": 2024,
            }
        )
    return [record for record in records if record["name"]]


def parse_euas_hydro_plants(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if len(row) <= 20 or number(row.iloc[2], 0) is None:
            continue
        records.append(
            {
                "name": clean_text(row.iloc[4]),
                "plant_type": "hydro",
                "source": "Hidroelektrik",
                "province": clean_text(row.iloc[6]),
                "installed_capacity_mw": number(row.iloc[17]),
                "gross_generation_gwh": number(row.iloc[18]),
                "average_project_generation_gwh": number(row.iloc[19]),
                "firm_project_generation_gwh": number(row.iloc[20]),
                "reference_year": 2024,
            }
        )
    return [record for record in records if record["name"]]
