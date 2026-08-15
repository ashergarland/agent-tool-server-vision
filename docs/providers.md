# OCR providers

OCR and layout are the only provider-backed capability. Everything else — comparison, cropping,
optimization, metadata, and normalization — runs locally and deterministically.

## Provider interface

`vision_server.providers.base.OcrProvider` is intentionally narrow:

```
analyze(image, language) -> OcrResult
health() -> (status, detail)
```

Both providers normalize into the same `OcrBlock` list (stable ID, type, text, page, optional
polygon and confidence). Raw provider responses are never returned to callers.

## Local provider: PaddleOCR

- Family: PP-OCRv5 mobile detection and recognition models, loaded lazily on first use.
- Package: `paddleocr==3.1.0` with the CPU `paddlepaddle` runtime, installed by the `ml` extra.
- License: Apache-2.0 (PaddleOCR and the published PP-OCR model weights).
- Provenance and revision are recorded in `MODEL_PROVENANCE` in
  `src/vision_server/providers/paddle.py` and reported as the `model` field of results.
- Languages are restricted to `VISION_PADDLE_LANGUAGES`; engines are cached in a bounded LRU.
- Weights are downloaded on first use at runtime only. CI and hosted startup never download models:
  readiness uses an import check and tests use fakes. The default container includes the `ml` extra
  so local mode works out of the box; `--build-arg EXTRAS=azure` produces a managed-only image.

## Managed provider: Azure AI Content Understanding

- Uses the GA read/layout capability: `POST {endpoint}/contentunderstanding/analyzers/{analyzer}:analyzeBinary?api-version=2025-11-01`,
  then polls the returned `Operation-Location` until the operation succeeds or fails.
- Authentication is Microsoft Entra only (`DefaultAzureCredential`, scope
  `https://cognitiveservices.azure.com/.default`). The account is deployed with `disableLocalAuth`,
  and the workload identity holds only the built-in **Cognitive Services User** role.
- The deprecated Azure Image Analysis 4.0 API is not used.
- Timeouts are bounded by `VISION_PROVIDER_TIMEOUT_SECONDS`; HTTP status codes map to typed errors:
  401/403 to `forbidden`, 429 to `quota_exceeded`, 4xx to `invalid_input`, 5xx and transport errors
  to retryable `provider_unavailable`.

## Routing and fallback

`VISION_PROVIDER_MODE` sets the default, and each call may override it with `processingMode`:

| `processingMode` | Behaviour                                                                    |
| ---------------- | ---------------------------------------------------------------------------- |
| `local`          | Local provider only. Never switches, never falls back.                       |
| `azure`          | Managed provider only. Never switches, never falls back.                     |
| `auto`           | Follows configuration; falls back only on typed retryable failures.          |

Fallback never happens for authentication, validation, quota-policy, or malformed-input failures.
When a fallback occurs the response sets `fallbackUsed` and adds a warning; provenance always
reports the provider that actually produced the result.

## Extension point: Azure Machine Learning

A self-hosted Azure ML provider is a documented extension point only. To add one, implement
`OcrProvider`, register it in `OcrRouter`, and extend the Bicep with the required workspace,
endpoint, and RBAC. No Azure ML resource is provisioned, called, or billed in this phase.
