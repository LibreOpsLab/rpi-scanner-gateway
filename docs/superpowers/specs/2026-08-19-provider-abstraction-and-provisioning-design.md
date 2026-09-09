# Provider abstraction, headless provisioning, and public-release cleanup

Date: 2026-08-19
Status: Approved, ready for implementation planning

## Context

`rpi-scanner-gateway` is a working pipeline (Printer MFP → SMB → blank-page
strip → OCR → local archive → OneDrive → email) built for one deployment
(Andy's uncle, M365 tenant, tenant Global Admin access). It's about to serve
two purposes at once:

1. A real deployment for the uncle's MFP.
2. A public template other people — with a range of email providers and
   no tenant admin rights — can clone and run.

Today the code only speaks Microsoft Graph (client-secret app-only auth) for
both sending mail and storing a cloud copy, the docs are uncle-specific, the
repo has a duplicate `SETUP.md`, no `.gitignore`/`.env.example`, and the
setup guide leans on cloud-init for headless first boot — which the user has
found unreliable in practice. This spec covers the changes needed to make
the repo generically usable while keeping the uncle deployment as a
supported example.

## Goals

- Support sending mail via Gmail, Microsoft 365 (personal "Live"), Apple
  iCloud Mail, and cPanel-hosted email, in addition to M365 Graph.
- Support optional cloud storage via M365 (OneDrive/Graph) or generic
  WebDAV (Nextcloud/ownCloud/cPanel-provided), or no cloud storage at all.
- Upgrade the M365 Graph path from a client secret to certificate-based
  auth (Global Admin creates the app + cert, per the user's stated target).
- Replace the cloud-init-dependent first-boot flow with a provisioning path
  that doesn't depend on the flaky cloud-init datasource.
- Generalize branding/config so the repo reads as a public template, with
  the uncle deployment as a documented example, not the framing.
- Add `CLAUDE.md`, `.vscode/` config, `.gitignore`, `.env.example`, and fix
  the duplicate `SETUP.md`.

## Non-goals

- Google Drive / Dropbox storage backends (both need the end user to
  register their own OAuth app + consent screen — comparable burden to the
  M365 Graph app, multiplied per provider; WebDAV covers "bring your own
  storage" generically instead).
- A test suite (repo has none today; out of scope for this pass, but
  `CLAUDE.md` will note the gap).
- Real cloud-init NoCloud debugging (explicitly rejected in favor of
  bypassing it — see Provisioning section).
- Delegated (interactive) Graph auth. The existing `GRAPH_TOKEN_CACHE_PATH`
  / delegated-flow comment in `config.py` was never implemented and is
  dead scaffolding; this spec removes it. App-only is the only Graph mode
  needed for an unattended Pi.

## Architecture: email + storage as independent interfaces

Two selectable, independent backends — a user might pair Gmail SMTP with
WebDAV, or M365 Graph for both, or SMTP with no storage at all.

```text
app/
  email/
    __init__.py      # get_email_sender() -> EmailSender, from config.EMAIL_PROVIDER
    base.py           # EmailSender ABC: send(subject, body_html, attachment_path=None)
    smtp_sender.py     # stdlib smtplib + STARTTLS/SSL
    graph_sender.py     # Graph /sendMail, cert-authed
  storage/
    __init__.py      # get_storage_backend() -> StorageBackend | None, from config.STORAGE_PROVIDER
    base.py           # StorageBackend ABC: upload(local_path, filename) -> str (link)
    onedrive.py        # Graph OneDrive upload, cert-authed (moved from graph.py)
    webdav.py           # generic WebDAV PUT via requests + basic auth
  graph_auth.py       # shared MSAL cert-based token acquisition (used by graph_sender + onedrive)
```

`app/graph.py` is removed; its two responsibilities split into
`app/email/graph_sender.py` and `app/storage/onedrive.py`, sharing token
acquisition via `app/graph_auth.py` instead of duplicating it.

### EmailSender

```python
class EmailSender(ABC):
    def send(self, subject: str, body_html: str, attachment_path: str | None = None) -> None: ...
```

- `smtp_sender.SmtpSender`: `smtplib.SMTP`/`SMTP_SSL` depending on port
  convention (465 → implicit SSL, else STARTTLS), authenticates with
  `SMTP_USERNAME`/`SMTP_PASSWORD` (an app password for Gmail/Apple/Live),
  attaches the PDF via `email.mime.multipart`. Covers Gmail, Microsoft
  Live (personal), Apple iCloud Mail, cPanel — same protocol, different
  host/port documented per provider in SETUP.md.
- `graph_sender.GraphSender`: today's Graph `/sendMail` call, moved
  as-is except token acquisition now goes through `graph_auth.py` (cert,
  not secret).

### StorageBackend

```python
class StorageBackend(ABC):
    def upload(self, local_path: str, filename: str) -> str: ...  # returns a link
```

- `onedrive.OneDriveStorage`: today's OneDrive upload logic (simple PUT
  under 4MB, resumable session above), moved as-is except token
  acquisition via `graph_auth.py`.
- `webdav.WebDavStorage`: `requests.put()` to
  `f"{WEBDAV_URL}/{WEBDAV_FOLDER_PATH}/{filename}"` with HTTP basic auth;
  raises `StorageError` on non-2xx. No new dependency — plain `requests`.
- `STORAGE_PROVIDER=none` → `get_storage_backend()` returns `None`;
  callers skip the upload step and omit the "saved to your cloud storage"
  line from the email body.

### Shared cert-based auth (`app/graph_auth.py`)

```python
def get_token(scopes: list[str] = ["https://graph.microsoft.com/.default"]) -> str
```

Builds `msal.ConfidentialClientApplication` with
`client_credential={"private_key": <GRAPH_CERT_PATH contents>, "thumbprint": GRAPH_CERT_THUMBPRINT}`
instead of a secret string. Both `graph_sender.py` and `onedrive.py` call
this instead of each doing their own `ConfidentialClientApplication`
construction (today's `graph.py` only had one consumer of this pattern;
splitting into two files without sharing it would duplicate the MSAL
boilerplate, so it's pulled out).

### Watcher changes

`app/watcher.py` replaces:

```python
from app.graph import upload_to_onedrive, send_email, GraphError
```

with:

```python
from app.email import get_email_sender, EmailError
from app.storage import get_storage_backend, StorageError
```

`process_file()` calls `get_storage_backend()` once at module load (or
per-call — module load is fine, config doesn't change at runtime), skips
the upload step and `onedrive_link` entirely when it's `None`, and the
email body template conditionally includes the storage-link paragraph.
The `except (OcrError, GraphError)` clause becomes
`except (OcrError, EmailError, StorageError)`.

The `db` schema's `onedrive_link` column is reused as a generic
`storage_link` — rename the column (SQLite `ALTER TABLE ... RENAME
COLUMN`, or since there's no migration system and no production data yet,
just rename it in `SCHEMA` and any references in `watcher.py`/
`dashboard.py`/templates). No live deployments exist yet, so no migration
path is needed — this is a pre-release rename.

## Configuration (`app/config.py`, `.env.example`)

New/changed keys:

```dotenv
# --- Email sending ---
EMAIL_PROVIDER=smtp              # smtp | graph
RECIPIENT_EMAIL=                  # replaces UNCLE_EMAIL

# SMTP (Gmail / Microsoft Live / Apple iCloud / cPanel)
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_ADDRESS=

# Graph (M365, app-only, certificate auth)
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CERT_PATH=/opt/scan-pipeline/certs/graph-app.key   # PEM private key, mode 600
GRAPH_CERT_THUMBPRINT=
SEND_FROM_MAILBOX=

# --- Cloud storage (optional) ---
STORAGE_PROVIDER=none             # onedrive | webdav | none
ONEDRIVE_FOLDER_PATH=/Scanned Documents

WEBDAV_URL=
WEBDAV_USERNAME=
WEBDAV_PASSWORD=
WEBDAV_FOLDER_PATH=/ScannedDocuments
```

Removed: `GRAPH_CLIENT_SECRET`, `GRAPH_TOKEN_CACHE_PATH`, `GRAPH_SCOPES`
(dead delegated-flow scaffolding), `UNCLE_EMAIL` (renamed).

`Config` validation: today's `_req()` raises at import time for anything
missing. Since required vars now depend on which providers are selected,
validation moves to each backend's `__init__` (e.g. `SmtpSender.__init__`
calls `_req("SMTP_HOST")` etc.), not module-level `Config` construction —
so picking `graph`/`smtp` and `onedrive`/`webdav`/`none` doesn't force
unrelated env vars to be present.

## Provisioning: bypass cloud-init

Root cause of the "cloud-init not read" problem: Raspberry Pi OS's
cloud-init support (the NoCloud-style datasource driving `firstrun`) has
been unreliable in the user's testing. Rather than debugging that
datasource, first boot uses **Raspberry Pi Imager's OS Customisation**
(gear icon in the Imager UI, or Ctrl+Shift+X), which writes hostname,
SSH key, user account, and Wi-Fi credentials directly into the image via
its own `firstrun.sh` mechanism — a different, simpler code path than
cloud-init that doesn't depend on the service that's been flaky.

New `scripts/install.sh`: bash, run once over SSH after first boot,
idempotent (safe to re-run after fixing a mistake). Responsibilities,
each individually guarded so a re-run skips completed steps:

1. `apt install` the OS packages (python3-venv, samba, tesseract-ocr,
   ghostscript, jbig2enc, git).
2. Create the `scanpipeline` service user and `/srv/scans/*` dirs
   (skip if user/dirs already exist).
3. Copy the repo (the directory the script is run from) to
   `/opt/scan-pipeline` via `rsync -a --exclude .git --exclude venv`,
   chown to the invoking user for editing, set up the venv, `pip
   install -r requirements.txt`.
4. Copy `.env.example` → `.env` only if `.env` doesn't already exist
   (never clobber a filled-in config on re-run).
5. Append the Samba share block from `scripts/samba-scan-share.conf`
   to `/etc/samba/smb.conf` only if not already present (grep for the
   `[scans]` marker first), `smbpasswd -a scanner` prompts interactively.
6. Install the systemd units (`cp` + `daemon-reload` + `enable`, but
   **not** `start` — `.env` isn't filled in yet at this point).
7. Print next steps: fill in `.env` per the provider you're using (link
   to SETUP.md sections), configure the Printer panel, install
   Tailscale, then `sudo systemctl start scan-watcher scan-dashboard`
   and enable the retention timer.

`docs/SETUP.md` is restructured around this: Step 0 is flashing with
Imager OS Customisation (with an explicit callout on *why*, referencing
the cloud-init unreliability), Step 1 is SSH in + clone + run
`install.sh`, and the remaining steps are the parts that genuinely need a
human at an external portal — Printer panel config, chosen email
provider's setup (subsection per provider: Gmail, Microsoft Live, Apple
iCloud, cPanel, and M365 Graph with the cert-generation walkthrough),
chosen storage provider's setup, Tailscale, then starting services and
the end-to-end test.

### M365 Graph cert walkthrough (replaces the client-secret steps)

```bash
openssl req -x509 -newkey rsa:2048 -keyout graph-app.key -out graph-app.crt \
    -days 730 -nodes -subj "/CN=rpi-scanner-gateway"
openssl x509 -in graph-app.crt -noout -fingerprint -sha1
```

Entra admin center → App registration → **Certificates & secrets →
Certificates → Upload certificate** (upload `graph-app.crt`); the
`-fingerprint -sha1` output (minus colons) is `GRAPH_CERT_THUMBPRINT`.
`graph-app.key` goes on the Pi at `GRAPH_CERT_PATH`, `chmod 600`, owned
by `scanpipeline`. Same API permissions and `New-ApplicationAccessPolicy`
mailbox-scoping as today — that part of the current SETUP.md is correct
and unchanged, only the credential type changes.

## Rebranding

- `README.md`: generic title (e.g. "RPi Scanner Gateway"), generic
  tagline describing the pipeline without "uncle" framing, keep a short
  "Example deployment" section describing the actual uncle use case as a
  worked example, not the framing for the whole doc.
- `UNCLE_EMAIL` → `RECIPIENT_EMAIL` throughout code, docs, `.env.example`.
- Service/repo names are already generic (`scan-watcher`,
  `rpi-scanner-gateway`) — no change needed there.
- `docs/SETUP.md` restructured as provider-agnostic with the uncle
  deployment noted only where genuinely illustrative (e.g. as one example
  in the provider table).

## Housekeeping

- Delete root `SETUP.md` (byte-identical duplicate of `docs/SETUP.md`,
  both currently tracked in git — keep the `docs/` copy since README
  already links there).
- Add `.gitignore`: `.venv/`, `__pycache__/`, `*.pyc`, `.env`, `*.key`,
  `*.crt`, `*.pem`.
- Add `.env.example` reflecting the full config surface above, with
  inline comments marking which vars apply to which
  `EMAIL_PROVIDER`/`STORAGE_PROVIDER` choice.
- Add `CLAUDE.md`: architecture map (pipeline stages, module layout
  including the new `email`/`storage` packages), the provider
  abstraction and how to add a new backend, dev commands (venv setup,
  running watcher/dashboard locally without a Pi), conventions (config
  pattern, error-class-per-module, systemd deployment model), and an
  explicit note that there's no test suite yet.
- Add `.vscode/settings.json` (Python interpreter → `./venv/bin/python`,
  format-on-save via Ruff, exclude `__pycache__`/`venv` from search) and
  `.vscode/extensions.json` recommending `ms-python.python`,
  `ms-python.vscode-pylance`, `charliermarsh.ruff`, a Jinja2 template
  extension, and a shell-script formatter/linter for `install.sh`.
- Add `CONTRIBUTING.md`: how to propose changes (issues/PRs against the
  public repo), the backend-interface pattern for adding a new
  `EmailSender`/`StorageBackend` (since that's the most likely community
  contribution — "add provider X"), code style (Ruff), and that changes
  touching the pipeline should be smoke-tested locally per the
  Development section below since there's no automated suite.
- Add a **Development setup** section (in `README.md` or split into
  `docs/DEVELOPMENT.md` if it grows long — decide during implementation
  based on length): cloning, creating the venv, installing
  `requirements.txt` on a non-Pi machine for editing/testing, running
  `app.watcher`/`app.dashboard` locally against a local directory instead
  of `/srv/scans` (override via `.env`), and the manual smoke-test
  approach from the Testing section above (no CI/automated suite exists,
  so this is what "verified" means pre-PR).

## Testing

No test suite exists in the repo today (confirmed — out of scope to add
one here). Verification for this change is manual:

- `python -m app.watcher` / `python -m app.dashboard` run locally without
  a Pi (paths default under `/srv/scans` via env override to a local
  temp dir) to confirm no import errors from the module split.
- Each new backend (`SmtpSender`, `GraphSender`, `OneDriveStorage`,
  `WebDavStorage`) gets a manual smoke test against a real account during
  implementation, since there's no mocking/test harness to exercise them
  automatically.
- `install.sh` is validated by actually running it on a freshly flashed
  Pi (the uncle deployment doubles as the real-world test).

## Open items for the implementation plan

- Exact SMTP host/port values to document per provider (Gmail
  `smtp.gmail.com:587`, Outlook/Live `smtp.office365.com:587` or
  `smtp-mail.outlook.com:587` depending on account type, iCloud
  `smtp.mail.me.com:587`, cPanel varies by host) — confirm current values
  during implementation via each provider's current documentation rather
  than relying on possibly-stale memory.
- Whether `GRAPH_CERT_PATH` should support a `.pfx`/PKCS12 bundle instead
  of separate key file, for users more comfortable with that format —
  default to PEM private key (simpler, no passphrase-on-pfx complexity)
  unless implementation reveals a strong reason otherwise.
