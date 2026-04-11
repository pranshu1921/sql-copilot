"""
agent.py

Basic SQL agent: generates a SQL query from a natural language question
using defog/sqlcoder-7b-2 via HF Inference API, validates syntax with
SQLGlot, and executes it against DuckDB.

This version makes one attempt only. The self-correction retry loop
is added in commit 6.
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

SQLCODER_MODEL = "defog/sqlcoder-7b-2"

SYSTEM_PROMPT = """You are SQLCoder, an expert SQL query generator.
Given a database schema and a natural language question, write a valid DuckDB SQL query.

Rules:
- Output ONLY the SQL query, nothing else.
- Do not include markdown code fences or any explanation.
- Use only the table and column names provided in the schema.
- Prefer simple, readable SQL over complex nested subqueries.
- For aggregations, always include a GROUP BY clause when needed.
- Use table aliases for clarity in joins.
"""

QUERY_PROMPT_TEMPLATE = """Database schema:
{schema}

Question: {question}

SQL query:"""


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
    Generates SQL from a natural language question and executes it.
    Single attempt only. Retry loop added in commit 6.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection, hf_token: str) -> None:
        self.con = con
        self.client = InferenceClient(token=hf_token)
        self._schema_cache: str | None = None

    def run(self, question: str) -> AgentResult:
        """Generate SQL, validate it, and execute it. One attempt only."""
        result = AgentResult(question=question)
        result.attempts = 1
        schema = self._get_schema()

        sql = self._generate_sql(question, schema)
        result.sql = sql

        validation_error = self._validate_sql(sql)
        if validation_error:
            result.error_message = f"Syntax error: {validation_error}"
            logger.warning("SQLGlot validation failed: %s", validation_error)
            return result

        try:
            df = self.con.execute(sql).fetchdf()
            result.result = df
            result.explanation = f"Query returned {len(df)} rows."
            result.success = True
            logger.info("Query succeeded, %d rows returned", len(df))
        except Exception as exc:
            result.error_message = str(exc)
            logger.error("Execution failed: %s", exc)

        return result

    def _generate_sql(self, question: str, schema: str) -> str:
        """Call SQLCoder to generate SQL from the question and schema."""
        prompt = f"{SYSTEM_PROMPT}\n\n{QUERY_PROMPT_TEMPLATE.format(schema=schema, question=question)}"
        response = self.client.text_generation(
            prompt,
            model=SQLCODER_MODEL,
            max_new_tokens=512,
            temperature=0.01,
            repetition_penalty=1.1,
            stop_sequences=["Question:", "\n\n\n"],
        )
        return self._extract_sql(response)

    def _validate_sql(self, sql: str) -> str | None:
        """Use SQLGlot to check syntax. Returns error string or None if valid."""
        try:
            sqlglot.parse_one(sql, dialect="duckdb")
            return None
        except sqlglot.errors.ParseError as exc:
            return str(exc)

    def _get_schema(self) -> str:
        """Build and cache the CREATE TABLE schema string for all loaded tables."""
        if self._schema_cache:
            return self._schema_cache
        lines = []
        for table in get_table_names(self.con):
            schema_df = get_table_schema(self.con, table)
            col_defs = ", ".join(
                f"{row['column_name']} {row['column_type']}"
                for _, row in schema_df.iterrows()
            )
            lines.append(f"CREATE TABLE {table} ({col_defs});")
        self._schema_cache = "\n".join(lines)
        return self._schema_cache

    @staticmethod
    def _extract_sql(raw_response: str) -> str:
        """Extract the SQL statement from the model response."""
        cleaned = raw_response.strip()
        fenced = re.search(r"```(?:sql)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
        if fenced:
            return fenced.group(1).strip()
        for keyword in ("SELECT", "WITH", "INSERT", "UPDATE", "DELETE"):
            idx = cleaned.upper().find(keyword)
            if idx != -1:
                return cleaned[idx:].strip()
        return cleaned