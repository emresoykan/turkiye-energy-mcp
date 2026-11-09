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


def _generation_mix_frame() -> pd.DataFrame:
    # parse_generation_mix: yıl 1. sütunda, kaynaklar 2..17 (2 = taş kömürü+asfaltit,
    # 15 = jeotermal+rüzgâr, 17 = toplam).
    frame = pd.DataFrame([[None] * 18 for _ in range(2)])
    frame.iloc[1, 1] = 2024
    for column in range(2, 18):
        frame.iloc[1, column] = float(column)
    frame.iloc[1, 2] = 1234.5
    frame.iloc[1, 15] = 45678.9
    return frame


def _capacity_mix_frame() -> pd.DataFrame:
    # parse_capacity_mix: yıl 2. sütunda, 3 = taş kömürü, 14 = jeotermal, 15 = rüzgâr.
    frame = pd.DataFrame([[None] * 18 for _ in range(2)])
    frame.iloc[1, 2] = 2024
    for column in range(3, 18):
        frame.iloc[1, column] = float(column)
    return frame


class _AnnualTeias:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    async def annual_workbook(self, selector: str) -> Workbook:
        return workbook(selector, workbook_bytes(self.frames[selector]))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "combined", "value", "label"),
    [
        ("rüzgar", "geothermal_and_wind", 45678.9, "saf rüzgâr değildir"),
        ("Rüzgâr", "geothermal_and_wind", 45678.9, "saf rüzgâr değildir"),
        ("wind", "geothermal_and_wind", 45678.9, "saf rüzgâr değildir"),
        ("jeotermal", "geothermal_and_wind", 45678.9, "saf jeotermal değildir"),
        ("taş kömürü", "hard_coal_and_asphaltite", 1234.5, "saf taş kömürü değildir"),
        ("asfaltit", "hard_coal_and_asphaltite", 1234.5, "saf asfaltit değildir"),
    ],
)
async def test_generation_maps_pure_source_to_combined_column_with_warning(
    requested, combined, value, label
):
    service = EnergyService(_AnnualTeias({"generation_mix": _generation_mix_frame()}))
    response = await service.get_generation(2024, 2024, requested)
    assert response["data"] == [{"year": 2024, "source": combined, "generation_gwh": value}]
    meta = response["metadata"]
    assert meta["requested_source"] == requested
    assert len(meta["warnings"]) == 1 and label in meta["warnings"][0]
    assert combined in meta["warnings"][0]
    assert meta["notes"].startswith("UYARI: TEİAŞ bu tabloda")


@pytest.mark.asyncio
async def test_generation_exact_source_has_no_combined_warning():
    service = EnergyService(_AnnualTeias({"generation_mix": _generation_mix_frame()}))
    response = await service.get_generation(2024, 2024, "güneş")
    assert [r["source"] for r in response["data"]] == ["solar"]
    assert "warnings" not in response["metadata"]
    assert not response["metadata"]["notes"].startswith("UYARI")


@pytest.mark.asyncio
async def test_installed_capacity_keeps_pure_wind_and_hard_coal():
    service = EnergyService(_AnnualTeias({"capacity_mix": _capacity_mix_frame()}))
    wind = await service.get_installed_capacity(2024, 2024, "rüzgâr")
    assert wind["data"] == [{"year": 2024, "source": "wind", "capacity_mw": 15.0}]
    assert "warnings" not in wind["metadata"]
    coal = await service.get_installed_capacity(2024, 2024, "taş kömürü")
    assert [r["source"] for r in coal["data"]] == ["hard_coal"]
    assert "warnings" not in coal["metadata"]


@pytest.mark.asyncio
async def test_euas_generation_maps_wind_to_combined_hydro_column_with_warning():
    # parse_euas_generation_by_source: yıllar 6. satırda, etiketler 8..17. satırların 3. sütununda.
    frame = pd.DataFrame([[None] * 6 for _ in range(18)])
    frame.iloc[6, 4] = 2024
    frame.iloc[8, 3] = "Thermal Total"
    frame.iloc[8, 4] = 100.0
    frame.iloc[9, 3] = "Hydro+Jeothermal+Wind Total"
    frame.iloc[9, 4] = 200.0
    frame.iloc[17, 3] = "Kaynak: TEİAŞ"  # boş son satırlar Excel'e yazılmaz
    service = EnergyService(_AnnualTeias({"euas_generation_by_source": frame}))
    for requested in ("rüzgâr", "hidro"):
        response = await service.get_euas_generation(2024, 2024, requested)
        assert response["data"] == [
            {"year": 2024, "source": "hydro_geothermal_wind", "generation_gwh": 200.0}
        ]
        assert "değer saf" in response["metadata"]["warnings"][0]
