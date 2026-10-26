select
    transaction_month,
    count(*) as transaction_count,
    round(sum(case when amount < 0 then amount else 0 end), 2) as total_debits,
    round(sum(case when amount > 0 then amount else 0 end), 2) as total_credits,
    round(sum(amount), 2) as net_amount
from {{ ref('fct_transactions') }}
group by transaction_month
