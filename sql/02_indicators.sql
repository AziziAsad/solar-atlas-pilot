-- Turn staged MaStR totals into indicators for every area level.
--
-- Land totals use every unit with a known Land (complete).
-- Kreis and Gemeinde totals use units whose Gemeindeschluessel matches a
-- VG250 Gemeinde. The checks report how much capacity that leaves out.

INSERT INTO core.indicator VALUES
  ('pv_w_per_capita', 'PV-Leistung je Einwohner', 'W/EW',
   'Installierte Bruttoleistung von Photovoltaikanlagen in Betrieb je Einwohnerin und Einwohner', 0,
   ARRAY['mastr', 'bkg_vg250_ew']),
  ('pv_capacity_mw', 'Installierte PV-Leistung', 'MW',
   'Summe der Bruttoleistung aller Photovoltaikanlagen in Betrieb', 1, ARRAY['mastr']),
  ('pv_units_per_1000', 'PV-Anlagen je 1.000 Einwohner', 'Anlagen/1.000 EW',
   'Anzahl der Photovoltaikanlagen in Betrieb je 1.000 Einwohnerinnen und Einwohner', 1,
   ARRAY['mastr', 'bkg_vg250_ew']),
  ('pv_rooftop_share', 'Anteil Gebäude-PV', '%',
   'Anteil der Leistung auf oder an Gebäuden an der Leistung mit bekannter Lage', 1, ARRAY['mastr'])
ON CONFLICT (indicator_id) DO UPDATE
  SET name_de = EXCLUDED.name_de, unit = EXCLUDED.unit,
      description_de = EXCLUDED.description_de, decimals = EXCLUDED.decimals;

DELETE FROM core.observation WHERE period = (SELECT period FROM core.run_context);

CREATE TEMP TABLE totals ON COMMIT DROP AS
WITH matched AS (
    SELECT r.* FROM raw.mastr_solar_agg r
    JOIN core.area g ON g.ags = r.gemeinde_ags AND g.level = 'gemeinde'
),
by_gemeinde AS (
    SELECT gemeinde_ags AS ags, sum(units) units, sum(capacity_kw) kw,
           sum(rooftop_kw) roof, sum(lage_known_kw) known
    FROM matched GROUP BY 1
),
by_kreis AS (
    SELECT left(gemeinde_ags, 5) AS ags, sum(units), sum(capacity_kw),
           sum(rooftop_kw), sum(lage_known_kw)
    FROM matched GROUP BY 1
),
by_land AS (
    SELECT land_ags AS ags, sum(units), sum(capacity_kw),
           sum(rooftop_kw), sum(lage_known_kw)
    FROM raw.mastr_solar_agg WHERE land_ags IS NOT NULL GROUP BY 1
)
SELECT a.ags, a.population, t.units, t.kw, t.roof, t.known
FROM core.area a
LEFT JOIN (
    SELECT * FROM by_gemeinde
    UNION ALL SELECT * FROM by_kreis
    UNION ALL SELECT * FROM by_land
) t ON t.ags = a.ags;

-- An area with no registered unit has genuinely 0 kW (the register is complete
-- by law), so NULL totals become 0 here. A missing population does NOT: those
-- per-head values stay NULL with status 'missing'.
INSERT INTO core.observation (indicator_id, ags, period, value, status)
SELECT i.indicator_id, t.ags, c.period, v.value,
       CASE WHEN v.value IS NULL THEN 'missing' ELSE 'ok' END
FROM totals t
CROSS JOIN core.run_context c
CROSS JOIN LATERAL (VALUES
    ('pv_capacity_mw',    coalesce(t.kw, 0) / 1000.0),
    ('pv_w_per_capita',   coalesce(t.kw, 0) * 1000.0 / nullif(t.population, 0)),
    ('pv_units_per_1000', coalesce(t.units, 0) * 1000.0 / nullif(t.population, 0)),
    ('pv_rooftop_share',  100.0 * t.roof / nullif(t.known, 0))
) AS v(indicator_id, value)
JOIN core.indicator i USING (indicator_id);
