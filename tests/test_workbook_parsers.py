from io import BytesIO
import json
from pathlib import Path
import warnings

import pandas as pd
import pytest

from turkiye_energy_mcp.exceptions import EnergyDataError, ErrorCode
from turkiye_energy_mcp.parsers import workbooks
from turkiye_energy_mcp.parsers.workbooks import (
    parse_capacity_by_organization,
    parse_capacity_mix,
    parse_energy_balance,
    parse_euas_generation_by_source,
    parse_euas_hydro_plants,
    parse_euas_thermal_plants,
    parse_generation_by_organization,
    parse_generation_mix,
    parse_monthly_energy,
    parse_monthly_euas_generation,
    parse_peak_demand,
    parse_transformers,
    parse_transmission_lines,
)


def workbook_bytes(frame: pd.DataFrame, sheet_name: str = "Sheet1") -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name=sheet_name, header=False, index=False)
    return output.getvalue()


# Real TEİAŞ yearly workbooks (2025 galleries), downloaded unmodified.
TEIAS_FIXTURES = Path(__file__).parent / "fixtures" / "teias"
CAPACITY_MIX = "kurulu_guc_kaynak_2006-2025.xls"
CAPACITY_BY_ORG = "kurulu_guc_kurulus_2006-2025.xls"
GENERATION_MIX = "uretim_kaynak_2000-2025.xls"
GENERATION_BY_ORG = "uretim_kurulus_2014-2025.xlsx"
EUAS_BY_SOURCE = "uretim_kurulus_kaynak_2019-2025.xlsx"
PEAK_DEMAND = "puant_kurulu_guc_1980-2025.xls"
ENERGY_BALANCE = "uretim_ithalat_ihracat_2012-2025.xlsx"
TRANSMISSION_LINES = "iletim_hat_uzunluk_1979-2025.xls"
TRANSFORMERS = "trafo_adet_guc_1980-2025.xls"
MONTHLY = "aylik_uretim_tuketim_2026-09.xlsx"  # 2026 monthly report, Jan-Sep
EUAS_THERMAL = "euas_termik_santraller_2025.xls"
EUAS_HYDRO = "euas_hidrolik_santraller_2025_19.xls"
EUAS_HYDRO_CONTINUED = "euas_hidrolik_santraller_2025_20.xls"


def fixture_bytes(name: str) -> bytes:
    return (TEIAS_FIXTURES / name).read_bytes()


def fixture_frame(name: str, sheet: str | int = 0) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # xlrd deprecation / openpyxl header noise
        return pd.read_excel(TEIAS_FIXTURES / name, sheet_name=sheet, header=None)


def insert_column(frame: pd.DataFrame, at: int) -> pd.DataFrame:
    shifted = frame.copy()
    shifted.insert(at, "inserted", None)
    shifted.columns = range(shifted.shape[1])
    return shifted


def insert_row(frame: pd.DataFrame, at: int) -> pd.DataFrame:
    blank = pd.DataFrame([[None] * frame.shape[1]], columns=frame.columns)
    return pd.concat([frame.iloc[:at], blank, frame.iloc[at:]], ignore_index=True)


def assert_same_records(actual: list[dict], expected: list[dict]) -> None:
    # Rewriting through openpyxl can move a float by one unit in the 6th decimal.
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert got.keys() == want.keys()
        for key, value in want.items():  # one level at a time: approx is not recursive
            assert got[key] == pytest.approx(value, abs=1e-5)


def assert_layout_error(parser, frame: pd.DataFrame) -> EnergyDataError:
    with pytest.raises(EnergyDataError) as excinfo:
        parser(workbook_bytes(frame))
    assert excinfo.value.code == ErrorCode.PARSING_ERROR
    assert "düzeni beklenenden farklı" in excinfo.value.message
    return excinfo.value


