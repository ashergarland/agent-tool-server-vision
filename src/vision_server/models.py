from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class OutputFormat(StrEnum):
    MARKDOWN = "markdown"
    TEXT = "text"
    CSV = "csv"


class ExtractTextRequest(BaseModel):
    image_base64: str = Field(
        min_length=1,
        description="Base64-encoded image, optionally prefixed with an image data-URL header.",
    )
    output_format: OutputFormat = OutputFormat.MARKDOWN
    language: str | None = Field(
        default=None, min_length=2, max_length=16, pattern=r"^[A-Za-z0-9_-]+$"
    )
    include_coordinates: bool = True

    @field_validator("image_base64")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("image_base64 must not be blank")
        return value


class BoundingBox(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=0)
    height: int = Field(ge=0)


class TextFragment(BaseModel):
    text: str
    confidence: float = Field(ge=0, le=1)
    bounding_box: BoundingBox | None = None


class ImageMetadata(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    mode: str


class ExtractTextResponse(BaseModel):
    tool: str = "extract_text_and_layout"
    format: OutputFormat
    content: str
    fragments: list[TextFragment]
    image: ImageMetadata


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
