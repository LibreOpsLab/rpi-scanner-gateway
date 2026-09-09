"""
Lightweight system stats for the mini dashboard — Pi temperature/throttling,
disk free space, and Tailscale connectivity. Every check is best-effort:
a missing binary, a permission issue (the dashboard runs as the restricted
scanpipeline service account, not interactively), or a timeout all degrade
to None rather than raising, so the dashboard always renders.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str], timeout: float = 3):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError, OSError):
        return None


def pi_temperature_celsius() -> float | None:
    result = _run(["vcgencmd", "measure_temp"])
    if not result or result.returncode != 0:
        return None
    match = re.search(r"temp=([\d.]+)", result.stdout)
    return float(match.group(1)) if match else None


def pi_throttled() -> bool | None:
    """True if under-voltage or throttling has occurred (any of bits
    0/1/2/16/17/18/19 of `vcgencmd get_throttled` — see docs/SETUP.md's
    power troubleshooting section for what each bit means)."""
    result = _run(["vcgencmd", "get_throttled"])
    if not result or result.returncode != 0:
        return None
    match = re.search(r"0x([0-9a-fA-F]+)", result.stdout)
    return int(match.group(1), 16) != 0 if match else None


def disk_free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def tailscale_online() -> bool | None:
    result = _run(["tailscale", "status", "--json"], timeout=5)
    if not result or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        return bool(data.get("Self", {}).get("Online"))
    except (json.JSONDecodeError, AttributeError):
        return None
