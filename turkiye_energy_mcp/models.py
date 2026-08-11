from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

ISTANBUL = ZoneInfo("Europe/Istanbul")


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(ISTANBUL))
    source_url: str
    source_format: str
    frequency: str
    notes: str | None = None
    original_unit: str | None = None
    latest_available_period: str | None = None
    data_freshness: str | None = None
    publication_date: str | None = None
    selected_source_name: str | None = None


class DatasetResponse(BaseModel):
    source: str
    subject: str | None = None
    dataset: str
    start_date: str | None = None
    end_date: str | None = None
    unit: str | None = None
    data: list[dict[str, Any]]
    metadata: SourceMetadata

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=False)


def dataset_response(
    *,
    source: str,
    dataset: str,
    data: list[dict[str, Any]],
    source_url: str,
    source_format: str,
    frequency: str,
    subject: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    unit: str | None = None,
    notes: str | None = None,
    original_unit: str | None = None,
    latest_available_period: str | None = None,
    data_freshness: str | None = None,
    publication_date: str | None = None,
    selected_source_name: str | None = None,
    **metadata: Any,
) -> dict[str, Any]:
    return DatasetResponse(
        source=source,
        subject=subject,
        dataset=dataset,
        start_date=start_date,
        end_date=end_date,
        unit=unit,
        data=data,
        metadata=SourceMetadata(
            source_url=source_url,
            source_format=source_format,
            frequency=frequency,
            notes=notes,
            original_unit=original_unit,
            latest_available_period=latest_available_period,
            data_freshness=data_freshness,
            publication_date=publication_date,
            selected_source_name=selected_source_name,
            **metadata,
        ),
    ).as_dict()
