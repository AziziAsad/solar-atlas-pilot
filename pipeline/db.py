"""Database access: load staged data into PostGIS and run the SQL steps."""
import json
import logging

import geopandas as gpd
import pandas as pd
import psycopg
from shapely.geometry import MultiPolygon

from pipeline import config

log = logging.getLogger(__name__)


def connect() -> psycopg.Connection:
    return psycopg.connect(config.DATABASE_URL)


def run_sql_file(conn: psycopg.Connection, name: str) -> None:
    sql = (config.SQL_DIR / name).read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    log.info("Ran %s", name)


def _multi(geom):
    return geom if isinstance(geom, MultiPolygon) else MultiPolygon([geom])


def load_areas(conn, levels: dict[str, gpd.GeoDataFrame], ref_date) -> None:
    rows = []
    for level, gdf in levels.items():
        for r in gdf.itertuples(index=False):
            ags = str(r.ags)
            parent = {"land": None, "kreis": ags[:2], "gemeinde": ags[:5]}[level]
            pop = None if pd.isna(r.population) else int(r.population)
            rows.append((ags, level, r.name, r.bez, parent, ags[:2], pop, ref_date,
                         _multi(r.geometry).wkb))
    with conn.cursor() as cur:
        cur.execute("TRUNCATE core.area CASCADE")
        cur.executemany(
            "INSERT INTO core.area VALUES (%s,%s,%s,%s,%s,%s,%s,%s,"
            "ST_Multi(ST_GeomFromWKB(%s, 25832)))",
            rows,
        )
    log.info("Loaded %d areas", len(rows))


def load_mastr(conn, df: pd.DataFrame) -> None:
    df = df.astype(object).where(df.notna(), None)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE raw.mastr_solar_agg")
        with cur.copy("COPY raw.mastr_solar_agg FROM STDIN") as copy:
            for row in df.itertuples(index=False):
                copy.write_row(row)
    log.info("Loaded %d MaStR aggregate rows", len(df))


def load_sources(conn, metas: list[dict]) -> None:
    with conn.cursor() as cur:
        for m in metas:
            cur.execute(
                """INSERT INTO core.source (source_id, name, publisher, url, licence,
                                            attribution, ref_date)
                   VALUES (%(source_id)s, %(name)s, %(publisher)s, %(url)s, %(licence)s,
                           %(attribution)s, %(ref_date)s)
                   ON CONFLICT (source_id) DO UPDATE SET
                     name = EXCLUDED.name, url = EXCLUDED.url, ref_date = EXCLUDED.ref_date,
                     attribution = EXCLUDED.attribution, retrieved_at = now()""",
                m,
            )


def set_period(conn, period) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO core.run_context (id, period) VALUES (1, %s) "
            "ON CONFLICT (id) DO UPDATE SET period = EXCLUDED.period",
            (period,),
        )


def query(conn, sql: str, params=None) -> list[dict]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
