# Finance Ledger Lab

Finance Ledger Lab turns bank statements into searchable spending analytics. It is a local, single-user Python app: upload a PDF or CSV, review the extracted transactions, import them into SQLite, and explore results in Streamlit.

## Run It

From the project folder in Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app/streamlit_app.py --server.address 127.0.0.1
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process RemoteSigned`, then activate the environment again. Open the local URL Streamlit prints, usually `http://localhost:8501`.

## Upload a Statement

1. Open **Upload statements** near the top of the app.
2. Select one or more PDF or CSV files. The combined upload limit is 50 MB, with a 25 MB limit per file.
3. Choose **Parse statements for review**. Check the bank/account shown, extracted rows, and any validation warnings.
4. Choose **Import reviewed statements**. Statements with warnings require an additional acknowledgement; statements with no usable transactions are not imported.

After import, the app loads transactions into `outputs/finance_raw.sqlite`, runs dbt to update the cleaned tables and dashboards, and refreshes forecasts. Re-uploading the same file is detected by its content hash. If an analytics refresh fails, use **Rebuild analytics** in the sidebar after resolving the issue.

The dashboard includes monthly spending, category trends, merchant insights, forecasts and spending alerts, category suggestions, and a searchable transaction table.

## What Happens to Your Data

- The app stores the SQLite database and generated files under `outputs/`, which is excluded from Git.
- Temporary files used to parse an upload are deleted. Uploaded content remains in the Streamlit session until that session expires.
- Parsing and storage run on the machine hosting Streamlit. If you run the app on a remote host, statements are sent to that host.
- Do not commit statements, exports, or the SQLite database to GitHub.

This project does not have sign-in or per-user data separation. Run it on your own computer with your own database. Do not expose it as a shared public app or use one shared database for multiple people with real financial data.

## Parsing and AI

The parser has dedicated formats for Alliant, Bank of America, and Capital One, plus CSV mapping and a generic fallback for other text-based PDFs. Bank formats vary, so review every preview and validation report. Scanned/image-only PDFs are not supported without OCR.

For an unfamiliar text-based PDF, you can optionally enable **Try local Ollama** in the upload panel. Install Ollama and download a model first:

```powershell
ollama pull llama3.1:8b
```

The app sends fallback requests to the Ollama service on the machine running Streamlit. AI results are still previews that need human review. No hosted AI service is configured by default.

## Classification and Forecasts

- dbt rules provide the main merchant normalization and category assignments. Edit `finance_dbt/seeds/merchant_rules.csv` or `finance_dbt/seeds/category_rules.csv`, then select **Rebuild analytics**.
- The optional TF-IDF classifier suggests categories for uncategorized transactions. Run it from the project folder:

  ```powershell
  python ml/tfidf_category_classifier.py --db outputs/finance_raw.sqlite
  ```

- Forecasts use a recent rolling average with a simple trend. Spending alerts compare the latest month with recent history. These are transparent baselines, not advanced predictive models. Treat results cautiously when the newest statement covers only part of a month.
- Category suggestions are for review and do not overwrite dbt assignments. The TF-IDF evaluation measures agreement with existing rule labels, not independently verified real-world accuracy.

## Command-Line Parsing

To parse and ingest without the dashboard:

```powershell
python ingestion/statement_parser.py "C:\path\to\statement.pdf" --ingest --db outputs/finance_raw.sqlite -o outputs/transactions.csv
```

For standalone dbt commands, make a local profile and set its database path:

```powershell
Copy-Item finance_dbt\profiles.example.yml finance_dbt\profiles.yml
$env:FINANCE_DB_PATH = (Join-Path (Get-Location) "outputs/finance_raw.sqlite")
$env:FINANCE_DB_DIR = (Join-Path (Get-Location) "outputs")
dbt seed --project-dir finance_dbt --profiles-dir finance_dbt
dbt run --project-dir finance_dbt --profiles-dir finance_dbt
dbt test --project-dir finance_dbt --profiles-dir finance_dbt
python forecasting/baseline_forecasts.py --db outputs/finance_raw.sqlite
```

## Tests

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python tests/run_dbt_integration.py
```

The integration test uses synthetic data and does not test parsing accuracy across every bank.

## Project Layout

- `app/`: Streamlit dashboard and upload workflow
- `ingestion/`: PDF/CSV parsing, validation, and SQLite loading
- `finance_dbt/`: dbt staging, deduplication, merchant/category rules, and analytics marts
- `ml/`: TF-IDF and optional local Ollama category suggestions
- `forecasting/`: baseline forecasts and spending anomaly checks
- `tests/`: unit and synthetic integration tests
- `outputs/`: local database and generated artifacts; not tracked by Git

See [LAUNCH_READINESS.md](LAUNCH_READINESS.md) before considering a multi-user or public deployment.
