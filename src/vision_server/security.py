"""Authentication helpers: constant-time digests and opaque principal IDs.

API keys are machine-generated, high-entropy random tokens (see
``scripts/bootstrap/provision.sh``), never user-chosen passwords. They are
therefore not vulnerable to offline guessing, and a fast keyed digest is used so
that authentication adds no meaningful latency to every request. A slow password
hash such as PBKDF2 or Argon2 would be required only for low-entropy secrets.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_PRINCIPAL_PREFIX = "p_"
_KEY_DOMAIN = b"vision-server/api-key/v1"
_PRINCIPAL_DOMAIN = b"vision-server/principal/v1"
_BUCKET_DOMAIN = b"vision-server/bucket/v1"


def _keyed_digest(domain: bytes, value: str) -> str:
    return hmac.new(domain, value.encode("utf-8"), hashlib.sha256).hexdigest()


def digest_secret(api_key: str) -> str:
    """Return a keyed digest of an API key; raw keys are never stored."""
    return _keyed_digest(_KEY_DOMAIN, api_key)


def match_digest(candidate: str, digests: tuple[str, ...]) -> str | None:
    """Constant-time comparison of a presented key against configured digests."""
    presented = digest_secret(candidate)
    matched: str | None = None
    for digest in digests:
        if hmac.compare_digest(presented, digest):
            matched = digest
    return matched


def principal_from_digest(digest: str) -> str:
    """Derive a stable, opaque principal identifier from a key digest."""
    return _PRINCIPAL_PREFIX + _keyed_digest(_PRINCIPAL_DOMAIN, digest)[:32]


ANONYMOUS_PRINCIPAL = _PRINCIPAL_PREFIX + "local-development"


def new_token(byte_length: int = 24) -> str:
    """Unguessable identifier used for assets and generated file names."""
    return secrets.token_urlsafe(byte_length)


def principal_bucket(principal: str) -> str:
    """Stable, non-reversible directory or blob prefix for a principal."""
    return _keyed_digest(_BUCKET_DOMAIN, principal)[:32]
