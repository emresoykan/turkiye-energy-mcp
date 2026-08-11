import pytest

from turkiye_energy_mcp.server import mcp, teias_get_monthly_energy


@pytest.mark.asyncio
async def test_production_tool_list_has_only_verified_tools():
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert len(names) == 16
    assert "teias_get_monthly_energy" in names
    assert "euas_get_power_plants" in names
    assert "get_euas_share_of_generation" in names
    assert not any("epias" in name.casefold() for name in names)


@pytest.mark.asyncio
async def test_tool_returns_structured_invalid_parameter_error():
    response = await teias_get_monthly_energy("2026-08-12", "2026-08-11")
    assert response == {
        "error": True,
        "code": "INVALID_PARAMETER",
        "message": "Başlangıç tarihi bitiş tarihinden büyük olamaz.",
        "source": None,
    }