# Output of the fixed-position parsers (before header checks were added) on the
# real fixtures above; the checked parsers must return exactly the same records.
LEGACY_OUTPUT = json.loads(
    (TEIAS_FIXTURES / "legacy_parser_output.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    "entry", LEGACY_OUTPUT, ids=lambda entry: f"{entry['parser']}-{entry['fixture']}"
)
def test_real_workbooks_match_fixed_position_output(entry):
    parser = getattr(workbooks, entry["parser"])
    records = parser(fixture_bytes(entry["fixture"]), *entry["args"])
    if entry["parser"] == "parse_monthly_euas_generation":
        # Deliberate change after the legacy capture: the "GEOHTERMAL" row is
        # now read (see test_monthly_euas_generation_reads_misspelled_geothermal_row).
        records = [r for r in records if r["source"] != "geothermal"]
    assert records == entry["records"]


def test_teias_capacity_parser():
    records = parse_capacity_mix(fixture_bytes(CAPACITY_MIX))
    assert {"year": 2006, "source": "hard_coal", "capacity_mw": 1986.0} in records
    assert {"year": 2025, "source": "total", "capacity_mw": 123302.990857} in records


@pytest.mark.parametrize(
    ("parser", "name"),
    [
        (parse_capacity_mix, CAPACITY_MIX),
        (parse_capacity_by_organization, CAPACITY_BY_ORG),
        (parse_generation_mix, GENERATION_MIX),
        (parse_generation_by_organization, GENERATION_BY_ORG),
        (parse_euas_generation_by_source, EUAS_BY_SOURCE),
        (parse_peak_demand, PEAK_DEMAND),
        (parse_energy_balance, ENERGY_BALANCE),
        (parse_transmission_lines, TRANSMISSION_LINES),
        (parse_transformers, TRANSFORMERS),
        (parse_euas_thermal_plants, EUAS_THERMAL),
        (parse_euas_hydro_plants, EUAS_HYDRO),
        (parse_euas_hydro_plants, EUAS_HYDRO_CONTINUED),
    ],
)
def test_real_workbooks_round_trip_through_test_writer(parser, name):
    # Guards the in-memory mutation tests below: rewriting the unmodified fixture
    # must not change the parse result.
    original = parser(fixture_bytes(name))
    assert original
    assert_same_records(parser(workbook_bytes(fixture_frame(name))), original)


def test_real_organization_workbooks_parse_euas_figures():
    capacity = {
        row["year"]: row for row in parse_capacity_by_organization(fixture_bytes(CAPACITY_BY_ORG))
    }
    assert capacity[2025] == {
        "year": 2025,
        "thermal_mw": 6359.94,
        "renewable_mw": 14481.259,
        "total_mw": 20841.199,
    }
    generation = {
        row["year"]: row
        for row in parse_generation_by_organization(fixture_bytes(GENERATION_BY_ORG))
    }
    assert generation[2025]["euas_generation_gwh"] == 44334.923976
    by_source = parse_euas_generation_by_source(fixture_bytes(EUAS_BY_SOURCE))
    assert {"year": 2025, "source": "total", "generation_gwh": 44334.923976} in by_source
    # Turkey total in the utility table equals the general total in the source mix.
    mix_total = {
        row["year"]: row["generation_gwh"]
        for row in parse_generation_mix(fixture_bytes(GENERATION_MIX))
        if row["source"] == "total"
    }
    assert generation[2025]["turkey_generation_gwh"] == mix_total[2025]


@pytest.mark.parametrize(
    ("parser", "name", "column"),
    [
        (parse_capacity_mix, CAPACITY_MIX, 5),  # new fuel column after lignite
        (parse_capacity_mix, CAPACITY_MIX, 16),  # new column before solar
        (parse_capacity_mix, CAPACITY_MIX, 0),  # whole table shifted right
        (parse_generation_mix, GENERATION_MIX, 3),
        (parse_generation_mix, GENERATION_MIX, 15),
        (parse_capacity_by_organization, CAPACITY_BY_ORG, 1),
        (parse_euas_generation_by_source, EUAS_BY_SOURCE, 2),
    ],
)
def test_inserted_column_raises_instead_of_shifting_values(parser, name, column):
    assert_layout_error(parser, insert_column(fixture_frame(name), column))


def test_capacity_mix_error_names_the_mismatched_column():
    error = assert_layout_error(parse_capacity_mix, insert_column(fixture_frame(CAPACITY_MIX), 5))
    assert error.details["column"] == 5
    assert error.details["expected_any"] == ["sivi yakit", "liquid fuel"]


def test_generation_by_organization_locates_columns_by_label():
    expected = parse_generation_by_organization(fixture_bytes(GENERATION_BY_ORG))
    for column in (0, 2, 5):
        shifted = insert_column(fixture_frame(GENERATION_BY_ORG), column)
        assert_same_records(parse_generation_by_organization(workbook_bytes(shifted)), expected)


def test_generation_by_organization_without_euas_header_raises():
    frame = fixture_frame(GENERATION_BY_ORG)
    frame = frame.replace({"EÜAŞ": "BAŞKA KURULUŞ"})
    assert_layout_error(parse_generation_by_organization, frame)


def test_capacity_by_organization_locates_euas_block_by_label():
    expected = parse_capacity_by_organization(fixture_bytes(CAPACITY_BY_ORG))
    shifted = insert_row(fixture_frame(CAPACITY_BY_ORG), 4)  # extra title row
    assert_same_records(parse_capacity_by_organization(workbook_bytes(shifted)), expected)


def test_capacity_by_organization_row_inside_euas_block_raises():
    frame = fixture_frame(CAPACITY_BY_ORG)
    euas_row = next(i for i in range(len(frame)) if frame.iloc[i, 1] == "EÜAŞ")
    # A new row between "TERMİK" and the EÜAŞ renewable row would make the
    # positional thermal read pick up the English "THERMAL" caption row.
    error = assert_layout_error(parse_capacity_by_organization, insert_row(frame, euas_row - 1))
    assert error.details["expected_label"] == "termik"


def test_euas_generation_by_source_rows_located_by_label():
    expected = parse_euas_generation_by_source(fixture_bytes(EUAS_BY_SOURCE))
    shifted = insert_row(fixture_frame(EUAS_BY_SOURCE), 12)  # blank row inside EÜAŞ block
    assert_same_records(parse_euas_generation_by_source(workbook_bytes(shifted)), expected)


def test_euas_generation_by_source_rejects_other_utility_block():
    frame = fixture_frame(EUAS_BY_SOURCE)
    frame.iloc[9:16, 1] = None  # vertical "E Ü A Ş" letters gone
    assert_layout_error(parse_euas_generation_by_source, frame)


MONTHLY_BY_SOURCE = "Kaynaklara Göre"
MONTHLY_BY_UTILITY = "Kuruluşlara Göre"


def test_teias_monthly_parser():
    records = parse_monthly_energy(fixture_bytes(MONTHLY), 2026)
    assert {row["date"] for row in records} == {f"2026-{month:02d}" for month in range(1, 10)}
    assert {"date": "2026-01", "metric": "wind", "value": 5227.048549} in records
    assert {"date": "2026-09", "metric": "total_generation", "value": 31787.679365} in records


def test_euas_monthly_parser_handles_blank_merged_cells():
    # TR caption rows ("HİDROLİK") are blank; values sit on the EN rows below.
    records = parse_monthly_euas_generation(fixture_bytes(MONTHLY), 2026)
    september = {
        row["source"]: row["generation_gwh"] for row in records if row["date"] == "2026-09"
    }
    assert september == {
        "thermal": 777.07177,
        "hydro": 5161.22059,
        "geothermal": 0.0,
        "wind": 0.0,
        "total": 5938.29236,
    }


@pytest.mark.parametrize(
    ("parser", "sheet", "column"),
    [
        (parse_monthly_energy, MONTHLY_BY_SOURCE, 0),  # OCAK no longer in column 2
        (parse_monthly_energy, MONTHLY_BY_SOURCE, 5),  # extra column inside the months
        (parse_monthly_energy, MONTHLY_BY_SOURCE, 13),  # before ARALIK
        (parse_monthly_euas_generation, MONTHLY_BY_UTILITY, 2),
        (parse_monthly_euas_generation, MONTHLY_BY_UTILITY, 8),
    ],
)
def test_monthly_parsers_inserted_column_raises(parser, sheet, column):
    shifted = insert_column(fixture_frame(MONTHLY, sheet), column)
    with pytest.raises(EnergyDataError) as excinfo:
        parser(workbook_bytes(shifted, sheet), 2026)
    assert excinfo.value.code == ErrorCode.PARSING_ERROR
    assert "düzeni beklenenden farklı" in excinfo.value.message


@pytest.mark.parametrize(
    ("parser", "sheet", "row"),
    [
        (parse_monthly_energy, MONTHLY_BY_SOURCE, 3),
        (parse_monthly_energy, MONTHLY_BY_SOURCE, 20),
        (parse_monthly_euas_generation, MONTHLY_BY_UTILITY, 6),
        (parse_monthly_euas_generation, MONTHLY_BY_UTILITY, 14),  # inside the EÜAŞ block
    ],
)
def test_monthly_parsers_inserted_row_keeps_values(parser, sheet, row):
    expected = parser(fixture_bytes(MONTHLY), 2026)
    shifted = insert_row(fixture_frame(MONTHLY, sheet), row)
    assert parser(workbook_bytes(shifted, sheet), 2026) == expected


def test_peak_parser_and_empty_dataset():
    records = {row["year"]: row for row in parse_peak_demand(fixture_bytes(PEAK_DEMAND))}
    assert sorted(records) == list(range(1980, 2026))
    # MW power and GWh energy columns stay separate.
    assert records[2025] == {
        "year": 2025,
        "installed_capacity_mw": 123302.990857,
        "instantaneous_peak_mw": 60405.61,
        "hourly_peak_mw": 59503.67,
        "gross_demand_gwh": 366920.321254,
        "average_generation_capacity_gwh": 567530.3792,
        "firm_generation_capacity_gwh": 486688.2452,
    }
    # Header present but no year rows yet: empty, not an error.
    frame = fixture_frame(PEAK_DEMAND)
    header_only = frame.iloc[:14]
    assert parse_peak_demand(workbook_bytes(header_only)) == []


def test_peak_parser_without_header_raises():
    error = assert_layout_error(parse_peak_demand, pd.DataFrame([[None] * 8]))
    assert error.details["expected_label"] == "YILLAR"


@pytest.mark.parametrize("column", [0, 3, 5, 7])
def test_peak_parser_inserted_column_raises(column):
    # 0: whole table shifted; 3: between installed MW and instantaneous peak;
    # 5: between hourly peak MW and gross demand GWh; 7: before firm capacity.
    assert_layout_error(parse_peak_demand, insert_column(fixture_frame(PEAK_DEMAND), column))


@pytest.mark.parametrize("row", [5, 13, 30])
def test_peak_parser_inserted_row_keeps_values(row):
    expected = parse_peak_demand(fixture_bytes(PEAK_DEMAND))
    shifted = insert_row(fixture_frame(PEAK_DEMAND), row)
    assert_same_records(parse_peak_demand(workbook_bytes(shifted)), expected)


def test_peak_parser_relabelled_column_raises():
    frame = fixture_frame(PEAK_DEMAND)
    frame.iloc[10, 3] = "SAATLİK"  # instantaneous-peak column now says hourly
    frame.iloc[11, 3] = "HOURLY"
    error = assert_layout_error(parse_peak_demand, frame)
    assert error.details["column"] == 3


def test_energy_balance_real_workbook():
    records = {row["year"]: row for row in parse_energy_balance(fixture_bytes(ENERGY_BALANCE))}
    assert sorted(records) == list(range(2012, 2026))
    latest = records[2025]
    assert latest["generation_gwh"] == 366920.321254
    assert latest["imports_gwh"] == 2012.967544
    assert latest["exports_gwh"] == 4103.282119
    assert latest["gross_demand_gwh"] == 364830.00668
    assert latest["import_by_country_gwh"]["Georgia"] == 1262.81293
    assert latest["export_by_country_gwh"]["Iraq"] == 1334.633677
    # Gross demand = generation + imports - exports (TEİAŞ footnote 1).
    assert latest["gross_demand_gwh"] == pytest.approx(
        latest["generation_gwh"] + latest["net_import_gwh"], abs=1e-3
    )


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        (0, "YILLAR"),  # whole table shifted right: year header not in column 1
        (5, ("toplam", "total")),  # new generation column before the total
        (9, ("azerbaycan", "azerbaijan")),  # new import country
        (14, ("bulgaria",)),  # column between import total and export block
        (21, ("toplam", "total")),  # new export country before the total
        (22, ("brut talep", "gross demand")),
    ],
)
def test_energy_balance_inserted_column_raises(column, expected):
    shifted = insert_column(fixture_frame(ENERGY_BALANCE), column)
    error = assert_layout_error(parse_energy_balance, shifted)
    if isinstance(expected, str):
        assert error.details["expected_label"] == expected
    else:
        assert tuple(error.details["expected_any"]) == expected


