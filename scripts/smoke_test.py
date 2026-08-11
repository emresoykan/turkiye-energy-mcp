#!/usr/bin/env python3
"""Internet-dependent smoke checks for verified official sources."""

import asyncio
import json

from turkiye_energy_mcp.cache import AsyncTTLCache
from turkiye_energy_mcp.clients.teias import TeiasClient
from turkiye_energy_mcp.config import get_settings
from turkiye_energy_mcp.http_client import ResilientHTTPClient
from turkiye_energy_mcp.service import EnergyService


async def run() -> int:
    settings = get_settings()
    http = ResilientHTTPClient(settings)
    service = EnergyService(TeiasClient(settings, http, AsyncTTLCache()))
    checks = {
        "capacity": service.get_installed_capacity(2024, 2024, "rüzgar"),
        "generation": service.get_generation(2024, 2024, "toplam"),
        "trade": service.get_import_export(2024, 2024, "Bulgaristan"),
        "euas_plants": service.get_euas_power_plants("hydro", None),
        "euas_share": service.get_euas_generation_share(2024, 2024),
    }
    results: dict[str, object] = {}
    exit_code = 0
    try:
        for name, check in checks.items():
            try:
                response = await check
                results[name] = {
                    "ok": not response.get("error", False),
                    "rows": len(response.get("data", [])),
                    "source": response.get("source"),
                    "subject": response.get("subject"),
                }
            except Exception as exc:
                results[name] = {"ok": False, "error": str(exc)}
                exit_code = 1
    finally:
        await http.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
