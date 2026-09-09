# CLAUDE.md — RPi Scanner Gateway Assistant Specification

## Purpose

Claude is the technical assistant for this repository: a hands-off document-scanning gateway
that runs on a dedicated Raspberry Pi. Its job is to help design, maintain, and reason about:

- The scan-to-email pipeline (`app/watcher.py` and everything it orchestrates)
- Blank-page stripping, OCR, and compression (`app/blank_pages.py`, `app/ocr.py`)
- Pluggable email delivery (`app/email/`) and (currently non-pluggable) cloud storage upload
  (`app/graph.py`)
- The monitoring/admin dashboard (`app/dashboard.py`)
- Retention cleanup, systemd deployment, Samba/avahi/Tailscale integration
- The in-flight provider-abstraction and public-release cleanup effort (see `.claude/intent.md`)

This directory (`.claude/`) also holds two files worth checking at the start of nontrivial work:

- [`.claude/memory.md`](memory.md) — durable, non-obvious facts and gotchas about this repo that
  aren't derivable from a quick read of the code.
- [`.claude/intent.md`](intent.md) — active, session-spanning threads, chiefly the
  provider-abstraction/provisioning/public-release-cleanup effort.

Tone, working style, and collaboration defaults are already set globally in
`~/.claude/CLAUDE.md` and apply here unmodified — this file only covers what's specific to this
project.

---

## What This Is

A single-purpose pipeline, not a general app: a printer's SMB "scan to network folder" feature
drops a PDF into `/srv/scans/inbox` on a Raspberry Pi 3B, and a `watchdog`-based service picks it
up and runs it through: strip blank pages → OCR + compress (`ocrmypdf`) → thumbnail → local
30-day archive copy → optional cloud upload (OneDrive today) → optional email notification. A
small Flask dashboard shows job status/history and lets the recipient view/download/delete scans
without needing email at all. Everything is designed to run unattended on one Pi, not
containerized, not clustered — see `README.md` and `docs/SETUP.md` for the full walkthrough.

## Architecture / Module Map

| Module | Role |
| --- | --- |
| `app/config.py` | Single `Config` class / `config` singleton, all settings loaded from `.env` via `python-dotenv`. Any new setting belongs here, mirrored in `.env.example`. |
| `app/db.py` | SQLite, no ORM — one `jobs` table (status machine: `received → ocr_running → uploading → done`/`failed`) plus a small `settings` key/value table, WAL mode (watcher writes, dashboard reads concurrently). |
| `app/blank_pages.py` | Pre-OCR blank-page detection (PyMuPDF render + white-pixel fraction) and removal (`pikepdf`). Keeps the original if *every* page is flagged blank (probable mis-scan) rather than producing an empty file. |
| `app/ocr.py` | `ocrmypdf` subprocess wrapper (`--jbig2-lossy --clean --optimize 3 --skip-text --deskew`) plus thumbnail generation. Resolves the `ocrmypdf` binary as a sibling of `sys.executable` because systemd's bare `PATH` doesn't include the venv's `bin/`. |
| `app/graph.py` | OneDrive upload only, via client-secret Graph auth (`msal.ConfidentialClientApplication` with `GRAPH_CLIENT_SECRET`). Email sending used to live here too; it moved to `app/email/graph_sender.py`, which uses certificate auth instead — `upload_to_onedrive()` has **not** been migrated to that yet (see `.claude/intent.md`). Called directly from `watcher.py`, not through a provider factory. |
| `app/graph_auth.py` | Shared certificate-based (app-only/client-credentials) Graph token acquisition. Currently only consumed by `app/email/graph_sender.py`. |
| `app/email/` | Pluggable `EmailSender` backends: `base.py` (ABC + `require_config()` helper), `smtp_sender.py`, `graph_sender.py`, dispatched by `get_email_sender()` in `__init__.py` keyed on `EMAIL_PROVIDER` (`smtp`/`graph`/`none` → `None`). This is the abstraction pattern any new email backend, and eventually a storage backend, should follow. |
| `app/watcher.py` | Orchestrator. `wait_until_stable()` polls file size to avoid grabbing a scan mid-SMB-write; `process_file()` runs the full pipeline, updating `db` status at every step, wrapped so failures preserve the original in `SCAN_FAILED` and always clean up the per-job work directory. |
| `app/dashboard.py` | Flask app: `/` (simple recipient view), `/details` (full history), `/thumb`, `/scans/<id>/view|download|delete`, `/jobs/clean-failed`, `/settings` (recipient email override + optional password gate + Settings > Maintenance apt/reboot actions), `/settings/maintenance/*`, `/help`. All routes open by default except the optional `/settings` password; every POST route is CSRF-protected via a double-submit cookie. |
| `app/system_stats.py` | Best-effort Pi temperature/throttle (`vcgencmd`), disk free, Tailscale status — every check degrades to `None` rather than raising. |
| `scripts/retention_cleanup.py` | Deletes local archive copies (not OneDrive, not email) older than `RETENTION_DAYS`; run daily via `systemd/retention-cleanup.timer`. |
| `scripts/samba-scan-share.conf`, `scripts/scanpipeline-maintenance.sudoers` | Samba share snippet for the SMB scan target; narrowly-scoped sudoers grant (exactly `apt-get update`, one specific `apt-get upgrade` invocation, `reboot`) backing the dashboard's Settings > Maintenance feature. |
| `systemd/*.service`, `systemd/*.timer` | Unit files for `scan-watcher`, `scan-dashboard`, `retention-cleanup.timer`. |
| `avahi/scanner-gateway.service` | LAN mDNS advertisement, independent of Tailscale MagicDNS. |

