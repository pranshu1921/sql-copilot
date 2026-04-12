"""
db.py

Manages the DuckDB in-memory connection and auto-loads every CSV file
found in the data/ folder as a queryable DuckDB table.

Also loads relationship hints from:
  - data/relationships.txt  (explicit, preferred)
  - data/*.png / *.jpg      (ERD image, parsed via vision model if no txt exists)
"""
import logging
import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"


def _csv_to_table_name(filename: str) -> str:
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
        logger.info(
            "Loaded '%s': %d rows, %d columns", table_name, len(df), len(df.columns)
        )

    logger.info(
        "DuckDB ready: %d tables loaded from %s", len(csv_files), DATA_DIR
    )


def get_table_names(con: duckdb.DuckDBPyConnection) -> list[str]:
    result = con.execute("SHOW TABLES").fetchdf()
    return sorted(result["name"].tolist())


def get_table_schema(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame:
    return con.execute(f"DESCRIBE {table}").fetchdf()


def get_row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts = {}
    for table in get_table_names(con):
        counts[table] = con.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
    return counts


def get_relationships() -> str:
    """
    Load relationship hints from data/relationships.txt if it exists.
    Returns an empty string if no file is found.
    """
    rel_path = DATA_DIR / "relationships.txt"
    if rel_path.exists():
        content = rel_path.read_text(encoding="utf-8").strip()
        logger.info("Loaded relationships from %s", rel_path)
        return content

    # Check for ERD images
    erd_images = list(DATA_DIR.glob("*.png")) + list(DATA_DIR.glob("*.jpg"))
    if erd_images:
        logger.info(
            "ERD image found: %s. Use extract_relationships_from_erd() to parse it.",
            erd_images[0].name,
        )

    return ""


def extract_relationships_from_erd(image_path: Path, hf_token: str) -> str:
    """
    Use a vision-capable model to extract table relationships from an ERD image.
    Saves the result to data/relationships.txt for future use.
    """
    from huggingface_hub import InferenceClient
    import base64

    client = InferenceClient(token=hf_token)

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    ext = image_path.suffix.lower().replace(".", "")
    media_type = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"

    response = client.chat_completion(
        model="Qwen/Qwen2.5-VL-7B-Instruct",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_b64}"
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is an Entity Relationship Diagram. "
                            "List every relationship between tables in this format exactly:\n"
                            "table1.column = table2.column\n"
                            "One relationship per line. Nothing else."
                        ),
                    },
                ],
            }
        ],
        max_tokens=512,
    )

    relationships = (response.choices[0].message.content or "").strip()

    # Cache to file so we do not parse the image on every restart
    rel_path = DATA_DIR / "relationships.txt"
    rel_path.write_text(relationships, encoding="utf-8")
    logger.info("Relationships extracted from ERD and saved to %s", rel_path)

    return relationships