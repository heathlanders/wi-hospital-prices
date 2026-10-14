from pathlib import Path

import duckdb
import pytest

from wihp import load, model

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def con():
    con = duckdb.connect()
    con.execute(load.SCHEMA)
    for slug in ("tall", "wide"):
        path = FIXTURES / slug / "sample_standardcharges.csv"
        load.load_file(con, slug, path, {"hospital": f"{slug.title()} Hospital"})
    model.build(con)
    yield con
    con.close()


def rows(con, sql):
    return con.sql(sql).fetchall()


def test_shoppable_rate_keeps_dollar_facility_rates_only(con):
    # 01967 is not shoppable, the modifier row is dropped, Payer C has only a percentage.
    assert rows(con, "SELECT slug, service_code, payer_name, negotiated_dollar FROM shoppable_rate "
                     "ORDER BY slug, service_code, payer_name") == [
        ("tall", "70553", "Payer A", 1200.5),
        ("tall", "70553", "Payer B", 800.0),
        ("wide", "45378", "Payer A", 1800.0),
        ("wide", "80053", "Payer A", 25.5),
    ]


def test_hospital_display_name(con):
    assert rows(con, "SELECT DISTINCT hospital_name FROM shoppable_rate ORDER BY 1") == [
        ("Tall Hospital",), ("Wide Hospital",)]


def test_product_line_from_payer_and_plan(con):
    con.execute("UPDATE rate SET plan_name = 'Medicare Advantage' WHERE payer_name = 'Payer B'")
    assert rows(con, "SELECT payer_name, product_line FROM shoppable_rate WHERE slug = 'tall' "
                     "ORDER BY 1") == [("Payer A", "Commercial"), ("Payer B", "Medicare Advantage")]


def test_service_spread(con):
    assert rows(con, "SELECT hospitals, payer_groups, min_rate, max_rate, round(max_to_min, 4) "
                     "FROM service_spread WHERE service_code = '70553'") == [
        (1, 2, 800.0, 1200.5, round(1200.5 / 800, 4))]


def test_cash_to_commercial(con):
    assert rows(con, "SELECT cash_price, commercial_median, cash_to_commercial FROM hospital_service "
                     "WHERE slug = 'wide' AND service_code = '45378'") == [(1500.0, 1800.0, 1500 / 1800)]


def test_config_tables():
    con = duckdb.connect()
    con.execute(load.SCHEMA)
    model.build(con)
    assert rows(con, "SELECT count(DISTINCT service_code) FROM shoppable_service") == [(70,)]
    assert rows(con, "SELECT count(*) FROM payer_map WHERE payer_group IS NULL") == [(0,)]
