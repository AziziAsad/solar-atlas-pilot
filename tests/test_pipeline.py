"""End to end on synthetic data: load into PostGIS, compute indicators, run all
checks, export. Skipped when no database is reachable."""
import json

import psycopg
import pytest

from pipeline import config, run


def _db_available():
    try:
        psycopg.connect(config.DATABASE_URL, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no PostGIS database")


@pytest.fixture(scope="module")
def meta(tmp_path_factory):
    config.WEB_DATA_DIR = tmp_path_factory.mktemp("web")
    assert run.main(["--fixtures"]) == 0
    return json.loads((config.WEB_DATA_DIR / "meta.json").read_text())


def checks(meta):
    return {c["id"]: c for c in meta["checks"]}


def test_all_error_checks_pass(meta):
    assert meta["status"] == "ok"


def test_reconciliation_against_source(meta):
    assert checks(meta)["reconciliation"]["passed"]


def test_merged_gemeinde_is_detected(meta):
    assert "12099999" in checks(meta)["territory"]["detail"]


def test_solar_park_is_flagged_as_outlier(meta):
    assert "12061" in checks(meta)["outliers"]["value"]


def test_implausible_national_population_is_warned(meta):
    # the synthetic country is too small: the warning must fire
    assert not checks(meta)["population_total"]["passed"]


def test_layers_exported(meta):
    assert meta["layers"] == {"laender": 16, "pilot_kreise": 6, "pilot_gemeinden": 24}
    fc = json.loads((config.WEB_DATA_DIR / "laender.geojson").read_text())
    assert all("pv_w_per_capita" in f["properties"] for f in fc["features"])
