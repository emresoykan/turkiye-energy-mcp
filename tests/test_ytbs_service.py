from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from turkiye_energy_mcp.cache import AsyncTTLCache
from turkiye_energy_mcp.config import Settings
from turkiye_energy_mcp.exceptions import EnergyDataError, ErrorCode
from turkiye_energy_mcp.http_client import ResilientHTTPClient
from turkiye_energy_mcp.models import ISTANBUL
from turkiye_energy_mcp.ytbs.parsers import parse_daily_report, parse_day_series
from turkiye_energy_mcp.ytbs.repository import YtbsRepository
from turkiye_energy_mcp.ytbs.service import YtbsService
from turkiye_energy_mcp.ytbs.store import SERIES_TABLE, CsvStore

FIXTURES = Path(__file__).parent / "fixtures" / "ytbs"
FETCHED = datetime(2026, 10, 19, 9, 0, tzinfo=ISTANBUL)


@pytest.fixture
def data_dir(tmp_path):
    store = CsvStore(tmp_path)
    ajax = (FIXTURES / "gunluk_rapor_ajax_2026-10-18.xml").read_text(encoding="utf-8")
    store.upsert(SERIES_TABLE, parse_day_series(ajax, date(2026, 10, 18)), FETCHED)
    tables = parse_daily_report((FIXTURES / "gunluk_2026-10-01.xlsx").read_bytes())
    for table, rows in tables.items():
        store.upsert(table, rows, FETCHED)
    store.record_sync(synced=["2026-10-01", "2026-10-18"], failed={}, finished_at=FETCHED)
    return tmp_path


def make_service(**settings) -> YtbsService:
    s = Settings(**settings)
    return YtbsService(YtbsRepository(s, ResilientHTTPClient(s), AsyncTTLCache()))


async def test_installed_capacity_snapshot_by_source(data_dir):
    result = await make_service(ytbs_data_dir=str(data_dir)).get_installed_capacity(
        "2026-10-10", "kaynak"
    )
    wind = next(r for r in result["data"] if r["kaynak"] == "ruzgar")
    assert wind["tarih"] == "2026-10-01"
    assert wind["kurulu_guc_mw"] == pytest.approx(15570.2684)
    assert wind["santral_sayisi"] == 420
    assert sum(r["kurulu_guc_mw"] for r in result["data"]) == pytest.approx(127241.2, abs=0.01)
    assert result["metadata"]["provisional"] is True


async def test_hourly_balance_reports_missing_days(data_dir):
    result = await make_service(ytbs_data_dir=str(data_dir)).get_hourly_balance(
        "2026-10-01", "2026-10-02"
    )
    assert len(result["data"]) == 24
    assert result["data"][0]["tuketim_mwh"] == pytest.approx(36320.76)
    assert result["metadata"]["missing_days"] == ["2026-10-02"]


async def test_15min_source_filter_accepts_turkish_and_english_names(data_dir):
    svc = make_service(ytbs_data_dir=str(data_dir))
    result = await svc.get_load_generation_15min("2026-10-18", "2026-10-18", ["rüzgar", "solar"])
    row = result["data"][0]
    assert set(row) == {
        "tarih",
        "zaman",
        "yuk_mw",
        "ruzgar_mw",
        "gunes_mw",
        "toplam_uretim_mw",
        "cekim_zamani",
    }
    with pytest.raises(EnergyDataError) as exc:
        await svc.get_load_generation_15min("2026-10-01", "2026-10-18")
    assert exc.value.code == ErrorCode.INVALID_PARAMETER


async def test_unconfigured_store_is_a_clear_error():
    with pytest.raises(EnergyDataError) as exc:
        await make_service().get_sync_status()
    assert "YTBS_DATA_DIR" in exc.value.message


@respx.mock
async def test_github_backend_reads_month_files_with_token(data_dir):
    base = "https://api.github.com/repos/acme/ytbs-veri/contents"
    month = (data_dir / "saatlik_denge" / "2026" / "2026-10.csv").read_text(encoding="utf-8")
    manifest = (data_dir / "manifest.json").read_text(encoding="utf-8")
    month_route = respx.get(f"{base}/saatlik_denge/2026/2026-10.csv").mock(
        return_value=httpx.Response(200, text=month)
    )
    respx.get(f"{base}/manifest.json").mock(return_value=httpx.Response(200, text=manifest))
    respx.get(f"{base}/saatlik_denge/2026/2026-09.csv").mock(return_value=httpx.Response(404))

    svc = make_service(ytbs_data_repo="acme/ytbs-veri", ytbs_data_token="secret-read-token")
    result = await svc.get_hourly_balance("2026-09-30", "2026-10-01")
    assert len(result["data"]) == 24
    request = month_route.calls.last.request
    assert request.headers["Authorization"] == "Bearer secret-read-token"
    assert request.url.params["ref"] == "main"
    assert result["metadata"]["storage_backend"] == "GitHub acme/ytbs-veri@main"
