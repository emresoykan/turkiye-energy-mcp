from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from turkiye_energy_mcp.exceptions import EnergyDataError, ErrorCode
from turkiye_energy_mcp.models import ISTANBUL
from turkiye_energy_mcp.ytbs.client import YtbsClient
from turkiye_energy_mcp.ytbs.parsers import (
    balance_gap_mwh,
    parse_daily_report,
    parse_day_series,
    parse_form_state,
)
from turkiye_energy_mcp.ytbs.store import CsvStore

FIXTURES = Path(__file__).parent / "fixtures" / "ytbs"
PAGE = (FIXTURES / "istatistikler_2026-10-19.html").read_text(encoding="utf-8")
AJAX = (FIXTURES / "gunluk_rapor_ajax_2026-10-18.xml").read_text(encoding="utf-8")
EXCEL = (FIXTURES / "gunluk_2026-10-01.xlsx").read_bytes()

SOURCE_CODES = {
    "nukleer",
    "linyit",
    "taskomur",
    "asfaltitkomur",
    "ithalkomur",
    "fueloil",
    "motorin",
    "nafta",
    "lpg",
    "dogalgaz",
    "lng",
    "atikisi",
    "jeotermal",
    "biyokutle",
    "barajli",
    "akarsu",
    "ruzgar",
    "gunes",
}


def test_form_state_from_page():
    form = parse_form_state(PAGE)
    assert form.post_path.startswith("/ytbsbilgi/frm_istatistikler.jsf")
    assert form.hidden1 == "13"
    assert form.year == "2026"
    assert form.daily_excel_button == "formdash:j_idt35"
    assert form.view_state


def test_partial_day_from_page_keeps_unpublished_points_empty():
    rows = parse_day_series(PAGE, date(2026, 10, 19))
    assert len(rows) == 96
    assert sum(r["yuk_mw"] is not None for r in rows) == 55
    assert sum(r["toplam_uretim_mw"] is not None for r in rows) == 54
    assert rows[-1]["zaman"] == "2026-10-20 00:00"
    assert rows[-1]["ruzgar_mw"] is None


def test_ajax_partial_response_with_split_cdata():
    assert "}]]]><![CDATA[;" in AJAX
    rows = parse_day_series(AJAX, date(2026, 10, 18))
    assert len(rows) == 96 and all(r["yuk_mw"] for r in rows)
    assert max(r["yuk_mw"] for r in rows) == pytest.approx(40119.72)
    assert {k[:-3] for k in rows[0] if k.endswith("_mw")} >= SOURCE_CODES
    for r in rows:
        parts = sum(r[f"{c}_mw"] for c in SOURCE_CODES)
        assert parts == pytest.approx(r["toplam_uretim_mw"], abs=0.01)


def test_wrong_day_is_rejected():
    with pytest.raises(EnergyDataError) as exc:
        parse_day_series(AJAX, date(2026, 10, 17))
    assert exc.value.code == ErrorCode.DATA_NOT_AVAILABLE


def test_daily_report_tables_match_workbook_totals():
    tables = parse_daily_report(EXCEL, expected_day=date(2026, 10, 1))

    hourly = tables["saatlik_denge"]
    assert len(hourly) == 24
    assert hourly[0]["zaman_baslangic"] == "2026-10-01 00:00"
    assert sum(r["tuketim_mwh"] for r in hourly) == pytest.approx(953244.06)
    assert balance_gap_mwh(hourly) < 1.0

    capacity = tables["kurulu_guc"]
    assert sum(r["kurulu_guc_mw"] for r in capacity) == pytest.approx(127241.2005, abs=0.01)
    assert {r["kaynak"] for r in capacity} <= SOURCE_CODES
    wind = sum(r["kurulu_guc_mw"] for r in capacity if r["kaynak"] == "ruzgar")
    assert wind == pytest.approx(15570.2684)

    assert sum(r["santral_sayisi"] for r in tables["santral_sayisi"]) == 44204
    daily = tables["gunluk_uretim_kaynak"]
    assert sum(r["uretim_mwh"] for r in daily) == pytest.approx(972161.219)
    assert {r["kaynak"] for r in daily} <= SOURCE_CODES
    assert len(tables["gunluk_uretim_kurulus"]) == 5

    available = tables["emre_amade"]
    assert {"ruzgar", "toplam", "taleptahmin", "arzguvenligiorani"} - {
        r["kalem"] for r in available
    } == set()
    assert len(tables["bolgesel_puant"]) == 9
    peak = next(r for r in tables["puant"] if r["donem"] == "GÜNLÜK")
    assert peak["deger"] == pytest.approx(46441.1) and peak["birim"] == "MW"
    assert tables["su_durumu"][0]["ayin_gunu"] == 1


