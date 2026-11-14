"""Export map layers (GeoJSON) and run metadata (JSON) for the static web page."""
import json
import logging
from datetime import datetime, timezone

from pipeline import config
from pipeline.db import query

log = logging.getLogger(__name__)

# name -> (level, restrict to pilot Land?, simplification tolerance in metres)
LAYERS = {
    "laender": ("land", False, 400),
    "pilot_kreise": ("kreis", True, 120),
    "pilot_gemeinden": ("gemeinde", True, 60),
}


def _layer(conn, level: str, pilot_only: bool, tol: int) -> dict:
    rows = query(conn, """
        SELECT a.ags, a.name, a.bez, a.population,
               jsonb_object_agg(o.indicator_id, round(o.value::numeric, 3))
                   FILTER (WHERE o.indicator_id IS NOT NULL) AS values,
               ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(a.geom, %(tol)s), 4326), 5)
                   AS geometry
        FROM core.area a
        LEFT JOIN core.observation o
               ON o.ags = a.ags AND o.period = (SELECT period FROM core.run_context)
        WHERE a.level = %(level)s AND (NOT %(pilot)s OR a.land_ags = %(land)s)
        GROUP BY a.ags, a.name, a.bez, a.population, a.geom
        ORDER BY a.ags""",
        {"level": level, "pilot": pilot_only, "land": config.PILOT_LAND, "tol": tol})
    features = [{
        "type": "Feature",
        "id": int(r["ags"]),
        "properties": {"ags": r["ags"], "name": r["name"], "bez": r["bez"],
                       "population": r["population"],
                       **{k: (float(v) if v is not None else None)
                          for k, v in (r["values"] or {}).items()}},
        "geometry": json.loads(r["geometry"]),
    } for r in rows]
    return {"type": "FeatureCollection", "features": features}


def write_all(conn, checks: list[dict], mastr_stats: dict, mode: str) -> dict:
    out = config.WEB_DATA_DIR
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, (level, pilot, tol) in LAYERS.items():
        fc = _layer(conn, level, pilot, tol)
        (out / f"{name}.geojson").write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
        counts[name] = len(fc["features"])
        log.info("Wrote %s.geojson (%d features)", name, counts[name])

    period = query(conn, "SELECT period FROM core.run_context")[0]["period"]
    indicators = query(conn, "SELECT * FROM core.indicator ORDER BY indicator_id")
    sources = query(conn, "SELECT * FROM core.source ORDER BY source_id")
    pop = int(query(conn, "SELECT coalesce(sum(population), 0) AS p FROM core.area "
                          "WHERE level='land'")[0]["p"])
    kw = mastr_stats["capacity_kw_in_operation"]
    meta = {
        "mode": mode,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "period": period.isoformat(),
        "pilot_land": {"ags": config.PILOT_LAND, "name": config.LAND_NAMES[config.PILOT_LAND]},
        "national": {
            "pv_capacity_mw": round(kw / 1000, 1),
            "units": mastr_stats.get("units_in_operation"),
            "population": pop,
            "pv_w_per_capita": round(kw * 1000 / pop, 1) if pop else None,
        },
        "indicators": indicators,
        "sources": sources,
        "checks": checks,
        "layers": counts,
        "status": "ok" if all(c["passed"] or c["severity"] != "error" for c in checks) else "failed",
    }
    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info("Wrote meta.json")
    return meta
