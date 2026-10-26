# Finance Ledger Lab Streamlit App

Run from the project root:

```powershell
streamlit run app/streamlit_app.py
```

The app reads the dbt-built SQLite marts from:

```text
outputs/finance_raw.sqlite
```

Expected tables:

- `fct_transactions`
- `mart_monthly_spending`
- `mart_category_trends`
- `mart_merchant_spending`
- `forecast_monthly_spending`
- `forecast_category_spending`
- `spending_anomalies`

If data looks stale after ingesting a new statement, run:

```powershell
dbt seed --project-dir finance_dbt --profiles-dir finance_dbt
dbt run --project-dir finance_dbt --profiles-dir finance_dbt
dbt test --project-dir finance_dbt --profiles-dir finance_dbt
python forecasting/baseline_forecasts.py --db outputs/finance_raw.sqlite
```

Then click **Refresh data** in the app sidebar.

## Baseline Forecasting

To refresh the baseline forecast tables:

```powershell
python forecasting/baseline_forecasts.py --db outputs/finance_raw.sqlite
```

This writes:

- `forecast_monthly_spending`
- `forecast_category_spending`
- `spending_anomalies`

The baseline excludes `Transfers`, `Credit Card Payment`, `Income`, and `Investments` by default so the forecast focuses on controllable spending. To include those categories:

```powershell
python forecasting/baseline_forecasts.py --db outputs/finance_raw.sqlite --include-transfer-spending
```

## TF-IDF Category Suggestions

To generate local ML suggestions for uncategorized rows:

```powershell
python ml/tfidf_category_classifier.py --db outputs/finance_raw.sqlite
```

This writes:

- `outputs/category_suggestions.csv`
- `outputs/tfidf_category_classifier.joblib`
- SQLite table `ml_category_suggestions`

The dashboard shows these in the **ML Suggestions** tab. Suggestions do not overwrite final categories.

## AI Category Suggestions

To generate local LLM suggestions for uncategorized rows, start Ollama and make sure the model is downloaded:

```powershell
ollama pull llama3.1:8b
```

Then run:

```powershell
python ml/ai_category_suggester.py --db outputs/finance_raw.sqlite --model llama3.1:8b
```

This writes:

- `outputs/ai_category_suggestions.csv`
- SQLite table `ai_category_suggestions`

The dashboard shows these in the **ML Suggestions** tab under **AI Suggestions**. These suggestions are for review only; approved patterns should be added back to the dbt seed rules.
