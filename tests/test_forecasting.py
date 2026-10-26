from __future__ import annotations

import unittest

import pandas as pd

from forecasting.baseline_forecasts import (
    ForecastConfig,
    build_category_forecast,
    build_monthly_forecast,
    build_spending_anomalies,
    complete_months,
)


class ForecastTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history = pd.DataFrame(
            [
                {"transaction_month": "2026-01", "category": "Groceries", "subcategory": "Supermarket", "spending_amount": 100.0},
                {"transaction_month": "2026-03", "category": "Groceries", "subcategory": "Supermarket", "spending_amount": 300.0},
            ]
        )
        self.config = ForecastConfig(lookback_months=3, forecast_months=1)

    def test_monthly_forecast_counts_missing_month_as_zero(self) -> None:
        forecast = build_monthly_forecast(self.history, self.config)
        self.assertAlmostEqual(forecast.iloc[0]["baseline_amount"], 400 / 3, places=2)
        self.assertEqual(forecast.iloc[0]["history_months_used"], 3)

    def test_category_forecast_counts_missing_month_as_zero(self) -> None:
        forecast = build_category_forecast(self.history, self.config)
        self.assertAlmostEqual(forecast.iloc[0]["baseline_amount"], 400 / 3, places=2)
        self.assertEqual(forecast.iloc[0]["history_months_used"], 3)

    def test_anomaly_baseline_uses_calendar_months(self) -> None:
        anomalies = build_spending_anomalies(self.history, self.config)
        self.assertEqual(len(anomalies), 1)
        self.assertAlmostEqual(anomalies.iloc[0]["baseline_amount"], 50.0, places=2)

    def test_month_completion_does_not_backfill_before_activity(self) -> None:
        result = complete_months(self.history, ["category", "subcategory"])
        self.assertEqual(result["transaction_month"].tolist(), ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(result.loc[result["transaction_month"] == "2026-02", "spending_amount"].iloc[0], 0.0)


if __name__ == "__main__":
    unittest.main()
