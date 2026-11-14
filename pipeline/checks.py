"""Automated quality checks, run on every update before anything is published.

Two severities:
  error   -> the run stops, the previously published version stays online
  warning -> the run publishes, and the warning is shown on the page

The checks follow four rules:
  1. Reconcile against the source: totals counted while streaming the source
     must equal what ends up in the database and in the indicators.
  2. Missing is not zero: unknown values are counted and reported, never filled with 0.
  3. Keep the territorial status consistent: codes that do not match the
     boundary vintage are measured, not silently dropped.
  4. Flag outliers with robust statistics (median and MAD), because regional
     data is heavily skewed and a mean/standard-deviation rule hides outliers.
"""
import logging
import statistics

import requests

from pipeline import config
from pipeline.db import query

log = logging.getLogger(__name__)


def _check(cid, title, severity, passed, detail, value=None):
    return {"id": cid, "title": title, "severity": severity,
            "passed": bool(passed), "detail": detail, "value": value}


def _fmt(x, d=0):
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def robust_z(values: dict[str, float]) -> dict[str, float]:
    """Modified z-score (Iglewicz and Hoaglin): 0.6745 * (x - median) / MAD."""
    xs = [v for v in values.values() if v is not None]
    if len(xs) < 5:
        return {}
    med = statistics.median(xs)
    mad = statistics.median(abs(x - med) for x in xs)
    if mad == 0:
        return {}
    return {k: 0.6745 * (v - med) / mad for k, v in values.items() if v is not None}


def previous_national_mw() -> float | None:
    url = config.PREVIOUS_META_URL
    if not url:
        return None
    try:
        meta = requests.get(url, timeout=20).json()
        return float(meta["national"]["pv_capacity_mw"])
    except Exception as exc:  # first run, or the page is not online yet
        log.info("No previous run to compare with (%s)", exc)
        return None


