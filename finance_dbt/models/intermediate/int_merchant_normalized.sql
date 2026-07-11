with matched as (
    select
        t.transaction_id,
        r.normalized_merchant,
        r.merchant_group,
        row_number() over (
            partition by t.transaction_id
            order by cast(r.priority as integer), length(r.match_pattern) desc
        ) as rule_rank
    from {{ ref('int_deduped_transactions') }} t
    join {{ ref('merchant_rules') }} r
        on t.raw_description_upper like upper(r.match_pattern)
),

best_match as (
    select
        transaction_id,
        normalized_merchant,
        merchant_group
    from matched
    where rule_rank = 1
)

select
    t.transaction_id,
    t.file_id,
    t.account_id,
    t.bank_name,
    t.transaction_date,
    t.posting_date,
    t.raw_description,
    t.parser_merchant_name,
    coalesce(b.normalized_merchant, t.parser_merchant_name, 'Unknown') as merchant_name,
    coalesce(b.merchant_group, b.normalized_merchant, t.parser_merchant_name, 'Unknown') as merchant_group,
    t.amount,
    t.transaction_type,
    t.transaction_month,
    t.source_file,
    t.raw_row_text,
    t.page_number,
    t.parser_method,
    t.parser_confidence,
    t.ingested_at
from {{ ref('int_deduped_transactions') }} t
left join best_match b
    on t.transaction_id = b.transaction_id
where t.is_exact_duplicate = 0
