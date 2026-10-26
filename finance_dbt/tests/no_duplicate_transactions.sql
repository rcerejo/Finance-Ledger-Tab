select
    account_id,
    transaction_date,
    coalesce(posting_date, '') as posting_date,
    round(amount, 2) as amount,
    upper(trim(raw_description)) as normalized_description,
    count(*) as duplicate_count
from {{ ref('int_deduped_transactions') }}
group by
    bank_name,
    account_id,
    transaction_date,
    coalesce(posting_date, ''),
    round(amount, 2),
    upper(trim(raw_description))
having count(*) > 1
