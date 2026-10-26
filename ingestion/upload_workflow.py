from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ingestion.statement_parser import (
    ParsedStatement,
    export_row,
    parse_statement,
    validate_rows,
    validation_status,
)


MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@dataclass
class UploadPreview:
    filename: str
    statement: ParsedStatement
    validation: list[str]
    status: str
    preview_rows: list[dict[str, str]]


def parse_uploaded_statement(
    filename: str,
    content: bytes,
    *,
    ai_fallback: bool = False,
    ai_model: str = "llama3.1:8b",
) -> UploadPreview:
    original_name = Path(filename).name
    suffix = Path(original_name).suffix.lower()
    if suffix not in {".pdf", ".csv"}:
        raise ValueError("Only PDF and CSV statement files are supported.")
    if not content:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Each statement must be 25 MB or smaller.")

    with tempfile.TemporaryDirectory(prefix="finance-ledger-upload-") as directory:
        temporary_path = Path(directory) / original_name
        temporary_path.write_bytes(content)
        statement = parse_statement(
            temporary_path,
            ai_fallback=ai_fallback,
            ai_model=ai_model,
        )

    statement.source_file = original_name
    for row in statement.rows:
        row.source_file = original_name

    issues = validate_rows(statement.rows)
    status, _ = validation_status(issues)
    created_at = datetime.now().isoformat(timespec="seconds")
    return UploadPreview(
        filename=original_name,
        statement=statement,
        validation=issues,
        status=status,
        preview_rows=[export_row(row, created_at) for row in statement.rows],
    )


def existing_statement_hashes(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    with closing(sqlite3.connect(db_path)) as connection:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'raw_statement_files'"
        ).fetchone()
        if not table_exists:
            return set()
        return {
            row[0]
            for row in connection.execute("SELECT file_hash FROM raw_statement_files")
        }
