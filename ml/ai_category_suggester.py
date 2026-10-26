#!/usr/bin/env python3
"""
Generate local LLM category suggestions for Uncategorized transactions.

This script uses Ollama locally and writes suggestions only. It does not update
dbt categories, raw transactions, or rule seed files.

Usage:
  python ml/ai_category_suggester.py --db outputs/finance_raw.sqlite
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_DB = Path("outputs/finance_raw.sqlite")
DEFAULT_OUTPUT = Path("outputs/ai_category_suggestions.csv")
DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_LIMIT = 50
DEFAULT_CATEGORIES = [
    "Food & Dining",
    "Groceries",
    "Transportation",
    "Shopping",
    "Entertainment",
    "Health & Pharmacy",
    "Fees & Interest",
    "Transfers",
    "Credit Card Payment",
    "Investments",
    "Income",
    "Uncategorized",
]


def load_uncategorized_transactions(db_path: Path, limit: int) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        query = """
            select
                transaction_id,
                transaction_date,
                bank_name,
                merchant_name,
                raw_description,
                amount,
                transaction_type,
                category,
                subcategory
            from fct_transactions
            where category = 'Uncategorized'
            order by transaction_date desc, abs(amount) desc
        """
        if limit > 0:
            query += f"\nlimit {int(limit)}"
        return pd.read_sql_query(query, conn)


def load_category_options(db_path: Path) -> list[str]:
    categories = set(DEFAULT_CATEGORIES)
    with sqlite3.connect(db_path) as conn:
        for table_name in ("fct_transactions", "category_rules"):
            exists = conn.execute(
                "select 1 from sqlite_master where type in ('table', 'view') and name = ?",
                (table_name,),
            ).fetchone()
            if not exists:
                continue
            column = "category"
            rows = conn.execute(
                f"select distinct {column} from {table_name} where {column} is not null and trim({column}) <> ''"
            ).fetchall()
            categories.update(row[0] for row in rows)
    return sorted(categories)


def build_prompt(row: pd.Series, category_options: list[str]) -> str:
    categories = "\n".join(f"- {category}" for category in category_options)
    return f"""
You categorize personal finance transactions.

Choose one category from this list:
{categories}

Return only valid JSON with this exact shape:
{{
  "merchant_name": "clean merchant name",
  "category": "one category from the list",
  "subcategory": "short useful subcategory",
  "confidence": 0.0,
  "reason": "brief reason"
}}

Rules:
- Do not change transaction dates or amounts.
- If the transaction is a bank transfer, payment, Zelle/Venmo transfer, or movement between accounts, use Transfers unless Credit Card Payment is clearly better.
- If the description is too ambiguous, use Uncategorized with low confidence.
- Confidence must be a number from 0 to 1.

Transaction:
- bank_name: {row.get("bank_name", "")}
- transaction_date: {row.get("transaction_date", "")}
- merchant_name: {row.get("merchant_name", "")}
- raw_description: {row.get("raw_description", "")}
- amount: {row.get("amount", "")}
- transaction_type: {row.get("transaction_type", "")}
""".strip()


def extract_json_object(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").strip()
        raw = raw.removesuffix("```").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model response did not contain a JSON object.")
    return json.loads(raw[start : end + 1])


def call_ollama(prompt: str, model: str, ollama_url: str) -> dict[str, Any]:
    endpoint = ollama_url.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach Ollama at {endpoint}. Start Ollama and pull {model} first."
        ) from exc
    return extract_json_object(str(data.get("response", "")))


def normalize_suggestion(
    row: pd.Series,
    response: dict[str, Any],
    category_options: list[str],
    model: str,
) -> dict[str, Any]:
    category = str(response.get("category", "Uncategorized")).strip()
    if category not in category_options:
        category = "Uncategorized"

    try:
        confidence = float(response.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return {
        "transaction_id": row["transaction_id"],
        "transaction_date": row["transaction_date"],
        "bank_name": row["bank_name"],
        "merchant_name": row["merchant_name"],
        "raw_description": row["raw_description"],
        "amount": row["amount"],
        "transaction_type": row["transaction_type"],
        "suggested_merchant_name": str(response.get("merchant_name", "")).strip(),
        "suggested_category": category,
        "suggested_subcategory": str(response.get("subcategory", "")).strip(),
        "suggestion_confidence": round(confidence, 4),
        "suggestion_status": "review" if confidence >= 0.7 else "low_confidence",
        "suggestion_reason": str(response.get("reason", "")).strip(),
        "model_name": model,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def make_suggestions(
    transactions: pd.DataFrame,
    category_options: list[str],
    model: str,
    ollama_url: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in transactions.iterrows():
        response = call_ollama(build_prompt(row, category_options), model, ollama_url)
        rows.append(normalize_suggestion(row, response, category_options, model))
    return pd.DataFrame(rows)


def write_suggestions(db_path: Path, suggestions: pd.DataFrame, output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    suggestions.to_csv(output_csv, index=False)
    with sqlite3.connect(db_path) as conn:
        suggestions.to_sql("ai_category_suggestions", conn, if_exists="replace", index=False)


def empty_suggestions() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "transaction_id",
            "transaction_date",
            "bank_name",
            "merchant_name",
            "raw_description",
            "amount",
            "transaction_type",
            "suggested_merchant_name",
            "suggested_category",
            "suggested_subcategory",
            "suggestion_confidence",
            "suggestion_status",
            "suggestion_reason",
            "model_name",
            "created_at",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate local AI category suggestions with Ollama.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output suggestions CSV path")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL, help="Ollama server URL")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max uncategorized rows to score; 0 means all")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    transactions = load_uncategorized_transactions(db_path, args.limit)
    if transactions.empty:
        suggestions = empty_suggestions()
    else:
        categories = load_category_options(db_path)
        suggestions = make_suggestions(transactions, categories, args.model, args.ollama_url)

    write_suggestions(db_path, suggestions, Path(args.output))
    print(f"Uncategorized rows scored: {len(transactions)}")
    print(f"AI suggestions written to: {args.output}")
    print("Suggestions table: ai_category_suggestions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
