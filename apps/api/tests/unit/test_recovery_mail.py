"""SMTP transport configuration and sanitized failure handling."""

import ssl
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from tests.conftest import make_settings

from nexus.identity import mail
from nexus.identity.tokens import password_reset_hash, token_hash


def smtp_settings(**overrides):
    return make_settings(
        smtp_host="smtp.example.com",
        smtp_sender="nexus@example.com",
        password_reset_origin="https://nexus.example.com",
        **overrides,
    )


@pytest.mark.parametrize("mode", ["starttls", "implicit", "plain"])
async def test_mail_transport_and_link(mode, monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    smtp = MagicMock(return_value=client)
    smtp_ssl = MagicMock(return_value=client)
    monkeypatch.setattr(mail.smtplib, "SMTP", smtp)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", smtp_ssl)
    settings = smtp_settings(
        smtp_tls=mode, smtp_username="mailer", smtp_password="secret"
    )
    mail.send_reset_email(settings, "ada@example.com", "token-123")
    called = smtp_ssl if mode == "implicit" else smtp
    assert called.call_args.args == ("smtp.example.com", 587)
    assert called.call_args.kwargs["timeout"] == 10
    if mode == "starttls":
        assert isinstance(client.starttls.call_args.kwargs["context"], ssl.SSLContext)
    else:
        client.starttls.assert_not_called()
    client.login.assert_called_once_with("mailer", "secret")
    message = client.send_message.call_args.args[0]
    assert message["To"] == "ada@example.com"
    assert message["From"] == "nexus@example.com"
    assert (
        "https://nexus.example.com/reset-password#token=token-123"
        in message.get_content()
    )
    assert "30 minutes" in message.get_content()
    assert client.send_message.call_args.kwargs["to_addrs"] == ["ada@example.com"]


async def test_delivery_failure_is_sanitized(monkeypatch, caplog):
    async def issue(*args):
        return "ada@example.com"

    def fail(*args):
        raise RuntimeError("secret token-123 ada@example.com smtp-password")

    events = []
    monkeypatch.setattr(
        mail, "log_event", lambda *args, **kwargs: events.append((args, kwargs))
    )
    monkeypatch.setattr(mail, "issue_password_reset", issue)
    monkeypatch.setattr(mail, "send_reset_email", fail)
    await mail.deliver_password_reset(None, smtp_settings(), "ada@example.com")
    assert events == [
        (("identity.audit",), {"action": "password_reset_delivery_failed"})
    ]
    for secret in ["secret", "token-123", "ada@example.com", "smtp-password"]:
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"smtp_tls": "plain", "environment": "production"},
        {"smtp_username": "user"},
        {"smtp_sender": "sender@example.com\r\nBcc: x@example.com"},
        {"password_reset_origin": "https://nexus.example.com/redirect"},
    ],
)
def test_invalid_mail_settings(overrides):
    values = dict(
        smtp_host="smtp.example.com",
        smtp_sender="nexus@example.com",
        password_reset_origin="https://nexus.example.com",
    )
    values.update(overrides)
    with pytest.raises(ValidationError):
        make_settings(**values)


def test_reset_hash_domain_is_separate():
    assert password_reset_hash("token", "pepper") != token_hash("token", "pepper")
    assert len(password_reset_hash("token", "pepper")) == 32
