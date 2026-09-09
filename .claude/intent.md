# intent.md — rpi-scanner-gateway

Active, session-spanning threads and open questions. Not a duplicate of any roadmap doc (there
isn't one in this repo yet) — this is nearer-term and collaboration-focused, same role as
HomeLab's `.claude/intent.md`.

---

## Active threads

### Provider abstraction, headless provisioning, and public-release cleanup

Source docs: `docs/superpowers/specs/2026-08-19-provider-abstraction-and-provisioning-design.md`
(the design), `docs/superpowers/plans/2026-08-19-provider-abstraction-and-provisioning.md` (the
original 15-task plan, per its own commit message: "config rewrite, pluggable
EmailSender/StorageBackend interfaces, cert-based Graph auth, watcher integration, install.sh,
docs rewrite, and CLAUDE.md/CONTRIBUTING.md/.vscode additions"), and
`docs/superpowers/plans/2026-08-21-email-provider-abstraction.md` (a narrower, email-only
follow-up plan).

**Done** (confirmed against current code + git log, not plan checkboxes — see `memory.md`'s note
on why the checkboxes themselves are stale):

- Config rewrite (`app/config.py` matches the planned shape — `EMAIL_PROVIDER`/`STORAGE_PROVIDER`
  switches, provider-specific var groups).
- `EmailSender` interface + shared Graph cert auth (`app/email/base.py`, `app/graph_auth.py`).
- SMTP email backend (`app/email/smtp_sender.py`).
- Graph email backend, certificate-based (`app/email/graph_sender.py`).
- Email provider dispatch (`app/email/__init__.py`'s `get_email_sender()`).
- Watcher integration for email (`app/watcher.py` uses `get_email_sender()`, not a hardcoded
  backend).
- Terminology genericization ("uncle" → "recipient", commit `6a67f50`).

**Not done** (checked directly — files/modules don't exist or code still shows the old shape):

- **Storage backend abstraction.** No `app/storage/` package, no `StorageBackend` ABC, no
  `webdav.py`. `app/graph.py`'s `upload_to_onedrive()` is still called directly from
  `watcher.py`, still on client-secret Graph auth (not migrated to `app/graph_auth.py`'s
  certificate flow, despite that module's own docstring saying it "will move onto this too in a
  later pass").
- **`db.jobs.onedrive_link` → generic column rename.** Still literally `onedrive_link` in
  `app/db.py`'s schema and every reference to it.
- **`scripts/install.sh`.** Doesn't exist. The plan describes an idempotent provisioning script
  to replace/automate large chunks of the current manual `docs/SETUP.md` walkthrough.
- **README rebrand.** Current `README.md` is already partway there (generic "Printer" language,
  no personal narrative) but hasn't been fully rewritten to the plan's drafted version (which adds
  a "Choosing providers" table, an `app/storage/` line in the structure diagram, and an "Example
  deployment" section that's explicit about this being a real family deployment used as a
  template) — worth comparing against the plan's Task 12 draft before doing a final pass, since
  the current file has diverged in its own direction too (e.g. already says "Printer panel," not
  "Brother panel" as the plan draft still does).
- **`CONTRIBUTING.md`, `.vscode/`.** Neither exists yet (Tasks 14–15).
- **`.claude/CLAUDE.md` and friends.** This was Task 13 of the original plan — this session's
  output (`.claude/CLAUDE.md`, `.claude/memory.md`, `.claude/intent.md`) is the first real work on
  that specific task, done via the HomeLab-pattern structure rather than the plan's own drafted
  outline. Worth a quick diff against the plan's Task 13 draft if a "did we cover what the plan
  wanted" check matters later.

Net: the **email side** of this initiative is functionally complete; the **storage side,
provisioning script, and public-facing docs/meta files** are the open remainder.

---

## Open questions / noticed in passing

- **No test suite exists anywhere in this repo.** Worth asking Andy directly whether automated
  tests are in scope for the public-release cleanup, or deliberately out of scope for a
  single-purpose tool verified by manual smoke test (current pattern throughout `docs/SETUP.md`
  and the plan docs' "manual verification" sections).
- **`PyYAML` is in `requirements.txt` but unused.** No `import yaml` anywhere in `app/` or
  `scripts/` as of this check. Could be a leftover, or reserved for `scripts/install.sh` (not yet
  written) doing YAML-based config templating — unconfirmed either way.
- **Both `docs/superpowers/specs/2026-08-19-...-design.md` and
  `docs/superpowers/plans/2026-08-21-email-provider-abstraction.md` show a filesystem mtime of
  2026-09-09** (today, per `ls -la`) despite the 2026-08-19/21 dates in their filenames — unclear
  whether that reflects real edits made today (a `.remember/today-2026-09-09.md` entry from
  15:51 today does reference "committed spec/plan docs/superpowers/... pending," suggesting some
  activity today) or just a non-content filesystem touch. Worth clarifying with Andy if the exact
  edit history of those two files matters.
- **`docs/SETUP.md` says the Graph certificate private key is "covered by `.gitignore`"** — true
  only because the documented deploy path (`/opt/scan-pipeline/certs/graph-app.key`) sits outside
  this git checkout entirely, not because `.gitignore` has a `certs/`/`*.key` entry (it doesn't).
  Not a bug, just worth knowing the claim is deploy-topology-dependent rather than
  gitignore-enforced — if someone ever puts a cert inside the repo tree during local dev, it
  would not actually be excluded.

## Resolved

(Nothing yet — this is the initial pass, 2026-09-09.)
