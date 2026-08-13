from __future__ import annotations

import base64
import binascii
import csv
import io

from fastapi import HTTPException, status
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Settings
from .models import (
    BoundingBox,
    ExtractTextRequest,
    ExtractTextResponse,
    ImageMetadata,
    OutputFormat,
    TextFragment,
)
from .ocr import OcrEngine, OcrFragment


class TextExtractionService:
    def __init__(self, settings: Settings, engine: OcrEngine) -> None:
        self._settings = settings
        self._engine = engine

    def extract(self, request: ExtractTextRequest) -> ExtractTextResponse:
        image = self._decode_image(request.image_base64)
        fragments = sorted(
            self._engine.extract(image, request.language),
            key=lambda fragment: (_top(fragment), _left(fragment)),
        )
        response_fragments = [
            TextFragment(
                text=fragment.text,
                confidence=max(0.0, min(1.0, fragment.confidence)),
                bounding_box=_bounding_box(fragment) if request.include_coordinates else None,
            )
            for fragment in fragments
        ]
        return ExtractTextResponse(
            format=request.output_format,
            content=_format_content(response_fragments, request.output_format),
            fragments=response_fragments,
            image=ImageMetadata(width=image.width, height=image.height, mode=image.mode),
        )

    def _decode_image(self, encoded: str) -> Image.Image:
        payload = encoded.strip()
        if payload.startswith("data:"):
            header, separator, payload = payload.partition(",")
            if not separator or ";base64" not in header or not header.startswith("data:image/"):
                raise _bad_image("Only base64-encoded image data URLs are supported")
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise _bad_image("image_base64 is not valid base64") from exc
        if not raw:
            raise _bad_image("Decoded image is empty")
        if len(raw) > self._settings.max_image_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Image exceeds the {self._settings.max_image_bytes}-byte limit",
            )
        try:
            image = Image.open(io.BytesIO(raw))
            if image.width * image.height > self._settings.max_image_pixels:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Image exceeds the {self._settings.max_image_pixels}-pixel limit",
                )
            image.load()
        except HTTPException:
            raise
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise _bad_image("Decoded data is not a supported image") from exc
        return ImageOps.exif_transpose(image).convert("RGB")


def _bad_image(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _left(fragment: OcrFragment) -> float:
    return min((point[0] for point in fragment.points), default=0)


def _top(fragment: OcrFragment) -> float:
    return min((point[1] for point in fragment.points), default=0)


def _bounding_box(fragment: OcrFragment) -> BoundingBox:
    xs = [point[0] for point in fragment.points]
    ys = [point[1] for point in fragment.points]
    left, top = min(xs), min(ys)
    right, bottom = max(xs), max(ys)
    return BoundingBox(
        x=max(0, round(left)),
        y=max(0, round(top)),
        width=max(0, round(right - left)),
        height=max(0, round(bottom - top)),
    )


def _format_content(fragments: list[TextFragment], output_format: OutputFormat) -> str:
    if output_format is OutputFormat.TEXT:
        return "\n".join(fragment.text for fragment in fragments)
    if output_format is OutputFormat.CSV:
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("text", "confidence", "x", "y", "width", "height"))
        for fragment in fragments:
            box = fragment.bounding_box
            writer.writerow(
                (
                    fragment.text,
                    f"{fragment.confidence:.4f}",
                    box.x if box else "",
                    box.y if box else "",
                    box.width if box else "",
                    box.height if box else "",
                )
            )
        return stream.getvalue()
    return "\n".join(f"- {fragment.text}" for fragment in fragments)

