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
        "teias_get_monthly_energy": service.get_monthly_energy(
            "2025-01-01", "2025-12-31", "tüketim"
        ),
        "teias_get_generation": service.get_generation(2024, 2024, "toplam"),
        "teias_get_installed_capacity": service.get_installed_capacity(
            2024, 2024, "rüzgar"
        ),
        "teias_get_peak_demand": service.get_peak_demand(2024, 2024),
        "teias_get_import_export": service.get_import_export(
            2024, 2024, "Bulgaristan"
        ),
        "teias_get_transmission_statistics": service.get_transmission_statistics(
            2024, 2024
        ),
        "teias_get_renewable_summary": service.get_renewable_summary(2024, 2024),
        "teias_get_system_summary": service.get_system_summary(2024),
        "euas_get_power_plants": service.get_euas_power_plants("hydro", None),
        "euas_get_plant": service.get_euas_plant("Keban"),
        "euas_get_installed_capacity": service.get_euas_installed_capacity(2024, 2024),
        "euas_get_generation": service.get_euas_generation(2024, 2024, "termik"),
        "euas_get_monthly_generation": service.get_euas_monthly_generation(
            "2025-01-01", "2025-12-31", "hidro"
        ),
        "get_euas_share_of_installed_capacity": service.get_euas_capacity_share(
            2024, 2024
        ),
        "get_euas_share_of_generation": service.get_euas_generation_share(2024, 2024),
        "compare_euas_vs_turkey_generation": service.compare_euas_vs_turkey_generation(
            2024, 2024
        ),
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
