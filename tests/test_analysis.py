"""Structured visual analysis and benchmark proof."""

from __future__ import annotations

from pathlib import Path

import pytest

from vision_server.schemas import AnalyzeImageInput, LocalPathImage
from vision_server.svg_analysis import MAX_SVG_ELEMENTS, SvgAnalysisError, analyze_svg
from vision_server.tools.analyze import analyze_image

from .conftest import FakeOcrProvider

FIXTURE = Path(__file__).parent / "fixtures" / "portal-snapshot.svg"


def local_input(path: Path, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "image": {"kind": "local_path", "path": str(path.resolve())},
    }
    value.update(overrides)
    return value


def test_level_two_svg_extracts_required_facts_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(FIXTURE.parent.resolve()))
    result = analyze_svg(local_input(FIXTURE))
    facts = result["facts"]
    assert isinstance(facts, list)

    def has(kind: str, value: str) -> bool:
        return any(fact["kind"] == kind and fact["value"] == value for fact in facts)

    assert has("revision", "checkout-api--pr-1842")
    assert has("status", "Degraded")
    assert has("listener", "3000")
    assert has("ingress", "8080")
    assert has("readiness", "8080")
    assert has("environment", "production")
    assert any(
        fact["kind"] == "readiness" and "connection_refused" in fact["evidence"] for fact in facts
    )
    assert all(fact["confidence"] > 0 and fact["provenance"]["blockIds"] for fact in facts)
    assert result["fallbackUsed"] is False


def test_svg_analysis_promotes_supported_visible_text_with_high_confidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "visible.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80">'
        '<rect width="240" height="80" fill="rgb(100%, 100%, 100%)"/>'
        '<text x="10" y="40" fill="rgba(17, 17, 17, 100%)">'
        "<tspan>server_started port=3000</tspan></text>"
        "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    listener = next(
        fact for fact in result["facts"] if fact["kind"] == "listener" and fact["value"] == "3000"
    )
    assert listener["confidence"] >= 0.95
    assert result["fallbackUsed"] is False
    assert result["meta"]["warnings"] == []


def test_svg_analysis_omits_text_indistinguishable_from_solid_background(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "same-color.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80">'
        '<rect width="240" height="80" fill="white"/>'
        '<text x="10" y="40" fill="#fff">server_started port=9999</text>'
        "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    assert result["facts"] == []
    assert result["extractedText"] == ""
    assert result["fallbackUsed"] is True
    assert any("indistinguishable" in warning for warning in result["meta"]["warnings"])


def test_svg_analysis_omits_text_covered_by_later_opaque_geometry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "occluded.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80">'
        '<rect width="240" height="80" fill="#ffffff"/>'
        '<text x="10" y="40" fill="#111111">server_started port=9999</text>'
        '<rect x="0" y="0" width="240" height="60" fill="#111111"/>'
        "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    assert result["facts"] == []
    assert result["fallbackUsed"] is True
    assert any("later opaque geometry" in warning for warning in result["meta"]["warnings"])


def test_svg_analysis_omits_overlapping_text_with_ambiguous_paint_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "overlapping-text.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80">'
        '<rect width="240" height="80" fill="#fff"/>'
        '<text x="10" y="40">server_started port=9999</text>'
        '<text x="10" y="40">server_started port=8888</text>'
        "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    assert result["facts"] == []
    assert result["fallbackUsed"] is True
    assert any("ambiguous paint order" in warning for warning in result["meta"]["warnings"])


def test_svg_analysis_omits_translucent_text_and_ambiguous_background_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ambiguous-paint.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="120">'
        '<rect width="300" height="120" fill="#ffffff"/>'
        '<text x="10" y="35" fill="rgba(0, 0, 0, 0.5)">'
        "server_started port=9999</text>"
        '<rect x="0" y="50" width="30" height="50" fill="#ff0000"/>'
        '<text x="10" y="80">server_started port=8888</text>'
        "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    assert result["facts"] == []
    assert result["fallbackUsed"] is True
    assert any("translucent" in warning for warning in result["meta"]["warnings"])
    assert any("solid background" in warning for warning in result["meta"]["warnings"])


def test_svg_analysis_rejects_external_entities_and_outside_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    hostile = root / "hostile.svg"
    hostile.write_text(
        '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        "<text>&xxe;</text></svg>"
    )
    outside = tmp_path / "outside.svg"
    outside.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(root.resolve()))

    with pytest.raises(SvgAnalysisError) as entity:
        analyze_svg(local_input(hostile))
    assert entity.value.code == "invalid_input"

    with pytest.raises(SvgAnalysisError) as escaped:
        analyze_svg(local_input(outside))
    assert escaped.value.code == "forbidden"


