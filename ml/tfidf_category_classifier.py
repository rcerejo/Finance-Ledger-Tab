#!/usr/bin/env python3
"""
Train a local TF-IDF category classifier from categorized transactions.

The model only writes suggestions for currently Uncategorized transactions.
It does not overwrite dbt categories or rule files.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline


DEFAULT_DB = Path("outputs/finance_raw_final.sqlite")
DEFAULT_MODEL = Path("outputs/tfidf_category_classifier.joblib")
DEFAULT_SUGGESTIONS = Path("outputs/category_suggestions.csv")
MIN_CONFIDENCE = 0.45


def load_transactions(db_path: Path) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(
            """
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
            """,
            conn,
        )


def build_features(df: pd.DataFrame) -> pd.Series:
    return (
        df["merchant_name"].fillna("")
        + " | "
        + df["raw_description"].fillna("")
        + " | "
        + df["transaction_type"].fillna("")
    )


def train_model(train_df: pd.DataFrame) -> Pipeline:
    model = Pipeline(
        steps=[
            (
                "tfidf",
                TfidfVectorizer(
                    lowercase=True,
                    ngram_range=(1, 2),
                    min_df=1,
                    max_features=5000,
                    strip_accents="unicode",
                ),
            ),
            (
                "classifier",
                LogisticRegression(
                    max_iter=1000,
                    class_weight="balanced",
                    random_state=42,
                ),
            ),
        ]
    )
    model.fit(build_features(train_df), train_df["category"])
    return model


def make_suggestions(model: Pipeline, uncategorized_df: pd.DataFrame) -> pd.DataFrame:
    if uncategorized_df.empty:
        return pd.DataFrame(
            columns=[
                "transaction_id",
                "transaction_date",
                "merchant_name",
                "raw_description",
                "amount",
                "suggested_category",
                "suggestion_confidence",
                "suggestion_status",
            ]
        )

    probabilities = model.predict_proba(build_features(uncategorized_df))
    classes = model.classes_
    best_indexes = probabilities.argmax(axis=1)
    suggestions = uncategorized_df[
        ["transaction_id", "transaction_date", "merchant_name", "raw_description", "amount"]
    ].copy()
    suggestions["suggested_category"] = [classes[index] for index in best_indexes]
    suggestions["suggestion_confidence"] = [round(float(probabilities[i, index]), 4) for i, index in enumerate(best_indexes)]
    suggestions["suggestion_status"] = suggestions["suggestion_confidence"].apply(
        lambda value: "review" if value >= MIN_CONFIDENCE else "low_confidence"
    )
    return suggestions.sort_values(["suggestion_status", "suggestion_confidence"], ascending=[False, False])


def write_suggestions(db_path: Path, suggestions: pd.DataFrame, output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    suggestions.to_csv(output_csv, index=False)
    with sqlite3.connect(db_path) as conn:
        suggestions.to_sql("ml_category_suggestions", conn, if_exists="replace", index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train TF-IDF category model and suggest categories.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    parser.add_argument("--model-out", default=str(DEFAULT_MODEL), help="Output model path")
    parser.add_argument("--suggestions-out", default=str(DEFAULT_SUGGESTIONS), help="Output suggestions CSV path")
    args = parser.parse_args()

    db_path = Path(args.db)
    model_path = Path(args.model_out)
    suggestions_path = Path(args.suggestions_out)

    df = load_transactions(db_path)
    train_df = df[df["category"].notna() & (df["category"] != "Uncategorized")].copy()
    uncategorized_df = df[df["category"] == "Uncategorized"].copy()

    if train_df["category"].nunique() < 2:
        raise SystemExit("Need at least two labeled categories to train the classifier.")

    model = train_model(train_df)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)

    suggestions = make_suggestions(model, uncategorized_df)
    write_suggestions(db_path, suggestions, suggestions_path)

    print(f"Training rows: {len(train_df)}")
    print(f"Categories learned: {train_df['category'].nunique()}")
    print(f"Uncategorized rows scored: {len(uncategorized_df)}")
    print(f"Suggestions written to: {suggestions_path}")
    print(f"Suggestions table: ml_category_suggestions")
    print(f"Model written to: {model_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
