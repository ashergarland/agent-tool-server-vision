import base64
import io

from fastapi.testclient import TestClient
from PIL import Image

from vision_server.config import Settings
from vision_server.main import create_app
from vision_server.ocr import OcrFragment, OcrUnavailableError


class FakeOcrEngine:
    def extract(self, image: Image.Image, language: str) -> list[OcrFragment]:
        assert image.mode == "RGB"
        assert language == "en"
        return [
            OcrFragment("second", 1.2, ((20, 20), (50, 20), (50, 30), (20, 30))),
            OcrFragment("first", 0.95, ((10, 2), (40, 2), (40, 12), (10, 12))),
        ]


def image_base64(width: int = 64, height: int = 32) -> str:
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


def client(**settings: int) -> TestClient:
    config = Settings(**settings)
    return TestClient(create_app(config, FakeOcrEngine()))


def test_health_and_openapi() -> None:
    api = client()
    assert api.get("/health").json() == {
        "status": "ok",
        "service": "agent-tool-server-vision",
        "version": "0.1.0",
    }
    operation = api.get("/openapi.json").json()["paths"]["/tools/extract_text_and_layout"]
    assert "post" in operation


def test_extracts_markdown_in_visual_order() -> None:
    response = client().post(
        "/tools/extract_text_and_layout",
        json={"image_base64": f"data:image/png;base64,{image_base64()}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "- first\n- second"
    assert body["fragments"][0] == {
        "text": "first",
        "confidence": 0.95,
        "bounding_box": {"x": 10, "y": 2, "width": 30, "height": 10},
    }
    assert body["fragments"][1]["confidence"] == 1
    assert body["image"] == {"width": 64, "height": 32, "mode": "RGB"}


def test_supports_text_and_csv_without_coordinates() -> None:
    api = client()
    text = api.post(
        "/tools/extract_text_and_layout",
        json={"image_base64": image_base64(), "output_format": "text"},
    ).json()
    assert text["content"] == "first\nsecond"

    csv_response = api.post(
        "/tools/extract_text_and_layout",
        json={
            "image_base64": image_base64(),
            "output_format": "csv",
            "include_coordinates": False,
        },
    ).json()
    assert csv_response["content"] == (
        "text,confidence,x,y,width,height\nfirst,0.9500,,,,\nsecond,1.0000,,,,\n"
    )
    assert csv_response["fragments"][0]["bounding_box"] is None


def test_rejects_invalid_and_oversized_images() -> None:
    api = client()
    blank = api.post("/tools/extract_text_and_layout", json={"image_base64": " "})
    assert blank.status_code == 422

    invalid = api.post("/tools/extract_text_and_layout", json={"image_base64": "not base64!"})
    assert invalid.status_code == 400

    invalid_data_url = api.post(
        "/tools/extract_text_and_layout",
        json={"image_base64": f"data:text/plain;base64,{image_base64()}"},
    )
    assert invalid_data_url.status_code == 400

    not_image = api.post(
        "/tools/extract_text_and_layout",
        json={"image_base64": base64.b64encode(b"not an image").decode()},
    )
    assert not_image.status_code == 400

    too_large = client(max_image_bytes=1).post(
        "/tools/extract_text_and_layout", json={"image_base64": image_base64()}
    )
    assert too_large.status_code == 413

    too_many_pixels = client(max_image_pixels=1).post(
        "/tools/extract_text_and_layout", json={"image_base64": image_base64()}
    )
    assert too_many_pixels.status_code == 413


def test_reports_missing_ml_runtime() -> None:
    class MissingEngine:
        def extract(self, image: Image.Image, language: str) -> list[OcrFragment]:
            raise OcrUnavailableError("PaddleOCR is unavailable")

    api = TestClient(create_app(Settings(), MissingEngine()))
    response = api.post("/tools/extract_text_and_layout", json={"image_base64": image_base64()})
    assert response.status_code == 503
    assert response.json() == {"detail": "PaddleOCR is unavailable"}
