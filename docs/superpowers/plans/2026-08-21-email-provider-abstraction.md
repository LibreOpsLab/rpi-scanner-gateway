# Email Provider Abstraction (SMTP + M365 Graph) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded Graph-only email sending in `app/graph.py` with a selectable `EmailSender` interface, shipping two backends: generic SMTP (covers cPanel and, generically, Gmail/Live/iCloud) and Microsoft Graph with certificate-based auth (covers M365).

**Architecture:** A new `app/email/` package with an `EmailSender` ABC (`base.py`), one module per backend (`smtp_sender.py`, `graph_sender.py`), and a `get_email_sender()` factory (`__init__.py`) that picks a backend from `config.EMAIL_PROVIDER`. Graph's certificate-based token acquisition is pulled into a new shared `app/graph_auth.py` so the email backend doesn't duplicate MSAL boilerplate. `app/watcher.py` is rewired to call the new abstraction instead of `app.graph.send_email`.

**Tech Stack:** Python stdlib `smtplib`/`email.mime` (SMTP), `msal` + `requests` (Graph, already a dependency).

**Spec:** [docs/superpowers/specs/2026-08-19-provider-abstraction-and-provisioning-design.md](../specs/2026-08-19-provider-abstraction-and-provisioning-design.md) — this plan implements the **email half only** (`EmailSender`, `graph_auth.py`, the `EMAIL_PROVIDER`/`RECIPIENT_EMAIL`/`SMTP_*`/`GRAPH_CERT_*` config). The spec's `StorageBackend`/WebDAV work, the cloud-init→`install.sh` provisioning rewrite, and the README/CONTRIBUTING/CLAUDE.md rebranding are separate, later plans.

## Global Constraints

- `EmailSender.send(self, subject: str, body_html: str, attachment_path: str | None = None) -> None` — exact signature from the spec's Architecture section. Both backends implement this and nothing else.
- `EMAIL_PROVIDER` values are `smtp` | `graph`, default `smtp` (spec's `.env.example` block).
- SMTP backend must be provider-agnostic (host/port/username/password only) so it covers Gmail, Microsoft Live, Apple iCloud, and cPanel without per-provider branches — port `465` → implicit SSL (`smtplib.SMTP_SSL`), any other port → STARTTLS (spec's SmtpSender description).
- Graph backend uses **certificate** auth (`GRAPH_CERT_PATH` + `GRAPH_CERT_THUMBPRINT`), not the client-secret flow the code uses today — this is a deliberate upgrade per the spec's Goals section ("Upgrade the M365 Graph path from a client secret to certificate-based auth").
- Config validation happens in each backend's `__init__`, not at module-level `Config` construction, so choosing one provider doesn't force the other provider's env vars to be present (spec's Configuration section).
- No delegated (interactive) Graph auth, no token cache — `GRAPH_TOKEN_CACHE_PATH`/`GRAPH_SCOPES` are dead scaffolding per the spec's Non-goals and get deleted, not migrated.
- No test suite is being added — the spec's Non-goals section explicitly defers this, and its Testing section describes manual verification only. This plan's verification steps use plain `python3 -c` checks against real behavior (raises/returns), not a new pytest dependency.
- **`app/graph.py`'s `upload_to_onedrive` is explicitly out of scope and must keep working unchanged.** It currently reads `config.UNCLE_EMAIL` (whose OneDrive to upload to) and `config.GRAPH_CLIENT_SECRET` (its auth). Both stay in `config.py`. The new email work adds `RECIPIENT_EMAIL` (who receives the email) and `GRAPH_CERT_PATH`/`GRAPH_CERT_THUMBPRINT` (email's own Graph auth) alongside them, rather than renaming/removing what OneDrive depends on. Storage's eventual migration to `graph_auth.py` and a `RECIPIENT_EMAIL`-only config is the follow-up plan's job.

---

### Task 1: Config surface for email providers

**Files:**

- Modify: `app/config.py` (full file, 67 lines today)
- Modify: `.env.example` (full file)

**Interfaces:**

- Produces: `config.EMAIL_PROVIDER: str`, `config.RECIPIENT_EMAIL: str`, `config.SMTP_HOST: str`, `config.SMTP_PORT: int`, `config.SMTP_USERNAME: str`, `config.SMTP_PASSWORD: str`, `config.SMTP_FROM_ADDRESS: str`, `config.GRAPH_CERT_PATH: str`, `config.GRAPH_CERT_THUMBPRINT: str` — every later task reads these off the `config` singleton exactly as named here.
- Consumes: nothing new (this task only touches config).

