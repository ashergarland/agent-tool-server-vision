"""Authentication helpers: hardened digests and opaque principal IDs."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_PRINCIPAL_PREFIX = "p_"
_KEY_SALT = b"vision-server/api-key/v2"
_KEY_ITERATIONS = 600_000


def digest_secret(api_key: str) -> str:
    """Return a deterministic password KDF digest; raw API keys are never stored."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        api_key.encode("utf-8"),
        _KEY_SALT,
        _KEY_ITERATIONS,
    ).hex()


def match_secret(candidate: str, credentials: tuple[tuple[str, str], ...]) -> str | None:
    """Return the cached digest for a constant-time raw API-key match."""
    matched: str | None = None
    for configured_key, digest in credentials:
        if hmac.compare_digest(candidate, configured_key):
            matched = digest
    return matched


def principal_from_digest(digest: str) -> str:
    """Derive a stable, opaque principal identifier from a key digest."""
    return _PRINCIPAL_PREFIX + digest[:32]


ANONYMOUS_PRINCIPAL = _PRINCIPAL_PREFIX + "local-development"


def new_token(byte_length: int = 24) -> str:
    """Unguessable identifier used for assets and generated file names."""
    return secrets.token_urlsafe(byte_length)


def principal_bucket(principal: str) -> str:
    """Stable, non-reversible directory or blob prefix for a principal."""
    if principal.startswith(_PRINCIPAL_PREFIX):
        return principal.removeprefix(_PRINCIPAL_PREFIX)
    return principal
