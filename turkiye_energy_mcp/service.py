from datetime import date
from typing import Any

from .clients.teias import TeiasClient, Workbook
from .exceptions import EnergyDataError, ErrorCode
from .models import dataset_response
from .parsers.common import normalize_key, parse_date, parse_year_range
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


def _require_data(data: list[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    if not data:
        raise EnergyDataError(ErrorCode.DATA_NOT_AVAILABLE, message, source="TEİAŞ")
    return data


class EnergyService:
    def __init__(self, teias: TeiasClient) -> None:
        self.teias = teias

    async def _capacity_mix(self) -> tuple[list[dict[str, Any]], Workbook]:
        workbook = await self.teias.annual_workbook("i-kurulu-guc", "9-")
        return parse_capacity_mix(workbook.content), workbook

    async def _generation_mix(self) -> tuple[list[dict[str, Any]], Workbook]:
        workbook = await self.teias.annual_workbook(
            "iii-elektrik-enerjisi-uretimi-tuketimi-kayiplar", "63-"
        )
        return parse_generation_mix(workbook.content), workbook

    async def _energy_balance(self) -> tuple[list[dict[str, Any]], Workbook]:
        workbook = await self.teias.annual_workbook(
            "iii-elektrik-enerjisi-uretimi-tuketimi-kayiplar", "48-"
        )
        return parse_energy_balance(workbook.content), workbook

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
        if start.year < 2019:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "TEİAŞ aylık çalışma kitapları 2019 yılından başlıyor.",
                source="TEİAŞ",
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
        sources: list[str] = []
        formats: set[str] = set()
        for year in range(start.year, end.year + 1):
            workbook = await self.teias.monthly_workbook(year)
            sources.append(workbook.source_url)
            formats.add(workbook.source_format)
            data.extend(parse_monthly_energy(workbook.content, year))

        start_month = start.strftime("%Y-%m")
        end_month = end.strftime("%Y-%m")
        data = [
            record
            for record in data
            if start_month <= record["date"] <= end_month
            and (metric_key is None or record["metric"] == metric_key)
        ]
        _require_data(data, "Belirtilen dönem ve metrik için aylık veri bulunamadı.")
        return dataset_response(
            source="TEİAŞ",
            dataset="monthly_energy",
            data=data,
            source_url=MONTHLY_PAGE,
            source_format="/".join(sorted(formats)),
            frequency="monthly",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            unit="GWh",
            original_unit="GWh",
            notes="Cari yıl değerleri TEİAŞ tarafından geçici olarak işaretlenebilir.",
            input_sources=sources,
        )

    async def get_generation(
        self,
        start_year: int,
        end_year: int,
        source: str | None = None,
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        records, workbook = await self._generation_mix()
        source_key = _canonical_source(source)
        if source_key == "geothermal":
            source_key = "geothermal_and_wind"
        data = [
            record
            for record in _filter_years(records, start_year, end_year)
            if source_key is None or record["source"] == source_key
        ]
        _require_data(data, "Belirtilen yıllar veya kaynak için üretim verisi bulunamadı.")
        return dataset_response(
            source="TEİAŞ",
            dataset="generation_by_source",
            data=data,
            source_url=workbook.source_url,
            source_format=workbook.source_format,
            frequency="annual",
            start_date=str(start_year),
            end_date=str(end_year),
            unit="GWh",
            original_unit="GWh",
            notes=(
                "2000-2024 tablosunda jeotermal ve rüzgâr tek sütunda birleştirilmiştir; "
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
        source_key = _canonical_source(source)
        data = [
            record
            for record in _filter_years(records, start_year, end_year)
            if source_key is None or record["source"] == source_key
        ]
        _require_data(data, "Belirtilen yıllar veya kaynak için kurulu güç verisi bulunamadı.")
        return dataset_response(
            source="TEİAŞ",
            dataset="installed_capacity_by_source",
            data=data,
            source_url=workbook.source_url,
            source_format=workbook.source_format,
            frequency="annual",
            start_date=str(start_year),
            end_date=str(end_year),
            unit="MW",
            original_unit="MW",
        )

    async def get_peak_demand(self, start_year: int, end_year: int) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        workbook = await self.teias.annual_workbook(
            "ii-turkiye-kurulu-gucunun-kullanim-degerleri", "26-"
        )
        data = _filter_years(parse_peak_demand(workbook.content), start_year, end_year)
        _require_data(data, "Belirtilen yıllar için puant verisi bulunamadı.")
        return dataset_response(
            source="TEİAŞ",
            dataset="peak_demand",
            data=data,
            source_url=workbook.source_url,
            source_format=workbook.source_format,
            frequency="annual",
            start_date=str(start_year),
            end_date=str(end_year),
            unit=None,
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
        _require_data(data, "Belirtilen yıllar için ithalat/ihracat verisi bulunamadı.")
        return dataset_response(
            source="TEİAŞ",
            dataset="import_export",
            data=data,
            source_url=workbook.source_url,
            source_format=workbook.source_format,
            frequency="annual",
            start_date=str(start_year),
            end_date=str(end_year),
            unit="GWh",
            original_unit="GWh",
        )

    async def get_transmission_statistics(
        self, start_year: int, end_year: int
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        lines = await self.teias.annual_workbook(
            "vi-enerji-nakil-hat-ve-trafolari", "95-"
        )
        transformers = await self.teias.annual_workbook(
            "vi-enerji-nakil-hat-ve-trafolari", "98-"
        )
        line_data = {
            record["year"]: record
            for record in _filter_years(
                parse_transmission_lines(lines.content), start_year, end_year
            )
        }
        transformer_data = {
            record["year"]: record
            for record in _filter_years(
                parse_transformers(transformers.content), start_year, end_year
            )
        }
        data = [
            {**line_data.get(year, {}), **transformer_data.get(year, {}), "year": year}
            for year in sorted(set(line_data) | set(transformer_data))
        ]
        _require_data(data, "Belirtilen yıllar için iletim istatistiği bulunamadı.")
        return dataset_response(
            source="TEİAŞ",
            dataset="transmission_statistics",
            data=data,
            source_url=ANNUAL_PAGE,
            source_format=f"{lines.source_format}/{transformers.source_format}",
            frequency="annual",
            start_date=str(start_year),
            end_date=str(end_year),
            unit=None,
            notes="Hat uzunlukları km, trafo kapasiteleri MVA'dır.",
            input_sources=[lines.source_url, transformers.source_url],
        )

    async def get_renewable_summary(
        self, start_year: int, end_year: int
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        capacity, capacity_workbook = await self._capacity_mix()
        generation, generation_workbook = await self._generation_mix()
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
        _require_data(data, "Belirtilen yıllar için yenilenebilir özeti hesaplanamadı.")
        return dataset_response(
            source="TEİAŞ",
            dataset="renewable_summary",
            data=data,
            source_url=ANNUAL_PAGE,
            source_format=f"{capacity_workbook.source_format}/{generation_workbook.source_format}",
            frequency="annual",
            start_date=str(start_year),
            end_date=str(end_year),
            unit=None,
            notes="Paylar TEİAŞ kaynak ve toplam sütunlarından hesaplanmıştır.",
            input_sources=[capacity_workbook.source_url, generation_workbook.source_url],
        )

    async def get_system_summary(self, year: int) -> dict[str, Any]:
        parse_year_range(year, year)
        capacity, capacity_workbook = await self._capacity_mix()
        balance, balance_workbook = await self._energy_balance()
        peak_workbook = await self.teias.annual_workbook(
            "ii-turkiye-kurulu-gucunun-kullanim-degerleri", "26-"
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
        peak_item = next(
            (item for item in parse_peak_demand(peak_workbook.content) if item["year"] == year),
            None,
        )
        if capacity_total is None or balance_item is None or peak_item is None:
            raise EnergyDataError(
                ErrorCode.DATA_NOT_AVAILABLE,
                "Belirtilen yıl için eksiksiz sistem özeti bulunamadı.",
                source="TEİAŞ",
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
        return dataset_response(
            source="TEİAŞ",
            dataset="system_summary",
            data=data,
            source_url=ANNUAL_PAGE,
            source_format="xls/xlsx",
            frequency="annual",
            start_date=str(year),
            end_date=str(year),
            unit=None,
            input_sources=[
                capacity_workbook.source_url,
                balance_workbook.source_url,
                peak_workbook.source_url,
            ],
        )

    async def _euas_plants(self) -> tuple[list[dict[str, Any]], list[Workbook]]:
        thermal = await self.teias.annual_workbook("i-kurulu-guc", "18-")
        hydro = await self.teias.annual_workbook("i-kurulu-guc", "19-")
        records = parse_euas_thermal_plants(thermal.content) + parse_euas_hydro_plants(
            hydro.content
        )
        return records, [thermal, hydro]

    async def get_euas_power_plants(
        self,
        plant_type: str | None = None,
        province: str | None = None,
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
        data = [
            record
            for record in records
            if (not type_key or record["plant_type"] == type_key)
            and (not province_key or province_key in normalize_key(record["province"] or ""))
        ]
        _require_data(data, "Filtrelere uyan EÜAŞ santrali bulunamadı.")
        data.sort(key=lambda item: item.get("installed_capacity_mw") or 0, reverse=True)
        return dataset_response(
            source="TEİAŞ",
            subject="EÜAŞ",
            dataset="power_plants",
            data=data,
            source_url=ANNUAL_PAGE,
            source_format="xls",
            frequency="annual",
            start_date="2024",
            end_date="2024",
            unit=None,
            notes=(
                "TEİAŞ'ın EÜAŞ termik/hidrolik santral tabloları kullanılmıştır. "
                "Çalışma kitabındaki güvenilir olmayan ünite ve devreye giriş hücreleri yayımlanmaz."
            ),
            input_sources=[workbook.source_url for workbook in workbooks],
        )

    async def get_euas_plant(self, plant_name: str) -> dict[str, Any]:
        if len(plant_name.strip()) < 2:
            raise EnergyDataError(
                ErrorCode.INVALID_PARAMETER, "Santral adı en az iki karakter olmalıdır."
            )
        result = await self.get_euas_power_plants()
        needle = normalize_key(plant_name)
        result["data"] = [
            item for item in result["data"] if needle in normalize_key(item["name"])
        ]
        _require_data(result["data"], "EÜAŞ santral adı bulunamadı.")
        result["dataset"] = "power_plant"
        return result

    async def get_euas_installed_capacity(
        self, start_year: int, end_year: int
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        workbook = await self.teias.annual_workbook("i-kurulu-guc", "13-")
        data = _filter_years(
            parse_capacity_by_organization(workbook.content), start_year, end_year
        )
        _require_data(data, "Belirtilen yıllar için EÜAŞ kurulu güç verisi bulunamadı.")
        return dataset_response(
            source="TEİAŞ",
            subject="EÜAŞ",
            dataset="installed_capacity",
            data=data,
            source_url=workbook.source_url,
            source_format=workbook.source_format,
            frequency="annual",
            start_date=str(start_year),
            end_date=str(end_year),
            unit="MW",
            original_unit="MW",
        )

    async def get_euas_generation(
        self, start_year: int, end_year: int, source: str | None = None
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        if source:
            workbook = await self.teias.annual_workbook(
                "iii-elektrik-enerjisi-uretimi-tuketimi-kayiplar", "71-"
            )
            source_key = _canonical_source(source)
            if source_key == "hydro":
                source_key = "hydro_geothermal_wind"
            data = [
                item
                for item in _filter_years(
                    parse_euas_generation_by_source(workbook.content),
                    start_year,
                    end_year,
                )
                if item["source"] == source_key
            ]
            notes = (
                "TEİAŞ tablosunda EÜAŞ hidro, jeotermal ve rüzgâr üretimi birleşik sütundur."
            )
        else:
            workbook = await self.teias.annual_workbook(
                "iii-elektrik-enerjisi-uretimi-tuketimi-kayiplar", "73-"
            )
            data = _filter_years(
                parse_generation_by_organization(workbook.content), start_year, end_year
            )
            data = [
                {"year": item["year"], "generation_gwh": item["euas_generation_gwh"]}
                for item in data
            ]
            notes = None
        _require_data(data, "Belirtilen yıllar/kaynak için EÜAŞ üretim verisi bulunamadı.")
        return dataset_response(
            source="TEİAŞ",
            subject="EÜAŞ",
            dataset="generation",
            data=data,
            source_url=workbook.source_url,
            source_format=workbook.source_format,
            frequency="annual",
            start_date=str(start_year),
            end_date=str(end_year),
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
        sources: list[str] = []
        source_key = _canonical_source(source)
        for year in range(start.year, end.year + 1):
            workbook = await self.teias.monthly_workbook(year)
            sources.append(workbook.source_url)
            data.extend(parse_monthly_euas_generation(workbook.content, year))
        start_month, end_month = start.strftime("%Y-%m"), end.strftime("%Y-%m")
        data = [
            item
            for item in data
            if start_month <= item["date"] <= end_month
            and (source_key is None or item["source"] == source_key)
        ]
        _require_data(data, "Belirtilen dönem için aylık EÜAŞ üretimi bulunamadı.")
        return dataset_response(
            source="TEİAŞ",
            subject="EÜAŞ",
            dataset="monthly_generation",
            data=data,
            source_url=MONTHLY_PAGE,
            source_format="xlsx",
            frequency="monthly",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            unit="GWh",
            original_unit="GWh",
            input_sources=sources,
        )

    async def get_euas_capacity_share(
        self, start_year: int, end_year: int
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        euas_workbook = await self.teias.annual_workbook("i-kurulu-guc", "13-")
        turkey, turkey_workbook = await self._capacity_mix()
        euas = {
            item["year"]: item["total_mw"]
            for item in parse_capacity_by_organization(euas_workbook.content)
        }
        totals = {
            item["year"]: item["capacity_mw"]
            for item in turkey
            if item["source"] == "total"
        }
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
        _require_data(data, "Belirtilen yıllar için EÜAŞ kapasite payı hesaplanamadı.")
        return dataset_response(
            source="TEİAŞ",
            subject="EÜAŞ",
            dataset="share_of_installed_capacity",
            data=data,
            source_url=ANNUAL_PAGE,
            source_format="xls",
            frequency="annual",
            start_date=str(start_year),
            end_date=str(end_year),
            unit="percent",
            notes="Pay, aynı TEİAŞ raporundaki EÜAŞ ve Türkiye toplamlarından hesaplanmıştır.",
            input_sources=[euas_workbook.source_url, turkey_workbook.source_url],
        )

    async def get_euas_generation_share(
        self, start_year: int, end_year: int
    ) -> dict[str, Any]:
        start_year, end_year = parse_year_range(start_year, end_year)
        workbook = await self.teias.annual_workbook(
            "iii-elektrik-enerjisi-uretimi-tuketimi-kayiplar", "73-"
        )
        rows = _filter_years(
            parse_generation_by_organization(workbook.content), start_year, end_year
        )
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
        _require_data(data, "Belirtilen yıllar için EÜAŞ üretim payı hesaplanamadı.")
        return dataset_response(
            source="TEİAŞ",
            subject="EÜAŞ",
            dataset="share_of_generation",
            data=data,
            source_url=workbook.source_url,
            source_format=workbook.source_format,
            frequency="annual",
            start_date=str(start_year),
            end_date=str(end_year),
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