- [ ] **Step 1: Rewrite `app/config.py`**

Replace the whole file with:

```python
"""
Central configuration, loaded from environment variables (.env file).
Copy .env.example to .env and fill in before first run.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


class Config:
    # --- Filesystem paths ---
    SCAN_INBOX = Path(os.getenv("SCAN_INBOX", "/srv/scans/inbox"))
    SCAN_PROCESSING = Path(os.getenv("SCAN_PROCESSING", "/srv/scans/processing"))
    SCAN_ARCHIVE = Path(os.getenv("SCAN_ARCHIVE", "/srv/scans/archive"))  # 30-day local backup
    SCAN_FAILED = Path(os.getenv("SCAN_FAILED", "/srv/scans/failed"))
    THUMBNAIL_DIR = Path(os.getenv("THUMBNAIL_DIR", "/srv/scans/thumbnails"))
    DB_PATH = Path(os.getenv("DB_PATH", "/srv/scans/dashboard.db"))

    # --- Retention ---
    RETENTION_DAYS = _int("RETENTION_DAYS", 30)

    # --- OCR / pipeline tuning ---
    OCR_JOBS = _int("OCR_JOBS", 4)  # Pi 3B has 4 cores
    OCR_LANGUAGE = os.getenv("OCR_LANGUAGE", "eng")
    BLANK_PAGE_THRESHOLD = float(os.getenv("BLANK_PAGE_THRESHOLD", "0.995"))  # % white pixels

    # --- Email sending ---
    EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "smtp")  # smtp | graph
    RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "")  # who receives the scan notification

    # SMTP (Gmail / Microsoft Live / Apple iCloud / cPanel / any standard SMTP account)
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = _int("SMTP_PORT", 587)  # 465 = implicit SSL, anything else = STARTTLS
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_ADDRESS = os.getenv("SMTP_FROM_ADDRESS", "")

    # --- Microsoft Graph ---
    GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "")
    GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "")
    # Client-secret auth — still used by upload_to_onedrive() in app/graph.py.
    # Not used by the email backend, which uses the certificate pair below.
    GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "")
    # Certificate auth — used by app/email/graph_sender.py via app/graph_auth.py.
    GRAPH_CERT_PATH = os.getenv("GRAPH_CERT_PATH", "/opt/scan-pipeline/certs/graph-app.key")
    GRAPH_CERT_THUMBPRINT = os.getenv("GRAPH_CERT_THUMBPRINT", "")
    SEND_FROM_MAILBOX = os.getenv("SEND_FROM_MAILBOX", "")  # UPN of the mailbox sending Graph email

    # --- OneDrive ---
    # UNCLE_EMAIL is the OneDrive upload target for upload_to_onedrive() in app/graph.py
    # (distinct from RECIPIENT_EMAIL above, which is who gets the email). They're the
    # same address in the uncle deployment today, but kept separate since a future
    # storage backend or recipient could differ.
    UNCLE_EMAIL = os.getenv("UNCLE_EMAIL", "")
    ONEDRIVE_FOLDER_PATH = os.getenv("ONEDRIVE_FOLDER_PATH", "/Scanned Documents")

    # --- Dashboard ---
    DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    DASHBOARD_PORT = _int("DASHBOARD_PORT", 5000)

    @classmethod
    def ensure_dirs(cls):
        for d in [cls.SCAN_INBOX, cls.SCAN_PROCESSING, cls.SCAN_ARCHIVE,
                  cls.SCAN_FAILED, cls.THUMBNAIL_DIR, cls.DB_PATH.parent]:
            d.mkdir(parents=True, exist_ok=True)


config = Config()
```

This removes the unused `_req()` helper (defined in the old file, never called anywhere — confirmed via `grep -rn "_req(" --include="*.py" .` before writing this plan) and the dead `GRAPH_TOKEN_CACHE_PATH`/`GRAPH_SCOPES` fields (the delegated-auth flow they supported was never implemented).

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd /home/andy/Development/rpi-scanner-gateway && python3 -c "from app.config import config; print(config.EMAIL_PROVIDER, config.SMTP_PORT)"`
Expected: `smtp 587` (the defaults), no traceback.

- [ ] **Step 3: Rewrite `.env.example`**

Replace the whole file with:

```dotenv
# Copy to .env and fill in. See docs/SETUP.md for the full walkthrough,
# including provider-specific setup for whichever EMAIL_PROVIDER you pick.

