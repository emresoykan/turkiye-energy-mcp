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
    average, firm, drop_reasons, suspect_reasons = guard_project_generation(
        plant_name="BERKE",
        capacity_mw=510,
        gross_generation_gwh=1211.236,
        average_gwh=8900,
        firm_gwh=7400,
    )
    assert average is None
    assert firm is None
    assert "average_project_generation_gwh_exceeds_capacity_ceiling" in drop_reasons
    assert suspect_reasons == ["average_vs_gross_ratio_exceeds_3x"]
    assert annual_energy_ceiling_gwh(510) == 510 * 8760 / 1000


def test_guard_keeps_plausible_values():
    average, firm, drop_reasons, suspect_reasons = guard_project_generation(
        plant_name="ATATÜRK",
        capacity_mw=2405,
        gross_generation_gwh=5456.5,
        average_gwh=8900,
        firm_gwh=7400,
    )
    assert average == 8900
    assert firm == 7400
    assert drop_reasons == []
    assert suspect_reasons == []


def test_guard_drops_both_fields_when_only_one_fails():
    average, firm, drop_reasons, suspect_reasons = guard_project_generation(
        plant_name="DERBENT",
        capacity_mw=56.4,
        gross_generation_gwh=114.1,
        average_gwh=596,
        firm_gwh=270,
    )
    assert average is None
    assert firm is None
    assert "average_project_generation_gwh_exceeds_capacity_ceiling" in drop_reasons
    assert suspect_reasons == ["average_vs_gross_ratio_exceeds_3x"]


def test_guard_keeps_pair_and_marks_suspect_when_average_and_gross_differ_over_3x():
    average, firm, drop_reasons, suspect_reasons = guard_project_generation(
        plant_name="KARAKAYA",
        capacity_mw=1800,
        gross_generation_gwh=6316,
        average_gwh=400,
        firm_gwh=178,
    )
    assert average == 400
    assert firm == 178
    assert drop_reasons == []
    assert suspect_reasons == ["average_vs_gross_ratio_exceeds_3x"]


def test_single_thermal_guard_applies_ceiling_and_ratio():
    value, drop_reasons, suspect_reasons = guard_single_project_generation(
        plant_name="Soma A",
        capacity_mw=44,
        gross_generation_gwh=100,
        project_generation_gwh=1365,
    )
    assert value is None
    assert drop_reasons == ["project_generation_gwh_exceeds_capacity_ceiling"]
    assert suspect_reasons == ["project_vs_gross_ratio_exceeds_3x"]


def test_single_thermal_guard_keeps_afsin_value_and_marks_suspect():
    value, drop_reasons, suspect_reasons = guard_single_project_generation(
        plant_name="Afşin-Elbistan B",
        capacity_mw=1440,
        gross_generation_gwh=2834,
        project_generation_gwh=9360,
    )
    assert value == 9360
    assert drop_reasons == []
    assert suspect_reasons == ["project_vs_gross_ratio_exceeds_3x"]
