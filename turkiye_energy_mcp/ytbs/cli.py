"""`ytbs-sync`: YTBS verisini gün gün çekip CSV deposuna yazar.

Türkiye IP'si olan bir makinede çalıştırılmalıdır. Varsayılan olarak bugün dahil son 7 gün
yeniden çekilir; böylece geriye dönük revizyonlar ve kaçırılan günler kendiliğinden düzelir.

Örnekler:
    ytbs-sync
    ytbs-sync --gun 30
    ytbs-sync --baslangic 2026-01-01 --bitis 2026-03-31 --veri-klasoru D:\\ytbs-veri
"""

from __future__ import annotations

import argparse
import logging
import os
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
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args(argv)


def _day_range(args: argparse.Namespace) -> list[date]:
    end = args.bitis or datetime.now(ISTANBUL).date()
    start = args.baslangic or end - timedelta(days=max(args.gun, 1) - 1)
    start = max(start, EARLIEST_DAY)
    if start > end:
        raise SystemExit("Başlangıç, bitişten sonra olamaz.")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    store = CsvStore(args.veri_klasoru)
    days = _day_range(args)
    synced: list[str] = []
    failed: dict[str, str] = {}

    with YtbsClient() as client:
        for i, day in enumerate(days):
            label = day.isoformat()
            try:
                result = client.fetch_day(day, with_excel=not args.excel_yok)
                store.upsert(SERIES_TABLE, result.series, result.fetched_at)
                summary = f"{sum(r['yuk_mw'] is not None for r in result.series)}/96 nokta"
                if result.excel is not None:
                    tables = parse_daily_report(result.excel, expected_day=day)
                    for table, rows in tables.items():
                        store.upsert(table, rows, result.fetched_at)
                    gap = balance_gap_mwh(tables["saatlik_denge"])
                    if gap is not None and gap > BALANCE_TOLERANCE_MWH:
                        print(
                            f"UYARI {label}: saatlik enerji dengesi farkı {gap:.2f} MWh",
                            file=sys.stderr,
                        )
                    summary += f" | {len(tables['saatlik_denge'])} saatlik denge satırı"
                synced.append(label)
                print(f"{label}: {summary}")
            except EnergyDataError as exc:
                failed[label] = f"{exc.code.value}: {exc.message}"
                print(f"HATA {label}: {exc.message}", file=sys.stderr)
            if i < len(days) - 1:
                time.sleep(args.bekleme)

    store.record_sync(synced=synced, failed=failed, finished_at=datetime.now(ISTANBUL))
    print(f"Depo: {store.root} | başarılı {len(synced)}, hatalı {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
