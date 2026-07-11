with ranked as (
    select
        *,
        row_number() over (
            partition by
                account_id,
                transaction_date,
                coalesce(posting_date, ''),
                amount,
                raw_description,
                source_file
            order by transaction_id
        ) as exact_duplicate_rank
    from stg_transactions
)

select
    *,
    case when exact_duplicate_rank = 1 then 0 else 1 end as is_exact_duplicate
from ranked
where exact_duplicate_rank = 1
