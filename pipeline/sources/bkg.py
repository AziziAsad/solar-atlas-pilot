"""BKG VG250-EW: administrative boundaries with population figures.

One download gives us geometry AND population for every level (Land, Kreis,
Gemeinde) on the same territorial status, which keeps per-head figures
consistent. Licence: dl-de/by-2-0, (c) BKG.
"""
import logging
import re
import zipfile
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import requests

from pipeline import config
from pipeline.sources.common import download, list_links

log = logging.getLogger(__name__)

LAYERS = {"land": "vg250_lan", "kreis": "vg250_krs", "gemeinde": "vg250_gem"}
LAND_SURFACE = 4  # GF = 4: land area with structure (excludes coastal waters)
_AKTUELL = "https://daten.gdz.bkg.bund.de/produkte/vg/vg250-ew_ebenen_1231/aktuell/"
KNOWN_URLS = [_AKTUELL + "vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
              _AKTUELL + "vg250-ew_12-31.utm32s.gpkg.zip"]


def discover_url() -> str:
    """Find the current VG250-EW GeoPackage zip on the BKG download server."""
    if config.BKG_URL_OVERRIDE:
        return config.BKG_URL_OVERRIDE
    try:
        product_dirs = [
            u for u in list_links(config.BKG_BASE_URL)
            if "vg250-ew" in u.lower() and u.endswith("/")
        ]
    except Exception as exc:
        log.warning("BKG directory listing failed (%s); trying known paths", exc)
        product_dirs = []
    product_dirs += [config.BKG_BASE_URL + "vg250-ew_ebenen_1231/"]
    # Prefer the 31.12. product, which carries the population figures.
    product_dirs.sort(key=lambda u: ("1231" not in u, u))
    for product in product_dirs:
        for folder in (product + "aktuell/", product):
            try:
                links = list_links(folder)
            except Exception:  # folder may not exist
                continue
            zips = [u for u in links if u.lower().endswith(".zip") and "gpkg" in u.lower()]
            zips.sort(key=lambda u: ("utm32s" not in u.lower(), u))
            if zips:
                log.info("BKG source: %s", zips[0])
                return zips[0]
    for candidate in KNOWN_URLS:  # last resort: published file names
        try:
            if requests.head(candidate, headers=config.HTTP_HEADERS, timeout=30,
                             allow_redirects=True).ok:
                log.info("BKG source (known path): %s", candidate)
                return candidate
        except requests.RequestException:
            pass
    raise RuntimeError(
        "Could not find a VG250-EW GeoPackage on the BKG server. "
        "Set BKG_URL to the zip URL from https://gdz.bkg.bund.de to continue."
    )


def _find_layer(gpkg: str, wanted: str) -> str:
    names = [row[0] for row in pyogrio.list_layers(gpkg)]
    for n in names:
        if n.lower() == wanted:
            return n
    raise RuntimeError(f"Layer {wanted} not found in {gpkg}. Layers: {names}")


def read_levels(zip_path: Path) -> tuple[dict[str, gpd.GeoDataFrame], date | None]:
    """Return one GeoDataFrame per level: ags, name, bez, population, geometry."""
    with zipfile.ZipFile(zip_path) as zf:
        inner = [n for n in zf.namelist() if n.lower().endswith(".gpkg")]
    if not inner:
        raise RuntimeError(f"No .gpkg inside {zip_path.name}")
    gpkg = f"/vsizip/{zip_path}/{inner[0]}"

    levels, ref_dates = {}, []
    for level, layer in LAYERS.items():
        gdf = pyogrio.read_dataframe(gpkg, layer=_find_layer(gpkg, layer))
        gdf.columns = [c.lower() if c != "geometry" else c for c in gdf.columns]
        if "gf" in gdf.columns:
            gdf = gdf[gdf["gf"].astype(int) == LAND_SURFACE]
        if "wsk" in gdf.columns:
            ref_dates.append(pd.to_datetime(gdf["wsk"], errors="coerce").max())
        gdf = gdf.to_crs(25832)
        out = gdf.dissolve(
            by="ags",
            aggfunc={"gen": "first", "bez": "first", "ewz": "max"},
            as_index=False,
        ).rename(columns={"gen": "name", "ewz": "population"})
        out["level"] = level
        levels[level] = out[["ags", "name", "bez", "population", "level", "geometry"]]
        log.info("BKG %s: %d areas", level, len(out))

    ref = max((d for d in ref_dates if pd.notna(d)), default=None)
    return levels, (ref.date() if ref is not None else None)


def fetch() -> tuple[dict[str, gpd.GeoDataFrame], dict]:
    url = discover_url()
    path = download(url)
    levels, ref_date = read_levels(path)
    year = re.search(r"(20\d\d)", url)
    meta = {
        "source_id": "bkg_vg250_ew",
        "name": "Verwaltungsgebiete 1:250 000 mit Einwohnerzahlen (VG250-EW)",
        "publisher": "Bundesamt für Kartographie und Geodäsie (BKG)",
        "url": url,
        "licence": "dl-de/by-2-0",
        "attribution": f"© BKG ({year.group(1) if year else date.today().year}) dl-de/by-2-0, "
                       "Datenquellen: https://sgx.geodatenzentrum.de/web_public/gdz/"
                       "datenquellen/datenquellen_vg_nuts.pdf",
        "ref_date": ref_date.isoformat() if ref_date else None,
    }
    return levels, meta
