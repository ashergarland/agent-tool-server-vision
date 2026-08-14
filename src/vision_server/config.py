"""Runtime configuration.

Every value is environment driven with the ``VISION_`` prefix so that the public
repository never embeds account, tenant, or region specific defaults.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"en", "ch", "fr", "german", "japan", "korean"})


class Environment(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class ProviderMode(StrEnum):
    """Configured OCR provider preference."""

    LOCAL = "local"
    AZURE = "azure"
    AUTO = "auto"


class StorageBackend(StrEnum):
    FILESYSTEM = "filesystem"
    AZURE_BLOB = "azure_blob"


class Settings(BaseSettings):
    """Validated process configuration."""

    model_config = SettingsConfigDict(env_prefix="VISION_", env_file=".env", extra="ignore")

    # Service identity
    service_name: str = "agent-tool-server-vision"
    service_version: str = "0.2.0"
    environment: Environment = Environment.DEVELOPMENT

    # Authentication
    auth_enabled: bool = True
    api_keys: str = ""

    # Image input limits
    allowed_roots: str = ""
    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    max_image_pixels: int = Field(default=40_000_000, ge=1024, le=200_000_000)
    max_json_bytes: int = Field(default=1_000_000, ge=1024, le=8 * 1024 * 1024)

    # OCR providers
    provider_mode: ProviderMode = ProviderMode.LOCAL
    default_language: str = "en"
    paddle_languages: str = "en"
    paddle_cache_size: int = Field(default=2, ge=1, le=8)
    azure_content_understanding_endpoint: str = ""
    azure_content_understanding_api_version: str = "2025-11-01"
    azure_content_understanding_analyzer: str = "prebuilt-documentAnalyzer"

    # Assets
    storage_backend: StorageBackend = StorageBackend.FILESYSTEM
    asset_root: str = ""
    asset_container: str = ""
    artifact_container: str = ""
    storage_account_url: str = ""
    asset_ttl_seconds: int = Field(default=3600, ge=60, le=7 * 24 * 3600)
    asset_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    asset_quota_bytes: int = Field(default=256 * 1024 * 1024, ge=1024)
    asset_quota_count: int = Field(default=200, ge=1, le=10_000)

    # Concurrency and timeouts
    max_concurrency: int = Field(default=4, ge=1, le=64)
    max_queue_depth: int = Field(default=32, ge=1, le=1024)
    operation_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    provider_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    shutdown_grace_seconds: float = Field(default=15.0, ge=0, le=120)

    # Observability
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_payload_metadata: bool = False

    @field_validator("default_language")
    @classmethod
    def _known_default_language(cls, value: str) -> str:
        if value not in SUPPORTED_LANGUAGES:
            raise ValueError(f"default_language must be one of {sorted(SUPPORTED_LANGUAGES)}")
        return value

    @field_validator("azure_content_understanding_endpoint", "storage_account_url")
    @classmethod
    def _https_endpoint(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith("https://"):
            raise ValueError("endpoints must use https")
        return value

    @model_validator(mode="after")
    def _validate_combination(self) -> Settings:
        if self.provider_mode in (ProviderMode.AZURE, ProviderMode.AUTO):
            if not self.azure_content_understanding_endpoint:
                raise ValueError(
                    "VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT is required for this provider mode"
                )
        if self.storage_backend is StorageBackend.AZURE_BLOB:
            if not self.storage_account_url or not self.asset_container:
                raise ValueError(
                    "VISION_STORAGE_ACCOUNT_URL and VISION_ASSET_CONTAINER are required for "
                    "the azure_blob storage backend"
                )
        if self.is_production:
            if not self.auth_enabled:
                raise ValueError("authentication cannot be disabled in production")
            if not self.api_key_digests:
                raise ValueError("VISION_API_KEYS must be set in production")
        if not self.allowed_language_set <= SUPPORTED_LANGUAGES:
            raise ValueError(f"paddle_languages must be a subset of {sorted(SUPPORTED_LANGUAGES)}")
        if self.default_language not in self.allowed_language_set:
            raise ValueError("default_language must be present in paddle_languages")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def allowed_language_set(self) -> frozenset[str]:
        return frozenset(_split(self.paddle_languages)) or frozenset({"en"})

    @property
    def api_key_digests(self) -> tuple[str, ...]:
        from .security import digest_secret

        return tuple(digest_secret(key) for key in _split(self.api_keys))

    @property
    def allowed_root_paths(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        for raw in _split(self.allowed_roots, separator=_root_separator(self.allowed_roots)):
            try:
                roots.append(Path(raw).resolve(strict=True))
            except OSError:  # pragma: no cover - depends on host filesystem
                continue
        return tuple(roots)

    @property
    def filesystem_asset_root(self) -> Path:
        root = self.asset_root or "./.vision-assets"
        return Path(root).expanduser().resolve()

    def public_summary(self) -> dict[str, Any]:
        """Non-sensitive configuration summary safe for readiness output."""
        return {
            "environment": self.environment.value,
            "providerMode": self.provider_mode.value,
            "storageBackend": self.storage_backend.value,
            "authEnabled": self.auth_enabled,
            "maxConcurrency": self.max_concurrency,
            "assetTtlSeconds": self.asset_ttl_seconds,
        }


def _split(value: str, separator: str = ",") -> list[str]:
    """Split a delimited environment value, ignoring blanks."""
    if not value:
        return []
    return [part.strip() for part in value.split(separator) if part.strip()]


def _root_separator(value: str) -> str:
    """Allow either ``:`` (POSIX path list) or ``,`` for allowed roots."""
    return ":" if ":" in value and "," not in value else ","
