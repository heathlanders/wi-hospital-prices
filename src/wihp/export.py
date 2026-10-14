"""Export the model to Parquet for Power BI (Get Data > Parquet).

Usage: python -m wihp.export [--db data/wihp.duckdb] [--out data/export]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

from wihp.load import DEFAULT_DB, ROOT

# Output file -> query. service and payer are dimension tables for the Power BI
# star schema (shoppable_service has one row per code, not per service).
EXPORTS = {
    "hospital": "SELECT slug, hospital_name, city, system, last_updated_on FROM hospital",
    "service": "SELECT DISTINCT service_code, category, description AS service "
               "FROM shoppable_service WHERE code = service_code",
    "payer": "SELECT DISTINCT payer_group FROM shoppable_rate",
    "shoppable_rate": "SELECT * EXCLUDE (hospital_name, service, category) FROM shoppable_rate",
    "hospital_service": "SELECT * FROM hospital_service",
    "service_spread": "SELECT * FROM service_spread",
}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(ROOT / "data" / "export"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(args.db, read_only=True)
    for old in out.glob("*.parquet"):
        old.unlink()
    for name, query in EXPORTS.items():
        path = out / f"{name}.parquet"
        con.execute(f"COPY ({query}) TO '{path}' (FORMAT parquet)")
        n = con.sql(f"SELECT count(*) FROM ({query})").fetchone()[0]
        print(f"{path.name} ({n:,} rows)")


if __name__ == "__main__":
    main()
