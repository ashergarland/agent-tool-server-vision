# Configuration and limits

Agent Tool Platform owns service identity, HTTP/MCP transport, authentication, request body limits,
rate limits, request cancellation, shutdown, logging, and mutation gates. Vision contributes only
the capability and worker variables below. Blank variables are treated as unset and invalid values
fail application assembly.

## Files and image bounds

| Variable                   | Default                | Bound and behavior                                                                                         |
| -------------------------- | ---------------------- | ---------------------------------------------------------------------------------------------------------- |
| `VISION_ALLOWED_ROOTS`     | required               | Absolute roots separated by the host path separator or commas. Empty or invalid roots make readiness fail. |
| `VISION_PYTHON_PATH`       | PATH resolution        | Optional absolute executable path; relative overrides are rejected.                                        |
| `VISION_MAX_IMAGE_BYTES`   | `10485760`             | `1024` to `67108864`; enforced on the opened file before decode/parse.                                     |
| `VISION_MAX_IMAGE_PIXELS`  | `40000000`             | `1024` to `200000000`; enforced before raster decode and against SVG dimensions.                           |
| `VISION_ASSET_ROOT`        | private worker scratch | Optional absolute persistent filesystem artifact root.                                                     |
| `VISION_ASSET_TTL_SECONDS` | `3600`                 | `60` to seven days.                                                                                        |
| `VISION_ASSET_MAX_BYTES`   | `10485760`             | `1024` to `67108864`.                                                                                      |
| `VISION_ASSET_QUOTA_BYTES` | `268435456`            | Per-principal byte ceiling.                                                                                |
| `VISION_ASSET_QUOTA_COUNT` | `200`                  | `1` to `10000` objects per principal.                                                                      |

The D4 package profiles use filesystem assets. Legacy `azure_blob` storage remains a Python domain
adapter for embedding compatibility but is not part of either declared package profile.

## OCR policy

| Variable                                         | Default                     | Bound and behavior                                                 |
| ------------------------------------------------ | --------------------------- | ------------------------------------------------------------------ |
| `VISION_PROVIDER_MODE`                           | `local`                     | `local`, `azure`, or `auto`; Azure/auto require an HTTPS endpoint. |
| `VISION_DEFAULT_LANGUAGE`                        | `en`                        | One of `en`, `ch`, `fr`, `german`, `japan`, `korean`.              |
| `VISION_PADDLE_LANGUAGES`                        | `en`                        | Comma-separated non-empty subset of the same allow-list.           |
| `VISION_PADDLE_CACHE_SIZE`                       | `2`                         | `1` to `8` lazy local engines within one worker process.           |
| `VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT`    | unset                       | HTTPS only; required by Azure/auto mode.                           |
| `VISION_AZURE_CONTENT_UNDERSTANDING_API_VERSION` | `2025-11-01`                | At most 40 characters.                                             |
| `VISION_AZURE_CONTENT_UNDERSTANDING_ANALYZER`    | `prebuilt-documentAnalyzer` | At most 120 characters.                                            |

## Work, time, and output bounds

| Variable                           | Default   | Bound and behavior                                                                        |
| ---------------------------------- | --------- | ----------------------------------------------------------------------------------------- |
| `VISION_MAX_CONCURRENCY`           | `2`       | `1` to `16` worker processes.                                                             |
| `VISION_MAX_QUEUE_DEPTH`           | `8`       | `0` to `128`; overflow is a retryable Platform `busy` error.                              |
| `VISION_OPERATION_TIMEOUT_SECONDS` | `60`      | `0.1` to `600`; Python domain operation ceiling.                                          |
| `VISION_PROVIDER_TIMEOUT_SECONDS`  | `30`      | `0.1` to `600` and no greater than operation timeout.                                     |
| `VISION_WORKER_TIMEOUT_MS`         | `65000`   | `1000` to `610000`, at least 1000 ms longer than the Python operation timeout.            |
| `VISION_WORKER_MAX_OUTPUT_BYTES`   | `2097152` | `65536` to `8388608`; Python truncates bulk result fields before Platform's hard ceiling. |
| `VISION_SHUTDOWN_GRACE_SECONDS`    | `10`      | `0` to `120`; Python queue defense in depth.                                              |

Worker protocol input is independently limited to 65536 bytes and stderr to 8192 bytes. MCP tool
schemas also bound every path, list, string, region, text block, and fact.

For an embedded HTTP application, keep Platform `BODY_LIMIT_BYTES`, `RATE_LIMIT_MAX`,
`PRE_AUTH_RATE_LIMIT_MAX`, and `REQUEST_TIMEOUT_MS` bounded. A hosted operator must additionally
authorize workload roots/uploads and provider spend; no hosted profile is declared here.

## Hybrid identity variables

The declared hybrid profile fixes `VISION_PROVIDER_MODE=azure` and uses `AZURE_TENANT_ID`,
`AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET`. All three are required in `azure` and `auto` modes,
and the last value must come from an operator secret manager. They are forwarded only for those
modes. `auto` is an embedding-only mode that also requires the `ml` extra for local fallback.
Unrelated environment variables and Azure credentials present during local-mode startup are never
inherited by the worker.
