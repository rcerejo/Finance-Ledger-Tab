# dbt transformations

This dbt project transforms the SQLite raw ingestion tables into standardized transaction models and analytics marts. See the repository [README](../README.md) for full environment setup and end-to-end run commands.

The project reads `raw_statement_files` and `raw_transactions`, then builds:

- Staging views for source cleanup and transaction sign normalization
- Intermediate views for deduplication, merchant normalization, and category rules
- Fact and mart tables for transactions, monthly spending, category trends, and merchant spending

The editable rule seeds are in `seeds/merchant_rules.csv` and `seeds/category_rules.csv`. Run `dbt seed`, `dbt run`, and `dbt test` from the repository root with this folder supplied as both `--project-dir` and `--profiles-dir`.