## Tech Stack

- Python 3, no framework beyond Flask 3 for the dashboard (no ORM — raw `sqlite3`).
- `watchdog` for filesystem events, `ocrmypdf`/`pikepdf`/`PyMuPDF`/`Pillow` for the OCR pipeline
  (with external binary dependencies: `ghostscript`, `jbig2`, `unpaper`, `pngquant`, `tesseract`
  — see `docs/SETUP.md` step 1 for why each is a hard requirement, not optional).
- `msal` for Microsoft Graph auth (both client-secret and certificate flows), `requests` for raw
  Graph HTTP calls, `python-dotenv` for config loading.
- `PyYAML` is in `requirements.txt` but not currently imported anywhere in `app/` or `scripts/` —
  unclear if it's a leftover or reserved for planned work (e.g. `scripts/install.sh`, not yet
  written). Don't assume it's load-bearing without checking.
- **No automated test suite exists in this repo.** Verification throughout `docs/SETUP.md` and
  the `docs/superpowers/plans/` files is manual/smoke-test based (run the pipeline end to end,
  check the dashboard, check logs). Don't assume `pytest` or similar tooling is present.

## Deployment Model

Single Raspberry Pi 3B, 64-bit Raspberry Pi OS (Bookworm+ required — `pikepdf`/`PyMuPDF`/`Pillow`
only ship aarch64 wheels; 32-bit falls back to slow/failure-prone source builds). Not
containerized, not HA — this is a single-purpose appliance running as the restricted
`scanpipeline` service account via three systemd units (`scan-watcher.service`,
`scan-dashboard.service`, `retention-cleanup.timer`), fed by a Samba share the printer scans
into, discoverable on the LAN via avahi and remotely via Tailscale. See `docs/SETUP.md` for the
full one-time setup sequence and its various Pi/systemd/Samba gotchas.

## Behavioural Rules

1. **Config is centralized and `.env`-driven.** New settings go in `app/config.py`'s `Config`
   class, mirrored in `.env.example` with the same commented-optional-vs-required convention
   already used there. Never hardcode a secret, host, or path that should be configurable.

2. **Never write real credentials into the repo.** `GRAPH_CERT_PATH` (a private key file),
   `GRAPH_CLIENT_SECRET`, `SMTP_PASSWORD`, and a populated `.env` must never be committed —
   `.env` is gitignored. When producing example configs or docs, use placeholders and point at
   the relevant `docs/SETUP.md` section instead of inventing plausible-looking secrets.

3. **Follow the `EmailSender` abstraction pattern for pluggable backends.** `app/email/base.py`'s
   ABC (`send()`, self-validating config in `__init__`) plus `get_email_sender()`'s
   provider-keyed dispatch in `app/email/__init__.py` is the established shape. Cloud storage
   does **not** have this yet — `app/graph.py`'s `upload_to_onedrive()` is called directly from
   `watcher.py` with no factory or interface. Don't assume a `StorageBackend` abstraction exists
   until it's actually built (see `.claude/intent.md`).

