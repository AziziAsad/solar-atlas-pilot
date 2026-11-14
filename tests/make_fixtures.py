"""Generate small synthetic test data in the exact formats of the real sources.

The data contains deliberately planted problems, so the tests can prove that
every quality check actually catches what it is meant to catch:
  - a unit in operation with no capacity value (missing must not become 0)
  - a Gemeindeschlüssel that no longer exists (territorial status mismatch)
  - a unit with only a Bundesland and no Gemeindeschlüssel
  - a unit whose Bundesland contradicts its Gemeindeschlüssel
  - one very large solar park (a robust outlier at Kreis level)
  - units that are planned or shut down (must be excluded)
  - a coastal-water polygon (GF = 2) that must not count as land

Run:  python -m tests.make_fixtures
"""
import io
import json
import random
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from pipeline.config import LAND_NAMES

FX = Path(__file__).parent / "fixtures"
PILOT = "12"
STATUS = {"35": "In Betrieb", "31": "In Planung", "38": "Endgültig stillgelegt"}
LAGE = {"852": "Bauliche Anlagen (Hausdach, Gebäude und Fassade)", "2484": "Freifläche"}
BL_IDS = {name: str(1400 + i) for i, name in enumerate(LAND_NAMES.values())}


def build_areas():
    lands, kreise, gemeinden = [], [], []
    for i, (code, name) in enumerate(LAND_NAMES.items()):
        x0, y0 = 300_000 + (i % 4) * 110_000, 5_300_000 + (i // 4) * 110_000
        n_kreise = 6 if code == PILOT else 2
        for k in range(n_kreise):
            kags = f"{code}{k + 1:03d}" if code != PILOT else f"{code}0{60 + k}"
            kx = x0 + k * (100_000 / n_kreise)
            kgeom = box(kx, y0, kx + 100_000 / n_kreise, y0 + 100_000)
            kpop = 0
            for g in range(4):
                gags = f"{kags}{g + 1:03d}"
                gy = y0 + g * 25_000
                pop = 80_000 + 30_000 * ((i + k + g) % 7)
                kpop += pop
                gemeinden.append(dict(AGS=gags, GEN=f"Gemeinde {gags}", BEZ="Gemeinde",
                                      GF=4, EWZ=pop, WSK="2025-12-31",
                                      geometry=box(kx, gy, kx + 100_000 / n_kreise, gy + 25_000)))
            kreise.append(dict(AGS=kags, GEN=f"Kreis {kags}", BEZ="Landkreis", GF=4,
                               EWZ=kpop, WSK="2025-12-31", geometry=kgeom))
        lpop = sum(g["EWZ"] for g in gemeinden if g["AGS"].startswith(code))
        lands.append(dict(AGS=code, GEN=name, BEZ="Land", GF=4, EWZ=lpop,
                          WSK="2025-12-31", geometry=box(x0, y0, x0 + 100_000, y0 + 100_000)))
    # Coastal water for Schleswig-Holstein: GF = 2, must be filtered out.
    lands.append(dict(AGS="01", GEN="Schleswig-Holstein", BEZ="Land", GF=2, EWZ=0,
                      WSK="2025-12-31", geometry=box(300_000, 5_250_000, 400_000, 5_299_000)))
    return lands, kreise, gemeinden


def write_gpkg(lands, kreise, gemeinden):
    gpkg = FX / "DE_VG250.gpkg"
    for layer, rows in (("vg250_lan", lands), ("vg250_krs", kreise), ("vg250_gem", gemeinden)):
        gpd.GeoDataFrame(rows, crs=25832).to_file(gpkg, layer=layer, driver="GPKG")
    with zipfile.ZipFile(FX / "vg250-ew_fixture.gpkg.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(gpkg, "vg250-ew_ebenen_1231/DE_VG250.gpkg")
    gpkg.unlink()


def xml_utf16(root: str, item: str, records: list[dict]) -> bytes:
    buf = io.StringIO()
    buf.write('<?xml version="1.0" encoding="UTF-16"?>\n')
    buf.write(f"<{root}>\n")
    for r in records:
        buf.write(f"  <{item}>")
        buf.write("".join(f"<{k}>{v}</{k}>" for k, v in r.items() if v is not None))
        buf.write(f"</{item}>\n")
    buf.write(f"</{root}>\n")
    return buf.getvalue().encode("utf-16")


def build_units(gemeinden):
    rnd = random.Random(42)
    units, expected = [], {"kw_in_operation": 0.0, "units_in_operation": 0,
                           "missing_capacity": 0, "without_gemeinde": 0, "land_mismatch": 0}

    def add(gs, bl, kw, status="35", lage="852"):
        u = {"EinheitMastrNummer": f"SEE{len(units):09d}", "Bundesland": bl,
             "Gemeindeschluessel": gs, "Bruttoleistung": kw,
             "EinheitBetriebsstatus": status, "Lage": lage}
        units.append(u)
        if status == "35" and kw is not None:
            expected["kw_in_operation"] += float(kw)
        if status == "35":
            expected["units_in_operation"] += 1

    for g in gemeinden:
        bl = BL_IDS[LAND_NAMES[g["AGS"][:2]]]
        for _ in range(rnd.randint(20, 40)):
            add(g["AGS"], bl, f"{rnd.uniform(3, 30):.3f}")
        add(g["AGS"], bl, f"{rnd.uniform(500, 5000):.3f}", lage="2484")
        add(g["AGS"], bl, "9.9", status="31")   # planned: excluded
        add(g["AGS"], bl, "7.5", status="38")   # shut down: excluded

    bb = BL_IDS["Brandenburg"]
    add("12061002", bb, "300000.000", lage="2484")          # huge park: outlier
    add(None, bb, None)                                     # missing capacity
    expected["missing_capacity"] += 1
    add("12099999", bb, "40.000")                           # merged-away Gemeinde
    add(None, bb, "12.000")                                 # only Bundesland known
    expected["without_gemeinde"] += 1
    add("09001001", BL_IDS["Hessen"], "8.000")              # Bundesland contradicts code
    expected["land_mismatch"] += 1
    rnd.shuffle(units)
    return units, expected


def main():
    FX.mkdir(exist_ok=True)
    lands, kreise, gemeinden = build_areas()
    write_gpkg(lands, kreise, gemeinden)
    units, expected = build_units(gemeinden)
    catalogue = [{"Id": k, "KatalogKategorieId": "1", "Wert": v}
                 for k, v in {**STATUS, **LAGE, **{v: k for k, v in BL_IDS.items()}}.items()]
    half = len(units) // 2
    with zipfile.ZipFile(FX / "Gesamtdatenexport_20261101_25.2.zip", "w",
                         zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Katalogwerte.xml", xml_utf16("Katalogwerte", "Katalogwert", catalogue))
        zf.writestr("EinheitenSolar_1.xml", xml_utf16("EinheitenSolar", "EinheitSolar", units[:half]))
        zf.writestr("EinheitenSolar_2.xml", xml_utf16("EinheitenSolar", "EinheitSolar", units[half:]))
    (FX / "expected.json").write_text(json.dumps(expected, indent=2))
    print("Fixtures written to", FX, expected)


if __name__ == "__main__":
    main()
