"""OCR providers.

Phase 1 ships a local PaddleOCR provider and the managed Azure AI Content
Understanding provider. An Azure Machine Learning provider is a documented
extension point: implement :class:`~vision_server.providers.base.OcrProvider`
and register it in :class:`~vision_server.providers.router.OcrRouter`. No Azure
ML resource is provisioned or called in this phase.
"""

from .base import OcrBlock, OcrProvider, OcrResult, ProviderError
from .content_understanding import ContentUnderstandingProvider
from .paddle import MODEL_PROVENANCE, PaddleOcrProvider
from .router import OcrRouter, RoutedResult

__all__ = [
    "MODEL_PROVENANCE",
    "ContentUnderstandingProvider",
    "OcrBlock",
    "OcrProvider",
    "OcrResult",
    "OcrRouter",
    "PaddleOcrProvider",
    "ProviderError",
    "RoutedResult",
]
