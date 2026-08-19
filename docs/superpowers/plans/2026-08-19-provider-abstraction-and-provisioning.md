# Provider Abstraction, Provisioning, and Public-Release Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `rpi-scanner-gateway` from a single M365-only deployment into a generic, publicly-usable template — pluggable email (SMTP or M365 Graph) and cloud storage (OneDrive, WebDAV, or none) backends, certificate-based Graph auth, a cloud-init-free headless provisioning path, and the docs/config a stranger with basic IT skills needs to stand it up.

**Architecture:** Split today's Graph-only `app/graph.py` into two independently-selectable interfaces — `app/email/` (`EmailSender`) and `app/storage/` (`StorageBackend`, optional) — each with a factory reading its own `.env` key. Both Graph-based backends share certificate-auth token acquisition via `app/graph_auth.py`. Provisioning moves from a cloud-init dependency to Raspberry Pi Imager's OS Customisation (first boot) plus an idempotent `scripts/install.sh` (everything after).

**Tech Stack:** Python 3, Flask, `msal`, `requests`, stdlib `smtplib`/`email`, bash (`install.sh`), systemd.

**Spec:** [docs/superpowers/specs/2026-08-19-provider-abstraction-and-provisioning-design.md](../specs/2026-08-19-provider-abstraction-and-provisioning-design.md)

## Global Constraints

