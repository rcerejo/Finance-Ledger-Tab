# finance_dbt

Real dbt project for the personal finance pipeline.

This project reads raw ingestion tables from SQLite:

- `raw_statement_files`
- `raw_transactions`

Then builds:

- staging models
- intermediate dedupe, merchant normalization, and categorization models
- dashboard-ready marts

## Install

From PowerShell:

```powershell
cd "C:/Users/rosha/Documents/Codex/2026-07-08/can-you-write-a-script-to/finance_dbt"
python -m pip install -r requirements.txt
```

## Configure Profile

Copy `profiles.example.yml` into your dbt profiles folder:

```powershell
mkdir "$HOME/.dbt" -Force
copy ".\profiles.example.yml" "$HOME/.dbt\profiles.yml"
```

The example points to:

```text
C:/Users/rosha/Documents/Codex/2026-07-08/can-you-write-a-script-to/outputs/finance_raw_final.sqlite
```

## Run

```powershell
dbt debug
dbt seed
dbt run
dbt test
```

## Model Flow

```text
raw_statement_files
raw_transactions
        ↓
stg_statement_files
stg_transactions
        ↓
int_deduped_transactions
int_merchant_normalized
int_categorized_transactions
        ↓
fct_transactions
mart_monthly_spending
mart_category_trends
mart_merchant_spending
```

## Categorization Method

Categorization is rule-based for now:

1. `merchant_rules.csv` normalizes raw descriptions to merchants.
2. `category_rules.csv` maps merchants/descriptions/types to categories.
3. Unmatched transactions stay `Uncategorized`.

This is intentional. ML should come later as a suggestion layer after you have enough reviewed/labeled history.
