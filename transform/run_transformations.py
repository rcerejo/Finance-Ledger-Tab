#!/usr/bin/env python3
"""
Run dbt-style transformations against the local SQLite finance database.

This is intentionally small and dependency-light. It gives you the dbt layer
shape now, and the SQL files can later be ported into a real dbt project.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SEEDS = {
    "seed_merchant_rules": ROOT / "seeds" / "merchant_rules.csv",
    "seed_category_rules": ROOT / "seeds" / "category_rules.csv",
}
MODELS = [
    ("view", "stg_statement_files", ROOT / "models" / "staging" / "stg_statement_files.sql"),
    ("view", "stg_transactions", ROOT / "models" / "staging" / "stg_transactions.sql"),
    ("view", "int_deduped_transactions", ROOT / "models" / "intermediate" / "int_deduped_transactions.sql"),
    ("view", "int_merchant_normalized", ROOT / "models" / "intermediate" / "int_merchant_normalized.sql"),
    ("view", "int_categorized_transactions", ROOT / "models" / "intermediate" / "int_categorized_transactions.sql"),
    ("view", "fct_transactions", ROOT / "models" / "marts" / "fct_transactions.sql"),
    ("view", "mart_monthly_spending", ROOT / "models" / "marts" / "mart_monthly_spending.sql"),
    ("view", "mart_category_trends", ROOT / "models" / "marts" / "mart_category_trends.sql"),
    ("view", "mart_merchant_spending", ROOT / "models" / "marts" / "mart_merchant_spending.sql"),
]


def load_seed(conn: sqlite3.Connection, table_name: str, csv_path: Path) -> None:
    with csv_path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    conn.execute(f"drop table if exists {table_name}")
    columns_sql = ", ".join(f"{column} text" for column in fieldnames)
    conn.execute(f"create table {table_name} ({columns_sql})")
    if not rows:
        return

    placeholders = ", ".join("?" for _ in fieldnames)
    conn.executemany(
        f"insert into {table_name} ({', '.join(fieldnames)}) values ({placeholders})",
        [[row[column] for column in fieldnames] for row in rows],
    )


def create_model(conn: sqlite3.Connection, materialization: str, model_name: str, sql_path: Path) -> None:
    sql = sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    conn.execute(f"drop view if exists {model_name}")
    conn.execute(f"drop table if exists {model_name}")
    if materialization == "table":
        conn.execute(f"create table {model_name} as {sql}")
    else:
        conn.execute(f"create view {model_name} as {sql}")


def scalar(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def run_tests(conn: sqlite3.Connection) -> list[str]:
    failures: list[str] = []
    tests = [
        ("fct_transactions has rows", "select count(*) from fct_transactions", lambda value: value > 0),
        ("transaction_id not null", "select count(*) from fct_transactions where transaction_id is null or transaction_id = ''", lambda value: value == 0),
        ("transaction_id unique", "select count(*) - count(distinct transaction_id) from fct_transactions", lambda value: value == 0),
        ("transaction_date not null", "select count(*) from fct_transactions where transaction_date is null", lambda value: value == 0),
        ("amount not null", "select count(*) from fct_transactions where amount is null", lambda value: value == 0),
        ("bank_name not null", "select count(*) from fct_transactions where bank_name is null or bank_name = ''", lambda value: value == 0),
    ]
    for name, sql, predicate in tests:
        value = scalar(conn, sql)
        if not predicate(value):
            failures.append(f"{name} failed with value {value}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local dbt-style finance transformations.")
    parser.add_argument(
        "--db",
        default="outputs/finance_raw_final.sqlite",
        help="SQLite database containing raw_statement_files and raw_transactions",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            for table_name, csv_path in SEEDS.items():
                load_seed(conn, table_name, csv_path)
                print(f"Loaded seed {table_name}")
            for materialization, model_name, sql_path in MODELS:
                create_model(conn, materialization, model_name, sql_path)
                count = scalar(conn, f"select count(*) from {model_name}")
                print(f"Built {model_name}: {count} rows")

        failures = run_tests(conn)
        if failures:
            print("Tests failed:")
            for failure in failures:
                print(f"  - {failure}")
            return 1

        print("All transformation tests passed.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
