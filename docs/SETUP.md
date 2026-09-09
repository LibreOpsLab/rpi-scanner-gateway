# Setup Guide — RPi MFP Scanning Pipeline

One-time setup, in order. Budget ~2 hours including OCR test runs.

## 1. Raspberry Pi OS base

Flash **64-bit** Raspberry Pi OS (Bookworm or later), not 32-bit. `pikepdf`,
`Pillow`, and `PyMuPDF` only publish prebuilt wheels for aarch64 — on 32-bit
armv7l, pip falls back to building them from source (pikepdf alone needs a
Rust toolchain and qpdf headers), which is slow and often fails outright on
a Pi 3B. The Pi 3B has a 64-bit-capable CPU, so this is just an OS image
choice.

Use Raspberry Pi Imager's **OS Customisation** (gear icon, or Ctrl+Shift+X,
before you click Write) to set the hostname, enable SSH with your public
key, create the login user, and configure Wi-Fi. This writes first-boot
config so the Pi comes up headless-ready with no monitor/keyboard needed.

**Verify it actually applied before you eject the card.** Raspberry Pi
Imager (confirmed on 1.8.5) can silently fail to write your customisation
into the image — it happened on this project's own first attempt with a
Trixie-based image. Imager still *remembers* what you typed (visible in its
own cache, `~/.config/Raspberry Pi/Imager.conf` on Linux) but the card
itself keeps the stock, unconfigured first-boot files. With the card still
mounted on your computer, check the boot partition:

- Newer images (Trixie+) use cloud-init: open `user-data` on the boot
  partition. If every line is commented out (`#hostname: raspberrypi`,
  etc.), customisation did **not** apply — this is the default template
  pi-gen ships, not your settings.
- Older images use `custom.toml` / `firstrun.sh` instead. If neither of
  those exists on the boot partition, same problem.

If it didn't apply, don't bother re-running Imager and hoping — it's just
as likely to no-op again. Either downgrade/upgrade Imager to a version
known to work for the image you're flashing, or hand-edit `user-data` (and
`network-config` for Wi-Fi) on the boot partition directly with the
settings you meant to apply — it's plain YAML, and cloud-init reads it on
first boot without any extra work.

