# AI-Assisted Statement Parsing

This project treats AI as a fallback and review layer, not as the source of truth
for financial data.

## Architecture

```text
PDF / CSV statement
    -> text extraction
    -> bank-specific rule parser
    -> optional LLM fallback for unknown/low-confidence layouts
    -> schema validation
    -> raw SQLite ingestion tables
    -> dbt staging/intermediate/mart models
    -> Streamlit analytics dashboard
```

## Why This Design

Rule-based parsing is still the safest path for dates, amounts, debit/credit
signs, and duplicate handling. These fields need repeatable behavior. The LLM is
useful when a new bank statement format is unfamiliar or when the rule parser
cannot confidently identify rows.

The parser validates LLM output before loading it:

- transaction dates must parse
- amounts must parse numerically
- descriptions are required
- dates must fall inside the statement period when that period is known
- LLM rows are marked with `parser_method = llm_structured_extraction`
- LLM rows are marked with an AI review confidence value

## Local Ollama Setup

Install Ollama, then pull a free local model:

```powershell
ollama pull llama3.1:8b
```

Recommended model:

```text
llama3.1:8b
```

Other reasonable free local models:

```text
mistral:7b
qwen2.5:7b
```

## Run With AI Fallback

Use the normal parser first and only call the LLM for unknown or low-confidence
PDF layouts:

```powershell
& C:/Users/rosha/anaconda3/python.exe "C:/Users/rosha/Documents/Codex/2026-07-08/can-you-write-a-script-to/outputs/bank_statement_parser.py" "C:/Users/rosha/Downloads/your_statement.pdf" -o "C:/Users/rosha/Downloads/transactions_ai.csv" --ai-fallback --ai-model llama3.1:8b
```

To ingest into SQLite:

```powershell
& C:/Users/rosha/anaconda3/python.exe "C:/Users/rosha/Documents/Codex/2026-07-08/can-you-write-a-script-to/outputs/bank_statement_parser.py" "C:/Users/rosha/Downloads/your_statement.pdf" -o "C:/Users/rosha/Downloads/transactions_ai.csv" --ingest --db "C:/Users/rosha/Documents/Codex/2026-07-08/can-you-write-a-script-to/outputs/finance_raw_final.sqlite" --ai-fallback --ai-model llama3.1:8b
```

## Evaluation Plan

To make this resume-strong, create a labeled test set of 100 to 300
transactions. Compare parser output to the labeled data at the field level:

- transaction date accuracy
- posting date accuracy
- merchant/description accuracy
- amount accuracy
- debit/credit accuracy
- category accuracy

This turns the project from "used an LLM on PDFs" into a validated document
intelligence pipeline.
