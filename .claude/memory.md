# memory.md — rpi-scanner-gateway

Durable, non-obvious facts and gotchas about this repo that aren't derivable from a quick read of
the code or `README.md`. See `.claude/CLAUDE.md` for the architecture/behavioral-rules spec and
`.claude/intent.md` for active work.

---

## Feedback — how to work in this repo

- **`docs/superpowers/` checkbox state lags real progress — verify against git log, not the
  markdown.** `docs/superpowers/plans/2026-08-21-email-provider-abstraction.md` still shows every
  step as `- [ ]` unchecked, but `git log` (commits `b382f59`, `e662eed`, `de90332`, `b8fbd53`,
  `61be0e3`) and the current `app/email/` module confirm the SMTP and Graph email backends are
  actually implemented and wired into `watcher.py`.
  **Why:** these plan docs are point-in-time design artifacts (per the HomeLab convention this
  repo's `.claude/` setup mirrors), not live trackers someone goes back and checks off.
  **How to apply:** when asked "is X done," check the actual module/git log first; only cite plan
  checkboxes as corroborating evidence, never as the primary source.

- **No automated tests exist anywhere in this repo.** Confirmed via `find` across the whole tree
  (excluding `.git`/`.remember`) — no `test_*.py`, no `tests/` directory, no CI config.
  **Why:** unclear if deliberate (single-purpose homelab-adjacent tool) or just not gotten to yet
  — see the open question in `.claude/intent.md`.
  **How to apply:** don't claim "tests pass," don't scaffold a test runner into docs/CI
  unprompted, and verify changes the way the project already does — manual smoke test per
  `docs/SETUP.md` step 9's flow (scan a doc, watch `journalctl -u scan-watcher -f`, check the
  dashboard, check the email/OneDrive link).

## Project context — decisions, state, gotchas not obvious from the code

- **Storage (cloud upload) has no provider abstraction yet; email does.** `app/email/` has a
  full `EmailSender` ABC + factory dispatch (`get_email_sender()`) keyed on `EMAIL_PROVIDER`.
  `app/graph.py`'s `upload_to_onedrive()` is still called directly from `watcher.py`, still uses
  client-secret Graph auth (not the certificate flow `app/graph_auth.py` provides), and there is
  no `webdav.py` or `StorageBackend` interface despite both being designed in
  `docs/superpowers/specs/2026-08-19-provider-abstraction-and-provisioning-design.md`.
  **Why:** the 15-task implementation plan
  (`docs/superpowers/plans/2026-08-19-provider-abstraction-and-provisioning.md`) covers config,
  email backends, *and* storage backends, provisioning, and docs — only the email-side tasks
  (config rewrite, provider interfaces, SMTP/Graph email backends, watcher wiring) have landed so
  far, per git log.
  **How to apply:** don't describe `STORAGE_PROVIDER` as pluggable in the same sense as
  `EMAIL_PROVIDER` — today it's effectively `onedrive` or `none`, hardcoded, not a real dispatch.
  See `.claude/intent.md` for the specific remaining tasks.

- **`ocrmypdf` binary resolution is a deliberate workaround, not an oversight.**
  `app/ocr.py` resolves `_OCRMYPDF` as `Path(sys.executable).with_name("ocrmypdf")` instead of a
  plain `"ocrmypdf"` lookup on `$PATH`.
  **Why:** systemd gives `scan-watcher.service` a bare `PATH` that doesn't include the venv's
  `bin/`, even though `ExecStart` points at the venv's `python` directly — a plain subprocess
  lookup for `ocrmypdf` would fail under systemd despite working fine in an interactive shell
  with the venv activated.
  **How to apply:** if adding another shelled-out venv console script, use the same
  sibling-of-`sys.executable` resolution rather than assuming `PATH` will have it under systemd.

- **`scan-dashboard.service` and `scan-watcher.service` are hardened asymmetrically on purpose.**
  `scan-watcher.service` has `NoNewPrivileges=true`; `scan-dashboard.service` deliberately omits
  it and leaves `CapabilityBoundingSet` at its unrestricted default.
  **Why:** the dashboard's Settings > Maintenance feature needs `scanpipeline` to `sudo` into
  three exact commands (`apt-get update`, one specific `apt-get upgrade` invocation, `reboot` —
  see `scripts/scanpipeline-maintenance.sudoers`). Both `NoNewPrivileges` and a
  `CapabilityBoundingSet` narrowed to just `CAP_NET_BIND_SERVICE` (added for the optional port-80
  bind) independently break `sudo` — the former directly, the latter by capping what even a
  setuid-root binary can ever gain, which excludes the `CAP_SETUID`/`CAP_SETGID` `sudo` itself
  needs (`sudo: unable to change to root gid: Operation not permitted` is the exact symptom).
  **How to apply:** don't "fix" this by re-adding `NoNewPrivileges` or narrowing
  `CapabilityBoundingSet` on `scan-dashboard.service` without re-reading the comment block already
  in that file — the sudoers file is the actual privilege boundary now, not these systemd flags.

- **The `db.jobs.onedrive_link` column name is still literal, not generic**, even though the
  design intends the storage layer to eventually support WebDAV too (per the spec's note that the
  column would be "reused as a generic [storage link]").
  **Why:** the DB-rename step (Task 7 of the provider-abstraction plan) hasn't been done — it's
  gated on the storage-backend abstraction landing first.
  **How to apply:** don't assume the column is provider-agnostic; if the storage abstraction work
  resumes, the rename needs a small migration (existing SQLite DBs on deployed Pis would need
  `ALTER TABLE`, not just a fresh `CREATE TABLE IF NOT EXISTS`).

- **"Recipient" replaced "uncle" as the deliberate public-release terminology change** (git commit
  `6a67f50`, part of the broader genericization documented in
  `docs/superpowers/specs/2026-08-19-provider-abstraction-and-provisioning-design.md`).
  **Why:** the repo originated as a specific family deployment (see the plan's own "Example
  deployment" README draft: a Brother MFP → Pi 3B → M365 Graph setup for a family member) and is
  being cleaned up to be usable as a generic template.
  **How to apply:** don't reintroduce personal/narrative naming in new code, env vars, or docs —
  use "recipient" and provider-neutral language throughout.

---

## Reconciliation log

- **2026-09-09** — Initial pass. Created `.claude/CLAUDE.md`, `.claude/memory.md`,
  `.claude/intent.md` from scratch (no prior CLAUDE.md or Claude Code auto-memory existed for this
  repo). Entries above are backed by direct code/doc/git-log inspection at that date; the
  provider-abstraction/storage-backend/install.sh/CONTRIBUTING.md/.vscode work described as
  "not done" was verified absent by checking for the actual files/modules, not inferred from the
  plan docs alone.
