"""Authentication helpers: constant-time digests and opaque principal IDs."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_PRINCIPAL_PREFIX = "p_"


def digest_secret(secret: str) -> str:
    """Return a hex digest of a shared secret; raw secrets are never stored."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


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
    digest_hex = hashlib.sha256(("principal:" + digest).encode("utf-8")).hexdigest()
    return _PRINCIPAL_PREFIX + digest_hex[:32]


ANONYMOUS_PRINCIPAL = _PRINCIPAL_PREFIX + "local-development"


def new_token(byte_length: int = 24) -> str:
    """Unguessable identifier used for assets and generated file names."""
    return secrets.token_urlsafe(byte_length)


def principal_bucket(principal: str) -> str:
    """Stable, non-reversible directory or blob prefix for a principal."""
    return hashlib.sha256(principal.encode("utf-8")).hexdigest()[:32]
