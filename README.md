# agent-tool-server-vision

A local FastAPI service that converts images into compact text and layout metadata for AI agents.
The first implemented tool, `extract_text_and_layout`, uses PaddleOCR and returns Markdown, plain
text, or CSV together with normalized text fragments and bounding boxes.

## API

| Method | Path                             | Purpose                                  |
| ------ | -------------------------------- | ---------------------------------------- |
| `GET`  | `/health`                        | Liveness and service metadata            |
| `GET`  | `/openapi.json`                  | OpenAPI 3.1 document                     |
| `POST` | `/tools/extract_text_and_layout` | Extract text and coordinates from images |

The tool accepts JSON containing a base64-encoded image:

```json
{
  "image_base64": "iVBORw0KGgo...",
  "output_format": "markdown",
  "language": "en",
  "include_coordinates": true
}
```

`image_base64` may also be an `image/*` data URL. The image is decoded only in memory and is never
persisted. Images larger than 10 MiB or 25 megapixels are rejected by default.

## Local development

Python 3.11 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev,ml]'
uvicorn vision_server.main:app --host 0.0.0.0 --port 8080
```

The `ml` extra installs PaddleOCR and its CPU PaddlePaddle runtime. Tests use a fake OCR engine and
therefore only require `pip install -e '.[dev]'`.

## Configuration

Settings use the `VISION_` prefix:

| Variable                  | Default                    |
| ------------------------- | -------------------------- |
| `VISION_SERVICE_NAME`     | `agent-tool-server-vision` |
| `VISION_SERVICE_VERSION`  | `0.1.0`                    |
| `VISION_MAX_IMAGE_BYTES`  | `10485760`                 |
| `VISION_MAX_IMAGE_PIXELS` | `25000000`                 |
| `VISION_DEFAULT_LANGUAGE` | `en`                       |

## Validation

```bash
ruff check .
ruff format --check .
mypy
pytest
docker build -t agent-tool-server-vision .
```

## License

MIT

