"""Opaque upload token generation and one-way verification helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_UPLOAD_TOKEN_BYTES = 32


def digest_upload_token(token: str) -> str:
    """Return the SHA-256 hex digest for an opaque upload token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_upload_token() -> tuple[str, str]:
    """Issue a cryptographically random token and its persistence-safe digest."""
    token = secrets.token_urlsafe(_UPLOAD_TOKEN_BYTES)
    return token, digest_upload_token(token)


def verify_upload_token(token: object, expected_digest: object) -> bool:
    """Safely compare an opaque token with a stored SHA-256 digest."""
    if not isinstance(token, str) or not isinstance(expected_digest, str):
        return False
    try:
        candidate_digest = digest_upload_token(token)
        return hmac.compare_digest(candidate_digest, expected_digest)
    except (TypeError, UnicodeError):
        return False