@pytest.mark.parametrize("row", [4, 12, 20])
def test_energy_balance_inserted_row_keeps_values(row):
    expected = parse_energy_balance(fixture_bytes(ENERGY_BALANCE))
    shifted = insert_row(fixture_frame(ENERGY_BALANCE), row)
    assert_same_records(parse_energy_balance(workbook_bytes(shifted)), expected)


def test_energy_balance_row_inside_header_raises():
    # Splitting the caption block pushes the English captions out of the
    # checked window: refuse rather than guess.
    shifted = insert_row(fixture_frame(ENERGY_BALANCE), 11)
    assert_layout_error(parse_energy_balance, shifted)


def test_grid_workbooks_real_values():
    lines = {
        row["year"]: row for row in parse_transmission_lines(fixture_bytes(TRANSMISSION_LINES))
    }
    assert sorted(lines) == list(range(1979, 2026))
    assert lines[2025] == {
        "year": 2025,
        "line_380kv_km": 26902.05,
        "line_220kv_km": 85.35,
        "line_154kv_km": 49594.18,
        "line_66kv_km": 119.53,
        "total_line_km": 76701.11,
    }
    transformers = {row["year"]: row for row in parse_transformers(fixture_bytes(TRANSFORMERS))}
    assert sorted(transformers) == list(range(1980, 2026))
    assert transformers[2025] == {
        "year": 2025,
        "transformers_380kv": 450,
        "capacity_380kv_mva": 95532.5,
        "transformers_154kv": 1756,
        "capacity_154kv_mva": 141970.99,
        "transformers_66kv_and_below": 18,
        "capacity_66kv_and_below_mva": 215.05,
        "total_transformers": 2224,
        "total_capacity_mva": 237718.54,
    }


