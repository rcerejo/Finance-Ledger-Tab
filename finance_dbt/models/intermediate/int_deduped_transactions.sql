-- Bank exports often omit stable transaction IDs. This conservative identity
-- key deduplicates exact matches across overlapping statement files; identical
-- same-day purchases can still be ambiguous without a bank-provided reference.
with ranked as (
    select
        *,
        row_number() over (
            partition by
                bank_name,
                account_id,
                transaction_date,
                coalesce(posting_date, ''),
                round(amount, 2),
                upper(trim(raw_description))
            order by source_file, transaction_id
        ) as exact_duplicate_rank
    from {{ ref('stg_transactions') }}
)

select
    *,
    case when exact_duplicate_rank = 1 then 0 else 1 end as is_exact_duplicate
from ranked
where exact_duplicate_rank = 1
