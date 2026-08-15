"""Single safe error model shared by every transport."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    """Stable, client-visible error codes."""

    INVALID_INPUT = "invalid_input"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UNSUPPORTED_MEDIA = "unsupported_media"
    QUOTA_EXCEEDED = "quota_exceeded"
    BUSY = "busy"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ERROR = "provider_error"
    INTERNAL = "internal"


_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_INPUT: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_MEDIA: 415,
    ErrorCode.QUOTA_EXCEEDED: 429,
    ErrorCode.BUSY: 503,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.PROVIDER_UNAVAILABLE: 503,
    ErrorCode.PROVIDER_ERROR: 502,
    ErrorCode.INTERNAL: 500,
}

_RETRYABLE_CODES = frozenset(
    {
        ErrorCode.BUSY,
        ErrorCode.TIMEOUT,
        ErrorCode.PROVIDER_UNAVAILABLE,
    }
)

MAX_DETAIL_ENTRIES = 10
MAX_DETAIL_LENGTH = 200


def _bounded_details(details: dict[str, Any] | None) -> dict[str, str]:
    """Clamp detail payloads so provider or filesystem data cannot leak."""
    if not details:
        return {}
    bounded: dict[str, str] = {}
    for key, value in list(details.items())[:MAX_DETAIL_ENTRIES]:
        bounded[str(key)[:MAX_DETAIL_LENGTH]] = str(value)[:MAX_DETAIL_LENGTH]
    return bounded


class ErrorBody(BaseModel):
    """Body of an error response."""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    retryable: bool
    details: dict[str, str] = Field(default_factory=dict)
    request_id: str = Field(alias="requestId", default="")


class ErrorResponse(BaseModel):
    """Envelope returned by every transport for failures."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    error: ErrorBody


class VisionError(Exception):
    """Application error carrying only client-safe information."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = code in _RETRYABLE_CODES if retryable is None else retryable
        self.details = _bounded_details(details)

    @property
    def status_code(self) -> int:
        return _STATUS_BY_CODE[self.code]

    def to_response(self, request_id: str = "") -> ErrorResponse:
        return ErrorResponse(
            error=ErrorBody(
                code=self.code,
                message=self.message,
                retryable=self.retryable,
                details=self.details,
                requestId=request_id,
            )
        )


def invalid_input(message: str, **details: Any) -> VisionError:
    return VisionError(ErrorCode.INVALID_INPUT, message, details=details or None)
