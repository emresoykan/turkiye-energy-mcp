"""YTBS MCP tool'larının servis katmanı (CSV deposundan okur)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from ..exceptions import EnergyDataError, ErrorCode
from ..models import ISTANBUL, dataset_response
from ..parsers.common import parse_date
from . import YTBS_DATA_NOTE, YTBS_PAGE_URL
from .parsers import SOURCE, source_code
from .repository import YtbsRepository
from .store import SERIES_TABLE

TEXT_FIELDS = {
    "tarih",
    "zaman",
    "saat",
    "zaman_baslangic",
    "kurulus",
    "kaynak",
    "kalem",
    "birim",
    "donem",
    "olay_tarihi",
    "olay_saati",
    "bolge",
    "puant_gunu",
    "puant_zamani",
    "cekim_zamani",
}
ENGLISH_SOURCES = {
    "wind": "ruzgar",
    "solar": "gunes",
    "natural_gas": "dogalgaz",
    "naturalgas": "dogalgaz",
    "gas": "dogalgaz",
    "lignite": "linyit",
    "imported_coal": "ithalkomur",
    "importedcoal": "ithalkomur",
    "hard_coal": "taskomur",
    "geothermal": "jeotermal",
    "biomass": "biyokutle",
    "river": "akarsu",
    "dam": "barajli",
    "nuclear": "nukleer",
}
SERIES_FORMAT = "Sayfaya gömülü JSON (ytbs-sync CSV deposu)"
REPORT_FORMAT = "Günlük XLSX rapor (ytbs-sync CSV deposu)"


def _typed(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in TEXT_FIELDS:
            out[key] = value or None
        elif value in ("", None):
            out[key] = None
        else:
            try:
                out[key] = float(value)
            except ValueError:
                out[key] = value
    return out


def _source_key(value: str) -> str:
    key = source_code(value)
    return ENGLISH_SOURCES.get(value.strip().lower(), ENGLISH_SOURCES.get(key, key))


def _date_range(start_date: str, end_date: str, max_days: int) -> tuple[date, date]:
    start, end = parse_date(start_date), parse_date(end_date)
    if start > end:
        raise EnergyDataError(
            ErrorCode.INVALID_PARAMETER, "Başlangıç tarihi bitiş tarihinden büyük olamaz."
        )
    if (end - start).days + 1 > max_days:
        raise EnergyDataError(
            ErrorCode.INVALID_PARAMETER,
            f"Bu veri seti için en fazla {max_days} günlük aralık istenebilir.",
        )
    return start, end


class YtbsService:
    def __init__(self, repository: YtbsRepository) -> None:
        self.repo = repository

    async def _response(
        self,
        *,
        dataset: str,
        data: list[dict[str, Any]],
        start: date,
        end: date,
        unit: str,
        frequency: str,
        source_format: str,
        notes: str | None = None,
    ) -> dict[str, Any]:
        if not data:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "YTBS deposunda istenen aralık için veri yok.",
                source=SOURCE,
                details={"start_date": start.isoformat(), "end_date": end.isoformat()},
            )
        manifest = await self.repo.manifest()
        days = sorted({r["tarih"] for r in data})
        wanted = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}
        return dataset_response(
            source=SOURCE,
            dataset=dataset,
            data=data,
            source_url=YTBS_PAGE_URL,
            source_format=source_format,
            frequency=frequency,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            unit=unit,
            notes=f"{YTBS_DATA_NOTE} {notes}" if notes else YTBS_DATA_NOTE,
            latest_available_period=days[-1],
            data_freshness=f"Son senkron: {manifest.get('son_senkron')}",
            provisional=True,
            missing_days=sorted(wanted - set(days)),
            storage_backend=self.repo.backend,
        )

    async def get_load_generation_15min(
        self, start_date: str, end_date: str, sources: list[str] | None = None
    ) -> dict[str, Any]:
        start, end = _date_range(start_date, end_date, max_days=7)
        rows = [_typed(r) for r in await self.repo.read(SERIES_TABLE, start, end)]
        if sources:
            keep = {"tarih", "zaman", "yuk_mw", "toplam_uretim_mw", "cekim_zamani"}
            keep |= {f"{_source_key(s)}_mw" for s in sources}
            rows = [{k: v for k, v in r.items() if k in keep} for r in rows]
        return await self._response(
            dataset="ytbs_15dk_yuk_uretim",
            data=rows,
            start=start,
            end=end,
            unit="MW",
            frequency="15 dakika",
            source_format=SERIES_FORMAT,
            notes=(
                "yuk_mw anlık talep değeridir (günlük maksimumu 'maksimum anlık talep' ile aynı); "
                "MWh'e çevrilmemelidir. Kaynak bazlı üretim lisanssız üretimi içerir. "
                "Yayımlanmamış dilimler null'dır."
            ),
        )

    async def get_hourly_balance(self, start_date: str, end_date: str) -> dict[str, Any]:
        start, end = _date_range(start_date, end_date, max_days=92)
        rows = [_typed(r) for r in await self.repo.read("saatlik_denge", start, end)]
        return await self._response(
            dataset="ytbs_saatlik_enerji_dengesi",
            data=rows,
            start=start,
            end=end,
            unit="MWh",
            frequency="saatlik",
            source_format=REPORT_FORMAT,
            notes=(
                "saat etiketi saat-sonudur; zaman_baslangic bu varsayımla türetilmiştir. "
                "Üretim + ithalat - ihracat + depolama deşarj - şarj = tüketim. Yenilenebilir: "
                "akarsu, barajlı, biyokütle, güneş, jeotermal, rüzgar (atık ısı hariç)."
            ),
        )

    async def get_daily_generation(
        self, start_date: str, end_date: str, breakdown: str = "kaynak", source: str | None = None
    ) -> dict[str, Any]:
        start, end = _date_range(start_date, end_date, max_days=366)
        if breakdown not in ("kaynak", "kurulus"):
            raise EnergyDataError(
                ErrorCode.INVALID_PARAMETER, "breakdown 'kaynak' veya 'kurulus' olmalıdır."
            )
        rows = [_typed(r) for r in await self.repo.read(f"gunluk_uretim_{breakdown}", start, end)]
        if source and breakdown == "kaynak":
            key = _source_key(source)
            rows = [r for r in rows if r["kaynak"] == key]
        return await self._response(
            dataset=f"ytbs_gunluk_uretim_{breakdown}",
            data=rows,
            start=start,
            end=end,
            unit="MWh",
            frequency="günlük",
            source_format=REPORT_FORMAT,
            notes="Lisanssız üretim dahildir; ay sonuna kadar tahminidir.",
        )

    async def get_installed_capacity(
        self, on_date: str | None = None, breakdown: str = "kaynak"
    ) -> dict[str, Any]:
        if breakdown not in ("kaynak", "kurulus", "detay"):
            raise EnergyDataError(
                ErrorCode.INVALID_PARAMETER, "breakdown 'kaynak', 'kurulus' veya 'detay' olmalıdır."
            )
        end = parse_date(on_date) if on_date else datetime.now(ISTANBUL).date()
        start = end - timedelta(days=31)
        capacity = await self.repo.read("kurulu_guc", start, end)
        if not capacity:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "Son 31 gün içinde YTBS kurulu güç verisi yok.",
                source=SOURCE,
                details={"on_date": end.isoformat()},
            )
        day = max(r["tarih"] for r in capacity)
        counts = {
            (r["kurulus"], r["kaynak"]): float(r["santral_sayisi"] or 0)
            for r in await self.repo.read(
                "santral_sayisi", date.fromisoformat(day), date.fromisoformat(day)
            )
        }
        grouped: dict[tuple[str, ...], dict[str, float]] = defaultdict(
            lambda: {"kurulu_guc_mw": 0.0, "santral_sayisi": 0.0}
        )
        for r in capacity:
            if r["tarih"] != day:
                continue
            key = {
                "kaynak": (r["kaynak"],),
                "kurulus": (r["kurulus"],),
                "detay": (r["kurulus"], r["kaynak"]),
            }[breakdown]
            grouped[key]["kurulu_guc_mw"] += float(r["kurulu_guc_mw"] or 0)
            grouped[key]["santral_sayisi"] += counts.get((r["kurulus"], r["kaynak"]), 0.0)
        names = {"kaynak": ("kaynak",), "kurulus": ("kurulus",), "detay": ("kurulus", "kaynak")}
        data = [
            {
                "tarih": day,
                **dict(zip(names[breakdown], key)),
                "kurulu_guc_mw": round(v["kurulu_guc_mw"], 6),
                "santral_sayisi": int(v["santral_sayisi"]),
            }
            for key, v in sorted(grouped.items())
        ]
        snapshot = date.fromisoformat(day)
        return await self._response(
            dataset=f"ytbs_kurulu_guc_{breakdown}",
            data=data,
            start=snapshot,
            end=snapshot,
            unit="MW",
            frequency="günlük anlık görüntü",
            source_format=REPORT_FORMAT,
            notes=(
                "İstenen tarihe en yakın (önceki) mevcut günün anlık görüntüsüdür. Lisanssız "
                "santraller 'Lisanssız Santrallar' kuruluşu altındadır."
            ),
        )

    async def get_available_capacity(
        self, start_date: str, end_date: str, items: list[str] | None = None
    ) -> dict[str, Any]:
        start, end = _date_range(start_date, end_date, max_days=31)
        rows = [_typed(r) for r in await self.repo.read("emre_amade", start, end)]
        if items:
            keys = {_source_key(i) for i in items}
            rows = [r for r in rows if r["kalem"] in keys]
        return await self._response(
            dataset="ytbs_emre_amade_kapasite",
            data=rows,
            start=start,
            end=end,
            unit="MWh / %",
            frequency="saatlik",
            source_format=REPORT_FORMAT,
            notes=(
                "kalem: kaynak kodu, 'toplam', 'taleptahmin' (TEİAŞ talep tahmini), "
                "'emreamadelikorani' ve 'arzguvenligiorani' (%). Birim her satırın 'birim' alanındadır."
            ),
        )

    async def get_peaks(
        self, start_date: str, end_date: str, scope: str = "ulusal"
    ) -> dict[str, Any]:
        start, end = _date_range(start_date, end_date, max_days=92)
        if scope not in ("ulusal", "bolgesel"):
            raise EnergyDataError(
                ErrorCode.INVALID_PARAMETER, "scope 'ulusal' veya 'bolgesel' olmalıdır."
            )
        table = "puant" if scope == "ulusal" else "bolgesel_puant"
        rows = [_typed(r) for r in await self.repo.read(table, start, end)]
        return await self._response(
            dataset=f"ytbs_{table}",
            data=rows,
            start=start,
            end=end,
            unit="MW / MWh",
            frequency="günlük",
            source_format=REPORT_FORMAT,
            notes=(
                "Ulusal tabloda donem GÜNLÜK/AYLIK/YILLIK rekorları, olay_tarihi/olay_saati "
                "rekorun gerçekleştiği anı verir. Bölgesel puant 9 Yük Tevzi bölgesi içindir."
            ),
        )

    async def get_sync_status(self) -> dict[str, Any]:
        manifest = await self.repo.manifest()
        days = sorted(manifest.get("gunler", {}))
        return {
            "source": SOURCE,
            "storage_backend": self.repo.backend,
            "son_senkron": manifest.get("son_senkron"),
            "gun_sayisi": len(days),
            "ilk_gun": days[0] if days else None,
            "son_gun": days[-1] if days else None,
            "son_hatalar": manifest.get("son_hatalar", {}),
            "notes": YTBS_DATA_NOTE,
        }
