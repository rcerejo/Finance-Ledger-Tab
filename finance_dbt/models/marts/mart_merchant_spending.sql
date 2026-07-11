select
    transaction_month,
    merchant_group,
    merchant_name,
    category,
    count(*) as transaction_count,
    round(sum(amount), 2) as net_amount,
    round(sum(case when amount < 0 then abs(amount) else 0 end), 2) as spending_amount
from {{ ref('fct_transactions') }}
group by transaction_month, merchant_group, merchant_name, category
