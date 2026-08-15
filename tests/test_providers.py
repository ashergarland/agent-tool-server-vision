"""Provider adapters, normalization, typed errors, and routing policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from vision_server.config import Settings
from vision_server.errors import ErrorCode, VisionError
from vision_server.providers.base import provider_auth_error, provider_unavailable
from vision_server.providers.content_understanding import ContentUnderstandingProvider
from vision_server.providers.paddle import MODEL_PROVENANCE, PaddleOcrProvider
from vision_server.providers.router import OcrRouter
from vision_server.runtime import Runtime
from vision_server.schemas import ProcessingMode

from .conftest import FakeOcrProvider, loaded_image

ANALYZE_RESULT = {
    "status": "Succeeded",
    "result": {
        "contents": [
            {
                "markdown": "# Invoice",
                "paragraphs": [
                    {
                        "content": "Total 42",
                        "confidence": 0.91,
                        "source": {"polygon": [1, 2, 30, 2, 30, 12, 1, 12]},
                    },
                    {"content": "   "},
                ],
                "words": [{"content": "ignored"}],
            }
        ]
    },
}


def azure_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "provider_mode": "azure",
        "azure_content_understanding_endpoint": "https://example-cu.invalid",
        "provider_timeout_seconds": 5,
    }
    values.update(overrides)
    return Settings(**values)


def transport_for(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


class FakeCredential:
    def __init__(self) -> None:
        self.requests = 0
        self.closed = False

    def get_token(self, scope: str) -> object:
        assert scope == "https://cognitiveservices.azure.com/.default"
        self.requests += 1
        return type("AccessToken", (), {"token": "credential-token"})()

    def close(self) -> None:
        self.closed = True


async def test_content_understanding_normalizes_results() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization", "")
            return httpx.Response(
                202, headers={"operation-location": "https://example-cu.invalid/results/1"}
            )
        return httpx.Response(200, json=ANALYZE_RESULT)

    provider = ContentUnderstandingProvider(
        azure_settings(),
        transport=transport_for(handler),
        token_provider=lambda: "token-value",
        poll_interval=0.0,
    )
    result = await provider.analyze(loaded_image(100, 50), "en")

    assert "api-version=2025-11-01" in seen["url"]
    assert "analyzeBinary" in seen["url"]
    assert seen["auth"].startswith("Bearer ")
    assert result.provider_name == "azure_content_understanding"
    assert result.api_version == "2025-11-01"
    assert result.markdown == "# Invoice"
    assert [block.text for block in result.blocks] == ["Total 42"]
    assert result.blocks[0].block_type == "paragraph"
    assert result.blocks[0].confidence == pytest.approx(0.91)
    assert result.blocks[0].polygon == (1.0, 2.0, 30.0, 2.0, 30.0, 12.0, 1.0, 12.0)


async def test_content_understanding_accepts_synchronous_results() -> None:
    provider = ContentUnderstandingProvider(
        azure_settings(),
        transport=transport_for(lambda request: httpx.Response(200, json=ANALYZE_RESULT)),
        token_provider=lambda: "token-value",
    )
    result = await provider.analyze(loaded_image(), "en")
    assert result.markdown == "# Invoice"


async def test_content_understanding_reuses_and_closes_credential() -> None:
    credential = FakeCredential()
    provider = ContentUnderstandingProvider(
        azure_settings(),
        transport=transport_for(lambda request: httpx.Response(200, json=ANALYZE_RESULT)),
        credential=credential,
    )
    await provider.analyze(loaded_image(), "en")
    await provider.analyze(loaded_image(), "en")
    await provider.close()

    assert credential.requests == 2
    assert credential.closed is True


async def test_runtime_shutdown_closes_providers(tmp_path: Path) -> None:
    class ClosableProvider(FakeOcrProvider):
        closed = False

        async def close(self) -> None:
            self.closed = True

    settings = azure_settings(
        asset_root=str(tmp_path / "assets"),
        shutdown_grace_seconds=0,
    )
    managed = ClosableProvider("azure_content_understanding")
    runtime = Runtime(
        settings,
        router=OcrRouter(
            settings,
            local=FakeOcrProvider("local_paddleocr"),
            azure=managed,
        ),
    )
    await runtime.shutdown()
    assert managed.closed is True


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, ErrorCode.FORBIDDEN, False),
        (403, ErrorCode.FORBIDDEN, False),
        (429, ErrorCode.QUOTA_EXCEEDED, False),
        (400, ErrorCode.INVALID_INPUT, False),
        (500, ErrorCode.PROVIDER_UNAVAILABLE, True),
        (302, ErrorCode.PROVIDER_ERROR, False),
    ],
)
async def test_content_understanding_maps_status_codes(
    status: int, code: ErrorCode, retryable: bool
) -> None:
    provider = ContentUnderstandingProvider(
        azure_settings(),
        transport=transport_for(lambda request: httpx.Response(status)),
        token_provider=lambda: "token-value",
    )
    with pytest.raises(VisionError) as error:
        await provider.analyze(loaded_image(), "en")
    assert error.value.code is code
    assert error.value.retryable is retryable
    assert "example-cu" not in json.dumps(error.value.details)


async def test_content_understanding_rejects_malformed_payloads() -> None:
    provider = ContentUnderstandingProvider(
        azure_settings(),
        transport=transport_for(lambda request: httpx.Response(200, content=b"not json")),
        token_provider=lambda: "token-value",
    )
    with pytest.raises(VisionError) as error:
        await provider.analyze(loaded_image(), "en")
    assert error.value.code is ErrorCode.PROVIDER_ERROR
    assert error.value.retryable is False


async def test_content_understanding_reports_failed_operations() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202, headers={"operation-location": "https://example-cu.invalid/results/1"}
            )
        return httpx.Response(200, json={"status": "Failed"})

    provider = ContentUnderstandingProvider(
        azure_settings(),
        transport=transport_for(handler),
        token_provider=lambda: "token-value",
        poll_interval=0.0,
    )
    with pytest.raises(VisionError) as error:
        await provider.analyze(loaded_image(), "en")
    assert error.value.code is ErrorCode.PROVIDER_ERROR


async def test_content_understanding_rejects_unknown_operation_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202, headers={"operation-location": "https://example-cu.invalid/results/1"}
            )
        return httpx.Response(200, json={"result": {}})

    provider = ContentUnderstandingProvider(
        azure_settings(),
        transport=transport_for(handler),
        token_provider=lambda: "token-value",
        poll_interval=0.0,
    )
    with pytest.raises(VisionError) as error:
        await provider.analyze(loaded_image(), "en")
    assert error.value.code is ErrorCode.PROVIDER_ERROR
    assert error.value.retryable is False


async def test_content_understanding_translates_transport_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    provider = ContentUnderstandingProvider(
        azure_settings(),
        transport=transport_for(handler),
        token_provider=lambda: "token-value",
    )
    with pytest.raises(VisionError) as error:
        await provider.analyze(loaded_image(), "en")
    assert error.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert error.value.retryable is True


async def test_content_understanding_requires_configuration() -> None:
    provider = ContentUnderstandingProvider(Settings(_env_file=None))  # type: ignore[call-arg]
    assert provider.configured is False
    assert await provider.health() == ("unavailable", "endpoint is not configured")
    with pytest.raises(VisionError):
        await provider.analyze(loaded_image(), "en")


# -- local provider ---------------------------------------------------------


class FakeModernEngine:
    def predict(self, pixels: Any) -> list[dict[str, Any]]:
        return [
            {
                "res": {
                    "rec_texts": ["hello", ""],
                    "rec_scores": [0.9, 0.5],
                    "rec_polys": [[[0, 0], [10, 0], [10, 5], [0, 5]], [[0, 0]]],
                }
            }
        ]


class FakeLegacyEngine:
    def ocr(self, pixels: Any) -> list[list[Any]]:
        return [[[[[0, 0], [8, 0], [8, 4], [0, 4]], ("legacy", 0.75)]]]


async def test_paddle_provider_parses_modern_and_legacy_results() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    modern = PaddleOcrProvider(settings, engine_factory=lambda language: FakeModernEngine())
    result = await modern.analyze(loaded_image(), "en")
    assert [block.text for block in result.blocks] == ["hello"]
    assert result.model == MODEL_PROVENANCE["family"]

    legacy = PaddleOcrProvider(settings, engine_factory=lambda language: FakeLegacyEngine())
    legacy_result = await legacy.analyze(loaded_image(), "en")
    assert [block.text for block in legacy_result.blocks] == ["legacy"]
    assert legacy_result.blocks[0].confidence == pytest.approx(0.75)


async def test_paddle_provider_enforces_language_allow_list() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    provider = PaddleOcrProvider(settings, engine_factory=lambda language: FakeModernEngine())
    with pytest.raises(VisionError) as error:
        await provider.analyze(loaded_image(), "japan")
    assert error.value.code is ErrorCode.INVALID_INPUT


async def test_paddle_engine_cache_is_bounded() -> None:
    settings = Settings(_env_file=None, paddle_languages="en,ch,fr", paddle_cache_size=2)  # type: ignore[call-arg]
    created: list[str] = []

    def factory(language: str) -> FakeModernEngine:
        created.append(language)
        return FakeModernEngine()

    provider = PaddleOcrProvider(settings, engine_factory=factory)
    for language in ("en", "ch", "fr", "ch", "en"):
        await provider.analyze(loaded_image(), language)
    assert created == ["en", "ch", "fr", "en"]
    assert len(provider._engines) == 2  # noqa: SLF001


async def test_paddle_health_does_not_load_weights() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    provider = PaddleOcrProvider(settings, engine_factory=lambda language: FakeModernEngine())
    assert await provider.health() == ("ok", None)

    real = PaddleOcrProvider(settings)
    status, _detail = await real.health()
    assert status in {"ok", "unavailable"}
    assert real._engines == {}  # noqa: SLF001


# -- routing ----------------------------------------------------------------


def router_for(mode: str, local: FakeOcrProvider, azure: FakeOcrProvider) -> OcrRouter:
    settings = (
        azure_settings(provider_mode=mode) if mode != "local" else Settings(_env_file=None)  # type: ignore[call-arg]
    )
    return OcrRouter(settings, local=local, azure=azure)


async def test_router_forced_modes(
    local_provider: FakeOcrProvider, azure_provider: FakeOcrProvider
) -> None:
    router = router_for("auto", local_provider, azure_provider)
    local_result = await router.analyze(loaded_image(), "en", ProcessingMode.LOCAL)
    assert local_result.result.provider_name == "local_paddleocr"
    assert local_result.fallback_used is False

    azure_result = await router.analyze(loaded_image(), "en", ProcessingMode.AZURE)
    assert azure_result.result.provider_name == "azure_content_understanding"


async def test_router_rejects_azure_without_configuration(
    local_provider: FakeOcrProvider, azure_provider: FakeOcrProvider
) -> None:
    router = router_for("local", local_provider, azure_provider)
    with pytest.raises(VisionError) as error:
        await router.analyze(loaded_image(), "en", ProcessingMode.AZURE)
    assert error.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert azure_provider.calls == []


async def test_router_auto_prefers_configured_azure_and_falls_back(
    local_provider: FakeOcrProvider, azure_provider: FakeOcrProvider
) -> None:
    router = router_for("auto", local_provider, azure_provider)
    assert router.preferred_mode() is ProcessingMode.AZURE

    azure_provider._error = provider_unavailable("down")  # noqa: SLF001
    routed = await router.analyze(loaded_image(), "en", ProcessingMode.AUTO)
    assert routed.fallback_used is True
    assert routed.mode is ProcessingMode.LOCAL

    azure_provider._error = provider_auth_error("denied")  # noqa: SLF001
    with pytest.raises(VisionError):
        await router.analyze(loaded_image(), "en", ProcessingMode.AUTO)


async def test_router_times_out_slow_providers(
    local_provider: FakeOcrProvider, azure_provider: FakeOcrProvider
) -> None:
    import asyncio

    class SlowProvider(FakeOcrProvider):
        async def analyze(self, image: Any, language: str) -> Any:
            await asyncio.sleep(1)
            raise AssertionError("should not complete")

    settings = Settings(_env_file=None, provider_timeout_seconds=0.01)  # type: ignore[call-arg]
    router = OcrRouter(settings, local=SlowProvider("local_paddleocr"), azure=azure_provider)
    with pytest.raises(VisionError) as error:
        await router.analyze(loaded_image(), "en", ProcessingMode.LOCAL)
    assert error.value.code is ErrorCode.TIMEOUT
    assert error.value.retryable is True


async def test_router_health_reports_optional_providers(
    local_provider: FakeOcrProvider, azure_provider: FakeOcrProvider
) -> None:
    local_only = router_for("local", local_provider, azure_provider)
    assert [name for name, _status, _detail in await local_only.health()] == [
        "provider:local_paddleocr"
    ]
    hybrid = router_for("auto", local_provider, azure_provider)
    assert len(await hybrid.health()) == 2
