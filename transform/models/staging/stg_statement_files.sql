select
    file_id,
    original_filename,
    source_file_path,
    file_hash,
    bank_name,
    account_id,
    statement_period_start,
    statement_period_end,
    parser_name,
    parser_version,
    parse_status,
    row_count,
    validation_status,
    validation_message,
    created_at as ingested_at
from raw_statement_files
