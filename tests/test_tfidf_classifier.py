from __future__ import annotations

import unittest

import pandas as pd

from ml.tfidf_category_classifier import evaluate_model


class TfidfEvaluationTests(unittest.TestCase):
    def test_evaluation_holds_out_merchant_groups(self) -> None:
        rows = []
        for category, prefix in (("Groceries", "market"), ("Transportation", "transit"), ("Dining", "cafe")):
            for merchant_number in range(8):
                merchant = f"{prefix} {merchant_number}"
                rows.append(
                    {
                        "merchant_name": merchant,
                        "raw_description": f"{merchant} purchase",
                        "transaction_type": "debit",
                        "category": category,
                    }
                )

        result = evaluate_model(pd.DataFrame(rows))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["split_strategy"], "merchant_group_holdout")
        self.assertFalse(result["group_leakage"])
        self.assertIn("macro_f1", result)
        self.assertIn("caveat", result)


if __name__ == "__main__":
    unittest.main()
