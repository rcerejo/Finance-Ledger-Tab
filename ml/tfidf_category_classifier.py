#!/usr/bin/env python3
"""
Train a local TF-IDF category classifier from categorized transactions.

The model only writes suggestions for currently Uncategorized transactions.
It does not overwrite dbt categories or rule files.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import joblib
import pandas as pd
from sklearn.metrics import classification_report, accuracy_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline


DEFAULT_DB = Path("outputs/finance_raw.sqlite")
DEFAULT_MODEL = Path("outputs/tfidf_category_classifier.joblib")
DEFAULT_SUGGESTIONS = Path("outputs/category_suggestions.csv")
DEFAULT_EVALUATION = Path("outputs/tfidf_evaluation.json")
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


def evaluate_model(labeled_df: pd.DataFrame, test_size: float = 0.25) -> dict[str, object]:
    """Evaluate generalization to held-out merchants, not random transactions."""
    labeled = labeled_df[labeled_df["category"].notna() & (labeled_df["category"] != "Uncategorized")].copy()
    labeled["category"] = labeled["category"].astype(str)
    original_categories = sorted(labeled["category"].astype(str).unique().tolist())
    merchant = labeled["merchant_name"].fillna("").astype(str).str.strip().str.lower()
    fallback = labeled["raw_description"].fillna("").astype(str).str.strip().str.lower()
    labeled["_group"] = merchant.where((merchant != "") & (merchant != "unknown"), fallback)
    labeled = labeled[labeled["_group"] != ""].copy()

    group_counts = labeled.groupby("category")["_group"].nunique()
    eligible_categories = group_counts[group_counts >= 2].index.tolist()
    excluded_categories = sorted(set(original_categories) - set(eligible_categories))
    labeled = labeled[labeled["category"].isin(eligible_categories)].copy()
    categories = sorted(labeled["category"].unique().tolist())
    if len(categories) < 2 or labeled["_group"].nunique() < 4:
        return {
            "status": "insufficient_data",
            "reason": "Need at least two categories with multiple distinct merchants for a merchant-held-out evaluation.",
            "label_source": "dbt_rule_categories",
            "split_strategy": "merchant_group_holdout",
            "excluded_categories": excluded_categories,
            "rows_evaluated": 0,
        }

    splitter = GroupShuffleSplit(n_splits=50, test_size=test_size, random_state=42)
    selected: tuple[list[int], list[int]] | None = None
    best_test_coverage = -1
    for train_indexes, test_indexes in splitter.split(labeled, labeled["category"], labeled["_group"]):
        train_categories = set(labeled.iloc[train_indexes]["category"])
        test_categories = set(labeled.iloc[test_indexes]["category"])
        if train_categories != set(categories):
            continue
        if len(test_categories) > best_test_coverage:
            selected = (train_indexes.tolist(), test_indexes.tolist())
            best_test_coverage = len(test_categories)
        if test_categories == set(categories):
            selected = (train_indexes.tolist(), test_indexes.tolist())
            break

    if selected is None:
        return {
            "status": "insufficient_data",
            "reason": "Could not create a merchant-held-out split with every evaluated category represented in training.",
            "label_source": "dbt_rule_categories",
            "split_strategy": "merchant_group_holdout",
            "excluded_categories": excluded_categories,
            "rows_evaluated": 0,
        }

    train_indexes, test_indexes = selected
    train = labeled.iloc[train_indexes]
    test = labeled.iloc[test_indexes]
    model = train_model(train)
    predictions = model.predict(build_features(test))
    report = classification_report(
        test["category"],
        predictions,
        labels=categories,
        output_dict=True,
        zero_division=0,
    )
    train_groups = set(train["_group"])
    test_groups = set(test["_group"])
    return {
        "status": "completed",
        "label_source": "dbt_rule_categories",
        "split_strategy": "merchant_group_holdout",
        "rows_evaluated": int(len(test)),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "train_merchant_groups": int(len(train_groups)),
        "test_merchant_groups": int(len(test_groups)),
        "group_leakage": bool(train_groups & test_groups),
        "categories": categories,
        "excluded_categories": excluded_categories,
        "accuracy": round(float(accuracy_score(test["category"], predictions)), 4),
        "macro_precision": round(float(report["macro avg"]["precision"]), 4),
        "macro_recall": round(float(report["macro avg"]["recall"]), 4),
        "macro_f1": round(float(report["macro avg"]["f1-score"]), 4),
        "per_category": {
            category: {
                "precision": round(float(report[category]["precision"]), 4),
                "recall": round(float(report[category]["recall"]), 4),
                "f1": round(float(report[category]["f1-score"]), 4),
                "support": int(report[category]["support"]),
            }
            for category in categories
        },
        "caveat": "This measures agreement with dbt rule labels, not independent ground-truth category accuracy.",
    }


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
    parser.add_argument("--evaluation-out", default=str(DEFAULT_EVALUATION), help="JSON evaluation report path")
    args = parser.parse_args()

    db_path = Path(args.db)
    model_path = Path(args.model_out)
    suggestions_path = Path(args.suggestions_out)
    evaluation_path = Path(args.evaluation_out)

    df = load_transactions(db_path)
    train_df = df[df["category"].notna() & (df["category"] != "Uncategorized")].copy()
    uncategorized_df = df[df["category"] == "Uncategorized"].copy()

    if train_df["category"].nunique() < 2:
        raise SystemExit("Need at least two labeled categories to train the classifier.")

    evaluation = evaluate_model(train_df)
    evaluation_path.parent.mkdir(parents=True, exist_ok=True)
    evaluation_path.write_text(json.dumps(evaluation, indent=2), encoding="utf-8")

    model = train_model(train_df)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)

    suggestions = make_suggestions(model, uncategorized_df)
    write_suggestions(db_path, suggestions, suggestions_path)

    print(f"Training rows: {len(train_df)}")
    print(f"Categories learned: {train_df['category'].nunique()}")
    if evaluation["status"] == "completed":
        print(f"Merchant-held-out macro F1 vs dbt labels: {evaluation['macro_f1']}")
    else:
        print(f"Evaluation: {evaluation['reason']}")
    print(f"Evaluation report: {evaluation_path}")
    print(f"Uncategorized rows scored: {len(uncategorized_df)}")
    print(f"Suggestions written to: {suggestions_path}")
    print(f"Suggestions table: ml_category_suggestions")
    print(f"Model written to: {model_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
