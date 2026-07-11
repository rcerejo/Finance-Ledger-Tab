select
    transaction_id,
    file_id,
    account_id,
    bank_name,
    transaction_date,
    posting_date,
    raw_description,
    merchant_name,
    merchant_group,
    amount,
    transaction_type,
    category,
    subcategory,
    transaction_month,
    source_file,
    ingested_at
from int_categorized_transactions
