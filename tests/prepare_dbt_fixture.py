from __future__ import annotations

import argparse
from pathlib import Path

from ingestion.statement_parser import ParsedStatement, Transaction, ingest_statements, make_account_id


def transaction(
    *,
    source_file: str,
    bank: str,
    account: str,
    account_name: str,
    date: str,
    description: str,
    amount: str,
    row_number: int,
) -> Transaction:
    return Transaction(
        source_file=source_file,
        source_row_number=row_number,
        page_number=1,
        source_method="synthetic_ci_fixture",
        confidence="high",
        bank=bank,
        account=account,
        account_name=account_name,
        transaction_date=date,
        posting_date="",
        description=description,
        amount=amount,
        balance="",
        raw_text=f"{date} {description} {amount}",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    db_path = parser.parse_args().db

    kroger_first = transaction(
        source_file="statement-january-a.csv",
        bank="Alliant Credit Union",
        account="1234",
        account_name="Checking",
        date="2026-01-05",
        description="Kroger #123",
        amount="-45.00",
        row_number=1,
    )
    uber = transaction(
        source_file="statement-january-a.csv",
        bank="Alliant Credit Union",
        account="1234",
        account_name="Checking",
        date="2026-01-06",
        description="Uber Trip",
        amount="-22.00",
        row_number=2,
    )
    dividend = transaction(
        source_file="statement-january-a.csv",
        bank="Alliant Credit Union",
        account="1234",
        account_name="Checking",
        date="2026-01-10",
        description="Dividend",
        amount="1.25",
        row_number=3,
    )
    kroger_overlap = transaction(
        source_file="statement-january-b.csv",
        bank="Alliant Credit Union",
        account="1234",
        account_name="Checking",
        date="2026-01-05",
        description="Kroger #123",
        amount="-45.00",
        row_number=1,
    )
    bofa_purchase = transaction(
        source_file="bofa-january.csv",
        bank="Bank of America",
        account="9876",
        account_name="Credit Card",
        date="2026-01-08",
        description="MCDONALDS",
        amount="14.75",
        row_number=1,
    )
    bofa_payment = transaction(
        source_file="bofa-january.csv",
        bank="Bank of America",
        account="9876",
        account_name="Credit Card",
        date="2026-01-20",
        description="BANK OF AMERICA CREDIT CARD PAYMENT",
        amount="-28.50",
        row_number=2,
    )

    statements = []
    for filename, file_hash, bank, rows in (
        ("statement-january-a.csv", "a" * 64, "Alliant Credit Union", [kroger_first, uber, dividend]),
        ("statement-january-b.csv", "b" * 64, "Alliant Credit Union", [kroger_overlap]),
        ("bofa-january.csv", "c" * 64, "Bank of America", [bofa_purchase, bofa_payment]),
    ):
        statements.append(
            ParsedStatement(
                source_file=filename,
                file_hash=file_hash,
                bank_name=bank,
                account_id=make_account_id(rows[0]),
                statement_period_start="2026-01-01",
                statement_period_end="2026-01-31",
                rows=rows,
            )
        )

    ingest_statements(db_path, statements, "2026-02-01T00:00:00")


if __name__ == "__main__":
    main()
