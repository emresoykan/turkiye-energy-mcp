from turkiye_energy_mcp.parsers.common import (
    annual_energy_ceiling_gwh,
    guard_project_generation,
    normalize_plant_name,
)


def test_normalize_plant_name_variants():
    assert normalize_plant_name("H.POLATKAN/SARIYAR") == normalize_plant_name(
        "H POLATKAN SARIYAR"
    )
    assert normalize_plant_name("DERİNER HES") == normalize_plant_name("Deriner")
    assert normalize_plant_name("Ambarlı KÇ (CC)") == "ambarli kc cc"


def test_guard_drops_values_above_capacity_ceiling():
    average, firm = guard_project_generation(
        plant_name="BERKE",
        capacity_mw=510,
        average_gwh=8900,
        firm_gwh=7400,
    )
    assert average is None
    assert firm is None
    assert annual_energy_ceiling_gwh(510) == 510 * 8760 / 1000


def test_guard_keeps_plausible_values():
    average, firm = guard_project_generation(
        plant_name="ATATÜRK",
        capacity_mw=2405,
        average_gwh=8900,
        firm_gwh=7400,
    )
    assert average == 8900
    assert firm == 7400
