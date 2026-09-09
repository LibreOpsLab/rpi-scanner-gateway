"""
Microsoft Graph /sendMail backend — app-only, certificate auth via
app/graph_auth.py. Requires:
  - Entra app registration with Mail.Send application permission,
    admin-consented, and a Mail.Send Application Access Policy scoping it
    to SEND_FROM_MAILBOX (see docs/SETUP.md) — the same requirement as the
    old client-secret flow, just a different credential type.
"""
import base64
from pathlib import Path

import requests

from app import db
from app.config import config
from app.email.base import EmailError, EmailSender, require_config
from app.graph_auth import GraphAuthError, get_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphSender(EmailSender):
    def __init__(self):
        self.tenant_id = require_config("GRAPH_TENANT_ID", config.GRAPH_TENANT_ID)
        self.client_id = require_config("GRAPH_CLIENT_ID", config.GRAPH_CLIENT_ID)
        self.cert_path = require_config("GRAPH_CERT_PATH", str(config.GRAPH_CERT_PATH))
        self.cert_thumbprint = require_config("GRAPH_CERT_THUMBPRINT", config.GRAPH_CERT_THUMBPRINT)
        self.from_mailbox = require_config("SEND_FROM_MAILBOX", config.SEND_FROM_MAILBOX)
        self.recipient = require_config("RECIPIENT_EMAIL", db.get_recipient_email())

    def send(self, subject: str, body_html: str, attachment_path: str | None = None) -> None:
        message = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body_html},
                "toRecipients": [{"emailAddress": {"address": self.recipient}}],
            },
            "saveToSentItems": "true",
        }

        if attachment_path and Path(attachment_path).stat().st_size <= 3 * 1024 * 1024:
            with open(attachment_path, "rb") as f:
                content_b64 = base64.b64encode(f.read()).decode()
            message["message"]["attachments"] = [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": Path(attachment_path).name,
                "contentBytes": content_b64,
            }]

        try:
            token = get_token(self.tenant_id, self.client_id, self.cert_path, self.cert_thumbprint)
        except GraphAuthError as e:
            raise EmailError(str(e)) from e

        url = f"{GRAPH_BASE}/users/{self.from_mailbox}/sendMail"
        resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=message, timeout=30)
        if resp.status_code != 202:
            raise EmailError(f"sendMail failed ({resp.status_code}): {resp.text}")
