"""Managed OCR/layout provider backed by Azure AI Content Understanding.

Uses the generally available analyzer API (``api-version=2025-11-01`` by
default) with a prebuilt document analyzer, authenticated with
``DefaultAzureCredential`` so hosted deployments use managed identity. Raw
service payloads are normalized and never returned to callers.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..config import Settings
from ..imaging import LoadedImage, encode_image
from .base import (
    OcrBlock,
    OcrResult,
    provider_auth_error,
    provider_invalid_input,
    provider_malformed,
    provider_quota_error,
    provider_timeout,
    provider_unavailable,
)

CREDENTIAL_SCOPE = "https://cognitiveservices.azure.com/.default"
_POLL_INTERVAL_SECONDS = 1.0


class ContentUnderstandingProvider:
    """Thin, typed client for the Content Understanding analyze operation."""

    name = "azure_content_understanding"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: Any | None = None,
        token_provider: Any | None = None,
        credential: Any | None = None,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._token_provider = token_provider
        self._credential = credential
        self._credential_lock = asyncio.Lock()
        self._poll_interval = poll_interval

    @property
    def configured(self) -> bool:
        return bool(self._settings.azure_content_understanding_endpoint)

    async def analyze(self, image: LoadedImage, language: str) -> OcrResult:
        if not self.configured:
            raise provider_unavailable("Content Understanding endpoint is not configured")
        payload = await asyncio.to_thread(encode_image, image.image, "png", 100)
        document = await self._analyze_binary(payload)
        blocks, markdown, warnings = _normalize(document, image)
        return OcrResult(
            blocks=tuple(blocks),
            provider_name=self.name,
            model=self._settings.azure_content_understanding_analyzer,
            api_version=self._settings.azure_content_understanding_api_version,
            markdown=markdown,
            warnings=tuple(warnings),
        )

    async def health(self) -> tuple[str, str | None]:
        if not self.configured:
            return "unavailable", "endpoint is not configured"
        return "ok", "configuration present; not called during readiness"

    async def close(self) -> None:
        credential = self._credential
        self._credential = None
        close = getattr(credential, "close", None)
        if close is not None:
            await asyncio.to_thread(close)

    # -- internals ----------------------------------------------------------

    async def _analyze_binary(self, payload: bytes) -> dict[str, Any]:
        client = await self._client()
        scheme = "Bearer "
        headers = {
            "Authorization": scheme + await self._token(),
            "Content-Type": "application/octet-stream",
        }
        analyzer = self._settings.azure_content_understanding_analyzer
        version = self._settings.azure_content_understanding_api_version
        url = (
            f"{self._settings.azure_content_understanding_endpoint}"
            f"/contentunderstanding/analyzers/{analyzer}:analyzeBinary"
            f"?api-version={version}"
        )
        async with client:
            response = await _request(client, "POST", url, headers=headers, content=payload)
            _raise_for_status(response)
            if response.status_code == 200:
                return _json(response)
            operation_url = response.headers.get("operation-location") or response.headers.get(
                "Operation-Location"
            )
            if not operation_url:
                raise provider_malformed("Content Understanding response is missing a poll target")
            deadline = asyncio.get_running_loop().time() + self._settings.provider_timeout_seconds
            while True:
                if asyncio.get_running_loop().time() > deadline:
                    raise provider_timeout("Content Understanding analysis timed out")
                poll = await _request(
                    client,
                    "GET",
                    operation_url,
                    headers={"Authorization": headers["Authorization"]},
                )
                _raise_for_status(poll)
                body = _json(poll)
                status = str(body.get("status", "")).lower()
                if status in {"succeeded", "completed"}:
                    return body
                if status in {"failed", "canceled", "cancelled"}:
                    raise provider_malformed("Content Understanding analysis failed")
                if not status:
                    raise provider_malformed("Content Understanding returned an unknown status")
                await asyncio.sleep(self._poll_interval)

    async def _client(self) -> Any:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - httpx ships with FastAPI stack
            raise provider_unavailable("httpx is not installed") from exc
        timeout = self._settings.provider_timeout_seconds
        if self._transport is not None:
            return httpx.AsyncClient(transport=self._transport, timeout=timeout)
        return httpx.AsyncClient(timeout=timeout)

    async def _token(self) -> str:
        if self._token_provider is not None:
            token = self._token_provider()
            return await token if asyncio.iscoroutine(token) else str(token)
        if self._credential is None:
            async with self._credential_lock:
                if self._credential is None:
                    try:
                        from azure.identity import DefaultAzureCredential
                    except ImportError as exc:  # pragma: no cover - optional dependency
                        raise provider_unavailable("azure-identity is not installed") from exc
                    self._credential = DefaultAzureCredential()
        try:
            access = await asyncio.to_thread(self._credential.get_token, CREDENTIAL_SCOPE)
        except Exception as exc:  # noqa: BLE001 - SDK errors must not leak
            raise provider_auth_error("Managed identity token acquisition failed") from exc
        return str(access.token)


async def _request(client: Any, method: str, url: str, **kwargs: Any) -> Any:
    try:
        return await client.request(method, url, **kwargs)
    except Exception as exc:  # noqa: BLE001 - normalize transport failures
        name = type(exc).__name__.lower()
        if "timeout" in name:
            raise provider_timeout("Content Understanding request timed out") from exc
        raise provider_unavailable("Content Understanding is unreachable") from exc


def _raise_for_status(response: Any) -> None:
    status = int(response.status_code)
    if status in (401, 403):
        raise provider_auth_error("Content Understanding rejected the service identity")
    if status == 429:
        raise provider_quota_error("Content Understanding throttled the request")
    if status in (400, 415):
        raise provider_invalid_input("Content Understanding rejected the image")
    if status >= 500 or status == 408:
        raise provider_unavailable("Content Understanding is temporarily unavailable")
    if status not in (200, 201, 202):
        raise provider_malformed("Content Understanding returned an unexpected status")


def _json(response: Any) -> dict[str, Any]:
    try:
        body = response.json()
    except Exception as exc:  # noqa: BLE001 - malformed body
        raise provider_malformed("Content Understanding returned a malformed body") from exc
    if not isinstance(body, dict):
        raise provider_malformed("Content Understanding returned a malformed body")
    return body


def _normalize(
    document: dict[str, Any], image: LoadedImage
) -> tuple[list[OcrBlock], str | None, list[str]]:
    """Map the service payload onto the shared block model."""
    result = document.get("result", document)
    if not isinstance(result, dict):
        raise provider_malformed("Content Understanding returned a malformed result")
    contents = result.get("contents")
    if not isinstance(contents, list) or not contents:
        return [], None, ["provider returned no content"]
    markdown_parts: list[str] = []
    blocks: list[OcrBlock] = []
    warnings: list[str] = []
    for page_index, content in enumerate(contents, start=1):
        if not isinstance(content, dict):
            continue
        markdown = content.get("markdown")
        if isinstance(markdown, str):
            markdown_parts.append(markdown)
        for key, block_type in (("paragraphs", "paragraph"), ("lines", "line"), ("words", "word")):
            items = content.get(key)
            if not isinstance(items, list) or not items:
                continue
            for item in items:
                block = _block(item, block_type, page_index, image)
                if block is not None:
                    blocks.append(block)
            break
        else:
            warnings.append("provider returned no positional blocks")
    return blocks, "\n\n".join(markdown_parts) or None, warnings


def _block(item: Any, block_type: str, page: int, image: LoadedImage) -> OcrBlock | None:
    if not isinstance(item, dict):
        return None
    text = item.get("content") or item.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    polygon = _polygon(item, image)
    confidence = item.get("confidence")
    return OcrBlock(
        text=text,
        block_type=block_type,
        page=page,
        polygon=polygon,
        confidence=float(confidence) if isinstance(confidence, int | float) else None,
    )


def _polygon(item: dict[str, Any], image: LoadedImage) -> tuple[float, ...]:
    raw = item.get("polygon")
    if raw is None:
        source = item.get("source")
        raw = source.get("polygon") if isinstance(source, dict) else None
    values: list[float] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, int | float):
                values.append(float(entry))
            elif isinstance(entry, list | tuple) and len(entry) >= 2:
                values.extend((float(entry[0]), float(entry[1])))
    if len(values) < 6 or len(values) % 2:
        return ()
    clamped: list[float] = []
    for index, value in enumerate(values[:32]):
        limit = float(image.width if index % 2 == 0 else image.height)
        clamped.append(max(0.0, min(limit, value)))
    return tuple(clamped)
