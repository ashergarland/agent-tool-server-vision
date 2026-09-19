"""Safe, dependency-free SVG text analysis used by the worker boundary.

The npm package smoke path may run before optional Python image dependencies are
installed. SVG text extraction therefore uses only the standard library while
retaining the same allowed-root, byte, structure, and output bounds as raster
analysis.
"""

from __future__ import annotations

import math
import os
import re
import stat
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .text_limits import truncate_utf16, utf16_length

SUPPORTED_LANGUAGES = frozenset({"en", "ch", "fr", "german", "japan", "korean"})
MAX_SVG_ELEMENTS = 5_000
MAX_TEXT_BLOCKS = 500
MAX_TEXT_CHARS = 200_000
MAX_LINE_CHARS = 4_000
PARTIAL_RENDERING_CONFIDENCE = 0.7
AVERAGE_GLYPH_WIDTH_EM = 0.55
NON_RENDERED_CONTAINERS = frozenset(
    {
        "clippath",
        "desc",
        "defs",
        "marker",
        "mask",
        "metadata",
        "pattern",
        "script",
        "style",
        "switch",
        "symbol",
        "title",
    }
)
SUPPORTED_CONTAINERS = frozenset({"a", "g"})
NAMED_COLORS = {
    "black": (0, 0, 0),
    "blue": (0, 0, 255),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "green": (0, 128, 0),
    "navy": (0, 0, 128),
    "red": (255, 0, 0),
    "white": (255, 255, 255),
    "yellow": (255, 255, 0),
}
NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
NUMBER_RE = re.compile(NUMBER_PATTERN)
TRANSFORM_RE = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")

Matrix = tuple[float, float, float, float, float, float]
Color = tuple[int, int, int]
IDENTITY_MATRIX: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


