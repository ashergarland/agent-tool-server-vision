"""MCP transports: stdio and stateless Streamable HTTP.

Both transports serve exactly the tools declared in
:mod:`vision_server.registry`; no tool metadata is defined here.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import mcp_types as types
from mcp.server.lowlevel import Server
from starlette.applications import Starlette

from ..errors import ErrorCode, VisionError
from ..registry import SERVER_INSTRUCTIONS, TOOLS, get_tool
from ..runtime import Runtime, ToolContext
from .context import current_principal


def _mcp_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=tool.name,
            title=tool.title,
            description=tool.description,
            input_schema=tool.input_schema(),
            output_schema=tool.output_schema(),
            annotations=types.ToolAnnotations(
                read_only_hint=tool.annotations.read_only_hint,
                destructive_hint=tool.annotations.destructive_hint,
                idempotent_hint=tool.annotations.idempotent_hint,
                open_world_hint=tool.annotations.open_world_hint,
            ),
        )
        for tool in TOOLS
    ]


def build_server(runtime: Runtime) -> Server[Any]:
    """Create an MCP server whose tool surface is generated from the registry."""

    async def on_list_tools(
        _ctx: Any, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=_mcp_tools())

    async def on_call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        request_id = uuid.uuid4().hex
        try:
            tool = get_tool(params.name)
            context = ToolContext(
                runtime=runtime,
                principal=current_principal.get(),
                request_id=request_id,
            )
            result = await tool.run(params.arguments or {}, context)
        except VisionError as error:
            payload = error.to_response(request_id).model_dump(mode="json", by_alias=True)
            return types.CallToolResult(
                content=[types.TextContent(text=json.dumps(payload))],
                structured_content=payload,
                is_error=True,
            )
        except Exception:  # noqa: BLE001 - never leak stacks or SDK errors
            payload = (
                VisionError(ErrorCode.INTERNAL, "Internal error")
                .to_response(request_id)
                .model_dump(mode="json", by_alias=True)
            )
            return types.CallToolResult(
                content=[types.TextContent(text=json.dumps(payload))],
                structured_content=payload,
                is_error=True,
            )
        body = result.model_dump(mode="json", by_alias=True)
        return types.CallToolResult(
            content=[types.TextContent(text=json.dumps(body))],
            structured_content=body,
        )

    return Server(
        runtime.settings.service_name,
        version=runtime.settings.service_version,
        instructions=SERVER_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def build_streamable_http_app(runtime: Runtime) -> Starlette:
    """Stateless Streamable HTTP MCP application, mounted by the HTTP transport."""
    server = build_server(runtime)
    return server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        max_request_body_size=runtime.settings.max_json_bytes,
        host="0.0.0.0",  # noqa: S104 - containers bind all interfaces; auth is enforced upstream
    )


async def run_stdio(runtime: Runtime) -> None:
    """Serve the tools over stdio for local agent hosts."""
    from mcp.server.stdio import stdio_server

    server = build_server(runtime)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
