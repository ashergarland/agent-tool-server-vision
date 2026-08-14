"""Filesystem asset store used for local development and tests."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from ..security import new_token, principal_bucket
from .base import (
    AssetRecord,
    authorize,
    ensure_content_type,
    expiry_from,
    not_found,
    quota_exceeded,
    too_large,
)


class FilesystemAssetStore:
    """Stores assets as unguessable files beneath a private root directory."""

    def __init__(
        self,
        root: Path,
        *,
        ttl_seconds: int,
        max_bytes: int,
        quota_bytes: int,
        quota_count: int,
    ) -> None:
        self._root = root
        self._ttl_seconds = ttl_seconds
        self._max_bytes = max_bytes
        self._quota_bytes = quota_bytes
        self._quota_count = quota_count

    # -- public API ---------------------------------------------------------

    async def put(
        self,
        principal: str,
        chunks: AsyncIterator[bytes],
        content_type: str,
    ) -> AssetRecord:
        normalized_type = ensure_content_type(content_type)
        buffer = bytearray()
        async for chunk in chunks:
            buffer.extend(chunk)
            if len(buffer) > self._max_bytes:
                raise too_large(self._max_bytes)
        if not buffer:
            raise _empty()
        return await asyncio.to_thread(self._write, principal, bytes(buffer), normalized_type)

    async def get(self, principal: str, asset_id: str) -> tuple[AssetRecord, bytes]:
        return await asyncio.to_thread(self._read, principal, asset_id)

    async def delete(self, principal: str, asset_id: str) -> None:
        await asyncio.to_thread(self._delete, principal, asset_id)

    async def purge_expired(self) -> int:
        if not self._root.exists():
            return 0
        return await asyncio.to_thread(self._purge_expired)

    async def health(self) -> tuple[str, str | None]:
        try:
            await asyncio.to_thread(self._ensure_root)
            return "ok", None
        except OSError:  # pragma: no cover - depends on host filesystem
            return "unavailable", "asset root is not writable"

    # -- internals ----------------------------------------------------------

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)

    def _bucket(self, principal: str) -> Path:
        return self._root / principal_bucket(principal)

    def _write(self, principal: str, payload: bytes, content_type: str) -> AssetRecord:
        self._ensure_root()
        bucket = self._bucket(principal)
        bucket.mkdir(parents=True, exist_ok=True)
        os.chmod(bucket, 0o700)
        self._purge_expired()
        self._enforce_quota(bucket, len(payload))
        asset_id = new_token()
        created, expires = expiry_from(self._ttl_seconds)
        record = AssetRecord(
            asset_id=asset_id,
            principal=principal,
            content_type=content_type,
            byte_count=len(payload),
            created_at=created,
            expires_at=expires,
        )
        data_path = bucket / f"{asset_id}.bin"
        with open(os.open(data_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as handle:
            handle.write(payload)
        meta_path = bucket / f"{asset_id}.json"
        with open(os.open(meta_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as handle:
            json.dump(_encode(record), handle)
        return record

    def _paths(self, principal: str, asset_id: str) -> tuple[Path, Path]:
        if not asset_id or "/" in asset_id or "\\" in asset_id or asset_id.startswith("."):
            raise not_found()
        bucket = self._bucket(principal)
        return bucket / f"{asset_id}.bin", bucket / f"{asset_id}.json"

    def _read(self, principal: str, asset_id: str) -> tuple[AssetRecord, bytes]:
        data_path, meta_path = self._paths(principal, asset_id)
        try:
            record = _decode(json.loads(meta_path.read_text()))
            payload = data_path.read_bytes()
        except (OSError, ValueError, KeyError) as exc:
            raise not_found() from exc
        authorize(record, principal)
        return record, payload

    def _delete(self, principal: str, asset_id: str) -> None:
        data_path, meta_path = self._paths(principal, asset_id)
        data_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)

    def _purge_expired(self) -> int:
        removed = 0
        now = datetime.now(UTC)
        for meta_path in self._root.glob("*/*.json"):
            try:
                record = _decode(json.loads(meta_path.read_text()))
            except (OSError, ValueError, KeyError):
                meta_path.unlink(missing_ok=True)
                continue
            if record.expires_at <= now:
                meta_path.with_suffix(".bin").unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _enforce_quota(self, bucket: Path, incoming_bytes: int) -> None:
        total_bytes = 0
        count = 0
        for path in bucket.glob("*.bin"):
            try:
                total_bytes += path.stat().st_size
            except OSError:  # pragma: no cover - race with purge
                continue
            count += 1
        if count + 1 > self._quota_count or total_bytes + incoming_bytes > self._quota_bytes:
            raise quota_exceeded()

    def reset(self) -> None:
        """Test and development helper that removes every stored asset."""
        shutil.rmtree(self._root, ignore_errors=True)
        self._root.mkdir(parents=True, exist_ok=True)


def _encode(record: AssetRecord) -> dict[str, str | int]:
    return {
        "assetId": record.asset_id,
        "principal": record.principal,
        "contentType": record.content_type,
        "byteCount": record.byte_count,
        "createdAt": record.created_at.isoformat(),
        "expiresAt": record.expires_at.isoformat(),
    }


def _decode(payload: dict[str, str | int]) -> AssetRecord:
    return AssetRecord(
        asset_id=str(payload["assetId"]),
        principal=str(payload["principal"]),
        content_type=str(payload["contentType"]),
        byte_count=int(payload["byteCount"]),
        created_at=datetime.fromisoformat(str(payload["createdAt"])),
        expires_at=datetime.fromisoformat(str(payload["expiresAt"])),
    )


def _empty() -> Exception:
    from ..errors import ErrorCode, VisionError

    return VisionError(ErrorCode.INVALID_INPUT, "Asset payload is empty")
