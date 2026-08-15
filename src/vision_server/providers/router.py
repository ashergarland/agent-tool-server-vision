"""Provider selection, fallback, and timeout policy."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..config import ProviderMode, Settings
from ..errors import ErrorCode
from ..imaging import LoadedImage
from ..schemas import ProcessingMode
from .base import (
    ClosableOcrProvider,
    OcrProvider,
    OcrResult,
    ProviderError,
    provider_timeout,
    provider_unavailable,
)
from .content_understanding import ContentUnderstandingProvider
from .paddle import PaddleOcrProvider

#: Failures that may be retried on the alternate provider. Authentication,
#: validation, quota policy, and malformed provider payloads never fall back.
RETRYABLE_CODES = frozenset({ErrorCode.PROVIDER_UNAVAILABLE, ErrorCode.TIMEOUT})


@dataclass(frozen=True)
class RoutedResult:
    result: OcrResult
    mode: ProcessingMode
    fallback_used: bool
    warnings: tuple[str, ...] = ()


class OcrRouter:
    """Chooses a provider from configuration and the caller's processing mode."""

    def __init__(
        self,
        settings: Settings,
        *,
        local: OcrProvider | None = None,
        azure: OcrProvider | None = None,
    ) -> None:
        self._settings = settings
        self._local = local if local is not None else PaddleOcrProvider(settings)
        self._azure = azure if azure is not None else ContentUnderstandingProvider(settings)

    @property
    def local(self) -> OcrProvider:
        return self._local

    @property
    def azure(self) -> OcrProvider:
        return self._azure

    @property
    def azure_configured(self) -> bool:
        return bool(self._settings.azure_content_understanding_endpoint)

    def preferred_mode(self) -> ProcessingMode:
        if self._settings.provider_mode is ProviderMode.LOCAL:
            return ProcessingMode.LOCAL
        if self._settings.provider_mode is ProviderMode.AZURE:
            return ProcessingMode.AZURE
        return ProcessingMode.AZURE if self.azure_configured else ProcessingMode.LOCAL

    @property
    def required_health_components(self) -> frozenset[str]:
        if self.preferred_mode() is ProcessingMode.AZURE:
            return frozenset({"provider:azure_content_understanding"})
        return frozenset({"provider:local_paddleocr"})

    async def analyze(
        self, image: LoadedImage, language: str, requested: ProcessingMode
    ) -> RoutedResult:
        if requested is ProcessingMode.LOCAL:
            return RoutedResult(await self._run(self._local, image, language), requested, False)
        if requested is ProcessingMode.AZURE:
            if not self.azure_configured:
                raise provider_unavailable("Azure provider is not configured")
            return RoutedResult(await self._run(self._azure, image, language), requested, False)

        primary_mode = self.preferred_mode()
        primary = self._azure if primary_mode is ProcessingMode.AZURE else self._local
        secondary = self._local if primary_mode is ProcessingMode.AZURE else None
        try:
            return RoutedResult(await self._run(primary, image, language), primary_mode, False)
        except ProviderError as error:
            if secondary is None or error.code not in RETRYABLE_CODES:
                raise
            result = await self._run(secondary, image, language)
            return RoutedResult(
                result,
                ProcessingMode.LOCAL,
                True,
                (f"fell back to the local provider after a retryable failure: {error.code.value}",),
            )

    async def _run(self, provider: OcrProvider, image: LoadedImage, language: str) -> OcrResult:
        try:
            async with asyncio.timeout(self._settings.provider_timeout_seconds):
                return await provider.analyze(image, language)
        except TimeoutError as exc:
            raise provider_timeout("OCR provider timed out") from exc

    async def health(self) -> list[tuple[str, str, str | None]]:
        statuses: list[tuple[str, str, str | None]] = []
        local_status, local_detail = await self._local.health()
        statuses.append(("provider:local_paddleocr", local_status, local_detail))
        if self._settings.provider_mode is not ProviderMode.LOCAL:
            azure_status, azure_detail = await self._azure.health()
            statuses.append(("provider:azure_content_understanding", azure_status, azure_detail))
        return statuses

    async def close(self) -> None:
        seen: set[int] = set()
        for provider in (self._local, self._azure):
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            if isinstance(provider, ClosableOcrProvider):
                await provider.close()
