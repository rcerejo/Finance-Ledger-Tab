#!/usr/bin/env python3
"""
Parse bank and credit-card statement PDFs into normalized CSV rows.

Supported well from the included samples:
  - Alliant Credit Union account statements
  - Bank of America credit-card eStatements

The parser also includes a conservative generic fallback for other text-based
PDF statements. Scanned/image-only PDFs will need OCR before this script can
read them.

Usage:
  python bank_statement_parser.py "C:\\path\\statement.pdf" -o transactions.csv
  python bank_statement_parser.py "C:\\path\\statements_folder" -o transactions.csv
  python bank_statement_parser.py statement.pdf --debug-text debug
  python bank_statement_parser.py unknown_bank.pdf --ai-fallback --ai-model llama3.1:8b
  python bank_statement_parser.py statement1.pdf statement2.pdf --json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: pypdf. Install it with: python -m pip install pypdf"
    ) from exc


DATE_MDY = re.compile(r"\b(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?\b")
DATE_MONTH_NAME = re.compile(
    r"\b(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(?P<d>\d{1,2})(?:,\s*(?P<y>\d{4}))?\b",
    re.I,
)
MONEY = re.compile(r"(?<![\w.])(?:-\$?\d{1,3}|\$?\s*\d{1,3})(?:,\d{3})*(?:\.\d{2})")
EXPORT_FIELDNAMES = [
    "transaction_id",
    "account_id",
    "bank_name",
    "transaction_date",
    "posting_date",
    "raw_description",
    "merchant_name",
    "amount",
    "category",
    "statement_month",
    "source_file",
    "created_at",
]
PARSER_NAME = "bank_statement_parser"
PARSER_VERSION = "0.3.0"


@dataclass
class StatementMeta:
    bank: str = ""
    account: str = ""
    period_start: str = ""
    period_end: str = ""


@dataclass
class Transaction:
    source_file: str
    source_row_number: int
    page_number: int
    source_method: str
    confidence: str
    bank: str
    account: str
    account_name: str
    transaction_date: str
    posting_date: str
    description: str
    amount: str
    balance: str
    raw_text: str


@dataclass
class ParsedStatement:
    source_file: str
    file_hash: str
    bank_name: str
    account_id: str
    statement_period_start: str
    statement_period_end: str
    rows: list[Transaction]


def clean_text(text: str) -> str:
    return (
        text.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\x96", "-")
        .replace("\u2022", " ")
        .replace("\xa0", " ")
    )


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", clean_text(text)).strip()


def clean_description(text: str) -> str:
    text = re.sub(r"\s+-\s*-+\s*$", "", clean_text(text))
    return squash(text)


def parse_amount(raw: str) -> Decimal | None:
    raw = clean_text(raw).replace("$", "").replace(",", "").replace(" ", "")
    if not raw or raw in {"-", "--"}:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def amount_to_str(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def statement_month(transaction_date: str) -> str:
    return transaction_date[:7] if re.match(r"^\d{4}-\d{2}-\d{2}$", transaction_date) else ""


def make_account_id(row: Transaction) -> str:
    parts = [row.bank, row.account, row.account_name]
    return "_".join(re.sub(r"[^A-Za-z0-9]+", "_", part).strip("_") for part in parts if part) or "unknown_account"


def infer_merchant_name(description: str) -> str:
    merchant = description
    merchant = re.sub(r"^(WITHDRAWAL|DEPOSIT)\s+", "", merchant, flags=re.I)
    merchant = re.sub(r"^(ACH|ONLINE BANKING|DIVIDEND)\s+", "", merchant, flags=re.I)
    merchant = re.split(r"\s+(TYPE:|ID:|DATA:|CO:|NAME:)\s+", merchant, maxsplit=1, flags=re.I)[0]
    return squash(merchant)


def make_transaction_id(row: Transaction) -> str:
    key = "|".join(
        [
            row.source_file,
            row.bank,
            row.account,
            row.account_name,
            row.transaction_date,
            row.posting_date,
            row.description,
            row.amount,
            row.balance,
            str(row.page_number),
            str(row.source_row_number),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def export_row(row: Transaction, created_at: str) -> dict[str, str]:
    return {
        "transaction_id": make_transaction_id(row),
        "account_id": make_account_id(row),
        "bank_name": row.bank,
        "transaction_date": row.transaction_date,
        "posting_date": row.posting_date,
        "raw_description": row.description,
        "merchant_name": infer_merchant_name(row.description),
        "amount": row.amount,
        "category": "",
        "statement_month": statement_month(row.transaction_date),
        "source_file": row.source_file,
        "created_at": created_at,
    }


def extract_pages(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:
            text = page.extract_text() or ""
        pages.append(clean_text(text))
    return pages


def parse_statement(
    path: Path,
    *,
    ai_fallback: bool = False,
    ai_only: bool = False,
    ai_model: str = "llama3.1:8b",
    ai_url: str = "http://localhost:11434",
) -> ParsedStatement:
    file_hash = file_sha256(path)
    if path.suffix.lower() == ".pdf":
        pages = extract_pages(path)
        meta = infer_meta("\n".join(pages))
        rows = [] if ai_only else parse_pages(path, pages, meta)
        if ai_only or should_use_ai_fallback(rows, meta, ai_fallback):
            ai_rows = parse_with_llm(path, pages, meta, ai_model=ai_model, ai_url=ai_url)
            if ai_rows:
                rows = ai_rows
    elif path.suffix.lower() == ".csv":
        rows, meta = parse_csv_statement(path)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")

    account_id = make_account_id(rows[0]) if rows else ""
    return ParsedStatement(
        source_file=str(path),
        file_hash=file_hash,
        bank_name=meta.bank,
        account_id=account_id,
        statement_period_start=meta.period_start,
        statement_period_end=meta.period_end,
        rows=rows,
    )


def should_use_ai_fallback(rows: list[Transaction], meta: StatementMeta, enabled: bool) -> bool:
    if not enabled:
        return False
    if not rows:
        return True
    if meta.bank == "Unknown":
        return True
    return any(row.confidence == "low" for row in rows)


def infer_meta(text: str) -> StatementMeta:
    compact = squash(text)
    lower = compact.lower()
    meta = StatementMeta()

    if "alliantcreditunion.org" in lower or "alliant credit union" in lower:
        meta.bank = "Alliant Credit Union"
    elif "bank of america" in lower or "bankofamerica.com" in lower:
        meta.bank = "Bank of America"
    elif "capitalone.com" in lower or "capital one" in lower:
        meta.bank = "Capital One"
    else:
        meta.bank = "Unknown"

    account_patterns = [
        r"Account\s*#\s*([0-9 ][0-9 ]{6,})",
        r"AccountNumber\s+StatementPeriod\s+Page\s+([0-9]{4,})",
        r"AccountNumber\s*([0-9]{4,})",
        r"Account Number:\s*([0-9 ][0-9 ]{6,})",
        r"(?:Debit Card|Savings|Spending)\s*-\s*([0-9]{4,})",
    ]
    for pattern in account_patterns:
        match = re.search(pattern, compact, re.I)
        if match:
            meta.account = squash(match.group(1))
            break

    period_patterns = [
        r"(\d{1,2}/\d{1,2}/\d{2,4})\s+thru\s+(\d{1,2}/\d{1,2}/\d{2,4})",
        r"([A-Z][a-z]+ \d{1,2})\s*-\s*([A-Z][a-z]+ \d{1,2}, \d{4})",
        r"([A-Z][a-z]{2,8} \d{1,2})\s*-\s*([A-Z][a-z]{2,8} \d{1,2}, \d{4})",
    ]
    for pattern in period_patterns:
        match = re.search(pattern, compact)
        if match:
            meta.period_start = match.group(1)
            meta.period_end = match.group(2)
            break

    return meta


def year_from_meta(meta: StatementMeta) -> int | None:
    years = re.findall(r"\b(20\d{2}|\d{2})\b", meta.period_end)
    if not years:
        return None
    year = int(years[-1])
    return 2000 + year if year < 100 else year


def normalize_date(raw: str, meta: StatementMeta) -> str:
    match = DATE_MDY.search(raw)
    if not match:
        month_match = DATE_MONTH_NAME.search(raw)
        if month_match:
            month = datetime.strptime(month_match.group("mon")[:3].title(), "%b").month
            day = int(month_match.group("d"))
            raw_year = month_match.group("y")
            year = int(raw_year) if raw_year else (year_from_meta(meta) or date.today().year)
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return ""
        for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(raw.strip(), fmt).date().isoformat()
            except ValueError:
                continue
        return ""
    month = int(match.group("m"))
    day = int(match.group("d"))
    raw_year = match.group("y")
    if raw_year:
        year = int(raw_year)
        if year < 100:
            year += 2000
    else:
        year = year_from_meta(meta) or date.today().year
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return raw


def parse_period_date(raw: str, meta: StatementMeta) -> str:
    normalized = normalize_date(raw, meta)
    if normalized:
        return normalized

    compact = squash(raw)
    end_year = year_from_meta(meta)
    if not end_year:
        return ""
    for fmt in ("%B %d", "%b %d"):
        try:
            parsed = datetime.strptime(compact, fmt)
            return date(end_year, parsed.month, parsed.day).isoformat()
        except ValueError:
            continue
    return ""


def date_in_statement_period(transaction_date: str, meta: StatementMeta) -> bool:
    if not transaction_date:
        return False
    start = parse_period_date(meta.period_start, meta)
    end = parse_period_date(meta.period_end, meta)
    if not start or not end:
        return True
    return start <= transaction_date <= end


def llm_prompt(text: str, meta: StatementMeta, *, page_number: int, account_name: str) -> str:
    return f"""
