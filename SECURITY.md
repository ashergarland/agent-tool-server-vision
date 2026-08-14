# Security

Report vulnerabilities privately through GitHub Security Advisories for this repository. Do not
open a public issue for an undisclosed vulnerability.

Deployments must enable authentication, store credentials in a secret manager, use least-privilege
provider roles, restrict `VISION_ALLOWED_ROOTS` to the smallest possible set, keep asset TTL and quotas bounded, and
review dependency and container findings before release.

## Known scanner finding

CodeQL reports `py/weak-sensitive-data-hashing` for the API-key digest in
`src/vision_server/security.py`. This is a false positive for this design: API keys are
machine-generated, high-entropy random tokens rather than user-chosen passwords, so they are not
subject to offline guessing, and a keyed HMAC-SHA256 digest is used deliberately to keep
per-request authentication constant-time and fast. If a deployment ever accepts human-chosen
secrets, replace the digest with a memory-hard password hash.
