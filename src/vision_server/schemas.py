"""Strict tool contracts shared by every transport.

All request and response models are camelCase on the wire, forbid unknown
fields, and bound every collection so that responses stay compact.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from .text_limits import truncate_utf16, utf16_length

MAX_BLOCKS = 500
MAX_IMAGE_AXIS = 100_000
MAX_REGIONS = 100
SupportedLanguage = Literal["en", "ch", "fr", "german", "japan", "korean"]


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

    @field_validator("path")
    @classmethod
    def validate_path_length(cls, value: str) -> str:
        if utf16_length(value) > 4096:
            raise ValueError("path exceeds 4096 UTF-16 code units")
        return value


class AssetImage(StrictModel):
    """An opaque asset identifier previously returned by the asset API."""

    kind: Literal["asset"]
    asset_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


ImageReference = Annotated[LocalPathImage | AssetImage, Field(discriminator="kind")]


class Dimensions(StrictModel):
    width: int = Field(gt=0, le=MAX_IMAGE_AXIS)
    height: int = Field(gt=0, le=MAX_IMAGE_AXIS)


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

    warnings: list[Annotated[str, Field(max_length=300)]] = Field(
        default_factory=list, max_length=20
    )
    truncated: bool = False

    @field_validator("warnings", mode="before")
    @classmethod
    def bound_warnings(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return [truncate_utf16(item, 300) if isinstance(item, str) else item for item in value]
        return value


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


class AnalysisMode(StrEnum):
    EMBEDDED_SVG_TEXT = "embedded_svg_text"
    OCR = "ocr"


class AnalysisFactKind(StrEnum):
    IDENTITY = "identity"
    REVISION = "revision"
    ENVIRONMENT = "environment"
    LISTENER = "listener"
    INGRESS = "ingress"
    READINESS = "readiness"
    STATUS = "status"


class BlockType(StrEnum):
    LINE = "line"
    PARAGRAPH = "paragraph"
    WORD = "word"


class ExtractTextInput(StrictModel):
    image: ImageReference
    output_format: OutputFormat = OutputFormat.MARKDOWN
    language: SupportedLanguage | None = None
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

    @field_validator("text", mode="before")
    @classmethod
    def bound_text(cls, value: object) -> object:
        return truncate_utf16(value, 4000) if isinstance(value, str) else value


class ProviderInfo(StrictModel):
    """Provenance for the OCR result; never contains endpoints or secrets."""

    name: Literal[
        "embedded_svg_text",
        "local_paddleocr",
        "azure_content_understanding",
    ]
    mode: ProcessingMode
    model: str | None = Field(default=None, max_length=120)
    api_version: str | None = Field(default=None, max_length=40)

    @field_validator("model", mode="before")
    @classmethod
    def bound_model(cls, value: object) -> object:
        return truncate_utf16(value, 120) if isinstance(value, str) else value

    @field_validator("api_version", mode="before")
    @classmethod
    def bound_api_version(cls, value: object) -> object:
        return truncate_utf16(value, 40) if isinstance(value, str) else value


class ExtractTextOutput(StrictModel):
    content: str = Field(max_length=200_000)
    blocks: list[TextBlock] = Field(default_factory=list, max_length=MAX_BLOCKS)
    dimensions: Dimensions
    provider: ProviderInfo
    fallback_used: bool = False
    meta: ResultMeta = Field(default_factory=ResultMeta)

    @field_validator("content", mode="before")
    @classmethod
    def bound_content(cls, value: object) -> object:
        return truncate_utf16(value, 200_000) if isinstance(value, str) else value


# --------------------------------------------------------------------------
# analyze_image
# --------------------------------------------------------------------------


class AnalyzeImageInput(StrictModel):
    image: ImageReference
    language: SupportedLanguage | None = None
    processing_mode: ProcessingMode = ProcessingMode.AUTO
    max_facts: int = Field(default=50, ge=1, le=100)


class FactProvenance(StrictModel):
    source: Literal["embedded_svg_text", "ocr"]
    block_ids: list[str] = Field(min_length=1, max_length=20)


class AnalysisFact(StrictModel):
    kind: AnalysisFactKind
    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0.0, le=1.0)
    box: NormalizedBox | None = None
    provenance: FactProvenance

    @field_validator("label", mode="before")
    @classmethod
    def bound_label(cls, value: object) -> object:
        return truncate_utf16(value, 120) if isinstance(value, str) else value

    @field_validator("value", mode="before")
    @classmethod
    def bound_value(cls, value: object) -> object:
        return truncate_utf16(value, 1000) if isinstance(value, str) else value

    @field_validator("evidence", mode="before")
    @classmethod
    def bound_evidence(cls, value: object) -> object:
        return truncate_utf16(value, 4000) if isinstance(value, str) else value


class AnalyzeImageOutput(StrictModel):
    summary: str = Field(max_length=20_000)
    extracted_text: str = Field(max_length=200_000)
    facts: list[AnalysisFact] = Field(default_factory=list, max_length=100)
    dimensions: Dimensions
    provider: ProviderInfo
    analysis_mode: AnalysisMode
    fallback_used: bool = False
    fallback_reason: str | None = Field(default=None, max_length=500)
    meta: ResultMeta = Field(default_factory=ResultMeta)

    @field_validator("summary", mode="before")
    @classmethod
    def bound_summary(cls, value: object) -> object:
        return truncate_utf16(value, 20_000) if isinstance(value, str) else value

    @field_validator("extracted_text", mode="before")
    @classmethod
    def bound_extracted_text(cls, value: object) -> object:
        return truncate_utf16(value, 200_000) if isinstance(value, str) else value

    @field_validator("fallback_reason", mode="before")
    @classmethod
    def bound_fallback_reason(cls, value: object) -> object:
        return truncate_utf16(value, 500) if isinstance(value, str) else value


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