You extract bank statement transactions into strict JSON.

Return only valid JSON with this shape:
{{
  "bank_name": "string",
  "account_id": "string",
  "statement_period_start": "YYYY-MM-DD or empty string",
  "statement_period_end": "YYYY-MM-DD or empty string",
  "transactions": [
    {{
      "transaction_date": "YYYY-MM-DD",
      "posting_date": "YYYY-MM-DD or empty string",
      "description": "original transaction description",
      "amount": "-12.34 for debits, 12.34 for credits",
      "transaction_type": "debit or credit",
      "account_name": "account section name or empty string",
      "balance": "optional numeric string or empty string",
      "confidence": "low, medium, or high"
    }}
  ]
}}

Rules:
- Do not invent transactions.
- Do not include beginning balance, ending balance, subtotals, summaries, APR, fees tables without transaction rows, or disclosure text.
- Preserve the source description text.
- Use negative amounts for money leaving the account and positive amounts for money entering the account.
- If a date or amount is uncertain, omit that transaction.
- Extract transactions only from the supplied page; do not repeat transactions from other pages.
- Ignore opening/closing balances and summary tables.

Known parser metadata:
- bank_name: {meta.bank}
- account_id: {meta.account}
- statement_period_start: {meta.period_start}
- statement_period_end: {meta.period_end}
- current_account_section: {account_name}
- source_page_number: {page_number}

