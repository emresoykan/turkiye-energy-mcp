import pytest

from turkiye_energy_mcp.exceptions import EnergyDataError
from turkiye_energy_mcp.parsers.common import (
    normalize_energy,
    normalize_turkish_number,
    parse_date,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234,56", 1234.56),
        ("12,5", 12.5),
        ("1,234.56", 1234.56),
        (123.4, 123.4),
        ("-", None),
        ("N/A", None),
        ("", None),
    ],
)
def test_normalize_turkish_number(raw, expected):
    assert normalize_turkish_number(raw) == expected


def test_parse_turkish_date():
    assert parse_date("11.08.2026").isoformat() == "2026-08-11"
    assert parse_date("2026-08-11").isoformat() == "2026-08-11"


def test_invalid_date():
    with pytest.raises(EnergyDataError):
        parse_date("2026/99/99")


def test_energy_normalization_does_not_confuse_mw_and_mwh():
    assert normalize_energy(1.5, "TWh", "GWh") == 1500
    assert normalize_energy(1500, "GWh", "TWh") == 1.5
    with pytest.raises(EnergyDataError):
        normalize_energy(1, "MW", "MWh")
