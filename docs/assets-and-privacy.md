# Assets, data flow, and privacy

## Local development

1. The agent passes `{"kind": "local_path", "path": ...}`.
2. The path is resolved with `realpath` and must be a regular file beneath `VISION_ALLOWED_ROOTS`.
3. The file is opened with `O_NOFOLLOW | O_NONBLOCK`, size-checked from the open descriptor, magic
   validated, EXIF-normalized, and pixel-bounded before decoding.
4. Generated artifacts are written to the filesystem asset store with unpredictable names, mode
   0600, inside a per-principal directory. Inputs are never overwritten.

Nothing leaves the machine unless `VISION_PROVIDER_MODE` selects the managed provider.

## Hosted on Azure

1. Clients upload bytes to `POST /assets` and receive an opaque asset ID.
2. Inputs are stored in a private blob container; generated artifacts are stored in a separate
   private container. Both use unguessable, principal-scoped names and no public access.
3. Tool responses contain only opaque IDs, dimensions, byte counts, and metadata. Internal paths,
   container names, account URLs, and SAS URLs are never returned or logged.
4. All storage access uses `DefaultAzureCredential` (user-assigned managed identity). Shared key
   access is disabled on the storage account and no SAS is ever generated.
5. With `processingMode` `azure` or `auto`, image bytes are sent to the configured Azure AI Content
   Understanding account in your own tenant and region for OCR. No other service receives images.

## Retention

Assets expire after `VISION_ASSET_TTL_SECONDS`. Expired assets are rejected on read, purged in
process for the filesystem backend, and deleted in bulk by the blob lifecycle rule, which is
provisioned from the same TTL value. Quotas bound bytes and object count per principal.

## Isolation

Every asset operation is authorized against the calling principal using a constant-time comparison,
and cross-principal access returns `not_found` so existence is not disclosed.

## Logging

Only operational metadata is logged: request ID, tool name, duration, outcome, byte counts, and
provider. Image bytes, OCR text, credentials, and blob names are never logged by default.
