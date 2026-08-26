"""Small SMTP transport shared by isolated transactional mailers."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Literal, Protocol

from pydantic import SecretStr

DeliveryOutcome = Literal["sent", "transient_failure", "permanent_failure"]


class SmtpSettings(Protocol):
    ENVIRONMENT: Literal["development", "staging", "production"]
    EMAIL_HOST: str
    EMAIL_PORT: int
    EMAIL_USER: str
    EMAIL_PASSWORD: SecretStr
    EMAIL_USE_TLS: bool
    EMAIL_SMTP_TIMEOUT_SECONDS: int


@dataclass(frozen=True)
class DeliveryResult:
    outcome: DeliveryOutcome
    error_code: str | None = None


def send_smtp_message(message: EmailMessage, settings: SmtpSettings) -> DeliveryResult:
    """Deliver a message without exposing provider responses or credentials.

    Plain SMTP is accepted only by the local development mail catcher. Staging
    and production fail closed unless STARTTLS is enabled and verified.
    """
    if not settings.EMAIL_USE_TLS:
        local_hosts = {"mailpit", "localhost", "127.0.0.1", "::1"}
        local_plain_smtp = (
            settings.ENVIRONMENT == "development"
            and settings.EMAIL_HOST.strip().lower() in local_hosts
            and not settings.EMAIL_USER
            and not settings.EMAIL_PASSWORD.get_secret_value()
        )
        if not local_plain_smtp:
            return DeliveryResult("permanent_failure", "smtp_tls_required")

    result = DeliveryResult("sent")
    try:
        with smtplib.SMTP(
            settings.EMAIL_HOST,
            settings.EMAIL_PORT,
            timeout=settings.EMAIL_SMTP_TIMEOUT_SECONDS,
        ) as smtp:
            smtp.ehlo()
            if settings.EMAIL_USE_TLS:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if settings.EMAIL_USER:
                smtp.login(settings.EMAIL_USER, settings.EMAIL_PASSWORD.get_secret_value())
            smtp.send_message(message)
    except smtplib.SMTPRecipientsRefused:
        result = DeliveryResult("permanent_failure", "recipient_rejected")
    except smtplib.SMTPAuthenticationError:
        result = DeliveryResult("permanent_failure", "smtp_authentication_failed")
    except smtplib.SMTPSenderRefused:
        result = DeliveryResult("permanent_failure", "sender_rejected")
    except smtplib.SMTPResponseException as exc:
        if exc.smtp_code >= 500:
            result = DeliveryResult("permanent_failure", "smtp_permanent_error")
        else:
            result = DeliveryResult("transient_failure", "smtp_transient_error")
    except (ssl.CertificateError, ssl.SSLError):
        result = DeliveryResult("permanent_failure", "tls_verification_failed")
    except (smtplib.SMTPException, OSError, TimeoutError):
        result = DeliveryResult("transient_failure", "smtp_unavailable")
    return result
