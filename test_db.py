"""
test_db.py

Standalone smoke test for the DuckDB data layer.
Run with: python test_db.py
"""
import sys
import time
import duckdb
from db import DATA_DIR, _load_all_tables


def main():
    print(f"Scanning for CSV files in: {DATA_DIR}\n")

    con = duckdb.connect(":memory:")
    start = time.time()

    try:
        _load_all_tables(con)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    tables = con.execute("SHOW TABLES").fetchdf()["name"].tolist()

    for table in sorted(tables):
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        cols = con.execute(f"DESCRIBE {table}").fetchdf().shape[0]
        print(f"  OK  {table:<30} {count:>9,} rows   {cols} columns")

    print(f"\n{len(tables)} tables loaded in {time.time() - start:.1f}s")
    print("\nRunning join query test...")

    result = con.execute("""
        SELECT
            p.product_category_name                        AS category,
            ROUND(AVG(CAST(r.review_score AS DOUBLE)), 2)  AS avg_score,
            COUNT(DISTINCT oi.order_id)                    AS total_orders
        FROM olist_order_items_dataset    oi
        JOIN olist_products_dataset       p  ON oi.product_id = p.product_id
        JOIN olist_order_reviews_dataset  r  ON oi.order_id   = r.order_id
        WHERE p.product_category_name IS NOT NULL
        GROUP BY p.product_category_name
        ORDER BY avg_score DESC
        LIMIT 5
    """).fetchdf()

    print("\nTop 5 categories by review score:")
    print(result.to_string(index=False))
    print("\nData layer working. Proceed to commit 5.")


if __name__ == "__main__":
    main()