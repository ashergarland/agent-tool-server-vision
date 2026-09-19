# Agent Tool Server Vision

Vision is a thin Agent Tool Platform capability backed by a capability-owned Python image and OCR
worker:

```text
agent host
   |
   | MCP stdio (or a Platform-assembled authenticated HTTP application)
   v
TypeScript capability
   - Platform runtime, lifecycle, auth, rate limits, cancellation, MCP, HTTP, OpenAPI
   - bounded tool schemas and routing
   - bounded queue and safe process invocation
   |
   | one bounded JSON request / one bounded JSON response
   v
Python worker
   - image validation and normalization
   - OCR provider routing and fallback
   - deterministic comparison and optimization
   - structured visual evidence extraction
```

The migration follows the non-TypeScript worker seam documented by the D4 template at
`4b5a5d93c99614a6ca64d65e10f56918c45f1472`. It uses Agent Tool Platform runtime/application and
process mechanics from the Platform line represented by
`98ec8162fb11d5c04aee9e6f7b3625a472a0180d`. Python remains the domain implementation; image and OCR
behavior was not rewritten in TypeScript.

## Tools

| Tool                      | Purpose                                                                                                                          |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `analyze_image`           | Extract bounded status, identity, environment, listener, ingress, and readiness facts with evidence, confidence, and provenance. |
| `extract_text_and_layout` | Return OCR text, reading order, confidence, and normalized coordinates.                                                          |
| `compare_images`          | Return deterministic similarity, changed pixels, and changed regions.                                                            |
| `optimize_image_region`   | Crop, downscale, and compress a known region into an ephemeral worker artifact.                                                  |

The TypeScript catalogue in `src/tools/definitions.ts` is the MCP-facing contract. Python validates
the worker request again and normalizes domain failures into a stable worker envelope. Platform
then projects the result or error consistently over MCP and HTTP.

## Python worker prerequisites

Node.js 22 and Python 3.11 through 3.13 are supported. The upper bound reflects the published
PaddlePaddle wheels used by the optional local OCR extra.

From a source checkout:

```bash
npm ci
python -m pip install -e .
```

The core install supports SVG analysis, raster decoding, deterministic comparison, region
optimization, fake-provider tests, and managed-provider integration. Local raster OCR additionally
needs PaddleOCR model dependencies:

```bash
python -m pip install -e '.[ml]'
```

Azure Content Understanding needs:

```bash
python -m pip install -e '.[azure]'
```

The npm package includes `pyproject.toml` and `src/vision_server`. After installing the npm package,
the same Python extras can be installed from its package directory. The wrapper sets `PYTHONPATH`
to the packaged source; it never installs dependencies or downloads model weights at runtime.

## Run the capability

Set at least one absolute allowed image root, build, and launch stdio:

```bash
export VISION_ALLOWED_ROOTS=/absolute/path/to/images
npm run build
npm run mcp:stdio
```

On Windows, separate multiple roots with semicolons; on POSIX use colons. Commas work on both.
`VISION_PYTHON_PATH` may name an explicit absolute Python executable. The installed executable is
`agent-tool-vision`.

The stdio path is host-neutral and binds no network listener. Platform forces local stdio
authentication semantics independently of inherited hosted environment variables.

## Image input boundary

Inputs are discriminated references:

```json
{ "kind": "local_path", "path": "/absolute/allowed/screenshot.png" }
{ "kind": "asset", "assetId": "aOpaqueWorkerArtifact" }
```

Remote URLs, storage URLs, SAS URLs, data URLs, base64 payloads, and bare strings are rejected.
Local paths must be absolute, resolve beneath `VISION_ALLOWED_ROOTS`, and name regular files.
Raster inputs are bounded by encoded bytes and decoded pixels, checked by magic bytes, and limited
to PNG, JPEG, or WebP. `analyze_image` also accepts bounded local SVG files; document type/entity
declarations are rejected, XML structure and extracted text are bounded, and facts are promoted
only for the supported rendering subset: solid opaque backgrounds, supported text paint and
geometry, and no later opaque overlap. Unsupported or ambiguous rendering is omitted, lowers
retained confidence where it could affect paint order, and sets bounded fallback metadata.

The wrapper accepts at most two active workers and eight queued calls by default. Platform process
execution uses an absolute Python executable, fixed argv, no shell, an explicit environment, a
private Platform-owned scratch workspace, wall-clock and stdout/stderr ceilings, cancellation, and
deterministic termination. Vision selects the worker-local asset and cache paths; Platform owns
workspace creation, confinement, and application-shutdown cleanup. See `docs/configuration.md` for
every bound.

## Benchmark visual proof

`tests/fixtures/portal-snapshot.svg` is the Level 2 `portal-snapshot.svg` fixture. The integration
test drives the real TypeScript-to-Python round trip and establishes:

- revision identity `checkout-api--pr-1842`;
- degraded revision state and zero-ready-replica evidence;
- ingress and TCP readiness target port `8080`;
- process listener port `3000`;
- repeated readiness connection refusal;
- `APP_PORT=8080` and `NODE_ENV=production`.

Facts are derived from generic label, key/value, listener, port, readiness, and status patterns.
Production code contains no fixture filename or expected fixture value. Embedded SVG text carries
block provenance and deterministic confidence. Raster results carry OCR block/provider provenance.

If no supported fact pattern is recognized, `analyze_image` returns bounded `extractedText`, sets
`fallbackUsed`, records `fallbackReason`, and warns the caller to use native vision for unsupported
semantics. Retryable managed-provider fallback is also explicit; authentication, quota, validation,
and malformed-provider failures never fall back.

## Document Optimizer seam

`analyze_image` is the figure-analysis boundary for a future Document Optimizer handoff. It accepts
one extracted image/figure reference and returns visual facts and evidence. Vision does not open,
split, parse, retrieve, summarize, or optimize surrounding documents. Document parsing and figure
extraction remain the document capability's responsibility.

## Profiles

`capability-profiles.json` uses exact D4 deployment contract v1 dimensions and declares:

- `local-package`: local/package/local-process/filesystem/no external provider/mutating;
- `hybrid-azure-package`: local/package/local-process/filesystem/external provider/mutating.

`analyze_image` and `extract_text_and_layout` are read-only. `compare_images` is conservatively
gated because `includeDiff` can create an artifact, and `optimize_image_region` always creates one.
Set Platform `MUTATIONS_ENABLED=true` for those tools and retain confirmation when appropriate;
neither tool modifies a source image, and artifacts remain principal-scoped until cleanup.

There is no hosted or container profile. Although Platform can assemble authenticated HTTP for an
embedding application, this repository does not claim production-ready public ingress, workload
authorization, provider identity, or operational controls for a hosted deployment. See
`docs/deployment.md`.

## Validation

```bash
npm ci
npm run format:check
npm run lint
npm run typecheck
npm run test:coverage
npm run build
npm run openapi:emit
npm run metadata:validate
npm run package:smoke

ruff check .
ruff format --check .
mypy
pytest
```

Deployment contract validation uses a built checkout of the exact Platform revision:

```bash
AGENT_TOOL_PLATFORM_CHECKOUT=/path/to/platform-at-98ec816 npm run deployment:validate
AGENT_TOOL_PLATFORM_CHECKOUT=/path/to/platform-at-98ec816 npm run deployment:conformance
```

The package smoke packs and installs the real npm artifact outside the repository, imports its
public API, invokes the installed stdio executable through the packaged Python worker, checks for
non-protocol stdout, and verifies clean shutdown. Nothing is published.

## License

MIT
