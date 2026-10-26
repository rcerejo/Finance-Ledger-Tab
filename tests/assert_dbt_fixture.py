from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    database = parser.parse_args().db
    with sqlite3.connect(database) as connection:
        count = connection.execute("select count(*) from fct_transactions").fetchone()[0]
        duplicate_count = connection.execute(
            """
            select count(*) from fct_transactions
            where bank_name = 'Alliant Credit Union'
              and account_id = 'Alliant_Credit_Union_1234_Checking'
              and transaction_date = '2026-01-05'
              and raw_description = 'Kroger #123'
            """
        ).fetchone()[0]
        groceries = connection.execute(
            """
            select amount, transaction_type, category from fct_transactions
            where raw_description = 'Kroger #123'
            """
        ).fetchone()
        bofa = connection.execute(
            """
            select amount, transaction_type from fct_transactions
            where bank_name = 'Bank of America'
            """
        ).fetchone()
        bofa_payment = connection.execute(
            """
            select amount, transaction_type, category from fct_transactions
            where raw_description = 'BANK OF AMERICA CREDIT CARD PAYMENT'
            """
        ).fetchone()
        alliant_credit = connection.execute(
            """
            select amount, transaction_type from fct_transactions
            where raw_description = 'Dividend'
            """
        ).fetchone()

    assert count == 5, f"Expected 5 canonical transactions after overlap dedupe, got {count}"
    assert duplicate_count == 1, f"Expected one Kroger transaction across overlapping files, got {duplicate_count}"
    assert groceries == (-45.0, "debit", "Groceries"), f"Unexpected grocery normalization: {groceries}"
    assert bofa == (-14.75, "debit"), f"Unexpected BofA charge sign: {bofa}"
    assert bofa_payment == (28.5, "credit", "Credit Card Payment"), f"Unexpected BofA payment normalization: {bofa_payment}"
    assert alliant_credit == (1.25, "credit"), f"Unexpected Alliant deposit sign: {alliant_credit}"


if __name__ == "__main__":
    main()
