import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

from PIL import Image

from vision_server.ocr import (
    PaddleOcrEngine,
    _parse_legacy_results,
    _parse_modern_results,
)


def test_parses_modern_paddle_results() -> None:
    result = {
        "res": {
            "rec_texts": ["bad", "hello"],
            "rec_scores": [None, 0.9],
            "rec_polys": [
                [[1, 2], [3, 2], [3, 4], [1, 4]],
                [[1, 2], [3, 2], [3, 4], [1, 4]],
            ],
        }
    }
    parsed = _parse_modern_results([result])
    assert parsed[0].text == "hello"
    parsed_from_json = _parse_modern_results([SimpleNamespace(json=lambda: result)])
    assert parsed_from_json[0].text == "hello"
    assert _parse_modern_results([{"unexpected": "shape"}]) == []
    assert _parse_modern_results([{"res": {"rec_texts": "invalid"}}]) == []


def test_parses_legacy_paddle_results() -> None:
    result = [
        [
            [[[1, 2], [3, 2], [3, 4], [1, 4]], ["bad", None]],
            [[[1, 2], [3, 2], [3, 4], [1, 4]], ["hello", 0.9]],
        ]
    ]
    parsed = _parse_legacy_results(result)
    assert parsed[0].confidence == 0.9
    assert _parse_legacy_results(None) == []
    assert _parse_legacy_results([["invalid"]]) == []


def test_paddle_adapter_loads_and_caches_engine(monkeypatch: Any) -> None:
    instances: list[Any] = []

    class FakePaddle:
        def __init__(self, **options: Any) -> None:
            self.options = options
            instances.append(self)

        def predict(self, image: Any) -> list[dict[str, Any]]:
            assert image.shape == (2, 2, 3)
            return [
                {
                    "rec_texts": ["cached"],
                    "rec_scores": [0.8],
                    "dt_polys": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
                }
            ]

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddle))
    adapter = PaddleOcrEngine()
    image = Image.new("RGB", (2, 2))
    assert adapter.extract(image, "en")[0].text == "cached"
    assert adapter.extract(image, "en")[0].text == "cached"
    assert len(instances) == 1
    assert instances[0].options["lang"] == "en"


def test_paddle_adapter_supports_legacy_api(monkeypatch: Any) -> None:
    class FakePaddle:
        def __init__(self, **options: Any) -> None:
            pass

        def ocr(self, image: Any) -> list[Any]:
            assert image.shape == (2, 2, 3)
            return [[[[[0, 0], [1, 0], [1, 1], [0, 1]], ["legacy", 0.7]]]]

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddle))
    parsed = PaddleOcrEngine().extract(Image.new("RGB", (2, 2)), "en")
    assert parsed[0].text == "legacy"


def test_paddle_adapter_engine_cache_is_thread_safe(monkeypatch: Any) -> None:
    instances: list[Any] = []

    class FakePaddle:
        def __init__(self, **options: Any) -> None:
            self.options = options
            instances.append(self)

        def predict(self, image: Any) -> list[dict[str, Any]]:
            return [
                {
                    "rec_texts": ["cached"],
                    "rec_scores": [0.8],
                    "dt_polys": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
                }
            ]

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddle))
    adapter = PaddleOcrEngine()
    image = Image.new("RGB", (2, 2))
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: adapter.extract(image, "en"), range(20)))
    assert len(instances) == 1
