"""HTTP transport: authentication, error model, assets, health, and OpenAPI."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vision_server.config import Settings
from vision_server.registry import TOOLS
from vision_server.runtime import Runtime
from vision_server.transports.http import create_app

from .conftest import API_KEY, local_reference, png_bytes, write_png


def test_health_and_openapi_document(client: TestClient) -> None:
    health = client.get("/health").json()
    assert health["status"] == "ok"

    document = client.get("/openapi.json").json()
    assert document["openapi"].startswith("3.1")
    for tool in TOOLS:
        operation = document["paths"][tool.http_path]["post"]
        assert operation["operationId"] == tool.name
        assert operation["description"] == tool.description
        assert operation["x-mcp-annotations"] == tool.annotations.as_dict()


def test_readiness_reports_components(client: TestClient) -> None:
    body = client.get("/ready").json()
    assert body["status"] == "ready"
    assert body["tools"] == [tool.name for tool in TOOLS]
    names = {component["name"] for component in body["components"]}
    assert {"registry", "storage", "provider:local_paddleocr"} <= names
    assert body["configuration"]["environment"] == "development"


def test_authentication_is_required(runtime: Runtime, allowed_root: Path) -> None:
    with TestClient(create_app(runtime=runtime)) as anonymous:
        path = write_png(allowed_root / "image.png")
        response = anonymous.post(
            "/tools/compare_images",
            json={"before": local_reference(path), "after": local_reference(path)},
        )
        assert response.status_code == 401
        body = response.json()["error"]
        assert body["code"] == "unauthorized"
        assert body["retryable"] is False
        assert body["requestId"]

        wrong = anonymous.post(
            "/tools/compare_images",
            headers={"authorization": "Bearer " + API_KEY[:-1]},
            json={"before": local_reference(path), "after": local_reference(path)},
        )
        assert wrong.status_code == 401

        api_key = anonymous.post(
            "/tools/compare_images",
            headers={"x-api-key": API_KEY},
            json={"before": local_reference(path), "after": local_reference(path)},
        )
        assert api_key.status_code == 200


def test_authentication_can_be_disabled_only_outside_production(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        auth_enabled=False,
        asset_root=str(tmp_path / "assets"),
    )
    with TestClient(create_app(runtime=Runtime(settings))) as anonymous:
        assert anonymous.get("/ready").status_code == 200


def test_tool_errors_use_the_shared_error_model(client: TestClient, allowed_root: Path) -> None:
    outside = allowed_root.parent / "outside.png"
    write_png(outside)
    response = client.post(
        "/tools/optimize_image_region",
        json={
            "image": {"kind": "local_path", "path": str(outside)},
            "box": {"x": 0, "y": 0, "width": 8, "height": 8},
        },
    )
    assert response.status_code == 403
    body = response.json()["error"]
    assert body["code"] == "forbidden"
    assert str(outside) not in response.text


@pytest.mark.parametrize(
    "image",
    [
        {"kind": "base64", "data": "aGk="},
        {"kind": "url", "url": "https://example.invalid/a.png"},
        "https://example.invalid/a.png",
        {"kind": "asset", "assetId": "../escape"},
    ],
)
def test_rejects_unsupported_image_references(client: TestClient, image: object) -> None:
    response = client.post(
        "/tools/extract_text_and_layout",
        json={"image": image},
    )
    assert response.status_code == 422


def test_rejects_oversized_json_bodies(client: TestClient, allowed_root: Path) -> None:
    path = write_png(allowed_root / "image.png")
    payload = {
        "image": local_reference(path),
        "language": "en" * 4000,
    }
    response = client.post("/tools/extract_text_and_layout", json=payload)
    assert response.status_code == 422

    runtime: Runtime = client.app.state.runtime  # type: ignore[attr-defined]
    runtime.settings.__dict__["max_json_bytes"] = 1024
    big = client.post(
        "/tools/extract_text_and_layout",
        json={"image": local_reference(path), "language": "e" * 5000},
    )
    assert big.status_code == 413
    assert big.json()["error"]["code"] == "payload_too_large"
    runtime.settings.__dict__["max_json_bytes"] = 1_000_000


def test_asset_upload_download_and_isolation(client: TestClient) -> None:
    payload = png_bytes(16, 16)
    created = client.post("/assets", content=payload, headers={"content-type": "image/png"})
    assert created.status_code == 201
    asset_id = created.json()["assetId"]
    assert created.json()["byteCount"] == len(payload)

    fetched = client.get(f"/assets/{asset_id}")
    assert fetched.status_code == 200
    assert fetched.content == payload
    assert fetched.headers["cache-control"] == "no-store"

    used = client.post(
        "/tools/optimize_image_region",
        json={
            "image": {"kind": "asset", "assetId": asset_id},
            "box": {"x": 0, "y": 0, "width": 8, "height": 8},
        },
    )
    assert used.status_code == 200
    assert "assets/" not in used.text

    other = client.post(
        "/assets", content=payload, headers={"content-type": "image/png", "x-api-key": API_KEY}
    )
    assert other.status_code == 201

    assert client.delete(f"/assets/{asset_id}").status_code == 204
    assert client.get(f"/assets/{asset_id}").status_code == 404


def test_asset_upload_rejects_unsupported_types(client: TestClient) -> None:
    response = client.post(
        "/assets", content=b"%PDF-1.4", headers={"content-type": "application/pdf"}
    )
    assert response.status_code == 415


def test_request_id_header_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"x-request-id": "abc123"})
    assert response.headers["x-request-id"] == "abc123"
