"""
Watches SCAN_INBOX for new PDFs dropped by the Printer's SMB scan-to-folder
job, then runs the full pipeline:

  1. Wait for the file to finish writing (SMB writes aren't instantaneous)
  2. Strip blank pages
  3. OCR + compress
  4. Generate thumbnail
  5. Archive a local copy (kept RETENTION_DAYS)
  6. Upload to OneDrive
  7. Email the recipient
  8. Update dashboard DB at every step so failures are visible, not silent

Run as a systemd service (see systemd/scan-watcher.service) so it survives
reboots and restarts automatically if it crashes.
"""
import logging
import shutil
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.config import config
from app import db
from app.blank_pages import strip_blank_pages
from app.ocr import run_ocr, make_thumbnail, OcrError
from app.graph import upload_to_onedrive, GraphError
from app.email import get_email_sender, EmailError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("watcher")

STABILITY_CHECK_SECONDS = 3
STABILITY_POLL_INTERVAL = 1


def wait_until_stable(path: Path):
    """Poll file size until it stops changing — cheap way to avoid grabbing
    a scan mid-write over SMB."""
    last_size = -1
    stable_since = None
    while True:
        if not path.exists():
            time.sleep(STABILITY_POLL_INTERVAL)
            continue
        size = path.stat().st_size
        if size == last_size and size > 0:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= STABILITY_CHECK_SECONDS:
                return
        else:
            stable_since = None
        last_size = size
        time.sleep(STABILITY_POLL_INTERVAL)


def process_file(src_path: Path):
    filename = src_path.name
    logger.info("New scan detected: %s", filename)
    job_id = db.create_job(filename)

    work_dir = config.SCAN_PROCESSING / f"job_{job_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        wait_until_stable(src_path)
        original_size = src_path.stat().st_size

        working_copy = work_dir / "00_original.pdf"
        shutil.move(str(src_path), working_copy)

        # --- Step 1: strip blank pages ---
        db.update_job(job_id, status="ocr_running")
        no_blanks_path = work_dir / "01_no_blanks.pdf"
        kept, removed = strip_blank_pages(
            str(working_copy), str(no_blanks_path), threshold=config.BLANK_PAGE_THRESHOLD
        )
        db.update_job(job_id, page_count=kept, blank_pages_removed=removed)
        logger.info("Job %s: kept %d pages, removed %d blank pages", job_id, kept, removed)

        # --- Step 2: OCR + compress ---
        ocr_output_path = work_dir / "02_final.pdf"
        run_ocr(str(no_blanks_path), str(ocr_output_path), jobs=config.OCR_JOBS, language=config.OCR_LANGUAGE)
        compressed_size = ocr_output_path.stat().st_size
        db.update_job(job_id, original_size_bytes=original_size, compressed_size_bytes=compressed_size)

        # --- Step 3: thumbnail ---
        thumb_path = config.THUMBNAIL_DIR / f"job_{job_id}.png"
        make_thumbnail(str(ocr_output_path), str(thumb_path))
        db.update_job(job_id, thumbnail_path=str(thumb_path))

        # --- Step 4: local archive copy (30-day backup) ---
        archive_name = f"{time.strftime('%Y-%m-%d')}_{filename}"
        archive_path = config.SCAN_ARCHIVE / archive_name
        shutil.copyfile(ocr_output_path, archive_path)
        db.update_job(job_id, archive_path=str(archive_path))

        # --- Step 5: upload to OneDrive (optional, STORAGE_PROVIDER=none skips it) ---
        onedrive_link = None
        if config.STORAGE_PROVIDER == "onedrive":
            db.update_job(job_id, status="uploading")
            onedrive_link = upload_to_onedrive(str(ocr_output_path), filename)
            db.update_job(job_id, onedrive_link=onedrive_link)

        # --- Step 6: email the recipient (optional, EMAIL_PROVIDER=none skips it) ---
        email_sender = get_email_sender()
        if email_sender:
            size_mb = compressed_size / (1024 * 1024)
            storage_note = (
                f'<p>A copy has also been saved to your OneDrive:<br>'
                f'<a href="{onedrive_link}">{onedrive_link}</a></p>'
                if onedrive_link else ""
            )
            body = f"""
            <p>Hi,</p>
            <p>Your scanned document <b>{filename}</b> is ready.</p>
            <p>{kept} page(s) processed{f', {removed} blank page(s) removed' if removed else ''}.
            File size: {size_mb:.1f} MB.</p>
            {storage_note}
            """
            email_sender.send(subject=f"Scanned: {filename}", body_html=body, attachment_path=str(ocr_output_path))
            db.update_job(job_id, email_sent=1)

        db.update_job(job_id, status="done")
        logger.info("Job %s complete: %s", job_id, filename)

    except (OcrError, GraphError, EmailError) as e:
        logger.error("Job %s failed: %s", job_id, e)
        _fail_job(job_id, work_dir, filename, str(e))
    except Exception as e:
        logger.exception("Job %s failed unexpectedly", job_id)
        _fail_job(job_id, work_dir, filename, f"Unexpected error: {e}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _fail_job(job_id: int, work_dir: Path, filename: str, error_message: str):
    db.update_job(job_id, status="failed", error_message=error_message)
    # Preserve whatever we had so it's recoverable, rather than silently losing the scan
    failed_dest = config.SCAN_FAILED / f"job_{job_id}_{filename}"
    original = work_dir / "00_original.pdf"
    if original.exists():
        shutil.copyfile(original, failed_dest)


class ScanHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".pdf":
            return
        process_file(path)


def main():
    config.ensure_dirs()
    db.init_db()
    logger.info("Watching %s for new scans...", config.SCAN_INBOX)

    # Catch anything already sitting in the inbox (e.g. scanned while the
    # service was down) before starting the live watch
    for existing in sorted(config.SCAN_INBOX.glob("*.pdf")):
        process_file(existing)

    observer = Observer()
    observer.schedule(ScanHandler(), str(config.SCAN_INBOX), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
