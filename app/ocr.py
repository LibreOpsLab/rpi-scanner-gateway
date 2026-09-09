"""
OCR + compression pass via ocrmypdf.

--jobs N            parallelize across the Pi's 4 cores
--optimize 3         maximum lossless+lossy optimization pass
--jbig2-lossy        stronger compression for B&W text pages (safe for
                      contracts/disclosures; skip this flag if you ever
                      scan documents where every pixel must be pixel-perfect,
                      e.g. inspection photos)
--skip-text          don't re-OCR pages that already have a text layer
--deskew             auto-straightens crooked feeder scans
--clean               removes scan speckle/noise before OCR (improves accuracy)
"""
import subprocess
import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# systemd gives the service a bare PATH that doesn't include venv/bin, so a
# plain "ocrmypdf" lookup fails even though ExecStart points at the venv's
# python — resolve the console script as a sibling of the interpreter instead.
_OCRMYPDF = str(Path(sys.executable).with_name("ocrmypdf"))


class OcrError(Exception):
    pass


def run_ocr(input_path: str, output_path: str, jobs: int = 4, language: str = "eng"):
    cmd = [
        _OCRMYPDF,
        "--jobs", str(jobs),
        "--language", language,
        "--optimize", "3",
        "--jbig2-lossy",
        "--deskew",
        "--clean",
        "--skip-text",
        "--output-type", "pdf",
        input_path,
        output_path,
    ]
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

    # ocrmypdf exit code 6 = "already has text, nothing to do" — treat as success
    if result.returncode not in (0, 6):
        raise OcrError(
            f"ocrmypdf failed (exit {result.returncode}): {result.stderr[-2000:]}"
        )

    if not Path(output_path).exists():
        raise OcrError("ocrmypdf reported success but produced no output file")


def make_thumbnail(pdf_path: str, thumbnail_path: str, dpi: int = 100):
    import fitz
    doc = fitz.open(pdf_path)
    if doc.page_count == 0:
        doc.close()
        return
    page = doc[0]
    pix = page.get_pixmap(dpi=dpi)
    pix.save(thumbnail_path)
    doc.close()