@pytest.mark.parametrize(
    ("parser", "name", "column"),
    [
        (parse_transmission_lines, TRANSMISSION_LINES, 0),
        (parse_transmission_lines, TRANSMISSION_LINES, 3),  # e.g. a new 400 kV column
        (parse_transmission_lines, TRANSMISSION_LINES, 6),
        (parse_transformers, TRANSFORMERS, 0),
        (parse_transformers, TRANSFORMERS, 3),  # splits the 380 kV count/MVA pair
        (parse_transformers, TRANSFORMERS, 4),  # new voltage level after 380 kV
        (parse_transformers, TRANSFORMERS, 8),
    ],
)
def test_grid_workbooks_inserted_column_raises(parser, name, column):
    assert_layout_error(parser, insert_column(fixture_frame(name), column))


def test_transformers_new_voltage_pair_raises():
    # A whole new count/MVA pair (e.g. 220 kV) passes the per-column
    # ADET/MVA checks; the voltage caption above it must not.
    frame = insert_column(insert_column(fixture_frame(TRANSFORMERS), 4), 4)
    frame.iloc[8, 4] = "220 kV"
    frame.iloc[10, 4:6] = ["ADET", "GÜÇ (MVA)"]
    error = assert_layout_error(parse_transformers, frame)
    assert error.details["column"] == 4
    assert error.details["expected_any"] == ["154 kv"]


