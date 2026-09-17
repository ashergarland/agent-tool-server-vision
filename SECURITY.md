# Security

Report vulnerabilities privately through GitHub Security Advisories for this repository. Do not
open a public issue for an undisclosed vulnerability.

## Supported security posture

The normal supported path is a local stdio process owned by the invoking user. It binds no network
listener, accepts only explicitly allowed local image roots, and applies fixed byte, pixel,
language, queue, concurrency, time, stdout, and stderr limits.

An expensive OCR or visual-analysis endpoint must not be exposed publicly without authentication,
pre-auth and authenticated rate limits, workload authorization, provider cost controls, request
deadlines, monitoring, and an operator-owned deployment contract. Agent Tool Platform supplies
generic HTTP authentication, rate limiting, body limits, cancellation, and lifecycle mechanics,
but this repository declares no hosted profile and does not claim that assembling HTTP alone is a
production-ready public service.

## Worker boundary

The TypeScript capability uses Platform's bounded process primitive:

- absolute pre-resolved Python executable;
- fixed `-B -m vision_server.worker` argv and no shell;
- complete environment built from an allow-list;
- private temporary working directory and temporary-home variables;
- bounded JSON stdin, stdout, and stderr;
- hard timeout, request cancellation, queue saturation, and deterministic termination.

Application credentials and unrelated parent variables are not inherited. Local mode receives no
provider credential. Hybrid mode forwards only selected Azure identity variables and Vision
configuration. The worker never returns or logs credential values, raw SDK errors, absolute
internal paths, or stack traces.

The published Platform 0.1.2 capability context predates the scratch-workspace lifecycle helper
present at Platform revision `98ec8162fb11d5c04aee9e6f7b3625a472a0180d`. Vision therefore owns one
small private worker directory and removes it after its Platform-bounded queue drains. It does not
implement a generic worker supervisor.

## Input policy

- Remote/base64/data/SAS/storage URL inputs are rejected by schema.
- Local paths must be absolute regular files beneath configured roots.
- Raster magic bytes, encoded bytes, decoded pixels, and decode failures are checked before work.
- SVG document types and entities are rejected; dimensions, nodes, text blocks, and text length are
  bounded.
- OCR languages use a fixed allow-list.
- Assets are opaque, principal-scoped, quota- and TTL-bounded.
- Image text is treated as untrusted evidence, not executable instructions.

Security CI runs the pinned Platform capability security workflow plus Python dependency audit and
Python CodeQL.