Page text:
```text
{text}
```
""".strip()


def extract_json_object(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response did not contain a JSON object.")
    return json.loads(raw[start : end + 1])


def call_ollama(prompt: str, *, ai_model: str, ai_url: str) -> dict[str, Any]:
    endpoint = ai_url.rstrip("/") + "/api/generate"
    payload = {
        "model": ai_model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 4096},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach Ollama at {endpoint}. Start Ollama and pull the model first."
        ) from exc
    return extract_json_object(str(data.get("response", "")))


def transaction_from_llm(
    item: dict[str, Any],
    *,
    pdf_path: Path,
    row_number: int,
    meta: StatementMeta,
    llm_bank: str,
    llm_account: str,
    page_number: int,
    default_account_name: str,
) -> Transaction | None:
    tx_date = normalize_date(str(item.get("transaction_date", "")), meta)
    posting_date = normalize_date(str(item.get("posting_date", "")), meta)
    description = squash(str(item.get("description", "")))
    amount = parse_amount(str(item.get("amount", "")))
    tx_type = squash(str(item.get("transaction_type", ""))).lower()
    balance = parse_amount(str(item.get("balance", ""))) if str(item.get("balance", "")).strip() else None
    confidence = squash(str(item.get("confidence", "medium"))).lower()
    account_name = squash(str(item.get("account_name", ""))) or default_account_name

    if amount is not None and tx_type == "debit" and amount > 0:
        amount *= Decimal("-1")
    if amount is not None and tx_type == "credit" and amount < 0:
        amount *= Decimal("-1")

    if not tx_date or not description or amount is None:
        return None
    if not date_in_statement_period(tx_date, meta):
        return None
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"

    return Transaction(
        source_file=str(pdf_path),
        source_row_number=row_number,
        page_number=page_number,
        source_method="llm_structured_extraction",
        confidence=f"{confidence}_ai_review",
        bank=llm_bank or meta.bank or "Unknown",
        account=llm_account or meta.account,
        account_name=account_name,
        transaction_date=tx_date,
        posting_date=posting_date,
        description=description,
        amount=amount_to_str(amount),
        balance=amount_to_str(balance),
        raw_text=json.dumps(item, ensure_ascii=False),
    )


def parse_with_llm(
    pdf_path: Path,
    pages: list[str],
    meta: StatementMeta,
    *,
    ai_model: str,
    ai_url: str,
) -> list[Transaction]:
    if not any(squash(page) for page in pages):
        raise RuntimeError("PDF has no extractable text. OCR is required before AI parsing.")
    rows: list[Transaction] = []
    current_account_name = ""
    account_header = re.compile(r"^\s*(Debit Card|Savings|Spending)\s*-\s*[0-9]{4,}\s*$", re.I)

    for page_number, page_text in enumerate(pages, start=1):
        if not squash(page_text):
            continue
        for line in page_text.splitlines():
            header = account_header.match(line)
            if header:
                current_account_name = squash(header.group(1))
        print(f"AI parsing page {page_number}/{len(pages)}...", file=sys.stderr, flush=True)
        response = call_ollama(
            llm_prompt(
                page_text,
                meta,
                page_number=page_number,
                account_name=current_account_name,
            ),
            ai_model=ai_model,
            ai_url=ai_url,
        )
        llm_bank = squash(str(response.get("bank_name", "")))
        llm_account = squash(str(response.get("account_id", "")))
        if response.get("statement_period_start"):
            meta.period_start = str(response["statement_period_start"])
        if response.get("statement_period_end"):
            meta.period_end = str(response["statement_period_end"])
        if llm_bank and meta.bank == "Unknown":
            meta.bank = llm_bank
        if llm_account and not meta.account:
            meta.account = llm_account

        transactions = response.get("transactions", [])
        if not isinstance(transactions, list):
            raise ValueError(f"LLM response for page {page_number} did not include a transactions list.")
        for item in transactions:
            if not isinstance(item, dict):
                continue
            tx = transaction_from_llm(
                item,
                pdf_path=pdf_path,
                row_number=len(rows) + 1,
                meta=meta,
                llm_bank=llm_bank,
                llm_account=llm_account,
                page_number=page_number,
                default_account_name=current_account_name,
            )
            if tx:
                rows.append(tx)
    if not rows:
        raise ValueError("AI parsing returned no valid transaction rows. Check the PDF text and Ollama output.")
    return rows


def parse_pdf(pdf_path: Path) -> list[Transaction]:
    pages = extract_pages(pdf_path)
    text = "\n".join(pages)
    meta = infer_meta(text)
    return parse_pages(pdf_path, pages, meta)


def parse_pages(pdf_path: Path, pages: list[str], meta: StatementMeta) -> list[Transaction]:
    if meta.bank == "Alliant Credit Union":
        return parse_alliant(pdf_path, pages, meta)
    if meta.bank == "Bank of America":
        return parse_bank_of_america(pdf_path, pages, meta)
    if meta.bank == "Capital One":
        return parse_capital_one(pdf_path, pages, meta)
    return parse_generic(pdf_path, pages, meta)


def parse_alliant(pdf_path: Path, pages: list[str], meta: StatementMeta) -> list[Transaction]:
    transactions: list[Transaction] = []
    account_name = ""
    last_tx: Transaction | None = None
    section_re = re.compile(r"^\s*([A-Z][A-Z ]+?)\s+\(ID\s*(\d+)\)")
    row_re = re.compile(r"^\s*(\d{1,2}/\d{1,2}/\d{2,4})\s+(.+)$")

    for page_number, page_text in enumerate(pages, start=1):
        for line in page_text.splitlines():
            line = clean_text(line.rstrip())
            if not line.strip():
                continue

            section = section_re.match(line)
            if section:
                account_name = squash(section.group(1))
                last_tx = None
                continue

            row = row_re.match(line)
            if row:
                tx_date = normalize_date(row.group(1), meta)
                body = row.group(2)
                money_matches = list(MONEY.finditer(body))
                description = body[: money_matches[0].start()] if money_matches else body
                description = clean_description(description)

                if description.upper() in {"BEGINNINGBALANCE", "BEGINNING BALANCE", "ENDING BALANCE"}:
                    last_tx = None
                    continue
                if any(
                    marker in description.upper()
                    for marker in ("ANNUAL PERCENTAGE YIELD", "BASED ON AVERAGE DAILY BALANCE")
                ):
                    last_tx = None
                    continue

                amounts = [parse_amount(match.group(0)) for match in money_matches]
                amounts = [amount for amount in amounts if amount is not None]
                amount: Decimal | None = None
                balance: Decimal | None = amounts[-1] if amounts else None
                upper = description.upper()

                if "WITHDRAWAL" in upper:
                    negatives = [amount for amount in amounts if amount < 0]
                    amount = negatives[0] if negatives else (amounts[0] * Decimal("-1") if amounts else None)
                elif "DEPOSIT" in upper:
                    amount = amounts[0] if amounts else None
                elif amounts:
                    amount = amounts[0] if len(amounts) == 1 else amounts[-2]

                tx = Transaction(
                    source_file=str(pdf_path),
                    source_row_number=len(transactions) + 1,
                    page_number=page_number,
                    source_method="alliant_known_layout",
                    confidence="high",
                    bank=meta.bank,
                    account=meta.account,
                    account_name=account_name,
                    transaction_date=tx_date,
                    posting_date="",
                    description=description,
                    amount=amount_to_str(amount),
                    balance=amount_to_str(balance),
                    raw_text=squash(line),
                )
                transactions.append(tx)
                last_tx = tx
                continue

            if last_tx and not re.search(r"\b(Joint Owner|Statement|Page|REPORTING INFORMATION)\b", line, re.I):
                extra = squash(line)
                if extra and not DATE_MDY.search(extra):
                    last_tx.description = squash(f"{last_tx.description} {extra}")
                    last_tx.raw_text = squash(f"{last_tx.raw_text} {extra}")

    return transactions


def parse_bank_of_america(pdf_path: Path, pages: list[str], meta: StatementMeta) -> list[Transaction]:
    transactions: list[Transaction] = []
    row_re = re.compile(r"^\s*(\d{1,2}/\d{1,2})\s+(\d{1,2}/\d{1,2})\s+(.+)$")

    for page_number, page_text in enumerate(pages, start=1):
        for line in page_text.splitlines():
            line = clean_text(line.rstrip())
            row = row_re.match(line)
            if not row:
                continue

            money_matches = list(MONEY.finditer(line))
            if not money_matches:
                continue
            amount_span = money_matches[-1]
            amount = parse_amount(amount_span.group(0))
            if amount is None:
                continue

            body = row.group(3)[: amount_span.start() - row.start(3)]
            body = re.sub(r"\s+\d{4}\s*$", "", body)  # account last four
            body = re.sub(r"\s+\d{4,20}\s*$", "", body)  # reference number
            description = squash(body)

            if not description or description.upper().startswith("TOTAL "):
                continue

            transactions.append(
                Transaction(
                    source_file=str(pdf_path),
                    source_row_number=len(transactions) + 1,
                    page_number=page_number,
                    source_method="bofa_known_layout",
                    confidence="high",
                    bank=meta.bank,
                    account=meta.account,
                    account_name="Credit Card",
                    transaction_date=normalize_date(row.group(1), meta),
                    posting_date=normalize_date(row.group(2), meta),
                    description=description,
                    amount=amount_to_str(amount),
                    balance="",
                    raw_text=squash(line),
                )
            )

    return transactions


def parse_capital_one(pdf_path: Path, pages: list[str], meta: StatementMeta) -> list[Transaction]:
    transactions: list[Transaction] = []
    account_name = ""
    account_number = meta.account
    last_tx: Transaction | None = None
    account_re = re.compile(r"^\s*(Debit Card|Savings|Spending)\s*-\s*([0-9]{4,})\s*$", re.I)
    row_re = re.compile(
        r"^\s*((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})\s+"
        r"(.+?)\s+"
        r"(Credit|Debit)\s+"
        r"([+-])\s*\$?([0-9,]+\.\d{2})\s+"
        r"\$?([0-9,]+\.\d{2})\s*$",
        re.I,
    )

    for page_number, page_text in enumerate(pages, start=1):
        for raw_line in page_text.splitlines():
            line = clean_text(raw_line.rstrip())
            if not line.strip():
                continue

            account = account_re.match(line)
            if account:
                account_name = squash(account.group(1))
                account_number = account.group(2)
                last_tx = None
                continue

            row = row_re.match(line)
            if row:
                description = clean_description(row.group(2))
                if description.upper() in {"OPENING BALANCE", "CLOSING BALANCE"}:
                    last_tx = None
                    continue

                amount = parse_amount(row.group(5))
                balance = parse_amount(row.group(6))
                if amount is None:
                    last_tx = None
                    continue
                if row.group(4) == "-":
                    amount *= Decimal("-1")

                tx = Transaction(
                    source_file=str(pdf_path),
                    source_row_number=len(transactions) + 1,
                    page_number=page_number,
                    source_method="capital_one_known_layout",
                    confidence="high",
                    bank=meta.bank,
                    account=account_number,
                    account_name=account_name,
                    transaction_date=normalize_date(row.group(1), meta),
                    posting_date="",
                    description=description,
                    amount=amount_to_str(amount),
                    balance=amount_to_str(balance),
                    raw_text=squash(line),
                )
                transactions.append(tx)
                last_tx = tx
                continue

            if last_tx and not re.search(
                r"\b(Page \d+ of|capitalone\.com|STATEMENT PERIOD|DATE\s+DESCRIPTION|APY|DAYS IN STATEMENT|Account Summary)\b",
                line,
                re.I,
            ):
                extra = squash(line)
                if extra and not DATE_MONTH_NAME.match(extra):
                    last_tx.description = squash(f"{last_tx.description} {extra}")
                    last_tx.raw_text = squash(f"{last_tx.raw_text} {extra}")

    return transactions


def parse_generic(pdf_path: Path, pages: list[str], meta: StatementMeta) -> list[Transaction]:
    transactions: list[Transaction] = []
    row_re = re.compile(r"^\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(.+)$")

    for page_number, page_text in enumerate(pages, start=1):
        for line in page_text.splitlines():
            line = clean_text(line.rstrip())
            row = row_re.match(line)
            if not row:
                continue
            money_matches = list(MONEY.finditer(line))
            if not money_matches:
                continue
            amount = parse_amount(money_matches[-1].group(0))
            description = squash(row.group(2)[: money_matches[-1].start() - row.start(2)])
            if not description:
                continue
            transactions.append(
                Transaction(
                    source_file=str(pdf_path),
                    source_row_number=len(transactions) + 1,
                    page_number=page_number,
                    source_method="generic_date_amount",
                    confidence="medium",
                    bank=meta.bank,
                    account=meta.account,
                    account_name="",
                    transaction_date=normalize_date(row.group(1), meta),
                    posting_date="",
                    description=description,
                    amount=amount_to_str(amount),
                    balance="",
                    raw_text=squash(line),
                )
            )

    return transactions


def first_present(row: dict[str, str], names: Iterable[str]) -> str:
    lowered = {key.lower().strip(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def parse_csv_statement(csv_path: Path) -> tuple[list[Transaction], StatementMeta]:
    transactions: list[Transaction] = []
    with csv_path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    bank_guess = first_present(rows[0], ["bank_name", "bank", "institution"]) if rows else ""
    meta = StatementMeta(bank=bank_guess or "Unknown")
    if not rows:
        return transactions, meta

    dates: list[str] = []
    for index, row in enumerate(rows, start=2):
        transaction_date_raw = first_present(row, ["transaction_date", "date", "trans date", "transaction date"])
        posting_date_raw = first_present(row, ["posting_date", "post date", "posted date", "posting date"])
        description = first_present(row, ["raw_description", "description", "memo", "name", "merchant"])
        account = first_present(row, ["account_id", "account", "account_number"])
        bank = first_present(row, ["bank_name", "bank", "institution"]) or meta.bank
        amount_raw = first_present(row, ["amount", "transaction amount"])
        debit_raw = first_present(row, ["debit", "withdrawal", "withdrawals"])
        credit_raw = first_present(row, ["credit", "deposit", "deposits"])

        amount = parse_amount(amount_raw)
        if amount is None and debit_raw:
            debit = parse_amount(debit_raw)
            amount = debit * Decimal("-1") if debit is not None and debit > 0 else debit
        if amount is None and credit_raw:
            amount = parse_amount(credit_raw)
        if not transaction_date_raw or not description or amount is None:
            continue

        tx_date = normalize_date(transaction_date_raw, meta)
        if tx_date:
            dates.append(tx_date)
        transactions.append(
                Transaction(
                    source_file=str(csv_path),
                    source_row_number=len(transactions) + 1,
                    page_number=0,
                source_method="csv_header_mapping",
                confidence="medium",
                bank=bank,
                account=account,
                account_name=account,
                transaction_date=tx_date,
                posting_date=normalize_date(posting_date_raw, meta) if posting_date_raw else "",
                description=squash(description),
                amount=amount_to_str(amount),
                balance="",
                raw_text=json.dumps(row, ensure_ascii=False),
            )
        )

    if dates:
        meta.period_start = min(dates)
        meta.period_end = max(dates)
    return transactions, meta


def iter_statement_files(paths: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            files.extend(sorted(path.glob("*.pdf")))
            files.extend(sorted(path.glob("*.csv")))
        elif path.suffix.lower() in {".pdf", ".csv"}:
            files.append(path)
        else:
            raise SystemExit(f"Not a PDF/CSV or directory: {path}")
    return files


def write_csv(rows: list[Transaction], output: Path, created_at: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=EXPORT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(export_row(row, created_at) for row in rows)


def write_debug_text(pdfs: list[Path], debug_dir: Path) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    for pdf in pdfs:
        if pdf.suffix.lower() != ".pdf":
            continue
        pages = extract_pages(pdf)
        output = debug_dir / f"{pdf.stem}.extracted.txt"
        with output.open("w", encoding="utf-8") as fh:
            for page_number, page_text in enumerate(pages, start=1):
                fh.write(f"\n\n===== PAGE {page_number} =====\n")
                fh.write(page_text)


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_statement_files (
            file_id TEXT PRIMARY KEY,
            original_filename TEXT NOT NULL,
            source_file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL UNIQUE,
            bank_name TEXT,
            account_id TEXT,
            statement_period_start TEXT,
            statement_period_end TEXT,
            parser_name TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            parse_status TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            validation_status TEXT NOT NULL,
            validation_message TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_transactions (
            transaction_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            account_id TEXT,
            bank_name TEXT,
            transaction_date TEXT,
            posting_date TEXT,
            raw_description TEXT,
            merchant_name TEXT,
            amount REAL,
            category TEXT,
            statement_month TEXT,
            source_file TEXT,
            raw_row_text TEXT,
            page_number INTEGER,
            parser_method TEXT,
            parser_confidence TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (file_id) REFERENCES raw_statement_files(file_id)
        )
        """
    )
    return conn


