"""Process runtime: configuration, storage, providers, and admission control."""

from __future__ import annotations

from dataclasses import dataclass

from .assets import AssetStore, AzureBlobAssetStore, FilesystemAssetStore
from .concurrency import WorkQueue
from .config import Settings, StorageBackend
from .providers import OcrRouter


def build_asset_store(settings: Settings) -> AssetStore:
    """Filesystem locally, private Azure Blob Storage when hosted."""
    if settings.storage_backend is StorageBackend.AZURE_BLOB:
        return AzureBlobAssetStore(
            settings.storage_account_url,
            settings.asset_container,
            settings.artifact_container or settings.asset_container,
            ttl_seconds=settings.asset_ttl_seconds,
            max_bytes=settings.asset_max_bytes,
            quota_bytes=settings.asset_quota_bytes,
            quota_count=settings.asset_quota_count,
        )
    return FilesystemAssetStore(
        settings.filesystem_asset_root,
        ttl_seconds=settings.asset_ttl_seconds,
        max_bytes=settings.asset_max_bytes,
        quota_bytes=settings.asset_quota_bytes,
        quota_count=settings.asset_quota_count,
    )


class Runtime:
    """Shared, transport independent dependencies."""

    def __init__(
        self,
        settings: Settings,
        *,
        asset_store: AssetStore | None = None,
        router: OcrRouter | None = None,
        queue: WorkQueue | None = None,
    ) -> None:
        self.settings = settings
        self.assets: AssetStore = asset_store or build_asset_store(settings)
        self.router = router or OcrRouter(settings)
        self.queue = queue or WorkQueue(
            settings.max_concurrency,
            settings.max_queue_depth,
            settings.operation_timeout_seconds,
        )

    async def shutdown(self) -> None:
        await self.queue.drain(self.settings.shutdown_grace_seconds)


@dataclass(frozen=True)
class ToolContext:
    """Per-call context handed to every tool handler."""

    runtime: Runtime
    principal: str
    request_id: str

    @property
    def settings(self) -> Settings:
        return self.runtime.settings

    @property
    def assets(self) -> AssetStore:
        return self.runtime.assets

    @property
    def router(self) -> OcrRouter:
        return self.runtime.router
