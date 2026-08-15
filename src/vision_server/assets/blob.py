"""Private Azure Blob Storage asset store.

Access uses ``DefaultAzureCredential`` (managed identity when hosted); no
account key, connection string, or SAS URL is ever constructed or returned.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from ..errors import ErrorCode, VisionError
from ..security import new_token, principal_bucket
from .base import (
    ID_PREFIX,
    AssetKind,
    AssetRecord,
    authorize,
    ensure_content_type,
    expiry_from,
    kind_of,
    not_found,
    quota_exceeded,
    too_large,
)

LOGGER = logging.getLogger("vision_server.assets.blob")
_QUOTA_LOCK_PREFIX = "_quota"


class AzureBlobAssetStore:
    """Asset store backed by two private containers: inputs and artifacts."""

    def __init__(
        self,
        account_url: str,
        container: str,
        artifact_container: str | None = None,
        *,
        ttl_seconds: int,
        max_bytes: int,
        quota_bytes: int,
        quota_count: int,
        client_factory: Any | None = None,
    ) -> None:
        self._account_url = account_url
        self._containers = {
            AssetKind.INPUT: container,
            AssetKind.ARTIFACT: artifact_container or container,
        }
        self._ttl_seconds = ttl_seconds
        self._max_bytes = max_bytes
        self._quota_bytes = quota_bytes
        self._quota_count = quota_count
        self._client_factory = client_factory
        self._clients: dict[AssetKind, Any] = {}

    # -- public API ---------------------------------------------------------

    async def put(
        self,
        principal: str,
        chunks: AsyncIterator[bytes],
        content_type: str,
        kind: AssetKind = AssetKind.INPUT,
    ) -> AssetRecord:
        normalized_type = ensure_content_type(content_type)
        buffer = bytearray()
        async for chunk in chunks:
            buffer.extend(chunk)
            if len(buffer) > self._max_bytes:
                raise too_large(self._max_bytes)
        if not buffer:
            raise VisionError(ErrorCode.INVALID_INPUT, "Asset payload is empty")
        return await asyncio.to_thread(
            self._upload, principal, bytes(buffer), normalized_type, kind
        )

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
            for kind in self._containers:
                await asyncio.to_thread(self._container_client(kind).get_container_properties)
        except Exception:  # noqa: BLE001 - SDK errors must not leak
            return "unavailable", "blob container is unreachable"
        return "ok", None

    # -- internals ----------------------------------------------------------

    def _container_client(self, kind: AssetKind) -> Any:
        client = self._clients.get(kind)
        if client is None:
            client = self._create_client(self._containers[kind])
            self._clients[kind] = client
        return client

    def _create_client(self, container: str) -> Any:
        if self._client_factory is not None:
            return self._client_factory(container)
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
            container_name=container,
            credential=DefaultAzureCredential(),
        )

    def _blob_name(self, principal: str, asset_id: str) -> str:
        if not asset_id.replace("-", "").replace("_", "").isalnum():
            raise not_found()
        return f"{principal_bucket(principal)}/{asset_id}"

    def _upload(
        self, principal: str, payload: bytes, content_type: str, kind: AssetKind
    ) -> AssetRecord:
        asset_id = ID_PREFIX[kind] + new_token()
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
        container = self._container_client(kind)
        with self._quota_lease(principal, kind):
            self._enforce_quota(principal, len(payload), container)
            client = container.get_blob_client(self._blob_name(principal, asset_id))
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
        client = self._container_client(kind_of(asset_id)).get_blob_client(
            self._blob_name(principal, asset_id)
        )
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
        kind = kind_of(asset_id)
        client = self._container_client(kind).get_blob_client(self._blob_name(principal, asset_id))
        with self._quota_lease(principal, kind):
            try:
                client.delete_blob()
            except Exception as exc:  # noqa: BLE001 - normalize Azure SDK errors
                if _is_sdk_error(exc, "ResourceNotFoundError"):
                    return
                raise _storage_unavailable("Asset deletion failed") from exc

    def _enforce_quota(self, principal: str, incoming_bytes: int, container: Any) -> None:
        prefix = principal_bucket(principal) + "/"
        total = 0
        count = 0
        try:
            blobs = container.list_blobs(name_starts_with=prefix, include=["metadata"])
        except Exception as exc:  # noqa: BLE001 - normalize Azure SDK errors
            raise _storage_unavailable("Asset quota accounting failed") from exc
        now = datetime.now(UTC)
        for blob in blobs:
            metadata = dict(getattr(blob, "metadata", {}) or {})
            expires_at = _try_parse(metadata.get("expiresat"))
            if expires_at is not None and expires_at <= now:
                self._delete_expired(container, str(getattr(blob, "name", "")))
                continue
            count += 1
            total += int(getattr(blob, "size", 0) or 0)
        if count + 1 > self._quota_count or total + incoming_bytes > self._quota_bytes:
            raise quota_exceeded()

    @contextmanager
    def _quota_lease(self, principal: str, kind: AssetKind) -> Iterator[None]:
        lock_name = f"{_QUOTA_LOCK_PREFIX}/{principal_bucket(principal)}"
        client = self._container_client(kind).get_blob_client(lock_name)
        try:
            client.upload_blob(b"", overwrite=False)
        except Exception as exc:  # noqa: BLE001 - normalize Azure SDK errors
            if not _is_sdk_error(exc, "ResourceExistsError"):
                raise _storage_unavailable("Asset quota lock creation failed") from exc
        try:
            lease = client.acquire_lease(lease_duration=60)
        except Exception as exc:  # noqa: BLE001 - normalize Azure SDK errors
            raise _storage_unavailable("Asset quota lock is busy") from exc
        try:
            yield
        finally:
            try:
                lease.release()
            except Exception:  # noqa: BLE001 - lease expires without compromising quota safety
                LOGGER.warning("failed to release asset quota lease", exc_info=True)

    @staticmethod
    def _delete_expired(container: Any, blob_name: str) -> None:
        if not blob_name:
            raise _storage_unavailable("Asset quota metadata is malformed")
        try:
            container.get_blob_client(blob_name).delete_blob()
        except Exception as exc:  # noqa: BLE001 - normalize Azure SDK errors
            if not _is_sdk_error(exc, "ResourceNotFoundError"):
                raise _storage_unavailable("Expired asset cleanup failed") from exc


def _parse(value: object) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _try_parse(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _is_sdk_error(error: Exception, class_name: str) -> bool:
    return any(base.__name__ == class_name for base in type(error).__mro__)


def _storage_unavailable(message: str) -> VisionError:
    return VisionError(ErrorCode.PROVIDER_UNAVAILABLE, message, retryable=True)
