"""Small HTTP helpers shared by all source connectors."""
import logging
import re
import time
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


def download(url: str, target_dir: Path = config.DOWNLOAD_DIR, attempts: int = 12) -> Path:
    """Stream a file to disk, resuming after dropped connections (HTTP Range).

    Public download servers drop long transfers; restarting a 3 GB file from zero
    each time would never finish. Skips the download if the file already exists.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / url.rstrip("/").split("/")[-1]
    if target.exists() and target.stat().st_size > 0:
        log.info("Using cached %s", target.name)
        return target
    log.info("Downloading %s", url)
    tmp = target.with_suffix(target.suffix + ".part")
    total, next_report, stalled, last = None, 0.1, 0, -1
    while True:
        have = tmp.stat().st_size if tmp.exists() else 0
        if have > last:      # progress since the last attempt: reset the counter
            stalled, last = 0, have
        stalled += 1
        if stalled > attempts:
            raise RuntimeError(f"Download of {url} stalled after {attempts} attempts")
        headers = dict(config.HTTP_HEADERS)
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with requests.get(url, headers=headers, stream=True, timeout=120) as r:
                if r.status_code == 416:  # nothing left to fetch
                    break
                r.raise_for_status()
                if have and r.status_code != 206:  # server ignored Range: start over
                    have = 0
                if total is None:
                    total = have + int(r.headers.get("content-length", 0))
                with open(tmp, "ab" if have else "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        fh.write(chunk)
                        have += len(chunk)
                        if total and have / total >= next_report:
                            log.info("  %s: %d%%", target.name, int(100 * have / total))
                            next_report += 0.1
            if not total or have >= total:
                break
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as exc:
            log.warning("  connection dropped at %.0f MB, resuming (%s)",
                        have / 1e6, type(exc).__name__)
            time.sleep(min(5 * stalled, 30))
    if total and tmp.stat().st_size < total:
        raise RuntimeError(f"Incomplete download of {url}")
    tmp.rename(target)
    log.info("Saved %s (%.1f MB)", target.name, target.stat().st_size / 1e6)
    return target