- No automated test suite exists and none is being added (spec Non-goals). Every task's verification step is a manual run (import check, local invocation, or — where noted — a real-account smoke test) rather than `pytest`.
- No Google Drive / Dropbox storage backends (spec Non-goals) — only `onedrive` and `webdav`.
- No delegated (interactive) Graph auth — app-only, certificate-based only. Remove the dead `GRAPH_TOKEN_CACHE_PATH`/`GRAPH_SCOPES` scaffolding.
- Graph credential type is a certificate, not a client secret — `GRAPH_CERT_PATH` (PEM private key file) + `GRAPH_CERT_THUMBPRINT`, not `GRAPH_CLIENT_SECRET`.
- SMTP default port is 587 (STARTTLS); port 465 means implicit SSL (`SMTP_SSL`). No other port-specific behavior.
- Each backend validates its own required `.env` vars in its own `__init__`, raising `EmailError`/`StorageError` — `app/config.py` itself does no validation (a user picking `EMAIL_PROVIDER=smtp` shouldn't be forced to also fill in Graph vars).
- `UNCLE_EMAIL` is renamed to `RECIPIENT_EMAIL` everywhere (code, docs, `.env.example`).
- The `db.jobs.onedrive_link` column is renamed to `storage_link` (no live deployment/migration to preserve — pre-release rename).

---

### Task 1: Config rewrite

**Files:**
- Modify: `app/config.py` (full rewrite)

**Interfaces:**
- Produces: `Config` attributes consumed by every later task —
  `RECIPIENT_EMAIL: str`, `EMAIL_PROVIDER: str`, `SMTP_HOST/PORT/USERNAME/PASSWORD/FROM_ADDRESS: str`,
  `GRAPH_TENANT_ID/CLIENT_ID/CERT_PATH/CERT_THUMBPRINT: str`, `SEND_FROM_MAILBOX: str`,
  `STORAGE_PROVIDER: str`, `ONEDRIVE_FOLDER_PATH: str`, `WEBDAV_URL/USERNAME/PASSWORD/FOLDER_PATH: str`.

- [ ] **Step 1: Rewrite `app/config.py`**

```python
"""
Central configuration, loaded from environment variables (.env file).
Copy .env.example to .env and fill in before first run.

Validation of provider-specific values (SMTP host, Graph cert, etc.) is
each backend's job (see app/email/ and app/storage/), not this module's —
only the provider you've selected should be required.
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

    # --- Who receives the scanned document ---
    RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "")

    # --- Email sending: EMAIL_PROVIDER selects the backend (see app/email/) ---
    EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "smtp")  # smtp | graph

    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = _int("SMTP_PORT", 587)
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_ADDRESS = os.getenv("SMTP_FROM_ADDRESS", "")

    # --- Microsoft Graph (app-only, certificate auth) ---
    # Shared by EMAIL_PROVIDER=graph and STORAGE_PROVIDER=onedrive.
    GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "")
    GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "")
    GRAPH_CERT_PATH = os.getenv("GRAPH_CERT_PATH", "")
    GRAPH_CERT_THUMBPRINT = os.getenv("GRAPH_CERT_THUMBPRINT", "")
    SEND_FROM_MAILBOX = os.getenv("SEND_FROM_MAILBOX", "")  # UPN of the mailbox sending the email

    # --- Cloud storage: STORAGE_PROVIDER selects the backend (see app/storage/) ---
    STORAGE_PROVIDER = os.getenv("STORAGE_PROVIDER", "none")  # onedrive | webdav | none
    ONEDRIVE_FOLDER_PATH = os.getenv("ONEDRIVE_FOLDER_PATH", "/Scanned Documents")

    WEBDAV_URL = os.getenv("WEBDAV_URL", "")
    WEBDAV_USERNAME = os.getenv("WEBDAV_USERNAME", "")
    WEBDAV_PASSWORD = os.getenv("WEBDAV_PASSWORD", "")
    WEBDAV_FOLDER_PATH = os.getenv("WEBDAV_FOLDER_PATH", "/ScannedDocuments")

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

- [ ] **Step 2: Verify it imports cleanly**

Run: `python3 -c "from app.config import config; print(config.EMAIL_PROVIDER, config.STORAGE_PROVIDER)"`
Expected: prints `smtp none` (the defaults) with no traceback. No `.env` is needed for this — every field has a default.

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "refactor: rework config for pluggable email/storage providers

Replaces UNCLE_EMAIL/GRAPH_CLIENT_SECRET/GRAPH_TOKEN_CACHE_PATH with
RECIPIENT_EMAIL and provider-selection keys (EMAIL_PROVIDER,
STORAGE_PROVIDER) plus per-provider settings. Validation moves to each
backend's __init__ in later tasks."
```

---

### Task 2: Provider interfaces and shared Graph auth

**Files:**
- Create: `app/email/__init__.py` (package marker only for now — the factory is added in Task 3)
- Create: `app/email/base.py`
- Create: `app/storage/__init__.py` (package marker only for now — the factory is added in Task 5)
- Create: `app/storage/base.py`
- Create: `app/graph_auth.py`

**Interfaces:**
- Consumes: `config.GRAPH_CLIENT_ID/GRAPH_CERT_PATH/GRAPH_CERT_THUMBPRINT/GRAPH_TENANT_ID` (Task 1).
- Produces: `EmailSender` (ABC, `send(subject, body_html, attachment_path=None) -> None`), `EmailError`;
  `StorageBackend` (ABC, `upload(local_path, filename) -> str`), `StorageError`;
  `get_token(scopes: list[str] | None = None) -> str`, `GraphAuthError` — consumed by Tasks 3–6.

- [ ] **Step 1: Create `app/email/base.py`**

```python
"""Common interface for email-sending backends."""
from abc import ABC, abstractmethod


class EmailError(Exception):
    pass


class EmailSender(ABC):
    @abstractmethod
    def send(self, subject: str, body_html: str, attachment_path: str | None = None) -> None:
        ...
```

- [ ] **Step 2: Create `app/email/__init__.py`**

```python
"""Email-sending backends. See get_email_sender() (added once the
backends exist) for the factory that reads EMAIL_PROVIDER."""
```

- [ ] **Step 3: Create `app/storage/base.py`**

```python
"""Common interface for cloud storage backends."""
from abc import ABC, abstractmethod


class StorageError(Exception):
    pass


class StorageBackend(ABC):
    @abstractmethod
    def upload(self, local_path: str, filename: str) -> str:
        """Uploads local_path, returns a link to the uploaded file."""
        ...
```

- [ ] **Step 4: Create `app/storage/__init__.py`**

```python
"""Cloud storage backends. See get_storage_backend() (added once the
backends exist) for the factory that reads STORAGE_PROVIDER."""
```

- [ ] **Step 5: Create `app/graph_auth.py`**

```python
"""Shared MSAL certificate-based token acquisition for the two Graph
backends (app/email/graph_sender.py and app/storage/onedrive.py) — both
call the Graph API under the same app registration.

App-only, certificate auth: no client secret, no interactive consent,
suited to an unattended Pi. See docs/SETUP.md for generating the
certificate and registering it against the Entra app.
"""
import msal
from app.config import config


class GraphAuthError(Exception):
    pass


def get_token(scopes: list[str] | None = None) -> str:
    scopes = scopes or ["https://graph.microsoft.com/.default"]
    with open(config.GRAPH_CERT_PATH) as f:
        private_key = f.read()
    app = msal.ConfidentialClientApplication(
        client_id=config.GRAPH_CLIENT_ID,
        client_credential={
            "private_key": private_key,
            "thumbprint": config.GRAPH_CERT_THUMBPRINT,
        },
        authority=f"https://login.microsoftonline.com/{config.GRAPH_TENANT_ID}",
    )
    result = app.acquire_token_for_client(scopes=scopes)
    if "access_token" not in result:
        raise GraphAuthError(
            f"Graph token acquisition failed: {result.get('error_description', result)}"
        )
    return result["access_token"]
```

- [ ] **Step 6: Verify imports**

Run: `python3 -c "from app.email.base import EmailSender, EmailError; from app.storage.base import StorageBackend, StorageError; from app.graph_auth import get_token, GraphAuthError; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 7: Commit**

```bash
git add app/email/ app/storage/ app/graph_auth.py
git commit -m "feat: add EmailSender/StorageBackend interfaces and shared Graph auth"
```

---

### Task 3: SMTP email backend and email factory

**Files:**
- Create: `app/email/smtp_sender.py`
- Modify: `app/email/__init__.py`

**Interfaces:**
- Consumes: `EmailSender`, `EmailError` (Task 2); `config.SMTP_*`, `config.RECIPIENT_EMAIL`, `config.EMAIL_PROVIDER` (Task 1).
- Produces: `SmtpSender(EmailSender)`; `get_email_sender() -> EmailSender` (used by Task 8's watcher integration).

- [ ] **Step 1: Create `app/email/smtp_sender.py`**

```python
"""Generic SMTP email sending — covers Gmail, Microsoft Live (personal),
Apple iCloud Mail, and cPanel-hosted email. All authenticate the same way
(host/port/username/app-password); see docs/SETUP.md for per-provider
host/port values.
"""
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path

from app.config import config
from app.email.base import EmailSender, EmailError


class SmtpSender(EmailSender):
    def __init__(self):
        self.host = config.SMTP_HOST
        self.port = config.SMTP_PORT
        self.username = config.SMTP_USERNAME
        self.password = config.SMTP_PASSWORD
        self.from_address = config.SMTP_FROM_ADDRESS
        for name, val in [
            ("SMTP_HOST", self.host),
            ("SMTP_USERNAME", self.username),
            ("SMTP_PASSWORD", self.password),
            ("SMTP_FROM_ADDRESS", self.from_address),
            ("RECIPIENT_EMAIL", config.RECIPIENT_EMAIL),
        ]:
            if not val:
                raise EmailError(f"Missing required env var for EMAIL_PROVIDER=smtp: {name}")

    def send(self, subject: str, body_html: str, attachment_path: str | None = None) -> None:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = self.from_address
        msg["To"] = config.RECIPIENT_EMAIL
        msg.attach(MIMEText(body_html, "html"))

        if attachment_path:
            path = Path(attachment_path)
            with open(path, "rb") as f:
                part = MIMEApplication(f.read(), _subtype="pdf", Name=path.name)
            part["Content-Disposition"] = f'attachment; filename="{path.name}"'
            msg.attach(part)

        try:
            if self.port == 465:
                server = smtplib.SMTP_SSL(self.host, self.port, context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(self.host, self.port)
                server.starttls(context=ssl.create_default_context())
            with server:
                server.login(self.username, self.password)
                server.sendmail(self.from_address, [config.RECIPIENT_EMAIL], msg.as_string())
        except smtplib.SMTPException as e:
            raise EmailError(f"SMTP send failed: {e}") from e
```

- [ ] **Step 2: Replace `app/email/__init__.py` with the factory**

```python
"""Factory for the configured EmailSender backend."""
from app.config import config
from app.email.base import EmailSender, EmailError

__all__ = ["get_email_sender", "EmailSender", "EmailError"]


def get_email_sender() -> EmailSender:
    if config.EMAIL_PROVIDER == "smtp":
        from app.email.smtp_sender import SmtpSender
        return SmtpSender()
    if config.EMAIL_PROVIDER == "graph":
        from app.email.graph_sender import GraphSender
        return GraphSender()
    raise EmailError(
        f"Unknown EMAIL_PROVIDER: {config.EMAIL_PROVIDER!r} (expected 'smtp' or 'graph')"
    )
```

Note: the `graph` branch imports `app.email.graph_sender`, which doesn't
exist until Task 4. That's fine — the import is local to the branch and
only runs if `EMAIL_PROVIDER=graph` is actually selected, which the next
step doesn't do.

- [ ] **Step 3: Verify the factory raises cleanly without a filled-in `.env`**

Run: `python3 -c "from app.email import get_email_sender; get_email_sender()"`
Expected: raises `app.email.base.EmailError: Missing required env var for EMAIL_PROVIDER=smtp: SMTP_HOST` (no `.env` is present yet, so `EMAIL_PROVIDER` defaults to `smtp` and the SMTP vars are blank) — this confirms the factory wiring and validation both work.

- [ ] **Step 4: Commit**

```bash
git add app/email/smtp_sender.py app/email/__init__.py
git commit -m "feat: add SMTP email backend (Gmail/Live/iCloud/cPanel)"
```

---

### Task 4: Graph email backend

**Files:**
- Create: `app/email/graph_sender.py`

**Interfaces:**
- Consumes: `EmailSender`, `EmailError` (Task 2); `get_token`, `GraphAuthError` (Task 2); `config.GRAPH_*`, `config.SEND_FROM_MAILBOX`, `config.RECIPIENT_EMAIL` (Task 1).
- Produces: `GraphSender(EmailSender)` — picked up by the factory added in Task 3.

- [ ] **Step 1: Create `app/email/graph_sender.py`**

```python
"""Microsoft Graph email sending — app-only, certificate auth.

Required Entra app registration: application permission Mail.Send
(admin-consented), scoped down via an Exchange "Application Access
Policy" to SEND_FROM_MAILBOX only. See docs/SETUP.md.
"""
import base64
from pathlib import Path

import requests

from app.config import config
from app.email.base import EmailSender, EmailError
from app.graph_auth import get_token, GraphAuthError

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphSender(EmailSender):
    def __init__(self):
        for name, val in [
            ("GRAPH_TENANT_ID", config.GRAPH_TENANT_ID),
            ("GRAPH_CLIENT_ID", config.GRAPH_CLIENT_ID),
            ("GRAPH_CERT_PATH", config.GRAPH_CERT_PATH),
            ("GRAPH_CERT_THUMBPRINT", config.GRAPH_CERT_THUMBPRINT),
            ("SEND_FROM_MAILBOX", config.SEND_FROM_MAILBOX),
            ("RECIPIENT_EMAIL", config.RECIPIENT_EMAIL),
        ]:
            if not val:
                raise EmailError(f"Missing required env var for EMAIL_PROVIDER=graph: {name}")

    def send(self, subject: str, body_html: str, attachment_path: str | None = None) -> None:
        message = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body_html},
                "toRecipients": [{"emailAddress": {"address": config.RECIPIENT_EMAIL}}],
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
            token = get_token()
        except GraphAuthError as e:
            raise EmailError(str(e)) from e

        url = f"{GRAPH_BASE}/users/{config.SEND_FROM_MAILBOX}/sendMail"
        resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=message)
        if resp.status_code != 202:
            raise EmailError(f"sendMail failed ({resp.status_code}): {resp.text}")
