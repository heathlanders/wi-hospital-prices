"""Data-quality checks on the loaded DuckDB warehouse.

Each check is a SQL query returning offending rows; a check passes when it
returns none. Warnings flag known source-data quirks that the model filters out;
they do not fail the run. Run against the real data with: python -m wihp.checks [--db PATH]
"""

from __future__ import annotations

import argparse
import sys

import duckdb

from wihp.load import DEFAULT_DB

CHECKS: dict[str, str] = {
    "template version is 3.x":
        "SELECT slug, version FROM hospital WHERE version NOT LIKE '3.%'",
    "every hospital has items and payer rates":
        "SELECT slug FROM hospital h WHERE NOT EXISTS (SELECT 1 FROM item i WHERE i.slug = h.slug) "
        "OR NOT EXISTS (SELECT 1 FROM rate r WHERE r.slug = h.slug)",
    "every rate belongs to an item":
        "SELECT r.slug, r.row_id FROM rate r ANTI JOIN item i USING (slug, row_id)",
    "every item has at least one code":
        "SELECT slug, row_id FROM item ANTI JOIN item_code USING (slug, row_id) "
        "WHERE modifiers IS NULL",  # modifier-only rows legitimately have no code
    "no negative charges or rates":
        "SELECT slug, row_id FROM item WHERE gross_charge < 0 OR cash_price < 0 "
        "UNION ALL SELECT slug, row_id FROM rate WHERE negotiated_dollar < 0",
    "every rate has a dollar, percent or algorithm":
        "SELECT slug, row_id FROM rate JOIN item USING (slug, row_id) "
        "WHERE negotiated_dollar IS NULL AND negotiated_percentage IS NULL "
        "AND negotiated_algorithm IS NULL AND modifiers IS NULL",
    "setting is inpatient, outpatient or both":
        "SELECT slug, setting FROM item WHERE setting NOT IN ('inpatient', 'outpatient', 'both')",
}

WARNINGS: dict[str, str] = {
    # Hospitals label internal charge codes as HCPCS (e.g. 7001526).
    "CPT/HCPCS codes are 5 characters":
        "SELECT slug, code, code_type FROM item_code "
        "WHERE code_type IN ('CPT', 'HCPCS') AND length(code) <> 5",
    # New payer names appear when hospitals refresh files; add them to config/payer_map.csv.
    "every payer name is in config/payer_map.csv (needs wihp.model)":
        "SELECT DISTINCT payer_name FROM rate ANTI JOIN payer_map USING (payer_name)",
}


def run(con: duckdb.DuckDBPyConnection, checks: dict[str, str] = CHECKS) -> dict[str, int]:
    """Return {check name: number of offending rows}."""
    return {name: con.sql(f"SELECT count(*) FROM ({sql})").fetchone()[0]
            for name, sql in checks.items()}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)
    con = duckdb.connect(args.db, read_only=True)
    failed = 0
    for name, bad in run(con).items():
        print(f"{'FAIL' if bad else 'ok  '} {name}" + (f" ({bad:,} rows)" if bad else ""))
        failed += bool(bad)
    for name, bad in run(con, WARNINGS).items():
        print(f"{'warn' if bad else 'ok  '} {name}" + (f" ({bad:,} rows)" if bad else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
