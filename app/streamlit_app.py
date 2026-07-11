from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "outputs" / "finance_raw_final.sqlite"


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


def apply_filters(df: pd.DataFrame, months: list[str], categories: list[str], accounts: list[str]) -> pd.DataFrame:
    filtered = df.copy()
    if months:
        filtered = filtered[filtered["transaction_month"].isin(months)]
    if categories:
        filtered = filtered[filtered["category"].isin(categories)]
    if accounts:
        filtered = filtered[filtered["account_id"].isin(accounts)]
    return filtered


def main() -> None:
    st.title("Finance Ledger Lab")

    with st.sidebar:
        st.header("Data Source")
        db_path = Path(st.text_input("SQLite database", str(DEFAULT_DB)))
        refresh = st.button("Refresh data", use_container_width=True)
        if refresh:
            st.cache_data.clear()

    if not db_path.exists():
        st.error(f"Database not found: {db_path}")
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

    tab_monthly, tab_category, tab_merchant, tab_suggestions, tab_transactions = st.tabs(
        ["Monthly Spending", "Category Trends", "Merchant Insights", "ML Suggestions", "Transactions"]
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

    with tab_suggestions:
        st.subheader("TF-IDF Category Suggestions")
        suggestions = load_optional_table(db_path, "ml_category_suggestions")
        if suggestions.empty:
            st.info("No ML suggestions found. Run ml/tfidf_category_classifier.py to generate them.")
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
