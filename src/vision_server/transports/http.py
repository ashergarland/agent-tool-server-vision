"""Authenticated HTTP/OpenAPI 3.1 transport built from the tool registry."""

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import Settings
from ..errors import ErrorCode, VisionError
from ..registry import SERVER_INSTRUCTIONS, TOOLS, ToolDefinition
from ..runtime import Runtime, ToolContext
from ..schemas import (
    AssetUploadResponse,
    ComponentStatus,
    HealthResponse,
    ReadinessResponse,
)
from ..security import ANONYMOUS_PRINCIPAL, match_secret, principal_from_digest
from .context import current_principal

LOGGER = logging.getLogger("vision_server.http")
REQUEST_ID_HEADER = "x-request-id"


def authenticate(request: Request) -> str:
    """Token or API-key authentication with constant-time digest comparison."""
    runtime: Runtime = request.app.state.runtime
    settings = runtime.settings
    if not settings.auth_enabled:
        if settings.is_production:  # pragma: no cover - blocked by settings validation
            raise VisionError(ErrorCode.INTERNAL, "Authentication is misconfigured")
        return ANONYMOUS_PRINCIPAL
    header = request.headers.get("authorization", "")
    presented = ""
    if header.lower().startswith("bearer "):
        presented = header[7:].strip()
    else:
        presented = request.headers.get("x-api-key", "").strip()
    if not presented:
        raise VisionError(ErrorCode.UNAUTHORIZED, "Missing credentials")
    digest = match_secret(presented, settings.api_key_credentials)
    if digest is None:
        raise VisionError(ErrorCode.UNAUTHORIZED, "Invalid credentials")
    return principal_from_digest(digest)


