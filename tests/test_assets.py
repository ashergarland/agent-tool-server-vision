"""Asset isolation, TTL, quota, containment, and streaming behaviour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vision_server.assets import FilesystemAssetStore
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


class FakeBlob:
    def __init__(self, name: str, container: FakeContainer) -> None:
        self.name = name
        self.container = container

    def upload_blob(
        self, payload: bytes, overwrite: bool, content_settings: object, metadata: dict[str, str]
    ) -> None:
        assert overwrite is False
        self.container.blobs[self.name] = (bytes(payload), dict(metadata))

    def download_blob(self) -> FakeBlob:
        if self.name not in self.container.blobs:
            raise KeyError(self.name)
        return self

    def readall(self) -> bytes:
        return self.container.blobs[self.name][0]

    def get_blob_properties(self) -> FakeProperties:
        payload, metadata = self.container.blobs[self.name]
        return FakeProperties(metadata, len(payload))

    def delete_blob(self) -> None:
        self.container.blobs.pop(self.name, None)


class FakeProperties:
    def __init__(self, metadata: dict[str, str], size: int) -> None:
        self.metadata = metadata
        self.size = size
        self.content_settings = type("Settings", (), {"content_type": "image/png"})()


class FakeContainer:
    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, dict[str, str]]] = {}

    def get_blob_client(self, name: str) -> FakeBlob:
        return FakeBlob(name, self)

    def get_container_properties(self) -> dict[str, str]:
        return {}

    def list_blobs(self, name_starts_with: str) -> list[FakeProperties]:
        return [
            FakeProperties(metadata, len(payload))
            for name, (payload, metadata) in self.blobs.items()
            if name.startswith(name_starts_with)
        ]


def blob_store(container: FakeContainer, **overrides: int) -> AzureBlobAssetStore:
    values: dict[str, int] = {
        "ttl_seconds": 3600,
        "max_bytes": 4096,
        "quota_bytes": 100_000,
        "quota_count": 5,
    }
    values.update(overrides)
    return AzureBlobAssetStore(
        "https://example.blob.core.windows.net",
        "assets",
        client_factory=lambda: container,
        **values,
    )


async def test_blob_store_roundtrip_and_isolation() -> None:
    container = FakeContainer()
    subject = blob_store(container)
    record = await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    blob_name = next(iter(container.blobs))
    assert blob_name.startswith(principal_bucket(PRINCIPAL_A) + "/")
    fetched, payload = await subject.get(PRINCIPAL_A, record.asset_id)
    assert fetched.byte_count == len(payload)

    with pytest.raises(VisionError) as error:
        await subject.get(PRINCIPAL_B, record.asset_id)
    assert error.value.code is ErrorCode.NOT_FOUND

    assert await subject.health() == ("ok", None)
    assert await subject.purge_expired() == 0
    await subject.delete(PRINCIPAL_A, record.asset_id)
    assert not container.blobs


async def test_blob_store_enforces_quota() -> None:
    container = FakeContainer()
    subject = blob_store(container, quota_count=1)
    await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    with pytest.raises(VisionError) as error:
        await subject.put(PRINCIPAL_A, single_chunk(png_bytes()), "image/png")
    assert error.value.code is ErrorCode.QUOTA_EXCEEDED


async def test_blob_store_reports_missing_assets() -> None:
    subject = blob_store(FakeContainer())
    with pytest.raises(VisionError) as error:
        await subject.get(PRINCIPAL_A, "unknown-asset-id")
    assert error.value.code is ErrorCode.NOT_FOUND
