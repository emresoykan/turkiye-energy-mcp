import httpx
import pytest
import respx

from turkiye_energy_mcp.config import Settings
from turkiye_energy_mcp.exceptions import EnergyDataError, ErrorCode
from turkiye_energy_mcp.http_client import ResilientHTTPClient


@pytest.mark.asyncio
@respx.mock
async def test_mocked_http_json_response():
    route = respx.get("https://example.test/data").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    client = ResilientHTTPClient(Settings(http_max_retries=0))
    try:
        assert await client.get_json("https://example.test/data") == {"success": True}
        assert route.called
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_http_404_maps_to_data_not_available():
    respx.get("https://example.test/missing").mock(return_value=httpx.Response(404))
    client = ResilientHTTPClient(Settings(http_max_retries=0))
    try:
        with pytest.raises(EnergyDataError) as caught:
            await client.get_bytes("https://example.test/missing")
        assert caught.value.code is ErrorCode.DATA_NOT_AVAILABLE
    finally:
        await client.close()