def test_daily_report_wrong_day_is_rejected():
    with pytest.raises(EnergyDataError):
        parse_daily_report(EXCEL, expected_day=date(2026, 10, 2))


def test_store_replaces_revised_day_and_keeps_others(tmp_path):
    store = CsvStore(tmp_path)
    t0 = datetime(2026, 10, 19, 9, 0, tzinfo=ISTANBUL)
    store.upsert("t", [{"tarih": "2026-10-01", "v": 1}, {"tarih": "2026-10-02", "v": 2}], t0)
    store.upsert("t", [{"tarih": "2026-10-02", "v": 3}], t0.replace(hour=10))
    rows = store.read("t", date(2026, 10, 1), date(2026, 10, 31))
    assert [(r["tarih"], r["v"]) for r in rows] == [("2026-10-01", "1"), ("2026-10-02", "3")]
    assert rows[1]["cekim_zamani"].startswith("2026-10-19T10:00")
    assert (tmp_path / "t" / "2026" / "2026-10.csv").exists()


def test_client_fetches_day_without_leaking_ajax_headers():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.headers.get("Faces-Request")))
        if request.method == "GET":
            assert request.headers.get("Faces-Request") is None
            return httpx.Response(200, text=PAGE)
        body = request.content.decode()
        if "partial.ajax=true" in body:
            assert "bitisTarihi2_input=18-10-2026" in body
            return httpx.Response(200, text=AJAX)
        assert "formdash%3Aj_idt35.x=10" in body
        return httpx.Response(200, content=EXCEL)

    client = YtbsClient(transport=httpx.MockTransport(handler), backoff_seconds=0)
    result = client.fetch_day(date(2026, 10, 18))
    assert len(result.series) == 96
    assert result.excel == EXCEL
    assert seen == [("GET", None), ("POST", "partial/ajax"), ("GET", None), ("POST", None)]


def test_client_reports_region_block_as_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection reset by peer", request=request)

    client = YtbsClient(transport=httpx.MockTransport(handler), backoff_seconds=0)
    with pytest.raises(EnergyDataError) as exc:
        client.fetch_series(date(2026, 10, 18))
    assert exc.value.code == ErrorCode.SOURCE_UNAVAILABLE
    assert "Türkiye" in exc.value.message


def test_sync_cli_writes_partitioned_csv_and_manifest(tmp_path, monkeypatch):
    from turkiye_energy_mcp.ytbs import cli

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PAGE if request.method == "GET" else AJAX)

    monkeypatch.setattr(
        cli, "YtbsClient", lambda: YtbsClient(transport=httpx.MockTransport(handler))
    )
    code = cli.main(
        [
            "--baslangic",
            "2026-10-18",
            "--bitis",
            "2026-10-18",
            "--excel-yok",
            "--veri-klasoru",
            str(tmp_path),
            "--bekleme",
            "0",
        ]
    )
    assert code == 0
    rows = CsvStore(tmp_path).read("15dk_yuk_uretim", date(2026, 10, 18), date(2026, 10, 18))
    assert len(rows) == 96
    assert "2026-10-18" in CsvStore(tmp_path).load_manifest()["gunler"]


