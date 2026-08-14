"""Private Azure Blob Storage asset store.

Access uses ``DefaultAzureCredential`` (managed identity when hosted); no
account key, connection string, or SAS URL is ever constructed or returned.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from ..errors import ErrorCode, VisionError
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


class AzureBlobAssetStore:
    """Asset store backed by a single private container."""

    def __init__(
        self,
        account_url: str,
        container: str,
        *,
        ttl_seconds: int,
        max_bytes: int,
        quota_bytes: int,
        quota_count: int,
        client_factory: Any | None = None,
    ) -> None:
        self._account_url = account_url
        self._container = container
        self._ttl_seconds = ttl_seconds
        self._max_bytes = max_bytes
        self._quota_bytes = quota_bytes
        self._quota_count = quota_count
        self._client_factory = client_factory
        self._client: Any | None = None

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
            raise VisionError(ErrorCode.INVALID_INPUT, "Asset payload is empty")
        return await asyncio.to_thread(self._upload, principal, bytes(buffer), normalized_type)

    async def get(self, principal: str, asset_id: str) -> tuple[AssetRecord, bytes]:
        return await asyncio.to_thread(self._download, principal, asset_id)

    async def delete(self, principal: str, asset_id: str) -> None:
        await asyncio.to_thread(self._delete, principal, asset_id)

    async def purge_expired(self) -> int:
        """Container lifecycle management performs bulk deletion.

        Expired assets are additionally rejected on read, so this call only
        reports that no in-process purge is required.
        """
        return 0

    async def health(self) -> tuple[str, str | None]:
        try:
            await asyncio.to_thread(self._container_client().get_container_properties)
        except Exception:  # noqa: BLE001 - SDK errors must not leak
            return "unavailable", "blob container is unreachable"
        return "ok", None

    # -- internals ----------------------------------------------------------

    def _container_client(self) -> Any:
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import ContainerClient
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise VisionError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Azure storage support is not installed",
            ) from exc
        return ContainerClient(
            account_url=self._account_url,
            container_name=self._container,
            credential=DefaultAzureCredential(),
        )

    def _blob_name(self, principal: str, asset_id: str) -> str:
        if not asset_id.replace("-", "").replace("_", "").isalnum():
            raise not_found()
        return f"{principal_bucket(principal)}/{asset_id}"

    def _upload(self, principal: str, payload: bytes, content_type: str) -> AssetRecord:
        self._enforce_quota(principal, len(payload))
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
        try:
            from azure.storage.blob import ContentSettings

            settings: Any = ContentSettings(content_type=content_type)
        except ImportError:  # pragma: no cover - exercised through fakes
            settings = None
        client = self._container_client().get_blob_client(self._blob_name(principal, asset_id))
        client.upload_blob(
            payload,
            overwrite=False,
            content_settings=settings,
            metadata={
                "principal": principal,
                "expiresat": expires.isoformat(),
                "createdat": created.isoformat(),
            },
        )
        return record

    def _download(self, principal: str, asset_id: str) -> tuple[AssetRecord, bytes]:
        client = self._container_client().get_blob_client(self._blob_name(principal, asset_id))
        try:
            downloader = client.download_blob()
            payload = downloader.readall()
            properties = client.get_blob_properties()
        except Exception as exc:  # noqa: BLE001 - SDK errors must not leak
            raise not_found() from exc
        metadata = dict(getattr(properties, "metadata", {}) or {})
        content_type = getattr(getattr(properties, "content_settings", None), "content_type", "")
        record = AssetRecord(
            asset_id=asset_id,
            principal=str(metadata.get("principal", "")),
            content_type=str(content_type or "image/png"),
            byte_count=len(payload),
            created_at=_parse(metadata.get("createdat")),
            expires_at=_parse(metadata.get("expiresat")),
        )
        authorize(record, principal)
        return record, bytes(payload)

    def _delete(self, principal: str, asset_id: str) -> None:
        client = self._container_client().get_blob_client(self._blob_name(principal, asset_id))
        try:
            client.delete_blob()
        except Exception:  # noqa: BLE001 - deletion is best effort
            return

    def _enforce_quota(self, principal: str, incoming_bytes: int) -> None:
        prefix = principal_bucket(principal) + "/"
        total = 0
        count = 0
        try:
            blobs = self._container_client().list_blobs(name_starts_with=prefix)
        except Exception:  # noqa: BLE001 - quota accounting is best effort
            return
        for blob in blobs:
            count += 1
            total += int(getattr(blob, "size", 0) or 0)
        if count + 1 > self._quota_count or total + incoming_bytes > self._quota_bytes:
            raise quota_exceeded()


def _parse(value: object) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.now(UTC)
