"""Load CMS standard-template (v3) hospital price files into DuckDB.

Handles both CSV layouts:
- tall: one row per item x payer/plan (payer_name, plan_name columns)
- wide: one row per item, payer/plan pairs encoded in column names
  (standard_charge|<payer>|<plan>|negotiated_dollar, ...)

Tables written:
  hospital(slug, hospital_name, legal_name, city, system, last_updated_on, version,
           source_file)
  item(slug, row_id, description, setting, billing_class, modifiers,
       gross_charge, cash_price, min_charge, max_charge)
  item_code(slug, row_id, code, code_type)
  rate(slug, row_id, payer_name, plan_name, negotiated_dollar,
       negotiated_percentage, negotiated_algorithm, methodology, median_amount)

Usage: python -m wihp.load [--db data/wihp.duckdb] [slug ...]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "config" / "sources.csv"
RAW = ROOT / "data" / "raw"
DEFAULT_DB = ROOT / "data" / "wihp.duckdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS hospital (
    slug VARCHAR PRIMARY KEY, hospital_name VARCHAR, legal_name VARCHAR,
    city VARCHAR, system VARCHAR,
    last_updated_on VARCHAR, version VARCHAR, source_file VARCHAR);
CREATE TABLE IF NOT EXISTS item (
    slug VARCHAR, row_id BIGINT, description VARCHAR, setting VARCHAR,
    billing_class VARCHAR, modifiers VARCHAR, gross_charge DOUBLE, cash_price DOUBLE,
    min_charge DOUBLE, max_charge DOUBLE);
CREATE TABLE IF NOT EXISTS item_code (
    slug VARCHAR, row_id BIGINT, code VARCHAR, code_type VARCHAR);
CREATE TABLE IF NOT EXISTS rate (
    slug VARCHAR, row_id BIGINT, payer_name VARCHAR, plan_name VARCHAR,
    negotiated_dollar DOUBLE, negotiated_percentage DOUBLE, negotiated_algorithm VARCHAR,
    methodology VARCHAR, median_amount DOUBLE);
CREATE OR REPLACE MACRO num(x) AS
    TRY_CAST(regexp_replace(trim(x), '[$,]', '', 'g') AS DOUBLE);
"""

# Item-level columns: target name -> source column (lower-cased).
ITEM_COLS = {
    "description": "description",
    "setting": "setting",
    "billing_class": "billing_class",
    "modifiers": "modifiers",
    "gross_charge": "standard_charge|gross",
    "cash_price": "standard_charge|discounted_cash",
    "min_charge": "standard_charge|min",
    "max_charge": "standard_charge|max",
}
NUMERIC = {"gross_charge", "cash_price", "min_charge", "max_charge",
           "negotiated_dollar", "negotiated_percentage", "median_amount"}
LOWER = {"setting", "billing_class"}  # files differ in case ("Both", "both")

# Payer-level fields: target name -> field suffix in CMS column names.
RATE_FIELDS = {
    "negotiated_dollar": "negotiated_dollar",
    "negotiated_percentage": "negotiated_percentage",
    "negotiated_algorithm": "negotiated_algorithm",
    "methodology": "methodology",
}


