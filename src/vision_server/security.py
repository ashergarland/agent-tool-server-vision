"""Opaque worker artifact identifiers and principal buckets."""

from __future__ import annotations

import secrets

_PRINCIPAL_PREFIX = "p_"


ANONYMOUS_PRINCIPAL = _PRINCIPAL_PREFIX + "local-development"


def new_token(byte_length: int = 24) -> str:
    """Unguessable identifier used for assets and generated file names."""
    return secrets.token_urlsafe(byte_length)


def principal_bucket(principal: str) -> str:
    """Stable, non-reversible directory or blob prefix for a principal."""
    if principal.startswith(_PRINCIPAL_PREFIX):
        return principal.removeprefix(_PRINCIPAL_PREFIX)
    return principal