# --- Email sending (required) ---
EMAIL_PROVIDER=smtp                # smtp | graph
RECIPIENT_EMAIL=                    # who gets the "scan ready" email

# SMTP — used when EMAIL_PROVIDER=smtp. Covers cPanel-hosted email, Gmail,
# Microsoft Live (personal), and Apple iCloud Mail — same protocol, just a
# different host/port/credentials per provider. See docs/SETUP.md for the
# exact host/port to use for each.
SMTP_HOST=
#SMTP_PORT=587                      # 465 = implicit SSL, anything else = STARTTLS
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_ADDRESS=

# Microsoft Graph — used when EMAIL_PROVIDER=graph (M365, app-only, certificate
# auth). Also required if you use OneDrive for cloud storage (ONEDRIVE_FOLDER_PATH
# below), independent of which EMAIL_PROVIDER you chose.
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CERT_PATH=/opt/scan-pipeline/certs/graph-app.key   # PEM private key, mode 600
GRAPH_CERT_THUMBPRINT=
SEND_FROM_MAILBOX=                  # UPN of the mailbox sending Graph email

# --- OneDrive (optional cloud storage) ---
# Still secret-based for now (separate from the cert-based email auth above).
GRAPH_CLIENT_SECRET=
UNCLE_EMAIL=                        # whose OneDrive receives the backup copy
#ONEDRIVE_FOLDER_PATH=/Scanned Documents

# --- Filesystem paths (optional — defaults match docs/SETUP.md step 2) ---
#SCAN_INBOX=/srv/scans/inbox
#SCAN_PROCESSING=/srv/scans/processing
#SCAN_ARCHIVE=/srv/scans/archive
#SCAN_FAILED=/srv/scans/failed
#THUMBNAIL_DIR=/srv/scans/thumbnails
#DB_PATH=/srv/scans/dashboard.db

# --- Retention (optional — default shown) ---
#RETENTION_DAYS=30

# --- OCR / pipeline tuning (optional — defaults shown) ---
#OCR_JOBS=4
#OCR_LANGUAGE=eng
#BLANK_PAGE_THRESHOLD=0.995

# --- Dashboard (optional — defaults shown) ---
#DASHBOARD_HOST=0.0.0.0
#DASHBOARD_PORT=5000
```

- [ ] **Step 4: Commit**

```bash
git add app/config.py .env.example
git commit -m "feat: add email provider config (EMAIL_PROVIDER, SMTP_*, GRAPH_CERT_*)"
```

---

### Task 2: `EmailSender` base class

**Files:**

- Create: `app/email/__init__.py` (empty in this task — package marker only; the factory function is added in Task 6)
- Create: `app/email/base.py`

**Interfaces:**

- Consumes: nothing.
- Produces: `EmailError(Exception)`, `EmailSender` ABC with abstract `send(self, subject: str, body_html: str, attachment_path: str | None = None) -> None`, and `require_config(name: str, value: str) -> str` — Tasks 3 and 4 import all three from `app.email.base`.

- [ ] **Step 1: Create the package directory and marker file**

```bash
mkdir -p app/email
touch app/email/__init__.py
```

- [ ] **Step 2: Write `app/email/base.py`**

```python
"""
EmailSender interface. Each backend (SMTP, Graph) implements send() and
validates its own required config in __init__ — see app/email/__init__.py
for how config.EMAIL_PROVIDER picks a backend.
"""
from abc import ABC, abstractmethod


class EmailError(Exception):
    pass


class EmailSender(ABC):
    @abstractmethod
    def send(self, subject: str, body_html: str, attachment_path: str | None = None) -> None:
        ...


def require_config(name: str, value: str) -> str:
    if not value:
        raise EmailError(f"Missing required env var: {name}. Check your .env file.")
    return value
```

- [ ] **Step 3: Verify it imports and the ABC can't be instantiated directly**

Run: `cd /home/andy/Development/rpi-scanner-gateway && python3 -c "
from app.email.base import EmailSender, EmailError, require_config
try:
    EmailSender()
    print('FAIL: should not be instantiable')
