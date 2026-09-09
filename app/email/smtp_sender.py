"""
Generic SMTP backend — covers cPanel-hosted email, Gmail, Microsoft Live
(personal), and Apple iCloud Mail with no provider-specific branches: it's
the same protocol everywhere, just a different host/port/credentials. See
docs/SETUP.md for the exact values per provider.

Port 465 is treated as implicit TLS (SMTP_SSL); anything else uses STARTTLS,
matching how every mainstream provider documents their SMTP endpoints.
"""
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from app import db
from app.config import config
from app.email.base import EmailError, EmailSender, require_config


class SmtpSender(EmailSender):
    def __init__(self):
        self.host = require_config("SMTP_HOST", config.SMTP_HOST)
        self.port = config.SMTP_PORT
        self.username = require_config("SMTP_USERNAME", config.SMTP_USERNAME)
        self.password = require_config("SMTP_PASSWORD", config.SMTP_PASSWORD)
        self.from_address = require_config("SMTP_FROM_ADDRESS", config.SMTP_FROM_ADDRESS)
        self.recipient = require_config("RECIPIENT_EMAIL", db.get_recipient_email())

    def send(self, subject: str, body_html: str, attachment_path: str | None = None) -> None:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = self.from_address
        msg["To"] = self.recipient
        msg.attach(MIMEText(body_html, "html"))

        if attachment_path:
            path = Path(attachment_path)
            with open(path, "rb") as f:
                attachment = MIMEApplication(f.read(), Name=path.name)
            attachment["Content-Disposition"] = f'attachment; filename="{path.name}"'
            msg.attach(attachment)

        try:
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, context=ssl.create_default_context(), timeout=30) as server:
                    server.login(self.username, self.password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                    server.starttls(context=ssl.create_default_context())
                    server.login(self.username, self.password)
                    server.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise EmailError(f"SMTP send failed: {e}") from e
