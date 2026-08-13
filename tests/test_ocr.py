from vision_server.ocr import _parse_legacy_results, _parse_modern_results


def test_parses_modern_paddle_results() -> None:
    result = {
        "res": {
            "rec_texts": ["hello"],
            "rec_scores": [0.9],
            "rec_polys": [[[1, 2], [3, 2], [3, 4], [1, 4]]],
        }
    }
    assert _parse_modern_results([result])[0].text == "hello"
    assert _parse_modern_results([{"unexpected": "shape"}]) == []


def test_parses_legacy_paddle_results() -> None:
    result = [[[[[1, 2], [3, 2], [3, 4], [1, 4]], ["hello", 0.9]]]]
    parsed = _parse_legacy_results(result)
    assert parsed[0].confidence == 0.9
    assert _parse_legacy_results(None) == []
    assert _parse_legacy_results([["invalid"]]) == []
