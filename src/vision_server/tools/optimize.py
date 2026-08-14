"""``optimize_image_region`` handler.

The bounding box convention is pixel coordinates with the origin at the
top-left corner: ``x``/``y`` are the inclusive left and top edges and
``width``/``height`` extend right and down. The box must lie fully inside the
image; boxes that are empty or partially outside are rejected rather than
clamped. Crops are never upscaled and resizing is deterministic (Lanczos).
"""

from __future__ import annotations

import asyncio

from ..assets.base import CONTENT_TYPE_BY_FORMAT, AssetKind, single_chunk
from ..errors import ErrorCode, VisionError
from ..imaging import LoadedImage, encode_image, load_image
from ..runtime import ToolContext
from ..schemas import (
    Dimensions,
    ImageFormat,
    NormalizedBox,
    OptimizeRegionInput,
    OptimizeRegionOutput,
    ResultMeta,
)


async def optimize_image_region(
    payload: OptimizeRegionInput, context: ToolContext
) -> OptimizeRegionOutput:
    image = await load_image(payload.image, context.settings, context.assets, context.principal)
    _validate_box(payload, image)
    encoded, crop_size, output_size, warnings = await asyncio.to_thread(_render, payload, image)
    record = await context.assets.put(
        context.principal,
        single_chunk(encoded),
        CONTENT_TYPE_BY_FORMAT[payload.output_format.value],
        AssetKind.ARTIFACT,
    )
    box = payload.box
    return OptimizeRegionOutput(
        artifact_id=record.asset_id,
        original_dimensions=Dimensions(width=image.width, height=image.height),
        crop_dimensions=Dimensions(width=crop_size[0], height=crop_size[1]),
        output_dimensions=Dimensions(width=output_size[0], height=output_size[1]),
        byte_count=len(encoded),
        original_byte_count=image.byte_count,
        format=payload.output_format,
        normalized_box=NormalizedBox(
            x=round(box.x / image.width, 6),
            y=round(box.y / image.height, 6),
            width=round(box.width / image.width, 6),
            height=round(box.height / image.height, 6),
        ),
        meta=ResultMeta(warnings=warnings[:20], truncated=False),
    )


def _validate_box(payload: OptimizeRegionInput, image: LoadedImage) -> None:
    box = payload.box
    if box.x >= image.width or box.y >= image.height:
        raise VisionError(ErrorCode.INVALID_INPUT, "Bounding box starts outside the image")
    if box.x + box.width > image.width or box.y + box.height > image.height:
        raise VisionError(
            ErrorCode.INVALID_INPUT,
            "Bounding box extends beyond the image; boxes are never clamped",
            details={"imageWidth": image.width, "imageHeight": image.height},
        )


def _render(
    payload: OptimizeRegionInput, image: LoadedImage
) -> tuple[bytes, tuple[int, int], tuple[int, int], list[str]]:
    from PIL import Image as PillowImage

    box = payload.box
    crop = image.image.crop((box.x, box.y, box.x + box.width, box.y + box.height))
    warnings: list[str] = []
    scale = min(payload.max_width / crop.width, payload.max_height / crop.height, 1.0)
    if scale < 1.0:
        target = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
        crop = crop.resize(target, resample=PillowImage.Resampling.LANCZOS)
        warnings.append("crop was downscaled to fit the requested maximum dimensions")
    if payload.output_format is ImageFormat.JPEG:
        crop = crop.convert("RGB")
    encoded = encode_image(crop, payload.output_format.value, payload.quality)
    return encoded, (box.width, box.height), (crop.width, crop.height), warnings