@pytest.mark.parametrize(
    ("parser", "name", "row"),
    [
        (parse_transmission_lines, TRANSMISSION_LINES, 4),
        (parse_transmission_lines, TRANSMISSION_LINES, 9),
        (parse_transmission_lines, TRANSMISSION_LINES, 30),
        (parse_transformers, TRANSFORMERS, 5),
        (parse_transformers, TRANSFORMERS, 12),
        (parse_transformers, TRANSFORMERS, 40),
    ],
)
def test_grid_workbooks_inserted_row_keeps_values(parser, name, row):
    expected = parser(fixture_bytes(name))
    assert_same_records(parser(workbook_bytes(insert_row(fixture_frame(name), row))), expected)


# Column captions as printed in row 6 of the real EÜAŞ thermal table.
THERMAL_CAPTIONS = {
    2: "SIRA NO",
    3: "SANTRALIN ADI",
    4: "YAKIT CİNSİ",
    5: "BULUNDUĞU İL",
    16: "KURULU GÜÇ",
    17: "BRÜT ÜRETİM",
    18: "PROJE ÜRETİMİ",
}


def thermal_frame() -> pd.DataFrame:
    frame = pd.DataFrame([[None] * 19 for _ in range(12)])
    for column, caption in THERMAL_CAPTIONS.items():
        frame.iloc[6, column] = caption
    return frame