```

- [ ] **Step 2: Verify the factory now resolves the graph branch**

Run: `EMAIL_PROVIDER=graph python3 -c "from app.email import get_email_sender; get_email_sender()"`
Expected: raises `app.email.base.EmailError: Missing required env var for EMAIL_PROVIDER=graph: GRAPH_TENANT_ID` — confirms `graph_sender.py` is importable and wired into the factory.

- [ ] **Step 3: Commit**

```bash
git add app/email/graph_sender.py
git commit -m "feat: add Graph email backend with certificate auth"
```

---

### Task 5: OneDrive storage backend and storage factory

**Files:**
- Create: `app/storage/onedrive.py`
- Modify: `app/storage/__init__.py`

**Interfaces:**
- Consumes: `StorageBackend`, `StorageError` (Task 2); `get_token`, `GraphAuthError` (Task 2); `config.GRAPH_*`, `config.RECIPIENT_EMAIL`, `config.ONEDRIVE_FOLDER_PATH` (Task 1).
- Produces: `OneDriveStorage(StorageBackend)`; `get_storage_backend() -> StorageBackend | None` (used by Task 8).

- [ ] **Step 1: Create `app/storage/onedrive.py`**

```python
"""Microsoft Graph OneDrive upload — app-only, certificate auth.

Required Entra app registration: application permission
Files.ReadWrite.All (admin-consented). Uploads to
config.ONEDRIVE_FOLDER_PATH in RECIPIENT_EMAIL's OneDrive —
Files.ReadWrite.All grants tenant-wide access, restrict via an
equivalent access policy if your tenant supports it, or accept the
broader grant for a single-user tenant. See docs/SETUP.md.
"""
from pathlib import Path

import requests

from app.config import config
from app.storage.base import StorageBackend, StorageError
from app.graph_auth import get_token, GraphAuthError

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class OneDriveStorage(StorageBackend):
    def __init__(self):
        for name, val in [
            ("GRAPH_TENANT_ID", config.GRAPH_TENANT_ID),
            ("GRAPH_CLIENT_ID", config.GRAPH_CLIENT_ID),
            ("GRAPH_CERT_PATH", config.GRAPH_CERT_PATH),
            ("GRAPH_CERT_THUMBPRINT", config.GRAPH_CERT_THUMBPRINT),
            ("RECIPIENT_EMAIL", config.RECIPIENT_EMAIL),
        ]:
            if not val:
                raise StorageError(f"Missing required env var for STORAGE_PROVIDER=onedrive: {name}")

    def _headers(self) -> dict:
        try:
            return {"Authorization": f"Bearer {get_token()}"}
        except GraphAuthError as e:
            raise StorageError(str(e)) from e

    def upload(self, local_path: str, filename: str) -> str:
        file_size = Path(local_path).stat().st_size
        remote_path = f"{config.ONEDRIVE_FOLDER_PATH.strip('/')}/{filename}"
        user = config.RECIPIENT_EMAIL

        if file_size <= 4 * 1024 * 1024:
            url = f"{GRAPH_BASE}/users/{user}/drive/root:/{remote_path}:/content"
            with open(local_path, "rb") as f:
                resp = requests.put(url, headers=self._headers(), data=f.read())
            if resp.status_code not in (200, 201):
                raise StorageError(f"OneDrive upload failed ({resp.status_code}): {resp.text}")
            return resp.json().get("webUrl", "")

        session_url = f"{GRAPH_BASE}/users/{user}/drive/root:/{remote_path}:/createUploadSession"
        resp = requests.post(session_url, headers=self._headers(), json={
            "item": {"@microsoft.graph.conflictBehavior": "replace"}
        })
        if resp.status_code not in (200, 201):
            raise StorageError(f"Could not create upload session ({resp.status_code}): {resp.text}")
        upload_url = resp.json()["uploadUrl"]

        chunk_size = 320 * 1024 * 10  # ~3.2MB, must be a multiple of 320 KiB
        with open(local_path, "rb") as f:
            offset = 0
            while offset < file_size:
                chunk = f.read(chunk_size)
                chunk_end = offset + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{chunk_end}/{file_size}",
                }
                put_resp = requests.put(upload_url, headers=headers, data=chunk)
                if put_resp.status_code not in (200, 201, 202):
                    raise StorageError(f"Chunk upload failed ({put_resp.status_code}): {put_resp.text}")
                offset += len(chunk)

        return put_resp.json().get("webUrl", "")
```

- [ ] **Step 2: Replace `app/storage/__init__.py` with the factory**

```python
"""Factory for the configured StorageBackend, or None if disabled."""
from app.config import config
from app.storage.base import StorageBackend, StorageError

__all__ = ["get_storage_backend", "StorageBackend", "StorageError"]


def get_storage_backend() -> StorageBackend | None:
    if config.STORAGE_PROVIDER == "none":
        return None
    if config.STORAGE_PROVIDER == "onedrive":
        from app.storage.onedrive import OneDriveStorage
        return OneDriveStorage()
    if config.STORAGE_PROVIDER == "webdav":
        from app.storage.webdav import WebDavStorage
        return WebDavStorage()
    raise StorageError(
        f"Unknown STORAGE_PROVIDER: {config.STORAGE_PROVIDER!r} (expected 'onedrive', 'webdav', or 'none')"
    )
```

As with the email factory, the `webdav` branch references a module
(`app.storage.webdav`) that doesn't exist until Task 6 — safe, since it's
a local import inside a branch not exercised until that provider is
selected.

- [ ] **Step 3: Verify both the `none` and `onedrive` paths**

Run: `python3 -c "from app.storage import get_storage_backend; print(get_storage_backend())"`
Expected: prints `None` (default `STORAGE_PROVIDER=none`).

Run: `STORAGE_PROVIDER=onedrive python3 -c "from app.storage import get_storage_backend; get_storage_backend()"`
Expected: raises `app.storage.base.StorageError: Missing required env var for STORAGE_PROVIDER=onedrive: GRAPH_TENANT_ID`.

- [ ] **Step 4: Commit**

```bash
git add app/storage/onedrive.py app/storage/__init__.py
git commit -m "feat: add OneDrive storage backend and storage factory"
```

---

### Task 6: WebDAV storage backend, retire app/graph.py

**Files:**
- Create: `app/storage/webdav.py`
- Delete: `app/graph.py` (fully superseded by `app/email/graph_sender.py` + `app/storage/onedrive.py`)

**Interfaces:**
- Consumes: `StorageBackend`, `StorageError` (Task 2); `config.WEBDAV_*` (Task 1).
- Produces: `WebDavStorage(StorageBackend)` — picked up by the factory from Task 5.

- [ ] **Step 1: Create `app/storage/webdav.py`**

```python
"""Generic WebDAV upload — covers Nextcloud/ownCloud and any host
offering WebDAV (some cPanel hosts do). Plain HTTP PUT with basic auth,
no extra dependency beyond `requests`.
"""
import requests

from app.config import config
from app.storage.base import StorageBackend, StorageError


class WebDavStorage(StorageBackend):
    def __init__(self):
        for name, val in [
            ("WEBDAV_URL", config.WEBDAV_URL),
            ("WEBDAV_USERNAME", config.WEBDAV_USERNAME),
            ("WEBDAV_PASSWORD", config.WEBDAV_PASSWORD),
        ]:
            if not val:
                raise StorageError(f"Missing required env var for STORAGE_PROVIDER=webdav: {name}")

    def upload(self, local_path: str, filename: str) -> str:
        folder = config.WEBDAV_FOLDER_PATH.strip("/")
        url = f"{config.WEBDAV_URL.rstrip('/')}/{folder}/{filename}"
        with open(local_path, "rb") as f:
            resp = requests.put(url, data=f, auth=(config.WEBDAV_USERNAME, config.WEBDAV_PASSWORD))
        if resp.status_code not in (200, 201, 204):
            raise StorageError(f"WebDAV upload failed ({resp.status_code}): {resp.text}")
        return url
