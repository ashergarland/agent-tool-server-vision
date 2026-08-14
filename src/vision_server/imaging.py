"""Image resolution, validation, and normalization.

Only two input shapes are accepted: a local path beneath an allowed root, or an
opaque asset identifier. Base64 payloads, data URLs, remote URLs, storage URLs,
and SAS URLs are rejected by construction.
"""

from __future__ import annotations

import asyncio
import io
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Settings
from .errors import ErrorCode, VisionError
from .schemas import AssetImage, ImageReference, LocalPathImage

_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


@dataclass(frozen=True)
class LoadedImage:
    """A validated, EXIF-normalized RGB image."""

    image: Image.Image
    content_type: str
    byte_count: int
    source_kind: str

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height


def sniff_content_type(payload: bytes) -> str | None:
    """Identify PNG, JPEG, or WebP purely from magic bytes."""
    for signature, content_type in _MAGIC_SIGNATURES:
        if payload.startswith(signature):
            return content_type
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def resolve_allowed_path(raw_path: str, settings: Settings) -> Path:
    """Resolve a caller supplied path and confirm containment."""
    roots = settings.allowed_root_paths
    if not roots:
        raise VisionError(
            ErrorCode.INVALID_INPUT,
            "Local paths are disabled because VISION_ALLOWED_ROOTS is not configured",
        )
    if "\x00" in raw_path:
        raise VisionError(ErrorCode.INVALID_INPUT, "Path contains invalid characters")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise VisionError(ErrorCode.INVALID_INPUT, "Path must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise VisionError(ErrorCode.NOT_FOUND, "Image was not found") from exc
    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise VisionError(ErrorCode.FORBIDDEN, "Path is outside the allowed roots")


def read_allowed_file(path: Path, settings: Settings) -> bytes:
    """Read a regular file with the size limit enforced on the open handle."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise VisionError(ErrorCode.NOT_FOUND, "Image was not found") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise VisionError(ErrorCode.INVALID_INPUT, "Only regular files are supported")
        if info.st_size > settings.max_image_bytes:
            raise VisionError(
                ErrorCode.PAYLOAD_TOO_LARGE,
                "Image exceeds the configured byte limit",
                details={"maxBytes": settings.max_image_bytes},
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(settings.max_image_bytes + 1)
    finally:
        os.close(descriptor)
    if len(payload) > settings.max_image_bytes:
        raise VisionError(
            ErrorCode.PAYLOAD_TOO_LARGE,
            "Image exceeds the configured byte limit",
            details={"maxBytes": settings.max_image_bytes},
        )
    return payload


def decode_image(payload: bytes, settings: Settings, source_kind: str) -> LoadedImage:
    """Validate magic bytes and decoded pixel count before decoding fully."""
    if not payload:
        raise VisionError(ErrorCode.INVALID_INPUT, "Image payload is empty")
    if len(payload) > settings.max_image_bytes:
        raise VisionError(
            ErrorCode.PAYLOAD_TOO_LARGE,
            "Image exceeds the configured byte limit",
            details={"maxBytes": settings.max_image_bytes},
        )
    content_type = sniff_content_type(payload)
    if content_type is None:
        raise VisionError(
            ErrorCode.UNSUPPORTED_MEDIA,
            "Only PNG, JPEG, and WebP images are supported",
        )
    try:
        with Image.open(io.BytesIO(payload)) as probe:
            if probe.width * probe.height > settings.max_image_pixels:
                raise VisionError(
                    ErrorCode.PAYLOAD_TOO_LARGE,
                    "Image exceeds the configured pixel limit",
                    details={"maxPixels": settings.max_image_pixels},
                )
            probe.load()
            normalized = ImageOps.exif_transpose(probe) or probe
            rgb = normalized.convert("RGB")
    except VisionError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise VisionError(ErrorCode.INVALID_INPUT, "Image could not be decoded") from exc
    return LoadedImage(
        image=rgb,
        content_type=content_type,
        byte_count=len(payload),
        source_kind=source_kind,
    )


async def load_image_bytes(
    reference: ImageReference,
    settings: Settings,
    store: object,
    principal: str,
) -> tuple[bytes, str]:
    """Return the raw bytes for a reference along with its source kind."""
    if isinstance(reference, LocalPathImage):
        path = await asyncio.to_thread(resolve_allowed_path, reference.path, settings)
        payload = await asyncio.to_thread(read_allowed_file, path, settings)
        return payload, "local_path"
    if isinstance(reference, AssetImage):
        getter = getattr(store, "get", None)
        if getter is None:  # pragma: no cover - defensive
            raise VisionError(ErrorCode.INTERNAL, "Asset store is not configured")
        _record, payload = await getter(principal, reference.asset_id)
        return payload, "asset"
    raise VisionError(ErrorCode.INVALID_INPUT, "Unsupported image reference")  # pragma: no cover


async def load_image(
    reference: ImageReference,
    settings: Settings,
    store: object,
    principal: str,
) -> LoadedImage:
    payload, kind = await load_image_bytes(reference, settings, store, principal)
    return await asyncio.to_thread(decode_image, payload, settings, kind)


def encode_image(image: Image.Image, output_format: str, quality: int) -> bytes:
    """Encode deterministically without embedding metadata."""
    buffer = io.BytesIO()
    pillow_format = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}[output_format]
    if pillow_format == "PNG":
        image.save(buffer, format="PNG", optimize=True)
    elif pillow_format == "JPEG":
        image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=False)
    else:
        image.save(buffer, format="WEBP", quality=quality, method=4)
    return buffer.getvalue()
