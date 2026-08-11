from io import BytesIO

import pandas as pd

from turkiye_energy_mcp.parsers.workbooks import (
    parse_capacity_mix,
    parse_euas_hydro_plants,
    parse_euas_thermal_plants,
    parse_monthly_energy,
    parse_peak_demand,
)


def workbook_bytes(frame: pd.DataFrame, sheet_name: str = "Sheet1") -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name=sheet_name, header=False, index=False)
    return output.getvalue()


def test_teias_capacity_parser():
    frame = pd.DataFrame([[None] * 18 for _ in range(16)])
    frame.iloc[15, 2] = 2024
    frame.iloc[15, 13] = 32202.964
    frame.iloc[15, 15] = 12870.7904
    frame.iloc[15, 16] = 20232.112
    frame.iloc[15, 17] = 116265.06573
    records = parse_capacity_mix(workbook_bytes(frame))
    assert {"year": 2024, "source": "wind", "capacity_mw": 12870.7904} in records
    assert {"year": 2024, "source": "total", "capacity_mw": 116265.06573} in records


def test_teias_monthly_parser():
    frame = pd.DataFrame([[None] * 15 for _ in range(12)])
    frame.iloc[11, 1] = "WIND"
    frame.iloc[11, 2] = 100.25
    frame.iloc[11, 3] = 200.5
    records = parse_monthly_energy(
        workbook_bytes(frame, "Kaynaklara Göre"),
        2026,
    )
    assert records == [
        {"date": "2026-01", "metric": "wind", "value": 100.25},
        {"date": "2026-02", "metric": "wind", "value": 200.5},
    ]


def test_peak_parser_and_empty_dataset():
    frame = pd.DataFrame([[None] * 8 for _ in range(15)])
    frame.iloc[14] = [None, 2024, 116265.1, 58709.76, 57772.4, 353579.8, 550571.8, 478190.6]
    records = parse_peak_demand(workbook_bytes(frame))
    assert records[0]["instantaneous_peak_mw"] == 58709.76
    assert parse_peak_demand(workbook_bytes(pd.DataFrame([[None] * 8]))) == []


def test_euas_thermal_parser():
    frame = pd.DataFrame([[None] * 19 for _ in range(12)])
    frame.iloc[11, 2] = 1
    frame.iloc[11, 3] = "Afşin-Elbistan B"
    frame.iloc[11, 4] = "Linyit"
    frame.iloc[11, 5] = "K.Maraş"
    frame.iloc[11, 16] = 1440
    frame.iloc[11, 17] = 2833.648
    records = parse_euas_thermal_plants(workbook_bytes(frame))
    assert records[0]["name"] == "Afşin-Elbistan B"
    assert records[0]["installed_capacity_mw"] == 1440
    assert records[0]["source"] == "Linyit"


def test_euas_hydro_parser():
    frame = pd.DataFrame([[None] * 21 for _ in range(13)])
    frame.iloc[12, 2] = 1
    frame.iloc[12, 4] = "ATATÜRK"
    frame.iloc[12, 6] = "Şanlıurfa"
    frame.iloc[12, 17] = 2405
    frame.iloc[12, 18] = 5456.5
    records = parse_euas_hydro_plants(workbook_bytes(frame))
    assert records[0]["plant_type"] == "hydro"
    assert records[0]["province"] == "Şanlıurfa"