def test_sync_cli_git_push_commits_changes(tmp_path, monkeypatch):
    import subprocess

    from turkiye_energy_mcp.ytbs import cli

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    work = tmp_path / "veri"
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(work), "config", key, value], check=True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PAGE if request.method == "GET" else AJAX)

    monkeypatch.setattr(
        cli, "YtbsClient", lambda: YtbsClient(transport=httpx.MockTransport(handler))
    )
    args = [
        "--baslangic",
        "2026-10-18",
        "--bitis",
        "2026-10-18",
        "--excel-yok",
        "--veri-klasoru",
        str(work),
        "--bekleme",
        "0",
        "--git-push",
    ]
    assert cli.main(args) == 0
    log = subprocess.run(
        ["git", "-C", str(remote), "log", "--oneline"], capture_output=True, text=True, check=True
    ).stdout
    assert "ytbs-sync 2026-10-18..2026-10-18" in log


def _cli_with(monkeypatch, handler, today):
    from turkiye_energy_mcp.ytbs import cli

    monkeypatch.setattr(
        cli, "YtbsClient", lambda: YtbsClient(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(cli, "_today", lambda: today)
    return cli


def test_sync_skips_daily_report_for_today(tmp_path, monkeypatch):
    excel_posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=PAGE)
        if "partial.ajax=true" not in request.content.decode():
            excel_posts.append(request)
        return httpx.Response(200, text=AJAX)

    cli = _cli_with(monkeypatch, handler, today=date(2026, 10, 18))
    args = ["--gun", "1", "--veri-klasoru", str(tmp_path), "--bekleme", "0"]
    assert cli.main(args) == 0
    assert excel_posts == []
    rows = CsvStore(tmp_path).read("15dk_yuk_uretim", date(2026, 10, 18), date(2026, 10, 18))
    assert len(rows) == 96


def test_sync_keeps_series_when_past_daily_report_fails(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=PAGE)
        if "partial.ajax=true" in request.content.decode():
            return httpx.Response(200, text=AJAX)
        return httpx.Response(200, text="<html>rapor yok</html>")

    cli = _cli_with(monkeypatch, handler, today=date(2026, 10, 19))
    args = [
        "--baslangic",
        "2026-10-18",
        "--bitis",
        "2026-10-18",
        "--veri-klasoru",
        str(tmp_path),
        "--bekleme",
        "0",
    ]
    assert cli.main(args) == 1
    store = CsvStore(tmp_path)
    assert len(store.read("15dk_yuk_uretim", date(2026, 10, 18), date(2026, 10, 18))) == 96
    assert "2026-10-18" in store.load_manifest()["son_hatalar"]


def test_sync_writes_utf8_log_and_keeps_stderr_clean_on_success(tmp_path, monkeypatch, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PAGE if request.method == "GET" else AJAX)

    cli = _cli_with(monkeypatch, handler, today=date(2026, 10, 18))
    log = tmp_path / "ytbs-sync.log"
    args = [
        "--gun",
        "1",
        "--veri-klasoru",
        str(tmp_path / "veri"),
        "--bekleme",
        "0",
        "--log",
        str(log),
    ]
    assert cli.main(args) == 0
    assert capsys.readouterr().err == ""
    text = log.read_text(encoding="utf-8")
    assert "günlük rapor gün bitince alınacak" in text
    assert "başarılı 1, hatalı 0" in text


# --- Ayrıştırıcı sağlamlığı ve kısmi hata davranışı ---


def _edited_excel(edit) -> bytes:
    """Fixture çalışma kitabını `edit(wb)` ile değiştirip XLSX baytı olarak döndürür."""
    import io

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(EXCEL))
    edit(wb)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _sheet_titled(wb, fragment):
    from turkiye_energy_mcp.ytbs.parsers import _sheet

    return _sheet(wb, fragment)


