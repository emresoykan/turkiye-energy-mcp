import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _Entry(Generic[T]):
    value: T
    expires_at: float


class AsyncTTLCache:
    """Small in-memory TTL cache with per-key request coalescing."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry[object]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def get(self, key: str) -> object | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._entries.pop(key, None)
            return None
        return entry.value

    async def set(self, key: str, value: T, ttl_seconds: int) -> T:
        self._entries[key] = _Entry(
            value=value,
            expires_at=time.monotonic() + max(0, ttl_seconds),
        )
        return value

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl_seconds: int,
    ) -> T:
        cached = await self.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        async with self._guard:
            lock = self._locks.setdefault(key, asyncio.Lock())

        async with lock:
            cached = await self.get(key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            value = await factory()
            return await self.set(key, value, ttl_seconds)

    async def clear(self) -> None:
        self._entries.clear()
        self._locks.clear()

    def __len__(self) -> int:
        now = time.monotonic()
        return sum(entry.expires_at > now for entry in self._entries.values())