**SSH still won't be reachable even with a correct `user-data`.** Raspberry
Pi OS ships `openssh-server` pre-installed but the service **disabled by
default**, and merely setting `ssh_authorized_keys`/`ssh_pwauth` on a user
does not enable or start it — you'll boot, the Pi will join Wi-Fi/Ethernet
fine, and `ssh piscan.local` will get **connection refused** (not a
timeout — the host is up, nothing's listening on 22). Cover both of the
independent mechanisms that can enable it, so you're not relying on one:

1. In `user-data`, add a `runcmd` that flips it on explicitly:

   ```yaml
   runcmd:
     - systemctl enable --now ssh
   ```

2. Belt-and-suspenders, and the only fix if you're patching a card *after*
   first boot already ran (cloud-init only applies `user-data` once per
   `instance_id` — editing it post-boot won't retrigger it): drop an empty
   file named `ssh` at the root of the boot partition (`touch ssh` there).
   This is Raspberry Pi OS's own override, unrelated to cloud-init — a
   systemd unit checks for that file on **every** boot, and if present,
   enables + starts `ssh` then deletes the marker. Works even on a card
   that's already been provisioned.

**Set the locale explicitly too** — pi-gen bakes in `en_GB.UTF-8` as the
default and only that locale is generated on the image (`locale -a` won't
even list others, e.g. `en_AU.UTF-8`, until they're generated). Neither
`keyboard:` nor `timezone:` in `user-data` touches this — you need the
dedicated `locale:` key, which handles generation and `update-locale`
together on first boot:

```yaml
locale: en_AU.UTF-8
```

If you're fixing an already-provisioned Pi instead (same one-shot
`instance_id` caveat as above — editing `user-data` won't retrigger it),
do it live over SSH:

```bash
sudo sed -i 's/^# en_AU.UTF-8 UTF-8/en_AU.UTF-8 UTF-8/' /etc/locale.gen
sudo locale-gen
sudo update-locale LANG=en_AU.UTF-8
```

`locale-gen` genuinely takes a while on a Pi 3B (a minute or more for a
couple of locales) — give it a generous timeout rather than killing it
early. Killing it mid-run leaves `locale -a` missing locales that were
previously generated, including the pre-existing default; if that happens,
just re-run `locale-gen` to completion, it regenerates everything in
`/etc/locale.gen` and repairs itself.

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-venv python3-pip samba tesseract-ocr \
    ghostscript jbig2 unpaper pngquant git avahi-daemon fonts-noto
```

**`jbig2`, `unpaper`, `pngquant`, and `ghostscript` are all hard
requirements**, not optional extras — `app/ocr.py` runs ocrmypdf with
`--jbig2-lossy --clean --optimize 3`, and each flag shells out to one of
these; missing any one fails every OCR job outright with `exit 3`, it does
not degrade quietly. Confirmed by checking what `ocrmypdf`'s own
`_exec/` package actually wraps (`ghostscript.py`, `jbig2enc.py`,
`pngquant.py`, `tesseract.py`, `unpaper.py`) rather than guessing — that's
the complete external-binary surface for this project's flag set. `qpdf`
is *not* needed despite `pikepdf` using it internally: pikepdf bundles its
own vendored `libqpdf` rather than linking or shelling out to a system
install.

Package name note: on older Raspberry Pi OS (Bookworm and earlier) this
encoder was packaged as `jbig2enc`; on Trixie it was renamed to plain
`jbig2` (the CLI binary itself has always been called `jbig2` either way —
`jbig2enc` was only ever the package name, and apt won't fuzzy-match a
renamed package for you, it just 404s). If `jbig2` 404s on whatever you're
running, `apt-cache search jbig2` shows the actual current package name
rather than guessing.

`unpaper` pulls in a surprisingly large dependency chain on Trixie (~90
packages, mostly multimedia libs it links against) — give the install a
few minutes on a Pi 3B rather than assuming it's hung; `jbig2` and
`pngquant` are tiny by contrast.

## 2. Create the service user and directories

```bash
sudo useradd -r -s /usr/sbin/nologin scanpipeline
sudo usermod -aG video scanpipeline
sudo mkdir -p /srv/scans/{inbox,processing,archive,failed,thumbnails}
sudo chown -R scanpipeline:scanpipeline /srv/scans
```

The `video` group membership is for the dashboard's Pi-temperature stat
(`vcgencmd measure_temp`/`get_throttled`) — `/dev/vcio_gencmd` is
group-owned by `video`, mode 660, and `scanpipeline` (a bare `-r` service
account with no supplementary groups) can't open it otherwise. Without
this, the dashboard doesn't error, the temperature stat just silently
shows `—`. If you add this after the service is already running,
`systemctl restart scan-dashboard.service` is required — group membership
is resolved once at process start, not live.

## 3. Deploy the code

```bash
sudo mkdir -p /opt/scan-pipeline
sudo chown $USER:$USER /opt/scan-pipeline
git clone <your-repo-or-copy-files-here> /opt/scan-pipeline
cd /opt/scan-pipeline
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env — you'll fill in the email provider values in step 6
sudo chown -R scanpipeline:scanpipeline /opt/scan-pipeline
```

## 4. Samba share (the Printer's scan target)

`smb.conf` restricts the share to `valid users = scanner`, but that's a
Samba-level restriction, not an account — `smbpasswd -a` only sets a Samba
password for a user that **already exists** at the Unix level. Skipping
this step fails with `Failed to add entry for user scanner.`, since there's
no such system account (only `scanpipeline`, the separate service account
from step 2, exists at that point):

```bash
sudo useradd -r -M -s /usr/sbin/nologin scanner
sudo tee -a /etc/samba/smb.conf < scripts/samba-scan-share.conf
sudo smbpasswd -a scanner        # pick a password, note it down
sudo systemctl restart smbd
```

Test from your Mac/PC before touching the Printer:
`smb://<pi-ip-or-tailscale-name>/scans` — connect as user `scanner`, confirm
you can drop a test PDF in.

## 5. Printer panel — Scan to Network Folder shortcut

On the MFP touchscreen (menu wording varies slightly by model):

1. **Settings → Network → Scan to Network Folder** (or via the web-based
   management page at the printer's IP, under Scan → Scan to FTP/Network)
2. Add a new profile:
   - **Network Folder Path:** `\\<pi-ip>\scans` (or `//<pi-ip>/scans` depending on model UI)
   - **Username:** `scanner`
   - **Password:** the one from step 4
   - **File name:** something predictable, e.g. `Scan`
   - **File type:** PDF
   - **Resolution:** 300 dpi (600dpi roughly doubles OCR time and file size for
     no real quality benefit on text documents)
   - **Color:** Black & White or Grey for contracts/disclosures — smaller
     files, faster OCR. Only use Color if you're scanning something with
     color content that matters.
3. Assign it to a **Shortcut button** on the home screen, name it something
   recognizable for whoever uses the printer — "Scan to Office" or similar.
4. Test: scan a page, confirm it lands in `/srv/scans/inbox` on the Pi.

## 6. Email provider setup (optional)

Don't want notification emails at all? Set `EMAIL_PROVIDER=none` in `.env`
and skip straight to [step 7](#7-tailscale) — recipients view, download,
and delete processed scans directly from the dashboard's `/details` page
instead. Nothing else in this section applies.

Otherwise, pick one and set `EMAIL_PROVIDER` accordingly in `.env`.

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

Default is `STORAGE_PROVIDER=onedrive`. Set it to `STORAGE_PROVIDER=none`
in `.env` for a fully local setup instead — no M365 tenant or app
registration needed at all, whether or not you're using Graph for email.
The 30-day local archive under `/srv/scans/archive` (browsable from the
dashboard) is the only copy, and none of the vars below are required.

To keep the OneDrive mirror (`STORAGE_PROVIDER=onedrive`, the default): it
still uses the client-secret Graph flow. Follow the app registration steps
above but also add a client secret (**Certificates & secrets → New client
secret**) for `GRAPH_CLIENT_SECRET`, grant `Files.ReadWrite.All`, and set
`ONEDRIVE_USER_EMAIL` to whose OneDrive receives the upload. This will move
onto the same certificate as email sending in a future update.

## 7. Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Authenticate against your tailnet — the one you'll share with anyone else
who needs to reach the dashboard or the Pi. Note the MagicDNS name it's
assigned (e.g. `scanner-pi`) — that's what you'll use to reach the
dashboard: `http://scanner-pi:5000`.

If you want the scan recipient to check the simple view themselves, install
Tailscale on their device too and share the Pi node with their account
(node sharing is included free — see Tailscale's Personal plan). Otherwise
this stays just for you.

## 8. Install and start the services

```bash
cd /opt/scan-pipeline
sudo cp systemd/*.service systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now scan-watcher.service
sudo systemctl enable --now scan-dashboard.service
sudo systemctl enable --now retention-cleanup.timer
```

Want a bare `http://<host>/` URL instead of `:5000`? Set `DASHBOARD_PORT=80`
in `.env` and restart `scan-dashboard.service`. `systemd/scan-dashboard.service`
already carries `AmbientCapabilities=CAP_NET_BIND_SERVICE`, which lets the
service bind port 80 directly without running as root or needing a reverse
proxy — no other change needed on the systemd side. If you're also using the
mDNS advertisement below, update `<port>` in `avahi/scanner-gateway.service`
to match before copying it (it isn't read from `.env` automatically).

Also install the mDNS advertisement so the dashboard is discoverable on the
local network as "Scanner Gateway" (no Tailscale needed, LAN only — see
`avahi/scanner-gateway.service`'s comment for how this differs from the
Tailscale MagicDNS name from step 7):

```bash
sudo cp avahi/scanner-gateway.service /etc/avahi/services/
```

`avahi-daemon` picks up new service files automatically; no restart needed.

Check it's alive:

```bash
sudo systemctl status scan-watcher
sudo journalctl -u scan-watcher -f     # tail logs live while testing
```

## 9. End-to-end test

1. Scan a multi-page test document (include a deliberately blank page)
   from the Printer using the shortcut button.
2. Watch `journalctl -u scan-watcher -f` — you should see it move through
   received → OCR → (uploading →, if `STORAGE_PROVIDER=onedrive`) → done.
3. Check the dashboard at `http://scanner-pi:5000` — confirm the scan
   shows up, opens (View), downloads correctly, and is searchable (Cmd+F
   for a word you know is in the doc).
4. If `EMAIL_PROVIDER` isn't `none`, confirm the email arrived and the
   attachment opens/searches the same way. If `STORAGE_PROVIDER=onedrive`,
   confirm the OneDrive link works.
5. Check `/srv/scans/archive` has the local backup copy.

## Ongoing maintenance

- Dashboard access: the whole dashboard is unauthenticated by default — it's
  only as safe as the network it's reachable on (Tailscale/LAN). If you want
  a password on just the `/settings` page (where the recipient email is
  changed), set `DASHBOARD_SETTINGS_PASSWORD` in `.env` and restart
  `scan-dashboard.service`. Everything else stays open either way.
- Logs: `journalctl -u scan-watcher` / `-u scan-dashboard`
- If a scan fails, it's visible on `/details` with the error message, and
  the original is preserved in `/srv/scans/failed` for reprocessing.
- Client secret expiry: Entra secrets typically max out at 24 months —
  put a reminder in your calendar.

### System maintenance from the dashboard (Settings > Maintenance)

Lets you run `apt-get update`/`upgrade` and reboot the Pi from the
dashboard, instead of SSHing in. **Requires `DASHBOARD_SETTINGS_PASSWORD`
to be set at all** — unlike the rest of the dashboard, this section stays
disabled with no password configured, since it runs real privileged
commands rather than just changing a stored setting.

One-time setup, since `scanpipeline` (the account the dashboard runs as)
needs a narrow sudo grant to run these three specific commands as root:

```bash
sudo cp scripts/scanpipeline-maintenance.sudoers /etc/sudoers.d/scanpipeline-maintenance
sudo chmod 440 /etc/sudoers.d/scanpipeline-maintenance
sudo visudo -c
```

`visudo -c` should end with `/etc/sudoers.d/scanpipeline-maintenance: parsed
OK`. Ignore a "syntax error" line pointing at the upgrade rule if a `parsed
OK` for that same file follows it — that's `visudo -c`'s own preliminary
pass, not the final result; `sudo -u scanpipeline sudo -n -l` is the
authoritative check and should list all three commands.

If you're updating an existing deployment rather than following this guide
fresh, re-copy `systemd/scan-dashboard.service` and `daemon-reload` +
`restart` it — this feature needed two changes to the unit that are easy to
half-apply by hand: `NoNewPrivileges` had to come out entirely (it blocks
`sudo` outright), and `CapabilityBoundingSet` (added for the port-80 trick
above) had to come out too, *not* just be widened — capping the bounding
set to `CAP_NET_BIND_SERVICE` blocks `sudo` from ever acquiring
`CAP_SETUID`/`CAP_SETGID` even via its setuid bit, which surfaces as `sudo:
unable to change to root gid: Operation not permitted` if you hit it.
`AmbientCapabilities=CAP_NET_BIND_SERVICE` alone is enough for the port-80
binding and doesn't conflict with sudo.

Patch runs in the background (can take several minutes on a Pi 3B) — the
page shows "an update is currently running" until it's done, and the last
run's output stays visible in a collapsible panel either way. Reboot
interrupts any scan in progress, same as pulling the power.
