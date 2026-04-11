"""
db.py

Manages the DuckDB in-memory connection and auto-loads every CSV file
found in the data/ folder as a queryable DuckDB table.

Table names are derived from filenames — spaces and hyphens replaced
with underscores, .csv extension removed. For example:
    olist_orders_dataset.csv  →  table: olist_orders_dataset
    my sales data.csv         →  table: my_sales_data

To use your own data: drop any CSV files into the data/ folder and
restart the app. No code changes required.
"""
import logging
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"


def _csv_to_table_name(filename: str) -> str:
    """Convert a CSV filename to a clean SQL table name."""
    return filename.replace(".csv", "").replace(" ", "_").replace("-", "_")


@st.cache_resource(show_spinner="Loading CSV files into DuckDB...")
def get_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    _load_all_tables(con)
    return con


def _load_all_tables(con: duckdb.DuckDBPyConnection) -> None:
    csv_files = sorted(DATA_DIR.glob("*.csv"))

    if not csv_files:
        raise FileNotFoundError(
            f"No CSV files found in {DATA_DIR}.\n"
            f"Add at least one CSV file to the data/ folder and restart."
        )

    for csv_path in csv_files:
        table_name = _csv_to_table_name(csv_path.name)
        df = pd.read_csv(csv_path)
        con.register(table_name, df)
        logger.info("Loaded '%s': %d rows, %d columns", table_name, len(df), len(df.columns))

    logger.info("DuckDB ready: %d tables loaded from %s", len(csv_files), DATA_DIR)


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