except TypeError:
    print('OK: ABC blocks direct instantiation')
try:
    require_config('X', '')
    print('FAIL: should have raised')
except EmailError as e:
    print('OK:', e)
"`
Expected:

```
OK: ABC blocks direct instantiation
OK: Missing required env var: X. Check your .env file.
```

- [ ] **Step 4: Commit**

```bash
git add app/email/__init__.py app/email/base.py
git commit -m "feat: add EmailSender base class"
```

---

### Task 3: Shared certificate-based Graph auth

**Files:**

- Create: `app/graph_auth.py`

**Interfaces:**

- Consumes: nothing beyond stdlib + `msal`.
- Produces: `GraphAuthError(Exception)`, `get_token(tenant_id: str, client_id: str, cert_path: str, cert_thumbprint: str, scopes: list[str] | None = None) -> str` — Task 5's `graph_sender.py` imports both.

- [ ] **Step 1: Write `app/graph_auth.py`**

```python
"""
Shared certificate-based Microsoft Graph token acquisition (app-only /
client-credentials flow). Used by app/email/graph_sender.py today; the
storage backend (app/graph.py's upload_to_onedrive, still secret-based)
will move onto this too in a later pass.
"""
import msal

DEFAULT_SCOPES = ["https://graph.microsoft.com/.default"]


class GraphAuthError(Exception):
    pass


def get_token(
    tenant_id: str,
    client_id: str,
    cert_path: str,
    cert_thumbprint: str,
    scopes: list[str] | None = None,
) -> str:
    try:
        with open(cert_path) as f:
            private_key = f.read()
    except OSError as e:
        raise GraphAuthError(f"Could not read GRAPH_CERT_PATH ({cert_path}): {e}") from e

    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential={"private_key": private_key, "thumbprint": cert_thumbprint},
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )
    result = app.acquire_token_for_client(scopes=scopes or DEFAULT_SCOPES)
    if "access_token" not in result:
        raise GraphAuthError(f"Token acquisition failed: {result.get('error_description', result)}")
    return result["access_token"]
```

- [ ] **Step 2: Verify the missing-cert-file path raises cleanly**

Run: `cd /home/andy/Development/rpi-scanner-gateway && python3 -c "
from app.graph_auth import get_token, GraphAuthError
try:
    get_token('t', 'c', '/nonexistent/cert.key', 'thumb')
    print('FAIL: should have raised')
except GraphAuthError as e:
    print('OK:', e)
"`
Expected: `OK: Could not read GRAPH_CERT_PATH (/nonexistent/cert.key): ...`

- [ ] **Step 3: Commit**

```bash
git add app/graph_auth.py
git commit -m "feat: add shared certificate-based Graph token acquisition"
```

---

### Task 4: SMTP backend

**Files:**

- Create: `app/email/smtp_sender.py`

**Interfaces:**

- Consumes: `app.config.config` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDRESS`, `RECIPIENT_EMAIL`), `app.email.base.{EmailSender, EmailError, require_config}`.
- Produces: `SmtpSender(EmailSender)` — Task 6's factory imports this.

- [ ] **Step 1: Write `app/email/smtp_sender.py`**

```python
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

from app.config import config
from app.email.base import EmailError, EmailSender, require_config


class SmtpSender(EmailSender):
    def __init__(self):
        self.host = require_config("SMTP_HOST", config.SMTP_HOST)
        self.port = config.SMTP_PORT
        self.username = require_config("SMTP_USERNAME", config.SMTP_USERNAME)
        self.password = require_config("SMTP_PASSWORD", config.SMTP_PASSWORD)
        self.from_address = require_config("SMTP_FROM_ADDRESS", config.SMTP_FROM_ADDRESS)
        self.recipient = require_config("RECIPIENT_EMAIL", config.RECIPIENT_EMAIL)

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
```

- [ ] **Step 2: Verify validation fires when config is missing**

Run: `cd /home/andy/Development/rpi-scanner-gateway && python3 -c "
from app.email.smtp_sender import SmtpSender
from app.email.base import EmailError
try:
    SmtpSender()
    print('FAIL: should have raised')
except EmailError as e:
    print('OK:', e)
"`
Expected: `OK: Missing required env var: SMTP_HOST. Check your .env file.`

- [ ] **Step 3: Verify construction succeeds with full fake config (no network call yet)**

