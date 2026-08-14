"""Narrow OCR provider interface.

Only text and layout extraction is delegated to a provider. Comparison,
cropping, optimization, metadata, and normalization always run locally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..errors import ErrorCode, VisionError
from ..imaging import LoadedImage


@dataclass(frozen=True)
class OcrBlock:
    """A normalized text block with coordinates in image pixel space."""

    text: str
    block_type: str = "line"
    page: int = 1
    polygon: tuple[float, ...] = ()
    confidence: float | None = None


@dataclass(frozen=True)
class OcrResult:
    """Provider agnostic OCR output. Raw provider payloads never escape."""

    blocks: tuple[OcrBlock, ...]
    provider_name: str
    model: str | None = None
    api_version: str | None = None
    markdown: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


class ProviderError(VisionError):
    """Base class for provider failures with an explicit retry decision."""


def provider_unavailable(message: str, **details: object) -> ProviderError:
    """Transient failure: the caller may retry or fall back."""
    return ProviderError(
        ErrorCode.PROVIDER_UNAVAILABLE, message, retryable=True, details=dict(details)
    )


def provider_timeout(message: str) -> ProviderError:
    return ProviderError(ErrorCode.TIMEOUT, message, retryable=True)


def provider_auth_error(message: str) -> ProviderError:
    """Authentication failures are never retried and never trigger fallback."""
    return ProviderError(ErrorCode.FORBIDDEN, message, retryable=False)


def provider_quota_error(message: str) -> ProviderError:
    return ProviderError(ErrorCode.QUOTA_EXCEEDED, message, retryable=False)


def provider_invalid_input(message: str) -> ProviderError:
    return ProviderError(ErrorCode.INVALID_INPUT, message, retryable=False)


def provider_malformed(message: str) -> ProviderError:
    return ProviderError(ErrorCode.PROVIDER_ERROR, message, retryable=False)


class OcrProvider(Protocol):
    """Contract implemented by the local and managed providers."""

    name: str

    async def analyze(self, image: LoadedImage, language: str) -> OcrResult: ...

    async def health(self) -> tuple[str, str | None]: ...
