# Finance Ledger Lab Streamlit App

Run from the project root:

```powershell
streamlit run app/streamlit_app.py
```

The app reads the dbt-built SQLite marts from:

```text
outputs/finance_raw_final.sqlite
```

Expected tables:

- `fct_transactions`
- `mart_monthly_spending`
- `mart_category_trends`
- `mart_merchant_spending`

If data looks stale after ingesting a new statement, run:

```powershell
dbt seed --project-dir finance_dbt --profiles-dir finance_dbt
dbt run --project-dir finance_dbt --profiles-dir finance_dbt
dbt test --project-dir finance_dbt --profiles-dir finance_dbt
```

Then click **Refresh data** in the app sidebar.

## TF-IDF Category Suggestions

To generate local ML suggestions for uncategorized rows:

```powershell
python ml/tfidf_category_classifier.py --db outputs/finance_raw_final.sqlite
```

This writes:

- `outputs/category_suggestions.csv`
- `outputs/tfidf_category_classifier.joblib`
- SQLite table `ml_category_suggestions`

The dashboard shows these in the **ML Suggestions** tab. Suggestions do not overwrite final categories.