def _cell_with(ws, value):
    return next(c for row in ws.iter_rows() for c in row if c.value == value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("-", None),
        (" - ", None),
        (12, 12.0),
        (1.5, 1.5),
        ("100", 100.0),
        ("12,5", 12.5),
        ("1.234,5", 1234.5),
        ("-1.234,5", -1234.5),
        ("1.234.567", 1234567.0),
        ("12.5", 12.5),
    ],
)
def test_num_handles_missing_and_turkish_format(raw, expected):
    from turkiye_energy_mcp.ytbs.parsers import _num

    assert _num(raw) == expected


@pytest.mark.parametrize("raw", ["yok", "1.234", "-12.500"])
def test_num_rejects_text_with_parsing_error(raw):
    # "1.234" is ambiguous in a Turkish source (1234 or 1.234): fail, don't guess.
    from turkiye_energy_mcp.ytbs.parsers import _num

    with pytest.raises(EnergyDataError) as exc:
        _num(raw)
    assert exc.value.code == ErrorCode.PARSING_ERROR


def test_tr_date_accepts_date_and_datetime_cells():
    from turkiye_energy_mcp.ytbs.parsers import _tr_date

    assert _tr_date("01.10.2026") == date(2026, 10, 1)
    excel_cell = datetime(2026, 10, 1, 0, 0)  # noqa: DTZ001 - openpyxl tarihleri naive döner
    assert _tr_date(excel_cell) == date(2026, 10, 1)
    assert _tr_date(date(2026, 10, 1)) == date(2026, 10, 1)


def test_daily_report_accepts_date_cells_and_dash_values():
    def edit(wb):
        peaks = _sheet_titled(wb, "GÜNLÜK TÜRKİYE PUANT RAPORU")
        _cell_with(peaks, "13.08.2026").value = datetime(2026, 8, 13)  # noqa: DTZ001
        regional = _sheet_titled(wb, "BÖLGESEL PUANT RAPORU")
        trakya_day = _cell_with(regional, "Trakya YTM").offset(column=2)
        trakya_day.value = datetime(2026, 10, 1)  # noqa: DTZ001
        hourly = _sheet_titled(wb, "TÜKETİMİNİN SAATLİK GELİŞİMİ")
        _cell_with(hourly, "01:00").offset(column=3).value = "-"
        _cell_with(hourly, "02:00").offset(column=1).value = "11.900,02"

    tables = parse_daily_report(_edited_excel(edit), expected_day=date(2026, 10, 1))
    yearly = next(
        r
        for r in tables["puant"]
        if r["donem"] == "YILLIK" and r["kalem"] == "MAKSİMUM ANLIK TALEP (MW)"
    )
    assert yearly["olay_tarihi"] == "2026-08-13"
    trakya = next(r for r in tables["bolgesel_puant"] if r["bolge"] == "Trakya YTM")
    assert trakya["puant_gunu"] == "2026-10-01"
    hourly = tables["saatlik_denge"]
    assert hourly[0]["depolama_desarj_mwh"] is None
    assert hourly[1]["yenilenemez_uretim_mwh"] == pytest.approx(11900.02)


def test_daily_report_missing_sheet_is_an_explicit_error():
    def edit(wb):
        wb.remove(_sheet_titled(wb, "BÖLGESEL PUANT RAPORU"))

    with pytest.raises(EnergyDataError) as exc:
        parse_daily_report(_edited_excel(edit), expected_day=date(2026, 10, 1))
    assert exc.value.code == ErrorCode.PARSING_ERROR
    assert "BÖLGESEL PUANT RAPORU" in exc.value.message