Run: `cd /home/andy/Development/rpi-scanner-gateway && SMTP_HOST=smtp.example.com SMTP_USERNAME=u SMTP_PASSWORD=p SMTP_FROM_ADDRESS=from@example.com RECIPIENT_EMAIL=to@example.com python3 -c "
from app.email.smtp_sender import SmtpSender
s = SmtpSender()
print('OK:', s.host, s.port, s.recipient)
"`
Expected: `OK: smtp.example.com 587 to@example.com`

Actually sending mail requires a live SMTP account and is not exercised here — that's the manual smoke test called out in the spec's Testing section, done once real cPanel/Gmail/etc. credentials are available.

- [ ] **Step 4: Commit**

```bash
git add app/email/smtp_sender.py
git commit -m "feat: add SMTP email backend (cPanel, Gmail, Live, iCloud)"
```

---

### Task 5: Graph backend (certificate auth)

**Files:**

- Create: `app/email/graph_sender.py`

**Interfaces:**

- Consumes: `app.config.config` (`GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CERT_PATH`, `GRAPH_CERT_THUMBPRINT`, `SEND_FROM_MAILBOX`, `RECIPIENT_EMAIL`), `app.graph_auth.{get_token, GraphAuthError}`, `app.email.base.{EmailSender, EmailError, require_config}`.
- Produces: `GraphSender(EmailSender)` — Task 6's factory imports this.

- [ ] **Step 1: Write `app/email/graph_sender.py`**

```python
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
        self.recipient = require_config("RECIPIENT_EMAIL", config.RECIPIENT_EMAIL)

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
```

- [ ] **Step 2: Verify validation fires when config is missing**

Run: `cd /home/andy/Development/rpi-scanner-gateway && python3 -c "
from app.email.graph_sender import GraphSender
from app.email.base import EmailError
try:
    GraphSender()
    print('FAIL: should have raised')
except EmailError as e:
    print('OK:', e)
"`
Expected: `OK: Missing required env var: GRAPH_TENANT_ID. Check your .env file.`

- [ ] **Step 3: Verify construction succeeds with full fake config**

Run: `cd /home/andy/Development/rpi-scanner-gateway && GRAPH_TENANT_ID=t GRAPH_CLIENT_ID=c GRAPH_CERT_PATH=/tmp/fake.key GRAPH_CERT_THUMBPRINT=deadbeef SEND_FROM_MAILBOX=scanner@example.com RECIPIENT_EMAIL=to@example.com python3 -c "
from app.email.graph_sender import GraphSender
g = GraphSender()
print('OK:', g.from_mailbox, g.recipient)
"`
Expected: `OK: scanner@example.com to@example.com`

Real `send()` needs a live Entra app registration with an uploaded cert and a real mailbox — that's the manual smoke test from the spec's Testing section, not exercised here.

- [ ] **Step 4: Commit**

```bash
git add app/email/graph_sender.py
git commit -m "feat: add Graph email backend with certificate auth"
```

---

### Task 6: Provider dispatch factory

**Files:**

- Modify: `app/email/__init__.py` (currently empty from Task 2)

**Interfaces:**

- Consumes: `app.config.config.EMAIL_PROVIDER`, `SmtpSender` (Task 4), `GraphSender` (Task 5).
- Produces: `get_email_sender() -> EmailSender` — Task 7 (`app/watcher.py`) imports this.

- [ ] **Step 1: Write `app/email/__init__.py`**

```python
from app.config import config
from app.email.base import EmailError, EmailSender

__all__ = ["get_email_sender", "EmailSender", "EmailError"]


def get_email_sender() -> EmailSender:
    provider = config.EMAIL_PROVIDER
    if provider == "smtp":
        from app.email.smtp_sender import SmtpSender
        return SmtpSender()
    if provider == "graph":
        from app.email.graph_sender import GraphSender
        return GraphSender()
    raise EmailError(f"Unknown EMAIL_PROVIDER: {provider!r} (expected 'smtp' or 'graph')")
```

- [ ] **Step 2: Verify dispatch reaches each backend and rejects unknown providers**

`config.EMAIL_PROVIDER` is read once at import time (a class-level `os.getenv` call), so each provider must be checked in its own process — mutating `os.environ` after `app.config` is imported has no effect on it. Run each of these as a separate invocation:

