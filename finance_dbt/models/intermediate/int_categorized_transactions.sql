with matched as (
    select
        t.transaction_id,
        r.category,
        r.subcategory,
        row_number() over (
            partition by t.transaction_id
            order by cast(r.priority as integer)
        ) as rule_rank
    from {{ ref('int_merchant_normalized') }} t
    join {{ ref('category_rules') }} r
        on t.merchant_name like r.merchant_pattern
        and upper(t.raw_description) like upper(r.description_pattern)
        and (r.transaction_type = '' or r.transaction_type = t.transaction_type)
),

best_match as (
    select
        transaction_id,
        category,
        subcategory
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
    t.merchant_name,
    t.merchant_group,
    t.amount,
    t.transaction_type,
    coalesce(b.category, 'Uncategorized') as category,
    coalesce(b.subcategory, 'Uncategorized') as subcategory,
    t.transaction_month,
    t.source_file,
    t.raw_row_text,
    t.page_number,
    t.parser_method,
    t.parser_confidence,
    t.ingested_at
from {{ ref('int_merchant_normalized') }} t
left join best_match b
    on t.transaction_id = b.transaction_id
