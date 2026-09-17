"""One-request worker protocol behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from vision_server import worker
from vision_server.schemas import BlockType, ExtractTextOutput, TextBlock
from vision_server.text_limits import utf16_length

FIXTURE = Path(__file__).parent / "fixtures" / "portal-snapshot.svg"
ROOT = Path(__file__).resolve().parents[1]


def request(operation: str, input_value: dict[str, object]) -> dict[str, object]:
    return {
        "protocolVersion": 1,
        "operation": operation,
        "requestId": "worker-test",
        "principal": "test-principal",
        "input": input_value,
    }


def invoke(
    payload: bytes,
    *,
    allowed_root: Path = FIXTURE.parent,
) -> subprocess.CompletedProcess[bytes]:
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "VISION_ALLOWED_ROOTS": str(allowed_root.resolve()),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(  # noqa: S603 - fixed interpreter and module argv
        [sys.executable, "-B", "-m", "vision_server.worker"],
        input=payload,
        capture_output=True,
        timeout=10,
        check=False,
        env=environment,
    )


def decode(completed: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    assert completed.returncode == 0
    assert completed.stderr == b""
    lines = completed.stdout.splitlines()
    assert len(lines) == 1
    value = json.loads(lines[0])
    assert isinstance(value, dict)
    return value


def test_worker_round_trip_and_clean_eof_shutdown() -> None:
    completed = invoke(
        json.dumps(
            request(
                "analyze_image",
                {"image": {"kind": "local_path", "path": str(FIXTURE.resolve())}},
            )
        ).encode()
    )
    envelope = decode(completed)
    assert envelope["ok"] is True
    assert envelope["result"]["provider"]["name"] == "embedded_svg_text"


def test_worker_rejects_malformed_and_oversized_protocol_input() -> None:
    malformed = decode(invoke(b"not-json"))
    assert malformed["ok"] is False
    assert malformed["error"]["code"] == "invalid_input"

    oversized = decode(invoke(b"x" * 65_537))
    assert oversized["ok"] is False
    assert oversized["error"]["code"] == "payload_too_large"


def test_worker_rejects_unknown_fields_deterministically() -> None:
    payload = request("health", {})
    payload["extra"] = True
    envelope = decode(invoke(json.dumps(payload).encode()))
    assert envelope == {
        "protocolVersion": 1,
        "ok": False,
        "error": {
            "code": "invalid_input",
            "message": "Worker request fields are invalid",
            "retryable": False,
            "details": {},
        },
    }


async def test_worker_dispatches_svg_and_domain_errors_in_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(FIXTURE.parent.resolve()))
    svg = await worker._dispatch(  # noqa: SLF001
        request(
            "analyze_image",
            {"image": {"kind": "local_path", "path": str(FIXTURE.resolve())}},
        )
    )
    assert svg["ok"] is True

    invalid = await worker._dispatch(  # noqa: SLF001
        request("extract_text_and_layout", {"unexpected": True})
    )
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "invalid_input"


async def test_worker_health_and_error_helpers_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(FIXTURE.parent.resolve()))
    health = await worker._health()  # noqa: SLF001
    assert health["state"] in {"ready", "degraded", "not_ready"}
    assert len(health["detail"]) <= 200

    principal = worker._principal_bucket("../unsafe")  # noqa: SLF001
    assert principal.startswith("p_")
    assert "/" not in principal
    error = worker._error(  # noqa: SLF001
        "timeout",
        "x" * 400,
        details={str(index): "y" * 400 for index in range(20)},
    )
    assert error["error"]["retryable"] is True
    assert len(error["error"]["message"]) == 300
    assert len(error["error"]["details"]) == 10


def test_worker_output_strings_use_typescript_utf16_limits() -> None:
    emoji = chr(0x1F600)
    block = TextBlock(id="b0000", type=BlockType.LINE, text=emoji * 4000, page=1)
    assert utf16_length(block.text) == 4000
    assert len(block.text) == 2000

    error = worker._error("invalid_input", emoji * 300)  # noqa: SLF001
    assert utf16_length(error["error"]["message"]) == 300


def test_worker_main_writes_exactly_one_protocol_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        worker,
        "_read_request",
        lambda: request(
            "analyze_image",
            {"image": {"kind": "local_path", "path": str(FIXTURE.resolve())}},
        ),
    )
    monkeypatch.setenv("VISION_ALLOWED_ROOTS", str(FIXTURE.parent.resolve()))
    worker.main()
    output = capsys.readouterr()
    assert output.err == ""
    assert len(output.out.splitlines()) == 1
    assert json.loads(output.out)["ok"] is True


def test_worker_truncates_schema_valid_multibyte_output_to_the_process_budget() -> None:
    block_text = "界" * 4000
    response = worker._success(  # noqa: SLF001
        {
            "content": "界" * 200_000,
            "blocks": [
                {
                    "id": f"b{index:04d}",
                    "type": "line",
                    "text": block_text,
                    "page": 1,
                    "box": None,
                    "polygon": None,
                    "confidence": 0.9,
                }
                for index in range(500)
            ],
            "dimensions": {"width": 100, "height": 100},
            "provider": {
                "name": "local_paddleocr",
                "mode": "local",
                "model": "test",
                "apiVersion": None,
            },
            "fallbackUsed": False,
            "meta": {"warnings": [], "truncated": False},
        }
    )

    encoded = worker._encode_response(response, 65_536)  # noqa: SLF001
    assert len(encoded.encode("ascii")) <= 65_536
    decoded = json.loads(encoded)
    output = ExtractTextOutput.model_validate(decoded["result"])
    assert output.meta.truncated is True
    assert any("output byte limit" in warning for warning in output.meta.warnings)
