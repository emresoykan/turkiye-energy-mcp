from collections.abc import Callable
from io import BytesIO
import re
from typing import Any

import pandas as pd

from ..exceptions import EnergyDataError, ErrorCode
from .common import (
    clean_text,
    guard_project_generation,
    guard_single_project_generation,
    logger,
    normalize_key,
    normalize_plant_name,
    number,
)

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
    "geohtermal": "geothermal",  # TEİAŞ aylık rapor yazımı ("GEOHTERMAL")
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
    "thermal total": "thermal",
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
    if isinstance(value, str) and not re.fullmatch(r"\s*\d{4}(?:\.0+)?\s*", value):
        return False
    parsed = number(value, 0)
    return parsed is not None and minimum <= parsed <= maximum and parsed.is_integer()


def _cell(row: pd.Series, index: int) -> Any:
    return row.iloc[index] if index < len(row) else None


# --- Layout guards -------------------------------------------------------------
# The yearly TEİAŞ tables are read at fixed rows/columns. If TEİAŞ inserts a row or
# column, positional reads would silently return a neighbouring series (e.g. a
# different utility's MW or another fuel's GWh). These helpers anchor the positions
# to the printed header labels and raise PARSING_ERROR instead of returning
# shifted numbers. Labels are compared Turkish-insensitively (İ/ı, ş, ğ, ü, ...).


def _label(value: Any) -> str:
    text = clean_text(value)
    return normalize_key(text) if text else ""


def _has_token(text: str, token: str) -> bool:
    # Token must start a word, so "thermal" does not match inside "geothermal".
    return re.search(rf"(?<![a-z]){re.escape(token)}", text) is not None


def _layout_error(table: str, problem: str, **details: Any) -> EnergyDataError:
    return EnergyDataError(
        ErrorCode.PARSING_ERROR,
        f"TEİAŞ '{table}' tablosunun düzeni beklenenden farklı: {problem}. "
        "Kaymış değer döndürmemek için ayrıştırma durduruldu.",
        source="TEİAŞ",
        details={"table": table, **details},
    )


def _cell_label(df: pd.DataFrame, row: int, column: int) -> str:
    if 0 <= row < len(df) and 0 <= column < df.shape[1]:
        return _label(df.iat[row, column])
    return ""


def _find_label(
    df: pd.DataFrame,
    table: str,
    expected: str,
    matches: Callable[[str], bool],
    *,
    column: int | None = None,
    start_row: int = 0,
    max_rows: int = 25,
) -> tuple[int, int]:
    columns = [column] if column is not None else range(df.shape[1])
    for row in range(start_row, min(len(df), start_row + max_rows)):
        for col in columns:
            if matches(_cell_label(df, row, col)):
                return row, col
    raise _layout_error(
        table,
        f"'{expected}' etiketi bulunamadı",
        expected_label=expected,
        expected_column=column,
    )


def _check_column_headers(
    df: pd.DataFrame,
    table: str,
    header_row: int,
    expected: dict[int, tuple[str, ...]],
) -> None:
    """Each fixed column's header (TR + EN rows) must contain one of its tokens."""
    rows = range(max(0, header_row - 2), header_row + 2)
    for column, tokens in expected.items():
        text = " ".join(filter(None, (_cell_label(df, row, column) for row in rows)))
        if not any(_has_token(text, token) for token in tokens):
            raise _layout_error(
                table,
                f"{column}. sütun başlığı beklenen etiketi içermiyor",
                column=column,
                expected_any=list(tokens),
                found=text or None,
            )


def _year_columns(df: pd.DataFrame, table: str, row: int) -> dict[int, int]:
    years = {
        index: int(float(value))
        for index, value in enumerate(df.iloc[row].tolist())
        if _is_year(value, 1900, 2100)
    }
    if not years:
        raise _layout_error(table, f"{row}. satırda yıl başlıkları yok", row=row)
    return years


# TR / EN month captions printed above the monthly value columns.
_MONTH_CAPTIONS: tuple[tuple[str, ...], ...] = (
    ("ocak", "january"),
    ("subat", "february"),
    ("mart", "march"),
    ("nisan", "april"),
    ("mayis", "may"),
    ("haziran", "june"),
    ("temmuz", "july"),
    ("agustos", "august"),
    ("eylul", "september"),
    ("ekim", "october"),
    ("kasim", "november"),
    ("aralik", "december"),
)


