select
    transaction_id,
    file_id,
    account_id,
    bank_name,
    date(transaction_date) as transaction_date,
    case
        when posting_date is null or trim(posting_date) = '' then null
        else date(posting_date)
    end as posting_date,
    trim(raw_description) as raw_description,
    upper(trim(raw_description)) as raw_description_upper,
    trim(merchant_name) as parser_merchant_name,
    cast(amount as real) as raw_amount,
    case
        when bank_name = 'Bank of America' then cast(amount as real) * -1
        else cast(amount as real)
    end as amount,
    case
        when bank_name = 'Bank of America' and amount > 0 then 'debit'
        when bank_name = 'Bank of America' and amount < 0 then 'credit'
        when amount < 0 then 'debit'
        when amount > 0 then 'credit'
        else 'zero'
    end as transaction_type,
    strftime('%Y-%m', transaction_date) as transaction_month,
    source_file,
    raw_row_text,
    page_number,
    parser_method,
    parser_confidence,
    created_at as ingested_at
from raw_transactions
