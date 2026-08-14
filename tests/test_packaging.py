"""Packaging checks: container hardening and portable, identity-based infrastructure."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
MAIN_BICEP = (ROOT / "infra" / "main.bicep").read_text()
CONTAINER_APP_BICEP = (ROOT / "infra" / "modules" / "container-app.bicep").read_text()
STORAGE_BICEP = (ROOT / "infra" / "modules" / "storage.bicep").read_text()
CONTENT_UNDERSTANDING_BICEP = (ROOT / "infra" / "modules" / "content-understanding.bicep").read_text()
INFRA_FILES = sorted((ROOT / "infra").rglob("*.bicep*"))


def test_container_runs_unprivileged_with_a_pinned_base_image() -> None:
    assert DOCKERFILE.count("FROM python:3.11.13-slim-bookworm") == 2
    assert "USER 10001:10001" in DOCKERFILE
    assert DOCKERFILE.index("USER 10001:10001") < DOCKERFILE.index("CMD [")
    assert "HEALTHCHECK" in DOCKERFILE and "/health" in DOCKERFILE


def test_container_separates_dependency_and_application_layers() -> None:
    dependency_layer = DOCKERFILE.index("COPY pyproject.toml")
    application_layer = DOCKERFILE.index("COPY src ./src")
    assert dependency_layer < application_layer


def test_container_declares_only_explicit_writable_locations() -> None:
    assert 'VOLUME ["/tmp", "/home/vision/.cache"]' in DOCKERFILE
    assert "read-only root filesystem" in DOCKERFILE
    assert "VISION_ASSET_ROOT=/tmp/vision-assets" in DOCKERFILE


def test_container_defaults_to_production_settings() -> None:
    assert "VISION_ENVIRONMENT=production" in DOCKERFILE


def test_storage_is_private_with_lifecycle_matching_the_asset_ttl() -> None:
    assert "allowBlobPublicAccess: false" in STORAGE_BICEP
    assert "allowSharedKeyAccess: false" in STORAGE_BICEP
    assert "minimumTlsVersion: 'TLS1_2'" in STORAGE_BICEP
    assert STORAGE_BICEP.count("publicAccess: 'None'") == 2
    assert "daysAfterCreationGreaterThan: assetRetentionDays" in STORAGE_BICEP
    assert "assetRetentionDays = max(1, assetTtlSeconds / 86400)" in MAIN_BICEP


def test_infrastructure_grants_least_privilege_managed_identity_access() -> None:
    # Storage Blob Data Contributor on the storage account only.
    assert "ba92f5b4-2d11-453d-a403-e96b0029c9fe" in STORAGE_BICEP
    assert "scope: storage" in STORAGE_BICEP
    # Cognitive Services User on the Content Understanding account only.
    assert "a97b65f3-24c7-4388-baec-2e87135dc908" in CONTENT_UNDERSTANDING_BICEP
    assert "disableLocalAuth: true" in CONTENT_UNDERSTANDING_BICEP
    assert "identity: {" in CONTAINER_APP_BICEP
    assert "AZURE_CLIENT_ID" in CONTAINER_APP_BICEP
    assert "connectionString" not in STORAGE_BICEP
    assert "listKeys" not in STORAGE_BICEP + CONTENT_UNDERSTANDING_BICEP


def test_key_vault_is_used_only_for_the_api_key_secret() -> None:
    assert CONTAINER_APP_BICEP.count("keyVaultUrl") == 1
    assert "secretRef: 'api-key'" in CONTAINER_APP_BICEP


def test_container_app_defaults_and_probes() -> None:
    assert "param cpu string = '2'" in MAIN_BICEP
    assert "param memory string = '4Gi'" in MAIN_BICEP
    assert "param minReplicas int = 0" in MAIN_BICEP
    assert "param maxReplicas int = 5" in MAIN_BICEP
    assert "param httpConcurrency int = 10" in MAIN_BICEP
    assert "concurrentRequests: string(httpConcurrency)" in CONTAINER_APP_BICEP
    liveness = CONTAINER_APP_BICEP.index("'Liveness'")
    readiness = CONTAINER_APP_BICEP.index("'Readiness'")
    assert "/health" in CONTAINER_APP_BICEP[liveness : liveness + 200]
    assert "/ready" in CONTAINER_APP_BICEP[readiness : readiness + 200]


def test_deferred_capabilities_are_not_provisioned() -> None:
    combined = "".join(path.read_text().lower() for path in INFRA_FILES)
    assert "machinelearningservices" not in combined
    assert "imageanalysis" not in combined
    assert "computervision" not in combined


@pytest.mark.parametrize("path", INFRA_FILES, ids=lambda path: path.name)
def test_infrastructure_is_account_neutral(path: Path) -> None:
    text = path.read_text()
    # Built-in role definition GUIDs are identical in every tenant; any other GUID
    # would bind this repository to a specific subscription, tenant, or principal.
    tenant_specific = [
        line
        for line in text.splitlines()
        if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", line)
        and "RoleId" not in line
    ]
    assert tenant_specific == []
    assert "subscriptionId" not in text
    assert not re.search(r"tenantId:\s*'", text)
    for region in ("eastus", "westeurope", "westus", "northeurope"):
        assert region not in text.lower()
    for host in (".azurecr.io/", ".blob.core.windows.net", "cognitiveservices.azure.com"):
        assert host not in text


def test_deployment_parameters_use_obvious_placeholders() -> None:
    parameters = (ROOT / "infra" / "parameters" / "dev.bicepparam").read_text()
    assert "replace.invalid" in parameters
    assert "location" not in parameters


def test_ci_runs_the_declared_gates() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for command in ("ruff check .", "ruff format --check .", "mypy", "pytest"):
        assert command in workflow
    assert "az bicep build" in workflow and "az bicep lint" in workflow
    assert "/ready" in workflow and "/mcp" in workflow
    assert "openapi.json" in workflow


def test_openapi_snapshot_script_is_available() -> None:
    script = ROOT / "scripts" / "check_openapi.py"
    assert script.exists()
    snapshot = json.loads((ROOT / "docs" / "openapi.json").read_text())
    assert snapshot["openapi"].startswith("3.1")
    assert set(snapshot["paths"]) >= {
        "/tools/extract_text_and_layout",
        "/tools/compare_images",
        "/tools/optimize_image_region",
    }