def validation_status(validation_lines: list[str]) -> tuple[str, str]:
    message = "\n".join(validation_lines)
    if any(line.startswith("ERROR:") for line in validation_lines):
        return "failed", message
    if any(re.match(r"Row \d+:", line) for line in validation_lines):
        return "warnings", message
    return "passed", message


def ingest_statement(conn: sqlite3.Connection, statement: ParsedStatement, created_at: str) -> str:
    existing = conn.execute(
        "SELECT file_id FROM raw_statement_files WHERE file_hash = ?",
        (statement.file_hash,),
    ).fetchone()
    if existing:
        return "duplicate_file"

    validation_lines = validate_rows(statement.rows)
    status, message = validation_status(validation_lines)
    parse_status = "parsed_successfully" if statement.rows and status == "passed" else "parsed_with_warnings"
    if not statement.rows:
        parse_status = "parse_failed"

    file_id = hashlib.sha256(f"{statement.file_hash}|{statement.source_file}".encode("utf-8")).hexdigest()[:24]
    with conn:
        conn.execute(
            """
            INSERT INTO raw_statement_files (
                file_id, original_filename, source_file_path, file_hash, bank_name, account_id,
                statement_period_start, statement_period_end, parser_name, parser_version,
                parse_status, row_count, validation_status, validation_message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                Path(statement.source_file).name,
                statement.source_file,
                statement.file_hash,
                statement.bank_name,
                statement.account_id,
                statement.statement_period_start,
                statement.statement_period_end,
                PARSER_NAME,
                PARSER_VERSION,
                parse_status,
                len(statement.rows),
                status,
                message,
                created_at,
            ),
        )
        for row in statement.rows:
            exported = export_row(row, created_at)
            conn.execute(
                """
                INSERT OR IGNORE INTO raw_transactions (
                    transaction_id, file_id, account_id, bank_name, transaction_date, posting_date,
                    raw_description, merchant_name, amount, category, statement_month, source_file,
                    raw_row_text, page_number, parser_method, parser_confidence, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exported["transaction_id"],
                    file_id,
                    exported["account_id"],
                    exported["bank_name"],
                    exported["transaction_date"],
                    exported["posting_date"],
                    exported["raw_description"],
                    exported["merchant_name"],
                    float(exported["amount"]) if exported["amount"] else None,
                    exported["category"],
                    exported["statement_month"],
                    exported["source_file"],
                    row.raw_text,
                    row.page_number,
                    row.source_method,
                    row.confidence,
                    exported["created_at"],
                ),
            )
    return parse_status


