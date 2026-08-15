"""Authentication digest and opaque identifier behavior."""

from __future__ import annotations

from vision_server.config import Settings
from vision_server.security import (
    digest_secret,
    match_secret,
    principal_bucket,
    principal_from_digest,
)


def test_secret_digests_are_deterministic_and_match_in_constant_time() -> None:
    first = digest_secret("first-secret")
    second = digest_secret("second-secret")

    assert first == digest_secret("first-secret")
    assert first != second
    assert len(first) == 64
    credentials = (("second-secret", second), ("first-secret", first))
    assert match_secret("first-secret", credentials) == first
    assert match_secret("unknown-secret", credentials) is None


def test_configured_digests_are_cached() -> None:
    settings = Settings(api_keys="first-secret,second-secret", _env_file=None)

    assert settings.api_key_credentials is settings.api_key_credentials
    assert len(settings.api_key_digests) == 2


def test_opaque_identifiers_are_stable_and_domain_separated() -> None:
    digest = digest_secret("first-secret")
    principal = principal_from_digest(digest)

    assert principal == principal_from_digest(digest)
    assert principal.startswith("p_")
    assert principal_bucket(principal) == principal_bucket(principal)
    assert principal_bucket(principal) not in principal
