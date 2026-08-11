#!/usr/bin/env python3
"""Internet-dependent smoke checks for verified official sources."""

import asyncio
import json

from turkiye_energy_mcp.cache import AsyncTTLCache
from turkiye_energy_mcp.clients.teias import TeiasClient
from turkiye_energy_mcp.config import get_settings
from turkiye_energy_mcp.exceptions import EnergyDataError
from turkiye_energy_mcp.freshness import MONTHLY_GALLERY_PREFIX, slug_period_year
from turkiye_energy_mcp.http_client import ResilientHTTPClient
from turkiye_energy_mcp.service import EnergyService


async def run() -> int:
    settings = get_settings()
    http = ResilientHTTPClient(settings)
    client = TeiasClient(settings, http, AsyncTTLCache())
    service = EnergyService(client)
    latest_annual = await client.latest_annual_period("i-kurulu-guc")
    galleries = await client.list_galleries()
    monthly_years = sorted(
        {
            year
            for item in galleries
            if isinstance(item.get("slug"), str)
            and str(item["slug"]).endswith(MONTHLY_GALLERY_PREFIX)
            for year in [slug_period_year(str(item["slug"]))]
            if year is not None
        }
    )
    latest_monthly_year = max(monthly_years)
    monthly = await client.monthly_workbook(latest_monthly_year)
    latest_month = monthly.latest_available_period or f"{latest_monthly_year}-01"
    month_start = f"{latest_month}-01"
    checks = {
        "teias_get_monthly_energy": service.get_monthly_energy(
            month_start, f"{latest_month}-28", "tüketim"
        ),
        "teias_get_generation": service.get_generation(
            latest_annual, latest_annual, "toplam"
        ),
        "teias_get_installed_capacity": service.get_installed_capacity(
            latest_annual, latest_annual, "rüzgar"
        ),
        "teias_get_peak_demand": service.get_peak_demand(latest_annual, latest_annual),
        "teias_get_import_export": service.get_import_export(
            latest_annual, latest_annual, "Bulgaristan"
        ),
        "teias_get_transmission_statistics": service.get_transmission_statistics(
            latest_annual, latest_annual
        ),
        "teias_get_renewable_summary": service.get_renewable_summary(
            latest_annual, latest_annual
        ),
        "teias_get_system_summary": service.get_system_summary(latest_annual),
        "euas_get_power_plants": service.get_euas_power_plants("hydro", None),
        "euas_get_plant": service.get_euas_plant("Keban"),
        "euas_get_installed_capacity": service.get_euas_installed_capacity(
            latest_annual, latest_annual
        ),
        "euas_get_generation": service.get_euas_generation(
            latest_annual, latest_annual, "termik"
        ),
        "euas_get_monthly_generation": service.get_euas_monthly_generation(
            month_start, f"{latest_month}-28", "hidro"
        ),
        "get_euas_share_of_installed_capacity": service.get_euas_capacity_share(
            latest_annual, latest_annual
        ),
        "get_euas_share_of_generation": service.get_euas_generation_share(
            latest_annual, latest_annual
        ),
        "compare_euas_vs_turkey_generation": service.compare_euas_vs_turkey_generation(
            latest_annual, latest_annual
        ),
    }
    results: dict[str, object] = {}
    exit_code = 0
    try:
        for name, check in checks.items():
            try:
                response = await check
                metadata = response.get("metadata") or {}
                results[name] = {
                    "ok": not response.get("error", False),
                    "rows": len(response.get("data", [])),
                    "source": response.get("source"),
                    "subject": response.get("subject"),
                    "latest_available_period": metadata.get("latest_available_period"),
                    "data_freshness": metadata.get("data_freshness"),
                }
                if response.get("error", False) or not metadata.get(
                    "latest_available_period"
                ):
                    exit_code = 1
                    results[name]["ok"] = False
            except Exception as exc:
                results[name] = {"ok": False, "error": str(exc)}
                exit_code = 1
        try:
            await service.get_generation(latest_annual + 5, latest_annual + 5, "toplam")
            results["freshness_unavailable_future_year"] = {
                "ok": False,
                "error": "expected miss",
            }
            exit_code = 1
        except EnergyDataError as exc:
            details = exc.details or {}
            ok = (
                details.get("data_freshness") == "unavailable"
                and details.get("latest_available_period") is not None
            )
            results["freshness_unavailable_future_year"] = {
                "ok": ok,
                "latest_available_period": details.get("latest_available_period"),
                "data_freshness": details.get("data_freshness"),
            }
            if not ok:
                exit_code = 1
    finally:
        await http.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
