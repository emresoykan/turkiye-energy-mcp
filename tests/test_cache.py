import asyncio

import pytest

from turkiye_energy_mcp.cache import AsyncTTLCache


@pytest.mark.asyncio
async def test_cache_hit_and_expiration():
    cache = AsyncTTLCache()
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return {"value": calls}

    first = await cache.get_or_set("key", factory, 1)
    second = await cache.get_or_set("key", factory, 1)
    assert first == second == {"value": 1}
    assert calls == 1

    await asyncio.sleep(1.01)
    third = await cache.get_or_set("key", factory, 1)
    assert third == {"value": 2}


@pytest.mark.asyncio
async def test_cache_coalesces_concurrent_calls():
    cache = AsyncTTLCache()
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return 42

    values = await asyncio.gather(
        *(cache.get_or_set("same", factory, 10) for _ in range(5))
    )
    assert values == [42] * 5
    assert calls == 1
