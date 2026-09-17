# Assets, data flow, and privacy

## Local file flow

1. The agent supplies a discriminated local path.
2. Python resolves it to a regular file beneath `VISION_ALLOWED_ROOTS`.
3. The opened handle is byte-bounded. Raster magic/pixels or SVG structure/dimensions are checked.
4. The worker performs local analysis or sends normalized image bytes only to the explicitly
   selected OCR provider.
5. The worker returns one bounded JSON envelope; TypeScript validates it against the public schema.

Nothing leaves the machine in the local profile. Source images are never modified.

## Ephemeral artifacts

`compare_images` can create a diff artifact and `optimize_image_region` creates a crop artifact.
They are stored under an opaque, unpredictable, principal-scoped identifier. The wrapper hashes the
Platform principal before it crosses into filesystem naming. Cross-principal access returns
not-found, assets have byte/count quotas and TTLs, and the default store lives in the private worker
directory removed at application shutdown.

The D4 package exposes no unauthenticated upload route. `asset` references are for artifacts created
within the same capability/application scope or for an explicitly configured embedding store.

## External provider flow

Only the hybrid profile can send raster image bytes to Azure Content Understanding. The provider
account, identity, region, retention, and cost policy are operator responsibilities. Vision returns
normalized text blocks and provenance, never raw provider payloads.

## Logging and protocol output

The stdio process uses Platform's silent logger because stdout is reserved for MCP. The Python
worker writes exactly one JSON response to stdout and no routine stderr. Neither layer logs image
bytes, OCR text, facts, paths, artifact names, provider endpoints, or credentials. Platform
telemetry may record bounded tool name, outcome, duration, and error code only when an embedding
application opts in.
