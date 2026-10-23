"""Email normalization and HMAC session tokens."""

import base64
import hashlib
import hmac
import re
import secrets

from nexus.identity.errors import IdentityError

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_EMAIL = 254
_TOKEN_BYTES = 32


def normalize_email(value: str) -> str:
    """Trim and lowercase a login identifier."""
    email = value.strip().lower()
    if not email or len(email) > _MAX_EMAIL or _EMAIL.fullmatch(email) is None:
        raise IdentityError("invalid_email")
    return email


def new_token() -> str:
    """Return a URL-safe cookie value from 32 random bytes."""
    raw = secrets.token_bytes(_TOKEN_BYTES)
    encoded = base64.urlsafe_b64encode(raw).decode("ascii")
    return encoded.rstrip("=")


def token_hash(token: str, pepper: str) -> bytes:
    """HMAC the cookie value. The digest is what PostgreSQL stores."""
    digest = hmac.new(
        pepper.encode("utf-8"),
        token.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if len(digest) != _TOKEN_BYTES:
        raise RuntimeError("session digest must be 32 bytes")
    return digest


def csrf_token(session_token: str, pepper: str) -> str:
    """MAC the session cookie. This is not the digest stored for lookup.

    The label keeps the readable CSRF cookie distinct from ``token_hash``.
    """
    digest = hmac.new(
        pepper.encode("utf-8"),
        b"nexus-csrf-v1\0" + session_token.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
