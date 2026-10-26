from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ingestion.statement_parser import ingest_statements, parse_statement


class StatementParserTests(unittest.TestCase):
    def test_csv_header_mapping_and_dates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "statement.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["bank_name", "account_id", "transaction_date", "posting_date", "description", "amount"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "bank_name": "Alliant Credit Union",
                        "account_id": "1234",
                        "transaction_date": "2026-01-05",
                        "posting_date": "2026-01-06",
                        "description": "Kroger #123",
                        "amount": "-45.00",
                    }
                )

            parsed = parse_statement(path)

        self.assertEqual(len(parsed.rows), 1)
        self.assertEqual(parsed.rows[0].transaction_date, "2026-01-05")
        self.assertEqual(parsed.rows[0].posting_date, "2026-01-06")
        self.assertEqual(parsed.rows[0].amount, "-45.00")
        self.assertEqual(parsed.rows[0].description, "Kroger #123")

    def test_ingestion_is_idempotent_for_same_statement_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            statement_path = root / "statement.csv"
            statement_path.write_text(
                "bank_name,account_id,transaction_date,description,amount\n"
                "Alliant Credit Union,1234,2026-01-05,Kroger,-45.00\n",
                encoding="utf-8",
            )
            database_path = root / "finance.sqlite"
            statement = parse_statement(statement_path)

            first = ingest_statements(database_path, [statement], "2026-01-07T00:00:00")
            second = ingest_statements(database_path, [statement], "2026-01-07T00:00:01")
            with closing(sqlite3.connect(database_path)) as connection:
                transaction_count = connection.execute("select count(*) from raw_transactions").fetchone()[0]

        self.assertEqual(first.get("parsed_with_warnings"), 1)
        self.assertEqual(second.get("duplicate_file"), 1)
        self.assertEqual(transaction_count, 1)


if __name__ == "__main__":
    unittest.main()