def test_svg_analysis_rejects_utf16_declarations_and_excess_elements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded_declaration = tmp_path / "encoded-declaration.svg"
    encoded_declaration.write_bytes(
        (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE svg [<!ENTITY hidden "server_started port=9999">]>'
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
            "<text>&hidden;</text></svg>"
        ).encode("utf-16")
    )
    excessive = tmp_path / "excessive.svg"
    excessive.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        + "<g/>" * MAX_SVG_ELEMENTS
        + "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    with pytest.raises(SvgAnalysisError) as declaration:
        analyze_svg(local_input(encoded_declaration))
    assert declaration.value.code == "invalid_input"

    with pytest.raises(SvgAnalysisError) as structure:
        analyze_svg(local_input(excessive))
    assert structure.value.code == "payload_too_large"


def test_svg_analysis_rejects_external_stylesheets_malformed_xml_and_non_svg_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_style = tmp_path / "external-style.svg"
    external_style.write_text(
        '<?xml-stylesheet href="https://example.invalid/hidden.css"?>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    )
    malformed = tmp_path / "malformed.svg"
    malformed.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">')
    wrong_root = tmp_path / "wrong-root.svg"
    wrong_root.write_text("<html/>")
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    with pytest.raises(SvgAnalysisError) as stylesheet:
        analyze_svg(local_input(external_style))
    assert stylesheet.value.code == "invalid_input"

    with pytest.raises(SvgAnalysisError) as invalid_xml:
        analyze_svg(local_input(malformed))
    assert invalid_xml.value.code == "invalid_input"

    with pytest.raises(SvgAnalysisError) as unsupported:
        analyze_svg(local_input(wrong_root))
    assert unsupported.value.code == "unsupported_media"


def test_svg_analysis_uses_only_visibly_rendered_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "visibility.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect width="200" height="100" fill="#ffffff"/>'
        '<defs><text x="1" y="20">server_started port=9999</text></defs>'
        '<g display="none"><text x="1" y="40">server_started port=8888</text></g>'
        '<text x="1" y="60" opacity="0">server_started port=7777</text>'
        '<text x="1" y="80">server_started port=3000</text>'
        "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    facts = result["facts"]
    assert isinstance(facts, list)
    assert any(fact["kind"] == "listener" and fact["value"] == "3000" for fact in facts)
    assert not any(fact["value"] in {"9999", "8888", "7777"} for fact in facts)


def test_svg_analysis_handles_inherited_visibility_paint_and_transform_lists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "rendering.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200">'
        '<rect width="400" height="200" fill="#ffffff"/>'
        '<g visibility="hidden"><text x="10" y="30">'
        '<tspan visibility="visible">server_started port=4000</tspan>'
        "</text></g>"
        '<text x="10" y="60" fill="none" stroke="none">server_started port=9999</text>'
        '<text x="10" y="90" fill="#00000000">server_started port=8888</text>'
        '<text x="10" y="120" style="fill: none; stroke: black; stroke-opacity: 100%; '
        'font-size: 12px !important" '
        'transform="translate(0) scale(1,1) rotate(0 0 0) skewX(0) skewY(0) '
        'matrix(1 0 0 1 0 0)">server_started port=5000</text>'
        '<text x="1000" y="1000">server_started port=7777</text>'
        '<text x="10" y="150" mask="url(#mask)">server_started port=6666</text>'
        "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    facts = result["facts"]
    assert isinstance(facts, list)
    values = {fact["value"] for fact in facts if fact["kind"] == "listener"}
    assert {"4000", "5000"} <= values
    assert not values & {"9999", "8888", "7777", "6666"}
    assert result["fallbackUsed"] is True
    assert any("masking" in warning for warning in result["meta"]["warnings"])


def test_svg_analysis_fails_closed_for_positioned_descendant_text_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "positioned-text-run.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect width="200" height="100" fill="#ffffff"/>'
        '<text x="10" y="10" dx="1000">server_started port=9997</text>'
        '<text x="10" y="15" rotate="90">server_started port=9996</text>'
        '<text x="10" y="20" textLength="190">server_started port=9995</text>'
        '<text x="10" y="20"><tspan x="1000" y="1000">'
        "server_started port=9999</tspan></text>"
        '<text x="10" y="40"><tspan style="transform: translate(1000px, 1000px)">'
        "server_started port=8888</tspan></text>"
        '<text x="10" y="60">server_started port=3000</text>'
        "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    facts = result["facts"]
    assert isinstance(facts, list)
    values = {fact["value"] for fact in facts if fact["kind"] == "listener"}
    assert values == {"3000"}
    listener = next(fact for fact in facts if fact["value"] == "3000")
    assert listener["confidence"] < 0.8
    assert result["fallbackUsed"] is True
    assert any("positioned or transformed" in warning for warning in result["meta"]["warnings"])


def test_svg_analysis_fails_closed_for_nested_and_conditional_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "conditional.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect width="200" height="100" fill="#ffffff"/>'
        '<svg x="1000" y="1000" width="10" height="10">'
        '<text x="1" y="8">server_started port=9999</text></svg>'
        "<switch>"
        '<text systemLanguage="zz" x="1" y="20">server_started port=8888</text>'
        '<text x="1" y="40">server_started port=7777</text>'
        "</switch>"
        '<text requiredFeatures="unknown" x="1" y="60">server_started port=6666</text>'
        '<text x="1" y="80">server_started port=3000</text>'
        "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    facts = result["facts"]
    assert isinstance(facts, list)
    values = {fact["value"] for fact in facts if fact["kind"] == "listener"}
    assert values == {"3000"}
    assert result["fallbackUsed"] is True
    assert any("Nested SVG viewport" in warning for warning in result["meta"]["warnings"])
    assert any("conditional rendering" in warning for warning in result["meta"]["warnings"])


def test_svg_analysis_preserves_supported_evidence_in_mixed_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "mixed.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="100">'
        '<rect width="240" height="100" fill="#ffffff"/>'
        '<text x="10" y="35" fill="#111111">server_started port=3000</text>'
        '<circle cx="220" cy="80" r="10" fill="#000000"/>'
        '<text x="10" y="70" clip-path="url(#unknown)">server_started port=9999</text>'
        "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    listeners = [fact for fact in result["facts"] if fact["kind"] == "listener"]
    assert [fact["value"] for fact in listeners] == ["3000"]
    assert listeners[0]["confidence"] < 0.8
    assert result["fallbackUsed"] is True
    assert result["fallbackReason"] is not None
    assert any(
        "Unsupported painted SVG geometry" in warning for warning in result["meta"]["warnings"]
    )
    assert any("clipping" in warning for warning in result["meta"]["warnings"])


def test_svg_analysis_falls_back_when_stylesheets_control_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "stylesheet.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        "<style>text { display: none; }</style>"
        '<text x="1" y="20">server_started port=9999</text>'
        "</svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    assert result["facts"] == []
    assert result["extractedText"] == ""
    assert result["fallbackUsed"] is True
    assert any("stylesheet-driven visibility" in warning for warning in result["meta"]["warnings"])


def test_svg_analysis_applies_viewbox_and_group_transforms_to_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "coordinates.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" '
        'viewBox="100 50 400 200">'
        '<rect x="100" y="50" width="400" height="200" fill="#ffffff"/>'
        '<g transform="translate(20 10)">'
        '<text x="100" y="70" font-size="20">server_started port=3000</text>'
        "</g></svg>"
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))

    result = analyze_svg(local_input(path))
    facts = result["facts"]
    assert isinstance(facts, list)
    listener = next(
        fact for fact in facts if fact["kind"] == "listener" and fact["value"] == "3000"
    )
    assert listener["box"]["x"] == pytest.approx(0.05)
    assert listener["box"]["y"] == pytest.approx(0.05)


def test_svg_analysis_records_text_only_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "figure.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<rect width="100" height="100" fill="#ffffff"/>'
        '<text x="1" y="20">decorative caption</text></svg>'
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(tmp_path.resolve()))
    result = analyze_svg(local_input(path))
    assert result["facts"] == []
    assert result["fallbackUsed"] is True
    assert "extractedText" in str(result["fallbackReason"])


async def test_raster_analysis_uses_normalized_ocr_evidence(
    context: object,
    allowed_root: Path,
    local_provider: FakeOcrProvider,
) -> None:
    from vision_server.providers.base import OcrBlock

    from .conftest import write_png

    local_provider._blocks = (  # noqa: SLF001
        OcrBlock("server_started port=4321", "line", 1, (1, 1, 20, 1, 20, 5, 1, 5), 0.9),
    )
    path = write_png(allowed_root / "runtime.png")
    result = await analyze_image(
        AnalyzeImageInput(image=LocalPathImage(kind="local_path", path=str(path))),
        context,  # type: ignore[arg-type]
    )
    assert result.analysis_mode.value == "ocr"
    assert any(fact.kind.value == "listener" and fact.value == "4321" for fact in result.facts)
    assert result.provider.name == "local_paddleocr"