def ingest_statements(db_path: Path, statements: list[ParsedStatement], created_at: str) -> dict[str, int]:
    conn = init_db(db_path)
    counts: dict[str, int] = defaultdict(int)
    try:
        for statement in statements:
            result = ingest_statement(conn, statement, created_at)
            counts[result] += 1
    finally:
        conn.close()
    return counts


def validate_rows(rows: list[Transaction]) -> list[str]:
    issues: list[str] = []

    if not rows:
        return ["ERROR: No transactions were parsed."]

    by_source: dict[str, int] = defaultdict(int)
    by_method: dict[str, int] = defaultdict(int)

    for index, row in enumerate(rows, start=2):
        by_source[row.source_file] += 1
        by_method[row.source_method] += 1

        if not row.transaction_date:
            issues.append(f"Row {index}: missing transaction_date.")
        if not row.description:
            issues.append(f"Row {index}: missing description.")
        if parse_amount(row.amount) is None:
            issues.append(f"Row {index}: missing or invalid amount.")
        if row.confidence != "high":
            issues.append(f"Row {index}: review {row.confidence}-confidence row from {row.source_method}.")

    previous_balance: dict[tuple[str, str], Decimal] = {}
    for index, row in enumerate(rows, start=2):
        if row.bank != "Alliant Credit Union" or not row.balance:
            continue
        amount = parse_amount(row.amount)
        balance = parse_amount(row.balance)
        key = (row.source_file, row.account_name)
        if amount is None or balance is None:
            continue
        if key in previous_balance:
            expected = previous_balance[key] + amount
            if abs(expected - balance) > Decimal("0.01"):
                issues.append(
                    f"Row {index}: Alliant balance check failed for {row.account_name}; "
                    f"expected {expected:.2f}, saw {balance:.2f}."
                )
        previous_balance[key] = balance

    summary = [
        "SUMMARY:",
        f"  Total transactions: {len(rows)}",
        "  Transactions by source file:",
        *[f"    {source}: {count}" for source, count in sorted(by_source.items())],
        "  Transactions by parser method:",
        *[f"    {method}: {count}" for method, count in sorted(by_method.items())],
        "",
        "ISSUES:",
    ]
    return summary + (issues if issues else ["  No validation issues found."])


