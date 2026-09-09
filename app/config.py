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
    EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "smtp")  # smtp | graph | none
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

    # --- Cloud storage ---
    STORAGE_PROVIDER = os.getenv("STORAGE_PROVIDER", "onedrive")  # onedrive | none

    # --- OneDrive (used when STORAGE_PROVIDER=onedrive) ---
    # ONEDRIVE_USER_EMAIL is the OneDrive upload target for upload_to_onedrive() in
    # app/graph.py (distinct from RECIPIENT_EMAIL above, which is who gets the email).
    # They may be the same address in a single-recipient deployment, but are kept
    # separate since a future storage backend or recipient could differ.
    ONEDRIVE_USER_EMAIL = os.getenv("ONEDRIVE_USER_EMAIL", "")
    ONEDRIVE_FOLDER_PATH = os.getenv("ONEDRIVE_FOLDER_PATH", "/Scanned Documents")

    # --- Dashboard ---
    DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    DASHBOARD_PORT = _int("DASHBOARD_PORT", 5000)
    # Optional HTTP Basic Auth password gating /settings only (the rest of the
    # dashboard stays open — see docs/SETUP.md). Empty/unset = no auth.
    DASHBOARD_SETTINGS_PASSWORD = os.getenv("DASHBOARD_SETTINGS_PASSWORD", "")

    @classmethod
    def ensure_dirs(cls):
        for d in [cls.SCAN_INBOX, cls.SCAN_PROCESSING, cls.SCAN_ARCHIVE,
                  cls.SCAN_FAILED, cls.THUMBNAIL_DIR, cls.DB_PATH.parent]:
            d.mkdir(parents=True, exist_ok=True)


config = Config()
