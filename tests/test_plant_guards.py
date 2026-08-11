from turkiye_energy_mcp.parsers.common import (
    annual_energy_ceiling_gwh,
    guard_project_generation,
    guard_single_project_generation,
    normalize_plant_name,
)


def test_normalize_plant_name_variants():
    assert normalize_plant_name("H.POLATKAN/SARIYAR") == normalize_plant_name(
        "H POLATKAN SARIYAR"
    )
    assert normalize_plant_name("DERİNER HES") == normalize_plant_name("Deriner")
    assert normalize_plant_name("Ambarlı KÇ (CC)") == "ambarli kc cc"


def test_guard_drops_values_above_capacity_ceiling():
    average, firm, reasons = guard_project_generation(
        plant_name="BERKE",
        capacity_mw=510,
        gross_generation_gwh=1211.236,
        average_gwh=8900,
        firm_gwh=7400,
    )
    assert average is None
    assert firm is None
    assert "average_project_generation_gwh_exceeds_capacity_ceiling" in reasons
    assert annual_energy_ceiling_gwh(510) == 510 * 8760 / 1000


def test_guard_keeps_plausible_values():
    average, firm, reasons = guard_project_generation(
        plant_name="ATATÜRK",
        capacity_mw=2405,
        gross_generation_gwh=5456.5,
        average_gwh=8900,
        firm_gwh=7400,
    )
    assert average == 8900
    assert firm == 7400
    assert reasons == []


def test_guard_drops_both_fields_when_only_one_fails():
    average, firm, reasons = guard_project_generation(
        plant_name="DERBENT",
        capacity_mw=56.4,
        gross_generation_gwh=114.1,
        average_gwh=596,
        firm_gwh=270,
    )
    assert average is None
    assert firm is None
    assert "average_project_generation_gwh_exceeds_capacity_ceiling" in reasons


def test_guard_drops_pair_when_average_and_gross_differ_over_3x():
    average, firm, reasons = guard_project_generation(
        plant_name="KARAKAYA",
        capacity_mw=1800,
        gross_generation_gwh=6316,
        average_gwh=400,
        firm_gwh=178,
    )
    assert average is None
    assert firm is None
    assert reasons == ["average_vs_gross_ratio_exceeds_3x"]


def test_single_thermal_guard_applies_ceiling_and_ratio():
    value, reasons = guard_single_project_generation(
        plant_name="Soma A",
        capacity_mw=44,
        gross_generation_gwh=100,
        project_generation_gwh=1365,
    )
    assert value is None
    assert reasons == [
        "project_generation_gwh_exceeds_capacity_ceiling",
        "project_vs_gross_ratio_exceeds_3x",
    ]