class BodyLimitMiddleware:
    """Rejects oversized JSON bodies before they are buffered."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self._app = app
        self._max_bytes = settings.max_json_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if scope["type"] != "http" or not (path.startswith("/tools/") or path.startswith("/mcp")):
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        if declared is not None:
            try:
                declared_bytes = int(declared)
            except ValueError:
                await self._send_error(
                    scope,
                    receive,
                    send,
                    VisionError(ErrorCode.INVALID_INPUT, "Content-Length must be an integer"),
                    headers,
                )
                return
            if declared_bytes < 0:
                await self._send_error(
                    scope,
                    receive,
                    send,
                    VisionError(ErrorCode.INVALID_INPUT, "Content-Length cannot be negative"),
                    headers,
                )
                return
            if declared_bytes > self._max_bytes:
                await self._send_error(scope, receive, send, self._too_large(), headers)
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                await self._app(scope, _replay_message(message), send)
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self._max_bytes:
                await self._send_error(scope, receive, send, self._too_large(), headers)
                return
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self._app(scope, replay_receive, send)

    def _too_large(self) -> VisionError:
        return VisionError(
            ErrorCode.PAYLOAD_TOO_LARGE,
            "Request body exceeds the configured JSON limit",
            details={"maxBytes": self._max_bytes},
        )

    @staticmethod
    async def _send_error(
        scope: Scope,
        receive: Receive,
        send: Send,
        error: VisionError,
        headers: Headers,
    ) -> None:
        request_id = headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        response = _error_response(error, request_id)
        response.headers[REQUEST_ID_HEADER] = request_id
        await response(scope, receive, send)


def _replay_message(message: Message) -> Receive:
    async def replay() -> Message:
        return message

    return replay


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request ID, authenticates MCP traffic, and logs metrics only."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        token = None
        if request.url.path.startswith("/mcp"):
            try:
                token = current_principal.set(authenticate(request))
            except VisionError as error:
                return _error_response(error, request_id)
        try:
            response = await call_next(request)
        finally:
            if token is not None:
                current_principal.reset(token)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        LOGGER.info(
            "request handled",
            extra={
                "path": request.url.path,
                "method": request.method,
                "status": response.status_code,
                "durationMs": duration_ms,
                "requestId": request_id,
            },
        )
        return response


def _error_response(error: VisionError, request_id: str) -> JSONResponse:
    payload = error.to_response(request_id).model_dump(mode="json", by_alias=True)
    return JSONResponse(status_code=error.status_code, content=payload)


def create_app(
    settings: Settings | None = None,
    runtime: Runtime | None = None,
    *,
    mcp_app: Any | None = None,
) -> FastAPI:
    """Build the FastAPI application; every tool route comes from the registry."""
    active_runtime = runtime or Runtime(settings or Settings())
    config = active_runtime.settings
    logging.basicConfig(level=config.log_level)

    if mcp_app is None:
        from .mcp import build_streamable_http_app

        mcp_app = build_streamable_http_app(active_runtime)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await active_runtime.assets.purge_expired()
        async with mcp_app.router.lifespan_context(mcp_app):
            try:
                yield
            finally:
                await active_runtime.shutdown()

    app = FastAPI(
        title=config.service_name,
        version=config.service_version,
        description=SERVER_INSTRUCTIONS,
        lifespan=lifespan,
    )
    app.state.runtime = active_runtime
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(BodyLimitMiddleware, settings=config)

    @app.exception_handler(VisionError)
    async def vision_error_handler(request: Request, exc: VisionError) -> JSONResponse:
        return _error_response(exc, getattr(request.state, "request_id", ""))

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(service=config.service_name, version=config.service_version)

    @app.get("/ready", response_model=ReadinessResponse, tags=["system"])
    async def ready(response: Response) -> ReadinessResponse:
        components = [ComponentStatus(name="registry", status="ok" if TOOLS else "unavailable")]
        storage_status, storage_detail = await active_runtime.assets.health()
        components.append(
            ComponentStatus(name="storage", status=storage_status, detail=storage_detail)  # type: ignore[arg-type]
        )
        for name, status, detail in await active_runtime.router.health():
            components.append(
                ComponentStatus(name=name, status=status, detail=detail)  # type: ignore[arg-type]
            )
        statuses = {component.name: component.status for component in components}
        required_components = {
            "registry",
            "storage",
            *active_runtime.router.required_health_components,
        }
        required_ok = all(
            statuses.get(component_name) == "ok" for component_name in required_components
        )
        if not required_ok:
            response.status_code = 503
        return ReadinessResponse(
            status="ready" if required_ok else "not_ready",
            service=config.service_name,
            version=config.service_version,
            tools=[tool.name for tool in TOOLS],
            components=components,
            configuration={
                key: value
                for key, value in config.public_summary().items()
                if isinstance(value, str | int | bool)
            },
        )

    @app.post("/assets", response_model=AssetUploadResponse, tags=["assets"], status_code=201)
    async def upload_asset(
        request: Request, principal: str = Depends(authenticate)
    ) -> AssetUploadResponse:
        content_type = request.headers.get("content-type", "")
        record = await active_runtime.assets.put(principal, request.stream(), content_type)
        return AssetUploadResponse(
            asset_id=record.asset_id,
            byte_count=record.byte_count,
            content_type=record.content_type,
            expires_at=record.expires_at.isoformat(),
        )

    @app.get("/assets/{asset_id}", tags=["assets"])
    async def download_asset(
        asset_id: str, principal: str = Depends(authenticate)
    ) -> StreamingResponse:
        record, payload = await active_runtime.assets.get(principal, asset_id)

        async def stream() -> AsyncIterator[bytes]:
            chunk = 64 * 1024
            for offset in range(0, len(payload), chunk):
                yield payload[offset : offset + chunk]

        return StreamingResponse(
            stream(),
            media_type=record.content_type,
            headers={"content-length": str(record.byte_count), "cache-control": "no-store"},
        )

    @app.delete("/assets/{asset_id}", status_code=204, tags=["assets"])
    async def delete_asset(asset_id: str, principal: str = Depends(authenticate)) -> Response:
        await active_runtime.assets.delete(principal, asset_id)
        return Response(status_code=204)

    for tool in TOOLS:
        _register_tool_route(app, tool, active_runtime)

    app.mount("/mcp", mcp_app)
    return app


def _register_tool_route(app: FastAPI, tool: ToolDefinition, runtime: Runtime) -> None:
    async def endpoint(
        request: Request, payload: Any, principal: str = Depends(authenticate)
    ) -> Response:
        context = ToolContext(
            runtime=runtime,
            principal=principal,
            request_id=getattr(request.state, "request_id", ""),
        )
        result = await tool.run(payload.model_dump(by_alias=True), context)
        return JSONResponse(content=result.model_dump(mode="json", by_alias=True))

    endpoint.__annotations__["payload"] = tool.input_model
    endpoint.__name__ = tool.name

    app.post(
        tool.http_path,
        response_model=tool.output_model,
        tags=["vision tools"],
        summary=tool.title,
        description=tool.description,
        operation_id=tool.name,
        openapi_extra={"x-mcp-annotations": tool.annotations.as_dict()},
    )(endpoint)