```bash
EMAIL_PROVIDER=bogus python3 -c "
from app.email import get_email_sender, EmailError
try:
    get_email_sender()
    print('FAIL: should have raised')
except EmailError as e:
    print('OK:', e)
"
EMAIL_PROVIDER=smtp python3 -c "
from app.email import get_email_sender, EmailError
try:
    get_email_sender()
    print('FAIL: should have raised')
except EmailError as e:
    print('OK:', e)
"
EMAIL_PROVIDER=graph python3 -c "
from app.email import get_email_sender, EmailError
try:
    get_email_sender()
    print('FAIL: should have raised')
except EmailError as e:
    print('OK:', e)
"
```

Expected:

```
OK: Unknown EMAIL_PROVIDER: 'bogus' (expected 'smtp' or 'graph')
OK: Missing required env var: SMTP_HOST. Check your .env file.
OK: Missing required env var: GRAPH_TENANT_ID. Check your .env file.
```

- [ ] **Step 3: Commit**

```bash
git add app/email/__init__.py
git commit -m "feat: add get_email_sender() provider dispatch"
```

---

### Task 7: Wire into the watcher

**Files:**

- Modify: `app/watcher.py:29` (import line), `app/watcher.py:118` (the `send_email(...)` call), `app/watcher.py:122` (the `except` clause)
- Modify: `app/graph.py` (remove `send_email`, the now-unused `_headers`/`_get_token`'s email-only caller — verify they're still used by `upload_to_onedrive` before touching them)

**Interfaces:**

- Consumes: `app.email.{get_email_sender, EmailError}` (Task 6), `app.graph.{upload_to_onedrive, GraphError}` (unchanged, still exists).
- Produces: `app/watcher.py`'s `process_file()` now sends email through the new abstraction; nothing downstream of this task depends on new names.

- [ ] **Step 1: Confirm `_get_token`/`_headers` in `app/graph.py` are still needed by `upload_to_onedrive` after `send_email` is removed**

Run: `grep -n "_get_token\|_headers" /home/andy/Development/rpi-scanner-gateway/app/graph.py`
Expected: both are called inside `upload_to_onedrive` (lines ~62-64 and ~71 in the current file) in addition to the `send_email` function being deleted — confirms they must stay.

- [ ] **Step 2: Remove `send_email` from `app/graph.py`**

Delete this function (currently lines 96-125 of `app/graph.py`) and its `Path`-only-for-attachments usage if `Path` becomes unused elsewhere in the file (it's still used by `upload_to_onedrive`'s `Path(local_path).stat()` call, so the import stays):

```python
def send_email(subject: str, body_html: str, attachment_path: str | None = None):
    """
    Sends from config.SEND_FROM_MAILBOX to config.UNCLE_EMAIL.
    Attaches the PDF directly if under 3MB (Graph inline attachment limit
    is technically higher, but we keep a safety margin); otherwise the
    OneDrive link in the email body is the delivery method.
    """
    message = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [{"emailAddress": {"address": config.UNCLE_EMAIL}}],
        },
        "saveToSentItems": "true",
    }

    if attachment_path and Path(attachment_path).stat().st_size <= 3 * 1024 * 1024:
        import base64
        with open(attachment_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode()
        message["message"]["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": Path(attachment_path).name,
            "contentBytes": content_b64,
        }]

    url = f"{GRAPH_BASE}/users/{config.SEND_FROM_MAILBOX}/sendMail"
    resp = requests.post(url, headers=_headers(), json=message)
    if resp.status_code != 202:
        raise GraphError(f"sendMail failed ({resp.status_code}): {resp.text}")
```

Also update the module docstring at the top of `app/graph.py` — it currently says "Microsoft Graph integration" broadly and lists `Mail.Send` as a required permission. Change the docstring's opening lines to:

```python
"""
Microsoft Graph integration — OneDrive upload only (email sending moved to
app/email/graph_sender.py, which uses certificate auth via app/graph_auth.py
instead of the client secret this module still uses).

Why app-only instead of delegated: this runs unattended on a Pi with no
browser and no one around to re-consent when a refresh token expires.
Client credentials + a secret just works indefinitely with zero babysitting.

Required Entra app registration:
  - Application permission (admin-consented): Files.ReadWrite.All
  - Upload goes to /users/{UNCLE_EMAIL}/drive/... — Files.ReadWrite.All
    grants access tenant-wide, so restrict via an Application Access Policy
    if your tenant supports it, or accept the broader grant for a
    single-user tenant.
"""
```

