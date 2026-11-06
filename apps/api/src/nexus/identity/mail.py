"""Optional SMTP password recovery, delivered after the generic response."""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.core.config import Settings
from nexus.identity.store import issue_password_reset
from nexus.identity.tokens import new_token, password_reset_hash
from nexus.observability.logging import log_event


def send_reset_email(settings: Settings, recipient: str, token: str) -> None:
    """Send without debug logging; socket operations use a bounded timeout."""
    assert (
        settings.smtp_host and settings.smtp_sender and settings.password_reset_origin
    )
    message = EmailMessage()
    message["From"] = settings.smtp_sender
    message["To"] = recipient
    message["Subject"] = "Reset your Nexus Research password"
    link = f"{settings.password_reset_origin}/reset-password#token={token}"
    message.set_content(
        "A password reset was requested for your Nexus Research account.\n\n"
        f"Set a new password: {link}\n\n"
        "This link expires in 30 minutes and can be used once. "
        "Requesting a new link invalidates this one.\n"
        "If you did not request this, you can ignore this email.\n"
    )
    context = ssl.create_default_context()
    client: smtplib.SMTP
    if settings.smtp_tls == "implicit":
        client = smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
            context=context,
        )
    else:
        client = smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        )
    with client:
        if settings.smtp_tls == "starttls":
            client.starttls(context=context)
        if settings.smtp_username and settings.smtp_password:
            client.login(
                settings.smtp_username, settings.smtp_password.get_secret_value()
            )
        client.send_message(
            message, from_addr=settings.smtp_sender, to_addrs=[recipient]
        )


async def deliver_password_reset(
    factory: async_sessionmaker[AsyncSession], settings: Settings, email: str
) -> None:
    """Keep account lookup and transport failures out of the public response."""
    try:
        token = new_token()
        recipient = await issue_password_reset(
            factory,
            email,
            password_reset_hash(token, settings.session_pepper.get_secret_value()),
        )
        if recipient is not None:
            await asyncio.to_thread(send_reset_email, settings, recipient, token)
    except Exception:
        # SMTP/database exceptions may contain recipient addresses or credentials.
        log_event("identity.audit", action="password_reset_delivery_failed")
