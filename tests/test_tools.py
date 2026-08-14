"""Tool contract tests for the three registered tools."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from vision_server.errors import ErrorCode, VisionError
from vision_server.providers.base import (
    OcrBlock,
    provider_auth_error,
    provider_unavailable,
)
from vision_server.registry import get_tool
from vision_server.runtime import ToolContext
from vision_server.schemas import (
    BoundingBox,
    CompareImagesInput,
    CompareImagesOutput,
    ExtractTextInput,
    ExtractTextOutput,
    ImageFormat,
    LocalPathImage,
    OptimizeRegionInput,
    OptimizeRegionOutput,
    OutputFormat,
    ProcessingMode,
)
from vision_server.tools import compare_images, extract_text_and_layout, optimize_image_region

from .conftest import FakeOcrProvider, write_png


def reference(path: Path) -> LocalPathImage:
    return LocalPathImage(kind="local_path", path=str(path))


# -- extract_text_and_layout ------------------------------------------------


async def test_extract_orders_blocks_and_normalizes_boxes(
    context: ToolContext, allowed_root: Path
) -> None:
    path = write_png(allowed_root / "page.png", 100, 50)
    result = await extract_text_and_layout(ExtractTextInput(image=reference(path)), context)
    assert isinstance(result, ExtractTextOutput)
    assert [block.text for block in result.blocks] == ["first", "second"]
    assert [block.id for block in result.blocks] == ["b0000", "b0001"]
    first = result.blocks[0]
    assert first.box is not None
    assert first.box.x == pytest.approx(0.1)
    assert first.box.y == pytest.approx(0.04)
    assert result.dimensions.width == 100
    assert result.content == "- first\n- second"
    assert result.fallback_used is False


async def test_extract_supports_text_and_csv_formats(
    context: ToolContext, allowed_root: Path
) -> None:
    path = write_png(allowed_root / "page.png")
    text = await extract_text_and_layout(
        ExtractTextInput(image=reference(path), output_format=OutputFormat.TEXT), context
    )
    assert text.content == "first\nsecond"
    csv_result = await extract_text_and_layout(
        ExtractTextInput(image=reference(path), output_format=OutputFormat.CSV), context
    )
    assert csv_result.content.splitlines()[0] == "id,text,confidence,x,y,width,height"
    assert "first" in csv_result.content


async def test_extract_can_omit_coordinates(context: ToolContext, allowed_root: Path) -> None:
    path = write_png(allowed_root / "page.png")
    result = await extract_text_and_layout(
        ExtractTextInput(image=reference(path), include_coordinates=False), context
    )
    assert all(block.box is None and block.polygon is None for block in result.blocks)


async def test_forced_modes_never_switch_provider(
    context: ToolContext,
    allowed_root: Path,
    local_provider: FakeOcrProvider,
    azure_provider: FakeOcrProvider,
) -> None:
    path = write_png(allowed_root / "page.png")
    context.runtime.settings.__dict__["azure_content_understanding_endpoint"] = (
        "https://example.invalid"
    )
    azure_provider._error = provider_unavailable("down")  # noqa: SLF001

    with pytest.raises(VisionError) as error:
        await extract_text_and_layout(
            ExtractTextInput(image=reference(path), processing_mode=ProcessingMode.AZURE), context
        )
    assert error.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert local_provider.calls == []


async def test_auto_mode_falls_back_only_on_retryable_failures(
    context: ToolContext,
    allowed_root: Path,
    local_provider: FakeOcrProvider,
    azure_provider: FakeOcrProvider,
) -> None:
    path = write_png(allowed_root / "page.png")
    context.runtime.settings.__dict__["azure_content_understanding_endpoint"] = (
        "https://example.invalid"
    )
    context.runtime.settings.__dict__["provider_mode"] = "auto"

    azure_provider._error = provider_unavailable("down")  # noqa: SLF001
    result = await extract_text_and_layout(
        ExtractTextInput(image=reference(path), processing_mode=ProcessingMode.AUTO), context
    )
    assert result.fallback_used is True
    assert result.provider.name == "local_paddleocr"
    assert result.meta.warnings

    azure_provider._error = provider_auth_error("denied")  # noqa: SLF001
    with pytest.raises(VisionError) as error:
        await extract_text_and_layout(
            ExtractTextInput(image=reference(path), processing_mode=ProcessingMode.AUTO), context
        )
    assert error.value.code is ErrorCode.FORBIDDEN


async def test_extract_truncates_long_block_lists(
    context: ToolContext, allowed_root: Path, local_provider: FakeOcrProvider
) -> None:
    path = write_png(allowed_root / "page.png")
    local_provider._blocks = tuple(  # noqa: SLF001
        OcrBlock(
            f"line-{index}",
            "line",
            1,
            (0.0, float(index), 5.0, float(index) + 1.0, 5.0, float(index) + 2.0),
            0.5,
        )
        for index in range(600)
    )
    result = await extract_text_and_layout(ExtractTextInput(image=reference(path)), context)
    assert len(result.blocks) == 500
    assert result.meta.truncated is True


async def test_extract_prefers_provider_markdown(
    context: ToolContext, allowed_root: Path, local_provider: FakeOcrProvider
) -> None:
    path = write_png(allowed_root / "page.png")
    local_provider._markdown = "# Title"  # noqa: SLF001
    result = await extract_text_and_layout(ExtractTextInput(image=reference(path)), context)
    assert result.content == "# Title"


# -- compare_images ---------------------------------------------------------


async def test_compare_identical_images_is_deterministic(
    context: ToolContext, allowed_root: Path
) -> None:
    before = write_png(allowed_root / "before.png", 40, 20)
    after = write_png(allowed_root / "after.png", 40, 20)
    payload = CompareImagesInput(before=reference(before), after=reference(after))
    first = await compare_images(payload, context)
    second = await compare_images(payload, context)
    assert isinstance(first, CompareImagesOutput)
    assert first.model_dump() == second.model_dump()
    assert first.similarity_score == 1.0
    assert first.changed_pixels == 0
    assert first.regions == []
    assert first.diff_artifact_id is None


async def test_compare_detects_regions_and_stores_diff(
    context: ToolContext, allowed_root: Path
) -> None:
    before = write_png(allowed_root / "before.png", 64, 64)
    changed = Image.new("RGB", (64, 64), "white")
    for x in range(4, 12):
        for y in range(4, 12):
            changed.putpixel((x, y), (0, 0, 0))
    after = allowed_root / "after.png"
    changed.save(after, format="PNG")

    result = await compare_images(
        CompareImagesInput(before=reference(before), after=reference(after), include_diff=True),
        context,
    )
    assert result.changed_pixels == 64
    assert result.regions[0].box.x == 4
    assert result.regions[0].box.width == 8
    assert result.regions[0].changed_pixels == 64
    assert result.diff_artifact_id is not None
    _record, payload = await context.assets.get(context.principal, result.diff_artifact_id)
    assert Image.open(io.BytesIO(payload)).size == (64, 64)


async def test_compare_threshold_controls_sensitivity(
    context: ToolContext, allowed_root: Path
) -> None:
    before = write_png(allowed_root / "before.png", 16, 16)
    subtle = Image.new("RGB", (16, 16), (250, 250, 250))
    after = allowed_root / "after.png"
    subtle.save(after, format="PNG")

    strict = await compare_images(
        CompareImagesInput(before=reference(before), after=reference(after), threshold=0.0),
        context,
    )
    assert strict.changed_pixels == 256

    lenient = await compare_images(
        CompareImagesInput(before=reference(before), after=reference(after), threshold=0.5),
        context,
    )
    assert lenient.changed_pixels == 0
    assert lenient.similarity_score < 1.0


async def test_compare_handles_unequal_sizes(context: ToolContext, allowed_root: Path) -> None:
    before = write_png(allowed_root / "before.png", 20, 10)
    after = write_png(allowed_root / "after.png", 40, 10)
    result = await compare_images(
        CompareImagesInput(before=reference(before), after=reference(after)), context
    )
    assert result.dimensions_changed is True
    assert result.comparison_dimensions.width == 20
    assert result.changed_pixels == 200
    assert result.changed_ratio == pytest.approx(0.5)
    assert any("non-overlapping" in warning for warning in result.meta.warnings)


async def test_compare_truncates_region_list(context: ToolContext, allowed_root: Path) -> None:
    before = write_png(allowed_root / "before.png", 128, 128)
    speckled = Image.new("RGB", (128, 128), "white")
    for index in range(4):
        speckled.putpixel((index * 40, index * 40), (0, 0, 0))
    after = allowed_root / "after.png"
    speckled.save(after, format="PNG")
    result = await compare_images(
        CompareImagesInput(before=reference(before), after=reference(after), max_regions=2), context
    )
    assert len(result.regions) == 2
    assert result.meta.truncated is True


# -- optimize_image_region --------------------------------------------------


async def test_optimize_crops_without_upscaling(context: ToolContext, allowed_root: Path) -> None:
    path = write_png(allowed_root / "image.png", 200, 100, "red")
    result = await optimize_image_region(
        OptimizeRegionInput(
            image=reference(path),
            box=BoundingBox(x=10, y=10, width=50, height=20),
            max_width=4000,
            max_height=4000,
        ),
        context,
    )
    assert isinstance(result, OptimizeRegionOutput)
    assert result.crop_dimensions.width == 50
    assert result.output_dimensions.width == 50
    assert result.normalized_box.x == pytest.approx(0.05)
    _record, payload = await context.assets.get(context.principal, result.artifact_id)
    assert len(payload) == result.byte_count


async def test_optimize_downscales_deterministically(
    context: ToolContext, allowed_root: Path
) -> None:
    path = write_png(allowed_root / "image.png", 400, 400, "blue")
    payload = OptimizeRegionInput(
        image=reference(path),
        box=BoundingBox(x=0, y=0, width=400, height=400),
        max_width=100,
        max_height=100,
        output_format=ImageFormat.PNG,
    )
    first = await optimize_image_region(payload, context)
    second = await optimize_image_region(payload, context)
    assert first.output_dimensions.width == 100
    assert first.byte_count == second.byte_count
    assert first.artifact_id != second.artifact_id
    assert first.meta.warnings


@pytest.mark.parametrize(
    "box",
    [
        BoundingBox(x=500, y=0, width=10, height=10),
        BoundingBox(x=0, y=0, width=500, height=10),
        BoundingBox(x=90, y=0, width=20, height=10),
    ],
)
async def test_optimize_rejects_boxes_outside_the_image(
    context: ToolContext, allowed_root: Path, box: BoundingBox
) -> None:
    path = write_png(allowed_root / "image.png", 100, 50)
    with pytest.raises(VisionError) as error:
        await optimize_image_region(OptimizeRegionInput(image=reference(path), box=box), context)
    assert error.value.code is ErrorCode.INVALID_INPUT


async def test_optimize_rejects_empty_boxes(context: ToolContext, allowed_root: Path) -> None:
    path = write_png(allowed_root / "image.png", 100, 50)
    with pytest.raises(ValidationError):
        OptimizeRegionInput(image=reference(path), box=BoundingBox(x=0, y=0, width=0, height=5))


@pytest.mark.parametrize("output_format", ["png", "jpeg", "webp"])
async def test_optimize_supports_every_format(
    context: ToolContext, allowed_root: Path, output_format: str
) -> None:
    path = write_png(allowed_root / "image.png", 64, 64, "green")
    result = await optimize_image_region(
        OptimizeRegionInput(
            image=reference(path),
            box=BoundingBox(x=0, y=0, width=32, height=32),
            output_format=ImageFormat(output_format),
        ),
        context,
    )
    assert result.format.value == output_format
    _record, payload = await context.assets.get(context.principal, result.artifact_id)
    assert Image.open(io.BytesIO(payload)).format is not None


async def test_registry_run_validates_arguments(context: ToolContext, allowed_root: Path) -> None:
    tool = get_tool("optimize_image_region")
    path = write_png(allowed_root / "image.png")
    with pytest.raises(VisionError) as error:
        await tool.run({"image": {"kind": "local_path", "path": str(path)}}, context)
    assert error.value.code is ErrorCode.INVALID_INPUT
    assert "box" in error.value.details["errors"]
