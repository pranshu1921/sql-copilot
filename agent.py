"""
agent.py

Self-correcting SQL agent with plain English result explanation.
Uses HF Inference API chat_completion for compatibility with
the new router.huggingface.co endpoint.

Schema context is built dynamically from loaded tables, explicit
relationships.txt, ERD image extraction, or inferred join keys.
Works with any CSV dataset — not just Olist.

Table aliases are automatically stripped from generated SQL using
SQLGlot AST transformation to prevent column resolution errors.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import duckdb
import pandas as pd
import sqlglot
from huggingface_hub import InferenceClient

from db import get_table_names, get_table_schema

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
SQLCODER_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"

SYSTEM_PROMPT = """You are an expert SQL query generator.
Given a database schema and a natural language question, write a valid DuckDB SQL query.

Rules:
- Output ONLY the SQL query, nothing else.
- Do not include markdown code fences, explanations, or comments.
- Use only the table and column names provided in the schema.
- Prefer simple, readable SQL over complex nested subqueries.
- For aggregations, always include a GROUP BY clause when needed.
- Always end the query with a semicolon.
- Never assume a column exists in a table unless it is listed in that table's schema.
- DO NOT use table aliases under any circumstances. Always use the full table name.
- Example of correct style: SELECT olist_orders_dataset.order_id FROM olist_orders_dataset
- Example of wrong style: SELECT o.order_id FROM olist_orders_dataset o
- To join tables, use the relationships listed in the schema comments as the join conditions.
- For column location, always check the column location guide in the schema comments.
"""

QUERY_PROMPT_TEMPLATE = """Database schema:
{schema}

Question: {question}
{error_context}
SQL query:"""

EXPLAIN_PROMPT_TEMPLATE = """The user asked: "{question}"
The following SQL query was run: {sql}
The result has {n_rows} rows.
First few rows: {sample}

