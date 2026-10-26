# Finance Ledger Lab

Upload bank statements, normalize transactions into SQLite, build analytics with dbt, and explore results in Streamlit.

## Quick start (Windows PowerShell)

Run these commands from the project root. Python 3.10+ is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
New-Item -ItemType Directory -Force outputs | Out-Null
Copy-Item finance_dbt\profiles.example.yml finance_dbt\profiles.yml
$env:FINANCE_DB_PATH = (Join-Path (Get-Location) "outputs/finance_raw.sqlite")
$env:FINANCE_DB_DIR = (Join-Path (Get-Location) "outputs")
```

If PowerShell blocks virtual-environment activation, run `Set-ExecutionPolicy -Scope Process RemoteSigned` and activate again.

## Process a statement

Replace the example path with a statement PDF or CSV you trust. The parser writes a CSV preview and loads parsed transactions into the local SQLite database.

```powershell
python ingestion/statement_parser.py "C:\path\to\statement.pdf" --ingest --db outputs/finance_raw.sqlite -o outputs/transactions.csv
```

Build and test the dbt models:

```powershell
dbt seed --project-dir finance_dbt --profiles-dir finance_dbt
dbt run --project-dir finance_dbt --profiles-dir finance_dbt
dbt test --project-dir finance_dbt --profiles-dir finance_dbt
```

Run the local regression tests with `python -m unittest discover -s tests -p "test_*.py" -v`.

To generate TF-IDF category suggestions and an evaluation report:

```powershell
python ml/tfidf_category_classifier.py --db outputs/finance_raw.sqlite
```

The report uses merchant-held-out validation and measures agreement with dbt rule labels. It is not a measure of independent ground-truth accuracy; manually review and label transactions before claiming real-world model accuracy.
The JSON report is written to `outputs/tfidf_evaluation.json` by default; override it with `--evaluation-out`.

Optionally refresh forecasts, then open the dashboard:

```powershell
python forecasting/baseline_forecasts.py --db outputs/finance_raw.sqlite
streamlit run app/streamlit_app.py
```

The baseline uses calendar months and treats missing category activity as zero. It assumes the latest imported month is complete, so forecasts from a partial statement month should be treated cautiously.

## Optional local AI

The parser can use a locally running Ollama model as a fallback for unfamiliar text-based PDF layouts. Install Ollama and pull a model first:

```powershell
ollama pull llama3.1:8b
python ingestion/statement_parser.py "C:\path\to\statement.pdf" -o outputs/transactions.csv --ai-fallback --ai-model llama3.1:8b
```

AI and TF-IDF category outputs are suggestions for review, not automatic replacements for dbt categories. Scanned/image-only PDFs need OCR. Review parser output and validation before relying on it; bank formats vary and support is not universal.

## Project map

- `ingestion/`: statement parsing, validation, and SQLite loading
- `finance_dbt/`: canonical dbt project, mapping seeds, tests, and marts
- `ml/`: TF-IDF and optional Ollama category suggestions
- `forecasting/`: baseline forecasts and anomaly calculations
- `app/`: Streamlit dashboard
- `outputs/`: local database and generated files (ignored by Git)

The database contains sensitive financial data and is intentionally kept out of version control. This is a local single-user project; it is not configured for public multi-user hosting or account isolation.

See [LAUNCH_READINESS.md](LAUNCH_READINESS.md) for the security and architecture work required before exposing real financial data to multiple users.
