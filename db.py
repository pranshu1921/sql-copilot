"""
db.py

Manages the DuckDB in-memory connection and loads the Olist e-commerce
dataset from Hugging Face Datasets Hub into queryable tables.
"""
import logging

import duckdb
import pandas as pd
import streamlit as st
from datasets import load_dataset

logger = logging.getLogger(__name__)

OLIST_HF_DATASETS = {
    "orders":            ("easonnie/olist_orders_dataset",              "train"),
    "order_items":       ("easonnie/olist_order_items_dataset",         "train"),
    "order_payments":    ("easonnie/olist_order_payments_dataset",      "train"),
    "order_reviews":     ("easonnie/olist_order_reviews_dataset",       "train"),
    "customers":         ("easonnie/olist_customers_dataset",           "train"),
    "products":          ("easonnie/olist_products_dataset",            "train"),
    "sellers":           ("easonnie/olist_sellers_dataset",             "train"),
    "geolocation":       ("easonnie/olist_geolocation_dataset",         "train"),
    "product_category":  ("easonnie/product_category_name_translation", "train"),
}


@st.cache_resource(show_spinner="Loading Olist dataset into DuckDB...")
def get_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    _load_all_tables(con)
    logger.info("DuckDB connection ready with %d tables", len(OLIST_HF_DATASETS))
    return con


def _load_all_tables(con: duckdb.DuckDBPyConnection) -> None:
    for table_name, (hf_path, split) in OLIST_HF_DATASETS.items():
        df = _load_from_hub(hf_path, split)
        con.register(table_name, df)
        logger.info("Loaded table '%s': %d rows", table_name, len(df))


def _load_from_hub(hf_path: str, split: str) -> pd.DataFrame:
    dataset = load_dataset(hf_path, split=split, trust_remote_code=False)
    return dataset.to_pandas()


def get_table_names(con: duckdb.DuckDBPyConnection) -> list[str]:
    result = con.execute("SHOW TABLES").fetchdf()
    return sorted(result["name"].tolist())


def get_table_schema(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame:
    return con.execute(f"DESCRIBE {table}").fetchdf()


def get_row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts = {}
    for table in get_table_names(con):
        counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return counts