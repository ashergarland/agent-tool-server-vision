"""``compare_images`` handler.

Determinism and documented semantics
------------------------------------

* Both images are decoded, EXIF-normalized, and converted to 8-bit RGB before
  comparison, so orientation and color mode never affect the result.
* When the two images differ in size, the overlapping top-left region is
  compared pixel by pixel. Every pixel that exists in only one image is counted
  as changed and ``dimensionsChanged`` is ``true``.
* ``threshold`` is the fraction of the full 0-255 channel range that a pixel's
  largest channel delta must exceed to count as changed.
* ``similarityScore`` is ``1 - mean(per-pixel mean absolute channel delta)/255``
  over the union area, where non-overlapping pixels contribute a full delta.
* Changed pixels are grouped into regions by 16x16 tiles joined with
  4-connectivity; regions are returned ordered by changed pixel count and then
  by position, so results are stable across runs.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from ..assets.base import CONTENT_TYPE_BY_FORMAT, AssetKind, single_chunk
from ..imaging import LoadedImage, encode_image, load_image
from ..runtime import ToolContext
from ..schemas import (
    BoundingBox,
    ChangedRegion,
    CompareImagesInput,
    CompareImagesOutput,
    Dimensions,
    ResultMeta,
)

BoolArray = np.ndarray[Any, np.dtype[np.bool_]]

TILE_SIZE = 16
MAX_COMPONENTS = 1000


async def compare_images(payload: CompareImagesInput, context: ToolContext) -> CompareImagesOutput:
    before = await load_image(payload.before, context.settings, context.assets, context.principal)
    after = await load_image(payload.after, context.settings, context.assets, context.principal)
    comparison = await asyncio.to_thread(
        _compare,
        before,
        after,
        payload.threshold,
        payload.max_regions,
        payload.include_diff,
        context.settings.max_image_pixels,
    )

    warnings = list(comparison.warnings)
    diff_artifact_id: str | None = None
    if payload.include_diff and comparison.diff_png is not None:
        record = await context.assets.put(
            context.principal,
            single_chunk(comparison.diff_png),
            CONTENT_TYPE_BY_FORMAT["png"],
            AssetKind.ARTIFACT,
        )
        diff_artifact_id = record.asset_id
    elif payload.include_diff and comparison.changed_pixels == 0:
        warnings.append("diff image was not produced because no pixels changed")

    return CompareImagesOutput(
        similarity_score=comparison.similarity,
        before_dimensions=Dimensions(width=before.width, height=before.height),
        after_dimensions=Dimensions(width=after.width, height=after.height),
        dimensions_changed=(before.width, before.height) != (after.width, after.height),
        comparison_dimensions=Dimensions(
            width=min(before.width, after.width), height=min(before.height, after.height)
        ),
        changed_pixels=comparison.changed_pixels,
        changed_ratio=comparison.changed_ratio,
        regions=comparison.regions,
        diff_artifact_id=diff_artifact_id,
        meta=ResultMeta(warnings=warnings[:20], truncated=comparison.truncated),
    )


class _Comparison:
    def __init__(self) -> None:
        self.similarity: float = 1.0
        self.changed_pixels: int = 0
        self.changed_ratio: float = 0.0
        self.regions: list[ChangedRegion] = []
        self.truncated: bool = False
        self.warnings: list[str] = []
        self.diff_png: bytes | None = None


@dataclass(frozen=True)
class _RegionComponent:
    region: ChangedRegion
    touches_right_edge: bool
    touches_bottom_edge: bool
    outside: bool = False


def _compare(
    before: LoadedImage,
    after: LoadedImage,
    threshold: float,
    max_regions: int,
    include_diff: bool,
    max_diff_pixels: int,
) -> _Comparison:
    outcome = _Comparison()
    overlap_width = min(before.width, after.width)
    overlap_height = min(before.height, after.height)
    union_width = max(before.width, after.width)
    union_height = max(before.height, after.height)
    overlap_area = overlap_width * overlap_height
    union_area = before.width * before.height + after.width * after.height - overlap_area
    outside = union_area - overlap_area
    if outside:
        outcome.warnings.append("images differ in size; non-overlapping pixels count as changed")

    left = np.asarray(before.image.crop((0, 0, overlap_width, overlap_height)), dtype=np.int16)
    right = np.asarray(after.image.crop((0, 0, overlap_width, overlap_height)), dtype=np.int16)
    delta = np.abs(left - right)
    max_delta = delta.max(axis=2)
    mean_delta = delta.mean(axis=2)

    cutoff = threshold * 255.0
    overlap_mask = max_delta > cutoff

    changed_in_overlap = int(overlap_mask.sum())
    outcome.changed_pixels = changed_in_overlap + outside
    outcome.changed_ratio = round(outcome.changed_pixels / union_area, 6) if union_area else 0.0
    difference_sum = float(mean_delta.sum()) + float(outside) * 255.0
    outcome.similarity = round(max(0.0, 1.0 - difference_sum / (union_area * 255.0)), 6)

    overlap_regions, overlap_truncated = _regions(overlap_mask, MAX_COMPONENTS)
    merged_regions = _merge_regions(
        overlap_regions,
        _outside_regions(before, after, overlap_width, overlap_height),
        overlap_width,
        overlap_height,
    )
    merged_regions.sort(key=lambda region: (-region.changed_pixels, region.box.y, region.box.x))
    outcome.regions = merged_regions[:max_regions]
    outcome.truncated = overlap_truncated or len(merged_regions) > max_regions
    if outcome.truncated:
        outcome.warnings.append(f"changed region list truncated to {max_regions} entries")
    if include_diff and outcome.changed_pixels:
        if union_width * union_height > max_diff_pixels:
            outcome.warnings.append(
                "diff image omitted because its bounding canvas exceeds the configured pixel limit"
            )
        else:
            mask = _union_mask(before, after, overlap_mask, union_width, union_height)
            outcome.diff_png = _diff_png(after, mask, union_width, union_height)
    return outcome


def _outside_regions(
    before: LoadedImage,
    after: LoadedImage,
    overlap_width: int,
    overlap_height: int,
) -> list[_RegionComponent]:
    regions: list[_RegionComponent] = []
    for image in (before, after):
        touches_right_edge = image.width > overlap_width
        touches_bottom_edge = image.height > overlap_height
        changed_pixels = image.width * image.height - overlap_width * overlap_height
        if changed_pixels == 0:
            continue
        if touches_right_edge and touches_bottom_edge:
            box = BoundingBox(x=0, y=0, width=image.width, height=image.height)
        elif touches_right_edge:
            box = BoundingBox(
                x=overlap_width,
                y=0,
                width=image.width - overlap_width,
                height=image.height,
            )
        else:
            box = BoundingBox(
                x=0,
                y=overlap_height,
                width=image.width,
                height=image.height - overlap_height,
            )
        regions.append(
            _RegionComponent(
                region=ChangedRegion(box=box, changed_pixels=changed_pixels),
                touches_right_edge=touches_right_edge,
                touches_bottom_edge=touches_bottom_edge,
                outside=True,
            )
        )
    return regions


def _merge_regions(
    overlap_regions: list[ChangedRegion],
    outside_regions: list[_RegionComponent],
    overlap_width: int,
    overlap_height: int,
) -> list[ChangedRegion]:
    components = [
        *[
            _RegionComponent(
                region=region,
                touches_right_edge=region.box.x + region.box.width == overlap_width,
                touches_bottom_edge=region.box.y + region.box.height == overlap_height,
            )
            for region in overlap_regions
        ],
        *outside_regions,
    ]
    parents = list(range(len(components)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for outside_index, outside in enumerate(components):
        if not outside.outside:
            continue
        for overlap_index, overlap in enumerate(components[: len(overlap_regions)]):
            if (
                outside.touches_right_edge
                and overlap.touches_right_edge
                or outside.touches_bottom_edge
                and overlap.touches_bottom_edge
            ):
                union(outside_index, overlap_index)

    groups: dict[int, list[ChangedRegion]] = {}
    for index, component in enumerate(components):
        groups.setdefault(find(index), []).append(component.region)

    merged: list[ChangedRegion] = []
    for regions in groups.values():
        left = min(region.box.x for region in regions)
        top = min(region.box.y for region in regions)
        right = max(region.box.x + region.box.width for region in regions)
        bottom = max(region.box.y + region.box.height for region in regions)
        merged.append(
            ChangedRegion(
                box=BoundingBox(x=left, y=top, width=right - left, height=bottom - top),
                changed_pixels=sum(region.changed_pixels for region in regions),
            )
        )
    return merged


def _union_mask(
    before: LoadedImage,
    after: LoadedImage,
    overlap_mask: BoolArray,
    union_width: int,
    union_height: int,
) -> BoolArray:
    mask: BoolArray = np.zeros((union_height, union_width), dtype=bool)
    before_presence = np.zeros_like(mask)
    after_presence = np.zeros_like(mask)
    before_presence[: before.height, : before.width] = True
    after_presence[: after.height, : after.width] = True
    mask[before_presence ^ after_presence] = True
    mask[: overlap_mask.shape[0], : overlap_mask.shape[1]] = overlap_mask
    return mask


def _regions(mask: BoolArray, max_regions: int) -> tuple[list[ChangedRegion], bool]:
    height, width = mask.shape
    if not mask.any():
        return [], False
    padded_h = -(-height // TILE_SIZE) * TILE_SIZE
    padded_w = -(-width // TILE_SIZE) * TILE_SIZE
    padded = np.zeros((padded_h, padded_w), dtype=bool)
    padded[:height, :width] = mask
    tiles = np.asarray(
        padded.reshape(padded_h // TILE_SIZE, TILE_SIZE, padded_w // TILE_SIZE, TILE_SIZE).any(
            axis=(1, 3)
        )
    )

    visited = np.zeros_like(tiles, dtype=bool)
    components: list[list[tuple[int, int]]] = []
    component_limit_truncated = False
    for ty, tx in zip(*np.nonzero(tiles), strict=True):
        if visited[ty, tx]:
            continue
        component: list[tuple[int, int]] = []
        queue: deque[tuple[int, int]] = deque([(int(ty), int(tx))])
        visited[ty, tx] = True
        while queue:
            cy, cx = queue.popleft()
            component.append((cy, cx))
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < tiles.shape[0] and 0 <= nx < tiles.shape[1]:
                    if tiles[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((ny, nx))
        components.append(component)
        if len(components) >= MAX_COMPONENTS:
            component_limit_truncated = bool(np.any(tiles & ~visited))
            break

    regions: list[tuple[int, BoundingBox]] = []
    for component in components:
        y0 = min(ty for ty, _ in component) * TILE_SIZE
        y1 = min(height, (max(ty for ty, _ in component) + 1) * TILE_SIZE)
        x0 = min(tx for _, tx in component) * TILE_SIZE
        x1 = min(width, (max(tx for _, tx in component) + 1) * TILE_SIZE)
        window = np.zeros((y1 - y0, x1 - x0), dtype=bool)
        for ty, tx in component:
            sy0 = ty * TILE_SIZE - y0
            sx0 = tx * TILE_SIZE - x0
            window[sy0 : sy0 + TILE_SIZE, sx0 : sx0 + TILE_SIZE] = mask[
                y0 + sy0 : y0 + sy0 + TILE_SIZE, x0 + sx0 : x0 + sx0 + TILE_SIZE
            ]
        ys, xs = np.nonzero(window)
        if ys.size == 0:  # pragma: no cover - defensive
            continue
        box = BoundingBox(
            x=int(x0 + xs.min()),
            y=int(y0 + ys.min()),
            width=int(xs.max() - xs.min() + 1),
            height=int(ys.max() - ys.min() + 1),
        )
        regions.append((int(ys.size), box))

    regions.sort(key=lambda item: (-item[0], item[1].y, item[1].x))
    truncated = component_limit_truncated or len(regions) > max_regions
    selected = regions[:max_regions]
    return [ChangedRegion(box=box, changed_pixels=count) for count, box in selected], truncated


def _diff_png(after: LoadedImage, mask: BoolArray, width: int, height: int) -> bytes:
    padded = Image.new("L", (width, height))
    padded.paste(after.image.convert("L"), (0, 0))
    base = np.asarray(padded, dtype=np.uint8)
    canvas = np.stack([base, base, base], axis=2)
    canvas[mask] = np.array([255, 0, 0], dtype=np.uint8)
    return encode_image(Image.fromarray(canvas, mode="RGB"), "png", 100)
