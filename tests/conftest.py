"""Shared fixtures. Tests never require Azure, network access, or model weights."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from vision_server.assets import FilesystemAssetStore
from vision_server.concurrency import WorkQueue
from vision_server.config import Settings
from vision_server.imaging import LoadedImage
from vision_server.providers.base import OcrBlock, OcrResult
from vision_server.providers.router import OcrRouter
from vision_server.runtime import Runtime, ToolContext
from vision_server.security import ANONYMOUS_PRINCIPAL
from vision_server.transports.http import create_app

API_KEY = "test-key-abcdef"


class FakeOcrProvider:
    """Deterministic OCR stand-in used instead of PaddleOCR or Azure."""

    def __init__(
        self,
        name: str,
        *,
        blocks: tuple[OcrBlock, ...] | None = None,
        error: Exception | None = None,
        markdown: str | None = None,
        model: str | None = "fake-model",
        api_version: str | None = None,
        health_status: tuple[str, str | None] = ("ok", None),
    ) -> None:
        self.name = name
        self.calls: list[str] = []
        self._blocks = blocks if blocks is not None else _default_blocks()
        self._error = error
        self._markdown = markdown
        self._model = model
        self._api_version = api_version
        self._health = health_status

    async def analyze(self, image: LoadedImage, language: str) -> OcrResult:
        self.calls.append(language)
        if self._error is not None:
            raise self._error
        return OcrResult(
            blocks=self._blocks,
            provider_name=self.name,
            model=self._model,
            api_version=self._api_version,
            markdown=self._markdown,
        )

    async def health(self) -> tuple[str, str | None]:
        return self._health


def _default_blocks() -> tuple[OcrBlock, ...]:
    return (
        OcrBlock("second", "line", 1, (20.0, 20.0, 50.0, 20.0, 50.0, 30.0, 20.0, 30.0), 0.8),
        OcrBlock("first", "line", 1, (10.0, 2.0, 40.0, 2.0, 40.0, 12.0, 10.0, 12.0), 0.95),
    )


def write_png(path: Path, width: int = 64, height: int = 32, color: str = "white") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), color).save(path, format="PNG")
    return path


def png_bytes(width: int = 8, height: int = 8, color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def loaded_image(width: int = 64, height: int = 32) -> LoadedImage:
    return LoadedImage(
        image=Image.new("RGB", (width, height), "white"),
        content_type="image/png",
        byte_count=128,
        source_kind="local_path",
    )


async def single_chunk(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


@pytest.fixture
def allowed_root(tmp_path: Path) -> Path:
    root = tmp_path / "images"
    root.mkdir()
    return root


@pytest.fixture
def settings(tmp_path: Path, allowed_root: Path) -> Settings:
    return Settings(
        allowed_roots=str(allowed_root),
        asset_root=str(tmp_path / "assets"),
        api_keys=API_KEY,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def asset_store(settings: Settings) -> FilesystemAssetStore:
    return FilesystemAssetStore(
        settings.filesystem_asset_root,
        ttl_seconds=settings.asset_ttl_seconds,
        max_bytes=settings.asset_max_bytes,
        quota_bytes=settings.asset_quota_bytes,
        quota_count=settings.asset_quota_count,
    )


@pytest.fixture
def local_provider() -> FakeOcrProvider:
    return FakeOcrProvider("local_paddleocr")


@pytest.fixture
def azure_provider() -> FakeOcrProvider:
    return FakeOcrProvider("azure_content_understanding", api_version="2025-11-01")


@pytest.fixture
def runtime(
    settings: Settings,
    asset_store: FilesystemAssetStore,
    local_provider: FakeOcrProvider,
    azure_provider: FakeOcrProvider,
) -> Runtime:
    router = OcrRouter(settings, local=local_provider, azure=azure_provider)
    return Runtime(
        settings,
        asset_store=asset_store,
        router=router,
        queue=WorkQueue(
            settings.max_concurrency,
            settings.max_queue_depth,
            settings.operation_timeout_seconds,
        ),
    )


@pytest.fixture
def context(runtime: Runtime) -> ToolContext:
    return ToolContext(runtime=runtime, principal=ANONYMOUS_PRINCIPAL, request_id="test-request")


@pytest.fixture
def client(runtime: Runtime) -> Iterator[TestClient]:
    app = create_app(runtime=runtime)
    with TestClient(app) as test_client:
        test_client.headers.update({"authorization": "Bearer " + API_KEY})
        yield test_client


def local_reference(path: Path) -> dict[str, Any]:
    return {"kind": "local_path", "path": str(path)}
