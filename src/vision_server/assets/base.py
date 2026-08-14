"""Asset storage abstraction.

Assets are opaque, principal-scoped, size- and TTL-bounded blobs. Callers never
see filesystem paths, container names, or storage URLs.
"""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ..errors import ErrorCode, VisionError

ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/webp"})
CONTENT_TYPE_BY_FORMAT: dict[str, str] = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


@dataclass(frozen=True)
class AssetRecord:
    """Metadata describing a stored asset."""

    asset_id: str
    principal: str
    content_type: str
    byte_count: int
    created_at: datetime
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= datetime.now(UTC)


class AssetStore(Protocol):
    """Storage backend contract."""

    async def put(
        self,
        principal: str,
        chunks: AsyncIterator[bytes],
        content_type: str,
    ) -> AssetRecord: ...

    async def get(self, principal: str, asset_id: str) -> tuple[AssetRecord, bytes]: ...

    async def delete(self, principal: str, asset_id: str) -> None: ...

    async def purge_expired(self) -> int: ...

    async def health(self) -> tuple[str, str | None]: ...


def expiry_from(ttl_seconds: int) -> tuple[datetime, datetime]:
    created = datetime.now(UTC).replace(microsecond=0)
    return created, created + timedelta(seconds=ttl_seconds)


def ensure_content_type(content_type: str) -> str:
    normalized = content_type.split(";")[0].strip().lower()
    if normalized not in ALLOWED_CONTENT_TYPES:
        raise VisionError(
            ErrorCode.UNSUPPORTED_MEDIA,
            "Only PNG, JPEG, and WebP assets are supported",
            details={"contentType": normalized},
        )
    return normalized


def authorize(record: AssetRecord, principal: str) -> AssetRecord:
    """Reject cross-principal access without revealing whether the asset exists."""
    if not hmac.compare_digest(record.principal, principal):
        raise VisionError(ErrorCode.NOT_FOUND, "Asset not found")
    if record.is_expired:
        raise VisionError(ErrorCode.NOT_FOUND, "Asset not found")
    return record


def not_found() -> VisionError:
    return VisionError(ErrorCode.NOT_FOUND, "Asset not found")


def too_large(limit: int) -> VisionError:
    return VisionError(
        ErrorCode.PAYLOAD_TOO_LARGE,
        "Asset exceeds the configured size limit",
        details={"maxBytes": limit},
    )


def quota_exceeded() -> VisionError:
    return VisionError(ErrorCode.QUOTA_EXCEEDED, "Asset quota exceeded for this principal")
