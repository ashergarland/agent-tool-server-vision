"""Central tool registry.

This module is the single source of truth for tool names, agent routing
descriptions, annotations, input and output schemas, handlers, MCP
registration, HTTP routes, and OpenAPI generation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from .errors import ErrorCode, VisionError
from .runtime import ToolContext
from .schemas import (
    CompareImagesInput,
    CompareImagesOutput,
    ExtractTextInput,
    ExtractTextOutput,
    OptimizeRegionInput,
    OptimizeRegionOutput,
)
from .tools.compare import compare_images
from .tools.extract import extract_text_and_layout
from .tools.optimize import optimize_image_region

Handler = Callable[[Any, ToolContext], Awaitable[BaseModel]]


@dataclass(frozen=True)
class ToolAnnotations:
    """MCP tool annotations mirrored into OpenAPI metadata."""

    read_only_hint: bool
    destructive_hint: bool
    idempotent_hint: bool
    open_world_hint: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "readOnlyHint": self.read_only_hint,
            "destructiveHint": self.destructive_hint,
            "idempotentHint": self.idempotent_hint,
            "openWorldHint": self.open_world_hint,
        }


@dataclass(frozen=True)
class ToolDefinition:
    """Everything a transport needs to expose a tool."""

    name: str
    title: str
    summary: str
    when_to_use: tuple[str, ...]
    when_not_to_use: tuple[str, ...]
    input_constraints: tuple[str, ...]
    determinism: str
    token_savings: str
    annotations: ToolAnnotations
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Handler

    @property
    def http_path(self) -> str:
        return f"/tools/{self.name}"

    @property
    def description(self) -> str:
        lines = [self.summary, "", "When to use:"]
        lines += [f"- {item}" for item in self.when_to_use]
        lines += ["", "When not to use:"]
        lines += [f"- {item}" for item in self.when_not_to_use]
        lines += ["", "Input constraints:"]
        lines += [f"- {item}" for item in self.input_constraints]
        lines += ["", f"Behaviour: {self.determinism}", f"Token savings: {self.token_savings}"]
        return "\n".join(lines)

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema(by_alias=True)

    def output_schema(self) -> dict[str, Any]:
        return self.output_model.model_json_schema(by_alias=True)

    def parse_input(self, arguments: Mapping[str, Any]) -> BaseModel:
        try:
            return self.input_model.model_validate(dict(arguments))
        except ValidationError as exc:
            raise VisionError(
                ErrorCode.INVALID_INPUT,
                "Tool arguments failed validation",
                details={"errors": _first_errors(exc)},
            ) from exc

    async def run(self, arguments: Mapping[str, Any], context: ToolContext) -> BaseModel:
        payload = self.parse_input(arguments)
        return await context.runtime.queue.run(lambda: self.handler(payload, context))


_IMAGE_REFERENCE_CONSTRAINT = (
    "Images are referenced only as {'kind':'local_path','path':...} inside the configured allowed "
    "roots or {'kind':'asset','assetId':...}; base64, data URLs, remote URLs, storage URLs, and "
    "SAS URLs are rejected."
)


EXTRACT_TEXT_AND_LAYOUT = ToolDefinition(
    name="extract_text_and_layout",
    title="Extract text and layout",
    summary=(
        "Read text and layout from an image and return formatted content plus ordered text "
        "blocks with normalized coordinates."
    ),
    when_to_use=(
        "OCR screenshots, scanned pages, tables, forms, labels, and UI captures.",
        "Any task whose goal is the text or reading order of an image.",
        "Before native LLM vision whenever text or layout is what you need.",
    ),
    when_not_to_use=(
        "Describing scenes, identifying objects, or answering open visual questions.",
        "Parsing diagrams, detecting objects, or grounding text to UI controls; these are not "
        "supported in this phase.",
    ),
    input_constraints=(
        _IMAGE_REFERENCE_CONSTRAINT,
        "outputFormat is markdown, text, or csv; processingMode is auto, local, or azure.",
        "Forced local or azure modes never switch providers.",
    ),
    determinism=(
        "Provider backed. The local PaddleOCR provider and the managed Azure Content "
        "Understanding provider are normalized to the same block schema; auto mode falls back to "
        "local only after a retryable provider failure."
    ),
    token_savings=(  # noqa: S106 - descriptive text, not a credential
        "Returns compact text and normalized boxes instead of the image, avoiding full-image "
        "vision tokens."
    ),
    annotations=ToolAnnotations(True, False, True, False),
    input_model=ExtractTextInput,
    output_model=ExtractTextOutput,
    handler=extract_text_and_layout,
)


COMPARE_IMAGES = ToolDefinition(
    name="compare_images",
    title="Compare two images",
    summary=(
        "Compare a before and after image and return a similarity score, changed pixel counts, "
        "and deterministic changed regions."
    ),
    when_to_use=(
        "Screenshot regression checks and before/after change detection.",
        "Deciding whether anything changed before inspecting an image visually.",
        "Locating the regions that changed so a later crop can be targeted.",
    ),
    when_not_to_use=(
        "Explaining why something changed or judging visual quality.",
        "Comparing images that are not pixel comparable, such as different renderings of "
        "unrelated content.",
    ),
    input_constraints=(
        _IMAGE_REFERENCE_CONSTRAINT,
        "threshold is 0.0-1.0 as a fraction of the channel range; maxRegions is 1-100.",
        "Unequal sizes compare the overlapping region and count the remainder as changed.",
    ),
    determinism=(
        "Fully deterministic and local: no model or network call is involved and repeated calls "
        "on the same inputs return identical output."
    ),
    token_savings=(  # noqa: S106 - descriptive text, not a credential
        "Replaces sending two full images to native vision with a small numeric summary and at "
        "most a few region boxes."
    ),
    annotations=ToolAnnotations(True, False, True, False),
    input_model=CompareImagesInput,
    output_model=CompareImagesOutput,
    handler=compare_images,
)


OPTIMIZE_IMAGE_REGION = ToolDefinition(
    name="optimize_image_region",
    title="Crop and optimize an image region",
    summary=(
        "Crop a known pixel region, optionally downscale it, and store the compressed result as "
        "an opaque artifact."
    ),
    when_to_use=(
        "You already know the coordinates worth inspecting, for example from compare_images or "
        "extract_text_and_layout.",
        "You must send a small, cheap crop to native vision instead of a full screenshot.",
    ),
    when_not_to_use=(
        "You do not know which region matters yet; find it first.",
        "You need the text itself, which extract_text_and_layout returns directly.",
    ),
    input_constraints=(
        _IMAGE_REFERENCE_CONSTRAINT,
        "box is pixels with a top-left origin: x and y are the inclusive left and top edges.",
        "The box must lie inside the image; it is rejected rather than clamped. maxWidth and "
        "maxHeight are 16-8192, quality is 1-100, and outputFormat is png, jpeg, or webp.",
    ),
    determinism=(
        "Fully deterministic and local: crops never upscale and resizing uses a fixed Lanczos "
        "filter."
    ),
    token_savings=(  # noqa: S106 - descriptive text, not a credential
        "A cropped, downscaled artifact costs a fraction of the image tokens of the original "
        "screenshot."
    ),
    annotations=ToolAnnotations(False, False, False, False),
    input_model=OptimizeRegionInput,
    output_model=OptimizeRegionOutput,
    handler=optimize_image_region,
)


TOOLS: tuple[ToolDefinition, ...] = (
    EXTRACT_TEXT_AND_LAYOUT,
    COMPARE_IMAGES,
    OPTIMIZE_IMAGE_REGION,
)

TOOLS_BY_NAME: dict[str, ToolDefinition] = {tool.name: tool for tool in TOOLS}


SERVER_INSTRUCTIONS = (
    "Token-efficient image tools. Routing rules: "
    "1) When the goal is text or layout, run OCR first instead of sending the image to native "
    "vision. "
    "2) For before/after questions, compare the two images first and use the returned regions. "
    "3) When the interesting coordinates are already known, optimize that region before "
    "inspecting it. "
    "4) Use native LLM vision only when these tools cannot answer the question, for example "
    "scene description, object detection, diagram parsing, or visual question answering, which "
    "this server does not provide."
)


def get_tool(name: str) -> ToolDefinition:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise VisionError(ErrorCode.NOT_FOUND, "Unknown tool", details={"tool": name})
    return tool


def _first_errors(exc: ValidationError, limit: int = 5) -> str:
    parts: list[str] = []
    for error in exc.errors()[:limit]:
        location = ".".join(str(item) for item in error.get("loc", ()))
        parts.append(f"{location}: {error.get('msg', 'invalid')}")
    return "; ".join(parts)
