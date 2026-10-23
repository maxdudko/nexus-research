"""Argon2id password verifiers. The hash string is the only stored form."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from nexus.identity.errors import IdentityError

_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)
_DUMMY: str | None = None
_DUMMY_SECRET = "nexus-dummy-verifier-not-a-user-password"
_MIN_PASSWORD = 12
_MAX_PASSWORD = 128


def hash_password(password: str) -> str:
    """Return an Argon2id verifier. The plaintext is not retained."""
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether the password matches. Mismatch is false, not an error."""
    try:
        _HASHER.verify(password_hash, password)
    except VerifyMismatchError, VerificationError, InvalidHashError:
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    """Return whether a successful login should store a new verifier."""
    try:
        return _HASHER.check_needs_rehash(password_hash)
    except InvalidHashError, VerificationError:
        return True


def dummy_password_hash() -> str:
    """Return one process-stable verifier used when no account matches."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(_DUMMY_SECRET)
    return _DUMMY


def validate_password(password: str, email: str) -> None:
    """Reject a password that is the wrong length or equal to the email."""
    if not _MIN_PASSWORD <= len(password) <= _MAX_PASSWORD:
        raise IdentityError("invalid_password")
    if password == email:
        raise IdentityError("password_matches_email")
