"""Structured image and figure analysis backed by embedded text or OCR."""

from __future__ import annotations

from ..runtime import ToolContext
from ..schemas import (
    AnalysisFact,
    AnalysisMode,
    AnalyzeImageInput,
    AnalyzeImageOutput,
    ExtractTextInput,
    OutputFormat,
    ResultMeta,
)
from ..svg_analysis import (
    EvidenceLine,
    analyze_svg,
    can_analyze_svg,
    extract_facts,
    summarize_facts,
)
from .extract import extract_text_and_layout


async def analyze_image(payload: AnalyzeImageInput, context: ToolContext) -> AnalyzeImageOutput:
    wire_input = payload.model_dump(mode="json", by_alias=True)
    if can_analyze_svg(wire_input):
        return AnalyzeImageOutput.model_validate(analyze_svg(wire_input))

    extracted = await extract_text_and_layout(
        ExtractTextInput(
            image=payload.image,
            output_format=OutputFormat.TEXT,
            language=payload.language,
            include_coordinates=True,
            processing_mode=payload.processing_mode,
        ),
        context,
    )
    lines = [
        EvidenceLine(
            block_id=block.id,
            text=block.text,
            confidence=block.confidence if block.confidence is not None else 0.75,
            box=block.box.model_dump(mode="json") if block.box is not None else None,
        )
        for block in extracted.blocks
    ]
    fact_values, facts_truncated = extract_facts(lines, payload.max_facts, "ocr")
    facts = [AnalysisFact.model_validate(fact) for fact in fact_values]
    warnings = list(extracted.meta.warnings)
    fallback_reasons: list[str] = []
    if extracted.fallback_used:
        fallback_reasons.append("The configured OCR provider used its recorded local fallback.")
    if not facts:
        fallback_reasons.append(
            "No supported structured fact pattern was recognized; use extractedText or native "
            "vision for unsupported visual semantics."
        )
        warnings.append(fallback_reasons[-1])
    if facts_truncated:
        warnings.append(f"structured facts were truncated to {payload.max_facts} entries")
    return AnalyzeImageOutput(
        summary=summarize_facts(fact_values),
        extracted_text=extracted.content,
        facts=facts,
        dimensions=extracted.dimensions,
        provider=extracted.provider,
        analysis_mode=AnalysisMode.OCR,
        fallback_used=bool(fallback_reasons),
        fallback_reason=" ".join(fallback_reasons) or None,
        meta=ResultMeta(
            warnings=warnings[:20],
            truncated=extracted.meta.truncated or facts_truncated,
        ),
    )
