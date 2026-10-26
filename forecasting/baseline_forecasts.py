#!/usr/bin/env python3
"""
Build baseline spending forecasts from dbt mart tables.

This script intentionally uses transparent statistical baselines instead of a
heavy model. With only a few months of history, rolling averages and trend
checks are easier to validate and explain.

Usage:
  python forecasting/baseline_forecasts.py --db outputs/finance_raw.sqlite
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd


DEFAULT_DB = Path("outputs") / "finance_raw.sqlite"


@dataclass(frozen=True)
class ForecastConfig:
    forecast_months: int = 3
    lookback_months: int = 3
    anomaly_threshold_ratio: float = 1.75
    anomaly_threshold_abs: float = 50.0
    excluded_categories: tuple[str, ...] = ("Transfers", "Credit Card Payment", "Income", "Investments")


def month_add(month: str, offset: int) -> str:
    period = pd.Period(month, freq="M") + offset
    return str(period)


def confidence_label(history_count: int, lookback_months: int) -> str:
    if history_count >= lookback_months:
        return "medium"
    if history_count >= 2:
        return "low"
    return "very_low"


def read_table(conn: sqlite3.Connection, table_name: str) -> pd.DataFrame:
    return pd.read_sql_query(f"select * from {table_name}", conn)


def filter_spend_categories(category: pd.DataFrame, config: ForecastConfig) -> pd.DataFrame:
    if category.empty:
        return pd.DataFrame()
    history = category.copy()
    history["spending_amount"] = pd.to_numeric(history["spending_amount"], errors="coerce")
    history = history.dropna(subset=["transaction_month", "category", "spending_amount"])
    return history[~history["category"].isin(config.excluded_categories)].copy()


def complete_months(df: pd.DataFrame, group_columns: list[str] | None = None) -> pd.DataFrame:
    """Fill missing calendar months with zero spending, without backfilling before first activity."""
    if df.empty:
        return df.copy()

    latest_month = pd.Period(df["transaction_month"].max(), freq="M")
    grouping = group_columns or []
    groups = df.groupby(grouping, dropna=False, sort=False) if grouping else [((), df)]
    completed: list[pd.DataFrame] = []
    for key, group in groups:
        group = group.copy()
        group["transaction_month"] = group["transaction_month"].astype(str)
        group = group.drop_duplicates("transaction_month", keep="last")
        months = pd.period_range(
            start=pd.Period(group["transaction_month"].min(), freq="M"),
            end=latest_month,
            freq="M",
        ).astype(str)
        indexed = group.set_index("transaction_month").reindex(months)
        indexed.index.name = "transaction_month"
        indexed["spending_amount"] = indexed["spending_amount"].fillna(0.0)
        for column, value in zip(grouping, key if isinstance(key, tuple) else (key,)):
            indexed[column] = value
        completed.append(indexed.reset_index())
    return pd.concat(completed, ignore_index=True)


def build_monthly_forecast(category: pd.DataFrame, config: ForecastConfig) -> pd.DataFrame:
    history = filter_spend_categories(category, config)
    if history.empty:
        return pd.DataFrame()

    monthly_history = (
        history.groupby("transaction_month", as_index=False)["spending_amount"]
        .sum()
        .sort_values("transaction_month")
    )
    monthly_history = complete_months(monthly_history)
    recent = monthly_history.tail(config.lookback_months)
    baseline = float(recent["spending_amount"].mean())
    slope = float(recent["spending_amount"].diff().dropna().mean()) if len(recent) > 1 else 0.0
    latest_month = str(monthly_history["transaction_month"].max())
    created_at = datetime.now().isoformat(timespec="seconds")

    rows: list[dict[str, object]] = []
    for horizon in range(1, config.forecast_months + 1):
        forecast = max(0.0, baseline + (slope * horizon))
        rows.append(
            {
                "forecast_month": month_add(latest_month, horizon),
                "metric_name": "monthly_spending",
                "forecast_amount": round(forecast, 2),
                "baseline_amount": round(baseline, 2),
                "trend_adjustment": round(slope * horizon, 2),
                "history_months_used": int(len(recent)),
                "method": "rolling_average_with_simple_trend",
                "confidence": confidence_label(len(recent), config.lookback_months),
                "excluded_categories": ", ".join(config.excluded_categories),
                "created_at": created_at,
            }
        )
    return pd.DataFrame(rows)


def build_category_forecast(category: pd.DataFrame, config: ForecastConfig) -> pd.DataFrame:
    history = filter_spend_categories(category, config)
    if history.empty:
        return pd.DataFrame()

    latest_month = str(history["transaction_month"].max())
    created_at = datetime.now().isoformat(timespec="seconds")
    rows: list[dict[str, object]] = []

    history = complete_months(history, ["category", "subcategory"])
    for (category_name, subcategory), group in history.groupby(["category", "subcategory"], dropna=False):
        group = group.sort_values("transaction_month").tail(config.lookback_months)
        if group.empty:
            continue
        baseline = float(group["spending_amount"].mean())
        slope = float(group["spending_amount"].diff().dropna().mean()) if len(group) > 1 else 0.0
        for horizon in range(1, config.forecast_months + 1):
            forecast = max(0.0, baseline + (slope * horizon))
            rows.append(
                {
                    "forecast_month": month_add(latest_month, horizon),
                    "category": category_name,
                    "subcategory": subcategory or "",
                    "forecast_amount": round(forecast, 2),
                    "baseline_amount": round(baseline, 2),
                    "trend_adjustment": round(slope * horizon, 2),
                    "history_months_used": int(len(group)),
                    "method": "category_rolling_average_with_simple_trend",
                    "confidence": confidence_label(len(group), config.lookback_months),
                    "excluded_categories": ", ".join(config.excluded_categories),
                    "created_at": created_at,
                }
            )
    return pd.DataFrame(rows)


def build_spending_anomalies(category: pd.DataFrame, config: ForecastConfig) -> pd.DataFrame:
    history = filter_spend_categories(category, config)
    if history.empty:
        return pd.DataFrame()

    history = complete_months(history, ["category", "subcategory"])
    latest_month = str(history["transaction_month"].max())
    created_at = datetime.now().isoformat(timespec="seconds")
    rows: list[dict[str, object]] = []

    for (category_name, subcategory), group in history.groupby(["category", "subcategory"], dropna=False):
        group = group.sort_values("transaction_month")
        current = group[group["transaction_month"] == latest_month]
        prior = group[group["transaction_month"] < latest_month].tail(config.lookback_months)
        if current.empty or prior.empty:
            continue

        current_amount = float(current["spending_amount"].sum())
        baseline = float(prior["spending_amount"].mean())
        variance = current_amount - baseline
        ratio = current_amount / baseline if baseline > 0 else 0.0
        is_anomaly = (
            baseline > 0
            and ratio >= config.anomaly_threshold_ratio
            and abs(variance) >= config.anomaly_threshold_abs
        )
        if not is_anomaly:
            continue

        rows.append(
            {
                "transaction_month": latest_month,
                "entity_type": "category",
                "entity_name": category_name,
                "subcategory": subcategory or "",
                "actual_amount": round(current_amount, 2),
                "baseline_amount": round(baseline, 2),
                "variance_amount": round(variance, 2),
                "variance_ratio": round(ratio, 2),
                "method": "latest_month_vs_prior_rolling_average",
                "severity": "high" if ratio >= 2.5 else "medium",
                "excluded_categories": ", ".join(config.excluded_categories),
                "created_at": created_at,
            }
        )
    return pd.DataFrame(rows)


def write_table(conn: sqlite3.Connection, df: pd.DataFrame, table_name: str) -> None:
    df.to_sql(table_name, conn, if_exists="replace", index=False)


def run(db_path: Path, config: ForecastConfig) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        category = read_table(conn, "mart_category_trends")

        monthly_forecast = build_monthly_forecast(category, config)
        category_forecast = build_category_forecast(category, config)
        anomalies = build_spending_anomalies(category, config)

        write_table(conn, monthly_forecast, "forecast_monthly_spending")
        write_table(conn, category_forecast, "forecast_category_spending")
        write_table(conn, anomalies, "spending_anomalies")

    return {
        "forecast_monthly_spending": len(monthly_forecast),
        "forecast_category_spending": len(category_forecast),
        "spending_anomalies": len(anomalies),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build baseline spending forecast tables.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database containing dbt mart tables")
    parser.add_argument("--forecast-months", type=int, default=3, help="Number of future months to forecast")
    parser.add_argument("--lookback-months", type=int, default=3, help="Number of historical months per baseline")
    parser.add_argument("--anomaly-threshold-ratio", type=float, default=1.75)
    parser.add_argument("--anomaly-threshold-abs", type=float, default=50.0)
    parser.add_argument(
        "--include-transfer-spending",
        action="store_true",
        help="Include Transfers, Credit Card Payment, Income, and Investments in forecast baselines",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    config = ForecastConfig(
        forecast_months=args.forecast_months,
        lookback_months=args.lookback_months,
        anomaly_threshold_ratio=args.anomaly_threshold_ratio,
        anomaly_threshold_abs=args.anomaly_threshold_abs,
        excluded_categories=() if args.include_transfer_spending else ForecastConfig.excluded_categories,
    )
    counts = run(db_path, config)
    print(f"Wrote baseline forecast tables to {db_path}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
