# Finance Ledger Lab

An end-to-end personal finance analytics project that turns bank statement files into validated transactions, dbt models, category suggestions, forecasts, and an interactive dashboard.

## What it does

- Ingests PDF and CSV statements into SQLite, preserving source-file metadata and raw transaction text.
- Parses supported layouts for Alliant Credit Union, Bank of America, and Capital One, with a conservative generic parser and optional local Ollama fallback for unfamiliar PDF layouts.
- Uses dbt staging, intermediate, and mart models for normalization, deduplication, categorization, and analytics.
- Provides editable merchant and category mapping seeds.
- Generates TF-IDF and optional local LLM category suggestions for review. Suggestions do not silently modify transaction categories.
- Builds simple rolling-baseline spending forecasts and category anomaly flags.
- Displays transactions and analytics in Streamlit.

## Architecture

```text
Bank statement PDF/CSV
        |
        v
Python ingestion and validation ---- optional Ollama extraction/category suggestions
        |
        v
SQLite raw_statement_files + raw_transactions
        |
        v
dbt staging -> intermediate -> fact/mart tables
        |                         |
        v                         v
Forecasting scripts          Streamlit dashboard
```

## Setup

Use Python 3.10 or newer. From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The checked-in dbt profile is an example. Create a local profile using the provided template and point it to your own SQLite database. Keep personal statements and databases out of version control; `.gitignore` excludes local output data.

```powershell
Copy-Item finance_dbt\profiles.example.yml finance_dbt\profiles.yml
```

Set `FINANCE_DB_PATH` to the absolute path of the SQLite database before running dbt. For example:

```powershell
$env:FINANCE_DB_PATH = (Join-Path (Get-Location) "outputs/finance_raw_final.sqlite")
$env:FINANCE_DB_DIR = (Join-Path (Get-Location) "outputs")
```

## Run the pipeline

Parse and ingest a statement:

```powershell
python outputs/bank_statement_parser.py "C:\path\to\statement.pdf" --ingest --db outputs/finance_raw_final.sqlite -o outputs/transactions.csv
```

Build and validate dbt models, then refresh forecast tables:

```powershell
dbt seed --project-dir finance_dbt --profiles-dir finance_dbt
dbt run --project-dir finance_dbt --profiles-dir finance_dbt
dbt test --project-dir finance_dbt --profiles-dir finance_dbt
python forecasting/baseline_forecasts.py --db outputs/finance_raw_final.sqlite
```

Start the dashboard:

```powershell
streamlit run app/streamlit_app.py
```

## Optional local AI

Install Ollama separately and download a model such as `llama3.1:8b`. Then run:

```powershell
ollama pull llama3.1:8b
python ml/ai_category_suggester.py --db outputs/finance_raw_final.sqlite --model llama3.1:8b --limit 25
```

For an unfamiliar text-based PDF layout, the parser also supports `--ai-fallback`. Review its output and validation report before relying on it. Scanned PDFs require OCR.

## Project layout

| Path | Purpose |
| --- | --- |
| `outputs/bank_statement_parser.py` | PDF/CSV ingestion, validation, and SQLite loading |
| `finance_dbt/` | dbt project, models, tests, and mapping seeds |
| `ml/` | TF-IDF classifier and optional Ollama suggestion script |
| `forecasting/` | Baseline forecasts and category anomaly calculations |
| `app/` | Streamlit dashboard |
| `transform/` | Earlier standalone transformation runner retained for comparison |

## Data and model limitations

Statement layouts vary, so parser support is format-specific. Always inspect the generated validation report when adding a bank or statement layout. Forecasts use a short-history rolling baseline and are exploratory, not financial advice. AI and TF-IDF outputs are suggestions for review; the dbt rules remain the categorization source of truth.

The local SQLite database contains sensitive financial information and is intentionally excluded from Git. The current app is a local single-user portfolio project; it is not configured for public multi-user hosting or account isolation.
