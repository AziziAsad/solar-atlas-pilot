"""Central settings for the pipeline.

Everything that might change between runs or between pilot regions lives here,
so the code itself never hardcodes a URL, a region or a threshold.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = Path(os.environ.get("ATLAS_DOWNLOAD_DIR", ROOT / "downloads"))
WEB_DATA_DIR = Path(os.environ.get("ATLAS_WEB_DATA_DIR", ROOT / "web" / "data"))
SQL_DIR = ROOT / "sql"

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/atlas"
)

# Pilot region shown down to Kreis and Gemeinde level (AGS Land code).
# 12 = Brandenburg. Any Land code works without code changes.
PILOT_LAND = os.environ.get("ATLAS_PILOT_LAND", "12")

# --- Sources -----------------------------------------------------------------
# BKG VG250-EW: official administrative boundaries with population (Stand 31.12.).
# The file name changes every year, so we discover it from the directory listing.
BKG_BASE_URL = "https://daten.gdz.bkg.bund.de/produkte/vg/"
BKG_URL_OVERRIDE = os.environ.get("BKG_URL")  # set to skip discovery

# MaStR Gesamtdatenexport (Bundesnetzagentur). The zip name contains the export
# date and schema version, so we discover it from the download page.
MASTR_PAGE_URL = "https://www.marktstammdatenregister.de/MaStR/Datendownload"
MASTR_URL_OVERRIDE = os.environ.get("MASTR_URL")

# The last published meta.json. Its national total is compared with the new run,
# so the state between runs lives in the published output, not in a database.
PREVIOUS_META_URL = os.environ.get("PREVIOUS_META_URL")

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) solar-atlas-pilot/1.0 (open-data research)"
}

# --- Quality thresholds ---------------------------------------------------------
# Errors stop the pipeline (nothing is published). Warnings are published
# alongside the data so readers see them.
MIN_GEMEINDE_MATCH_SHARE = 0.98   # capacity share that must join to a VG250 Gemeinde
MAX_UNASSIGNED_SHARE = 0.01       # capacity share with no usable location at all
PLAUSIBLE_PV_GW = (50, 400)       # national PV capacity in operation
PLAUSIBLE_POPULATION_M = (80, 86) # national population in millions
MAX_CHANGE_VS_LAST_RUN = 0.10     # national capacity change between two runs
ROBUST_Z_LIMIT = 3.5              # modified z-score (median/MAD) for outlier flags

LAND_NAMES = {
    "01": "Schleswig-Holstein", "02": "Hamburg", "03": "Niedersachsen",
    "04": "Bremen", "05": "Nordrhein-Westfalen", "06": "Hessen",
    "07": "Rheinland-Pfalz", "08": "Baden-Württemberg", "09": "Bayern",
    "10": "Saarland", "11": "Berlin", "12": "Brandenburg",
    "13": "Mecklenburg-Vorpommern", "14": "Sachsen", "15": "Sachsen-Anhalt",
    "16": "Thüringen",
}
