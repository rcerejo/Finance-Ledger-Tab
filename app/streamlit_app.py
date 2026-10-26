from __future__ import annotations

import sqlite3
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from forecasting.baseline_forecasts import ForecastConfig, run as run_forecasts
from ingestion.statement_parser import ingest_statements
from ingestion.upload_workflow import (
    MAX_UPLOAD_BYTES,
    UploadPreview,
    existing_statement_hashes,
    parse_uploaded_statement,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "outputs" / "finance_raw.sqlite"
MAX_TOTAL_UPLOAD_BYTES = 50 * 1024 * 1024


st.set_page_config(
    page_title="Finance Ledger Lab",
    page_icon="",
    layout="wide",
)


@st.cache_data(show_spinner=False)
def read_table(db_path: str, table_name: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(f"select * from {table_name}", conn)


def currency(value: float) -> str:
    return f"${value:,.2f}"


def load_data(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    db = str(db_path)
    transactions = read_table(db, "fct_transactions")
    monthly = read_table(db, "mart_monthly_spending")
    category = read_table(db, "mart_category_trends")
    merchant = read_table(db, "mart_merchant_spending")

    if not transactions.empty:
        transactions["transaction_date"] = pd.to_datetime(transactions["transaction_date"], errors="coerce")
        transactions["amount"] = pd.to_numeric(transactions["amount"], errors="coerce")
    for frame in (monthly, category, merchant):
        for column in ("total_debits", "total_credits", "net_amount", "spending_amount"):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return transactions, monthly, category, merchant


def load_optional_table(db_path: Path, table_name: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute(
            "select 1 from sqlite_master where type in ('table', 'view') and name = ?",
            (table_name,),
        ).fetchone()
        if not exists:
            return pd.DataFrame()
        return pd.read_sql_query(f"select * from {table_name}", conn)


def load_forecast_tables(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    monthly_forecast = load_optional_table(db_path, "forecast_monthly_spending")
    category_forecast = load_optional_table(db_path, "forecast_category_spending")
    anomalies = load_optional_table(db_path, "spending_anomalies")

    for frame in (monthly_forecast, category_forecast, anomalies):
        for column in (
            "forecast_amount",
            "baseline_amount",
            "trend_adjustment",
            "actual_amount",
            "variance_amount",
            "variance_ratio",
        ):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return monthly_forecast, category_forecast, anomalies


def apply_filters(df: pd.DataFrame, months: list[str], categories: list[str], accounts: list[str]) -> pd.DataFrame:
    filtered = df.copy()
    if months:
        filtered = filtered[filtered["transaction_month"].isin(months)]
    if categories:
        filtered = filtered[filtered["category"].isin(categories)]
    if accounts:
        filtered = filtered[filtered["account_id"].isin(accounts)]
    return filtered


def rebuild_analytics(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["FINANCE_DB_PATH"] = str(db_path.resolve())
    environment["FINANCE_DB_DIR"] = str(db_path.parent.resolve())

    with tempfile.TemporaryDirectory(prefix="finance-ledger-dbt-profile-") as directory:
        profiles_dir = Path(directory)
        shutil.copyfile(ROOT / "finance_dbt" / "profiles.example.yml", profiles_dir / "profiles.yml")
        for command in ("seed", "run", "test"):
            result = subprocess.run(
                [
                    "dbt",
                    command,
                    "--project-dir",
                    str(ROOT / "finance_dbt"),
                    "--profiles-dir",
                    str(profiles_dir),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if result.returncode:
                detail = (result.stdout + "\n" + result.stderr).strip()
                raise RuntimeError(f"dbt {command} failed:\n{detail[-6000:]}")

    run_forecasts(db_path, ForecastConfig())


def render_statement_uploader(db_path: Path) -> None:
    with st.expander("Upload statements", expanded=True):
        st.caption(
            "Temporary files used during parsing are deleted. Uploaded data remains in this Streamlit session "
            "until it expires. Review extracted transactions before importing. "
            "Maximum 25 MB per file, 50 MB total."
        )
        files = st.file_uploader(
            "Choose bank statements",
            type=["pdf", "csv"],
            accept_multiple_files=True,
            key="statement_uploads",
        )
        use_ai = st.checkbox(
            "Try local Ollama for unfamiliar text-based PDFs",
            value=False,
            help="Uses the Ollama service configured on the machine running this app. Scanned PDFs still need OCR.",
        )
        ai_model = st.text_input("Ollama model", "llama3.1:8b", disabled=not use_ai)

        if files and sum(file.size for file in files) > MAX_TOTAL_UPLOAD_BYTES:
            st.error("The selected files exceed the 50 MB total upload limit.")
            return

        if st.button("Parse statements for review", disabled=not files, type="primary"):
            previews: list[UploadPreview | dict[str, str]] = []
            for file in files or []:
                try:
                    if file.size > MAX_UPLOAD_BYTES:
                        raise ValueError("Each statement must be 25 MB or smaller.")
                    previews.append(
                        parse_uploaded_statement(
                            file.name,
                            file.getvalue(),
                            ai_fallback=use_ai,
                            ai_model=ai_model.strip() or "llama3.1:8b",
                        )
                    )
                except Exception as exc:
                    previews.append({"filename": Path(file.name).name, "error": str(exc)})
            st.session_state["statement_upload_previews"] = previews
            st.session_state.pop("statement_upload_message", None)

        previews = st.session_state.get("statement_upload_previews", [])
        if not previews:
            return

        known_hashes = existing_statement_hashes(db_path)
        importable: list[UploadPreview] = []
        needs_warning_acknowledgement = False

        for item in previews:
            if isinstance(item, dict):
                st.error(f"{item['filename']}: {item['error']}")
                continue

            statement = item.statement
            duplicate = statement.file_hash in known_hashes
            status_label = "Already imported" if duplicate else item.status.title()
            with st.expander(f"{item.filename} · {status_label}", expanded=not duplicate):
                st.write(
                    f"**Bank:** {statement.bank_name or 'Unknown'}  "
                    f"**Account:** {statement.account_id or 'Unknown'}  "
                    f"**Transactions:** {len(statement.rows)}"
                )
                if item.status == "failed":
                    st.error("No usable transactions were extracted. This statement will not be imported.")
                elif item.status == "warnings":
                    needs_warning_acknowledgement = True
                    st.warning("Review the validation messages before importing this statement.")
                else:
                    st.success("Validation passed.")

                issues = [line for line in item.validation if line.startswith(("ERROR:", "Row "))]
                if issues:
                    st.code("\n".join(issues), language=None)
                if item.preview_rows:
                    st.dataframe(pd.DataFrame(item.preview_rows), use_container_width=True, hide_index=True)

            if not duplicate and item.status != "failed":
                importable.append(item)

        if not importable:
            st.info("No new valid statements are ready to import.")
            return

        acknowledge_warnings = True
        if needs_warning_acknowledgement:
            acknowledge_warnings = st.checkbox(
                "I reviewed the warnings and want to import statements with warnings.",
                key="acknowledge_upload_warnings",
            )

        if st.button("Import reviewed statements", disabled=not acknowledge_warnings):
            counts = ingest_statements(
                db_path,
                [item.statement for item in importable],
                pd.Timestamp.now().isoformat(timespec="seconds"),
            )
            st.session_state.pop("statement_upload_previews", None)
            st.session_state.pop("acknowledge_upload_warnings", None)
            st.session_state["statement_upload_message"] = f"Statement import results: {dict(counts)}"
            try:
                with st.spinner("Building dbt models and refreshing forecasts..."):
                    rebuild_analytics(db_path)
                st.session_state["statement_upload_message"] += " Analytics and forecasts refreshed."
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.session_state.pop("statement_upload_message", None)
                st.info(f"Raw import completed: {dict(counts)}")
                st.error(
                    "Statements were imported into the raw database, but the analytics refresh failed. "
                    "Use Rebuild analytics in the sidebar after fixing the issue."
                )
                st.exception(exc)

        message = st.session_state.pop("statement_upload_message", None)
        if message:
            st.success(message)


def main() -> None:
    st.title("Finance Ledger Lab")

    with st.sidebar:
        st.header("Data Source")
        db_path = Path(st.text_input("SQLite database", str(DEFAULT_DB)))
        refresh = st.button("Refresh dashboard", use_container_width=True)
        if refresh:
            st.cache_data.clear()
        if st.button("Rebuild analytics", disabled=not db_path.exists(), use_container_width=True):
            try:
                with st.spinner("Running dbt and refreshing forecasts..."):
                    rebuild_analytics(db_path)
                st.cache_data.clear()
                st.session_state["analytics_refresh_message"] = "Analytics and forecasts refreshed."
                st.rerun()
            except Exception as exc:
                st.error("Analytics refresh failed.")
                st.exception(exc)

    analytics_message = st.session_state.pop("analytics_refresh_message", None)
    if analytics_message:
        st.success(analytics_message)

    render_statement_uploader(db_path)

    if not db_path.exists():
        st.info("Upload a statement above to create your local database and start exploring your finances.")
        return

    transactions, monthly, category, merchant = load_data(db_path)
    if transactions.empty:
        st.warning("No transformed transactions found. Run dbt first.")
        return

    months_all = sorted(transactions["transaction_month"].dropna().unique().tolist())
    categories_all = sorted(transactions["category"].fillna("Uncategorized").unique().tolist())
    accounts_all = sorted(transactions["account_id"].dropna().unique().tolist())

    with st.sidebar:
        st.header("Filters")
        selected_months = st.multiselect("Months", months_all, default=months_all)
        selected_categories = st.multiselect("Categories", categories_all)
        selected_accounts = st.multiselect("Accounts", accounts_all)
        search = st.text_input("Search transactions")

    filtered = apply_filters(transactions, selected_months, selected_categories, selected_accounts)
    if search:
        needle = search.lower()
        filtered = filtered[
            filtered["raw_description"].fillna("").str.lower().str.contains(needle)
            | filtered["merchant_name"].fillna("").str.lower().str.contains(needle)
            | filtered["category"].fillna("").str.lower().str.contains(needle)
        ]

    spending = float(filtered.loc[filtered["amount"] < 0, "amount"].abs().sum())
    income = float(filtered.loc[filtered["amount"] > 0, "amount"].sum())
    net = float(filtered["amount"].sum())
    uncategorized = int((filtered["category"] == "Uncategorized").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Spending", currency(spending))
    c2.metric("Income", currency(income))
    c3.metric("Net", currency(net))
    c4.metric("Uncategorized", f"{uncategorized:,}")

    monthly_f = monthly[monthly["transaction_month"].isin(selected_months)] if selected_months else monthly
    category_f = category[category["transaction_month"].isin(selected_months)] if selected_months else category
    merchant_f = merchant[merchant["transaction_month"].isin(selected_months)] if selected_months else merchant

    if selected_categories:
        category_f = category_f[category_f["category"].isin(selected_categories)]
        merchant_f = merchant_f[merchant_f["category"].isin(selected_categories)]

    tab_monthly, tab_category, tab_merchant, tab_forecast, tab_suggestions, tab_transactions = st.tabs(
        [
            "Monthly Spending",
            "Category Trends",
            "Merchant Insights",
            "Forecasting",
            "ML Suggestions",
            "Transactions",
        ]
    )

    with tab_monthly:
        left, right = st.columns([2, 1])
        monthly_chart = monthly_f.sort_values("transaction_month").copy()
        monthly_chart["spending_amount"] = monthly_chart["total_debits"].abs()
        with left:
            st.subheader("Monthly Spending")
            fig = px.bar(
                monthly_chart,
                x="transaction_month",
                y="spending_amount",
                text_auto=".2s",
                labels={"transaction_month": "Month", "spending_amount": "Spending"},
            )
            fig.update_layout(showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        with right:
            st.subheader("Monthly Summary")
            st.dataframe(
                monthly_chart[
                    ["transaction_month", "transaction_count", "total_debits", "total_credits", "net_amount"]
                ],
                use_container_width=True,
                hide_index=True,
            )

    with tab_category:
        st.subheader("Category Trends")
        category_rollup = (
            category_f.groupby(["transaction_month", "category"], as_index=False)["spending_amount"].sum()
            if not category_f.empty
            else category_f
        )
        fig = px.bar(
            category_rollup,
            x="transaction_month",
            y="spending_amount",
            color="category",
            labels={"transaction_month": "Month", "spending_amount": "Spending"},
        )
        st.plotly_chart(fig, use_container_width=True)
        category_totals = (
            category_f.groupby(["category", "subcategory"], as_index=False)
            .agg(transaction_count=("transaction_count", "sum"), spending_amount=("spending_amount", "sum"))
            .sort_values("spending_amount", ascending=False)
        )
        st.dataframe(category_totals, use_container_width=True, hide_index=True)

    with tab_merchant:
        st.subheader("Top Merchants")
        merchant_totals = (
            merchant_f.groupby(["merchant_group", "category"], as_index=False)
            .agg(transaction_count=("transaction_count", "sum"), spending_amount=("spending_amount", "sum"))
            .sort_values("spending_amount", ascending=False)
        )
        top_merchants = merchant_totals.head(20)
        fig = px.bar(
            top_merchants.sort_values("spending_amount"),
            x="spending_amount",
            y="merchant_group",
            color="category",
            orientation="h",
            labels={"spending_amount": "Spending", "merchant_group": "Merchant"},
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(merchant_totals, use_container_width=True, hide_index=True)

    with tab_forecast:
        st.subheader("Baseline Forecast")
        monthly_forecast, category_forecast, anomalies = load_forecast_tables(db_path)
        if monthly_forecast.empty:
            st.info("No forecast tables found. Run forecasting/baseline_forecasts.py to generate them.")
        else:
            next_month = monthly_forecast.sort_values("forecast_month").iloc[0]
            f1, f2, f3 = st.columns(3)
            f1.metric("Next Month Forecast", currency(float(next_month["forecast_amount"])))
            f2.metric("Baseline", currency(float(next_month["baseline_amount"])))
            f3.metric("Confidence", str(next_month["confidence"]).replace("_", " ").title())

            forecast_chart = monthly_forecast.sort_values("forecast_month")
            fig = px.bar(
                forecast_chart,
                x="forecast_month",
                y="forecast_amount",
                color="confidence",
                text_auto=".2s",
                labels={"forecast_month": "Forecast Month", "forecast_amount": "Forecast Spend"},
            )
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                forecast_chart[
                    [
                        "forecast_month",
                        "forecast_amount",
                        "baseline_amount",
                        "trend_adjustment",
                        "history_months_used",
                        "confidence",
                        "excluded_categories",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        if not category_forecast.empty:
            st.subheader("Category Forecast")
            selected_forecast_month = st.selectbox(
                "Forecast month",
                sorted(category_forecast["forecast_month"].dropna().unique().tolist()),
            )
            category_month = category_forecast[
                category_forecast["forecast_month"] == selected_forecast_month
            ].sort_values("forecast_amount", ascending=False)
            fig = px.bar(
                category_month.head(15).sort_values("forecast_amount"),
                x="forecast_amount",
                y="category",
                color="confidence",
                orientation="h",
                labels={"forecast_amount": "Forecast Spend", "category": "Category"},
            )
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                category_month[
                    [
                        "forecast_month",
                        "category",
                        "subcategory",
                        "forecast_amount",
                        "baseline_amount",
                        "trend_adjustment",
                        "history_months_used",
                        "confidence",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        st.subheader("Spending Anomalies")
        if anomalies.empty:
            st.success("No current anomalies found with the active baseline thresholds.")
        else:
            st.dataframe(
                anomalies.sort_values(["severity", "variance_ratio"], ascending=[True, False]),
                use_container_width=True,
                hide_index=True,
            )

    with tab_suggestions:
        tfidf_tab, ai_tab = st.tabs(["TF-IDF", "AI Suggestions"])

        with tfidf_tab:
            st.subheader("TF-IDF Category Suggestions")
            suggestions = load_optional_table(db_path, "ml_category_suggestions")
            if suggestions.empty:
                st.info("No TF-IDF suggestions found. Run ml/tfidf_category_classifier.py to generate them.")
            else:
                suggestions["suggestion_confidence"] = pd.to_numeric(
                    suggestions["suggestion_confidence"], errors="coerce"
                )
                st.metric("Suggestions", f"{len(suggestions):,}")
                fig = px.histogram(
                    suggestions,
                    x="suggestion_confidence",
                    color="suggested_category",
                    nbins=10,
                    labels={"suggestion_confidence": "Confidence"},
                )
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(
                    suggestions.sort_values("suggestion_confidence", ascending=False),
                    use_container_width=True,
                    hide_index=True,
                )

        with ai_tab:
            st.subheader("AI Category Suggestions")
            ai_suggestions = load_optional_table(db_path, "ai_category_suggestions")
            if ai_suggestions.empty:
                st.info("No AI suggestions found. Run ml/ai_category_suggester.py with Ollama running.")
            else:
                ai_suggestions["suggestion_confidence"] = pd.to_numeric(
                    ai_suggestions["suggestion_confidence"], errors="coerce"
                )
                a1, a2, a3 = st.columns(3)
                a1.metric("Suggestions", f"{len(ai_suggestions):,}")
                a2.metric(
                    "Review Ready",
                    f"{int((ai_suggestions['suggestion_status'] == 'review').sum()):,}",
                )
                a3.metric(
                    "Avg Confidence",
                    f"{float(ai_suggestions['suggestion_confidence'].mean()):.2f}",
                )
                fig = px.histogram(
                    ai_suggestions,
                    x="suggestion_confidence",
                    color="suggested_category",
                    nbins=10,
                    labels={"suggestion_confidence": "Confidence"},
                )
                st.plotly_chart(fig, use_container_width=True)
                display_columns = [
                    "transaction_date",
                    "bank_name",
                    "merchant_name",
                    "raw_description",
                    "amount",
                    "suggested_merchant_name",
                    "suggested_category",
                    "suggested_subcategory",
                    "suggestion_confidence",
                    "suggestion_status",
                    "suggestion_reason",
                    "model_name",
                ]
                st.dataframe(
                    ai_suggestions.sort_values("suggestion_confidence", ascending=False)[display_columns],
                    use_container_width=True,
                    hide_index=True,
                )

    with tab_transactions:
        st.subheader("Search / Filter Transactions")
        display = filtered.sort_values("transaction_date", ascending=False)[
            [
                "transaction_date",
                "bank_name",
                "account_id",
                "merchant_name",
                "category",
                "subcategory",
                "amount",
                "raw_description",
                "source_file",
            ]
        ]
        st.dataframe(display, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
