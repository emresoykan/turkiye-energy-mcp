from collections import Counter
from typing import Any

from .clients.teias import TeiasClient, Workbook
from .exceptions import EnergyDataError, ErrorCode
from .freshness import classify_annual_freshness, classify_monthly_freshness
from .models import dataset_response
from .parsers.common import normalize_key, normalize_plant_name, parse_date, parse_year_range
from .parsers.workbooks import (
    parse_capacity_by_organization,
    parse_capacity_mix,
    parse_energy_balance,
    parse_euas_generation_by_source,
    parse_euas_hydro_plants,
    parse_euas_thermal_plants,
    parse_generation_by_organization,
    parse_generation_mix,
    parse_monthly_energy,
    parse_monthly_euas_generation,
    parse_peak_demand,
    parse_transformers,
    parse_transmission_lines,
)

ANNUAL_PAGE = "https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri"
MONTHLY_PAGE = "https://www.teias.gov.tr/aylik-elektrik-uretim-tuketim-raporlari"

SOURCE_NAMES = {
    "ruzgar": "wind",
    "wind": "wind",
    "gunes": "solar",
    "solar": "solar",
    "hidro": "hydro",
    "hidrolik": "hydro",
    "hidroelektrik": "hydro",
    "hes": "hydro",
    "hydro": "hydro",
    "jeotermal": "geothermal",
    "geothermal": "geothermal",
    "dogal gaz": "natural_gas",
    "dogalgaz": "natural_gas",
    "natural gas": "natural_gas",
    "linyit": "lignite",
    "lignite": "lignite",
    "ithal komur": "imported_coal",
    "tas komuru": "hard_coal",
    "taskomuru": "hard_coal",
    "komur": "coal_total",
    "termik": "thermal",
    "thermal": "thermal",
    "biyokutle": "waste_and_biomass",
    "atik": "waste_and_biomass",
    "toplam": "total",
    "total": "total",
}

COUNTRY_NAMES = {
    "bulgaristan": "Bulgaria",
    "bulgaria": "Bulgaria",
    "yunanistan": "Greece",
    "greece": "Greece",
    "gurcistan": "Georgia",
    "georgia": "Georgia",
    "azerbaycan": "Azerbaijan",
    "azerbaijan": "Azerbaijan",
    "iran": "Iran",
    "irak": "Iraq",
    "iraq": "Iraq",
    "suriye": "Syria",
    "syria": "Syria",
}


def _canonical_source(value: str | None) -> str | None:
    if value is None:
        return None
    key = normalize_key(value)
    return SOURCE_NAMES.get(key, key.replace(" ", "_"))


def _filter_years(
    records: list[dict[str, Any]],
    start_year: int,
    end_year: int,
) -> list[dict[str, Any]]:
    return [record for record in records if start_year <= int(record["year"]) <= end_year]


def _require_data(
    data: list[dict[str, Any]],
    message: str,
    *,
    latest_available_period: str | None = None,
    requested_period: str | None = None,
) -> list[dict[str, Any]]:
    if not data:
        raise EnergyDataError(
            ErrorCode.DATA_NOT_AVAILABLE,
            message,
            source="TEİAŞ",
            details={
                "latest_available_period": latest_available_period,
                "data_freshness": "unavailable",
                "requested_period": requested_period,
            },
        )
    return data


def _workbook_meta(*workbooks: Workbook) -> dict[str, Any]:
    periods = [
        workbook.latest_available_period
        for workbook in workbooks
        if workbook.latest_available_period
    ]
    publications = [workbook.published_at for workbook in workbooks if workbook.published_at]
    names = [workbook.name for workbook in workbooks]
    return {
        "latest_available_period": max(periods) if periods else None,
        "publication_date": max(publications) if publications else None,
        "selected_source_name": " | ".join(names) if names else None,
    }