- [ ] **Step 3: Verify `app/graph.py` still compiles and `upload_to_onedrive`/`GraphError` are intact**

Run: `cd /home/andy/Development/rpi-scanner-gateway && python3 -c "from app.graph import upload_to_onedrive, GraphError; print('OK')" && python3 -m py_compile app/graph.py`
Expected: `OK`, no traceback.

- [ ] **Step 4: Update `app/watcher.py`'s import line**

Change line 29 from:

```python
from app.graph import upload_to_onedrive, send_email, GraphError
```

to:

```python
from app.graph import upload_to_onedrive, GraphError
from app.email import get_email_sender, EmailError
```

- [ ] **Step 5: Update the email-sending call in `process_file()`**

Change (currently line 118):

```python
        send_email(subject=f"Scanned: {filename}", body_html=body, attachment_path=str(ocr_output_path))
```

to:

```python
        get_email_sender().send(subject=f"Scanned: {filename}", body_html=body, attachment_path=str(ocr_output_path))
```

- [ ] **Step 6: Update the `except` clause to catch `EmailError` too**

Change (currently line 122):

```python
    except (OcrError, GraphError) as e:
```

to:

```python
    except (OcrError, GraphError, EmailError) as e:
```

- [ ] **Step 7: Verify the whole module compiles and imports**

Run: `cd /home/andy/Development/rpi-scanner-gateway && python3 -m py_compile app/watcher.py && python3 -c "import app.watcher; print('OK')"`
Expected: `OK`, no traceback. (This will succeed even without `.env` filled in, since nothing in `app.watcher` constructs a sender or config-validates at import time — validation only happens when `get_email_sender()` is actually called inside `process_file()`.)

- [ ] **Step 8: Commit**

```bash
git add app/watcher.py app/graph.py
git commit -m "feat: wire watcher to the new email provider abstraction"
```

---

### Task 8: Document provider setup in SETUP.md

**Files:**

- Modify: `docs/SETUP.md`

**Interfaces:**

- Consumes: nothing (docs only).
- Produces: nothing consumed by other tasks — this is the last task.

- [ ] **Step 1: Replace SETUP.md's step 6 ("Microsoft Graph app registration") and its "Fill in .env" subsection**

Read the current `docs/SETUP.md` first (`sed -n '70,120p' docs/SETUP.md` shows the section to replace — it currently jumps straight to Graph app registration as the only option). Replace that section with:

````markdown
## 6. Email provider setup

Pick one and set `EMAIL_PROVIDER` accordingly in `.env`.

### Option A — SMTP (`EMAIL_PROVIDER=smtp`)

Works for cPanel-hosted email, Gmail, Microsoft Live (personal), and Apple
iCloud Mail — same protocol, different host/port/credentials:

