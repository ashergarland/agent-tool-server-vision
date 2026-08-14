"""Strict tool contracts shared by every transport.

All request and response models are camelCase on the wire, forbid unknown
fields, and bound every collection so that responses stay compact.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

MAX_BLOCKS = 500
MAX_REGIONS = 100


class StrictModel(BaseModel):
    """Base model: camelCase aliases, no extra fields, bounded output."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        frozen=False,
    )


# --------------------------------------------------------------------------
# Image references
# --------------------------------------------------------------------------


class LocalPathImage(StrictModel):
    """A regular file beneath one of the configured allowed roots."""

    kind: Literal["local_path"]
    path: str = Field(min_length=1, max_length=4096)


class AssetImage(StrictModel):
    """An opaque asset identifier previously returned by the asset API."""

    kind: Literal["asset"]
    asset_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


ImageReference = Annotated[LocalPathImage | AssetImage, Field(discriminator="kind")]


class Dimensions(StrictModel):
    width: int = Field(gt=0, le=100_000)
    height: int = Field(gt=0, le=100_000)


class BoundingBox(StrictModel):
    """Pixel box using the top-left origin convention: x/y are inclusive."""

    x: int = Field(ge=0, le=1_000_000)
    y: int = Field(ge=0, le=1_000_000)
    width: int = Field(gt=0, le=1_000_000)
    height: int = Field(gt=0, le=1_000_000)


class NormalizedBox(StrictModel):
    """Box expressed as fractions of image width and height."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(ge=0.0, le=1.0)
    height: float = Field(ge=0.0, le=1.0)


class ResultMeta(StrictModel):
    """Truncation and warning metadata attached to every tool result."""

    warnings: list[str] = Field(default_factory=list, max_length=20)
    truncated: bool = False


# --------------------------------------------------------------------------
# extract_text_and_layout
# --------------------------------------------------------------------------


class OutputFormat(StrEnum):
    MARKDOWN = "markdown"
    TEXT = "text"
    CSV = "csv"


class ProcessingMode(StrEnum):
    AUTO = "auto"
    LOCAL = "local"
    AZURE = "azure"


class BlockType(StrEnum):
    LINE = "line"
    PARAGRAPH = "paragraph"
    WORD = "word"


class ExtractTextInput(StrictModel):
    image: ImageReference
    output_format: OutputFormat = OutputFormat.MARKDOWN
    language: str | None = Field(
        default=None, min_length=2, max_length=16, pattern=r"^[A-Za-z0-9_-]+$"
    )
    include_coordinates: bool = True
    processing_mode: ProcessingMode = ProcessingMode.AUTO


class TextBlock(StrictModel):
    id: str = Field(min_length=1, max_length=32)
    type: BlockType
    text: str = Field(max_length=4000)
    page: int = Field(ge=1, le=1000)
    box: NormalizedBox | None = None
    polygon: list[float] | None = Field(default=None, max_length=32)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ProviderInfo(StrictModel):
    """Provenance for the OCR result; never contains endpoints or secrets."""

    name: Literal["local_paddleocr", "azure_content_understanding"]
    mode: ProcessingMode
    model: str | None = Field(default=None, max_length=120)
    api_version: str | None = Field(default=None, max_length=40)


class ExtractTextOutput(StrictModel):
    content: str = Field(max_length=200_000)
    blocks: list[TextBlock] = Field(default_factory=list, max_length=MAX_BLOCKS)
    dimensions: Dimensions
    provider: ProviderInfo
    fallback_used: bool = False
    meta: ResultMeta = Field(default_factory=ResultMeta)


# --------------------------------------------------------------------------
# compare_images
# --------------------------------------------------------------------------


class CompareImagesInput(StrictModel):
    before: ImageReference
    after: ImageReference
    threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    include_diff: bool = False
    max_regions: int = Field(default=20, ge=1, le=MAX_REGIONS)


class ChangedRegion(StrictModel):
    box: BoundingBox
    changed_pixels: int = Field(ge=1)


class CompareImagesOutput(StrictModel):
    similarity_score: float = Field(ge=0.0, le=1.0)
    before_dimensions: Dimensions
    after_dimensions: Dimensions
    dimensions_changed: bool
    comparison_dimensions: Dimensions
    changed_pixels: int = Field(ge=0)
    changed_ratio: float = Field(ge=0.0, le=1.0)
    regions: list[ChangedRegion] = Field(default_factory=list, max_length=MAX_REGIONS)
    diff_artifact_id: str | None = Field(default=None, max_length=128)
    meta: ResultMeta = Field(default_factory=ResultMeta)


# --------------------------------------------------------------------------
# optimize_image_region
# --------------------------------------------------------------------------


class ImageFormat(StrEnum):
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"


class OptimizeRegionInput(StrictModel):
    image: ImageReference
    box: BoundingBox
    max_width: int = Field(default=1024, ge=16, le=8192)
    max_height: int = Field(default=1024, ge=16, le=8192)
    quality: int = Field(default=80, ge=1, le=100)
    output_format: ImageFormat = ImageFormat.WEBP


class OptimizeRegionOutput(StrictModel):
    artifact_id: str = Field(min_length=8, max_length=128)
    original_dimensions: Dimensions
    crop_dimensions: Dimensions
    output_dimensions: Dimensions
    byte_count: int = Field(ge=1)
    original_byte_count: int = Field(ge=1)
    format: ImageFormat
    normalized_box: NormalizedBox
    meta: ResultMeta = Field(default_factory=ResultMeta)


# --------------------------------------------------------------------------
# Service models
# --------------------------------------------------------------------------


class HealthResponse(StrictModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class ComponentStatus(StrictModel):
    name: str
    status: Literal["ok", "degraded", "unavailable"]
    detail: str | None = None


class ReadinessResponse(StrictModel):
    status: Literal["ready", "not_ready"]
    service: str
    version: str
    tools: list[str]
    components: list[ComponentStatus]
    configuration: dict[str, str | int | bool]


class AssetUploadResponse(StrictModel):
    asset_id: str
    byte_count: int
    content_type: str
    expires_at: str
