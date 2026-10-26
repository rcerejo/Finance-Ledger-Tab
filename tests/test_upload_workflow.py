from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ingestion.upload_workflow import existing_statement_hashes, parse_uploaded_statement
from ingestion.statement_parser import ingest_statements


class UploadWorkflowTests(unittest.TestCase):
    def test_csv_upload_preserves_filename_and_parses_without_retaining_temp_path(self) -> None:
        content = (
            "bank_name,account_id,transaction_date,posting_date,description,amount\n"
            "Alliant Credit Union,1234,2026-01-05,2026-01-06,Kroger,-45.00\n"
        ).encode("utf-8")

        preview = parse_uploaded_statement("monthly-statement.csv", content)

        self.assertEqual(preview.filename, "monthly-statement.csv")
        self.assertEqual(preview.statement.source_file, "monthly-statement.csv")
        self.assertEqual(preview.preview_rows[0]["source_file"], "monthly-statement.csv")
        self.assertNotIn(tempfile.gettempdir(), preview.statement.source_file)
        self.assertEqual(len(preview.statement.rows), 1)

    def test_existing_file_hashes_detect_ingested_uploads(self) -> None:
        content = (
            "bank_name,account_id,transaction_date,description,amount\n"
            "Alliant Credit Union,1234,2026-01-05,Kroger,-45.00\n"
        ).encode("utf-8")
        preview = parse_uploaded_statement("statement.csv", content)

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "finance.sqlite"
            self.assertEqual(existing_statement_hashes(db_path), set())
            ingest_statements(db_path, [preview.statement], "2026-01-07T00:00:00")
            self.assertEqual(existing_statement_hashes(db_path), {preview.statement.file_hash})
            with closing(sqlite3.connect(db_path)) as connection:
                source_path = connection.execute(
                    "SELECT source_file_path FROM raw_statement_files"
                ).fetchone()[0]
            self.assertEqual(source_path, "statement.csv")

    def test_rejects_unsupported_types_and_oversized_files(self) -> None:
        with self.assertRaises(ValueError):
            parse_uploaded_statement("statement.xlsx", b"data")
        with self.assertRaises(ValueError):
            parse_uploaded_statement("statement.csv", b"x" * (25 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