def _project_generation_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize only records remaining after caller filters."""
    dropped = [
        record for record in records if record.get("_project_generation_dropped")
    ]
    reasons: Counter[str] = Counter()
    for record in dropped:
        reasons.update(record.get("_project_generation_drop_reasons") or [])
    return {
        "dropped_project_generation_rows": len(dropped),
        "reason": (
            "Project-generation fields are nulled for returned rows when source "
            "mapping is unverifiable, capacity ceiling is exceeded, or project "
            "and gross generation differ by more than 3x."
        ),
        "reason_counts": dict(sorted(reasons.items())),
    }


def _public_plant_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if not key.startswith("_") and key != "name_key"
    }


def _ensure_annual_overlap(
    *,
    requested_start: int,
    requested_end: int,
    latest_available_year: int,
    series_years: list[int],
) -> None:
    if not series_years:
        raise EnergyDataError(
            ErrorCode.DATA_NOT_AVAILABLE,
            "Seçilen resmi kaynakta yıllara göre veri bulunamadı.",
            source="TEİAŞ",
            details={
                "latest_available_period": str(latest_available_year),
                "data_freshness": "unavailable",
                "requested_period": f"{requested_start}-{requested_end}",
            },
        )
    series_start, series_end = min(series_years), max(series_years)
    if requested_start > series_end or requested_end < series_start:
        raise EnergyDataError(
            ErrorCode.DATA_NOT_AVAILABLE,
            "İstenen dönem, en güncel resmi serinin kapsadığı yılların dışında.",
            source="TEİAŞ",
            details={
                "latest_available_period": str(latest_available_year),
                "data_freshness": "unavailable",
                "requested_period": f"{requested_start}-{requested_end}",
                "series_period": f"{series_start}-{series_end}",
            },
        )


class EnergyService:
    def __init__(self, teias: TeiasClient) -> None:
        self.teias = teias

    async def _capacity_mix(self) -> tuple[list[dict[str, Any]], Workbook]:
        workbook = await self.teias.annual_workbook("capacity_mix")
        return parse_capacity_mix(workbook.content), workbook

    async def _generation_mix(self) -> tuple[list[dict[str, Any]], Workbook]:
        workbook = await self.teias.annual_workbook("generation_mix")
        return parse_generation_mix(workbook.content), workbook

    async def _energy_balance(self) -> tuple[list[dict[str, Any]], Workbook]:
        workbook = await self.teias.annual_workbook("energy_balance")
        return parse_energy_balance(workbook.content), workbook

    def _annual_response(
        self,
        *,
        dataset: str,
        data: list[dict[str, Any]],
        workbooks: list[Workbook],
        requested_start: int,
        requested_end: int,
        source_url: str | None = None,
        source_format: str | None = None,
        unit: str | None = None,
        notes: str | None = None,
        original_unit: str | None = None,
        subject: str | None = None,
        extra_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        meta = _workbook_meta(*workbooks)
        latest_year = int(meta["latest_available_period"] or max(item["year"] for item in data))
        freshness = classify_annual_freshness(
            requested_start=requested_start,
            requested_end=requested_end,
            latest_available_year=latest_year,
            returned_years=[int(item["year"]) for item in data],
        )
        payload = dataset_response(
            source="TEİAŞ",
            subject=subject,
            dataset=dataset,
            data=data,
            source_url=source_url or workbooks[0].source_url,
            source_format=source_format
            or "/".join(sorted({workbook.source_format for workbook in workbooks})),
            frequency="annual",
            start_date=str(requested_start),
            end_date=str(requested_end),
            unit=unit,
            original_unit=original_unit,
            notes=notes,
            latest_available_period=meta["latest_available_period"],
            data_freshness=freshness,
            publication_date=meta["publication_date"],
            selected_source_name=meta["selected_source_name"],
            **(extra_meta or {}),
        )
        return payload

    async def get_monthly_energy(
        self,
        start_date: str,
        end_date: str,
        metric: str | None = None,
    ) -> dict[str, Any]:
        start = parse_date(start_date)
        end = parse_date(end_date)
        if start > end:
            raise EnergyDataError(
                ErrorCode.INVALID_PARAMETER,
                "Başlangıç tarihi bitiş tarihinden büyük olamaz.",
            )

        metric_key = _canonical_source(metric)
        metric_aliases = {
            "tuketim": "gross_demand",
            "consumption": "gross_demand",
            "uretim": "total_generation",
            "generation": "total_generation",
            "ithalat": "imports",
            "ihracat": "exports",
        }
        metric_key = metric_aliases.get(normalize_key(metric or ""), metric_key)
        data: list[dict[str, Any]] = []
        workbooks: list[Workbook] = []
        for year in range(start.year, end.year + 1):
            workbook = await self.teias.monthly_workbook(year)
            workbooks.append(workbook)
            data.extend(parse_monthly_energy(workbook.content, year))

        start_month = start.strftime("%Y-%m")
        end_month = end.strftime("%Y-%m")
        filtered = [
            record
            for record in data
            if start_month <= record["date"] <= end_month
            and (metric_key is None or record["metric"] == metric_key)
        ]
        meta = _workbook_meta(*workbooks)
        latest_period = meta["latest_available_period"]
        if not filtered:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "Belirtilen dönem ve metrik için aylık veri bulunamadı.",
                source="TEİAŞ",
                details={
                    "latest_available_period": latest_period,
                    "data_freshness": "unavailable",
                    "requested_period": f"{start_month}/{end_month}",
                },
            )
        freshness = classify_monthly_freshness(
            requested_start=start,
            requested_end=end,
            latest_available_period=latest_period,
            returned_periods=[record["date"] for record in filtered],
        )
        return dataset_response(
            source="TEİAŞ",
            dataset="monthly_energy",
            data=filtered,
            source_url=MONTHLY_PAGE,
            source_format="/".join(sorted({workbook.source_format for workbook in workbooks})),
            frequency="monthly",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            unit="GWh",
            original_unit="GWh",
            notes="Cari yıl değerleri TEİAŞ tarafından geçici olarak işaretlenebilir.",
            latest_available_period=latest_period,
            data_freshness=freshness,
            publication_date=meta["publication_date"],
            selected_source_name=meta["selected_source_name"],
            input_sources=[workbook.source_url for workbook in workbooks],
        )

    async def get_generation(
        self,
        start_year: int,
        end_year: int,
        source: str | None = None,
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        records, workbook = await self._generation_mix()
        latest = workbook.period_end or max(item["year"] for item in records)
        _ensure_annual_overlap(
            requested_start=start_year,
            requested_end=end_year,
            latest_available_year=latest,
            series_years=[item["year"] for item in records],
        )
        source_key = _canonical_source(source)
        if source_key == "geothermal":
            source_key = "geothermal_and_wind"
        data = [
            record
            for record in _filter_years(records, start_year, end_year)
            if source_key is None or record["source"] == source_key
        ]
        _require_data(
            data,
            "Belirtilen yıllar veya kaynak için üretim verisi bulunamadı.",
            latest_available_period=str(latest),
            requested_period=f"{start_year}-{end_year}",
        )
        return self._annual_response(
            dataset="generation_by_source",
            data=data,
            workbooks=[workbook],
            requested_start=start_year,
            requested_end=end_year,
            unit="GWh",
            original_unit="GWh",
            notes=(
                "En güncel resmi tabloda jeotermal ve rüzgâr tek sütunda birleştirilmiş olabilir; "
                "kaynak='jeotermal' sorgusu birleşik seriyi döndürür."
            ),
        )

    async def get_installed_capacity(
        self,
        start_year: int,
        end_year: int,
        source: str | None = None,
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        records, workbook = await self._capacity_mix()
        latest = workbook.period_end or max(item["year"] for item in records)
        _ensure_annual_overlap(
            requested_start=start_year,
            requested_end=end_year,
            latest_available_year=latest,
            series_years=[item["year"] for item in records],
        )
        source_key = _canonical_source(source)
        data = [
            record
            for record in _filter_years(records, start_year, end_year)
            if source_key is None or record["source"] == source_key
        ]
        _require_data(
            data,
            "Belirtilen yıllar veya kaynak için kurulu güç verisi bulunamadı.",
            latest_available_period=str(latest),
            requested_period=f"{start_year}-{end_year}",
        )
        return self._annual_response(
            dataset="installed_capacity_by_source",
            data=data,
            workbooks=[workbook],
            requested_start=start_year,
            requested_end=end_year,
            unit="MW",
            original_unit="MW",
        )

    async def get_peak_demand(self, start_year: int, end_year: int) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        workbook = await self.teias.annual_workbook("peak_demand")
        records = parse_peak_demand(workbook.content)
        latest = workbook.period_end or max(item["year"] for item in records)
        _ensure_annual_overlap(
            requested_start=start_year,
            requested_end=end_year,
            latest_available_year=latest,
            series_years=[item["year"] for item in records],
        )
        data = _filter_years(records, start_year, end_year)
        _require_data(
            data,
            "Belirtilen yıllar için puant verisi bulunamadı.",
            latest_available_period=str(latest),
            requested_period=f"{start_year}-{end_year}",
        )
        return self._annual_response(
            dataset="peak_demand",
            data=data,
            workbooks=[workbook],
            requested_start=start_year,
            requested_end=end_year,
            notes="MW güç ve GWh enerji alanları ayrı adlandırılmıştır.",
        )

    async def get_import_export(
        self,
        start_year: int,
        end_year: int,
        country: str | None = None,
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        records, workbook = await self._energy_balance()
        latest = workbook.period_end or max(item["year"] for item in records)
        _ensure_annual_overlap(
            requested_start=start_year,
            requested_end=end_year,
            latest_available_year=latest,
            series_years=[item["year"] for item in records],
        )
        records = _filter_years(records, start_year, end_year)
        country_key = None
        if country:
            country_key = COUNTRY_NAMES.get(normalize_key(country))
            if not country_key:
                raise EnergyDataError(
                    ErrorCode.INVALID_PARAMETER,
                    "Ülke bu TEİAŞ tablosunda yer almıyor.",
                    source="TEİAŞ",
                )
        data: list[dict[str, Any]] = []
        for record in records:
            if country_key:
                imported = record["import_by_country_gwh"].get(country_key, 0.0)
                exported = record["export_by_country_gwh"].get(country_key, 0.0)
                data.append(
                    {
                        "year": record["year"],
                        "country": country_key,
                        "imports_gwh": imported,
                        "exports_gwh": exported,
                        "net_import_gwh": round(imported - exported, 6),
                    }
                )
            else:
                data.append(record)
        _require_data(
            data,
            "Belirtilen yıllar için ithalat/ihracat verisi bulunamadı.",
            latest_available_period=str(latest),
            requested_period=f"{start_year}-{end_year}",
        )
        return self._annual_response(
            dataset="import_export",
            data=data,
            workbooks=[workbook],
            requested_start=start_year,
            requested_end=end_year,
            unit="GWh",
            original_unit="GWh",
        )

    async def get_transmission_statistics(
        self, start_year: int, end_year: int
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        lines = await self.teias.annual_workbook("transmission_lines")
        transformers = await self.teias.annual_workbook("transformers")
        line_records = parse_transmission_lines(lines.content)
        transformer_records = parse_transformers(transformers.content)
        latest = max(
            value
            for value in (
                lines.period_end,
                transformers.period_end,
                max((item["year"] for item in line_records), default=None),
                max((item["year"] for item in transformer_records), default=None),
            )
            if value is not None
        )
        series_years = sorted(
            {item["year"] for item in line_records}
            | {item["year"] for item in transformer_records}
        )
        _ensure_annual_overlap(
            requested_start=start_year,
            requested_end=end_year,
            latest_available_year=latest,
            series_years=series_years,
        )
        line_data = {
            record["year"]: record
            for record in _filter_years(line_records, start_year, end_year)
        }
        transformer_data = {
            record["year"]: record
            for record in _filter_years(transformer_records, start_year, end_year)
        }
        data = [
            {**line_data.get(year, {}), **transformer_data.get(year, {}), "year": year}
            for year in sorted(set(line_data) | set(transformer_data))
        ]
        _require_data(
            data,
            "Belirtilen yıllar için iletim istatistiği bulunamadı.",
            latest_available_period=str(latest),
            requested_period=f"{start_year}-{end_year}",
        )
        return self._annual_response(
            dataset="transmission_statistics",
            data=data,
            workbooks=[lines, transformers],
            requested_start=start_year,
            requested_end=end_year,
            source_url=ANNUAL_PAGE,
            notes="Hat uzunlukları km, trafo kapasiteleri MVA'dır.",
            extra_meta={
                "input_sources": [lines.source_url, transformers.source_url],
            },
        )

    async def get_renewable_summary(
        self, start_year: int, end_year: int
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        capacity, capacity_workbook = await self._capacity_mix()
        generation, generation_workbook = await self._generation_mix()
        latest = max(
            value
            for value in (
                capacity_workbook.period_end,
                generation_workbook.period_end,
                max((item["year"] for item in capacity), default=None),
                max((item["year"] for item in generation), default=None),
            )
            if value is not None
        )
        _ensure_annual_overlap(
            requested_start=start_year,
            requested_end=end_year,
            latest_available_year=latest,
            series_years=sorted(
                {item["year"] for item in capacity} & {item["year"] for item in generation}
            ),
        )
        capacity_by_year: dict[int, dict[str, float]] = {}
        for item in _filter_years(capacity, start_year, end_year):
            capacity_by_year.setdefault(item["year"], {})[item["source"]] = item["capacity_mw"]
        generation_by_year: dict[int, dict[str, float]] = {}
        for item in _filter_years(generation, start_year, end_year):
            generation_by_year.setdefault(item["year"], {})[item["source"]] = item[
                "generation_gwh"
            ]
        data: list[dict[str, Any]] = []
        for year in sorted(set(capacity_by_year) & set(generation_by_year)):
            cap = capacity_by_year[year]
            gen = generation_by_year[year]
            renewable_capacity = sum(
                cap.get(source, 0.0) for source in ("hydro", "geothermal", "wind", "solar")
            )
            renewable_generation = (
                gen.get("hydro", 0.0)
                + gen.get("geothermal_and_wind", 0.0)
                + gen.get("solar", 0.0)
            )
            data.append(
                {
                    "year": year,
                    "renewable_capacity_mw": round(renewable_capacity, 6),
                    "renewable_capacity_share_percent": round(
                        renewable_capacity / cap["total"] * 100, 4
                    ),
                    "renewable_generation_gwh": round(renewable_generation, 6),
                    "renewable_generation_share_percent": round(
                        renewable_generation / gen["total"] * 100, 4
                    ),
                }
            )
        _require_data(
            data,
            "Belirtilen yıllar için yenilenebilir özeti hesaplanamadı.",
            latest_available_period=str(latest),
            requested_period=f"{start_year}-{end_year}",
        )
        return self._annual_response(
            dataset="renewable_summary",
            data=data,
            workbooks=[capacity_workbook, generation_workbook],
            requested_start=start_year,
            requested_end=end_year,
            source_url=ANNUAL_PAGE,
            notes="Paylar TEİAŞ kaynak ve toplam sütunlarından hesaplanmıştır.",
            extra_meta={
                "input_sources": [
                    capacity_workbook.source_url,
                    generation_workbook.source_url,
                ],
            },
        )

    async def get_system_summary(self, year: int) -> dict[str, Any]:
        parse_year_range(year, year)
        capacity, capacity_workbook = await self._capacity_mix()
        balance, balance_workbook = await self._energy_balance()
        peak_workbook = await self.teias.annual_workbook("peak_demand")
        peak_records = parse_peak_demand(peak_workbook.content)
        latest = max(
            value
            for value in (
                capacity_workbook.period_end,
                balance_workbook.period_end,
                peak_workbook.period_end,
            )
            if value is not None
        )
        capacity_total = next(
            (
                item["capacity_mw"]
                for item in capacity
                if item["year"] == year and item["source"] == "total"
            ),
            None,
        )
        balance_item = next((item for item in balance if item["year"] == year), None)
        peak_item = next((item for item in peak_records if item["year"] == year), None)
        if capacity_total is None or balance_item is None or peak_item is None:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "Belirtilen yıl için eksiksiz sistem özeti bulunamadı.",
                source="TEİAŞ",
                details={
                    "latest_available_period": str(latest),
                    "data_freshness": "unavailable",
                    "requested_period": str(year),
                },
            )
        data = [
            {
                "year": year,
                "installed_capacity_mw": capacity_total,
                "generation_gwh": balance_item["generation_gwh"],
                "gross_demand_gwh": balance_item["gross_demand_gwh"],
                "imports_gwh": balance_item["imports_gwh"],
                "exports_gwh": balance_item["exports_gwh"],
                "net_import_gwh": balance_item["net_import_gwh"],
                "instantaneous_peak_mw": peak_item["instantaneous_peak_mw"],
                "hourly_peak_mw": peak_item["hourly_peak_mw"],
            }
        ]
        return self._annual_response(
            dataset="system_summary",
            data=data,
            workbooks=[capacity_workbook, balance_workbook, peak_workbook],
            requested_start=year,
            requested_end=year,
            source_url=ANNUAL_PAGE,
            source_format="xls/xlsx",
            extra_meta={
                "input_sources": [
                    capacity_workbook.source_url,
                    balance_workbook.source_url,
                    peak_workbook.source_url,
                ],
            },
        )

    async def _euas_plants(
        self,
    ) -> tuple[list[dict[str, Any]], list[Workbook]]:
        thermal_books = await self.teias.annual_workbooks("euas_thermal_plants")
        hydro_books = await self.teias.annual_workbooks("euas_hydro_plants")
        workbooks = thermal_books + hydro_books
        reference_year = max(
            (workbook.period_end for workbook in workbooks if workbook.period_end),
            default=None,
        )
        records: list[dict[str, Any]] = []
        seen: set[tuple[str | None, str | None]] = set()
        for workbook in thermal_books:
            for record in parse_euas_thermal_plants(
                workbook.content, reference_year=reference_year
            ):
                key = (
                    normalize_plant_name(record.get("name")),
                    record.get("plant_type"),
                )
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
        for workbook in hydro_books:
            for record in parse_euas_hydro_plants(
                workbook.content, reference_year=reference_year
            ):
                key = (
                    record.get("name_key") or normalize_plant_name(record.get("name")),
                    record.get("plant_type"),
                )
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
        return records, workbooks

    async def get_euas_power_plants(
        self,
        plant_type: str | None = None,
        province: str | None = None,
        *,
        _name_query: str | None = None,
    ) -> dict[str, Any]:
        records, workbooks = await self._euas_plants()
        type_key = normalize_key(plant_type or "")
        type_aliases = {
            "hidro": "hydro",
            "hidrolik": "hydro",
            "hes": "hydro",
            "termik": "thermal",
        }
        type_key = type_aliases.get(type_key, type_key)
        province_key = normalize_key(province or "")
        name_key = normalize_key(_name_query or "")
        selected = [
            record
            for record in records
            if (not type_key or record["plant_type"] == type_key)
            and (not province_key or province_key in normalize_key(record["province"] or ""))
            and (not name_key or name_key in normalize_key(record["name"] or ""))
        ]
        data_quality = _project_generation_quality(selected)
        data = [_public_plant_record(record) for record in selected]
        meta = _workbook_meta(*workbooks)
        period = meta["latest_available_period"]
        _require_data(
            data,
            "Filtrelere uyan EÜAŞ santrali bulunamadı.",
            latest_available_period=period,
        )
        data.sort(key=lambda item: item.get("installed_capacity_mw") or 0, reverse=True)
        return dataset_response(
            source="TEİAŞ",
            subject="EÜAŞ",
            dataset="power_plants",
            data=data,
            source_url=ANNUAL_PAGE,
            source_format="xls",
            frequency="annual",
            start_date=period,
            end_date=period,
            unit=None,
            notes=(
                "TEİAŞ'ın en güncel EÜAŞ termik/hidrolik santral tabloları kullanılmıştır. "
                "Çalışma kitabındaki güvenilir olmayan ünite ve devreye giriş hücreleri yayımlanmaz."
            ),
            latest_available_period=period,
            data_freshness="current",
            publication_date=meta["publication_date"],
            selected_source_name=meta["selected_source_name"],
            input_sources=[workbook.source_url for workbook in workbooks],
            data_quality=data_quality,
        )

    async def get_euas_plant(self, plant_name: str) -> dict[str, Any]:
        if len(plant_name.strip()) < 2:
            raise EnergyDataError(
                ErrorCode.INVALID_PARAMETER, "Santral adı en az iki karakter olmalıdır."
            )
        result = await self.get_euas_power_plants(_name_query=plant_name)
        _require_data(
            result["data"],
            "EÜAŞ santral adı bulunamadı.",
            latest_available_period=result["metadata"].get("latest_available_period"),
        )
        result["dataset"] = "power_plant"
        return result

    async def get_euas_installed_capacity(
        self, start_year: int, end_year: int
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        workbook = await self.teias.annual_workbook("capacity_by_organization")
        records = parse_capacity_by_organization(workbook.content)
        latest = workbook.period_end or max(item["year"] for item in records)
        _ensure_annual_overlap(
            requested_start=start_year,
            requested_end=end_year,
            latest_available_year=latest,
            series_years=[item["year"] for item in records],
        )
        data = _filter_years(records, start_year, end_year)
        _require_data(
            data,
            "Belirtilen yıllar için EÜAŞ kurulu güç verisi bulunamadı.",
            latest_available_period=str(latest),
            requested_period=f"{start_year}-{end_year}",
        )
        return self._annual_response(
            dataset="installed_capacity",
            data=data,
            workbooks=[workbook],
            requested_start=start_year,
            requested_end=end_year,
            subject="EÜAŞ",
            unit="MW",
            original_unit="MW",
        )

    async def get_euas_generation(
        self, start_year: int, end_year: int, source: str | None = None
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        if source:
            workbook = await self.teias.annual_workbook("euas_generation_by_source")
            source_key = _canonical_source(source)
            if source_key == "hydro":
                source_key = "hydro_geothermal_wind"
            records = parse_euas_generation_by_source(workbook.content)
            latest = workbook.period_end or max(item["year"] for item in records)
            _ensure_annual_overlap(
                requested_start=start_year,
                requested_end=end_year,
                latest_available_year=latest,
                series_years=[item["year"] for item in records],
            )
            data = [
                item
                for item in _filter_years(records, start_year, end_year)
                if item["source"] == source_key
            ]
            notes = (
                "TEİAŞ tablosunda EÜAŞ hidro, jeotermal ve rüzgâr üretimi birleşik sütundur."
            )
        else:
            workbook = await self.teias.annual_workbook("generation_by_organization")
            records = parse_generation_by_organization(workbook.content)
            latest = workbook.period_end or max(item["year"] for item in records)
            _ensure_annual_overlap(
                requested_start=start_year,
                requested_end=end_year,
                latest_available_year=latest,
                series_years=[item["year"] for item in records],
            )
            data = [
                {"year": item["year"], "generation_gwh": item["euas_generation_gwh"]}
                for item in _filter_years(records, start_year, end_year)
            ]
            notes = None
        _require_data(
            data,
            "Belirtilen yıllar/kaynak için EÜAŞ üretim verisi bulunamadı.",
            latest_available_period=str(latest),
            requested_period=f"{start_year}-{end_year}",
        )
        return self._annual_response(
            dataset="generation",
            data=data,
            workbooks=[workbook],
            requested_start=start_year,
            requested_end=end_year,
            subject="EÜAŞ",
            unit="GWh",
            original_unit="GWh",
            notes=notes,
        )

    async def get_euas_monthly_generation(
        self, start_date: str, end_date: str, source: str | None = None
    ) -> dict[str, Any]:
        start = parse_date(start_date)
        end = parse_date(end_date)
        if start > end:
            raise EnergyDataError(
                ErrorCode.INVALID_PARAMETER,
                "Başlangıç tarihi bitiş tarihinden büyük olamaz.",
            )
        data: list[dict[str, Any]] = []
        workbooks: list[Workbook] = []
        source_key = _canonical_source(source)
        for year in range(start.year, end.year + 1):
            workbook = await self.teias.monthly_workbook(year)
            workbooks.append(workbook)
            data.extend(parse_monthly_euas_generation(workbook.content, year))
        start_month, end_month = start.strftime("%Y-%m"), end.strftime("%Y-%m")
        filtered = [
            item
            for item in data
            if start_month <= item["date"] <= end_month
            and (source_key is None or item["source"] == source_key)
        ]
        meta = _workbook_meta(*workbooks)
        latest_period = meta["latest_available_period"]
        _require_data(
            filtered,
            "Belirtilen dönem için aylık EÜAŞ üretimi bulunamadı.",
            latest_available_period=latest_period,
            requested_period=f"{start_month}/{end_month}",
        )
        freshness = classify_monthly_freshness(
            requested_start=start,
            requested_end=end,
            latest_available_period=latest_period,
            returned_periods=[item["date"] for item in filtered],
        )
        return dataset_response(
            source="TEİAŞ",
            subject="EÜAŞ",
            dataset="monthly_generation",
            data=filtered,
            source_url=MONTHLY_PAGE,
            source_format="xlsx",
            frequency="monthly",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            unit="GWh",
            original_unit="GWh",
            latest_available_period=latest_period,
            data_freshness=freshness,
            publication_date=meta["publication_date"],
            selected_source_name=meta["selected_source_name"],
            input_sources=[workbook.source_url for workbook in workbooks],
        )

    async def get_euas_capacity_share(
        self, start_year: int, end_year: int
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        euas_workbook = await self.teias.annual_workbook("capacity_by_organization")
        turkey, turkey_workbook = await self._capacity_mix()
        euas_records = parse_capacity_by_organization(euas_workbook.content)
        latest = max(
            value
            for value in (euas_workbook.period_end, turkey_workbook.period_end)
            if value is not None
        )
        euas = {item["year"]: item["total_mw"] for item in euas_records}
        totals = {
            item["year"]: item["capacity_mw"]
            for item in turkey
            if item["source"] == "total"
        }
        series_years = sorted(set(euas) & set(totals))
        _ensure_annual_overlap(
            requested_start=start_year,
            requested_end=end_year,
            latest_available_year=latest,
            series_years=series_years,
        )
        data = [
            {
                "year": year,
                "euas_capacity_mw": euas[year],
                "turkey_capacity_mw": totals[year],
                "share_percent": round(euas[year] / totals[year] * 100, 4),
            }
            for year in range(start_year, end_year + 1)
            if year in euas and year in totals
        ]
        _require_data(
            data,
            "Belirtilen yıllar için EÜAŞ kapasite payı hesaplanamadı.",
            latest_available_period=str(latest),
            requested_period=f"{start_year}-{end_year}",
        )
        return self._annual_response(
            dataset="share_of_installed_capacity",
            data=data,
            workbooks=[euas_workbook, turkey_workbook],
            requested_start=start_year,
            requested_end=end_year,
            subject="EÜAŞ",
            source_url=ANNUAL_PAGE,
            unit="percent",
            notes="Pay, aynı TEİAŞ raporundaki EÜAŞ ve Türkiye toplamlarından hesaplanmıştır.",
            extra_meta={
                "input_sources": [euas_workbook.source_url, turkey_workbook.source_url],
            },
        )

    async def get_euas_generation_share(
        self, start_year: int, end_year: int
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        workbook = await self.teias.annual_workbook("generation_by_organization")
        records = parse_generation_by_organization(workbook.content)
        latest = workbook.period_end or max(item["year"] for item in records)
        _ensure_annual_overlap(
            requested_start=start_year,
            requested_end=end_year,
            latest_available_year=latest,
            series_years=[item["year"] for item in records],
        )
        rows = _filter_years(records, start_year, end_year)
        data = [
            {
                "year": row["year"],
                "euas_generation_gwh": row["euas_generation_gwh"],
                "turkey_generation_gwh": row["turkey_generation_gwh"],
                "share_percent": round(
                    row["euas_generation_gwh"] / row["turkey_generation_gwh"] * 100, 4
                ),
            }
            for row in rows
        ]
        _require_data(
            data,
            "Belirtilen yıllar için EÜAŞ üretim payı hesaplanamadı.",
            latest_available_period=str(latest),
            requested_period=f"{start_year}-{end_year}",
        )
        return self._annual_response(
            dataset="share_of_generation",
            data=data,
            workbooks=[workbook],
            requested_start=start_year,
            requested_end=end_year,
            subject="EÜAŞ",
            unit="percent",
            notes="Pay, aynı TEİAŞ üretici kuruluş tablosundan hesaplanmıştır.",
        )

    async def compare_euas_vs_turkey_generation(
        self, start_year: int, end_year: int
    ) -> dict[str, Any]:
        response = await self.get_euas_generation_share(start_year, end_year)
        response["dataset"] = "euas_vs_turkey_generation"
        response["unit"] = None
        return response
