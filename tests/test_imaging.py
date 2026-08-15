"""Path, content, and size attacks against image loading."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image

from vision_server.config import Settings
from vision_server.errors import ErrorCode, VisionError
from vision_server.imaging import (
    decode_image,
    load_image,
    read_allowed_file,
    resolve_allowed_path,
    sniff_content_type,
)
from vision_server.schemas import AssetImage, LocalPathImage
from vision_server.security import ANONYMOUS_PRINCIPAL

from .conftest import png_bytes, single_chunk, write_png


def test_rejects_relative_and_traversal_paths(settings: Settings, allowed_root: Path) -> None:
    write_png(allowed_root / "ok.png")
    with pytest.raises(VisionError) as relative:
        resolve_allowed_path("images/ok.png", settings)
    assert relative.value.code is ErrorCode.INVALID_INPUT

    outside = allowed_root.parent / "secret.png"
    write_png(outside)
    with pytest.raises(VisionError) as traversal:
        resolve_allowed_path(str(allowed_root / ".." / "secret.png"), settings)
    assert traversal.value.code is ErrorCode.FORBIDDEN


def test_rejects_symlink_escaping_the_root(settings: Settings, allowed_root: Path) -> None:
    target = allowed_root.parent / "outside.png"
    write_png(target)
    link = allowed_root / "link.png"
    link.symlink_to(target)
    with pytest.raises(VisionError) as error:
        resolve_allowed_path(str(link), settings)
    assert error.value.code is ErrorCode.FORBIDDEN


def test_rejects_non_regular_files(settings: Settings, allowed_root: Path) -> None:
    fifo = allowed_root / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(VisionError) as pipe_error:
        read_allowed_file(fifo, settings)
    assert pipe_error.value.code is ErrorCode.INVALID_INPUT

    with pytest.raises(VisionError) as directory_error:
        read_allowed_file(allowed_root, settings)
    assert directory_error.value.code is ErrorCode.INVALID_INPUT


def test_file_size_limit_is_enforced_on_the_handle(settings: Settings, allowed_root: Path) -> None:
    path = write_png(allowed_root / "big.png", 300, 300, "red")
    oversized = allowed_root / "oversized.bin"
    oversized.write_bytes(b"\x89PNG" + b"\x00" * 4096)
    small = settings.model_copy(update={"max_image_bytes": 1024})
    with pytest.raises(VisionError) as error:
        read_allowed_file(oversized, small)
    assert error.value.code is ErrorCode.PAYLOAD_TOO_LARGE
    assert read_allowed_file(path, settings)[:4] == b"\x89PNG"


def test_local_paths_disabled_without_allowed_roots() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(VisionError) as error:
        resolve_allowed_path("/etc/hostname", settings)
    assert error.value.code is ErrorCode.INVALID_INPUT


def test_null_byte_and_missing_file(settings: Settings, allowed_root: Path) -> None:
    with pytest.raises(VisionError) as null_byte:
        resolve_allowed_path(str(allowed_root / "a\x00.png"), settings)
    assert null_byte.value.code is ErrorCode.INVALID_INPUT
    with pytest.raises(VisionError) as missing:
        resolve_allowed_path(str(allowed_root / "missing.png"), settings)
    assert missing.value.code is ErrorCode.NOT_FOUND


def test_magic_detection_and_unsupported_media(settings: Settings) -> None:
    assert sniff_content_type(png_bytes()) == "image/png"
    assert sniff_content_type(b"GIF89a" + b"\x00" * 32) is None
    with pytest.raises(VisionError) as error:
        decode_image(b"GIF89a" + b"\x00" * 32, settings, "local_path")
    assert error.value.code is ErrorCode.UNSUPPORTED_MEDIA


def test_byte_and_pixel_limits(settings: Settings) -> None:
    tiny = settings.model_copy(update={"max_image_bytes": 1024})
    with pytest.raises(VisionError) as too_many_bytes:
        decode_image(png_bytes(400, 400, "red"), tiny, "local_path")
    assert too_many_bytes.value.code is ErrorCode.PAYLOAD_TOO_LARGE

    few_pixels = settings.model_copy(update={"max_image_pixels": 1024})
    with pytest.raises(VisionError) as too_many_pixels:
        decode_image(png_bytes(200, 200), few_pixels, "local_path")
    assert too_many_pixels.value.code is ErrorCode.PAYLOAD_TOO_LARGE


def test_truncated_payload_is_rejected(settings: Settings) -> None:
    payload = bytearray(png_bytes(32, 32, "red"))
    with pytest.raises(VisionError) as error:
        decode_image(bytes(payload[: len(payload) // 2]), settings, "local_path")
    assert error.value.code is ErrorCode.INVALID_INPUT


def test_empty_payload_is_rejected(settings: Settings) -> None:
    with pytest.raises(VisionError) as error:
        decode_image(b"", settings, "local_path")
    assert error.value.code is ErrorCode.INVALID_INPUT


def test_exif_orientation_is_normalized(settings: Settings, allowed_root: Path) -> None:
    buffer = io.BytesIO()
    image = Image.new("RGB", (40, 20), "white")
    exif = image.getexif()
    exif[274] = 6  # rotate 90 degrees
    image.save(buffer, format="JPEG", exif=exif)
    loaded = decode_image(buffer.getvalue(), settings, "local_path")
    assert (loaded.width, loaded.height) == (20, 40)
    assert loaded.image.mode == "RGB"


async def test_load_image_from_asset(settings: Settings, asset_store: object) -> None:
    record = await asset_store.put(  # type: ignore[attr-defined]
        ANONYMOUS_PRINCIPAL, single_chunk(png_bytes(16, 8)), "image/png"
    )
    loaded = await load_image(
        AssetImage(kind="asset", asset_id=record.asset_id),
        settings,
        asset_store,
        ANONYMOUS_PRINCIPAL,
    )
    assert (loaded.width, loaded.height) == (16, 8)
    assert loaded.source_kind == "asset"


async def test_load_image_from_local_path(
    settings: Settings, allowed_root: Path, asset_store: object
) -> None:
    path = write_png(allowed_root / "input.png", 12, 6)
    loaded = await load_image(
        LocalPathImage(kind="local_path", path=str(path)),
        settings,
        asset_store,
        ANONYMOUS_PRINCIPAL,
    )
    assert (loaded.width, loaded.height) == (12, 6)