```

- [ ] **Step 2: Delete `app/graph.py`**

Run: `git rm app/graph.py`

- [ ] **Step 3: Verify the webdav path and confirm nothing still imports app.graph**

Run: `STORAGE_PROVIDER=webdav python3 -c "from app.storage import get_storage_backend; get_storage_backend()"`
Expected: raises `app.storage.base.StorageError: Missing required env var for STORAGE_PROVIDER=webdav: WEBDAV_URL`.

Run: `grep -rn "app.graph\b" --include=*.py .`
Expected: no output (Task 8 will update `watcher.py`'s import, which is the only remaining consumer — if this greps something other than `app/graph_auth.py`, stop and check).

- [ ] **Step 4: Commit**

```bash
git add app/storage/webdav.py
git commit -m "feat: add WebDAV storage backend, retire app/graph.py

Its two responsibilities (email send, OneDrive upload) are now split
across app/email/graph_sender.py and app/storage/onedrive.py, sharing
token acquisition via app/graph_auth.py."
```

---

### Task 7: DB column rename and dashboard templates

**Files:**
- Modify: `app/db.py`
- Modify: `app/templates/details.html`
- Modify: `app/templates/index.html`

**Interfaces:**
- Consumes: nothing new.
- Produces: `db.SCHEMA` with a `storage_link` column (was `onedrive_link`) — Task 8's watcher writes to this column via `db.update_job(job_id, storage_link=...)`.

- [ ] **Step 1: Rename the column in `app/db.py`**

In the `SCHEMA` string, change:

```python
    onedrive_link TEXT,
```

to:

```python
    storage_link TEXT,
```

No other part of `db.py` references the column by name (`create_job`/`update_job` are generic over `**fields`), so this is the only edit in this file.

- [ ] **Step 2: Update `app/templates/details.html`**

Change the Links cell (currently):

```html
            <td>
                {% if job.onedrive_link %}<a href="{{ job.onedrive_link }}" target="_blank">OneDrive</a>{% endif %}
                {% if job.email_sent %} &middot; Emailed{% endif %}
            </td>
```

to:

```html
            <td>
                {% if job.storage_link %}<a href="{{ job.storage_link }}" target="_blank">Cloud copy</a>{% endif %}
                {% if job.email_sent %} &middot; Emailed{% endif %}
            </td>
```

- [ ] **Step 3: Update `app/templates/index.html`**

Change (currently hardcodes OneDrive and a personal "nephew" reference):

```html
        {% if job.status == 'done' %}
        <div class="meta">Emailed &amp; saved to OneDrive ✔</div>
        {% elif job.status == 'failed' %}
        <div class="meta">Something went wrong — check /details or ask your nephew</div>
        {% endif %}
```

to:

```html
        {% if job.status == 'done' %}
        <div class="meta">Emailed{% if job.storage_link %} &amp; saved to cloud storage{% endif %} ✔</div>
        {% elif job.status == 'failed' %}
        <div class="meta">Something went wrong — check /details or contact whoever manages this Pi</div>
        {% endif %}
```

- [ ] **Step 4: Verify**

Run: `grep -rn "onedrive_link" app/`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add app/db.py app/templates/details.html app/templates/index.html
git commit -m "refactor: rename onedrive_link column/references to storage_link

Generalizes the dashboard for any storage backend, not just OneDrive."
```

---

### Task 8: Watcher integration

**Files:**
- Modify: `app/watcher.py`

**Interfaces:**
- Consumes: `get_email_sender`, `EmailError` (Tasks 2–4); `get_storage_backend`, `StorageError` (Tasks 2, 5–6); `db.update_job(job_id, storage_link=...)` (Task 7).

- [ ] **Step 1: Update imports and add backend globals**

Change:

```python
from app.config import config
from app import db
from app.blank_pages import strip_blank_pages
from app.ocr import run_ocr, make_thumbnail, OcrError
from app.graph import upload_to_onedrive, send_email, GraphError
```

to:

```python
from app.config import config
from app import db
from app.blank_pages import strip_blank_pages
from app.ocr import run_ocr, make_thumbnail, OcrError
from app.email import get_email_sender, EmailError
from app.storage import get_storage_backend, StorageError
```

Add, just below the `logger = logging.getLogger("watcher")` line:

```python
_email_sender = None
_storage_backend = None
```

- [ ] **Step 2: Replace steps 5–6 of `process_file()`**

Change (currently):

```python
        # --- Step 5: upload to OneDrive ---
        db.update_job(job_id, status="uploading")
        onedrive_link = upload_to_onedrive(str(ocr_output_path), filename)
        db.update_job(job_id, onedrive_link=onedrive_link)

        # --- Step 6: email the uncle ---
        size_mb = compressed_size / (1024 * 1024)
        body = f"""
        <p>Hi,</p>
        <p>Your scanned document <b>{filename}</b> is ready.</p>
        <p>{kept} page(s) processed{f', {removed} blank page(s) removed' if removed else ''}.
        File size: {size_mb:.1f} MB.</p>
        <p>A copy has also been saved to your OneDrive:<br>
        <a href="{onedrive_link}">{onedrive_link}</a></p>
        """
        send_email(subject=f"Scanned: {filename}", body_html=body, attachment_path=str(ocr_output_path))
        db.update_job(job_id, email_sent=1, status="done")
        logger.info("Job %s complete: %s", job_id, filename)
```

to:

```python
        # --- Step 5: upload to cloud storage (optional) ---
        storage_link = None
        if _storage_backend is not None:
            db.update_job(job_id, status="uploading")
            storage_link = _storage_backend.upload(str(ocr_output_path), filename)
            db.update_job(job_id, storage_link=storage_link)

        # --- Step 6: email ---
        size_mb = compressed_size / (1024 * 1024)
        storage_note = (
            f'<p>A copy has also been saved to your cloud storage:<br>'
            f'<a href="{storage_link}">{storage_link}</a></p>'
        ) if storage_link else ""
        body = f"""
        <p>Hi,</p>
        <p>Your scanned document <b>{filename}</b> is ready.</p>
        <p>{kept} page(s) processed{f', {removed} blank page(s) removed' if removed else ''}.
        File size: {size_mb:.1f} MB.</p>
        {storage_note}
        """
        _email_sender.send(subject=f"Scanned: {filename}", body_html=body, attachment_path=str(ocr_output_path))
        db.update_job(job_id, email_sent=1, status="done")
        logger.info("Job %s complete: %s", job_id, filename)
```

- [ ] **Step 3: Update the exception clause**

Change:

```python
    except (OcrError, GraphError) as e:
```

to:

```python
    except (OcrError, EmailError, StorageError) as e:
```

- [ ] **Step 4: Instantiate the backends in `main()`**

Change:

```python
def main():
    config.ensure_dirs()
    db.init_db()
    logger.info("Watching %s for new scans...", config.SCAN_INBOX)
```

to:

```python
def main():
    global _email_sender, _storage_backend
    config.ensure_dirs()
    db.init_db()
    _email_sender = get_email_sender()
    _storage_backend = get_storage_backend()
    logger.info("Watching %s for new scans...", config.SCAN_INBOX)
```

- [ ] **Step 5: Verify the module imports and `main()` fails fast on missing config**