def write_validation_report(rows: list[Transaction], output: Path) -> None:
    report_path = output.with_suffix(".validation.txt")
    report_path.write_text("\n".join(validate_rows(rows)) + "\n", encoding="utf-8")
    print(f"Wrote validation report to {report_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse statement PDFs into CSV rows.")
    parser.add_argument("paths", nargs="*", help="PDF file(s) or folder(s) containing PDFs")
    parser.add_argument("-o", "--output", default="transactions.csv", help="CSV output path")
    parser.add_argument("--json", action="store_true", help="Write JSON to stdout instead of CSV")
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Load parsed statements into the raw SQLite ingestion tables",
    )
    parser.add_argument(
        "--db",
        default="outputs/finance_raw.sqlite",
        help="SQLite database path to use with --ingest",
    )
    parser.add_argument(
        "--debug-text",
        metavar="DIR",
        help="Save extracted PDF text files to DIR for debugging new bank layouts",
    )
    parser.add_argument(
        "--no-validation",
        action="store_true",
        help="Do not write the .validation.txt report next to the CSV",
    )
    parser.add_argument(
        "--ai-fallback",
        action="store_true",
        help="Use local Ollama LLM extraction only when the rule parser has low/no confidence",
    )
    parser.add_argument(
        "--ai-only",
        action="store_true",
        help="Skip rule parsers and use local Ollama LLM structured extraction for PDFs",
    )
    parser.add_argument(
        "--ai-model",
        default="llama3.1:8b",
        help="Ollama model to use with --ai-fallback or --ai-only",
    )
    parser.add_argument(
        "--ai-url",
        default="http://localhost:11434",
        help="Ollama server URL to use with --ai-fallback or --ai-only",
    )
    args = parser.parse_args(argv)

    if not args.paths:
        parser.print_help()
        print(
            "\nExamples:\n"
            r'  python bank_statement_parser.py "C:\Users\rosha\Downloads\DocumentDisplay.pdf" -o transactions.csv'
            "\n"
            r'  python bank_statement_parser.py "C:\Users\rosha\Downloads" -o transactions.csv'
            "\n"
            r'  python bank_statement_parser.py statement.pdf --debug-text debug'
            "\n"
            r'  python bank_statement_parser.py statement.pdf --ingest --db finance_raw.sqlite'
            "\n"
            r'  python bank_statement_parser.py unknown_bank.pdf --ai-fallback --ai-model llama3.1:8b'
        )
        return 2

    files = iter_statement_files(args.paths)
    if args.debug_text:
        write_debug_text(files, Path(args.debug_text))

    statements: list[ParsedStatement] = []
    rows: list[Transaction] = []
    failures = 0
    for statement_file in files:
        try:
            statement = parse_statement(
                statement_file,
                ai_fallback=args.ai_fallback,
                ai_only=args.ai_only,
                ai_model=args.ai_model,
                ai_url=args.ai_url,
            )
            statements.append(statement)
            rows.extend(statement.rows)
        except Exception as exc:
            failures += 1
            print(f"Warning: failed to parse {statement_file}: {exc}", file=sys.stderr)

    created_at = datetime.now().isoformat(timespec="seconds")
    if args.ingest:
        counts = ingest_statements(Path(args.db), statements, created_at)
        print(f"Ingested statements into {args.db}: {dict(counts)}")

    if args.json:
        print(json.dumps([export_row(row, created_at) for row in rows], indent=2))
    else:
        output = Path(args.output)
        write_csv(rows, output, created_at)
        print(f"Wrote {len(rows)} transactions to {output}")
        if not args.no_validation:
            write_validation_report(rows, output)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
