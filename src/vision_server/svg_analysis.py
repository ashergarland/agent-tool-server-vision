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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .text_limits import truncate_utf16, utf16_length

SUPPORTED_LANGUAGES = frozenset({"en", "ch", "fr", "german", "japan", "korean"})
MAX_SVG_ELEMENTS = 5_000
MAX_TEXT_BLOCKS = 500
MAX_TEXT_CHARS = 200_000
MAX_LINE_CHARS = 4_000
NON_RENDERED_CONTAINERS = frozenset(
    {
        "clippath",
        "defs",
        "marker",
        "mask",
        "metadata",
        "pattern",
        "script",
        "style",
        "switch",
        "symbol",
    }
)
NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
NUMBER_RE = re.compile(NUMBER_PATTERN)
TRANSFORM_RE = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")

Matrix = tuple[float, float, float, float, float, float]
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
    lines, text_truncated, stylesheet_ignored = _extract_lines(root, width, height)
    facts, facts_truncated = extract_facts(lines, parsed["max_facts"], "embedded_svg_text")
    extracted_text = "\n".join(line.text for line in lines)
    fallback_used = len(facts) == 0
    warnings: list[str] = []
    if text_truncated:
        warnings.append("SVG text was truncated to the configured output bounds")
    if stylesheet_ignored:
        warnings.append(
            "SVG embedded text was not used because stylesheet-driven visibility is unsupported"
        )
    if facts_truncated:
        warnings.append(f"structured facts were truncated to {parsed['max_facts']} entries")
    fallback_reason: str | None = None
    if fallback_used:
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
) -> tuple[list[EvidenceLine], bool, bool]:
    if any(_local_name(element.tag) == "style" for element in root.iter()):
        return [], False, True

    lines: list[EvidenceLine] = []
    total_chars = 0
    truncated = False
    initial_state = _RenderState(matrix=_viewport_matrix(root, width, height))

    def visit(element: ET.Element, parent_state: _RenderState, *, is_root: bool = False) -> None:
        nonlocal total_chars, truncated
        if truncated:
            return
        state = _render_state(element, parent_state)
        if _local_name(element.tag) == "svg" and not is_root:
            state = replace(state, matrix=None)
        if _local_name(element.tag) != "text":
            for child in element:
                visit(child, state)
            return
        if _has_independent_text_geometry(element):
            return
        text = _clean(_visible_text(element, state))
        if not text:
            return
        if len(lines) >= MAX_TEXT_BLOCKS or total_chars >= MAX_TEXT_CHARS:
            truncated = True
            return
        available = min(MAX_LINE_CHARS, MAX_TEXT_CHARS - total_chars)
        if utf16_length(text) > available:
            text = truncate_utf16(text, available)
            truncated = True
        box, outside_viewport = _text_box(
            element,
            text,
            state.font_size,
            state.matrix,
            width,
            height,
        )
        if outside_viewport:
            return
        block_id = f"b{len(lines):04d}"
        lines.append(
            EvidenceLine(
                block_id=block_id,
                text=text,
                confidence=0.99,
                box=box,
            )
        )
        total_chars += utf16_length(text) + 1

    visit(root, initial_state, is_root=True)
    return lines, truncated, False


def _text_box(
    element: ET.Element,
    text: str,
    font_size: float,
    matrix: Matrix | None,
    width: int,
    height: int,
) -> tuple[dict[str, float] | None, bool]:
    x = _svg_number(element.get("x"))
    baseline = _svg_number(element.get("y"))
    if x is None or baseline is None or matrix is None:
        return None, False
    local_left = x
    local_top = baseline - font_size
    local_right = x + len(text) * font_size * 0.62
    local_bottom = local_top + font_size * 1.2
    points = [
        _apply_matrix(matrix, local_left, local_top),
        _apply_matrix(matrix, local_right, local_top),
        _apply_matrix(matrix, local_left, local_bottom),
        _apply_matrix(matrix, local_right, local_bottom),
    ]
    if not all(math.isfinite(coordinate) for point in points for coordinate in point):
        return None, False
    left = min(point[0] for point in points)
    right = max(point[0] for point in points)
    top = min(point[1] for point in points)
    bottom = max(point[1] for point in points)
    if right <= 0 or bottom <= 0 or left >= width or top >= height:
        return None, True
    clipped_left = max(0.0, left)
    clipped_top = max(0.0, top)
    clipped_right = min(float(width), right)
    clipped_bottom = min(float(height), bottom)
    return (
        {
            "x": round(clipped_left / width, 6),
            "y": round(clipped_top / height, 6),
            "width": round(max(0.0, clipped_right - clipped_left) / width, 6),
            "height": round(max(0.0, clipped_bottom - clipped_top) / height, 6),
        },
        False,
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
        "font-size",
        "mask",
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


def _visible_text(element: ET.Element, state: _RenderState) -> str:
    if (
        state.display_hidden
        or state.non_rendered
        or state.uncertain_visibility
        or state.opacity <= 0
        or state.matrix is None
    ):
        return ""
    painted = _text_is_painted(state)
    parts = [element.text or ""] if painted else []
    for child in element:
        child_state = _render_state(child, state)
        parts.append(_visible_text(child, child_state))
        if painted and child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _has_independent_text_geometry(element: ET.Element) -> bool:
    if {"dx", "dy", "lengthAdjust", "rotate", "textLength"}.intersection(element.attrib):
        return True
    geometry_attributes = {
        "dx",
        "dy",
        "lengthAdjust",
        "rotate",
        "textLength",
        "transform",
        "x",
        "y",
    }
    for descendant in element.iter():
        if descendant is element:
            continue
        if _local_name(descendant.tag) in {"textpath", "tref"}:
            return True
        if geometry_attributes.intersection(descendant.attrib):
            return True
        style = descendant.get("style", "").lower()
        if re.search(r"(?:^|;)\s*transform\s*:", style):
            return True
    return False


def _text_is_painted(state: _RenderState) -> bool:
    if state.visibility not in {"visible", "inherit"} or state.font_size <= 0:
        return False
    return _paint_is_visible(state.fill, state.fill_opacity) or _paint_is_visible(
        state.stroke, state.stroke_opacity
    )


def _paint_is_visible(value: str, opacity: float) -> bool:
    if opacity <= 0 or value in {"none", "transparent"}:
        return False
    if re.fullmatch(r"#[0-9a-f]{8}", value) and value.endswith("00"):
        return False
    rgba = re.fullmatch(r"rgba\([^,]+,[^,]+,[^,]+,\s*([^)]+)\)", value)
    return rgba is None or _opacity(rgba.group(1), 1.0) > 0


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
