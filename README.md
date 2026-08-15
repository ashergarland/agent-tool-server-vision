# agent-tool-server-vision

A hybrid vision tool server for AI agents. It exposes exactly three token-saving image tools over
three transports, all generated from a single tool registry:

| Tool                       | Purpose                                                                    |
| -------------------------- | -------------------------------------------------------------------------- |
| `extract_text_and_layout`  | OCR and reading order as markdown, text, or CSV plus normalized text blocks |
| `compare_images`           | Deterministic before/after similarity, changed pixels, and changed regions  |
| `optimize_image_region`    | Crop, downscale, and compress a known region into an opaque artifact        |

Transports:

| Transport                      | Entry point                                            |
| ------------------------------ | ------------------------------------------------------ |
| stdio MCP                      | `vision-server-stdio` or `python -m vision_server`      |
| Streamable HTTP MCP (stateless)| `POST /mcp/` on the FastAPI app                         |
| HTTP / OpenAPI 3.1             | `POST /tools/{tool}` plus `/health`, `/ready`, `/assets`|

`src/vision_server/registry.py` is the only place tool names, routing descriptions, annotations,
schemas, and handlers are defined; every transport and the OpenAPI document are derived from it, and
`tests/test_mcp_transport.py` asserts transport parity.

## Image inputs

Tools accept a discriminated image reference only:

```json
{ "kind": "local_path", "path": "/allowed/root/screenshot.png" }
{ "kind": "asset", "assetId": "iA1b2C3..." }
```

Base64 payloads, data URLs, remote URLs, storage URLs, SAS URLs, and bare strings are rejected.
Local paths are resolved with `realpath`, must be regular files beneath `VISION_ALLOWED_ROOTS`,
are opened with `O_NOFOLLOW`, are validated by magic bytes (PNG, JPEG, WebP), are EXIF-normalized,
and are bounded by byte and decoded-pixel limits before any processing. Hosted responses return
opaque asset and artifact IDs only; internal paths, container names, and storage URLs are never
returned.

Assets are uploaded and downloaded through `/assets`. They are principal-scoped, unguessable,
size- and quota-bounded, and expire after `VISION_ASSET_TTL_SECONDS`.

## Quick start (local, no Azure)

Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'          # add ,ml for the local PaddleOCR provider
export VISION_ALLOWED_ROOTS="$PWD/samples"
export VISION_AUTH_ENABLED=false  # development only; production fails closed
uvicorn vision_server.main:app --port 8080
```

stdio MCP for an agent host:

```bash
VISION_ALLOWED_ROOTS=/path/to/images vision-server-stdio
```

Local-only mode needs no Azure account, no network access, and no credentials.

## Agent routing

The MCP server advertises concise instructions:

1. When the goal is text or layout, run OCR first instead of sending the image to native vision.
2. For before/after questions, compare the two images first and use the returned regions.
3. When the interesting coordinates are already known, optimize that region before inspecting it.
4. Use native LLM vision only when these tools cannot answer the question.

Every tool description states when to call it, when not to, its input constraints, whether it is
deterministic or provider-backed, and how it saves image tokens. `tests/test_routing.py` contains
table-driven positive and negative routing evaluations.

## Documentation

- [`docs/configuration.md`](docs/configuration.md) — every setting, limit, and default
- [`docs/providers.md`](docs/providers.md) — hybrid OCR, model provenance, licenses, fallback policy
- [`docs/assets-and-privacy.md`](docs/assets-and-privacy.md) — data flow, retention, logging
- [`docs/deployment.md`](docs/deployment.md) — portable Azure deployment and per-fork OIDC setup
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — common failures and deferred capabilities
- [`docs/openapi.json`](docs/openapi.json) — generated OpenAPI 3.1 snapshot

## Validation

```bash
ruff check .
ruff format --check .
mypy
pytest                                   # fakes only: no Azure, network, or model weights
python scripts/check_openapi.py
docker build -t agent-tool-server-vision .
az bicep build --file infra/main.bicep && az bicep lint --file infra/main.bicep
```

## Not implemented (deliberately)

UI mapping, diagram parsing, object detection, visual question answering, and text-region grounding
are out of scope for this phase, and the deprecated Azure Image Analysis 4.0 API is not used. An
Azure ML provider extension point is documented in `src/vision_server/providers/__init__.py` but is
not implemented or provisioned.

## License

MIT
