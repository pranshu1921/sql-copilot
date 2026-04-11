"""
agent.py

Self-correcting SQL agent: generates SQL, validates syntax, executes it,
and on failure feeds the error back into the prompt and retries.

Retry loop added in this commit. Plain English explanation added in commit 7.
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
{error_context}
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
    Self-correcting SQL agent. Retries up to MAX_RETRIES times,
    injecting the previous error back into the prompt each time.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection, hf_token: str) -> None:
        self.con = con
        self.client = InferenceClient(token=hf_token)
        self._schema_cache: str | None = None

    def run(self, question: str) -> AgentResult:
        """
        Run the agent with the self-correction retry loop.
        On each failure the error is injected into the next prompt.
        """
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
                logger.warning("Validation failed on attempt %d: %s", attempt, previous_error)
                continue

            try:
                df = self.con.execute(sql).fetchdf()
                result.result = df
                result.explanation = f"Query returned {len(df)} rows."
                result.success = True
                logger.info("Query succeeded on attempt %d, %d rows", attempt, len(df))
                return result

            except Exception as exc:
                previous_error = str(exc)
                result.error_history.append(previous_error)
                logger.warning("Execution failed on attempt %d: %s", attempt, previous_error)

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
        """Call SQLCoder. Injects previous error into the prompt if retrying."""
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
        response = self.client.text_generation(
            f"{SYSTEM_PROMPT}\n\n{prompt}",
            model=SQLCODER_MODEL,
            max_new_tokens=512,
            temperature=0.01,
            repetition_penalty=1.1,
            stop_sequences=["Question:", "\n\n\n"],
        )
        return self._extract_sql(response)

    def _validate_sql(self, sql: str) -> str | None:
        try:
            sqlglot.parse_one(sql, dialect="duckdb")
            return None
        except sqlglot.errors.ParseError as exc:
            return str(exc)

    def _get_schema(self) -> str:
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
        cleaned = raw_response.strip()
        fenced = re.search(r"```(?:sql)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
        if fenced:
            return fenced.group(1).strip()
        for keyword in ("SELECT", "WITH", "INSERT", "UPDATE", "DELETE"):
            idx = cleaned.upper().find(keyword)
            if idx != -1:
                return cleaned[idx:].strip()
        return cleaned