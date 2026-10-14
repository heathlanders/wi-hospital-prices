"""Build the analysis model (sql/model.sql) on the loaded DuckDB warehouse.

Usage: python -m wihp.model [--db data/wihp.duckdb]
"""

from __future__ import annotations

import argparse

import duckdb

from wihp.load import DEFAULT_DB, ROOT

MODEL_SQL = ROOT / "sql" / "model.sql"
CONFIG = ROOT / "config"


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(MODEL_SQL.read_text().replace("{config}", str(CONFIG)))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)
    con = duckdb.connect(args.db)
    build(con)
    print(con.sql("SELECT slug, count(*) AS rates, count(DISTINCT service_code) AS services "
                  "FROM shoppable_rate GROUP BY 1 ORDER BY 1"))
    con.close()


if __name__ == "__main__":
    main()
