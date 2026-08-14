"""MCP transport behaviour and cross-transport parity with HTTP/OpenAPI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from vision_server.registry import SERVER_INSTRUCTIONS, TOOLS
from vision_server.transports.mcp import _mcp_tools

from .conftest import API_KEY, local_reference, write_png

MCP_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def rpc(client: TestClient, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    response = client.post("/mcp/", json=body, headers=MCP_HEADERS)
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = json.loads(response.text)["result"]
    return payload


def initialize(client: TestClient) -> dict[str, Any]:
    return rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "tests", "version": "1"},
        },
    )


def test_mcp_advertises_instructions_and_registry_tools(client: TestClient) -> None:
    result = initialize(client)
    assert result["instructions"] == SERVER_INSTRUCTIONS
    assert "OCR" in SERVER_INSTRUCTIONS

    listed = rpc(client, "tools/list")["tools"]
    assert [tool["name"] for tool in listed] == [tool.name for tool in TOOLS]
    for advertised, definition in zip(listed, TOOLS, strict=True):
        assert advertised["description"] == definition.description
        assert advertised["inputSchema"] == definition.input_schema()
        assert advertised["outputSchema"] == definition.output_schema()
        assert advertised["annotations"]["readOnlyHint"] is definition.annotations.read_only_hint


def test_mcp_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={**MCP_HEADERS, "authorization": "Bearer " + API_KEY[:-1]},
    )
    assert response.status_code == 401


def test_mcp_call_tool_returns_structured_content(client: TestClient, allowed_root: Path) -> None:
    initialize(client)
    path = write_png(allowed_root / "image.png", 40, 20)
    result = rpc(
        client,
        "tools/call",
        {
            "name": "optimize_image_region",
            "arguments": {
                "image": local_reference(path),
                "box": {"x": 0, "y": 0, "width": 10, "height": 10},
            },
        },
    )
    assert result.get("isError") in (None, False)
    structured = result["structuredContent"]
    assert structured["cropDimensions"] == {"width": 10, "height": 10}
    assert structured["artifactId"]
    assert str(path) not in json.dumps(result)


def test_mcp_errors_use_the_shared_error_model(client: TestClient) -> None:
    initialize(client)
    result = rpc(
        client,
        "tools/call",
        {"name": "extract_text_and_layout", "arguments": {"image": {"kind": "asset"}}},
    )
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "invalid_input"
    assert error["retryable"] is False
    assert "Traceback" not in json.dumps(result)

    unknown = rpc(client, "tools/call", {"name": "does_not_exist", "arguments": {}})
    assert unknown["structuredContent"]["error"]["code"] == "not_found"


def test_transport_schema_parity(client: TestClient) -> None:
    document = client.get("/openapi.json").json()
    mcp_tools = {tool.name: tool for tool in _mcp_tools()}
    assert set(mcp_tools) == {tool.name for tool in TOOLS}

    for definition in TOOLS:
        advertised = mcp_tools[definition.name]
        assert advertised.input_schema == definition.input_schema()
        assert advertised.output_schema == definition.output_schema()

        operation = document["paths"][definition.http_path]["post"]
        request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        response_ref = operation["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ]
        assert request_ref.endswith(f"/{definition.input_model.__name__}")
        assert response_ref.endswith(f"/{definition.output_model.__name__}")
        assert advertised.description == operation["description"]


def test_same_input_produces_the_same_result_on_both_transports(
    client: TestClient, allowed_root: Path
) -> None:
    initialize(client)
    before = write_png(allowed_root / "before.png", 32, 32)
    after = write_png(allowed_root / "after.png", 32, 32, color="black")
    arguments = {"before": local_reference(before), "after": local_reference(after)}

    http_result = client.post("/tools/compare_images", json=arguments).json()
    mcp_result = rpc(client, "tools/call", {"name": "compare_images", "arguments": arguments})
    assert mcp_result["structuredContent"] == http_result
