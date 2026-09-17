"""One-request JSON worker protocol for the TypeScript capability adapter."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Mapping
from typing import Any

from .svg_analysis import SvgAnalysisError, analyze_svg, can_analyze_svg
from .text_limits import truncate_utf16, utf16_length

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 65_536
MIN_RESPONSE_BYTES = 65_536
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ID_LENGTH = 200
OPERATIONS = frozenset(
    {
        "health",
        "analyze_image",
        "extract_text_and_layout",
        "compare_images",
        "optimize_image_region",
    }
)


class WorkerRequestError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {
            truncate_utf16(str(key), 200): truncate_utf16(str(value), 200)
            for key, value in (details or {}).items()
        }


def main() -> None:
    response: dict[str, object]
    try:
        request = _read_request()
        response = asyncio.run(_dispatch(request))
    except WorkerRequestError as error:
        response = _error(error.code, error.message, details=error.details)
    except SvgAnalysisError as error:
        response = _error(error.code, error.message, details=error.details)
    except Exception:
        response = _error("internal", "Vision worker failed")
    sys.stdout.write(_encode_response(response, _maximum_response_bytes()))
    sys.stdout.flush()


def _read_request() -> dict[str, Any]:
    payload = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(payload) > MAX_REQUEST_BYTES:
        raise WorkerRequestError(
            "payload_too_large",
            "Worker request exceeds the configured protocol limit",
            details={"maxBytes": MAX_REQUEST_BYTES},
        )
    if not payload:
        raise WorkerRequestError("invalid_input", "Worker request is empty")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerRequestError("invalid_input", "Worker request is not valid JSON") from exc
    if not isinstance(value, dict):
        raise WorkerRequestError("invalid_input", "Worker request must be an object")
    expected = {"protocolVersion", "operation", "requestId", "principal", "input"}
    if set(value) != expected:
        raise WorkerRequestError("invalid_input", "Worker request fields are invalid")
    if value.get("protocolVersion") != PROTOCOL_VERSION:
        raise WorkerRequestError("invalid_input", "Worker protocol version is unsupported")
    operation = value.get("operation")
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise WorkerRequestError("invalid_input", "Worker operation is unsupported")
    for name in ("requestId", "principal"):
        field = value.get(name)
        if not isinstance(field, str) or not 1 <= utf16_length(field) <= MAX_ID_LENGTH:
            raise WorkerRequestError("invalid_input", f"Worker {name} is invalid")
    if not isinstance(value.get("input"), dict):
        raise WorkerRequestError("invalid_input", "Worker input must be an object")
    return value


async def _dispatch(request: dict[str, Any]) -> dict[str, object]:
    operation = str(request["operation"])
    input_value = request["input"]
    if operation == "health":
        if input_value:
            raise WorkerRequestError("invalid_input", "Health input must be empty")
        return _success(await _health())
    if operation == "analyze_image" and can_analyze_svg(input_value):
        return _success(analyze_svg(input_value))
    return await _run_domain_tool(operation, input_value, str(request["principal"]))


async def _run_domain_tool(
    operation: str,
    input_value: Mapping[str, Any],
    principal: str,
) -> dict[str, object]:
    try:
        from .config import Settings
        from .errors import VisionError
        from .registry import get_tool
        from .runtime import Runtime, ToolContext
    except ModuleNotFoundError as exc:
        return _error(
            "provider_unavailable",
            "Python vision dependencies are not installed",
            details={"dependency": exc.name or "unknown"},
        )

    runtime: Runtime | None = None
    try:
        settings = Settings()
        runtime = Runtime(settings)
        await runtime.assets.purge_expired()
        context = ToolContext(
            runtime=runtime,
            principal=_principal_bucket(principal),
            request_id="worker",
        )
        result = await get_tool(operation).run(input_value, context)
        return _success(result.model_dump(mode="json", by_alias=True))
    except VisionError as error:
        return _error(
            error.code.value,
            error.message,
            retryable=error.retryable,
            details=error.details,
        )
    except Exception:
        return _error("internal", "Vision worker failed")
    finally:
        if runtime is not None:
            await runtime.shutdown()


async def _health() -> dict[str, str]:
    required = ("PIL", "numpy", "pydantic", "pydantic_settings")
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        return {
            "state": "not_ready",
            "detail": "required Python image dependencies are not installed",
        }

    try:
        from .config import Settings
        from .runtime import Runtime

        settings = Settings()
    except Exception:
        return {"state": "not_ready", "detail": "worker configuration is invalid"}
    if not settings.allowed_root_paths:
        return {"state": "not_ready", "detail": "no valid image root is configured"}

    runtime = Runtime(settings)
    try:
        storage_status, _storage_detail = await runtime.assets.health()
        provider_statuses = await runtime.router.health()
        status_by_name = {name: status for name, status, _detail in provider_statuses}
        required_statuses = [
            status_by_name.get(name, "unavailable")
            for name in runtime.router.required_health_components
        ]
        statuses = [storage_status, *required_statuses]
        if any(status == "unavailable" for status in statuses):
            return {"state": "not_ready", "detail": "worker storage or OCR provider is unavailable"}
        if any(status == "degraded" for status in statuses):
            return {
                "state": "degraded",
                "detail": "worker is configured but an external provider is not live-verified",
            }
        return {
            "state": "ready",
            "detail": "worker, storage, and configured OCR provider are ready",
        }
    except Exception:
        return {"state": "not_ready", "detail": "worker readiness checks failed"}
    finally:
        await runtime.shutdown()


def _principal_bucket(principal: str) -> str:
    digest = hashlib.sha256(principal.encode("utf-8")).hexdigest()
    return f"p_{digest[:32]}"


def _success(result: object) -> dict[str, object]:
    return {"protocolVersion": PROTOCOL_VERSION, "ok": True, "result": result}


def _maximum_response_bytes() -> int:
    raw = os.environ.get("VISION_WORKER_MAX_OUTPUT_BYTES")
    if raw is None:
        return DEFAULT_RESPONSE_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RESPONSE_BYTES
    return min(MAX_RESPONSE_BYTES, max(MIN_RESPONSE_BYTES, value))


def _encode_response(response: dict[str, object], max_bytes: int) -> str:
    def encode() -> str:
        return json.dumps(response, separators=(",", ":"), ensure_ascii=True) + "\n"

    encoded = encode()
    if len(encoded.encode("ascii")) <= max_bytes:
        return encoded

    result = response.get("result")
    if response.get("ok") is not True or not isinstance(result, dict):
        return encode()

    _mark_response_truncated(result)
    list_values = {
        key: value
        for key in ("blocks", "facts", "regions")
        if isinstance((value := result.get(key)), list)
    }
    references = _bulk_string_references(result)
    _apply_string_budget(references, 0)
    for key in list_values:
        result[key] = []

    if len(encode().encode("ascii")) > max_bytes:
        return encode()

    for key, values in list_values.items():
        low = 0
        high = len(values)
        while low < high:
            candidate = (low + high + 1) // 2
            result[key] = values[:candidate]
            if len(encode().encode("ascii")) <= max_bytes:
                low = candidate
            else:
                high = candidate - 1
        result[key] = values[:low]

    maximum_string_bytes = max(
        (len(value.encode("utf-8")) for _, _, value, _ in references), default=0
    )
    low = 0
    high = maximum_string_bytes
    while low < high:
        candidate = (low + high + 1) // 2
        _apply_string_budget(references, candidate)
        if len(encode().encode("ascii")) <= max_bytes:
            low = candidate
        else:
            high = candidate - 1
    _apply_string_budget(references, low)
    encoded = encode()
    if len(encoded.encode("ascii")) <= max_bytes:
        return encoded
    return (
        json.dumps(
            _error("internal", "Vision worker response exceeded the configured output limit"),
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    )


def _mark_response_truncated(result: dict[str, object]) -> None:
    meta = result.get("meta")
    if not isinstance(meta, dict):
        return
    meta["truncated"] = True
    warning = "result truncated to the configured worker output byte limit"
    warnings = meta.get("warnings")
    if not isinstance(warnings, list):
        meta["warnings"] = [warning]
    elif warning not in warnings:
        if len(warnings) < 20:
            warnings.append(warning)
        elif warnings:
            warnings[-1] = warning


def _bulk_string_references(
    result: dict[str, object],
) -> list[tuple[dict[str, object], str, str, bool]]:
    references: list[tuple[dict[str, object], str, str, bool]] = []
    for key in ("content", "extractedText", "summary", "fallbackReason"):
        value = result.get(key)
        if isinstance(value, str):
            references.append((result, key, value, False))
    for list_key, string_keys in {
        "blocks": ("text",),
        "facts": ("label", "value", "evidence"),
    }.items():
        values = result.get(list_key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            for key in string_keys:
                text = value.get(key)
                if isinstance(text, str):
                    references.append((value, key, text, list_key == "facts"))
    return references


def _apply_string_budget(
    references: list[tuple[dict[str, object], str, str, bool]],
    max_bytes: int,
) -> None:
    for container, key, value, required in references:
        container[key] = _truncate_utf8(value, max_bytes, required)


def _truncate_utf8(value: str, max_bytes: int, required: bool) -> str:
    if len(value.encode("utf-8")) <= max_bytes:
        return value
    if max_bytes <= 0:
        return value[:1] if required else ""
    truncated = value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return truncated or (value[:1] if required else "")


def _error(
    code: str,
    message: str,
    *,
    retryable: bool | None = None,
    details: Mapping[str, object] | None = None,
) -> dict[str, object]:
    default_retryable = code in {"busy", "timeout", "provider_unavailable"}
    bounded_details = {
        truncate_utf16(str(key), 200): truncate_utf16(str(value), 200)
        for key, value in list((details or {}).items())[:10]
    }
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "ok": False,
        "error": {
            "code": code,
            "message": truncate_utf16(message, 300),
            "retryable": default_retryable if retryable is None else retryable,
            "details": bounded_details,
        },
    }


if __name__ == "__main__":
    main()
