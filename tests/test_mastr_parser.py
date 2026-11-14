"""The MaStR parser must count exactly what the source contains, and handle the
planted problems in the synthetic export the way the rules say."""
import json
from pathlib import Path

import pytest

from pipeline.sources import mastr

FX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def parsed():
    zip_path = next(FX.glob("Gesamtdatenexport_*.zip"))
    return (*mastr.parse(zip_path), json.loads((FX / "expected.json").read_text()))


def test_only_units_in_operation_are_counted(parsed):
    df, stats, exp = parsed
    assert stats["units_in_operation"] == exp["units_in_operation"]
    assert stats["status_counts"]["In Planung"] > 0  # planned units seen, but excluded


def test_capacity_reconciles_with_source(parsed):
    df, stats, exp = parsed
    assert df["capacity_kw"].sum() == pytest.approx(exp["kw_in_operation"], abs=0.01)


def test_missing_capacity_is_not_zero(parsed):
    df, stats, exp = parsed
    assert stats["units_missing_capacity"] == exp["missing_capacity"]
    # the unit with missing capacity is not in the totals at all
    assert df["units"].sum() == exp["units_in_operation"] - exp["missing_capacity"]


def test_unit_without_gemeinde_still_counts_for_its_land(parsed):
    df, stats, exp = parsed
    assert stats["units_without_gemeinde"] == exp["without_gemeinde"]
    assert df[df["gemeinde_ags"].isna()]["land_ags"].eq("12").all()


def test_contradicting_bundesland_is_reported(parsed):
    _, stats, exp = parsed
    assert stats["units_land_mismatch"] == exp["land_mismatch"]