Run: `python3 -c "import app.watcher; print('ok')"`
Expected: prints `ok` (import alone doesn't construct the backends).

Run: `python3 -m app.watcher`
Expected: raises `app.email.base.EmailError: Missing required env var for EMAIL_PROVIDER=smtp: SMTP_HOST` (still no `.env` filled in) — confirms `main()` now fails fast the same way `Config`'s old `_req()` used to, just at backend construction instead of import time.

- [ ] **Step 6: Commit**

```bash
git add app/watcher.py
git commit -m "feat: wire watcher up to pluggable email/storage backends"
```

---

### Task 9: `.env.example` and `.gitignore`

**Files:**
- Create: `.env.example`
- Create: `.gitignore`

**Interfaces:**
- Consumes: every config key from Task 1.

- [ ] **Step 1: Create `.env.example`**

```dotenv
# Copy this file to .env and fill in the values for your setup.
# Only the vars for your chosen EMAIL_PROVIDER and STORAGE_PROVIDER need
# real values — leave the others blank. See docs/SETUP.md for the full
# walkthrough for each provider.

# --- Filesystem paths (defaults are fine for the standard Pi layout) ---
# SCAN_INBOX=/srv/scans/inbox
# SCAN_PROCESSING=/srv/scans/processing
# SCAN_ARCHIVE=/srv/scans/archive
# SCAN_FAILED=/srv/scans/failed
# THUMBNAIL_DIR=/srv/scans/thumbnails
# DB_PATH=/srv/scans/dashboard.db

# --- Retention ---
# RETENTION_DAYS=30

# --- OCR ---
# OCR_JOBS=4
# OCR_LANGUAGE=eng
# BLANK_PAGE_THRESHOLD=0.995

# --- Who receives the scanned document ---
RECIPIENT_EMAIL=

# --- Email sending: choose ONE ---
# smtp  -> Gmail, Microsoft Live (personal), Apple iCloud Mail, cPanel
# graph -> Microsoft 365 with tenant admin (app-only, certificate auth)
EMAIL_PROVIDER=smtp

# SMTP settings (EMAIL_PROVIDER=smtp)
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_ADDRESS=

# Microsoft Graph settings (EMAIL_PROVIDER=graph and/or STORAGE_PROVIDER=onedrive)
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CERT_PATH=/opt/scan-pipeline/certs/graph-app.key
GRAPH_CERT_THUMBPRINT=
SEND_FROM_MAILBOX=

# --- Cloud storage: choose ONE (optional) ---
# onedrive -> Microsoft 365 (requires the Graph settings above)
# webdav   -> Nextcloud/ownCloud/any WebDAV-capable host
# none     -> skip cloud storage; the email attachment + local archive are enough
STORAGE_PROVIDER=none

ONEDRIVE_FOLDER_PATH=/Scanned Documents

WEBDAV_URL=
WEBDAV_USERNAME=
WEBDAV_PASSWORD=
WEBDAV_FOLDER_PATH=/ScannedDocuments

# --- Dashboard ---
# DASHBOARD_HOST=0.0.0.0
# DASHBOARD_PORT=5000
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
.venv/
venv/
__pycache__/
*.pyc
.env
*.key
*.crt
*.pem
*.pfx
msal_token_cache.json
.DS_Store
```

- [ ] **Step 3: Verify `.env.example` matches every key `Config` reads**

`Config` reads keys two ways — directly via `os.getenv("KEY", ...)` and
via the `_int("KEY", ...)` helper — so the check needs to catch both.
`.env.example` lists every key too, active (`KEY=value`) for
provider-relevant ones or commented (`# KEY=value`) for the
defaulted/optional ones — so strip a leading `# ` before comparing.

Run: `grep -oP '(?:os\.getenv|_int)\("\K[A-Z_]+' app/config.py | sort -u > /tmp/config-keys.txt; grep -oP '^#?\s*\K[A-Z_]+(?==)' .env.example | sort -u > /tmp/example-keys.txt; diff /tmp/config-keys.txt /tmp/example-keys.txt`
Expected: no output (every key `Config` reads appears in `.env.example`, and vice versa).

- [ ] **Step 4: Commit**

```bash
git add .env.example .gitignore
git commit -m "chore: add .env.example and .gitignore"
```

---

### Task 10: `scripts/install.sh`

**Files:**
- Create: `scripts/install.sh`

**Interfaces:**
- Consumes: `requirements.txt`, `scripts/samba-scan-share.conf`, `systemd/*.service`, `systemd/*.timer`, `.env.example` (all pre-existing or from Task 9).

- [ ] **Step 1: Create `scripts/install.sh`**

```bash
#!/usr/bin/env bash
# One-time (re-runnable) provisioning for the scan pipeline. Run this
# over SSH after first boot, from inside the cloned repo:
#
#   git clone https://github.com/AndyProsser/rpi-scanner-gateway.git
#   cd rpi-scanner-gateway
#   sudo ./scripts/install.sh
#
# Safe to re-run — each step checks whether it's already done, and it
# never overwrites an existing .env.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run this with sudo: sudo ./scripts/install.sh" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR=/opt/scan-pipeline
REAL_USER="${SUDO_USER:-$(whoami)}"

echo "==> Installing OS packages"
apt update
apt install -y python3-venv python3-pip samba tesseract-ocr ghostscript jbig2enc git rsync

echo "==> Service user and directories"
if ! id scanpipeline &>/dev/null; then
    useradd -r -s /usr/sbin/nologin scanpipeline
fi
mkdir -p /srv/scans/{inbox,processing,archive,failed,thumbnails}
chown -R scanpipeline:scanpipeline /srv/scans

echo "==> Deploying code to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
rsync -a --exclude '.git' --exclude 'venv' --exclude '.venv' "$REPO_DIR"/ "$INSTALL_DIR"/
cd "$INSTALL_DIR"

if [[ ! -d venv ]]; then
    python3 -m venv venv
fi
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "    Created .env from .env.example — fill it in before starting services."
fi

mkdir -p certs
chmod 700 certs
chown -R "$REAL_USER":"$REAL_USER" "$INSTALL_DIR"

echo "==> Samba share"
if ! grep -q '^\[scans\]' /etc/samba/smb.conf 2>/dev/null; then
    cat scripts/samba-scan-share.conf >> /etc/samba/smb.conf
    echo "    Added [scans] share to /etc/samba/smb.conf"
    echo "    Run 'sudo smbpasswd -a scanner' to set the share password, then"
    echo "    'sudo systemctl restart smbd'."
else
    echo "    [scans] share already present, skipping"
fi

echo "==> systemd units"
cp systemd/*.service systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable scan-watcher.service scan-dashboard.service retention-cleanup.timer

cat <<'EOF'

==> Install steps done. Before starting the services:

  1. sudo smbpasswd -a scanner        (if not done above)
  2. sudo systemctl restart smbd
  3. Edit /opt/scan-pipeline/.env — pick EMAIL_PROVIDER / STORAGE_PROVIDER
     and fill in the matching settings (see docs/SETUP.md)
  4. Configure the Brother panel's Scan-to-Network-Folder shortcut
  5. Install Tailscale: curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up

Then start everything:

  sudo systemctl start scan-watcher scan-dashboard
  sudo systemctl start retention-cleanup.timer

Check it's alive:

  sudo systemctl status scan-watcher
  sudo journalctl -u scan-watcher -f
EOF
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/install.sh`

- [ ] **Step 3: Verify with a shell syntax check** (this environment has no Pi to fully run it against; a syntax check plus a manual re-read of the idempotency guards is the available verification)

Run: `bash -n scripts/install.sh`
Expected: no output (valid syntax). Full execution is validated in Task 11 by actually running it on a Pi during the real deployment.

- [ ] **Step 4: Commit**

```bash
git add scripts/install.sh
git commit -m "feat: add idempotent install.sh provisioning script"
```

---

### Task 11: Rewrite `docs/SETUP.md`, delete duplicate root `SETUP.md`

**Files:**
- Delete: `SETUP.md` (root — byte-identical duplicate of `docs/SETUP.md`, both currently tracked)
- Modify: `docs/SETUP.md` (full rewrite)

**Interfaces:**
- Consumes: `scripts/install.sh` (Task 10), `.env.example` (Task 9), the provider list from the spec.

- [ ] **Step 1: Delete the duplicate**

Run: `git rm SETUP.md`

- [ ] **Step 2: Rewrite `docs/SETUP.md`**

```markdown
# Setup Guide

One-time setup, in order. Budget ~2 hours including OCR test runs — most
of that is choosing and configuring an email/storage provider, not the
Pi itself.

## 0. Flash the SD card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Before
writing, open **OS Customisation** (the gear icon, or Ctrl+Shift+X) and
set:

- **Hostname** — e.g. `scanner-pi`
- **Enable SSH** — with your public key (preferred) or a password
- **Username/password** for the default account
- **Wi-Fi** — only if you're not using Ethernet
- **Locale/timezone**

> **Why not cloud-init?** Raspberry Pi OS's cloud-init support has been
> unreliable in practice — the NoCloud datasource sometimes just isn't
> read on first boot, silently leaving you with no SSH access and no
> way in short of re-flashing. Imager's OS Customisation uses a
> different, simpler mechanism (it writes a `firstrun.sh` directly into
> the image) that doesn't depend on that service. Use it instead of
> hand-rolling a cloud-init `user-data` file.

Write the image, boot the Pi, and give it a minute or two to come up.

## 1. SSH in and run the installer

```bash
ssh <user>@<hostname>.local
git clone https://github.com/AndyProsser/rpi-scanner-gateway.git
cd rpi-scanner-gateway
sudo ./scripts/install.sh
```

This installs the OS packages (Samba, `tesseract-ocr`, `ghostscript`,
`jbig2enc`, Python), creates the `scanpipeline` service user and
`/srv/scans/*` directories, deploys the code to `/opt/scan-pipeline` with
a Python virtualenv, copies `.env.example` to `.env` (only if `.env`
doesn't already exist — safe to re-run), adds the Samba share, and
installs (but does not start) the systemd units. It prints what's left
to do when it finishes.

`jbig2enc` isn't always packaged for ARM on Raspberry Pi OS — if that
`apt install` step fails partway on it, `ocrmypdf` will silently skip
`--jbig2-lossy` and fall back to standard compression later. Not a
blocker, just slightly bigger files.

## 2. Finish the Samba share

```bash
sudo smbpasswd -a scanner        # pick a password, note it down
sudo systemctl restart smbd
```

Test from your Mac/PC before touching the Brother:
`smb://<pi-ip-or-hostname>/scans` — connect as user `scanner`, confirm
you can drop a test PDF in.

## 3. Brother panel — Scan to Network Folder shortcut

On the MFP touchscreen (menu wording varies slightly by model):

1. **Settings → Network → Scan to Network Folder** (or via the
   web-based management page at the printer's IP, under Scan → Scan to
   FTP/Network)
2. Add a new profile:
   - **Network Folder Path:** `\\<pi-ip>\scans` (or `//<pi-ip>/scans`
     depending on model UI)
   - **Username:** `scanner`
   - **Password:** the one from step 2
   - **File name:** something predictable, e.g. `Scan`
   - **File type:** PDF
   - **Resolution:** 300 dpi (600dpi roughly doubles OCR time and file
     size for no real quality benefit on text documents)
   - **Color:** Black & White or Grey for contracts/disclosures —
     smaller files, faster OCR. Only use Color if you're scanning
     something with color content that matters.
3. Assign it to a **Shortcut button** on the home screen, named
   something the person scanning will recognize.
4. Test: scan a page, confirm it lands in `/srv/scans/inbox` on the Pi.

## 4. Choose and configure email sending

Set `EMAIL_PROVIDER` in `/opt/scan-pipeline/.env` to `smtp` or `graph`.

### `smtp` — Gmail, Microsoft Live (personal), Apple iCloud Mail, cPanel

All of these speak standard SMTP with an app password. Generate an app
password from the provider (not your normal login password — none of
these will accept that for SMTP from a script), then fill in:

```dotenv
EMAIL_PROVIDER=smtp
SMTP_USERNAME=<your address>
SMTP_PASSWORD=<the app password>
SMTP_FROM_ADDRESS=<your address>
RECIPIENT_EMAIL=<who receives the scans>
```

| Provider | SMTP_HOST | SMTP_PORT | App password |
|---|---|---|---|
| Gmail | `smtp.gmail.com` | `587` | Google Account → Security → App passwords (requires 2-Step Verification on) |
| Microsoft Live (personal) | `smtp.office365.com` | `587` | account.live.com → Security → App passwords |
| Apple iCloud Mail | `smtp.mail.me.com` | `587` | appleid.apple.com → Sign-In and Security → App-Specific Passwords |
| cPanel-hosted email | check your host's docs — often `mail.<yourdomain>` | `587` or `465` | usually your mailbox password works directly; check with your host if not |

Double-check exact host/port values against the provider's current
documentation — mail providers do change these occasionally.

### `graph` — Microsoft 365 (you have tenant Global Admin)

App-only auth via a certificate (not a client secret — certs don't come
with a fixed 24-month expiry reminder hanging over you, and are
Microsoft's recommended pattern for unattended apps).

1. **Generate a certificate** (on the Pi, or anywhere — only the private
   key needs to end up on the Pi):

   ```bash
   openssl req -x509 -newkey rsa:2048 -keyout graph-app.key -out graph-app.crt \
       -days 730 -nodes -subj "/CN=rpi-scanner-gateway"
   openssl x509 -in graph-app.crt -noout -fingerprint -sha1
   ```

   Note the fingerprint output (strip the colons) — that's
   `GRAPH_CERT_THUMBPRINT`.

2. **Entra admin center → App registrations → New registration**
   - Name: `RPi Scanner Gateway`
   - Supported account types: single tenant
3. **Certificates & secrets → Certificates → Upload certificate** —
   upload `graph-app.crt`.
4. **API permissions → Add a permission → Microsoft Graph → Application
   permissions:**
   - `Mail.Send`
   - `Files.ReadWrite.All` (only needed if you're also using
     `STORAGE_PROVIDER=onedrive` — see step 5)
   - Click **Grant admin consent**.
5. Copy **Application (client) ID** → `GRAPH_CLIENT_ID`, and
   **Directory (tenant) ID** → `GRAPH_TENANT_ID`.
6. Move `graph-app.key` onto the Pi:

   ```bash
   scp graph-app.key <user>@<pi-hostname>:/opt/scan-pipeline/certs/
   ssh <user>@<pi-hostname> 'sudo chown scanpipeline:scanpipeline /opt/scan-pipeline/certs/graph-app.key && sudo chmod 600 /opt/scan-pipeline/certs/graph-app.key'
   ```

7. Fill in `.env`:

   ```dotenv
   EMAIL_PROVIDER=graph
   GRAPH_TENANT_ID=<from step 5>
   GRAPH_CLIENT_ID=<from step 5>
   GRAPH_CERT_PATH=/opt/scan-pipeline/certs/graph-app.key
   GRAPH_CERT_THUMBPRINT=<from step 1>
   SEND_FROM_MAILBOX=scanner@yourtenant.com
   RECIPIENT_EMAIL=<his/her real M365 address>
   ```

### Lock down `Mail.Send` (important, `graph` only)

App-only `Mail.Send` without scoping lets this app send as *any* mailbox
in the tenant. Restrict it to just the sending mailbox via Exchange
Online PowerShell:

```powershell
Connect-ExchangeOnline
New-ApplicationAccessPolicy -AppId "<GRAPH_CLIENT_ID>" `
    -PolicyScopeGroupId "scanner@yourtenant.com" `
    -AccessRight RestrictAccess `
    -Description "Scan pipeline - restrict to scanner mailbox only"
```

If `scanner@yourtenant.com` is a real mailbox (recommended — either a
licensed shared mailbox or a small standalone license), it needs
`Mail.Send` rights on itself, which the policy above grants exclusively
to this app.

## 5. Choose and configure cloud storage (optional)

Set `STORAGE_PROVIDER` in `.env` to `onedrive`, `webdav`, or `none`. This
is independent of your email choice — you can mix and match.

### `onedrive`

Reuses the same Entra app registration from step 4's `graph` section —
if you're not otherwise using `EMAIL_PROVIDER=graph`, follow that
section's steps 1–6 anyway (you still need the app registration and
cert), just add the `Files.ReadWrite.All` permission and set:

```dotenv
STORAGE_PROVIDER=onedrive
ONEDRIVE_FOLDER_PATH=/Scanned Documents
```

`Files.ReadWrite.All` app-only writes to `/users/{RECIPIENT_EMAIL}/drive/...`
— the recipient's own OneDrive, not the sending mailbox's.

### `webdav`

Any Nextcloud/ownCloud instance, or a cPanel host that exposes WebDAV:

```dotenv
STORAGE_PROVIDER=webdav
WEBDAV_URL=https://your-nextcloud.example.com/remote.php/dav/files/<user>
WEBDAV_USERNAME=<user>
WEBDAV_PASSWORD=<app password if your host supports one, else your password>
WEBDAV_FOLDER_PATH=/ScannedDocuments
```

### `none`

Leave `STORAGE_PROVIDER=none`. The email attachment plus the local
30-day archive (`/srv/scans/archive`) are the only copies — fine if you
don't need an off-Pi backup beyond what's in the recipient's inbox.

## 6. Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Note the MagicDNS name it's assigned (e.g. `scanner-pi`) — that's what
you'll use to reach the dashboard: `http://scanner-pi:5000`.

If you want the person scanning to check the simple view themselves,
install Tailscale on their device too and share the Pi node with their
account (node sharing is included free — see Tailscale's Personal
plan). Otherwise this stays just for you.

## 7. Start the services

```bash
sudo systemctl start scan-watcher scan-dashboard
sudo systemctl start retention-cleanup.timer
```

Check it's alive:

```bash
sudo systemctl status scan-watcher
sudo journalctl -u scan-watcher -f     # tail logs live while testing
```

## 8. End-to-end test

1. Scan a multi-page test document (include a deliberately blank page)
   from the Brother using the shortcut button.
2. Watch `journalctl -u scan-watcher -f` — you should see it move
   through received → OCR → uploading (if a storage provider is
   configured) → done.
3. Check the dashboard at `http://scanner-pi:5000`.
4. Confirm the email arrived, the attachment opens and is searchable
   (Cmd+F for a word you know is in the doc), and — if you configured
   one — the cloud storage link works.
5. Check `/srv/scans/archive` has the local backup copy.

## Ongoing maintenance

- Logs: `journalctl -u scan-watcher` / `-u scan-dashboard`
- If a scan fails, it's visible on `/details` with the error message,
  and the original is preserved in `/srv/scans/failed` for
  reprocessing.
- If you're on `EMAIL_PROVIDER=graph` or `STORAGE_PROVIDER=onedrive`:
  the certificate you generated in step 4 expires whenever you set it
  to (`-days 730` above = 2 years) — put a reminder in your own
  calendar, not the recipient's.
- If you're on SMTP with an app password: those don't expire on a fixed
  schedule the way Entra certs/secrets do, but can be revoked by the
  provider if you change your main account password — re-generate if
  sending suddenly stops working.
```

- [ ] **Step 3: Verify structure**

Run: `grep -c '^## ' docs/SETUP.md`
Expected: `9` (one `##` heading per numbered section, 0 through 8, matching the walkthrough above).

- [ ] **Step 4: Commit**

```bash
git add SETUP.md docs/SETUP.md
git commit -m "docs: rewrite SETUP.md for multi-provider setup and cloud-init-free provisioning

Removes the duplicate root SETUP.md, restructures around install.sh,
and adds per-provider (Gmail/Live/iCloud/cPanel/M365) walkthroughs plus
the OneDrive/WebDAV/none storage choice."
```

---

### Task 12: `README.md` rebrand

**Files:**
- Modify: `README.md` (full rewrite)

**Interfaces:**
- Consumes: nothing new; links to `docs/SETUP.md` (Task 11) and `CONTRIBUTING.md` (Task 14).

- [ ] **Step 1: Rewrite `README.md`**

```markdown
# RPi Scanner Gateway

Turn a Raspberry Pi and a network-scan-capable MFP into a hands-off
document pipeline: SMB scan-to-folder → strip blank pages → OCR +
compress → local 30-day backup → optional cloud storage upload → email
notification. Plus a tiny dashboard to monitor it, meant to be reached
over a private network (e.g. Tailscale) rather than exposed publicly.

Built to run headless and unattended on a Pi dedicated to this one job,
and to be usable by someone with basic IT skills as a starting point for
their own setup — not just a single deployment.

## Choosing providers

Two independent choices, set in `.env` — see
[docs/SETUP.md](docs/SETUP.md) for the full walkthrough of each:

| | Options |
|---|---|
| **Sending email** (`EMAIL_PROVIDER`) | `smtp` — Gmail, Microsoft Live (personal), Apple iCloud Mail, cPanel &nbsp;·&nbsp; `graph` — Microsoft 365 (requires tenant Global Admin) |
| **Cloud storage** (`STORAGE_PROVIDER`, optional) | `onedrive` — Microsoft 365 &nbsp;·&nbsp; `webdav` — Nextcloud/ownCloud/any WebDAV host &nbsp;·&nbsp; `none` — email attachment + local archive only |

## Structure

```text
app/
  config.py            # all settings, loaded from .env
  db.py                 # SQLite job tracking (single table, WAL mode)
  blank_pages.py         # pre-OCR blank page detection/removal
  ocr.py                  # ocrmypdf wrapper + thumbnail generation
  graph_auth.py            # shared MSAL certificate auth for the two Graph backends
  email/                    # pluggable email sending (see get_email_sender())
    base.py, smtp_sender.py, graph_sender.py
  storage/                   # pluggable cloud storage, optional (see get_storage_backend())
    base.py, onedrive.py, webdav.py
  watcher.py                  # orchestrator — watches inbox, runs the pipeline
  dashboard.py                  # Flask app (simple view + /details)
  templates/, static/             # dashboard HTML/CSS
scripts/
  install.sh                       # idempotent provisioning script — run once over SSH
  retention_cleanup.py               # deletes local backups older than 30 days
  samba-scan-share.conf                # Samba config snippet for the SMB scan target
systemd/                                # service + timer units for all three processes
docs/SETUP.md                             # full setup walkthrough — start here
```

## Setup

Follow **[docs/SETUP.md](docs/SETUP.md)** top to bottom — flashing the
SD card, running the installer, Brother panel configuration, choosing
and configuring an email/storage provider, Tailscale, and starting the
services.

## Status flow

Each scan moves through: `received → ocr_running → uploading → done`
(or `failed`, with the error preserved and visible on `/details`, and
the original file kept in `/srv/scans/failed` for reprocessing).
`uploading` is skipped when `STORAGE_PROVIDER=none`.

## Example deployment

This repo runs a real pipeline for a family member's Brother MFP → Pi
3B setup: Microsoft 365 Graph for both sending and OneDrive storage
(a family M365 tenant with Global Admin access), reachable over
Tailscale for remote monitoring. It's a normal instance of the
`graph`/`onedrive` provider combination described above, not special
cased in the code.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and how to
add a new email or storage provider.

## License

MIT — see [LICENSE](LICENSE).
```

- [ ] **Step 2: Verify links resolve**

Run: `grep -oP '\[.*?\]\(\K[^)]+' README.md`
Expected: `docs/SETUP.md`, `CONTRIBUTING.md`, `LICENSE` — confirm each exists (`docs/SETUP.md` from Task 11, `LICENSE` pre-existing, `CONTRIBUTING.md` from Task 14 — created after this task, so this check is repeated once more at the end of Task 14).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: rebrand README as a generic public template

Replaces uncle-specific framing with a provider-choice table and an
'Example deployment' section describing that use case as one instance
of the generic setup, not the framing for the whole doc."
```

---

### Task 13: `CLAUDE.md`

**Files:**
- Create: `CLAUDE.md`

**Interfaces:**
- Consumes: the finished architecture from Tasks 1–8 (describes it; written last among the code-adjacent tasks so it reflects the real file layout).

- [ ] **Step 1: Create `CLAUDE.md`**

```markdown
# CLAUDE.md

## What this is

A headless document pipeline: a network scanner (typically a Brother MFP
using "Scan to Network Folder") drops PDFs into an SMB share on a
Raspberry Pi. A watcher service strips blank pages, runs OCR +
compression, keeps a local 30-day archive, optionally uploads to cloud
storage, and emails the result. A small Flask dashboard shows job
status.

Designed to run unattended on a dedicated Pi, and to be usable by
someone with basic IT skills as a public template — not just the
author's own deployment (see README's "Example deployment" for that).

## Architecture

Pipeline, in `app/watcher.py`'s `process_file()`: received → strip blank
pages (`app/blank_pages.py`) → OCR + compress (`app/ocr.py`, via
`ocrmypdf`) → thumbnail → local archive copy → optional cloud storage
upload → email → done (or `failed`, with the original preserved in
`SCAN_FAILED` and the error visible on `/details`). Every step updates
the SQLite job row (`app/db.py`) so the dashboard reflects live state.

### Provider abstraction

Email and storage are separately pluggable, selected via `.env`:

- `app/email/` — `EmailSender` ABC (`base.py`), `get_email_sender()`
  factory (`__init__.py`) reads `EMAIL_PROVIDER`. Backends:
  `smtp_sender.py` (generic SMTP — Gmail/Microsoft Live/Apple
  iCloud/cPanel), `graph_sender.py` (M365 Graph, app-only, cert auth).
- `app/storage/` — same shape: `StorageBackend` ABC, `get_storage_backend()`
  reads `STORAGE_PROVIDER` and can return `None` (no cloud storage
  step). Backends: `onedrive.py` (M365 Graph), `webdav.py` (generic
  WebDAV).
- `app/graph_auth.py` — MSAL certificate-based token acquisition shared
  by `graph_sender.py` and `onedrive.py` (both are Graph API calls
  under the same app registration).

**Adding a new backend:** subclass `EmailSender` or `StorageBackend`,
implement the one abstract method, register it in the relevant
`__init__.py` factory, document its `.env` keys in `.env.example` and
`docs/SETUP.md`. Each backend validates its own required env vars in
`__init__` — don't add validation to `app/config.py` itself, since only
the selected provider's vars are actually required.

### Config

`app/config.py` is a single `Config` class reading everything from
`.env` (via `python-dotenv`) with defaults for paths/tuning, blank
strings for anything provider-specific. It intentionally does not
validate — that's each backend's job (see above).

### Deployment model

Three systemd units (`systemd/`): `scan-watcher` (the pipeline),
`scan-dashboard` (Flask, bind `0.0.0.0:5000`, meant to be reached over
Tailscale rather than exposed publicly), `retention-cleanup.timer`
(daily, deletes local archive copies older than `RETENTION_DAYS` —
never touches the email or cloud storage copies, those are the durable
ones). All run as the unprivileged `scanpipeline` service user.
`scripts/install.sh` is the provisioning entry point — see
`docs/SETUP.md` for the full walkthrough, including why it avoids
cloud-init for first boot.

## Conventions

- Each backend module defines its own error class (`EmailError`,
  `StorageError`, `OcrError`) rather than letting library exceptions
  leak; `watcher.py` catches the known set plus a bare `Exception`
  fallback so failures always land on the `failed` status with a
  preserved original file rather than crashing the watcher.
- Paths are `pathlib.Path` throughout, not strings.
- No ORM — `app/db.py` is a thin `sqlite3` wrapper (WAL mode, one
  table).

## Dev commands

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env   # point SCAN_INBOX etc at a local temp dir to test off-Pi
./venv/bin/python -m app.watcher      # pipeline
./venv/bin/python -m app.dashboard    # dashboard on :5000
```

## Testing

There is no automated test suite. Verify changes by running the module
locally (import errors surface immediately) and, for anything touching
an email/storage backend, a real smoke test against a live account —
see `CONTRIBUTING.md`.
```

- [ ] **Step 2: Verify no stale references**

Run: `grep -n "UNCLE_EMAIL\|GRAPH_CLIENT_SECRET\|app.graph\b" CLAUDE.md`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add CLAUDE.md"
```

---

### Task 14: `CONTRIBUTING.md`

**Files:**
- Create: `CONTRIBUTING.md`

**Interfaces:**
- Consumes: nothing new; referenced by `README.md` (Task 12) and `CLAUDE.md` (Task 13).

- [ ] **Step 1: Create `CONTRIBUTING.md`**

```markdown
# Contributing

Issues and PRs welcome — this started as a personal project but is
meant to be reusable.

## Development setup

```bash
git clone https://github.com/AndyProsser/rpi-scanner-gateway.git
cd rpi-scanner-gateway
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
```

You don't need a Raspberry Pi to work on this. Point the `SCAN_*` paths
in `.env` at a local directory (e.g. `/tmp/scans`) and run the pieces
directly:

```bash
mkdir -p /tmp/scans/{inbox,processing,archive,failed,thumbnails}
./venv/bin/python -m app.watcher      # drop a PDF in the inbox dir to trigger it
./venv/bin/python -m app.dashboard    # http://localhost:5000
```

`ocrmypdf`, `tesseract-ocr`, and `ghostscript` need to be installed
locally for the OCR step to run — see `docs/SETUP.md` step 1 for the
package list (same packages, any Debian/Ubuntu-like OS, not
Pi-specific).

## Adding a new email or storage provider

Both are a single abstract method:

- **Email:** subclass `app.email.base.EmailSender`, implement
  `send(subject, body_html, attachment_path=None)`, register it in
  `app.email.get_email_sender()`.
- **Storage:** subclass `app.storage.base.StorageBackend`, implement
  `upload(local_path, filename) -> str` (return a link), register it in
  `app.storage.get_storage_backend()`.

Validate the backend's required `.env` vars in its own `__init__`
(raise `EmailError`/`StorageError`), not in `app/config.py`. Document
the new vars in `.env.example` and add a setup subsection to
`docs/SETUP.md`.

## Code style

Ruff for linting/formatting (`.vscode/` runs it on save; `ruff check .`
/ `ruff format .` from the command line otherwise). No strong opinions
beyond what Ruff enforces — match the surrounding code.

## Testing / verification

There's no automated test suite (see `CLAUDE.md`). Before opening a PR:

- Run `python -m app.watcher` / `python -m app.dashboard` locally to
  confirm no import errors.
- If you touched an email or storage backend, smoke-test it against a
  real account for that provider — there's no mock/sandbox mode.
- Mention in the PR description what you tested and how.
```

- [ ] **Step 2: Verify README's links now all resolve**

Run: `for f in docs/SETUP.md CONTRIBUTING.md LICENSE; do test -f "$f" && echo "ok: $f" || echo "MISSING: $f"; done`
Expected: `ok: docs/SETUP.md`, `ok: CONTRIBUTING.md`, `ok: LICENSE`.

- [ ] **Step 3: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: add CONTRIBUTING.md"
```

---

### Task 15: `.vscode/` settings and extension recommendations

**Files:**
- Create: `.vscode/settings.json`
- Create: `.vscode/extensions.json`

**Interfaces:**
- Consumes: nothing.

- [ ] **Step 1: Create `.vscode/settings.json`**

```json
{
    "python.defaultInterpreterPath": "${workspaceFolder}/venv/bin/python",
    "python.analysis.typeCheckingMode": "basic",
    "editor.formatOnSave": true,
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.codeActionsOnSave": {
            "source.organizeImports": "explicit",
            "source.fixAll.ruff": "explicit"
        }
    },
    "search.exclude": {
        "venv": true,
        "**/__pycache__": true
    }
}
```

- [ ] **Step 2: Create `.vscode/extensions.json`**

```json
{
    "recommendations": [
        "ms-python.python",
        "ms-python.vscode-pylance",
        "charliermarsh.ruff",
        "wholroyd.jinja",
        "foxundermoon.shell-format",
        "timonwong.shellcheck",
        "coolbear.systemd-unit-file"
    ]
}
```

- [ ] **Step 3: Verify both are valid JSON**

Run: `python3 -c "import json; json.load(open('.vscode/settings.json')); json.load(open('.vscode/extensions.json')); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add .vscode/
git commit -m "chore: add .vscode settings and extension recommendations"
```

---

## Post-plan manual verification (not a task — do this once all 15 are done)

The plan's per-task steps are import/syntax checks; they can't exercise
a real Graph app, SMTP account, or Pi. Before calling this done for the
actual uncle deployment:

1. Run `scripts/install.sh` on a freshly flashed Pi end-to-end.
2. Fill in `.env` for `EMAIL_PROVIDER=graph` + `STORAGE_PROVIDER=onedrive`
   against the real family M365 tenant, per `docs/SETUP.md` section 4/5.
3. Run the section 8 end-to-end test in `docs/SETUP.md`.
4. Separately, smoke-test `EMAIL_PROVIDER=smtp` against at least one of
   Gmail/iCloud/Outlook to confirm the generic path works for the
   public-template audience, not just the Graph path this deployment
   uses.
