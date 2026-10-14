from pathlib import Path

import duckdb
import pytest

from wihp import checks, load

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def con():
    con = duckdb.connect()
    con.execute(load.SCHEMA)
    for slug in ("tall", "wide"):
        path = FIXTURES / slug / "sample_standardcharges.csv"
        load.load_file(con, slug, path, {"city": "Anytown", "system": "Sample"})
    yield con
    con.close()


def rows(con, sql):
    return con.sql(sql).fetchall()


def test_hospital_metadata(con):
    assert rows(con, "SELECT slug, hospital_name, version FROM hospital ORDER BY slug") == [
        ("tall", "Sample Tall Hospital", "3.0.0"),
        ("wide", "Sample Wide Hospital", "3.0.0"),
    ]


def test_tall_items_and_rates(con):
    assert rows(con, "SELECT count(*) FROM item WHERE slug = 'tall'") == [(4,)]
    assert rows(con, "SELECT payer_name, plan_name, negotiated_dollar, median_amount FROM rate "
                     "WHERE slug = 'tall' AND row_id = 1") == [("Payer A", "PPO", 1200.50, 1100.0)]


def test_money_and_case_normalized(con):
    # "$3,000.00" parses; upper-case headers and "Outpatient" values are matched.
    assert rows(con, "SELECT gross_charge, cash_price, setting, billing_class FROM item "
                     "WHERE slug = 'tall' AND row_id = 1") == [(3000.0, 1500.0, "outpatient", "facility")]


def test_codes_unpivoted_and_cpt_padded(con):
    assert rows(con, "SELECT code, code_type FROM item_code WHERE slug = 'tall' AND row_id = 1 "
                     "ORDER BY code_type") == [("70553", "CPT"), ("0611", "RC")]
    assert rows(con, "SELECT code FROM item_code WHERE slug = 'tall' AND row_id = 3") == [("01967",)]


def test_wide_unpivots_payer_plans(con):
    # Payer C's plan name has a trailing space in the header; it is trimmed.
    assert rows(con, "SELECT row_id, payer_name, plan_name, negotiated_dollar, negotiated_percentage, "
                     "methodology, median_amount FROM rate WHERE slug = 'wide' ORDER BY row_id, payer_name") == [
        (1, "Payer A", "PPO", 25.5, None, "fee schedule", 24.0),
        (2, "Payer A", "PPO", 1800.0, None, "case rate", 1750.0),
        (2, "Payer C", "MCO", None, 45.0, "percent of total billed charges", None),
    ]


def test_reload_replaces_hospital(con):
    load.load_file(con, "tall", FIXTURES / "tall" / "sample_standardcharges.csv", {})
    assert rows(con, "SELECT count(*) FROM item WHERE slug = 'tall'") == [(4,)]
    assert rows(con, "SELECT count(*) FROM hospital") == [(2,)]


def test_checks_pass_on_fixtures(con):
    assert checks.run(con) == {name: 0 for name in checks.CHECKS}


def test_checks_catch_bad_data(con):
    con.execute("UPDATE rate SET negotiated_dollar = -1 WHERE slug = 'tall' AND row_id = 1")
    con.execute("UPDATE hospital SET version = '2.0.0' WHERE slug = 'wide'")
    result = checks.run(con)
    assert result["no negative charges or rates"] == 1
    assert result["template version is 3.x"] == 1