class SvgAnalysisError(Exception):
    """Protocol-safe SVG validation or analysis error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {str(key)[:200]: str(value)[:200] for key, value in (details or {}).items()}


@dataclass(frozen=True)
class EvidenceLine:
    block_id: str
    text: str
    confidence: float
    box: dict[str, float] | None


@dataclass(frozen=True)
class _PixelBox:
    left: float
    top: float
    right: float
    bottom: float

    def intersects(self, other: _PixelBox) -> bool:
        return (
            self.left < other.right
            and other.left < self.right
            and self.top < other.bottom
            and other.top < self.bottom
        )

    def contains(self, other: _PixelBox) -> bool:
        return (
            self.left <= other.left
            and self.top <= other.top
            and self.right >= other.right
            and self.bottom >= other.bottom
        )


@dataclass(frozen=True)
class _SolidRect:
    box: _PixelBox
    color: Color
    radius_x: float
    radius_y: float

    def covers(self, other: _PixelBox) -> bool:
        if not self.box.contains(other):
            return False
        if self.radius_x <= 0 or self.radius_y <= 0:
            return True
        return (
            self.box.left + self.radius_x <= other.left
            and other.right <= self.box.right - self.radius_x
        ) or (
            self.box.top + self.radius_y <= other.top
            and other.bottom <= self.box.bottom - self.radius_y
        )


@dataclass(frozen=True)
class _TextRun:
    text: str
    colors: frozenset[Color]


@dataclass(frozen=True)
class _TextCandidate:
    text: str
    box: dict[str, float]
    pixel_box: _PixelBox


@dataclass(frozen=True)
class _RenderState:
    display_hidden: bool = False
    non_rendered: bool = False
    uncertain_visibility: bool = False
    visibility: str = "visible"
    opacity: float = 1.0
    fill: str = "black"
    stroke: str = "none"
    fill_opacity: float = 1.0
    stroke_opacity: float = 1.0
    font_size: float = 16.0
    matrix: Matrix | None = IDENTITY_MATRIX


def can_analyze_svg(input_value: object) -> bool:
    if not isinstance(input_value, dict):
        return False
    image = input_value.get("image")
    return (
        isinstance(image, dict)
        and image.get("kind") == "local_path"
        and isinstance(image.get("path"), str)
        and image["path"].lower().endswith(".svg")
    )


def analyze_svg(input_value: object) -> dict[str, object]:
    parsed = _parse_input(input_value)
    payload = _read_allowed_file(parsed["path"])
    root = _parse_svg(payload)
    width, height = _dimensions(root)
    lines, text_truncated, rendering_warnings = _extract_lines(root, width, height)
    facts, facts_truncated = extract_facts(lines, parsed["max_facts"], "embedded_svg_text")
    extracted_text = "\n".join(line.text for line in lines)
    fallback_used = len(facts) == 0 or bool(rendering_warnings)
    warnings = list(rendering_warnings)
    if text_truncated:
        warnings.append("SVG text was truncated to the configured output bounds")
    if facts_truncated:
        warnings.append(f"structured facts were truncated to {parsed['max_facts']} entries")
    fallback_reason: str | None = None
    if rendering_warnings:
        fallback_reason = (
            "SVG rendering coverage was partial; only evidence with supported visibility semantics "
            "was promoted. Use native vision for omitted or ambiguous regions."
        )
        warnings.append(fallback_reason)
    elif fallback_used:
        fallback_reason = (
            "No supported structured fact pattern was recognized; use extractedText or native "
            "vision for unsupported visual semantics."
        )
        warnings.append(fallback_reason)
    return {
        "summary": summarize_facts(facts),
        "extractedText": truncate_utf16(extracted_text, MAX_TEXT_CHARS),
        "facts": facts,
        "dimensions": {"width": width, "height": height},
        "provider": {
            "name": "embedded_svg_text",
            "mode": "local",
            "model": "svg-text-elements-v1",
            "apiVersion": None,
        },
        "analysisMode": "embedded_svg_text",
        "fallbackUsed": fallback_used,
        "fallbackReason": fallback_reason,
        "meta": {
            "warnings": warnings[:20],
            "truncated": text_truncated or facts_truncated,
        },
    }


def extract_facts(
    lines: list[EvidenceLine],
    max_facts: int,
    source: str,
) -> tuple[list[dict[str, object]], bool]:
    facts: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    context_kind: str | None = None
    context_label: str | None = None

    def add(kind: str, label: str, value: str, line: EvidenceLine, confidence: float) -> None:
        cleaned_value = _clean(value)
        key = (kind, label.lower(), cleaned_value.lower())
        if not cleaned_value or key in seen:
            return
        seen.add(key)
        facts.append(
            {
                "kind": kind,
                "label": truncate_utf16(label, 120),
                "value": truncate_utf16(cleaned_value, 1000),
                "evidence": truncate_utf16(line.text, MAX_LINE_CHARS),
                "confidence": round(min(1.0, max(0.0, line.confidence * confidence)), 3),
                "box": line.box,
                "provenance": {"source": source, "blockIds": [line.block_id]},
            }
        )

    for line in lines:
        text = _clean(line.text)
        lowered = text.lower()
        heading = _heading(lowered)
        if heading is not None:
            context_kind, context_label = heading
            continue

        identity = re.fullmatch(r"([^/\n]{1,120})\s*/\s*([^/\n]{1,240})", text)
        if identity and (
            "--" in identity.group(2)
            or re.search(r"\b(?:pr|rev|revision)[-_]?\d", identity.group(2), re.IGNORECASE)
        ):
            service, revision = (_clean(value) for value in identity.groups())
            add("identity", "service", service, line, 0.99)
            add("revision", "revision", revision, line, 0.99)

        listener_line = bool(re.search(r"\b(server_started|listen(?:er|ing|s)?)\b", lowered))
        revision_line = "revision" in lowered
        for match in re.finditer(
            r"\b([A-Za-z][A-Za-z0-9_.-]{0,39})\s*=\s*([^\s,;]{1,240})",
            text,
        ):
            key, value = match.groups()
            key_lower = key.lower()
            if listener_line and key_lower in {"address", "host", "port", "healthpath"}:
                add("listener", f"listener {key}", value, line, 0.98)
            elif "revision" in key_lower:
                add("revision", key, value, line, 0.98)
            elif key_lower == "status" and revision_line:
                add("revision", "revision status", value, line, 0.96)
            elif key.isupper() or "_" in key:
                add("environment", key, value, line, 0.98)

        target_port = re.search(r"\btarget[\s_-]*port\s*[:=]?\s*(\d{1,5})\b", lowered)
        if target_port:
            kind = "ingress" if context_kind == "ingress" or "ingress" in lowered else "readiness"
            add(kind, f"{kind} target port", target_port.group(1), line, 0.98)

        tcp_port = re.search(r"\btcp(?:\s+port|:)\s*(\d{1,5})\b", lowered)
        if tcp_port:
            add("readiness", "TCP readiness port", tcp_port.group(1), line, 0.98)

        replicas = re.search(r"\bready\s+replicas?\s*:\s*(\d+)\s*/\s*(\d+)\b", lowered)
        if replicas:
            add(
                "readiness",
                "ready replicas",
                f"{replicas.group(1)} / {replicas.group(2)}",
                line,
                0.99,
            )

        if re.search(r"\b(connection[_ ]refused|probe|readiness)\b", lowered):
            add("readiness", "readiness evidence", text, line, 0.96)

        status_match = re.fullmatch(
            r"(degraded|failed|failure|unavailable|unhealthy|not[_ ]ready|ready|healthy|success)",
            lowered,
        )
        if status_match:
            kind = context_kind or ("readiness" if "ready" in lowered else "status")
            label = context_label or f"{kind} state"
            add(kind, label, text, line, 0.96)

    truncated = len(facts) > max_facts
    return facts[:max_facts], truncated


def summarize_facts(facts: list[dict[str, object]]) -> str:
    if not facts:
        return "No supported structured facts were recognized from the visual text."
    parts = [f"{fact['label']}: {fact['value']}" for fact in facts[:20]]
    summary = "; ".join(parts)
    return truncate_utf16(summary, 20_000)


def _parse_input(input_value: object) -> dict[str, Any]:
    if not isinstance(input_value, dict):
        raise SvgAnalysisError("invalid_input", "Tool input must be an object")
    allowed = {"image", "language", "processingMode", "maxFacts"}
    if set(input_value) - allowed:
        raise SvgAnalysisError("invalid_input", "Tool input contains unknown fields")
    image = input_value.get("image")
    if not isinstance(image, dict) or set(image) != {"kind", "path"}:
        raise SvgAnalysisError(
            "invalid_input",
            "SVG analysis requires a local_path image reference",
        )
    if image.get("kind") != "local_path":
        raise SvgAnalysisError(
            "invalid_input",
            "SVG analysis requires a local_path image reference",
        )
    path = image.get("path")
    if not isinstance(path, str) or not (1 <= utf16_length(path) <= 4096):
        raise SvgAnalysisError("invalid_input", "Image path is invalid")
    language = input_value.get("language")
    if language is not None and language not in SUPPORTED_LANGUAGES:
        raise SvgAnalysisError("invalid_input", "Requested language is not supported")
    processing_mode = input_value.get("processingMode", "auto")
    if processing_mode not in {"auto", "local", "azure"}:
        raise SvgAnalysisError("invalid_input", "processingMode is invalid")
    max_facts = input_value.get("maxFacts", 50)
    if isinstance(max_facts, bool) or not isinstance(max_facts, int) or not 1 <= max_facts <= 100:
        raise SvgAnalysisError("invalid_input", "maxFacts must be an integer from 1 to 100")
    return {"path": path, "max_facts": max_facts}


def _read_allowed_file(raw_path: str) -> bytes:
    raw_roots = os.environ.get("VISION_ALLOWED_ROOTS", "")
    separator = "," if "," in raw_roots else os.pathsep
    roots: list[Path] = []
    for raw_root in (part.strip() for part in raw_roots.split(separator)):
        if not raw_root:
            continue
        try:
            roots.append(Path(raw_root).resolve(strict=True))
        except OSError:
            continue
    if not roots:
        raise SvgAnalysisError(
            "invalid_input",
            "Local paths are disabled because VISION_ALLOWED_ROOTS is not configured",
        )
    if "\x00" in raw_path:
        raise SvgAnalysisError("invalid_input", "Path contains invalid characters")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise SvgAnalysisError("invalid_input", "Path must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SvgAnalysisError("not_found", "Image was not found") from exc
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise SvgAnalysisError("forbidden", "Path is outside the allowed roots")

    max_bytes = _bounded_environment_integer(
        "VISION_MAX_IMAGE_BYTES",
        10 * 1024 * 1024,
        1024,
        64 * 1024 * 1024,
    )
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise SvgAnalysisError("not_found", "Image was not found") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SvgAnalysisError("invalid_input", "Only regular files are supported")
        if info.st_size > max_bytes:
            raise SvgAnalysisError(
                "payload_too_large",
                "Image exceeds the configured byte limit",
                details={"maxBytes": max_bytes},
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(payload) > max_bytes:
        raise SvgAnalysisError(
            "payload_too_large",
            "Image exceeds the configured byte limit",
            details={"maxBytes": max_bytes},
        )
    return payload


def _bounded_environment_integer(name: str, fallback: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except ValueError as exc:
        raise SvgAnalysisError("invalid_input", "Worker configuration is invalid") from exc
    if not minimum <= value <= maximum:
        raise SvgAnalysisError("invalid_input", "Worker configuration is invalid")
    return value


def _parse_svg(payload: bytes) -> ET.Element:
    if not payload:
        raise SvgAnalysisError("invalid_input", "Image payload is empty")

    root: ET.Element | None = None
    stack: list[ET.Element] = []
    element_count = 0
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True

    def reject_declaration(*_args: object) -> None:
        raise SvgAnalysisError(
            "invalid_input",
            "SVG document type and entity declarations are not supported",
        )

    def reject_external_entity(*_args: object) -> int:
        reject_declaration()
        return 0

    def start_element(name: str, attributes: dict[str, str]) -> None:
        nonlocal element_count, root
        element_count += 1
        if element_count > MAX_SVG_ELEMENTS:
            raise SvgAnalysisError(
                "payload_too_large",
                "SVG exceeds the configured structure limit",
                details={"maxElements": MAX_SVG_ELEMENTS},
            )
        element = ET.Element(name, attributes)
        if stack:
            stack[-1].append(element)
        else:
            root = element
        stack.append(element)

    def end_element(_name: str) -> None:
        stack.pop()

    def character_data(value: str) -> None:
        if not stack or not value:
            return
        current = stack[-1]
        if len(current):
            child = current[-1]
            child.tail = (child.tail or "") + value
        else:
            current.text = (current.text or "") + value

    def processing_instruction(target: str, _data: str) -> None:
        if target.lower() == "xml-stylesheet":
            raise SvgAnalysisError(
                "invalid_input",
                "External SVG stylesheets are not supported",
            )

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.CharacterDataHandler = character_data
    parser.ProcessingInstructionHandler = processing_instruction
    parser.StartDoctypeDeclHandler = reject_declaration
    parser.EntityDeclHandler = reject_declaration
    parser.UnparsedEntityDeclHandler = reject_declaration
    parser.ExternalEntityRefHandler = reject_external_entity
    try:
        parser.Parse(payload, True)
    except expat.ExpatError as exc:
        raise SvgAnalysisError("invalid_input", "SVG could not be parsed") from exc
    if root is None:
        raise SvgAnalysisError("invalid_input", "SVG could not be parsed")
    if _local_name(root.tag) != "svg":
        raise SvgAnalysisError("unsupported_media", "Input is not an SVG image")
    return root


def _dimensions(root: ET.Element) -> tuple[int, int]:
    width = _svg_number(root.get("width"))
    height = _svg_number(root.get("height"))
    view_box = _view_box(root)
    if (width is None or height is None) and view_box is not None:
        width = width if width is not None else view_box[2]
        height = height if height is not None else view_box[3]
    if width is None or height is None:
        raise SvgAnalysisError("invalid_input", "SVG width and height are required")
    rounded_width = round(width)
    rounded_height = round(height)
    max_pixels = _bounded_environment_integer(
        "VISION_MAX_IMAGE_PIXELS",
        40_000_000,
        1024,
        200_000_000,
    )
    if (
        rounded_width <= 0
        or rounded_height <= 0
        or rounded_width > 100_000
        or rounded_height > 100_000
        or rounded_width * rounded_height > max_pixels
    ):
        raise SvgAnalysisError(
            "payload_too_large",
            "SVG exceeds the configured dimension limit",
            details={"maxPixels": max_pixels},
        )
    return rounded_width, rounded_height


def _extract_lines(
    root: ET.Element,
    width: int,
    height: int,
) -> tuple[list[EvidenceLine], bool, tuple[str, ...]]:
    if any(_local_name(element.tag) == "style" for element in root.iter()):
        return (
            [],
            False,
            ("SVG stylesheet-driven visibility is unsupported; embedded text was omitted",),
        )

    candidates: list[_TextCandidate] = []
    backgrounds: list[_SolidRect] = []
    rendering_warnings: list[str] = []
    total_chars = 0
    truncated = False
    reduce_confidence = False
    initial_state = _RenderState(matrix=_viewport_matrix(root, width, height))

    def warn(message: str, *, uncertain: bool) -> None:
        nonlocal reduce_confidence
        if message not in rendering_warnings:
            rendering_warnings.append(message)
        reduce_confidence = reduce_confidence or uncertain

    def visit(element: ET.Element, parent_state: _RenderState, *, is_root: bool = False) -> None:
        nonlocal candidates, total_chars, truncated
        if truncated:
            return
        state = _render_state(element, parent_state)
        name = _local_name(element.tag)

        if parent_state.non_rendered or state.display_hidden or state.opacity <= 0:
            return
        if name == "svg" and not is_root:
            warn(
                "Nested SVG viewport rendering is unsupported; affected evidence was omitted",
                uncertain=True,
            )
            return
        if name == "switch":
            warn(
                "SVG conditional rendering is unsupported; affected evidence was omitted",
                uncertain=True,
            )
            return
        if state.non_rendered:
            return
        if state.uncertain_visibility:
            warn(
                "SVG clipping, masking, filtering, or conditional visibility is unsupported; "
                "affected evidence was omitted",
                uncertain=True,
            )
            return
        if state.matrix is None:
            warn(
                "Unsupported SVG transforms affected rendering; affected evidence was omitted",
                uncertain=True,
            )
            return

        if is_root or name in SUPPORTED_CONTAINERS:
            for child in element:
                visit(child, state)
            return

        if name == "rect":
            solid_rect, ambiguous = _solid_rect(element, state, width, height)
            if ambiguous:
                warn(
                    "Unsupported painted SVG geometry reduced confidence in retained evidence",
                    uncertain=True,
                )
                return
            if solid_rect is None:
                return
            retained: list[_TextCandidate] = []
            for candidate in candidates:
                if solid_rect.box.intersects(candidate.pixel_box):
                    warn(
                        "SVG text overlapped by later opaque geometry was omitted",
                        uncertain=False,
                    )
                else:
                    retained.append(candidate)
            candidates = retained
            backgrounds.append(solid_rect)
            return

        if name != "text":
            if _element_may_paint(state) or any(
                _local_name(descendant.tag) == "text" for descendant in element.iter()
            ):
                warn(
                    "Unsupported painted SVG geometry reduced confidence in retained evidence",
                    uncertain=True,
                )
            return

        if _has_independent_text_geometry(element):
            warn(
                "Unsupported positioned or transformed SVG text was omitted",
                uncertain=True,
            )
            return
        runs, run_warning = _visible_text_runs(element, state)
        if run_warning is not None:
            warn(run_warning, uncertain=True)
            return
        text = _clean("".join(run.text for run in runs))
        if not text:
            return
        if len(candidates) >= MAX_TEXT_BLOCKS or total_chars >= MAX_TEXT_CHARS:
            truncated = True
            return
        available = min(MAX_LINE_CHARS, MAX_TEXT_CHARS - total_chars)
        if utf16_length(text) > available:
            text = truncate_utf16(text, available)
            truncated = True
        box, pixel_box, outside_viewport = _text_box(
            element,
            text,
            state.font_size,
            state.matrix,
            width,
            height,
        )
        if outside_viewport:
            return
        if box is None or pixel_box is None:
            warn(
                "SVG text with unsupported geometry was omitted",
                uncertain=True,
            )
            return
        background = _background_color(backgrounds, pixel_box)
        if background is None:
            warn(
                "SVG text without a supported solid background was omitted",
                uncertain=True,
            )
            return
        if any(
            all(_contrast_ratio(color, background) < 1.25 for color in run.colors) for run in runs
        ):
            warn(
                "SVG text indistinguishable from its solid background was omitted",
                uncertain=False,
            )
            return
        overlapping = [
            candidate for candidate in candidates if candidate.pixel_box.intersects(pixel_box)
        ]
        if overlapping:
            candidates[:] = [candidate for candidate in candidates if candidate not in overlapping]
            warn(
                "Overlapping SVG text with ambiguous paint order was omitted",
                uncertain=True,
            )
            return
        candidates.append(_TextCandidate(text=text, box=box, pixel_box=pixel_box))
        total_chars += utf16_length(text) + 1

    visit(root, initial_state, is_root=True)
    confidence = PARTIAL_RENDERING_CONFIDENCE if reduce_confidence else 0.99
    lines = [
        EvidenceLine(
            block_id=f"b{index:04d}",
            text=candidate.text,
            confidence=confidence,
            box=candidate.box,
        )
        for index, candidate in enumerate(candidates)
    ]
    return lines, truncated, tuple(rendering_warnings[:10])


def _text_box(
    element: ET.Element,
    text: str,
    font_size: float,
    matrix: Matrix | None,
    width: int,
    height: int,
) -> tuple[dict[str, float] | None, _PixelBox | None, bool]:
    x = _svg_number(element.get("x"))
    baseline = _svg_number(element.get("y"))
    if x is None or baseline is None or matrix is None:
        return None, None, False
    local_left = x
    local_top = baseline - font_size
    local_right = x + len(text) * font_size * AVERAGE_GLYPH_WIDTH_EM
    local_bottom = local_top + font_size * 1.2
    points = [
        _apply_matrix(matrix, local_left, local_top),
        _apply_matrix(matrix, local_right, local_top),
        _apply_matrix(matrix, local_left, local_bottom),
        _apply_matrix(matrix, local_right, local_bottom),
    ]
    if not all(math.isfinite(coordinate) for point in points for coordinate in point):
        return None, None, False
    left = min(point[0] for point in points)
    right = max(point[0] for point in points)
    top = min(point[1] for point in points)
    bottom = max(point[1] for point in points)
    if right <= 0 or bottom <= 0 or left >= width or top >= height:
        return None, None, True
    clipped_left = max(0.0, left)
    clipped_top = max(0.0, top)
    clipped_right = min(float(width), right)
    clipped_bottom = min(float(height), bottom)
    pixel_box = _PixelBox(clipped_left, clipped_top, clipped_right, clipped_bottom)
    return (
        {
            "x": round(clipped_left / width, 6),
            "y": round(clipped_top / height, 6),
            "width": round(max(0.0, clipped_right - clipped_left) / width, 6),
            "height": round(max(0.0, clipped_bottom - clipped_top) / height, 6),
        },
        pixel_box,
        False,
    )


def _solid_rect(
    element: ET.Element,
    state: _RenderState,
    width: int,
    height: int,
) -> tuple[_SolidRect | None, bool]:
    if state.visibility in {"hidden", "collapse"}:
        return None, False
    if state.visibility not in {"visible", "inherit"} or state.matrix is None:
        return None, True
    color, ambiguous_paint = _opaque_color(
        state.fill,
        state.opacity * state.fill_opacity,
    )
    if ambiguous_paint:
        return None, True
    if color is None:
        return None, _paint_active(
            state.stroke,
            state.opacity * state.stroke_opacity,
        )

    x = _svg_number(element.get("x", "0"))
    y = _svg_number(element.get("y", "0"))
    rect_width = _svg_number(element.get("width"))
    rect_height = _svg_number(element.get("height"))
    if (
        x is None
        or y is None
        or rect_width is None
        or rect_height is None
        or rect_width <= 0
        or rect_height <= 0
    ):
        return None, True

    a, b, c, d, e, f = state.matrix
    if not math.isclose(b, 0.0, abs_tol=1e-9) or not math.isclose(c, 0.0, abs_tol=1e-9):
        return None, True
    left, right = sorted((a * x + e, a * (x + rect_width) + e))
    top, bottom = sorted((d * y + f, d * (y + rect_height) + f))
    if right <= 0 or bottom <= 0 or left >= width or top >= height:
        return None, False

    raw_radius_x = _svg_number(element.get("rx"))
    raw_radius_y = _svg_number(element.get("ry"))
    if element.get("rx") is not None and raw_radius_x is None:
        return None, True
    if element.get("ry") is not None and raw_radius_y is None:
        return None, True
    radius_x = raw_radius_x if raw_radius_x is not None else (raw_radius_y or 0.0)
    radius_y = raw_radius_y if raw_radius_y is not None else (raw_radius_x or 0.0)
    if radius_x < 0 or radius_y < 0:
        return None, True
    radius_x = min(abs(a) * radius_x, (right - left) / 2)
    radius_y = min(abs(d) * radius_y, (bottom - top) / 2)
    return (
        _SolidRect(
            box=_PixelBox(
                max(0.0, left),
                max(0.0, top),
                min(float(width), right),
                min(float(height), bottom),
            ),
            color=color,
            radius_x=radius_x,
            radius_y=radius_y,
        ),
        False,
    )


def _background_color(backgrounds: list[_SolidRect], box: _PixelBox) -> Color | None:
    color: Color | None = None
    partially_covered = False
    for background in backgrounds:
        if background.covers(box):
            color = background.color
            partially_covered = False
        elif background.box.intersects(box):
            partially_covered = True
    return None if partially_covered else color


def _visible_text_runs(
    element: ET.Element,
    state: _RenderState,
) -> tuple[list[_TextRun], str | None]:
    if state.display_hidden or state.non_rendered or state.opacity <= 0:
        return [], None
    if state.uncertain_visibility:
        return (
            [],
            "SVG clipping, masking, filtering, or conditional visibility is unsupported; "
            "affected evidence was omitted",
        )
    if state.matrix is None:
        return [], "Unsupported SVG transforms affected rendering; affected evidence was omitted"

    colors, paint_warning = _supported_text_colors(state)
    if paint_warning is not None:
        return [], paint_warning
    runs = [_TextRun(element.text, colors)] if colors and element.text else []
    for child in element:
        if _local_name(child.tag) != "tspan":
            return [], "Unsupported SVG text-run semantics affected rendering; evidence was omitted"
        child_runs, child_warning = _visible_text_runs(child, _render_state(child, state))
        if child_warning is not None:
            return [], child_warning
        runs.extend(child_runs)
        if colors and child.tail:
            runs.append(_TextRun(child.tail, colors))
    return runs, None


def _supported_text_colors(state: _RenderState) -> tuple[frozenset[Color], str | None]:
    if state.visibility in {"hidden", "collapse"} or state.font_size <= 0:
        return frozenset(), None
    if state.visibility not in {"visible", "inherit"}:
        return (
            frozenset(),
            "Unsupported SVG visibility values affected rendering; evidence was omitted",
        )

    colors: set[Color] = set()
    for value, opacity in (
        (state.fill, state.opacity * state.fill_opacity),
        (state.stroke, state.opacity * state.stroke_opacity),
    ):
        color, ambiguous = _opaque_color(value, opacity)
        if ambiguous:
            return (
                frozenset(),
                "Unsupported or translucent SVG text paint affected rendering; "
                "evidence was omitted",
            )
        if color is not None:
            colors.add(color)
    return frozenset(colors), None


def _opaque_color(value: str, opacity: float) -> tuple[Color | None, bool]:
    normalized = value.strip().lower()
    if opacity <= 0 or normalized in {"none", "transparent"}:
        return None, False
    parsed = _parse_color(normalized)
    if parsed is None:
        return None, True
    color, alpha = parsed
    effective_opacity = opacity * alpha
    if effective_opacity <= 0:
        return None, False
    if not math.isclose(effective_opacity, 1.0, abs_tol=1e-9):
        return None, True
    return color, False


def _parse_color(value: str) -> tuple[Color, float] | None:
    if value in NAMED_COLORS:
        return NAMED_COLORS[value], 1.0
    if match := re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})", value):
        digits = match.group(1)
        if len(digits) in {3, 4}:
            hex_channels = [int(character * 2, 16) for character in digits]
        else:
            hex_channels = [
                int(digits[index : index + 2], 16) for index in range(0, len(digits), 2)
            ]
        alpha = hex_channels[3] / 255 if len(hex_channels) == 4 else 1.0
        return (hex_channels[0], hex_channels[1], hex_channels[2]), alpha
    match = re.fullmatch(r"rgba?\(([^)]*)\)", value)
    if match is None:
        return None
    parts = [part.strip() for part in match.group(1).split(",")]
    if len(parts) not in {3, 4}:
        return None
    red, green, blue = (_color_channel(part) for part in parts[:3])
    if red is None or green is None or blue is None:
        return None
    color_alpha = _color_alpha(parts[3]) if len(parts) == 4 else 1.0
    if color_alpha is None:
        return None
    return (red, green, blue), color_alpha


def _color_channel(value: str) -> int | None:
    try:
        parsed = float(value[:-1]) * 2.55 if value.endswith("%") else float(value)
    except ValueError:
        return None
    if not math.isfinite(parsed) or not 0 <= parsed <= 255:
        return None
    return round(parsed)


def _color_alpha(value: str) -> float | None:
    try:
        parsed = float(value[:-1]) / 100 if value.endswith("%") else float(value)
    except ValueError:
        return None
    if not math.isfinite(parsed) or not 0 <= parsed <= 1:
        return None
    return parsed


def _contrast_ratio(left: Color, right: Color) -> float:
    lighter, darker = sorted((_relative_luminance(left), _relative_luminance(right)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(color: Color) -> float:
    channels = []
    for value in color:
        normalized = value / 255
        channels.append(
            normalized / 12.92 if normalized <= 0.04045 else ((normalized + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _paint_active(value: str, opacity: float) -> bool:
    return opacity > 0 and value.strip().lower() not in {"none", "transparent"}


def _element_may_paint(state: _RenderState) -> bool:
    if state.visibility in {"hidden", "collapse"} or state.opacity <= 0:
        return False
    return _paint_active(state.fill, state.opacity * state.fill_opacity) or _paint_active(
        state.stroke, state.opacity * state.stroke_opacity
    )


def _svg_number(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(rf"\s*({NUMBER_PATTERN})\s*(?:px)?\s*", value)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _view_box(root: ET.Element) -> tuple[float, float, float, float] | None:
    value = root.get("viewBox")
    if value is None:
        return None
    numbers = _number_list(value)
    if numbers is None or len(numbers) != 4 or numbers[2] <= 0 or numbers[3] <= 0:
        return None
    return numbers[0], numbers[1], numbers[2], numbers[3]


def _viewport_matrix(root: ET.Element, width: int, height: int) -> Matrix | None:
    view_box = _view_box(root)
    if view_box is None:
        return IDENTITY_MATRIX
    min_x, min_y, view_width, view_height = view_box
    preserve = root.get("preserveAspectRatio", "xMidYMid meet").strip().split()
    if preserve and preserve[0] == "defer":
        preserve = preserve[1:]
    align = preserve[0] if preserve else "xMidYMid"
    mode = preserve[1] if len(preserve) > 1 else "meet"
    if align == "none":
        scale_x = width / view_width
        scale_y = height / view_height
        return (
            scale_x,
            0.0,
            0.0,
            scale_y,
            -min_x * scale_x,
            -min_y * scale_y,
        )
    align_match = re.fullmatch(r"x(Min|Mid|Max)Y(Min|Mid|Max)", align)
    if align_match is None or mode not in {"meet", "slice"}:
        return None
    scale = (
        min(width / view_width, height / view_height)
        if mode == "meet"
        else max(width / view_width, height / view_height)
    )
    factors = {"Min": 0.0, "Mid": 0.5, "Max": 1.0}
    x_offset = (width - view_width * scale) * factors[align_match.group(1)]
    y_offset = (height - view_height * scale) * factors[align_match.group(2)]
    return (
        scale,
        0.0,
        0.0,
        scale,
        -min_x * scale + x_offset,
        -min_y * scale + y_offset,
    )


def _render_state(element: ET.Element, parent: _RenderState) -> _RenderState:
    properties = _style_properties(element)
    display_hidden = parent.display_hidden or properties.get("display", "").lower() == "none"
    non_rendered = parent.non_rendered or _local_name(element.tag) in NON_RENDERED_CONTAINERS
    visibility = properties.get("visibility", parent.visibility).lower()
    if visibility == "inherit":
        visibility = parent.visibility
    opacity = parent.opacity * _opacity(properties.get("opacity"), 1.0)
    fill = properties.get("fill", parent.fill).lower()
    if fill == "inherit":
        fill = parent.fill
    stroke = properties.get("stroke", parent.stroke).lower()
    if stroke == "inherit":
        stroke = parent.stroke
    fill_opacity = _opacity(properties.get("fill-opacity"), parent.fill_opacity)
    stroke_opacity = _opacity(properties.get("stroke-opacity"), parent.stroke_opacity)
    parsed_font_size = _svg_number(properties.get("font-size"))
    font_size = parsed_font_size if parsed_font_size is not None else parent.font_size
    uncertain_visibility = parent.uncertain_visibility or any(
        properties.get(name, "none").lower() != "none" for name in ("clip-path", "mask")
    )
    uncertain_visibility = (
        uncertain_visibility or properties.get("filter", "none").lower() != "none"
    )
    uncertain_visibility = (
        uncertain_visibility or properties.get("mix-blend-mode", "normal").lower() != "normal"
    )
    uncertain_visibility = uncertain_visibility or any(
        element.get(name) is not None
        for name in ("requiredExtensions", "requiredFeatures", "systemLanguage")
    )
    matrix = parent.matrix
    if matrix is not None:
        if "transform" in properties:
            matrix = None
        elif element.get("transform"):
            transform = _transform_matrix(element.get("transform", ""))
            matrix = _multiply_matrix(matrix, transform) if transform is not None else None
    return _RenderState(
        display_hidden=display_hidden,
        non_rendered=non_rendered,
        uncertain_visibility=uncertain_visibility,
        visibility=visibility,
        opacity=opacity,
        fill=fill,
        stroke=stroke,
        fill_opacity=fill_opacity,
        stroke_opacity=stroke_opacity,
        font_size=font_size,
        matrix=matrix,
    )


def _style_properties(element: ET.Element) -> dict[str, str]:
    presentation_names = (
        "clip-path",
        "display",
        "fill",
        "fill-opacity",
        "filter",
        "font-size",
        "mask",
        "mix-blend-mode",
        "opacity",
        "stroke",
        "stroke-opacity",
        "visibility",
    )
    properties = {
        name: value.strip()
        for name in presentation_names
        if (value := element.get(name)) is not None and value.strip()
    }
    style = element.get("style")
    if style:
        for declaration in style.split(";"):
            name, separator, value = declaration.partition(":")
            if (
                separator
                and name.strip().lower() in {*presentation_names, "transform"}
                and value.strip()
            ):
                properties[name.strip().lower()] = re.sub(
                    r"\s*!important\s*$",
                    "",
                    value.strip(),
                    flags=re.IGNORECASE,
                )
    return properties


def _has_independent_text_geometry(element: ET.Element) -> bool:
    root_geometry = {
        "dominant-baseline",
        "dx",
        "dy",
        "lengthAdjust",
        "letter-spacing",
        "rotate",
        "text-anchor",
        "textLength",
        "word-spacing",
        "writing-mode",
    }
    if root_geometry.intersection(element.attrib) or _style_declares(element, root_geometry):
        return True
    geometry_attributes = root_geometry | {"font-size", "transform", "x", "y"}
    for descendant in element.iter():
        if descendant is element:
            continue
        if _local_name(descendant.tag) in {"textpath", "tref"}:
            return True
        if geometry_attributes.intersection(descendant.attrib):
            return True
        if _style_declares(descendant, geometry_attributes):
            return True
    return False


def _style_declares(element: ET.Element, names: set[str]) -> bool:
    style = element.get("style", "")
    return any(
        separator and name.strip().lower() in names
        for declaration in style.split(";")
        for name, separator, _value in [declaration.partition(":")]
    )


def _opacity(value: str | None, fallback: float) -> float:
    if value is None:
        return fallback
    try:
        parsed = float(value[:-1]) / 100.0 if value.endswith("%") else float(value)
    except ValueError:
        return fallback
    if not math.isfinite(parsed):
        return fallback
    return min(1.0, max(0.0, parsed))


def _number_list(value: str) -> tuple[float, ...] | None:
    values = tuple(float(match.group(0)) for match in NUMBER_RE.finditer(value))
    remainder = NUMBER_RE.sub("", value)
    if remainder.replace(",", " ").strip():
        return None
    return values


def _transform_matrix(value: str) -> Matrix | None:
    result = IDENTITY_MATRIX
    position = 0
    matched = False
    for match in TRANSFORM_RE.finditer(value):
        if value[position : match.start()].replace(",", " ").strip():
            return None
        numbers = _number_list(match.group(2))
        if numbers is None:
            return None
        operation = _transform_operation(match.group(1).lower(), numbers)
        if operation is None:
            return None
        result = _multiply_matrix(result, operation)
        position = match.end()
        matched = True
    if not matched or value[position:].replace(",", " ").strip():
        return None
    return result


def _transform_operation(name: str, values: tuple[float, ...]) -> Matrix | None:
    if name == "matrix" and len(values) == 6:
        return values[0], values[1], values[2], values[3], values[4], values[5]
    if name == "translate" and len(values) in {1, 2}:
        return (1.0, 0.0, 0.0, 1.0, values[0], values[1] if len(values) == 2 else 0.0)
    if name == "scale" and len(values) in {1, 2}:
        return (
            values[0],
            0.0,
            0.0,
            values[1] if len(values) == 2 else values[0],
            0.0,
            0.0,
        )
    if name == "rotate" and len(values) in {1, 3}:
        radians = math.radians(values[0])
        rotation: Matrix = (
            math.cos(radians),
            math.sin(radians),
            -math.sin(radians),
            math.cos(radians),
            0.0,
            0.0,
        )
        if len(values) == 1:
            return rotation
        to_origin: Matrix = (1.0, 0.0, 0.0, 1.0, -values[1], -values[2])
        from_origin: Matrix = (1.0, 0.0, 0.0, 1.0, values[1], values[2])
        return _multiply_matrix(from_origin, _multiply_matrix(rotation, to_origin))
    if name in {"skewx", "skewy"} and len(values) == 1:
        tangent = math.tan(math.radians(values[0]))
        if not math.isfinite(tangent):
            return None
        return (
            1.0,
            tangent if name == "skewy" else 0.0,
            tangent if name == "skewx" else 0.0,
            1.0,
            0.0,
            0.0,
        )
    return None


def _multiply_matrix(left: Matrix, right: Matrix) -> Matrix:
    left_a, left_b, left_c, left_d, left_e, left_f = left
    right_a, right_b, right_c, right_d, right_e, right_f = right
    return (
        left_a * right_a + left_c * right_b,
        left_b * right_a + left_d * right_b,
        left_a * right_c + left_c * right_d,
        left_b * right_c + left_d * right_d,
        left_a * right_e + left_c * right_f + left_e,
        left_b * right_e + left_d * right_f + left_f,
    )


def _apply_matrix(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _heading(value: str) -> tuple[str, str] | None:
    normalized = re.sub(r"\s+", " ", value).strip(" :")
    headings = {
        "revision state": ("status", "revision state"),
        "status": ("status", "status"),
        "ingress": ("ingress", "ingress"),
        "readiness": ("readiness", "readiness"),
        "environment": ("environment", "environment"),
        "listener": ("listener", "listener"),
    }
    return headings.get(normalized)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()
