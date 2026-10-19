"""`ytbs-sync`: YTBS verisini gün gün çekip CSV deposuna yazar.

Türkiye IP'si olan bir makinede çalıştırılmalıdır. Varsayılan olarak bugün dahil son 7 gün
yeniden çekilir; böylece geriye dönük revizyonlar ve kaçırılan günler kendiliğinden düzelir.

Örnekler:
    ytbs-sync
    ytbs-sync --gun 30
    ytbs-sync --baslangic 2026-01-01 --bitis 2026-03-31 --veri-klasoru D:\\ytbs-veri
    pythonw -m turkiye_energy_mcp.ytbs.cli --git-push --log C:\\ytbs-sync.log  # zamanlanmış görev
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from ..exceptions import EnergyDataError
from ..models import ISTANBUL
from .client import YtbsClient
from .parsers import balance_gap_mwh, parse_daily_report
from .store import SERIES_TABLE, CsvStore

DEFAULT_DATA_DIR = Path.home() / "ytbs-veri"
EARLIEST_DAY = date(2016, 1, 1)  # sayfadaki takvimin alt sınırı
BALANCE_TOLERANCE_MWH = 1.0
_log_path: Path | None = None


def _say(message: str, *, error: bool = False) -> None:
    """Konsola (varsa) ve --log dosyasına UTF-8 yazar. Uyarılar stdout'a gider: Windows
    PowerShell 5.1 stderr'e yazılan her satırı hata sayıp görevi başarısız gösteriyor."""
    stream = sys.stderr if error else sys.stdout
    if stream is not None:  # pythonw altında konsol yoktur
        print(message, file=stream)
    if _log_path is not None:
        stamp = datetime.now(ISTANBUL).strftime("%Y-%m-%d %H:%M:%S")
        with _log_path.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {message}\n")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="ytbs-sync", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--gun", type=int, default=7, help="Bitişten geriye kaç gün (varsayılan 7)")
    ap.add_argument("--baslangic", type=date.fromisoformat, help="YYYY-MM-DD")
    ap.add_argument("--bitis", type=date.fromisoformat, help="YYYY-MM-DD (varsayılan bugün)")
    ap.add_argument(
        "--veri-klasoru",
        default=os.environ.get("YTBS_DATA_DIR", str(DEFAULT_DATA_DIR)),
        help="CSV deposu (varsayılan: YTBS_DATA_DIR veya ~/ytbs-veri)",
    )
    ap.add_argument("--excel-yok", action="store_true", help="Günlük Excel raporunu çekme")
    ap.add_argument("--bekleme", type=float, default=2.0, help="Günler arası bekleme (sn)")
    ap.add_argument(
        "--git-push",
        action="store_true",
        help="Veri klasörü bir git deposuysa değişiklikleri commit edip push et",
    )
    ap.add_argument("--log", type=Path, help="Çıktıyı bu dosyaya da UTF-8 olarak ekle")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args(argv)


def _today() -> date:
    return datetime.now(ISTANBUL).date()


def _day_range(args: argparse.Namespace) -> list[date]:
    end = args.bitis or _today()
    start = args.baslangic or end - timedelta(days=max(args.gun, 1) - 1)
    start = max(start, EARLIEST_DAY)
    if start > end:
        raise SystemExit("Başlangıç, bitişten sonra olamaz.")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def main(argv: list[str] | None = None) -> int:
    global _log_path
    args = _parse_args(argv)
    _log_path = args.log
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    store = CsvStore(args.veri_klasoru)
    days = _day_range(args)
    synced: list[str] = []
    failed: dict[str, str] = {}
    today = _today()

    with YtbsClient() as client:
        for i, day in enumerate(days):
            label = day.isoformat()
            try:
                series = client.fetch_series(day)
                fetched_at = datetime.now(ISTANBUL)
                store.upsert(SERIES_TABLE, series, fetched_at)
                summary = f"{sum(r['yuk_mw'] is not None for r in series)}/96 nokta"
                # Günlük rapor gün bitmeden yayımlanmıyor (XLSX yerine başka içerik döner);
                # bugünün raporu bir sonraki senkronda alınır.
                if not args.excel_yok and day >= today:
                    summary += " | günlük rapor gün bitince alınacak"
                elif not args.excel_yok:
                    excel = client.fetch_daily_excel(day)
                    tables = parse_daily_report(excel, expected_day=day)
                    for table, rows in tables.items():
                        store.upsert(table, rows, fetched_at)
                    gap = balance_gap_mwh(tables["saatlik_denge"])
                    if gap is not None and gap > BALANCE_TOLERANCE_MWH:
                        _say(f"UYARI {label}: saatlik enerji dengesi farkı {gap:.2f} MWh")
                    summary += f" | {len(tables['saatlik_denge'])} saatlik denge satırı"
                synced.append(label)
                _say(f"{label}: {summary}")
            except EnergyDataError as exc:
                failed[label] = f"{exc.code.value}: {exc.message}"
                _say(f"HATA {label}: {exc.message}", error=True)
            if i < len(days) - 1:
                time.sleep(args.bekleme)

    store.record_sync(synced=synced, failed=failed, finished_at=datetime.now(ISTANBUL))
    _say(f"Depo: {store.root} | başarılı {len(synced)}, hatalı {len(failed)}")
    pushed = _git_push(store.root, days) if args.git_push else True
    return 1 if failed or not pushed else 0


def _git_push(root: Path, days: list[date]) -> bool:
    """Veri klasöründeki değişiklikleri commit/push eder. Kimlik doğrulama makinenin git
    yapılandırmasına (ör. Git Credential Manager) bırakılır; token saklanmaz."""

    def git(*cmd: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *cmd],
            check=False,
            capture_output=True,
            text=True,
            # pythonw altında git için konsol penceresi açılmasın (yalnız Windows'ta tanımlı)
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    try:
        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            _say(f"HATA: {root} bir git deposu değil; --git-push atlandı.", error=True)
            return False
    except FileNotFoundError:
        _say("HATA: git bulunamadı; --git-push atlandı.", error=True)
        return False
    git("add", "-A")
    if git("diff", "--cached", "--quiet").returncode == 0:
        _say("Git: değişiklik yok.")
        return True
    message = f"ytbs-sync {days[0].isoformat()}..{days[-1].isoformat()}"
    for step in (("commit", "-q", "-m", message), ("push", "-q")):
        result = git(*step)
        if result.returncode != 0:
            _say(f"HATA: git {step[0]}: {result.stderr.strip()}", error=True)
            return False
    _say(f"Git: push edildi ({message}).")
    return True


if __name__ == "__main__":
    sys.exit(main())