Write one or two plain English sentences explaining what the result means.
Be direct and specific. Do not mention SQL."""


@dataclass
class AgentResult:
    """Structured output from a single agent run."""
    question: str
    sql: str = ""
    result: pd.DataFrame = field(default_factory=pd.DataFrame)
    explanation: str = ""
    attempts: int = 0
    error_history: list[str] = field(default_factory=list)
    success: bool = False
    error_message: str = ""


class SQLAgent:
    """
    Self-correcting SQL agent with plain English result explanation.
    Schema context is built dynamically — works with any CSV dataset.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection, hf_token: str) -> None:
        self.con = con
        self.hf_token = hf_token
        self.client = InferenceClient(token=hf_token)
        self._schema_cache: str | None = None

    def run(self, question: str) -> AgentResult:
        result = AgentResult(question=question)
        schema = self._get_schema()
        previous_error: str | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            result.attempts = attempt
            logger.info("Attempt %d/%d", attempt, MAX_RETRIES)

            sql = self._generate_sql(question, schema, previous_error)
            result.sql = sql

            validation_error = self._validate_sql(sql)
            if validation_error:
                previous_error = f"Syntax error: {validation_error}"
                result.error_history.append(previous_error)
                logger.warning(
                    "Validation failed on attempt %d: %s", attempt, previous_error
                )
                continue

            try:
                df = self.con.execute(sql).fetchdf()
                result.result = df
                result.explanation = self._generate_explanation(question, sql, df)
                result.success = True
                logger.info(
                    "Query succeeded on attempt %d, %d rows", attempt, len(df)
                )
                return result

            except Exception as exc:
                previous_error = str(exc)
                result.error_history.append(previous_error)
                logger.warning(
                    "Execution failed on attempt %d: %s", attempt, previous_error
                )

        result.error_message = (
            f"Failed after {MAX_RETRIES} attempts. Last error: {previous_error}"
        )
        return result

    def _generate_sql(
        self,
        question: str,
        schema: str,
        previous_error: str | None,
    ) -> str:
        error_context = ""
        if previous_error:
            error_context = (
                f"\nThe previous attempt failed with: {previous_error}\n"
                f"Please fix the query.\n"
            )

        prompt = QUERY_PROMPT_TEMPLATE.format(
            schema=schema,
            question=question,
            error_context=error_context,
        )

        response = self.client.chat_completion(
            model=SQLCODER_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=512,
            temperature=0.01,
        )
        raw = response.choices[0].message.content or ""
        sql = self._extract_sql(raw)
        sql = self._strip_aliases(sql)
        sql = self._fix_column_tables(sql)
        return sql

    def _generate_explanation(
        self,
        question: str,
        sql: str,
        df: pd.DataFrame,
    ) -> str:
        sample = df.head(3).to_dict(orient="records")
        prompt = EXPLAIN_PROMPT_TEMPLATE.format(
            question=question,
            sql=sql,
            n_rows=len(df),
            sample=sample,
        )
        try:
            response = self.client.chat_completion(
                model=SQLCODER_MODEL,
                messages=[
                    {"role": "user", "content": prompt},
                ],
                max_tokens=150,
                temperature=0.3,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("Explanation generation failed: %s", exc)
            return f"Query returned {len(df)} rows."

    def _validate_sql(self, sql: str) -> str | None:
        try:
            sqlglot.parse_one(sql, dialect="duckdb")
            return None
        except sqlglot.errors.ParseError as exc:
            return str(exc)

    @staticmethod
    def _strip_aliases(sql: str) -> str:
        """
        Rewrite SQL to replace all table aliases with full table names.
        Uses SQLGlot AST transformation so it works on any query structure.
        """
        try:
            expression = sqlglot.parse_one(sql, dialect="duckdb")

            # Build alias -> full table name map from FROM and JOIN clauses
            alias_map: dict[str, str] = {}
            for table in expression.find_all(sqlglot.exp.Table):
                if table.alias:
                    alias_map[table.alias] = table.name
                    table.set("alias", None)

            if not alias_map:
                return sql

            # Replace all column references that use an alias
            for column in expression.find_all(sqlglot.exp.Column):
                if column.table and column.table in alias_map:
                    column.set(
                        "table",
                        sqlglot.exp.Identifier(this=alias_map[column.table]),
                    )

            # Replace alias references in GROUP BY, ORDER BY, WHERE
            for node in expression.find_all(sqlglot.exp.Alias):
                if isinstance(node.this, sqlglot.exp.Column):
                    col = node.this
                    if col.table and col.table in alias_map:
                        col.set(
                            "table",
                            sqlglot.exp.Identifier(this=alias_map[col.table]),
                        )

            rewritten = expression.sql(dialect="duckdb")
            logger.info(
                "Alias rewrite: %s -> %s",
                list(alias_map.keys()),
                list(alias_map.values()),
            )
            return rewritten

        except Exception as exc:
            logger.warning(
                "Alias stripping failed, using original SQL: %s", exc
            )
            return sql

    def _fix_column_tables(self, sql: str) -> str:
        """
        Post-processing pass: for every table.column reference in the SQL,
        verify the column actually exists in that table. If it does not,
        find the correct table and rewrite the reference.
        """
        try:
            col_to_tables: dict[str, list[str]] = {}
            table_cols: dict[str, set[str]] = {}

            for table in get_table_names(self.con):
                schema_df = get_table_schema(self.con, table)
                cols = set(schema_df["column_name"].tolist())
                table_cols[table] = cols
                for col in cols:
                    col_to_tables.setdefault(col, []).append(table)

            expression = sqlglot.parse_one(sql, dialect="duckdb")

            for column in expression.find_all(sqlglot.exp.Column):
                tbl = column.table
                col = column.name
                if not tbl or not col:
                    continue
                if tbl in table_cols and col not in table_cols[tbl]:
                    correct_tables = col_to_tables.get(col, [])
                    if correct_tables:
                        correct_table = correct_tables[0]
                        logger.info(
                            "Column fix: %s.%s -> %s.%s",
                            tbl, col, correct_table, col,
                        )
                        column.set(
                            "table",
                            sqlglot.exp.Identifier(this=correct_table),
                        )

            return expression.sql(dialect="duckdb")

        except Exception as exc:
            logger.warning("Column table fix failed, using original: %s", exc)
            return sql

    def _get_schema(self) -> str:
        if self._schema_cache:
            return self._schema_cache

        from db import DATA_DIR, get_relationships, extract_relationships_from_erd

        tables = get_table_names(self.con)

        # Build column to tables map
        column_to_tables: dict[str, list[str]] = {}
        for table in tables:
            schema_df = get_table_schema(self.con, table)
            for col in schema_df["column_name"].tolist():
                column_to_tables.setdefault(col, []).append(table)

        # Build CREATE TABLE blocks with one sample row each
        lines = []
        for table in tables:
            schema_df = get_table_schema(self.con, table)
            col_defs = ", ".join(
                f"{row['column_name']} {row['column_type']}"
                for _, row in schema_df.iterrows()
            )
            lines.append(f"CREATE TABLE {table} ({col_defs});")
            try:
                sample = self.con.execute(
                    f"SELECT * FROM {table} LIMIT 1"
                ).fetchdf()
                if not sample.empty:
                    trimmed = {
                        k: (str(v)[:50] if len(str(v)) > 50 else v)
                        for k, v in sample.iloc[0].to_dict().items()
                    }
                    lines.append(f"-- Sample row: {trimmed}")
            except Exception:
                pass

        # Unique column location guide
        unique_cols: dict[str, str] = {
            col: tbls[0]
            for col, tbls in column_to_tables.items()
            if len(tbls) == 1
        }
        if unique_cols:
            lines.append(
                "\n-- Column location guide (these columns exist in ONE table only):"
            )
            for col, table in sorted(unique_cols.items()):
                lines.append(f"-- {col} -> ONLY in {table}")

        # Priority 1: explicit relationships.txt
        explicit = get_relationships()
        if explicit:
            lines.append("\n-- Table relationships:")
            for rel_line in explicit.splitlines():
                lines.append(f"-- {rel_line}")

        else:
            # Priority 2: ERD image
            erd_images = (
                list(DATA_DIR.glob("*.png")) + list(DATA_DIR.glob("*.jpg"))
            )
            if erd_images:
                try:
                    extracted = extract_relationships_from_erd(
                        erd_images[0], self.hf_token
                    )
                    lines.append(
                        "\n-- Table relationships (extracted from ERD):"
                    )
                    for rel_line in extracted.splitlines():
                        lines.append(f"-- {rel_line}")
                except Exception as exc:
                    logger.warning("ERD extraction failed: %s", exc)

            # Priority 3: infer from shared column names
            shared_cols = {
                col: tbls
                for col, tbls in column_to_tables.items()
                if len(tbls) > 1
            }
            if shared_cols:
                lines.append(
                    "\n-- Inferred join keys (column appears in multiple tables):"
                )
                for col, tbls in sorted(shared_cols.items()):
                    lines.append(f"-- {col}: {', '.join(tbls)}")

        self._schema_cache = "\n".join(lines)
        return self._schema_cache

    @staticmethod
    def _extract_sql(raw_response: str) -> str:
        cleaned = raw_response.strip()
        fenced = re.search(
            r"```(?:sql)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE
        )
        if fenced:
            return fenced.group(1).strip()
        for keyword in ("SELECT", "WITH", "INSERT", "UPDATE", "DELETE"):
            idx = cleaned.upper().find(keyword)
            if idx != -1:
                return cleaned[idx:].strip()
        return cleaned