def _month_header_row(df: pd.DataFrame, table: str, first_column: int) -> int:
    """Row of the OCAK caption; January..December must sit in consecutive columns."""
    header_row, _ = _find_label(
        df, table, "OCAK", lambda text: text == "ocak", column=first_column
    )
    _check_column_headers(
        df,
        table,
        header_row,
        {first_column + offset: tokens for offset, tokens in enumerate(_MONTH_CAPTIONS)},
    )
    return header_row


def parse_monthly_energy(content: bytes, year: int) -> list[dict[str, Any]]:
    df = _frame(content, "Kaynaklara Göre")
    header_row = _month_header_row(df, "aylık üretim / birincil enerji kaynakları", 2)
    records: list[dict[str, Any]] = []
    for _, row in df.iloc[header_row + 1 :].iterrows():
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
    header_row = _month_header_row(df, "aylık üretim / üretici kuruluşlar", 3)
    records: list[dict[str, Any]] = []
    in_euas = False
    for _, row in df.iloc[header_row + 1 :].iterrows():
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


_CAPACITY_MIX_COLUMNS: dict[int, tuple[str, tuple[str, ...]]] = {
    3: ("hard_coal", ("taskomuru", "hard coal")),
    4: ("lignite", ("linyit", "lignite")),
    5: ("liquid_fuels", ("sivi yakit", "liquid fuel")),
    6: ("natural_gas", ("dogal gaz", "natural gas")),
    7: ("waste_and_biomass", ("atik", "waste")),
    8: ("single_fuel_total", ("toplam", "total")),
    9: ("solid_and_liquid_multi_fuel", ("kati+sivi", "solid+liquid")),
    10: ("liquid_and_gas_multi_fuel", ("sivi+d.gaz", "liquid+n.gas")),
    11: ("multi_fuel_total", ("toplam", "total")),
    12: ("thermal", ("termik", "thermal")),
    13: ("hydro", ("hidrolik", "hydro")),
    14: ("geothermal", ("jeotermal", "geothermal")),
    15: ("wind", ("ruzgar", "wind")),
    16: ("solar", ("gunes", "solar")),
    17: ("total", ("genel", "general")),
}


