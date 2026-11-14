-- Data model for the atlas pilot.
--
-- Design choice: indicators are stored in LONG format (one row per indicator,
-- area and period). Adding a new theme (rents, jobs, wages, infrastructure) means
-- new rows in core.indicator and core.observation, not new columns or tables.
-- Every area level (Land, Kreis, Gemeinde, later grid cells) sits in ONE table
-- linked by parent_ags, so the same queries work at every spatial level.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;

-- Where every number comes from, with licence and reference date.
CREATE TABLE IF NOT EXISTS core.source (
    source_id    text PRIMARY KEY,
    name         text NOT NULL,
    publisher    text NOT NULL,
    url          text NOT NULL,
    licence      text NOT NULL,
    attribution  text NOT NULL,
    ref_date     date,
    retrieved_at timestamptz NOT NULL DEFAULT now()
);

-- All administrative areas on one territorial status (VG250-EW).
CREATE TABLE IF NOT EXISTS core.area (
    ags        text PRIMARY KEY,
    level      text NOT NULL CHECK (level IN ('land', 'kreis', 'gemeinde')),
    name       text NOT NULL,
    bez        text,
    parent_ags text,
    land_ags   text NOT NULL,
    population bigint,
    ref_date   date,
    geom       geometry(MultiPolygon, 25832) NOT NULL
);
CREATE INDEX IF NOT EXISTS area_geom_idx  ON core.area USING gist (geom);
CREATE INDEX IF NOT EXISTS area_level_idx ON core.area (level, land_ags);

CREATE TABLE IF NOT EXISTS core.indicator (
    indicator_id   text PRIMARY KEY,
    name_de        text NOT NULL,
    unit           text NOT NULL,
    description_de text NOT NULL,
    decimals       int  NOT NULL DEFAULT 0,
    source_ids     text[] NOT NULL
);

-- value is NULL with status 'missing' when it cannot be computed.
-- Missing is never stored as 0.
CREATE TABLE IF NOT EXISTS core.observation (
    indicator_id text NOT NULL REFERENCES core.indicator,
    ags          text NOT NULL REFERENCES core.area,
    period       date NOT NULL,
    value        double precision,
    status       text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'missing')),
    PRIMARY KEY (indicator_id, ags, period)
);

-- Staging: MaStR solar units already reduced to one row per Gemeinde key.
CREATE TABLE IF NOT EXISTS raw.mastr_solar_agg (
    gemeinde_ags  text,
    land_ags      text,
    units         bigint NOT NULL,
    capacity_kw   double precision NOT NULL,
    rooftop_kw    double precision NOT NULL,
    lage_known_kw double precision NOT NULL
);

-- One row: the reference period of the current run.
CREATE TABLE IF NOT EXISTS core.run_context (
    id     int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    period date NOT NULL
);
