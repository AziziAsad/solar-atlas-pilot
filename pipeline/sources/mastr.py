"""Marktstammdatenregister (MaStR) Gesamtdatenexport: solar units.

The bulk export is a ~3 GB zip of UTF-16 XML files, millions of units. We never
load it into memory: each XML file is streamed and every unit is reduced on the
fly to a running total per Gemeinde. Catalogue codes (status, Bundesland, Lage)
are resolved with Katalogwerte.xml from the same zip, so we never rely on
hardcoded code lists. Licence: dl-de/by-2-0, (c) Bundesnetzagentur.
"""
import logging
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
from lxml import etree

from pipeline import config
from pipeline.sources.common import download, get_text

log = logging.getLogger(__name__)

ZIP_PATTERN = re.compile(r"Gesamtdatenexport_(\d{8})_[\d.]+\.zip", re.IGNORECASE)
SOLAR_FILE = re.compile(r"EinheitenSolar(_\d+)?\.xml$", re.IGNORECASE)
IN_OPERATION_FALLBACK_CODE = "35"  # used only if the catalogue is missing
NAME_TO_LAND = {v.lower(): k for k, v in config.LAND_NAMES.items()}


def discover_url() -> str:
    if config.MASTR_URL_OVERRIDE:
        return config.MASTR_URL_OVERRIDE
    html = get_text(config.MASTR_PAGE_URL)
    names = re.findall(r"Gesamtdatenexport_\d{8}_[\d.]*\d\.zip", html)
    absolute = re.findall(r'(https?://[^"\'\s>]*Gesamtdatenexport_\d{8}_[\d.]*\d\.zip)', html)
    hits = absolute or [f"https://download.marktstammdatenregister.de/{n}" for n in names]
    if not hits:
        raise RuntimeError(
            "No Gesamtdatenexport link found on the MaStR download page. "
            "Set MASTR_URL to the zip URL to continue."
        )
    def key(u):
        return (re.search(r"Gesamtdatenexport_(\d{8})", u).group(1), "stichtag" not in u.lower())
    url = max(set(hits), key=key)  # newest export date; daily export over Stichtag on ties
    log.info("MaStR source: %s", url)
    return url


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter_units(stream):
    """Yield one dict per top-level record of a MaStR XML file, streaming."""
    depth = 0
    for event, elem in etree.iterparse(stream, events=("start", "end"), huge_tree=True):
        if event == "start":
            depth += 1
            continue
        if depth == 2:
            yield {_local(child.tag): (child.text or "").strip() for child in elem}
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]
        depth -= 1


def _read_catalogue(zf: zipfile.ZipFile) -> dict[str, str]:
    name = next((n for n in zf.namelist() if "katalogwerte" in n.lower()), None)
    if not name:
        log.warning("Katalogwerte.xml not found; falling back to known codes")
        return {}
    with zf.open(name) as fh:
        return {r.get("Id", ""): r.get("Wert", "") for r in _iter_units(fh)}


def parse(zip_path: Path) -> tuple[pd.DataFrame, dict]:
    """Aggregate in-operation solar units to (gemeinde_ags, land_ags)."""
    agg = defaultdict(lambda: [0, 0.0, 0.0, 0.0])  # units, kW, rooftop kW, kW with known Lage
    status_counts, stats = Counter(), Counter()

    with zipfile.ZipFile(zip_path) as zf:
        catalogue = _read_catalogue(zf)
        files = sorted(n for n in zf.namelist() if SOLAR_FILE.search(n))
        if not files:
            raise RuntimeError("No EinheitenSolar*.xml files in the export")
        for name in files:
            with zf.open(name) as fh:
                for u in _iter_units(fh):
                    stats["units_seen"] += 1
                    code = u.get("EinheitBetriebsstatus", "")
                    if code and code not in catalogue:
                        stats["status_unresolved"] += 1  # schema drift indicator
                    status = catalogue.get(code, code) or "(leer)"
                    status_counts[status] += 1
                    in_op = status.lower() == "in betrieb" or (
                        code not in catalogue and code == IN_OPERATION_FALLBACK_CODE
                    )
                    if not in_op:
                        continue
                    stats["units_in_operation"] += 1

                    raw_kw = u.get("Bruttoleistung", "")
                    try:
                        kw = float(raw_kw.replace(",", "."))
                    except ValueError:
                        # Missing is not zero: count it, never add it as 0 kW.
                        stats["units_missing_capacity"] += 1
                        continue

                    gs = re.sub(r"\D", "", u.get("Gemeindeschluessel", ""))
                    gem = gs if len(gs) == 8 and gs[:2] in config.LAND_NAMES else None
                    bl_name = catalogue.get(u.get("Bundesland", ""), "").lower()
                    land_from_bl = NAME_TO_LAND.get(bl_name)
                    land = gem[:2] if gem else land_from_bl
                    if gem and land_from_bl and land_from_bl != gem[:2]:
                        stats["units_land_mismatch"] += 1
                    if not gem:
                        stats["units_without_gemeinde"] += 1
                    if not land:
                        stats["units_without_location"] += 1
                        stats["kw_without_location"] += kw

                    lage_key = next((k for k in u if k.startswith("Lage")), None)
                    lage = catalogue.get(u.get(lage_key, ""), "") if lage_key else ""
                    rooftop = any(w in lage.lower() for w in ("gebäude", "hausdach", "fassade"))

                    row = agg[(gem, land)]
                    row[0] += 1
                    row[1] += kw
                    if lage:
                        row[3] += kw
                        if rooftop:
                            row[2] += kw
                    if stats["units_seen"] % 500_000 == 0:
                        log.info("  %d units read", stats["units_seen"])
            log.info("Parsed %s", name)

    df = pd.DataFrame(
        [(g, l, *v) for (g, l), v in agg.items()],
        columns=["gemeinde_ags", "land_ags", "units", "capacity_kw",
                 "rooftop_kw", "lage_known_kw"],
    )
    stats = dict(stats)
    stats["status_counts"] = dict(status_counts.most_common())
    stats["capacity_kw_in_operation"] = float(df["capacity_kw"].sum())
    stats["files"] = len(files)
    stats["catalogue_used"] = bool(catalogue)
    return df, stats


def fetch() -> tuple[pd.DataFrame, dict, dict]:
    url = discover_url()
    path = download(url)
    df, stats = parse(path)
    m = ZIP_PATTERN.search(path.name)
    export_date = datetime.strptime(m.group(1), "%Y%m%d").date().isoformat() if m else None
    meta = {
        "source_id": "mastr",
        "name": "Marktstammdatenregister, Gesamtdatenexport (Einheiten Solar)",
        "publisher": "Bundesnetzagentur",
        "url": url,
        "licence": "dl-de/by-2-0",
        "attribution": "© Bundesnetzagentur, Marktstammdatenregister, dl-de/by-2-0",
        "ref_date": export_date,
    }
    return df, stats, meta
