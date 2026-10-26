select transaction_id
from {{ ref('stg_transactions') }}
where (transaction_type = 'debit' and amount >= 0)
   or (transaction_type = 'credit' and amount <= 0)