def parse_capacity_mix(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    table = "kurulu güç / birincil enerji kaynakları"
    header_row, _ = _find_label(df, table, "YILLAR", lambda text: text == "yillar", column=2)
    _check_column_headers(
        df,
        table,
        header_row,
        {index: tokens for index, (_, tokens) in _CAPACITY_MIX_COLUMNS.items()},
    )
    records: list[dict[str, Any]] = []
    for _, row in df.iloc[header_row + 1 :].iterrows():
        if len(row) <= 17 or not _is_year(row.iloc[2], 1900, 2100):
            continue
        year = int(float(row.iloc[2]))
        for index, (source, _) in _CAPACITY_MIX_COLUMNS.items():
            value = number(row.iloc[index])
            if value is not None:
                records.append({"year": year, "source": source, "capacity_mw": value})
    return records


_GENERATION_MIX_COLUMNS: dict[int, tuple[str, tuple[str, ...]]] = {
    2: ("hard_coal_and_asphaltite", ("taskomuru", "hard coal")),
    3: ("imported_coal", ("ithal komur", "imported coal")),
    4: ("lignite", ("linyit", "lignite")),
    5: ("coal_total", ("toplam", "total")),
    6: ("fuel_oil", ("fuel-oil", "fuel oil")),
    7: ("diesel_oil", ("motorin", "diesel")),
    8: ("lpg", ("lpg",)),
    9: ("naphtha", ("nafta", "naphtha")),
    10: ("liquid_fuels_total", ("toplam", "total")),
    11: ("natural_gas", ("dogal gaz", "natural gas")),
    12: ("waste_and_biomass", ("atik", "waste")),
    13: ("thermal", ("termik", "thermal")),
    14: ("hydro", ("hidrolik", "hydro")),
    15: ("geothermal_and_wind", ("ruzgar", "wind")),
    16: ("solar", ("gunes", "solar")),
    17: ("total", ("genel", "general")),
}


def parse_generation_mix(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    table = "brüt üretim / birincil enerji kaynakları"
    header_row, _ = _find_label(df, table, "YILLAR", lambda text: text == "yillar", column=1)
    _check_column_headers(
        df,
        table,
        header_row,
        {index: tokens for index, (_, tokens) in _GENERATION_MIX_COLUMNS.items()},
    )
    records: list[dict[str, Any]] = []
    for _, row in df.iloc[header_row + 1 :].iterrows():
        if len(row) <= 17 or not _is_year(row.iloc[1], 1900, 2100):
            continue
        year = int(float(row.iloc[1]))
        for index, (source, _) in _GENERATION_MIX_COLUMNS.items():
            value = number(row.iloc[index])
            if value is not None:
                records.append({"year": year, "source": source, "generation_gwh": value})
    return records


_PEAK_DEMAND_COLUMNS: dict[int, tuple[str, tuple[str, ...]]] = {
    2: ("installed_capacity_mw", ("kurulu guc", "installed cap")),
    3: ("instantaneous_peak_mw", ("ani", "instantaneous")),
    4: ("hourly_peak_mw", ("saatlik", "hourly")),
    5: ("gross_demand_gwh", ("brut talep", "gross demand")),
    6: ("average_generation_capacity_gwh", ("ortalama", "average")),
    7: ("firm_generation_capacity_gwh", ("guvenilir", "firm")),
}


def parse_peak_demand(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    table = "kurulu güç / puant / üretim kapasitesi"
    header_row, _ = _find_label(df, table, "YILLAR", lambda text: text == "yillar", column=1)
    _check_column_headers(
        df,
        table,
        header_row,
        {index: tokens for index, (_, tokens) in _PEAK_DEMAND_COLUMNS.items()},
    )
    records: list[dict[str, Any]] = []
    for _, row in df.iloc[header_row + 1 :].iterrows():
        if len(row) <= 7 or not _is_year(row.iloc[1], 1900, 2100):
            continue
        record: dict[str, Any] = {"year": int(float(row.iloc[1]))}
        for index, (field, _) in _PEAK_DEMAND_COLUMNS.items():
            record[field] = number(row.iloc[index])
        records.append(record)
    return records


_COUNTRY_TOKENS: dict[str, tuple[str, ...]] = {
    "Bulgaria": ("bulgaria",),
    "Greece": ("greece",),
    "Azerbaijan": ("azerbaycan", "azerbaijan"),
    "Georgia": ("georgia",),
    "Syria": ("suriye", "syria"),
    "Iran": ("iran",),
    "Iraq": ("irak", "iraq"),
}


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
    table = "brüt üretim / ithalat / ihracat / talep"
    header_row, _ = _find_label(df, table, "YILLAR", lambda text: text == "yillar", column=1)
    # Generation, import and export totals are all captioned TOPLAM/TOTAL; their
    # neighbours (country columns, gross demand) pin them in place.
    _check_column_headers(
        df,
        table,
        header_row,
        {
            5: ("toplam", "total"),
            **{index: _COUNTRY_TOKENS[name] for index, name in countries_import.items()},
            13: ("toplam", "total"),
            **{index: _COUNTRY_TOKENS[name] for index, name in countries_export.items()},
            21: ("toplam", "total"),
            22: ("brut talep", "gross demand"),
        },
    )
    records: list[dict[str, Any]] = []
    for _, row in df.iloc[header_row + 1 :].iterrows():
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


_TRANSMISSION_LINE_COLUMNS: dict[int, tuple[str, tuple[str, ...]]] = {
    2: ("line_380kv_km", ("380 kv",)),
    3: ("line_220kv_km", ("220 kv",)),
    4: ("line_154kv_km", ("154 kv",)),
    5: ("line_66kv_km", ("66 kv",)),
    6: ("total_line_km", ("toplam", "total")),
}


def parse_transmission_lines(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    table = "iletim hat uzunlukları"
    header_row, _ = _find_label(df, table, "YILLAR", lambda text: text == "yillar", column=1)
    _check_column_headers(
        df,
        table,
        header_row,
        {index: tokens for index, (_, tokens) in _TRANSMISSION_LINE_COLUMNS.items()},
    )
    records: list[dict[str, Any]] = []
    for _, row in df.iloc[header_row + 1 :].iterrows():
        if len(row) <= 6 or not _is_year(row.iloc[1], 1900, 2100):
            continue
        record: dict[str, Any] = {"year": int(float(row.iloc[1]))}
        for index, (field, _) in _TRANSMISSION_LINE_COLUMNS.items():
            record[field] = number(row.iloc[index])
        records.append(record)
    return records


# Each voltage level spans a count column and an MVA column; the voltage caption
# is printed above the count column only (merged cells).
_TRANSFORMER_VOLTAGE_COLUMNS: dict[int, tuple[str, ...]] = {
    2: ("380 kv",),
    4: ("154 kv",),
    6: ("66 kv",),
    8: ("toplam", "total"),
}
_TRANSFORMER_COLUMNS: dict[int, tuple[str, tuple[str, ...]]] = {
    2: ("transformers_380kv", ("adet", "number")),
    3: ("capacity_380kv_mva", ("mva",)),
    4: ("transformers_154kv", ("adet", "number")),
    5: ("capacity_154kv_mva", ("mva",)),
    6: ("transformers_66kv_and_below", ("adet", "number")),
    7: ("capacity_66kv_and_below_mva", ("mva",)),
    8: ("total_transformers", ("adet", "number")),
    9: ("total_capacity_mva", ("mva",)),
}


def parse_transformers(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    table = "trafo adet ve güçleri"
    header_row, _ = _find_label(df, table, "YILLAR", lambda text: text == "yillar", column=1)
    _check_column_headers(df, table, header_row, _TRANSFORMER_VOLTAGE_COLUMNS)
    _check_column_headers(
        df,
        table,
        header_row,
        {index: tokens for index, (_, tokens) in _TRANSFORMER_COLUMNS.items()},
    )
    records: list[dict[str, Any]] = []
    for _, row in df.iloc[header_row + 1 :].iterrows():
        if len(row) <= 9 or not _is_year(row.iloc[1], 1900, 2100):
            continue
        record: dict[str, Any] = {"year": int(float(row.iloc[1]))}
        for index, (field, _) in _TRANSFORMER_COLUMNS.items():
            value = number(row.iloc[index])
            record[field] = value if field.endswith("_mva") else int(value or 0)
        records.append(record)
    return records


def parse_capacity_by_organization(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    table = "kurulu güç / üretici kuruluşlar"
    header_row, _ = _find_label(
        df, table, "KURULUŞLAR", lambda text: text == "kuruluslar", column=1
    )
    years = _year_columns(df, table, header_row)
    # The EÜAŞ label sits on the middle (renewable) row of its three-row block;
    # an exact match skips "EÜAŞ'IN BAĞLI ORTAKLIKLARI".
    euas_row, _ = _find_label(
        df, table, "EÜAŞ", lambda text: text == "euas", column=1, start_row=header_row + 1
    )
    row_mapping = {
        euas_row - 2: ("thermal_mw", "termik"),
        euas_row: ("renewable_mw", "hidrolik"),
        euas_row + 2: ("total_mw", "toplam"),
    }
    for row_index, (_, expected) in row_mapping.items():
        found = _cell_label(df, row_index, 2)
        if row_index <= header_row or not _has_token(found, expected):
            raise _layout_error(
                table,
                f"EÜAŞ bloğunda {row_index}. satır etiketi beklenenden farklı",
                row=row_index,
                expected_label=expected,
                found=found or None,
            )
    result: dict[int, dict[str, Any]] = {
        year: {"year": year} for year in years.values()
    }
    for row_index, (field, _) in row_mapping.items():
        for column, year in years.items():
            result[year][field] = number(df.iloc[row_index, column])
    return list(result.values())


def parse_generation_by_organization(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    table = "brüt üretim / üretici kuruluşlar"
    header_row, year_column = _find_label(df, table, "YILLAR", lambda text: text == "yillar")
    # Locate the utility columns by their header text rather than by index.
    header_rows = range(max(0, header_row - 1), header_row + 4)

    def column_of(expected: str, labels: set[str]) -> int:
        for column in range(df.shape[1]):
            if any(_cell_label(df, row, column) in labels for row in header_rows):
                return column
        raise _layout_error(
            table, f"'{expected}' sütunu bulunamadı", expected_label=expected
        )

    euas_column = column_of("EÜAŞ", {"euas"})
    turkey_column = column_of("TÜRKİYE", {"turkiye", "turkey"})
    records: list[dict[str, Any]] = []
    for _, row in df.iloc[header_row + 1 :].iterrows():
        if not _is_year(_cell(row, year_column), 1900, 2100):
            continue
        records.append(
            {
                "year": int(float(row.iloc[year_column])),
                "euas_generation_gwh": number(_cell(row, euas_column)),
                "turkey_generation_gwh": number(_cell(row, turkey_column)),
            }
        )
    return records


def parse_euas_generation_by_source(content: bytes) -> list[dict[str, Any]]:
    df = _frame(content)
    table = "brüt üretim / üretici kuruluş ve kaynak"
    header_row, _ = _find_label(
        df,
        table,
        "ÜRETİM KARAKTERİSTİĞİ",
        lambda text: text.startswith(("uretim karakteristig", "generation characteristic")),
        column=1,
    )
    years = _year_columns(df, table, header_row)
    # EÜAŞ is the first utility block: from the first data row through its first
    # "Total" row (English labels in column 3). Its name is printed vertically,
    # one letter per row ("E", "Ü", "A", "Ş") in column 1.
    start = header_row + 2
    end = next(
        (
            row
            for row in range(start, min(len(df), start + 20))
            if _cell_label(df, row, 3) == "total"
        ),
        None,
    )
    if end is None:
        raise _layout_error(
            table, "EÜAŞ bloğunun 'Total' satırı bulunamadı", expected_label="Total"
        )
    organization = "".join(_cell_label(df, row, 1) for row in range(start, end + 1))
    if organization.replace(" ", "") != "euas":
        raise _layout_error(
            table,
            "ilk kuruluş bloğu EÜAŞ değil",
            expected_label="EÜAŞ",
            found=organization or None,
        )
    result: list[dict[str, Any]] = []
    for row_index in range(start, end + 1):
        source_label = clean_text(df.iloc[row_index, 3])
        source = SOURCE_ALIASES.get(normalize_key(source_label or ""))
        if not source:
            continue
        for column, year in years.items():
            value = number(df.iloc[row_index, column])
            if value is not None:
                result.append({"year": year, "source": source, "generation_gwh": value})
    return result


def _find_column(df: pd.DataFrame, *needles: str) -> int | None:
    limit = min(12, len(df))
    for row_index in range(limit):
        for col_index, value in enumerate(df.iloc[row_index].tolist()):
            text = normalize_key(str(value or ""))
            if text and any(needle in text for needle in needles):
                return col_index
    return None


_THERMAL_PLANT_COLUMNS: dict[int, tuple[str, ...]] = {
    3: ("santralin adi", "name of power plant"),
    4: ("yakit", "fuel"),
    5: ("bulundugu il", "location"),
    16: ("kurulu guc", "installed cap"),
    17: ("brut uretim", "gross generation"),
    18: ("proje uretim", "project"),
}


def parse_euas_thermal_plants(
    content: bytes, reference_year: int | None = None
) -> list[dict[str, Any]]:
    df = _frame(content)
    logger.debug(
        "EUAS thermal raw dataframe first 15 rows (untransformed):\n%s",
        df.iloc[:15, :].to_string(index=True, header=True),
    )
    table = "EÜAŞ termik santralları"
    header_row, _ = _find_label(
        df, table, "SIRA NO", lambda text: text.startswith("sira no"), column=2
    )
    _check_column_headers(df, table, header_row, _THERMAL_PLANT_COLUMNS)
    records: list[dict[str, Any]] = []
    current_source: str | None = None
    for _, row in df.iloc[header_row + 1 :].iterrows():
        if len(row) <= 17 or number(_cell(row, 2), 0) is None:
            continue
        name = clean_text(_cell(row, 3))
        if not name:
            continue
        source_text = clean_text(_cell(row, 4))
        if source_text and source_text not in {'"', "'"} and normalize_key(source_text) not in {
            "lignite",
            "diesel oil",
        }:
            current_source = source_text
        elif source_text and source_text not in {'"', "'"}:
            aliases = {"lignite": "Linyit", "diesel oil": "Motorin"}
            current_source = aliases.get(normalize_key(source_text), source_text)
        capacity_mw = number(_cell(row, 16))
        gross_generation_gwh = number(_cell(row, 17))
        (
            project_generation_gwh,
            drop_reasons,
            suspect_reasons,
        ) = guard_single_project_generation(
            plant_name=name,
            capacity_mw=capacity_mw,
            gross_generation_gwh=gross_generation_gwh,
            project_generation_gwh=number(_cell(row, 18)),
        )
        # Dropped and suspect are mutually exclusive public states. A physical
        # ceiling violation wins over the advisory 3x ratio signal.
        if drop_reasons:
            suspect_reasons = []
        records.append(
            {
                "name": name,
                "plant_type": "thermal",
                "source": current_source,
                "province": clean_text(_cell(row, 5)),
                "installed_capacity_mw": capacity_mw,
                "gross_generation_gwh": gross_generation_gwh,
                "project_generation_gwh": project_generation_gwh,
                "project_generation_suspect": bool(suspect_reasons),
                "reference_year": reference_year,
                "_project_generation_dropped": bool(drop_reasons),
                "_project_generation_drop_reasons": drop_reasons,
                "_project_generation_suspect_reasons": suspect_reasons,
            }
        )
    return records


def parse_euas_hydro_plants(
    content: bytes, reference_year: int | None = None
) -> list[dict[str, Any]]:
    """Parse TEİAŞ EÜAŞ hydro plant tables.

    Capacity and gross generation are read from the plant identity row. Average /
    firm project-generation cells in the same TEİAŞ workbook are sometimes
    physically impossible for that row's MW; those values are guarded to None
    instead of being returned as if they belonged to the plant.
    """
    df = _frame(content)
    logger.debug(
        "EUAS hydro raw dataframe first 15 rows (untransformed):\n%s",
        df.iloc[:15, :].to_string(index=True, header=True),
    )
    table = "EÜAŞ hidrolik santralleri"

    # Columns are located by their header text; a missing header raises instead
    # of falling back to a fixed column that may now hold another field.
    def column(expected: str, *needles: str) -> int:
        found = _find_column(df, *needles)
        if found is None:
            raise _layout_error(
                table, f"'{expected}' sütunu bulunamadı", expected_label=expected
            )
        return found

    order_col = column("SIRA NO", "sira no", "order")
    name_col = column("SANTRALIN ADI", "santralin adi", "name of power plant")
    province_col = column("BULUNDUĞU İL", "bulundugu il", "location")
    capacity_col = column("KURULU GÜÇ", "kurulu guc", "installed cap")
    gross_col = column("BRÜT ÜRETİM", "brut uretim", "gross generation")
    # Average/firm project generation is never published as mapped (see below);
    # without its headers it is treated as absent rather than read from 19/20.
    average_col = _find_column(df, "ortalama", "average")
    firm_col = _find_column(df, "guvenilir", "firm")

    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if (
            len(row) <= max(capacity_col, gross_col, name_col)
            or number(_cell(row, order_col), 0) is None
        ):
            continue
        name = clean_text(_cell(row, name_col))
        if not name:
            continue
        capacity_mw = number(_cell(row, capacity_col))
        gross_generation_gwh = number(_cell(row, gross_col))
        average_raw = number(_cell(row, average_col)) if average_col is not None else None
        firm_raw = number(_cell(row, firm_col)) if firm_col is not None else None
        (
            average_gwh,
            firm_gwh,
            drop_reasons,
            suspect_reasons,
        ) = guard_project_generation(
            plant_name=name,
            capacity_mw=capacity_mw,
            gross_generation_gwh=gross_generation_gwh,
            average_gwh=average_raw,
            firm_gwh=firm_raw,
        )
        # The workbook has only one plant-name column (the identity table at
        # column 4). The project-generation block at columns 19/20 has no
        # independent plant-name key, while live values demonstrate that its
        # row order is not reliably aligned with the identity rows. Therefore
        # an honest name-based join is impossible: do not expose even
        # plausible-looking pairs as correctly mapped.
        if average_raw is not None or firm_raw is not None:
            drop_reasons.append("source_project_generation_mapping_unverifiable")
            average_gwh = None
            firm_gwh = None
            suspect_reasons = []
            logger.warning(
                "Dropping unverified project-generation pair for %s: "
                "source columns have no independent plant-name key",
                name,
            )
        records.append(
            {
                "name": name,
                "name_key": normalize_plant_name(name),
                "plant_type": "hydro",
                "source": "Hidroelektrik",
                "province": clean_text(_cell(row, province_col)),
                "installed_capacity_mw": capacity_mw,
                "gross_generation_gwh": gross_generation_gwh,
                "average_project_generation_gwh": average_gwh,
                "firm_project_generation_gwh": firm_gwh,
                "project_generation_suspect": bool(suspect_reasons),
                "reference_year": reference_year,
                "_project_generation_dropped": bool(drop_reasons),
                "_project_generation_drop_reasons": drop_reasons,
                "_project_generation_suspect_reasons": suspect_reasons,
            }
        )
    return records
