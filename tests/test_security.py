"""Opaque worker artifact identifier behavior."""

from __future__ import annotations

from vision_server.security import (
    ANONYMOUS_PRINCIPAL,
    new_token,
    principal_bucket,
)


def test_tokens_are_unguessable_and_principal_buckets_are_path_safe() -> None:
    first = new_token()
    second = new_token()
    assert first != second
    assert len(first) >= 32
    assert principal_bucket(ANONYMOUS_PRINCIPAL) == "local-development"
    assert principal_bucket("local-development") == "local-development"
