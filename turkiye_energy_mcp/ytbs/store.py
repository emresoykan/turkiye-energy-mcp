"""YTBS verileri için aylık bölümlenmiş CSV deposu.

Yerleşim: <kök>/<tablo>/<YYYY>/<YYYY-MM>.csv ve <kök>/manifest.json.
Her satır `tarih` (rapor günü) ve `cekim_zamani` taşır. Bir gün yeniden çekildiğinde o günün
eski satırları silinip yenileri yazılır (YTBS verileri geriye dönük revize olur).
"""

from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .parsers import EXCEL_TABLES

SERIES_TABLE = "15dk_yuk_uretim"
TABLES = (SERIES_TABLE, *EXCEL_TABLES)
MANIFEST = "manifest.json"


def _month_file(root: Path, table: str, day: str) -> Path:
    return root / table / day[:4] / f"{day[:7]}.csv"


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(k for r in rows for k in r))
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: "" if r.get(k) is None else r.get(k) for k in fields})
    os.replace(tmp, path)


class CsvStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def upsert(self, table: str, rows: Iterable[dict[str, Any]], fetched_at: datetime) -> int:
        """`rows` içindeki günlerin eski kayıtlarını yenileriyle değiştirir; yazılan satır sayısı."""
        stamp = fetched_at.isoformat(timespec="seconds")
        by_month: dict[Path, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            by_month[_month_file(self.root, table, r["tarih"])].append(r | {"cekim_zamani": stamp})
        written = 0
        for path, new_rows in by_month.items():
            days = {r["tarih"] for r in new_rows}
            kept = [r for r in _read(path) if r.get("tarih") not in days]
            merged = sorted(kept + new_rows, key=lambda r: r["tarih"])
            _write_atomic(path, merged)
            written += len(new_rows)
        return written

    def read(self, table: str, start: date, end: date) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        month = date(start.year, start.month, 1)
        while month <= end:
            for r in _read(_month_file(self.root, table, month.isoformat())):
                if start.isoformat() <= r.get("tarih", "") <= end.isoformat():
                    rows.append(r)
            month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
        return rows

    def load_manifest(self) -> dict[str, Any]:
        path = self.root / MANIFEST
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def record_sync(
        self, *, synced: list[str], failed: dict[str, str], finished_at: datetime
    ) -> None:
        manifest = self.load_manifest()
        days = manifest.setdefault("gunler", {})
        stamp = finished_at.isoformat(timespec="seconds")
        for d in synced:
            days[d] = stamp
        manifest["son_senkron"] = stamp
        manifest["son_hatalar"] = failed
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / MANIFEST
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
        os.replace(tmp, path)
