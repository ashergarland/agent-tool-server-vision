"""Local PaddleOCR provider.

Model weights are never downloaded during import, container build, CI, or
service startup: the first successful ``analyze`` call for an allow-listed
language triggers a lazy load into a bounded cache.

Model provenance (see ``docs/providers.md``):

* Source: PaddleOCR PP-OCRv5 mobile detection and recognition models,
  distributed by the PaddlePaddle project.
* Pinned revision: ``paddleocr==3.1.0`` with ``paddlepaddle==3.1.0``.
* License: Apache-2.0.
* Checksums are published by the upstream project and verified by PaddleOCR at
  download time; the digest of the pinned wheels is enforced by the lock in
  ``pyproject.toml``.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from threading import Lock
from typing import Any

from ..config import Settings
from ..imaging import LoadedImage
from .base import (
    OcrBlock,
    OcrResult,
    provider_invalid_input,
    provider_unavailable,
)

MODEL_PROVENANCE = {
    "family": "PP-OCRv5",
    "package": "paddleocr==3.1.0",
    "runtime": "paddlepaddle==3.1.0",
    "license": "Apache-2.0",
}


class PaddleOcrProvider:
    """Adapter around PaddleOCR with an allow-list and bounded engine cache."""

    name = "local_paddleocr"

    def __init__(self, settings: Settings, engine_factory: Any | None = None) -> None:
        self._settings = settings
        self._engine_factory = engine_factory
        self._engines: OrderedDict[str, Any] = OrderedDict()
        self._lock = Lock()

    async def analyze(self, image: LoadedImage, language: str) -> OcrResult:
        language = self._validate_language(language)
        blocks = await asyncio.to_thread(self._analyze_sync, image, language)
        return OcrResult(
            blocks=tuple(blocks),
            provider_name=self.name,
            model=MODEL_PROVENANCE["family"],
            api_version=None,
        )

    async def health(self) -> tuple[str, str | None]:
        """Readiness must not load model weights."""
        if self._engine_factory is not None:
            return "ok", None
        try:
            import importlib.util

            if importlib.util.find_spec("paddleocr") is None:
                return "unavailable", "paddleocr is not installed"
        except (ImportError, ValueError):  # pragma: no cover - defensive
            return "unavailable", "paddleocr is not importable"
        return "ok", "models load lazily on first use"

    # -- internals ----------------------------------------------------------

    def _validate_language(self, language: str) -> str:
        if language not in self._settings.allowed_language_set:
            raise provider_invalid_input("Requested language is not enabled for the local provider")
        return language

    def _analyze_sync(self, image: LoadedImage, language: str) -> list[OcrBlock]:
        engine = self._get_engine(language)
        try:
            import numpy
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise provider_unavailable("numpy is not installed") from exc
        pixels = numpy.asarray(image.image)
        if hasattr(engine, "predict"):
            return _parse_modern(engine.predict(pixels))
        return _parse_legacy(engine.ocr(pixels))

    def _get_engine(self, language: str) -> Any:
        with self._lock:
            engine = self._engines.get(language)
            if engine is not None:
                self._engines.move_to_end(language)
                return engine
            engine = self._create_engine(language)
            self._engines[language] = engine
            while len(self._engines) > self._settings.paddle_cache_size:
                self._engines.popitem(last=False)
            return engine

    def _create_engine(self, language: str) -> Any:
        if self._engine_factory is not None:
            return self._engine_factory(language)
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise provider_unavailable(
                "PaddleOCR is not installed; install the project with the 'ml' extra"
            ) from exc
        return PaddleOCR(lang=language, use_doc_orientation_classify=False)


def _parse_modern(results: Iterable[Any]) -> list[OcrBlock]:
    blocks: list[OcrBlock] = []
    for result in results:
        payload: Any = getattr(result, "json", result)
        if callable(payload):
            payload = payload()
        if not isinstance(payload, Mapping):
            continue
        payload = payload.get("res", payload)
        if not isinstance(payload, Mapping):
            continue
        texts = payload.get("rec_texts", ())
        scores = payload.get("rec_scores", ())
        polygons = payload.get("rec_polys", payload.get("dt_polys", ()))
        if not (_is_sequence(texts) and _is_sequence(scores) and _is_sequence(polygons)):
            continue
        for text, score, polygon in zip(texts, scores, polygons, strict=False):
            block = _block(text, score, polygon)
            if block is not None:
                blocks.append(block)
    return blocks


def _parse_legacy(results: Any) -> list[OcrBlock]:
    blocks: list[OcrBlock] = []
    pages = results if _is_sequence(results) else ()
    for page in pages:
        if not _is_sequence(page):
            continue
        for line in page:
            if not _is_sequence(line) or len(line) != 2:
                continue
            polygon, recognition = line
            if not _is_sequence(recognition) or len(recognition) != 2:
                continue
            block = _block(recognition[0], recognition[1], polygon)
            if block is not None:
                blocks.append(block)
    return blocks


def _block(text: Any, score: Any, polygon: Any) -> OcrBlock | None:
    confidence = _confidence(score)
    points = _points(polygon)
    if not text or not points:
        return None
    return OcrBlock(
        text=str(text), block_type="line", page=1, polygon=points, confidence=confidence
    )


def _points(value: Any) -> tuple[float, ...]:
    if not _is_sequence(value):
        return ()
    points: list[float] = []
    for point in value:
        if not _is_sequence(point) or len(point) < 2:
            return ()
        try:
            points.extend((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            return ()
    return tuple(points)


def _confidence(score: Any) -> float | None:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, value))


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)