4. **OCR pipeline flags and external binaries are tightly coupled.** The exact `ocrmypdf` flag
   set in `app/ocr.py` (`--jbig2-lossy --clean --optimize 3` etc.) depends on `jbig2`, `unpaper`,
   `pngquant`, and `ghostscript` being installed on the Pi — missing any one fails every OCR job
   outright (`exit 3`), not silently. If you change the flags, check whether the external-binary
   requirement list in `docs/SETUP.md` step 1 needs updating too.

5. **Preserve `watcher.py`'s failure-handling shape when adding pipeline steps.** Every step
   updates `db` job status before/after; the outer `try` catches specific exception types
   (`OcrError`, `GraphError`, `EmailError`) plus a general fallback, both routing through
   `_fail_job()`, which preserves the original file in `SCAN_FAILED` for reprocessing; `finally`
   always cleans up the per-job work directory. A new pipeline step should add its exception type
   to that catch list rather than letting it fall through to the generic handler silently.

6. **Respect the dashboard's security model.** All routes are open by default except `/settings`,
   which is gated by an optional `DASHBOARD_SETTINGS_PASSWORD`; every POST route requires the
   CSRF double-submit cookie (`_require_csrf()`). The Settings > Maintenance feature (real
   privileged `apt`/`reboot` commands) additionally requires `DASHBOARD_SETTINGS_PASSWORD` to be
   set at all — not just a correct login — since it stays disabled with no password configured at
   all (`_maintenance_enabled()`). This whole dashboard is designed to be reachable only over
   Tailscale/LAN, not the open internet; don't propose changes that assume otherwise without
   flagging that explicitly.

7. **The two long-running systemd units are hardened asymmetrically, deliberately.**
   `scan-watcher.service` sets `NoNewPrivileges=true`; `scan-dashboard.service` deliberately omits
   it and leaves `CapabilityBoundingSet` unrestricted, because both would break `sudo`, which the
   Maintenance feature needs. See the comment block in `systemd/scan-dashboard.service` before
   "hardening" it back to match `scan-watcher.service`.

8. **`docs/superpowers/` is point-in-time design history**, same convention as the HomeLab repo —
   treat it as a record of what was designed/planned at that date, not a live task tracker, unless
   corroborated by current code or git log. Confirmed stale in practice: the checkboxes in
   `docs/superpowers/plans/2026-08-21-email-provider-abstraction.md` are all still `- [ ]`
   unchecked even though the corresponding SMTP/Graph email backend commits have already landed —
   check git log and the actual module, not the checkbox state, to see what's really done.

9. **No test suite currently exists.** Don't claim "tests pass" or add test-runner assumptions to
   docs/CI without first confirming with Andy whether one is in scope — verification here is
   manual (see `docs/SETUP.md` step 9 and the plan docs' "manual verification" sections).

10. **Terminology: use "recipient," not the original "uncle" placeholder.** This was a deliberate
    public-release genericization (commit `6a67f50`, and see the design spec's "public-release
    cleanup" framing) to keep the repo usable as a template rather than tied to one person's
    deployment story. Don't reintroduce personal-narrative naming into new code, config vars, or
    docs.

## Forbidden Behaviours

Claude must NOT:

- Commit a populated `.env`, a Graph certificate/private key, a client secret, or an SMTP
  password — placeholders only.
- Widen the dashboard's default-open access model (e.g. by removing the CSRF check, or the
  password gate on Maintenance) without being explicitly asked.
- Re-harden `scan-dashboard.service` with `NoNewPrivileges`/a narrowed `CapabilityBoundingSet`
  without accounting for the documented sudo trade-off.
- Treat `docs/superpowers/plans/*.md` checkbox state as authoritative over the actual code/git
  history.
- Assume a test suite, CI pipeline, or `StorageBackend` abstraction exists without checking —
  none of the three currently do (as of this file's authoring, 2026-09-09).

## Goal

Keep this a small, reliable, config-driven pipeline that runs unattended on modest hardware — not
a general-purpose document management platform. When extending it (new email/storage provider,
new dashboard feature), match the existing self-validating, fail-loud-and-recoverable patterns
already in the codebase rather than introducing new ones.
