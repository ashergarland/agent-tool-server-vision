# Deployment profiles

The canonical declaration is [`../capability-profiles.json`](../capability-profiles.json). It uses
Agent Tool Platform deployment contract v1 at revision
`98ec8162fb11d5c04aee9e6f7b3625a472a0180d`.

## Local package

`local-package` is the normal hackathon and desktop path. An agent host launches the npm package
over stdio. The TypeScript process binds no listener and starts one bounded Python process per tool
call. Images remain on the local filesystem beneath `VISION_ALLOWED_ROOTS`. The profile needs
Python 3.11 through 3.13, the core Python dependencies, and the `ml` extra when raster OCR is
required.

This profile has no external provider, provider credential, secret, container, or infrastructure
requirement. Embedded text in bounded SVG figures is analyzed without model weights or network
access.

## Hybrid Azure package

`hybrid-azure-package` keeps the TypeScript capability and Python worker local but permits OCR bytes
to reach an explicitly configured Azure AI Content Understanding endpoint. The supported declared
form uses `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET`; the wrapper forwards only
that provider credential set and selected Vision settings to the child. This profile fixes
`VISION_PROVIDER_MODE=azure`; `auto` is available only to custom embeddings that install both the
`azure` and `ml` extras and own readiness for both providers.

Readiness confirms worker dependencies and configuration. It deliberately reports `degraded` for
the managed provider because readiness does not spend provider quota or claim a live credential
check. A real tool invocation remains the authoritative provider proof.

## No hosted profile

This revision declares no hosted, HTTP-service, container, or public-ingress profile. Agent Tool
Platform can assemble an authenticated HTTP application for embedding, but this repository does not
provide the workload authorization, provider identity, storage, deployment assets, or operational
evidence required to call that production-ready. The removed legacy Container Apps posture does not
describe the new wrapper/worker package and is not carried forward.