def test_euas_thermal_parser():
    frame = thermal_frame()
    frame.iloc[11, 2] = 1
    frame.iloc[11, 3] = "Afşin-Elbistan B"
    frame.iloc[11, 4] = "Linyit"
    frame.iloc[11, 5] = "K.Maraş"
    frame.iloc[11, 16] = 1440
    frame.iloc[11, 17] = 2833.648
    frame.iloc[11, 18] = 9360
    records = parse_euas_thermal_plants(workbook_bytes(frame))
    assert records[0]["name"] == "Afşin-Elbistan B"
    assert records[0]["installed_capacity_mw"] == 1440
    assert records[0]["source"] == "Linyit"
    assert records[0]["project_generation_gwh"] == 9360
    assert records[0]["project_generation_suspect"] is True


def test_euas_thermal_parser_nulls_implausible_project_generation():
    frame = thermal_frame()
    frame.iloc[11, 2] = 1
    frame.iloc[11, 3] = "Soma A"
    frame.iloc[11, 4] = "Linyit"
    frame.iloc[11, 5] = "Manisa"
    frame.iloc[11, 16] = 44
    frame.iloc[11, 17] = 100
    frame.iloc[11, 18] = 1365
    record = parse_euas_thermal_plants(workbook_bytes(frame))[0]
    assert record["installed_capacity_mw"] == 44
    assert record["gross_generation_gwh"] == 100
    assert record["project_generation_gwh"] is None
    assert record["project_generation_suspect"] is False
    assert record["_project_generation_dropped"] is True
    assert record["_project_generation_drop_reasons"] == [
        "project_generation_gwh_exceeds_capacity_ceiling",
    ]
    assert record["_project_generation_suspect_reasons"] == []


