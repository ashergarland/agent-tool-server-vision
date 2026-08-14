"""Table-driven routing evaluations for the registry descriptions.

These assert the agent-facing guidance is accurate: each tool states when to
call it, when not to, and never claims a capability this phase does not ship.
"""

from __future__ import annotations

import pytest

from vision_server.registry import SERVER_INSTRUCTIONS, TOOLS, TOOLS_BY_NAME

POSITIVE_CASES = [
    ("extract_text_and_layout", "read the text from this screenshot"),
    ("extract_text_and_layout", "ocr this scanned invoice and give me markdown"),
    ("extract_text_and_layout", "extract the table from this label photo"),
    ("compare_images", "did the screenshot change between these two runs"),
    ("compare_images", "detect the regression between before and after captures"),
    ("optimize_image_region", "crop this region and compress it before vision"),
    ("optimize_image_region", "resize a known bounding box to save tokens"),
]

NEGATIVE_CASES = [
    ("extract_text_and_layout", "describe the mood of this photograph"),
    ("extract_text_and_layout", "parse this architecture diagram into a graph"),
    ("compare_images", "explain what the person in the picture is doing"),
    ("optimize_image_region", "find where the login button is on screen"),
]

UNSUPPORTED_CLAIMS = [
    "ui mapping",
    "diagram parsing",
    "object detection",
    "visual question answering",
    "text-region grounding",
    "image analysis 4.0",
]


@pytest.mark.parametrize(("tool_name", "task"), POSITIVE_CASES)
def test_descriptions_cover_supported_tasks(tool_name: str, task: str) -> None:
    description = TOOLS_BY_NAME[tool_name].description.lower()
    keywords = [word for word in task.split() if len(word) > 4]
    assert any(keyword in description for keyword in keywords), keywords


@pytest.mark.parametrize(("tool_name", "task"), NEGATIVE_CASES)
def test_descriptions_steer_away_from_unsupported_tasks(tool_name: str, task: str) -> None:
    definition = TOOLS_BY_NAME[tool_name]
    section = " ".join(definition.when_not_to_use).lower()
    keywords = [word for word in task.split() if len(word) > 4]
    assert any(keyword.rstrip("s") in section for keyword in keywords), keywords


@pytest.mark.parametrize("claim", UNSUPPORTED_CLAIMS)
def test_no_tool_claims_unsupported_capability(claim: str) -> None:
    for definition in TOOLS:
        assert (
            claim
            not in " ".join(
                (definition.summary, *definition.when_to_use, definition.determinism)
            ).lower()
        )


def test_every_description_states_the_required_facets() -> None:
    for definition in TOOLS:
        description = definition.description
        assert "When to use:" in description
        assert "When not to use:" in description
        assert "Input constraints:" in description
        assert "Behaviour:" in description
        assert "Token savings:" in description
        assert definition.when_to_use and definition.when_not_to_use
        assert "deterministic" in definition.determinism.lower() or "provider" in (
            definition.determinism.lower()
        )


def test_server_instructions_state_the_four_routing_rules() -> None:
    instructions = SERVER_INSTRUCTIONS.lower()
    assert "ocr" in instructions
    assert "compare" in instructions
    assert "crop" in instructions or "optimize" in instructions
    assert "native" in instructions and "vision" in instructions
    assert len(SERVER_INSTRUCTIONS) < 1200
    named = [tool.name for tool in TOOLS if tool.name in SERVER_INSTRUCTIONS]
    assert len(named) < len(TOOLS)


def test_annotations_describe_side_effects() -> None:
    for definition in TOOLS:
        assert definition.annotations.destructive_hint is False
        assert definition.annotations.open_world_hint is (
            definition.name == "extract_text_and_layout"
        )
    assert TOOLS_BY_NAME["compare_images"].annotations.read_only_hint is True
