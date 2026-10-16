"""Sending email (SMTP). Settings come from environment variables or a local `mail.env` file, never from the
database, so the mail password does not end up in backups.

mail.env (or environment variables):
    POS_SMTP_USER=shop@gmail.com
    POS_SMTP_PASSWORD=abcd efgh ijkl mnop     # a Gmail App Password (spaces are ignored)
    POS_SMTP_HOST=smtp.gmail.com              # optional
    POS_SMTP_PORT=587                         # optional (465 = implicit SSL)
    POS_SMTP_FROM_NAME=Enterprise POS         # optional
    POS_MAIL_MODE=console                     # testing/demos: print the email in the terminal instead of sending it
"""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from . import config
from .errors import POSError

MAIL_ENV_FILE = Path(os.environ.get("POS_MAIL_ENV", config.PROJECT_ROOT / "mail.env"))
TIMEOUT_SECONDS = 15


class MailError(POSError):
    """The email could not be sent."""


@dataclass(frozen=True)
class MailSettings:
    user: str
    password: str
    host: str = "smtp.gmail.com"
    port: int = 587
    sender_name: str = "Enterprise POS"
    console: bool = False


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_settings(env=None, path: Path | None = None) -> MailSettings | None:
    """Environment variables win over the file. Returns None when email is not set up."""
    values = _read_env_file(MAIL_ENV_FILE if path is None else path)
    source = os.environ if env is None else env
    values.update({key: value for key, value in source.items() if key.startswith("POS_")})
    if values.get("POS_MAIL_MODE", "").strip().lower() == "console":
        return MailSettings(user="console", password="", host="console", port=0, console=True)
    user = values.get("POS_SMTP_USER", "").strip()
    password = values.get("POS_SMTP_PASSWORD", "").replace(" ", "")
    if not user or not password:
        return None
    try:
        port = int(values.get("POS_SMTP_PORT", "587"))
    except ValueError:
        port = 587
    return MailSettings(
        user=user, password=password, host=values.get("POS_SMTP_HOST", "").strip() or "smtp.gmail.com",
        port=port, sender_name=values.get("POS_SMTP_FROM_NAME", "").strip() or "Enterprise POS")


def is_configured() -> bool:
    return load_settings() is not None


def send_mail(to: str, subject: str, body: str, settings: MailSettings | None = None) -> None:
    settings = settings or load_settings()
    if settings is None:
        raise MailError("Email is not set up on this computer. See the README (Email setup).")
    if settings.console:
        print(f"\n----- EMAIL (console mode) -----\nTo: {to}\nSubject: {subject}\n\n{body}\n"
              "--------------------------------\n", flush=True)
        return
    message = EmailMessage()
    message["From"] = formataddr((settings.sender_name, settings.user))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        if settings.port == 465:
            with smtplib.SMTP_SSL(settings.host, settings.port, timeout=TIMEOUT_SECONDS,
                                  context=ssl.create_default_context()) as smtp:
                smtp.login(settings.user, settings.password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(settings.host, settings.port, timeout=TIMEOUT_SECONDS) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(settings.user, settings.password)
                smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise MailError("The mail server refused the login. For Gmail use an App Password, "
                        "not your normal password.") from None
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Could not send the email: {exc}") from exc
