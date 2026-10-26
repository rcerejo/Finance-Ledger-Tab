# AI-Assisted Statement Parsing

AI is an optional fallback, not the source of truth for financial data. The preferred flow is:

```text
PDF/CSV -> text extraction -> bank-specific parser -> optional LLM fallback
       -> validation -> SQLite -> dbt models -> dashboard
```

Deterministic parsers handle known layouts. The LLM can help with unfamiliar text-based layouts; extracted fields should still be reviewed and validated. Scanned/image-only statements require OCR before text parsing.

## Local Ollama

Install Ollama and download a model, for example:

```powershell
ollama pull llama3.1:8b
```

With the project virtual environment active and from the project root:

```powershell
python ingestion/statement_parser.py "C:\path\to\statement.pdf" -o outputs/transactions_ai.csv --ai-fallback --ai-model llama3.1:8b
```

To also ingest into SQLite:

```powershell
python ingestion/statement_parser.py "C:\path\to\statement.pdf" -o outputs/transactions_ai.csv --ingest --db outputs/finance_raw.sqlite --ai-fallback --ai-model llama3.1:8b
```

Check the generated transactions and validation report before using the data downstream. AI-extracted transactions should not be assumed correct merely because they passed schema validation.

## Evaluation

Measure extraction against manually reviewed examples. Track field-level accuracy for transaction date, posting date, description/merchant, amount, debit/credit direction, and category. Keep evaluation statements and expected labels private; do not commit real financial documents.
