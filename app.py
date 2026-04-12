"""
app.py

Final Streamlit interface for the SQL co-pilot.
Full chat history, sidebar, schema explorer, example questions,
retry log, query counter, and clear conversation.
"""
import os

import streamlit as st
from dotenv import load_dotenv

from agent import SQLAgent
from db import get_connection, get_row_counts, get_table_names, get_table_schema

load_dotenv()

st.set_page_config(
    page_title="SQL Co-pilot",
    page_icon="🗄",
    layout="wide",
)

EXAMPLE_QUESTIONS = [
    "How many orders are in the dataset?",
    "What are the different order statuses and how many orders are in each?",
    "Which cities have the most customers?",
    "What are the top 10 sellers by number of orders fulfilled?",
    "What are the top 10 product categories by total revenue?",
    "Which product categories have the highest average review score?",
    "What is the monthly order volume trend across 2017 and 2018?",
    "What percentage of orders were delivered late?",
    "Which sellers have the highest average review score with at least 50 orders?",
    "What payment methods are most commonly used?",
]


def get_hf_token() -> str:
    if os.getenv("SPACE_ID"):
        return st.secrets.get("HF_TOKEN", "")
    token = os.getenv("HF_TOKEN", "")
    if not token:
        st.error(
            "HF_TOKEN not found. "
            "Add it to your .env file for local development."
        )
        st.stop()
    return token


def render_sidebar(con) -> None:
    with st.sidebar:
        st.markdown("### How it works")
        st.markdown(
            "Type any business question in plain English. "
            "The agent writes the SQL, validates it, runs it against "
            "the loaded dataset, and explains the result. "
            "If the query fails, it automatically retries up to 3 times."
        )

        st.divider()

        query_count = len([
            m for m in st.session_state.get("messages", [])
            if m["role"] == "user"
        ])
        st.metric("Queries this session", query_count)

        if st.button("Clear conversation", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        st.divider()
        st.markdown("### Example questions")
        st.caption("Click any question to run it directly.")
        for q in EXAMPLE_QUESTIONS:
            if st.button(q, use_container_width=True, key=f"ex_{q[:30]}"):
                st.session_state.pending_question = q

        st.divider()
        st.markdown("### Schema explorer")
        st.caption("Tables currently loaded from the data/ folder.")
        tables = get_table_names(con)
        counts = get_row_counts(con)
        selected = st.selectbox("Select a table to inspect", tables)
        if selected:
            st.caption(f"{counts.get(selected, 0):,} rows")
            schema_df = get_table_schema(con, selected)
            st.dataframe(
                schema_df[["column_name", "column_type"]],
                use_container_width=True,
                hide_index=True,
            )

        st.divider()
        st.markdown("### Using your own data")
        st.caption(
            "Drop any CSV files into the data/ folder and restart the app. "
            "Each CSV becomes a queryable table automatically. "
            "Add a relationships.txt file to define join keys, "
            "or drop an ERD image and the app extracts them for you."
        )


def render_home_context(con) -> None:
    tables = get_table_names(con)
    counts = get_row_counts(con)
    total_rows = sum(counts.values())

    st.markdown("### What you can query")
    st.markdown(
        "This tool lets you ask questions about the loaded dataset in plain English. "
        "It translates your question into SQL, runs it instantly, and explains the result. "
        "No SQL knowledge needed."
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Tables loaded", len(tables))
    with col2:
        st.metric("Total rows", f"{total_rows:,}")
    with col3:
        st.metric("Max retries", "3")

    st.markdown("### Loaded tables")
    cols = st.columns(3)
    for i, table in enumerate(tables):
        with cols[i % 3]:
            st.markdown(
                f"**{table}**  \n"
                f"<span style='color: grey; font-size: 12px'>"
                f"{counts.get(table, 0):,} rows</span>",
                unsafe_allow_html=True,
            )

    st.divider()


def render_result(result) -> None:
    if not result.success:
        st.error(
            f"Could not generate a valid query after "
            f"{result.attempts} attempt(s)."
        )
        if result.error_history:
            with st.expander("Error details"):
                for i, err in enumerate(result.error_history, 1):
                    st.text(f"Attempt {i}: {err}")
        return

    attempt_label = (
        f"Done in {result.attempts} attempt(s)."
        if result.attempts == 1
        else f"Done in {result.attempts} attempts (self-corrected)."
    )
    st.success(attempt_label)

    if result.explanation:
        st.info(result.explanation)

    with st.expander("Generated SQL", expanded=True):
        st.code(result.sql, language="sql")

    if result.error_history:
        with st.expander(
            f"Self-correction log — {len(result.error_history)} retry(s)"
        ):
            for i, err in enumerate(result.error_history, 1):
                st.text(f"Attempt {i} error: {err}")

    st.dataframe(
        result.result,
        use_container_width=True,
        hide_index=True,
    )
    st.caption(f"{len(result.result):,} rows returned.")


def main() -> None:
    st.title("Agentic SQL Co-pilot")
    st.caption(
        "Powered by Qwen2.5-Coder-7B and DuckDB. "
        "Ask anything about the loaded dataset."
    )

    hf_token = get_hf_token()
    con = get_connection()

    render_sidebar(con)

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "pending_question" not in st.session_state:
        st.session_state.pending_question = None

    if not st.session_state.messages:
        render_home_context(con)

    agent = SQLAgent(con=con, hf_token=hf_token)

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.markdown(message["content"])
            else:
                render_result(message["result"])

    question = st.chat_input("Ask a question about the dataset...")

    if st.session_state.pending_question:
        question = st.session_state.pending_question
        st.session_state.pending_question = None

    if question:
        st.session_state.messages.append(
            {"role": "user", "content": question}
        )
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("Generating SQL and running query..."):
                result = agent.run(question)
            render_result(result)

        st.session_state.messages.append(
            {"role": "assistant", "result": result}
        )


if __name__ == "__main__":
    main()