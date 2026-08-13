from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from PIL import Image


@dataclass(frozen=True)
class OcrFragment:
    text: str
    confidence: float
    points: tuple[tuple[float, float], ...]


class OcrEngine(Protocol):
    def extract(self, image: Image.Image, language: str) -> list[OcrFragment]: ...


class PaddleOcrEngine:
    """Lazy PaddleOCR adapter so API startup does not eagerly load model weights."""

    def __init__(self) -> None:
        self._engines: dict[str, Any] = {}

    def extract(self, image: Image.Image, language: str) -> list[OcrFragment]:
        engine = self._engines.get(language)
        if engine is None:
            try:
                from paddleocr import PaddleOCR
            except ImportError as exc:
                raise RuntimeError(
                    "PaddleOCR is unavailable; install the project with the 'ml' extra"
                ) from exc
            engine = PaddleOCR(lang=language, use_doc_orientation_classify=False)
            self._engines[language] = engine

        if hasattr(engine, "predict"):
            return _parse_modern_results(engine.predict(image))
        return _parse_legacy_results(engine.ocr(image))


def _parse_modern_results(results: Iterable[Any]) -> list[OcrFragment]:
    fragments: list[OcrFragment] = []
    for result in results:
        payload = getattr(result, "json", result)
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
            points = _points(polygon)
            if text and points:
                fragments.append(OcrFragment(str(text), float(score), points))
    return fragments


def _parse_legacy_results(results: Any) -> list[OcrFragment]:
    fragments: list[OcrFragment] = []
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
            text, score = recognition
            points = _points(polygon)
            if text and points:
                fragments.append(OcrFragment(str(text), float(score), points))
    return fragments


def _points(value: Any) -> tuple[tuple[float, float], ...]:
    if not _is_sequence(value):
        return ()
    points: list[tuple[float, float]] = []
    for point in value:
        if not _is_sequence(point) or len(point) < 2:
            return ()
        points.append((float(point[0]), float(point[1])))
    return tuple(points)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))

