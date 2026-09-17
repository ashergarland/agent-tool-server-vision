# Troubleshooting

## Application fails before MCP starts

- `VISION_ALLOWED_ROOTS` is required and every entry must be absolute.
- Set `VISION_PYTHON_PATH` to an absolute Python 3 executable if `python3`/`python` is not on an
  absolute `PATH` entry.
- `VISION_WORKER_TIMEOUT_MS` must exceed `VISION_OPERATION_TIMEOUT_SECONDS` by at least one second.
- Azure/auto mode requires an HTTPS Content Understanding endpoint and explicit service-principal
  identity. The declared hybrid profile uses `azure`; custom `auto` embeddings must install both
  provider extras.

Configuration failures are intentional fail-closed behavior.

## Readiness is not ready

Readiness requires valid roots, Pillow, NumPy, Pydantic, filesystem storage, and the selected OCR
provider. Install the source/package with its core dependencies, then add `[ml]` for local raster
OCR or `[azure]` for managed OCR.

Azure readiness is expected to be `degraded`: configuration is present but credentials and provider
quota are not exercised by a public readiness probe.

## SVG analysis fails

`analyze_image` accepts local SVG only. The file must have bounded numeric width/height (or a
four-number viewBox), remain under the byte/pixel/node/text limits, and contain no DOCTYPE or ENTITY
declaration. Only text elements are interpreted; shape-only or open-ended visual semantics return a
recorded text-only/native-vision fallback. External stylesheet processing instructions are rejected.
When an embedded stylesheet could control visibility, source text is not treated as visual evidence
and the same explicit fallback is returned.

## Raster OCR is slow

Paddle model weights load lazily and a one-shot worker does not retain an engine between calls.
Model/cache files use the private application temporary home and can be reused while the TypeScript
application remains alive. For latency-sensitive local use, keep inputs small and select only
needed languages. Do not remove concurrency or timeout limits to hide cold starts.

## Busy, timeout, or upstream errors

- `busy`: active plus queued work reached configured capacity, or shutdown cancelled the call.
- `timeout`: the provider, Python operation, Platform request, or bounded process exceeded its
  budget.
- `upstream_error`: worker output was malformed/out of contract, the process output ceiling was
  reached, or a provider failed.

These failures are deterministic and do not include stderr, stack traces, paths, or provider
payloads. Retry only errors marked retryable.

## No hosted URL

No hosted profile is declared. Use the stdio package locally. An operator embedding Platform HTTP
must separately supply authentication, workload authorization, provider cost controls, deployment
evidence, and monitoring before exposing it.
