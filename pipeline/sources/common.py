"""Small HTTP helpers shared by all source connectors."""
import logging
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

from pipeline import config

log = logging.getLogger(__name__)


def get_text(url: str) -> str:
    resp = requests.get(url, headers=config.HTTP_HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.text


def list_links(url: str) -> list[str]:
    """Return absolute hrefs found on a page (works for Apache-style indexes)."""
    html = get_text(url)
    hrefs = re.findall(r'href=["\']([^"\'#?]+)["\']', html, flags=re.IGNORECASE)
    return [urljoin(url, h) for h in hrefs]


def download(url: str, target_dir: Path = config.DOWNLOAD_DIR) -> Path:
    """Stream a file to disk. Skips the download if the same file already exists."""
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / url.rstrip("/").split("/")[-1]
    if target.exists() and target.stat().st_size > 0:
        log.info("Using cached %s", target.name)
        return target
    log.info("Downloading %s", url)
    tmp = target.with_suffix(target.suffix + ".part")
    with requests.get(url, headers=config.HTTP_HEADERS, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done, next_report = 0, 0.1
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                fh.write(chunk)
                done += len(chunk)
                if total and done / total >= next_report:
                    log.info("  %s: %d%%", target.name, int(100 * done / total))
                    next_report += 0.1
    tmp.rename(target)
    log.info("Saved %s (%.1f MB)", target.name, target.stat().st_size / 1e6)
    return target