def test_euas_hydro_parser():
    frame = pd.DataFrame([[None] * 21 for _ in range(13)])
    frame.iloc[7, 2] = "SIRA NO"
    frame.iloc[7, 4] = "SANTRALIN ADI"
    frame.iloc[7, 6] = "BULUNDUĞU İL"
    frame.iloc[7, 17] = "KURULU GÜÇ"
    frame.iloc[7, 18] = "BRÜT ÜRETİM"
    frame.iloc[8, 19] = "ORTALAMA"
    frame.iloc[8, 20] = "GÜVENİLİR"
    frame.iloc[12, 2] = 1
    frame.iloc[12, 4] = "ATATÜRK"
    frame.iloc[12, 6] = "Şanlıurfa"
    frame.iloc[12, 17] = 2405
    frame.iloc[12, 18] = 5456.5
    frame.iloc[12, 19] = 8900
    frame.iloc[12, 20] = 7400
    records = parse_euas_hydro_plants(workbook_bytes(frame))
    assert records[0]["plant_type"] == "hydro"
    assert records[0]["province"] == "Şanlıurfa"
    assert records[0]["installed_capacity_mw"] == 2405
    assert records[0]["gross_generation_gwh"] == 5456.5
    assert records[0]["average_project_generation_gwh"] is None
    assert records[0]["firm_project_generation_gwh"] is None
    assert records[0]["project_generation_suspect"] is False
    assert records[0]["_project_generation_drop_reasons"] == [
        "source_project_generation_mapping_unverifiable"
    ]


def test_euas_hydro_parser_nulls_implausible_project_generation():
    frame = pd.DataFrame([[None] * 21 for _ in range(14)])
    frame.iloc[7, 2] = "SIRA NO"
    frame.iloc[7, 4] = "SANTRALIN ADI"
    frame.iloc[7, 6] = "BULUNDUĞU İL"
    frame.iloc[7, 17] = "KURULU GÜÇ"
    frame.iloc[7, 18] = "BRÜT ÜRETİM"
    frame.iloc[8, 19] = "ORTALAMA"
    frame.iloc[8, 20] = "GÜVENİLİR"
    # BERKE-sized plant with Atatürk-sized project generation (impossible).
    frame.iloc[12, 2] = 1
    frame.iloc[12, 4] = "BERKE"
    frame.iloc[12, 6] = "Adana"
    frame.iloc[12, 17] = 510
    frame.iloc[12, 18] = 1211.236
    frame.iloc[12, 19] = 8900
    frame.iloc[12, 20] = 7400
    # Control row: capacity/gross must remain untouched.
    frame.iloc[13, 2] = 2
    frame.iloc[13, 4] = "KEBAN"
    frame.iloc[13, 6] = "Elazığ"
    frame.iloc[13, 17] = 1330
    frame.iloc[13, 18] = 5536.518
    frame.iloc[13, 19] = 2000
    frame.iloc[13, 20] = 1500
    records = {item["name"]: item for item in parse_euas_hydro_plants(workbook_bytes(frame))}
    assert records["BERKE"]["installed_capacity_mw"] == 510
    assert records["BERKE"]["gross_generation_gwh"] == 1211.236
    assert records["BERKE"]["average_project_generation_gwh"] is None
    assert records["BERKE"]["firm_project_generation_gwh"] is None
    assert records["KEBAN"]["installed_capacity_mw"] == 1330
    assert records["KEBAN"]["gross_generation_gwh"] == 5536.518
    assert records["KEBAN"]["average_project_generation_gwh"] is None
    assert records["KEBAN"]["firm_project_generation_gwh"] is None


def test_euas_plant_tables_real_values():
    thermal = {row["name"]: row for row in parse_euas_thermal_plants(fixture_bytes(EUAS_THERMAL))}
    assert len(thermal) == 11
    assert thermal["Afşin-Elbistan B"]["installed_capacity_mw"] == 1440.0
    assert thermal["Afşin-Elbistan B"]["gross_generation_gwh"] == 530.308
    assert thermal["Afşin-Elbistan B"]["source"] == "Linyit"
    hydro = {row["name"]: row for row in parse_euas_hydro_plants(fixture_bytes(EUAS_HYDRO))}
    assert len(hydro) == 39
    assert hydro["YUSUFELİ"]["installed_capacity_mw"] == 548.1
    assert hydro["YUSUFELİ"]["gross_generation_gwh"] == 1720.856268
    assert hydro["YUSUFELİ"]["province"] == "Artvin"