def run_all(conn, mastr_stats: dict) -> list[dict]:
    checks = []

    # 1. Structure: 16 Länder with geometry and population
    lands = query(conn, "SELECT ags, population FROM core.area WHERE level = 'land'")
    n_pop = sum(1 for r in lands if r["population"])
    checks.append(_check(
        "structure", "Alle 16 Bundesländer mit Fläche und Einwohnerzahl", "error",
        len(lands) == 16 and n_pop == 16,
        f"{len(lands)} Länder geladen, {n_pop} mit Einwohnerzahl."))

    # 2. Population plausible and consistent across levels
    total_pop = sum(r["population"] or 0 for r in lands)
    lo, hi = config.PLAUSIBLE_POPULATION_M
    checks.append(_check(
        "population_total", "Einwohnerzahl Deutschland plausibel", "warning",
        lo * 1e6 <= total_pop <= hi * 1e6,
        f"{_fmt(total_pop)} Einwohner (erwartet {lo} bis {hi} Mio.).", total_pop))

    gaps = query(conn, """
        SELECT l.ags, l.population AS land_pop, sum(g.population) AS gem_pop
        FROM core.area l JOIN core.area g ON g.land_ags = l.ags AND g.level = 'gemeinde'
        WHERE l.level = 'land' GROUP BY 1, 2
        HAVING abs(sum(g.population) - l.population) > 0.001 * l.population""")
    checks.append(_check(
        "population_hierarchy", "Einwohner der Gemeinden ergeben die Landessumme", "error",
        not gaps,
        "Alle Länder stimmen überein." if not gaps else
        "Abweichung in: " + ", ".join(config.LAND_NAMES[g["ags"]] for g in gaps)))

    # 3. Schema drift: are the source's codes still understood?
    seen = max(mastr_stats.get("units_seen", 0), 1)
    unresolved = mastr_stats.get("status_unresolved", 0) / seen
    in_op = mastr_stats.get("units_in_operation", 0)
    checks.append(_check(
        "schema", "Quelle wird korrekt gelesen (Statuscodes und Anlagen erkannt)", "error",
        in_op > 0 and unresolved <= 0.05,
        f"{_fmt(in_op)} Anlagen in Betrieb erkannt, {_fmt(100 * unresolved, 2)} % der "
        "Statuscodes nicht im Katalog der Quelle.", unresolved))

    # 4. Reconciliation against the source stream
    src_kw = mastr_stats["capacity_kw_in_operation"]
    db_kw = query(conn, "SELECT coalesce(sum(capacity_kw),0) AS kw FROM raw.mastr_solar_agg")[0]["kw"]
    land_mw = query(conn, """
        SELECT coalesce(sum(o.value),0) AS mw FROM core.observation o
        JOIN core.area a USING (ags) JOIN core.run_context c USING (period)
        WHERE a.level = 'land' AND o.indicator_id = 'pv_capacity_mw'""")[0]["mw"]
    located_kw = src_kw - mastr_stats.get("kw_without_location", 0)
    ok = abs(db_kw - src_kw) < 1 and abs(land_mw * 1000 - located_kw) < 1
    checks.append(_check(
        "reconciliation", "Abgleich mit der Quelle: keine Leistung verloren", "error", ok,
        f"Quelle {_fmt(src_kw / 1e6, 2)} GW, Datenbank {_fmt(db_kw / 1e6, 2)} GW, "
        f"Summe der Länder {_fmt(land_mw / 1000, 2)} GW.", src_kw))

    # 4. Location coverage
    share_unloc = mastr_stats.get("kw_without_location", 0) / src_kw if src_kw else 1
    checks.append(_check(
        "location", "Anlagen mit bekanntem Bundesland", "error",
        share_unloc <= config.MAX_UNASSIGNED_SHARE,
        f"{_fmt(100 * share_unloc, 2)} % der Leistung ohne Standortangabe "
        f"({_fmt(mastr_stats.get('units_without_location', 0))} Anlagen).", share_unloc))

    # 5. Territorial status: do MaStR Gemeindeschlüssel match VG250 Gemeinden?
    match = query(conn, """
        SELECT sum(r.capacity_kw) FILTER (WHERE g.ags IS NOT NULL) AS matched,
               sum(r.capacity_kw) FILTER (WHERE r.gemeinde_ags IS NOT NULL) AS with_code
        FROM raw.mastr_solar_agg r
        LEFT JOIN core.area g ON g.ags = r.gemeinde_ags AND g.level = 'gemeinde'""")[0]
    share = (match["matched"] or 0) / (match["with_code"] or 1)
    unmatched = query(conn, """
        SELECT r.gemeinde_ags, sum(r.capacity_kw) AS kw FROM raw.mastr_solar_agg r
        LEFT JOIN core.area g ON g.ags = r.gemeinde_ags AND g.level = 'gemeinde'
        WHERE r.gemeinde_ags IS NOT NULL AND g.ags IS NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 5""")
    checks.append(_check(
        "territory", "Gemeindeschlüssel passen zum Gebietsstand der Karte", "error",
        share >= config.MIN_GEMEINDE_MATCH_SHARE,
        f"{_fmt(100 * share, 2)} % der Leistung zugeordnet. "
        + ("Nicht zuordenbar (meist Gemeindefusionen): "
           + ", ".join(u["gemeinde_ags"] for u in unmatched) + "."
           if unmatched else "Alle Schlüssel gefunden."), share))

    # 6. Missing is not zero
    miss = mastr_stats.get("units_missing_capacity", 0)
    checks.append(_check(
        "missing_capacity", "Fehlende Leistungsangaben getrennt erfasst", "warning",
        True,
        f"{_fmt(miss)} Anlagen in Betrieb ohne Leistungsangabe: gezählt, aber nicht als 0 kW "
        "verbucht.", miss))

    # 7. Internal consistency of the register
    mism = mastr_stats.get("units_land_mismatch", 0)
    checks.append(_check(
        "land_mismatch", "Bundesland und Gemeindeschlüssel widerspruchsfrei", "warning",
        mism <= 0.001 * max(mastr_stats.get("units_in_operation", 1), 1),
        f"{_fmt(mism)} Anlagen mit widersprüchlichen Angaben; maßgeblich ist der "
        "Gemeindeschlüssel.", mism))

    # 8. National plausibility and change versus the last published run
    gw = src_kw / 1e6
    lo, hi = config.PLAUSIBLE_PV_GW
    checks.append(_check(
        "national_range", "PV-Leistung Deutschland im plausiblen Bereich", "warning",
        lo <= gw <= hi, f"{_fmt(gw, 1)} GW in Betrieb (Plausibilitätsband {lo} bis {hi} GW).", gw))

    prev = previous_national_mw()
    if prev:
        change = (src_kw / 1000 - prev) / prev
        checks.append(_check(
            "change", "Veränderung gegenüber letzter Veröffentlichung", "warning",
            abs(change) <= config.MAX_CHANGE_VS_LAST_RUN,
            f"{_fmt(100 * change, 1)} % gegenüber {_fmt(prev / 1000, 2)} GW.", change))

    # 9. Robust outliers among all Kreise (flagged for review, never removed)
    rows = query(conn, """
        SELECT a.ags, a.name, o.value FROM core.observation o
        JOIN core.area a USING (ags) JOIN core.run_context c USING (period)
        WHERE a.level = 'kreis' AND o.indicator_id = 'pv_w_per_capita'""")
    z = robust_z({r["ags"]: r["value"] for r in rows})
    names = {r["ags"]: r["name"] for r in rows}
    flagged = sorted((k for k, v in z.items() if abs(v) > config.ROBUST_Z_LIMIT),
                     key=lambda k: -abs(z[k]))
    checks.append(_check(
        "outliers", "Auffällige Kreise (robuste Ausreißerprüfung)", "warning",
        True,
        f"{len(flagged)} von {len(z)} Kreisen mit modifiziertem z-Wert über "
        f"{config.ROBUST_Z_LIMIT} markiert, zur fachlichen Prüfung: "
        + (", ".join(names[k] for k in flagged[:6]) if flagged else "keine") + ".",
        {k: round(z[k], 2) for k in flagged}))

    for c in checks:
        mark = "OK  " if c["passed"] else ("FAIL" if c["severity"] == "error" else "WARN")
        log.info("[%s] %s: %s", mark, c["title"], c["detail"])
    return checks
