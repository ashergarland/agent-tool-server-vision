"""Configuration validation, including production fail-fast behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from vision_server.config import Environment, ProviderMode, Settings, StorageBackend


def base(**overrides: object) -> Settings:
    values: dict[str, object] = {"_env_file": None}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_defaults_are_local_only() -> None:
    settings = base()
    assert settings.provider_mode is ProviderMode.LOCAL
    assert settings.storage_backend is StorageBackend.FILESYSTEM
    assert settings.environment is Environment.DEVELOPMENT
    assert settings.max_image_pixels == 40_000_000
    assert settings.public_summary()["providerMode"] == "local"


def test_azure_provider_requires_endpoint() -> None:
    with pytest.raises(ValidationError):
        base(provider_mode="azure")
    settings = base(
        provider_mode="azure",
        azure_content_understanding_endpoint="https://example-endpoint.invalid",
    )
    assert settings.azure_content_understanding_api_version == "2025-11-01"


def test_endpoints_must_use_https() -> None:
    with pytest.raises(ValidationError):
        base(provider_mode="azure", azure_content_understanding_endpoint="http://insecure.invalid")


def test_blob_backend_requires_account_and_container() -> None:
    with pytest.raises(ValidationError):
        base(storage_backend="azure_blob")
    settings = base(
        storage_backend="azure_blob",
        storage_account_url="https://example.blob.core.windows.net",
        asset_container="assets",
    )
    assert settings.storage_backend is StorageBackend.AZURE_BLOB


def test_production_fails_fast_without_auth() -> None:
    with pytest.raises(ValidationError):
        base(environment="production", api_keys="")
    with pytest.raises(ValidationError):
        base(environment="production", auth_enabled=False, api_keys="k")
    settings = base(environment="production", api_keys="key-one,key-two")
    assert len(settings.api_key_digests) == 2


def test_language_allow_list_is_enforced() -> None:
    with pytest.raises(ValidationError):
        base(paddle_languages="klingon")
    with pytest.raises(ValidationError):
        base(default_language="ch", paddle_languages="en")


def test_allowed_roots_resolve_existing_directories(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    settings = base(allowed_roots=f"{first},{second}")
    assert set(settings.allowed_root_paths) == {first.resolve(), second.resolve()}
    colon_settings = base(allowed_roots=f"{first}:{second}")
    assert set(colon_settings.allowed_root_paths) == {first.resolve(), second.resolve()}