def test_daily_report_empty_table_is_an_explicit_error():
    def edit(wb):
        regional = _sheet_titled(wb, "BÖLGESEL PUANT RAPORU")
        header = _cell_with(regional, "BÖLGE ADI").row
        regional.delete_rows(header + 1, regional.max_row)

    with pytest.raises(EnergyDataError) as exc:
        parse_daily_report(_edited_excel(edit), expected_day=date(2026, 10, 1))
    assert exc.value.code == ErrorCode.PARSING_ERROR
    assert exc.value.details["tables"] == ["bolgesel_puant"]


@pytest.mark.parametrize(
    ("fragment", "old", "new"),
    [
        ("TÜKETİMİNİN SAATLİK GELİŞİMİ", "03:00", "ab:00"),  # saat etiketi int değil
        ("TÜKETİMİNİN SAATLİK GELİŞİMİ", 11596.538, "yok"),  # sayı değil
        ("GÜNLÜK TÜRKİYE PUANT RAPORU", "13.08.2026", "2026-08-13"),  # tarih biçimi
    ],
)
def test_daily_report_bad_cell_becomes_parsing_error(fragment, old, new):
    def edit(wb):
        _cell_with(_sheet_titled(wb, fragment), old).value = new

    with pytest.raises(EnergyDataError) as exc:
        parse_daily_report(_edited_excel(edit), expected_day=date(2026, 10, 1))
    assert exc.value.code == ErrorCode.PARSING_ERROR


def test_day_series_malformed_json_becomes_parsing_error():
    broken = AJAX.replace('"kod"', '"kodu"')
    with pytest.raises(EnergyDataError) as exc:
        parse_day_series(broken, date(2026, 10, 18))
    assert exc.value.code == ErrorCode.PARSING_ERROR


class _FakeClient:
    """Ağsız sahte YTBS istemcisi: her gün için tek 15 dk satırı ve boş Excel döndürür."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def fetch_series(self, day):
        return [{"tarih": day.isoformat(), "zaman": f"{day} 00:15", "yuk_mw": 1.0}]

    def fetch_daily_excel(self, day):
        return day.isoformat().encode()


def _fake_tables(day_label):
    from turkiye_energy_mcp.ytbs.parsers import EXCEL_TABLES

    return {t: [{"tarih": day_label, "deger": 1.0}] for t in EXCEL_TABLES}


def _run_fake_sync(tmp_path, monkeypatch, parse):
    from turkiye_energy_mcp.ytbs import cli

    monkeypatch.setattr(cli, "YtbsClient", _FakeClient)
    monkeypatch.setattr(cli, "parse_daily_report", parse)
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 10))
    args = ["--baslangic", "2026-10-01", "--bitis", "2026-10-03"]
    return cli.main([*args, "--veri-klasoru", str(tmp_path), "--bekleme", "0"])


def test_sync_continues_after_unexpected_parser_error(tmp_path, monkeypatch):
    def parse(content, expected_day):
        if expected_day == date(2026, 10, 2):
            raise KeyError("beklenmeyen")
        return _fake_tables(expected_day.isoformat())

    assert _run_fake_sync(tmp_path, monkeypatch, parse) == 1
    store = CsvStore(tmp_path)
    manifest = store.load_manifest()
    assert set(manifest["gunler"]) == {"2026-10-01", "2026-10-03"}
    assert "KeyError" in manifest["son_hatalar"]["2026-10-02"]
    rows = store.read("puant", date(2026, 10, 1), date(2026, 10, 31))
    assert [r["tarih"] for r in rows] == ["2026-10-01", "2026-10-03"]


def test_sync_records_manifest_even_when_interrupted(tmp_path, monkeypatch):
    def parse(content, expected_day):
        if expected_day == date(2026, 10, 2):
            raise KeyboardInterrupt
        return _fake_tables(expected_day.isoformat())

    with pytest.raises(KeyboardInterrupt):
        _run_fake_sync(tmp_path, monkeypatch, parse)
    assert set(CsvStore(tmp_path).load_manifest()["gunler"]) == {"2026-10-01"}