def read_metadata(path: Path) -> dict[str, str]:
    """Return the file's metadata (rows 1-2) as a dict with lower-case keys."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        keys, values = next(reader), next(reader)
    return {k.strip().lower(): v.strip() for k, v in zip(keys, values) if k}


def qi(name: str) -> str:
    """Quote a DuckDB identifier."""
    return '"' + name.replace('"', '""') + '"'


def expr(target: str, source: str | None) -> str:
    if source is None:
        return f"NULL AS {target}"
    col = f"NULLIF(trim({qi(source)}), '')"
    if target in NUMERIC:
        return f"num({col}) AS {target}"
    if target in LOWER:
        return f"lower({col}) AS {target}"
    return f"{col} AS {target}"


def find_file(slug: str) -> Path:
    files = sorted((RAW / slug).glob("*.csv"))
    if len(files) != 1:
        raise SystemExit(f"{slug}: expected one CSV in {RAW / slug}, found {len(files)}")
    return files[0]


def load_file(con: duckdb.DuckDBPyConnection, slug: str, path: Path,
              info: dict[str, str]) -> None:
    meta = read_metadata(path)

    for table in ("hospital", "item", "item_code", "rate"):
        con.execute(f"DELETE FROM {table} WHERE slug = ?", [slug])

    con.execute(
        "INSERT INTO hospital VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [slug, info.get("hospital") or meta.get("hospital_name"), meta.get("hospital_name"),
         info.get("city"), info.get("system"),
         meta.get("last_updated_on"), meta.get("version"), path.name],
    )

    con.execute("DROP TABLE IF EXISTS src")
    con.execute(
        "CREATE TEMP TABLE src AS SELECT row_number() OVER () AS row_id, * FROM "
        "read_csv(?, skip=2, header=true, all_varchar=true, quote='\"', escape='\"', "
        "null_padding=true, strict_mode=false, normalize_names=false)",
        [str(path)],
    )
    # Use DuckDB's column names (it trims whitespace), matched case-insensitively.
    header = [d[0] for d in con.execute("SELECT * FROM src LIMIT 0").description][1:]
    cols = {h.strip().lower(): h for h in header}

    item_sel = ", ".join(expr(t, cols.get(s)) for t, s in ITEM_COLS.items())
    con.execute(f"INSERT INTO item SELECT ?, row_id, {item_sel} FROM src", [slug])

    for i in range(1, 10):
        code, typ = cols.get(f"code|{i}"), cols.get(f"code|{i}|type")
        if code is None:
            break
        con.execute(
            # Some files drop the leading zero of CPT codes (01967 -> 1967).
            f"INSERT INTO item_code SELECT ?, row_id, CASE WHEN t = 'CPT' AND regexp_full_match(c, '\\d{{4}}') "
            f"THEN '0' || c ELSE c END, t FROM (SELECT row_id, trim({qi(code)}) AS c, "
            f"upper(trim({qi(typ)})) AS t FROM src) WHERE c <> ''",
            [slug],
        )

    if "payer_name" in cols:
        insert_tall_rates(con, slug, cols)
    else:
        insert_wide_rates(con, slug, header)
    con.execute("DROP TABLE src")


def insert_tall_rates(con, slug: str, cols: dict[str, str]) -> None:
    sel = [expr("payer_name", cols["payer_name"]), expr("plan_name", cols.get("plan_name"))]
    sel += [expr(t, cols.get(f"standard_charge|{f}")) for t, f in RATE_FIELDS.items()]
    sel.append(expr("median_amount", cols.get("median_amount")))
    con.execute(
        f"INSERT INTO rate SELECT ?, row_id, * EXCLUDE (row_id) "
        f"FROM (SELECT row_id, {', '.join(sel)} FROM src) "
        "WHERE payer_name IS NOT NULL",
        [slug],
    )


def wide_payer_plans(header: list[str]) -> dict[tuple[str, str], dict[str, str]]:
    """Map (payer, plan) -> {target field: original column name}."""
    plans: dict[tuple[str, str], dict[str, str]] = {}
    suffix_to_target = {f: t for t, f in RATE_FIELDS.items()}
    for h in header:
        parts = [p.strip() for p in h.split("|")]
        head = parts[0].lower()
        if head == "standard_charge" and len(parts) == 4:
            target = suffix_to_target.get(parts[3].lower())
            if target:
                plans.setdefault((parts[1], parts[2]), {})[target] = h
        elif head == "median_amount" and len(parts) == 3:
            plans.setdefault((parts[1], parts[2]), {})["median_amount"] = h
    return plans


def insert_wide_rates(con, slug: str, header: list[str]) -> None:
    for (payer, plan), fields in wide_payer_plans(header).items():
        sel = [expr(t, fields.get(t)) for t in [*RATE_FIELDS, "median_amount"]]
        con.execute(
            f"INSERT INTO rate SELECT ?, row_id, ?, ?, * EXCLUDE (row_id) FROM "
            f"(SELECT row_id, {', '.join(sel)} FROM src) "
            "WHERE negotiated_dollar IS NOT NULL OR negotiated_percentage IS NOT NULL "
            "OR negotiated_algorithm IS NOT NULL",
            [slug, payer, plan],
        )


def read_sources() -> dict[str, dict[str, str]]:
    with SOURCES.open(newline="") as f:
        return {row["slug"]: row for row in csv.DictReader(f)}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("slugs", nargs="*")
    args = ap.parse_args(argv)

    sources = read_sources()
    slugs = args.slugs or list(sources)
    con = duckdb.connect(args.db)
    con.execute(SCHEMA)
    for slug in slugs:
        path = find_file(slug)
        print(f"loading {slug}: {path.name}", file=sys.stderr)
        con.execute("BEGIN")
        load_file(con, slug, path, sources[slug])
        con.execute("COMMIT")
    print(con.sql(
        "SELECT h.slug, h.version, h.last_updated_on, "
        "(SELECT count(*) FROM item i WHERE i.slug = h.slug) AS items, "
        "(SELECT count(*) FROM rate r WHERE r.slug = h.slug) AS rates "
        "FROM hospital h ORDER BY h.slug"
    ))
    con.close()


if __name__ == "__main__":
    main()