@pytest.mark.parametrize("column", [0, 4, 10, 16, 18])
def test_euas_thermal_inserted_column_raises(column):
    # 4: new column before the fuel type; 10/16: inside the unit block, which
    # shifts the MW/GWh columns; 18: before project generation.
    assert_layout_error(
        parse_euas_thermal_plants, insert_column(fixture_frame(EUAS_THERMAL), column)
    )


@pytest.mark.parametrize("row", [3, 9, 20])
def test_euas_thermal_inserted_row_keeps_values(row):
    expected = parse_euas_thermal_plants(fixture_bytes(EUAS_THERMAL))
    shifted = insert_row(fixture_frame(EUAS_THERMAL), row)
    assert parse_euas_thermal_plants(workbook_bytes(shifted)) == expected


@pytest.mark.parametrize("name", [EUAS_HYDRO, EUAS_HYDRO_CONTINUED])
def test_euas_hydro_locates_columns_by_label(name):
    expected = parse_euas_hydro_plants(fixture_bytes(name))
    for column in (0, 5, 17):
        shifted = insert_column(fixture_frame(name), column)
        assert parse_euas_hydro_plants(workbook_bytes(shifted)) == expected
    shifted = insert_row(fixture_frame(name), 12)
    assert parse_euas_hydro_plants(workbook_bytes(shifted)) == expected


def blank_captions(frame: pd.DataFrame, *captions: str) -> pd.DataFrame:
    # Real captions carry stray leading spaces ("    KURULU GÜÇ", " FİRM").
    return frame.mask(frame.map(lambda value: isinstance(value, str) and value.strip() in captions))


def test_euas_hydro_missing_capacity_header_raises():
    frame = blank_captions(fixture_frame(EUAS_HYDRO), "KURULU GÜÇ", "INSTALLED CAP.")
    error = assert_layout_error(parse_euas_hydro_plants, frame)
    assert error.details["expected_label"] == "KURULU GÜÇ"


def test_euas_hydro_without_project_headers_reads_no_project_values():
    # No positional fallback to columns 19/20 when the captions are gone.
    expected = parse_euas_hydro_plants(fixture_bytes(EUAS_HYDRO))
    frame = blank_captions(
        fixture_frame(EUAS_HYDRO), "ORTALAMA", "AVERAGE", "GÜVENİLİR", "FİRM"
    )
    records = parse_euas_hydro_plants(workbook_bytes(frame))
    assert [row["installed_capacity_mw"] for row in records] == [
        row["installed_capacity_mw"] for row in expected
    ]
    assert all(row["_project_generation_drop_reasons"] == [] for row in records)


def test_year_parser_ignores_footnote_containing_a_year():
    frame = pd.DataFrame([[None] * 12 for _ in range(3)])
    frame.iloc[0, 1] = "YILLAR"
    frame.iloc[0, 2] = "EÜAŞ"
    frame.iloc[0, 11] = "TÜRKİYE"
    frame.iloc[1, 1] = 2024
    frame.iloc[1, 2] = 54_957.1
    frame.iloc[1, 11] = 354_570.2
    frame.iloc[2, 1] = "* Lisans Eylül 2008 tarihinde dönüştürülmüştür."
    assert parse_generation_by_organization(workbook_bytes(frame)) == [
        {
            "year": 2024,
            "euas_generation_gwh": 54957.1,
            "turkey_generation_gwh": 354570.2,
        }
    ]


def test_monthly_euas_generation_reads_misspelled_geothermal_row():
    # The monthly report labels EÜAŞ geothermal "GEOHTERMAL"; it used to be
    # dropped silently. Components must add up to the reported TOTAL.
    content = (TEIAS_FIXTURES / MONTHLY).read_bytes()
    records = workbooks.parse_monthly_euas_generation(content, 2026)
    by_source = {}
    for record in records:
        by_source.setdefault(record["source"], {})[record["date"]] = record["generation_gwh"]
    assert "geothermal" in by_source
    for month, total in by_source["total"].items():
        parts = sum(by_source[s].get(month, 0.0) for s in ("thermal", "hydro", "geothermal", "wind"))
        assert parts == pytest.approx(total, abs=1e-6)