| Provider                                   | SMTP_HOST                                                                               | SMTP_PORT                                           | SMTP_USERNAME                                                                                                      |
| ------------------------------------------ | --------------------------------------------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| cPanel                                     | usually `mail.yourdomain.com`, or check your host's docs/cPanel's "Email Accounts" page | `587` (STARTTLS) or `465` (SSL) — cPanel shows both | full email address                                                                                                 |
| Gmail                                      | `smtp.gmail.com`                                                                        | `587`                                               | full email address (needs an [app password](https://myaccount.google.com/apppasswords), not your regular password) |
| Microsoft Live (personal, not M365 tenant) | `smtp.office365.com` or `smtp-mail.outlook.com` — check your account type               | `587`                                               | full email address                                                                                                 |
| Apple iCloud Mail                          | `smtp.mail.me.com`                                                                      | `587`                                               | full iCloud email + an [app-specific password](https://support.apple.com/en-us/102654)                             |

`SMTP_FROM_ADDRESS` is usually the same as `SMTP_USERNAME`. `RECIPIENT_EMAIL`
is whoever should get the "scan ready" notification.

Confirm the exact host/port with your provider's current documentation —
these change occasionally and the table above may drift.

### Option B — Microsoft Graph (`EMAIL_PROVIDER=graph`)

For M365 tenants with Global Admin access, using certificate auth (no
client secret to rotate/leak):

```bash
openssl req -x509 -newkey rsa:2048 -keyout graph-app.key -out graph-app.crt \
    -days 730 -nodes -subj "/CN=rpi-scanner-gateway"
openssl x509 -in graph-app.crt -noout -fingerprint -sha1
```
````

1. **Entra admin center → App registrations → New registration**
   - Name: e.g. `Scanner Gateway`
   - Supported account types: single tenant
2. **Certificates & secrets → Certificates → Upload certificate** — upload
   `graph-app.crt`. The `-fingerprint -sha1` output from above, with the
   colons removed, is `GRAPH_CERT_THUMBPRINT`.
3. **API permissions → Add a permission → Microsoft Graph → Application
   permissions:** `Mail.Send`, then **Grant admin consent**.
4. Copy **Application (client) ID** → `GRAPH_CLIENT_ID`, and **Directory
   (tenant) ID** → `GRAPH_TENANT_ID`.
5. Put `graph-app.key` on the Pi at the path in `GRAPH_CERT_PATH`
   (default `/opt/scan-pipeline/certs/graph-app.key`), `chmod 600`, owned
   by `scanpipeline`. **Never commit it** — it's covered by `.gitignore`.

### Lock down Mail.Send (important, Option B only)

App-only `Mail.Send` without scoping lets this app send as _any_ mailbox in
the tenant. Restrict it to just the sending mailbox via Exchange Online
PowerShell:

```powershell
Connect-ExchangeOnline
New-ApplicationAccessPolicy -AppId "<GRAPH_CLIENT_ID>" `
    -PolicyScopeGroupId "scanner@yourtenant.com" `
    -AccessRight RestrictAccess `
    -Description "Scan pipeline - restrict to scanner mailbox only"
```

Set `SEND_FROM_MAILBOX` to that same mailbox.

### OneDrive backup (optional, independent of the above)

Still uses the client-secret Graph flow. If you want the local archive
copy also mirrored to OneDrive, follow the app registration steps above
but also add a client secret (**Certificates & secrets → New client
secret**) for `GRAPH_CLIENT_SECRET`, grant `Files.ReadWrite.All`, and set
`UNCLE_EMAIL` to whose OneDrive receives the upload. This will move onto
the same certificate as email sending in a future update.

```

- [ ] **Step 2: Fix the now-stale `.env` block further down SETUP.md**

Find the old block (originally step 6's tail):
```

GRAPH_TENANT_ID=<from step 6.4>
GRAPH_CLIENT_ID=<from step 6.4>
GRAPH_CLIENT_SECRET=<from step 6.2>
UNCLE_EMAIL=<his real M365 address>
SEND_FROM_MAILBOX=scanner@yourtenant.com
ONEDRIVE_FOLDER_PATH=/Scanned Documents

````
Delete it — it's superseded by the per-option guidance in Step 1 above and the annotated `.env.example`.

- [ ] **Step 3: Read the whole file back and sanity-check step numbering**

Run: `sed -n '1,50p' /home/andy/Development/rpi-scanner-gateway/docs/SETUP.md` and confirm steps still read 1→9 in order with no orphaned references to the deleted block (search `grep -n "step 6" docs/SETUP.md` to catch any cross-references that need updating to point at the new subsections).

- [ ] **Step 4: Commit**

```bash
git add docs/SETUP.md
git commit -m "docs: document SMTP and Graph-cert email provider setup"
````

---

## Self-Review Notes

- **Spec coverage:** This plan implements the spec's `EmailSender`/`base.py`/`smtp_sender.py`/`graph_sender.py`/`graph_auth.py` and the email-related `.env.example` keys in full. It deliberately does **not** implement `StorageBackend`/`webdav.py`, the `onedrive.py` split, the `install.sh` provisioning rewrite, or the README/CONTRIBUTING/CLAUDE.md rebranding — those remain the spec's job for later plans, called out explicitly in the Spec line above and the Global Constraints.
- **UNCLE_EMAIL / RECIPIENT_EMAIL split:** the spec says `RECIPIENT_EMAIL` replaces `UNCLE_EMAIL`, but that assumes the storage split (which redefines who owns the OneDrive target) happens in the same pass. Since storage is out of scope here, this plan keeps both — verified via `grep` that `UNCLE_EMAIL` is still needed by `upload_to_onedrive` before deciding this.
- **No new test framework:** matches the spec's own Non-goals/Testing sections; verification steps use `python3 -c` against real behavior instead.
