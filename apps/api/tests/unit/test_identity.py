"""Password, token, and session rules."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher
from pydantic import SecretStr, ValidationError
from tests.conftest import make_settings

from nexus.core.config import Settings, _format_settings_error
from nexus.identity.errors import IdentityError
from nexus.identity.passwords import (
    dummy_password_hash,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from nexus.identity.sessions import session_verdict, should_touch
from nexus.identity.tokens import new_token, normalize_email, token_hash
from nexus.observability.logging import configure_logging, log_event

PEPPER = "test-session-pepper-0123456789abcdef"
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
_RELEASE_URL = "postgresql+asyncpg://nexus:3f9a0c1e7b2d4a6f@postgres:5432/nexus"


def test_verify_rejects_a_different_password() -> None:
    stored = hash_password("correct-horse-1")
    assert verify_password("correct-horse-1", stored) is True
    assert verify_password("correct-horse-2", stored) is False
    assert stored != "correct-horse-1"
    assert "correct-horse-1" not in stored


def test_rehash_detects_other_argon2_parameters() -> None:
    other = PasswordHasher(
        time_cost=1,
        memory_cost=8,
        parallelism=1,
        hash_len=16,
        salt_len=8,
    )
    assert needs_rehash(other.hash("correct-horse-1")) is True
    assert needs_rehash(hash_password("correct-horse-1")) is False


def test_dummy_hash_is_stable_and_not_a_user_password() -> None:
    first = dummy_password_hash()
    assert dummy_password_hash() == first
    assert verify_password("correct-horse-1", first) is False


def test_email_is_trimmed_and_lowercased() -> None:
    assert normalize_email("  Ada@Example.com ") == "ada@example.com"


@pytest.mark.parametrize("value", ["", "ada", "ada@", "a" * 250 + "@b.co", "ada @b.co"])
def test_email_rejects_malformed_values(value: str) -> None:
    with pytest.raises(IdentityError) as exc_info:
        normalize_email(value)
    assert exc_info.value.code == "invalid_email"


def test_password_length_and_email_equality() -> None:
    validate_password("correct-horse-1", "ada@example.com")
    with pytest.raises(IdentityError) as short:
        validate_password("short-pass", "ada@example.com")
    assert short.value.code == "invalid_password"
    with pytest.raises(IdentityError) as same:
        validate_password("ada@example.com", "ada@example.com")
    assert same.value.code == "password_matches_email"


def test_session_token_hash_is_not_the_cookie() -> None:
    token = new_token()
    digest = token_hash(token, PEPPER)
    assert digest != token.encode()
    assert len(digest) == 32
    assert token_hash(token, PEPPER) == digest
    assert token_hash(token, PEPPER + "x") != digest
    assert token_hash(new_token(), PEPPER) != digest


def test_session_verdict_covers_revocation_expiry_and_idle() -> None:
    kwargs = {
        "status": "active",
        "expires_at": NOW + timedelta(hours=12),
        "last_used_at": NOW,
        "revoked_at": None,
        "now": NOW + timedelta(minutes=1),
        "idle_seconds": 4 * 60 * 60,
    }
    assert session_verdict(**kwargs) == "ok"
    assert session_verdict(**{**kwargs, "revoked_at": NOW}) == "revoked"
    assert session_verdict(**{**kwargs, "status": "disabled"}) == "inactive"
    assert session_verdict(**{**kwargs, "status": "quarantine"}) == "inactive"
    assert session_verdict(**{**kwargs, "now": kwargs["expires_at"]}) == "expired"
    idle_now = NOW + timedelta(hours=4)
    assert session_verdict(**{**kwargs, "now": idle_now}) == "idle"
    assert should_touch(NOW, NOW + timedelta(minutes=4), 300) is False
    assert should_touch(NOW, NOW + timedelta(minutes=5), 300) is True


def test_identity_audit_drops_password_and_token() -> None:
    stream = io.StringIO()
    configure_logging(make_settings(log_format="json"), stream=stream)
    log_event(
        "identity.audit",
        action="reset_password",
        target_user_id="00000000-0000-4000-8000-000000000001",
        password="correct-horse-1",
        token="session-token",
        pepper=PEPPER,
    )
    text = stream.getvalue()
    assert '"event":"identity.audit"' in text
    assert '"action":"reset_password"' in text
    assert "00000000-0000-4000-8000-000000000001" in text
    assert "correct-horse-1" not in text
    assert "session-token" not in text
    assert PEPPER not in text


def test_production_requires_a_pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEXUS_SESSION_PEPPER", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            environment="production",
            database_url=_RELEASE_URL,
            qdrant_url="http://qdrant:6333",
            trusted_origins=["http://127.0.0.1:3000"],
        )
    message = _format_settings_error(exc_info.value)
    assert "NEXUS_SESSION_PEPPER" in message


def test_production_rejects_a_short_pepper() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(
            environment="production",
            database_url=_RELEASE_URL,
            session_pepper="short-pepper",
        )
    assert "32 bytes" in str(exc_info.value)
    assert "short-pepper" not in str(exc_info.value)


def test_production_rejects_a_pepper_equal_to_the_database_password() -> None:
    pepper = "p" * 32
    with pytest.raises(ValidationError) as exc_info:
        make_settings(
            environment="production",
            database_url=f"postgresql+asyncpg://nexus:{pepper}@postgres:5432/nexus",
            session_pepper=SecretStr(pepper),
        )
    assert "database password" in str(exc_info.value)
    assert pepper not in str(exc_info.value)


def test_production_requires_secure_cookies_or_the_loopback_exception() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(
            environment="production",
            database_url=_RELEASE_URL,
            cookie_secure=False,
        )
    assert "COOKIE_INSECURE_LOOPBACK" in str(exc_info.value)

    settings = make_settings(
        environment="production",
        database_url=_RELEASE_URL,
        cookie_secure=False,
        cookie_insecure_loopback=True,
    )
    assert settings.secure_cookies() is False


def test_trusted_origins_from_the_environment_stay_comma_separated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXUS_ENVIRONMENT", "production")
    monkeypatch.setenv("NEXUS_DATABASE_URL", _RELEASE_URL)
    monkeypatch.setenv("NEXUS_QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("NEXUS_SESSION_PEPPER", PEPPER)
    monkeypatch.setenv("NEXUS_COOKIE_SECURE", "false")
    monkeypatch.setenv("NEXUS_COOKIE_INSECURE_LOOPBACK", "true")
    monkeypatch.setenv(
        "NEXUS_TRUSTED_ORIGINS",
        "http://127.0.0.1:3000, http://localhost:3000",
    )
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.trusted_origins == [
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]


def test_production_requires_trusted_origins() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(
            environment="production",
            database_url=_RELEASE_URL,
            trusted_origins=[],
        )
    assert "trusted origins" in str(exc_info.value)


def test_development_defaults_cookie_and_origins() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://nexus:nexus@127.0.0.1:5432/nexus",
        qdrant_url="http://127.0.0.1:6333",
        session_pepper=PEPPER,
    )
    assert settings.secure_cookies() is False
    assert settings.trusted_origins == [
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]
    assert settings.login_max_email_failures == 5
    assert settings.login_max_ip_failures == 30
    assert settings.login_failure_window_seconds == 900
