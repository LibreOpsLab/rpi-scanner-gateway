# RPi MFP Scanning Pipeline

Printer MFP → SMB scan-to-folder on a Raspberry Pi 3B → strip blank pages →
OCR + compress → local 30-day backup → OneDrive upload (optional) → email
notification (optional). Plus a dashboard to view, download, and manage
scans, reachable over Tailscale — the only required step if you skip both
optional ones.

## Structure

```text
app/
  config.py                  # all settings, loaded from .env
  db.py                      # SQLite job tracking (single table, WAL mode)
  blank_pages.py             # pre-OCR blank page detection/removal
  ocr.py                     # ocrmypdf wrapper + thumbnail generation
  graph.py                   # Microsoft Graph: OneDrive upload only
  graph_auth.py              # shared certificate-based Graph token acquisition
  email/                     # EmailSender backends (SMTP, Graph), see app/email/base.py
  watcher.py                 # orchestrator — watches inbox, runs the pipeline
  dashboard.py               # Flask app (simple view + /details)
  templates/, static/        # dashboard HTML/CSS
scripts/
  retention_cleanup.py       # deletes local backups older than 30 days
  samba-scan-share.conf      # Samba config snippet for the SMB scan target
systemd/                     # service + timer units for all three processes
avahi/                        # LAN mDNS service advertisement (no Tailscale needed)
docs/SETUP.md                # full one-time setup walkthrough — start here
```

## Setup

Follow **[docs/SETUP.md](docs/SETUP.md)** top to bottom — it covers the Pi
OS packages, Samba share, Printer panel configuration, Graph app
registration (with the Mail.Send scoping you'll want as tenant admin),
Tailscale, and the systemd services.

## Status flow

Each scan moves through: `received → ocr_running → uploading → done`
(or `failed`, with the error preserved and visible on `/details`, and the
original file kept in `/srv/scans/failed` for reprocessing).
