# OCR providers

Python owns OCR and provider normalization. TypeScript owns only the bounded public schema, safe
invocation, and error translation. Comparison, cropping, SVG text analysis, and fact extraction are
local; only raster OCR can call an external provider.

## Local provider: PaddleOCR

- PP-OCRv5 mobile detection and recognition models.
- `paddleocr==3.7.0` and `paddlepaddle==3.3.1`, installed by the `ml` extra.
- Apache-2.0 upstream license.
- Models load lazily on first raster OCR call for an allow-listed language.
- The in-process engine LRU is bounded by `VISION_PADDLE_CACHE_SIZE`.
- Readiness checks import availability but does not download weights or create an engine.

The D4 adapter invokes one Python process per call because Platform supplies bounded one-shot
process execution but no published generic long-lived worker supervisor. This favors deterministic
cleanup and isolation over retaining a model engine between calls. The private temporary home is
shared for the TypeScript application lifetime, so provider caches may reuse files without escaping
the lifecycle boundary.

## Managed provider: Azure AI Content Understanding

The managed adapter calls the configured `analyzeBinary` operation, polls its operation URL within
the provider timeout, and normalizes content into the same bounded block schema. It uses
`DefaultAzureCredential`; raw service responses, endpoints, tokens, and SDK errors never reach the
capability result.

HTTP/provider failures map deterministically:

- 401/403: non-retryable `forbidden`;
- 429: non-retryable quota failure;
- invalid input/media: non-retryable input failure;
- timeout, 408, 5xx, and transport failure: retryable unavailable/timeout;
- malformed or unknown provider response: non-retryable provider failure.

Readiness validates configuration but does not acquire a token or spend provider quota. It reports
the external provider boundary as `degraded`, not live-verified. The first real tool result is the
authoritative provider proof.

## Worker credential boundary

`buildChildEnvironment` constructs a complete environment from scratch. It includes:

- the resolved Python directory on `PATH`;
- private temporary home/directory variables;
- bounded `VISION_*` settings;
- `PYTHONPATH` pointing to the packaged worker;
- only explicitly selected Azure identity variables in hybrid mode.

Proxy variables, `NODE_OPTIONS`, Platform API keys, agent-host credentials, and unrelated
application secrets do not cross. Credential values are never included in readiness, errors,
stdout, stderr, or logs.

## Routing and fallback

Forced `local` and `azure` calls never switch providers. `auto` may fall back from managed OCR to
local OCR only after a typed retryable timeout or unavailable failure. Authentication, quota,
validation, and malformed-response failures never fall back.

Results report the provider and model that actually produced evidence. `fallbackUsed`, warnings,
and `fallbackReason` record fallback. For SVG, embedded text extraction is deterministic, local, and
provider-free regardless of OCR mode.
