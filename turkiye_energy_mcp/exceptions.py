from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    PARSING_ERROR = "PARSING_ERROR"
    INVALID_PARAMETER = "INVALID_PARAMETER"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"


@dataclass(slots=True)
class EnergyDataError(Exception):
    code: ErrorCode
    message: str
    source: str | None = None
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": True,
            "code": self.code.value,
            "message": self.message,
            "source": self.source,
        }
        if self.details:
            payload["details"] = self.details
        return payload
