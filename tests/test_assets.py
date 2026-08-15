"""Asset isolation, TTL, quota, containment, and streaming behaviour."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vision_server.assets import AssetKind, FilesystemAssetStore
from vision_server.assets.blob import AzureBlobAssetStore
from vision_server.errors import ErrorCode, VisionError
from vision_server.security import principal_bucket

from .conftest import png_bytes, single_chunk

PRINCIPAL_A = "p_alpha"
PRINCIPAL_B = "p_beta"


def store(tmp_path: Path, **overrides: int) -> FilesystemAssetStore:
    values: dict[str, int] = {
        "ttl_seconds": 3600,
        "max_bytes": 4096,
        "quota_bytes": 100_000,
        "quota_count": 5,
    }
    values.update(overrides)
    return FilesystemAssetStore(tmp_path / "assets", **values)


async def test_roundtrip_and_containment(tmp_path: Path) -> None:
    subject = store(tmp_path)
    payload = png_bytes()
    record = await subject.put(PRINCIPAL_A, single_chunk(payload), "image/png")
    assert record.asset_id.isascii() and len(record.asset_id) >= 16
    fetched, data = await subject.get(PRINCIPAL_A, record.asset_id)
    assert data == payload
    assert fetched.content_type == "image/png"
    stored = list((tmp_path / "assets").rglob("*.bin"))
    assert len(stored) == 1
    assert stored[0].parent.name == principal_bucket(PRINCIPAL_A)
    assert record.asset_id not in str(stored[0].parent)


async def test_assets_are_principal_scoped(tmp_path: Path) -> None:
    subject = store(tmp_path)
    record = await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    with pytest.raises(VisionError) as error:
        await subject.get(PRINCIPAL_B, record.asset_id)
    assert error.value.code is ErrorCode.NOT_FOUND


@pytest.mark.parametrize("asset_id", ["../escape", "..%2fescape", "a/b", "", "."])
async def test_identifier_traversal_is_rejected(tmp_path: Path, asset_id: str) -> None:
    subject = store(tmp_path)
    with pytest.raises(VisionError) as error:
        await subject.get(PRINCIPAL_A, asset_id)
    assert error.value.code is ErrorCode.NOT_FOUND


async def test_size_limit_and_content_type(tmp_path: Path) -> None:
    subject = store(tmp_path, max_bytes=1024)
    with pytest.raises(VisionError) as too_large:
        await subject.put(PRINCIPAL_A, single_chunk(b"x" * 2048), "image/png")
    assert too_large.value.code is ErrorCode.PAYLOAD_TOO_LARGE

    with pytest.raises(VisionError) as unsupported:
        await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "application/pdf")
    assert unsupported.value.code is ErrorCode.UNSUPPORTED_MEDIA

    with pytest.raises(VisionError) as empty:
        await subject.put(PRINCIPAL_A, single_chunk(b""), "image/png")
    assert empty.value.code is ErrorCode.INVALID_INPUT


async def test_quota_by_count_and_bytes(tmp_path: Path) -> None:
    counted = store(tmp_path / "count", quota_count=1)
    await counted.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    with pytest.raises(VisionError) as count_error:
        await counted.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    assert count_error.value.code is ErrorCode.QUOTA_EXCEEDED

    sized = store(tmp_path / "bytes", quota_bytes=1200)
    await sized.put(PRINCIPAL_A, single_chunk(png_bytes(64, 64, "red")), "image/png")
    with pytest.raises(VisionError) as byte_error:
        await sized.put(PRINCIPAL_A, single_chunk(b"\x89PNG" + b"0" * 1200), "image/png")
    assert byte_error.value.code is ErrorCode.QUOTA_EXCEEDED


async def test_expired_assets_are_rejected_and_purged(tmp_path: Path) -> None:
    subject = store(tmp_path, ttl_seconds=60)
    record = await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    meta_path = next((tmp_path / "assets").rglob("*.json"))
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    meta_path.write_text(meta_path.read_text().replace(record.expires_at.isoformat(), expired))

    with pytest.raises(VisionError) as error:
        await subject.get(PRINCIPAL_A, record.asset_id)
    assert error.value.code is ErrorCode.NOT_FOUND
    assert await subject.purge_expired() == 1
    assert not list((tmp_path / "assets").rglob("*.bin"))


async def test_delete_and_health(tmp_path: Path) -> None:
    subject = store(tmp_path)
    record = await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    await subject.delete(PRINCIPAL_A, record.asset_id)
    with pytest.raises(VisionError):
        await subject.get(PRINCIPAL_A, record.asset_id)
    assert await subject.health() == ("ok", None)
    assert await subject.purge_expired() >= 0


class ResourceExistsError(Exception):
    pass


class ResourceNotFoundError(Exception):
    pass


class FakeLease:
    def __init__(self, lock: threading.Lock) -> None:
        self._lock = lock

    def release(self) -> None:
        self._lock.release()


class FakeBlob:
    def __init__(self, name: str, container: FakeContainer) -> None:
        self.name = name
        self.container = container

    def upload_blob(
        self,
        payload: bytes,
        overwrite: bool,
        content_settings: object = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        assert overwrite is False
        if self.name in self.container.blobs:
            raise ResourceExistsError(self.name)
        self.container.blobs[self.name] = (bytes(payload), dict(metadata or {}))

    def download_blob(self) -> FakeBlob:
        if self.name not in self.container.blobs:
            raise KeyError(self.name)
        return self

    def readall(self) -> bytes:
        return self.container.blobs[self.name][0]

    def get_blob_properties(self) -> FakeProperties:
        payload, metadata = self.container.blobs[self.name]
        return FakeProperties(self.name, metadata, len(payload))

    def delete_blob(self) -> None:
        if self.container.delete_error is not None:
            raise self.container.delete_error
        if self.name not in self.container.blobs:
            raise ResourceNotFoundError(self.name)
        del self.container.blobs[self.name]

    def acquire_lease(self, lease_duration: int) -> FakeLease:
        assert lease_duration == 60
        if not self.container.quota_lock.acquire(blocking=False):
            raise RuntimeError("lease is already held")
        return FakeLease(self.container.quota_lock)


class FakeProperties:
    def __init__(self, name: str, metadata: dict[str, str], size: int) -> None:
        self.name = name
        self.metadata = metadata
        self.size = size
        self.content_settings = type("Settings", (), {"content_type": "image/png"})()


class FakeContainer:
    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.delete_error: Exception | None = None
        self.list_error: Exception | None = None
        self.quota_lock = threading.Lock()

    def get_blob_client(self, name: str) -> FakeBlob:
        return FakeBlob(name, self)

    def get_container_properties(self) -> dict[str, str]:
        return {}

    def list_blobs(
        self, name_starts_with: str, include: list[str] | None = None
    ) -> list[FakeProperties]:
        assert include == ["metadata"]
        if self.list_error is not None:
            raise self.list_error
        return [
            FakeProperties(name, metadata, len(payload))
            for name, (payload, metadata) in self.blobs.items()
            if name.startswith(name_starts_with)
        ]


def blob_store(
    container: FakeContainer,
    artifacts: FakeContainer | None = None,
    **overrides: int,
) -> AzureBlobAssetStore:
    containers = {"vision-input": container, "vision-artifacts": artifacts or container}
    values: dict[str, int] = {
        "ttl_seconds": 3600,
        "max_bytes": 4096,
        "quota_bytes": 100_000,
        "quota_count": 5,
    }
    values.update(overrides)
    return AzureBlobAssetStore(
        "https://example.blob.core.windows.net",
        "vision-input",
        "vision-artifacts",
        client_factory=lambda name: containers[name],
        **values,
    )


def asset_blobs(container: FakeContainer) -> dict[str, tuple[bytes, dict[str, str]]]:
    return {
        name: value for name, value in container.blobs.items() if not name.startswith("_quota/")
    }


async def test_blob_store_roundtrip_and_isolation() -> None:
    container = FakeContainer()
    subject = blob_store(container)
    record = await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    blob_name = next(iter(asset_blobs(container)))
    assert blob_name.startswith(principal_bucket(PRINCIPAL_A) + "/")
    fetched, payload = await subject.get(PRINCIPAL_A, record.asset_id)
    assert fetched.byte_count == len(payload)

    with pytest.raises(VisionError) as error:
        await subject.get(PRINCIPAL_B, record.asset_id)
    assert error.value.code is ErrorCode.NOT_FOUND

    assert await subject.health() == ("ok", None)
    assert await subject.purge_expired() == 0
    await subject.delete(PRINCIPAL_A, record.asset_id)
    assert not asset_blobs(container)


async def test_blob_store_enforces_quota() -> None:
    container = FakeContainer()
    subject = blob_store(container, quota_count=1)
    await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    with pytest.raises(VisionError) as error:
        await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    assert error.value.code is ErrorCode.QUOTA_EXCEEDED


async def test_blob_store_excludes_and_deletes_expired_assets_from_quota() -> None:
    container = FakeContainer()
    subject = blob_store(container, quota_count=1)
    await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    asset_name = next(iter(asset_blobs(container)))
    payload, metadata = container.blobs[asset_name]
    metadata["expiresat"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    container.blobs[asset_name] = (payload, metadata)

    replacement = await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    assert replacement.asset_id in next(iter(asset_blobs(container)))
    assert len(asset_blobs(container)) == 1


async def test_blob_store_fails_closed_when_quota_cannot_be_checked() -> None:
    container = FakeContainer()
    container.list_error = RuntimeError("storage unavailable")
    subject = blob_store(container)
    with pytest.raises(VisionError) as error:
        await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    assert error.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert error.value.retryable is True
    assert not asset_blobs(container)


async def test_blob_store_surfaces_deletion_failures() -> None:
    container = FakeContainer()
    subject = blob_store(container)
    record = await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    container.delete_error = RuntimeError("storage unavailable")
    with pytest.raises(VisionError) as error:
        await subject.delete(PRINCIPAL_A, record.asset_id)
    assert error.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert error.value.retryable is True
    assert asset_blobs(container)


async def test_blob_store_separates_inputs_from_artifacts() -> None:
    inputs = FakeContainer()
    artifacts = FakeContainer()
    subject = blob_store(inputs, artifacts)

    uploaded = await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    generated = await subject.put(
        PRINCIPAL_A, single_chunk(png_bytes()), "image/png", AssetKind.ARTIFACT
    )
    assert len(asset_blobs(inputs)) == 1
    assert len(asset_blobs(artifacts)) == 1
    assert uploaded.asset_id.startswith("i")
    assert generated.asset_id.startswith("a")

    for asset_id in (uploaded.asset_id, generated.asset_id):
        record, payload = await subject.get(PRINCIPAL_A, asset_id)
        assert record.byte_count == len(payload)

    await subject.delete(PRINCIPAL_A, generated.asset_id)
    assert not asset_blobs(artifacts)
    assert asset_blobs(inputs)


async def test_blob_store_reports_missing_assets() -> None:
    subject = blob_store(FakeContainer())
    with pytest.raises(VisionError) as error:
        await subject.get(PRINCIPAL_A, "iunknown-asset-id")
    assert error.value.code is ErrorCode.NOT_FOUND
