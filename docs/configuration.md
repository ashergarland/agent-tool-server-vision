# Configuration and limits

Every setting is environment driven with the `VISION_` prefix. No account, tenant, region, or
owner-specific default is committed to this repository. Required production settings are validated
at startup and the process fails fast when they are invalid.

## Service and authentication

| Variable                  | Default                    | Notes                                              |
| ------------------------- | -------------------------- | -------------------------------------------------- |
| `VISION_SERVICE_NAME`     | `agent-tool-server-vision` | Reported by `/health` and MCP `initialize`.        |
| `VISION_SERVICE_VERSION`  | package version            |                                                    |
| `VISION_ENVIRONMENT`      | `development`              | `production` requires auth and at least one key.   |
| `VISION_AUTH_ENABLED`     | `true`                     | May only be disabled outside production.           |
| `VISION_API_KEYS`         | empty                      | Comma separated. Compared by constant-time digest. |
| `VISION_LOG_LEVEL`        | `INFO`                     |                                                    |
| `VISION_LOG_PAYLOAD_METADATA` | `false`                | Never logs image bytes or OCR text.                |

HTTP transports accept `Authorization: Bearer <key>` or `X-API-Key: <key>`. Each key maps to a
stable, opaque principal that scopes asset access.

## Input limits

| Variable                  | Default      | Notes                                                        |
| ------------------------- | ------------ | ------------------------------------------------------------ |
| `VISION_ALLOWED_ROOTS`    | empty        | Comma or colon separated roots. Empty rejects all local paths.|
| `VISION_MAX_IMAGE_BYTES`  | `10485760`   | Enforced before decoding.                                     |
| `VISION_MAX_IMAGE_PIXELS` | `40000000`   | Decoded pixel budget, checked before full decode.             |
| `VISION_MAX_JSON_BYTES`   | `1000000`    | Request bodies above this are rejected with `payload_too_large`. |

## Providers

| Variable                                        | Default                      | Notes                                |
| ----------------------------------------------- | ---------------------------- | ------------------------------------ |
| `VISION_PROVIDER_MODE`                          | `local`                      | `local`, `azure`, or `auto`.         |
| `VISION_DEFAULT_LANGUAGE`                       | `en`                         | Must be in the allow list.           |
| `VISION_PADDLE_LANGUAGES`                       | `en`                         | Allow list for the local provider.   |
| `VISION_PADDLE_CACHE_SIZE`                      | `2`                          | Bounded LRU of loaded engines.       |
| `VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT`   | empty                        | HTTPS only; required for azure/auto. |
| `VISION_AZURE_CONTENT_UNDERSTANDING_API_VERSION`| `2025-11-01`                 | Current GA API version.              |
| `VISION_AZURE_CONTENT_UNDERSTANDING_ANALYZER`   | `prebuilt-documentAnalyzer`  | Read/layout analyzer.                |

## Assets

| Variable                        | Default        | Notes                                             |
| ------------------------------- | -------------- | ------------------------------------------------- |
| `VISION_STORAGE_BACKEND`        | `filesystem`   | `filesystem` or `azure_blob`.                     |
| `VISION_ASSET_ROOT`             | `./.vision-assets` | Filesystem backend root, created with mode 0700. |
| `VISION_STORAGE_ACCOUNT_URL`    | empty          | HTTPS blob endpoint; required for `azure_blob`.   |
| `VISION_ASSET_CONTAINER`        | empty          | Private container for uploaded inputs.            |
| `VISION_ARTIFACT_CONTAINER`     | empty          | Private container for generated artifacts.        |
| `VISION_ASSET_TTL_SECONDS`      | `3600`         | Must match the blob lifecycle deletion rule.      |
| `VISION_ASSET_MAX_BYTES`        | `10485760`     | Per asset.                                        |
| `VISION_ASSET_QUOTA_BYTES`      | `268435456`    | Per principal.                                    |
| `VISION_ASSET_QUOTA_COUNT`      | `200`          | Per principal.                                    |

## Concurrency, timeouts, and shutdown

| Variable                            | Default | Notes                                                  |
| ----------------------------------- | ------- | ------------------------------------------------------ |
| `VISION_MAX_CONCURRENCY`            | `4`     | Concurrent tool executions per replica.                |
| `VISION_MAX_QUEUE_DEPTH`            | `32`    | Excess work is rejected with retryable `busy`.         |
| `VISION_OPERATION_TIMEOUT_SECONDS`  | `60`    | Per tool call.                                         |
| `VISION_PROVIDER_TIMEOUT_SECONDS`   | `30`    | Per OCR provider attempt.                              |
| `VISION_SHUTDOWN_GRACE_SECONDS`     | `15`    | In-flight work drains on SIGTERM before exit.          |

## Health and readiness

`/health` is liveness only. `/ready` reports the registry, storage, configuration summary, and
per-provider capability status. Optional providers that are unavailable are reported but do not fail
readiness, and no model weights are loaded to answer a readiness probe.

## Error model

Every failure returns one shape:

```json
{ "error": { "code": "invalid_input", "message": "...", "retryable": false,
             "details": {}, "requestId": "..." } }
```

Only `busy`, `timeout`, and `provider_unavailable` are retryable. Stacks, filesystem paths, blob
names, secrets, signed URLs, and raw SDK errors are never exposed.
