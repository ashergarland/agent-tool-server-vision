"""``extract_text_and_layout`` handler."""

from __future__ import annotations

import csv
import io

from ..imaging import LoadedImage, load_image
from ..providers.base import OcrBlock
from ..runtime import ToolContext
from ..schemas import (
    MAX_BLOCKS,
    BlockType,
    Dimensions,
    ExtractTextInput,
    ExtractTextOutput,
    NormalizedBox,
    OutputFormat,
    ProcessingMode,
    ProviderInfo,
    ResultMeta,
    TextBlock,
)

MAX_CONTENT_CHARS = 200_000
MAX_BLOCK_CHARS = 4000


async def extract_text_and_layout(
    payload: ExtractTextInput, context: ToolContext
) -> ExtractTextOutput:
    image = await load_image(payload.image, context.settings, context.assets, context.principal)
    language = payload.language or context.settings.default_language
    routed = await context.router.analyze(image, language, payload.processing_mode)

    warnings = list(routed.warnings) + list(routed.result.warnings)
    ordered = sorted(routed.result.blocks, key=lambda block: _order_key(block))
    truncated = len(ordered) > MAX_BLOCKS
    if truncated:
        ordered = ordered[:MAX_BLOCKS]
        warnings.append(f"block list truncated to {MAX_BLOCKS} entries")

    blocks = [
        _to_schema(index, block, image, payload.include_coordinates)
        for index, block in enumerate(ordered)
    ]
    content, content_truncated = _format(blocks, routed.result.markdown, payload.output_format)
    if content_truncated:
        warnings.append("formatted content truncated")

    return ExtractTextOutput(
        content=content,
        blocks=blocks,
        dimensions=Dimensions(width=image.width, height=image.height),
        provider=ProviderInfo(
            name=routed.result.provider_name,  # type: ignore[arg-type]
            mode=routed.mode if routed.mode is not ProcessingMode.AUTO else ProcessingMode.LOCAL,
            model=routed.result.model,
            api_version=routed.result.api_version,
        ),
        fallback_used=routed.fallback_used,
        meta=ResultMeta(warnings=warnings[:20], truncated=truncated or content_truncated),
    )


def _order_key(block: OcrBlock) -> tuple[int, float, float]:
    xs = block.polygon[0::2]
    ys = block.polygon[1::2]
    return (block.page, min(ys, default=0.0), min(xs, default=0.0))


def _to_schema(
    index: int, block: OcrBlock, image: LoadedImage, include_coordinates: bool
) -> TextBlock:
    box: NormalizedBox | None = None
    polygon: list[float] | None = None
    if include_coordinates and block.polygon:
        xs = [_clamp(value / image.width) for value in block.polygon[0::2]]
        ys = [_clamp(value / image.height) for value in block.polygon[1::2]]
        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)
        box = NormalizedBox(
            x=round(left, 6),
            y=round(top, 6),
            width=round(max(0.0, right - left), 6),
            height=round(max(0.0, bottom - top), 6),
        )
        polygon = [round(value, 6) for value in _interleave(xs, ys)][:32]
    return TextBlock(
        id=f"b{index:04d}",
        type=BlockType(block.block_type) if block.block_type in set(BlockType) else BlockType.LINE,
        text=block.text[:MAX_BLOCK_CHARS],
        page=block.page,
        box=box,
        polygon=polygon,
        confidence=block.confidence,
    )


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _interleave(xs: list[float], ys: list[float]) -> list[float]:
    values: list[float] = []
    for x, y in zip(xs, ys, strict=False):
        values.extend((_clamp(x), _clamp(y)))
    return values


def _format(
    blocks: list[TextBlock], markdown: str | None, output_format: OutputFormat
) -> tuple[str, bool]:
    if output_format is OutputFormat.TEXT:
        content = "\n".join(block.text for block in blocks)
    elif output_format is OutputFormat.CSV:
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("id", "text", "confidence", "x", "y", "width", "height"))
        for block in blocks:
            box = block.box
            writer.writerow(
                (
                    block.id,
                    block.text,
                    "" if block.confidence is None else f"{block.confidence:.4f}",
                    "" if box is None else f"{box.x:.6f}",
                    "" if box is None else f"{box.y:.6f}",
                    "" if box is None else f"{box.width:.6f}",
                    "" if box is None else f"{box.height:.6f}",
                )
            )
        content = stream.getvalue()
    elif markdown:
        content = markdown
    else:
        content = "\n".join(f"- {block.text}" for block in blocks)
    if len(content) > MAX_CONTENT_CHARS:
        return content[:MAX_CONTENT_CHARS], True
    return content, False
