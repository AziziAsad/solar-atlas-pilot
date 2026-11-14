"""Run the whole pipeline: fetch -> load -> transform -> check -> export.

    python -m pipeline.run              # live sources (BKG and MaStR)
    python -m pipeline.run --fixtures   # small synthetic test data, no download

Everything happens in one database transaction. If an error-level check fails,
the transaction is rolled back, nothing is exported, and the exit code is 1,
so the previously published version of the map stays online.
"""
import argparse
import json
import logging
import re
import sys
from datetime import date, datetime

from pipeline import checks, config, db, export
from pipeline.sources import bkg, mastr

log = logging.getLogger("pipeline")


def fetch_fixtures():
    fx = config.ROOT / "tests" / "fixtures"
    levels, ref = bkg.read_levels(fx / "vg250-ew_fixture.gpkg.zip")
    bkg_meta = {"source_id": "bkg_vg250_ew", "name": "VG250-EW (TESTDATEN)",
                "publisher": "Synthetische Testdaten", "url": "tests/fixtures",
                "licence": "-", "attribution": "Synthetische Testdaten",
                "ref_date": ref.isoformat() if ref else None}
    zip_path = next(fx.glob("Gesamtdatenexport_*.zip"))
    df, stats = mastr.parse(zip_path)
    d = re.search(r"_(\d{8})_", zip_path.name).group(1)
    mastr_meta = {"source_id": "mastr", "name": "MaStR (TESTDATEN)",
                  "publisher": "Synthetische Testdaten", "url": "tests/fixtures",
                  "licence": "-", "attribution": "Synthetische Testdaten",
                  "ref_date": datetime.strptime(d, "%Y%m%d").date().isoformat()}
    return levels, bkg_meta, df, stats, mastr_meta


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", action="store_true", help="use synthetic test data")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.fixtures:
        levels, bkg_meta, mastr_df, mastr_stats, mastr_meta = fetch_fixtures()
    else:
        levels, bkg_meta = bkg.fetch()
        mastr_df, mastr_stats, mastr_meta = mastr.fetch()
    log.info("MaStR: %s units read, %s in operation",
             mastr_stats["units_seen"], mastr_stats["units_in_operation"])

    period = date.fromisoformat(mastr_meta["ref_date"]) if mastr_meta["ref_date"] else date.today()
    with db.connect() as conn:
        db.run_sql_file(conn, "01_schema.sql")
        db.load_areas(conn, levels, bkg_meta["ref_date"])
        db.load_mastr(conn, mastr_df)
        db.load_sources(conn, [bkg_meta, mastr_meta])
        db.set_period(conn, period)
        db.run_sql_file(conn, "02_indicators.sql")

        results = checks.run_all(conn, mastr_stats)
        failed = [c for c in results if not c["passed"] and c["severity"] == "error"]
        config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        (config.DOWNLOAD_DIR / "last_checks.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if failed:
            conn.rollback()
            log.error("%d error-level check(s) failed; nothing published: %s",
                      len(failed), ", ".join(c["id"] for c in failed))
            return 1

        export.write_all(conn, results, mastr_stats, "fixtures" if args.fixtures else "live")
        conn.commit()
    log.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
