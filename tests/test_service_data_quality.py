from io import BytesIO

import pandas as pd
import pytest

from turkiye_energy_mcp.clients.teias import Workbook
from turkiye_energy_mcp.service import EnergyService


def workbook_bytes(frame: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, header=False, index=False)
    return output.getvalue()


def workbook(name: str, content: bytes) -> Workbook:
    return Workbook(
        content=content,
        source_url=f"https://example.test/{name}",
        source_format="xlsx",
        name=name,
        gallery_url="https://example.test/gallery",
        gallery_slug="i-kurulu-guc-2024",
        period_start=2024,
        period_end=2024,
        published_at="2025-01-01T00:00:00+00:00",
        latest_available_period="2024",
    )


@pytest.mark.asyncio
async def test_power_plant_response_reports_dropped_project_rows():
    thermal = pd.DataFrame([[None] * 21 for _ in range(13)])
    thermal.iloc[11, 2] = 1
    thermal.iloc[11, 3] = "Soma A"
    thermal.iloc[11, 4] = "Linyit"
    thermal.iloc[11, 5] = "Manisa"
    thermal.iloc[11, 16] = 44
    thermal.iloc[11, 17] = 100
    thermal.iloc[11, 18] = 1365
    thermal.iloc[12, 2] = 2
    thermal.iloc[12, 3] = "Afşin-Elbistan B"
    thermal.iloc[12, 4] = "Linyit"
    thermal.iloc[12, 5] = "K.Maraş"
    thermal.iloc[12, 16] = 1440
    thermal.iloc[12, 17] = 2834
    thermal.iloc[12, 18] = 9360
    hydro = pd.DataFrame([[None] * 21 for _ in range(13)])
    hydro.iloc[7, 4] = "SANTRALIN ADI"
    hydro.iloc[7, 6] = "BULUNDUĞU İL"
    hydro.iloc[7, 17] = "KURULU GÜÇ"
    hydro.iloc[7, 18] = "BRÜT ÜRETİM"
    hydro.iloc[8, 19] = "ORTALAMA"
    hydro.iloc[8, 20] = "GÜVENİLİR"
    hydro.iloc[12, 2] = 1
    hydro.iloc[12, 4] = "KARAKAYA"
    hydro.iloc[12, 6] = "Diyarbakır"
    hydro.iloc[12, 17] = 1800
    hydro.iloc[12, 18] = 6316
    hydro.iloc[12, 19] = 400
    hydro.iloc[12, 20] = 178

    class FakeTeias:
        async def annual_workbooks(self, selector: str):
            if selector == "euas_thermal_plants":
                return [workbook("thermal", workbook_bytes(thermal))]
            return [workbook("hydro", workbook_bytes(hydro))]

    response = await EnergyService(FakeTeias()).get_euas_power_plants("hydro")
    assert response["data"][0]["installed_capacity_mw"] == 1800
    assert response["data"][0]["gross_generation_gwh"] == 6316
    assert response["data"][0]["average_project_generation_gwh"] is None
    assert response["data"][0]["firm_project_generation_gwh"] is None
    assert response["data"][0]["project_generation_suspect"] is False
    assert response["metadata"]["data_quality"] == {
        "dropped_project_generation_rows": 1,
        "suspect_project_generation_rows": 0,
        "reason": (
            "Project-generation fields are nulled only when source mapping is "
            "unverifiable or the physical capacity ceiling is exceeded. A project/"
            "gross difference over 3x is retained and marked suspect."
        ),
        "reason_counts": {
            "source_project_generation_mapping_unverifiable": 1,
        },
    }

    thermal_response = await EnergyService(FakeTeias()).get_euas_power_plants("thermal")
    thermal_rows = {item["name"]: item for item in thermal_response["data"]}
    assert thermal_rows["Soma A"]["installed_capacity_mw"] == 44
    assert thermal_rows["Soma A"]["gross_generation_gwh"] == 100
    assert thermal_rows["Soma A"]["project_generation_gwh"] is None
    assert thermal_rows["Soma A"]["project_generation_suspect"] is False
    assert thermal_rows["Afşin-Elbistan B"]["project_generation_gwh"] == 9360
    assert thermal_rows["Afşin-Elbistan B"]["project_generation_suspect"] is True
    assert thermal_response["metadata"]["data_quality"] == {
        "dropped_project_generation_rows": 1,
        "suspect_project_generation_rows": 1,
        "reason": (
            "Project-generation fields are nulled only when source mapping is "
            "unverifiable or the physical capacity ceiling is exceeded. A project/"
            "gross difference over 3x is retained and marked suspect."
        ),
        "reason_counts": {
            "project_generation_gwh_exceeds_capacity_ceiling": 1,
            "project_vs_gross_ratio_exceeds_3x": 1,
        },
    }
