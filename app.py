import streamlit as st
import matplotlib.pyplot as plt
import pandas as pd
import sqlite3
import tempfile
import os

if not os.path.exists("store.db"):
    import create_database

from agent import ask_agent, explain_result, describe_table, build_schema_from_db, SCHEMA

st.set_page_config(page_title="AI Business Analyst", page_icon="📊", layout="wide")

# --- Light CSS polish ---
st.markdown("""
<style>
    .block-container { padding-top: 2.5rem; }
    div[data-testid="stChatInput"] textarea { font-size: 1rem; }
    .hero { font-size: 2.3rem; font-weight: 800; margin-bottom: .2rem; }
    .sub  { color: #8b92a8; margin-top: 0; }
</style>
""", unsafe_allow_html=True)

if "history" not in st.session_state:
    st.session_state.history = []
if "pending" not in st.session_state:
    st.session_state.pending = None


def clean_columns(d):
    d.columns = (
        d.columns.astype(str)
        .str.strip().str.lower()
        .str.replace(r"[^\w\s]", "", regex=True)
        .str.replace(r"\s+", "_", regex=True)
    )
    return d


def safe_table_name(name):
    t = name.strip().lower().replace(" ", "_").replace("-", "_")
    return "".join(c for c in t if c.isalnum() or c == "_") or "sheet"


def list_tables(db_path):
    conn = sqlite3.connect(db_path)
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    return names


# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("📁 Data Source")
    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx", "xls"])

    active_db = "store.db"
    active_schema = SCHEMA

    if uploaded is not None:
        tmp_db = os.path.join(tempfile.gettempdir(), "uploaded.db")

        if uploaded.name.lower().endswith((".xlsx", ".xls")):
            xls = pd.ExcelFile(uploaded)
            conn = sqlite3.connect(tmp_db)
            sheet_info = []
            for name in xls.sheet_names:
                d = clean_columns(pd.read_excel(xls, sheet_name=name))
                table = safe_table_name(name)
                d.to_sql(table, conn, if_exists="replace", index=False)
                sheet_info.append((name, table, d.shape[0], d.shape[1]))
            conn.close()
            active_db = tmp_db
            active_schema = build_schema_from_db(tmp_db)
            st.success(f"Loaded {len(sheet_info)} sheet(s).")
            with st.expander("Sheets / tables"):
                for name, table, rows, cols in sheet_info:
                    st.write(f"**{name}** → `{table}` · {rows}×{cols}")
        else:
            try:
                df = pd.read_csv(uploaded)
            except UnicodeDecodeError:
                uploaded.seek(0)
                df = pd.read_csv(uploaded, encoding="latin-1")
            df = clean_columns(df)
            conn = sqlite3.connect(tmp_db)
            df.to_sql("data", conn, if_exists="replace", index=False)
            conn.close()
            active_db = tmp_db
            active_schema = build_schema_from_db(tmp_db)
            st.success(f"Loaded {df.shape[0]} rows, {df.shape[1]} columns.")
            with st.expander("Columns"):
                st.write(list(df.columns))
    else:
        st.caption("Using the built-in demo store database.")

    st.divider()
    st.header("🕘 History")
    if st.session_state.history:
        for i, q in enumerate(reversed(st.session_state.history)):
            if st.button(q, key=f"h{i}", width="stretch"):
                st.session_state.pending = q
    else:
        st.caption("Past questions appear here.")


# ---------------- Header ----------------
st.markdown('<p class="hero">📊 AI Business Analyst</p>', unsafe_allow_html=True)
st.markdown('<p class="sub">Ask anything about your data — in plain English.</p>',
            unsafe_allow_html=True)

# Compact mode switch (segmented control falls back to radio on older Streamlit)
try:
    mode = st.segmented_control(
        "Mode", ["Ask", "Explore"], default="Ask", label_visibility="collapsed"
    ) or "Ask"
except Exception:
    mode = st.radio("Mode", ["Ask", "Explore"], horizontal=True, label_visibility="collapsed")


# =========================================================
#  EXPLORE MODE
# =========================================================
if mode == "Explore":
    tables = list_tables(active_db)
    if not tables:
        st.info("Upload a file to explore it.")
    else:
        c1, c2 = st.columns([3, 1])
        chosen = c1.selectbox("Table / sheet", tables, label_visibility="collapsed")
        go = c2.button("📖 Describe", width="stretch")
        if go:
            with st.spinner("Reading the data…"):
                overview = describe_table(chosen, active_db)
            st.subheader(f"Overview of `{chosen}`")
            st.write(overview)
            conn = sqlite3.connect(active_db)
            preview = pd.read_sql(f'SELECT * FROM "{chosen}" LIMIT 10', conn)
            conn.close()
            st.caption("Preview")
            st.dataframe(preview, width="stretch")

# =========================================================
#  ASK MODE — chat-style input
# =========================================================
else:
    # Suggested questions when nothing asked yet
    if not st.session_state.history:
        st.caption("Try one of these:")
        s1, s2, s3 = st.columns(3)
        if s1.button("Revenue by month", width="stretch"):
            st.session_state.pending = "What is the total revenue for each month?"
        if s2.button("Biggest revenue drop", width="stretch"):
            st.session_state.pending = "Which product category had the biggest revenue drop from May to June 2026?"
        if s3.button("Revenue by region", width="stretch"):
            st.session_state.pending = "How much revenue came from each region?"

    typed = st.chat_input("Ask a question about your data…")
    question = typed or st.session_state.pending
    st.session_state.pending = None

    if question:
        if question not in st.session_state.history:
            st.session_state.history.append(question)

        st.markdown(f"**❓ {question}**")

        with st.spinner("Generating SQL and querying the database…"):
            result, sql = ask_agent(question, schema=active_schema, db_path=active_db)

        if result is None:
            st.error("I couldn't answer that. Try rephrasing, or switch to **Explore** "
                     "mode for a general overview of your data.")
        else:
            with st.expander("View the SQL the AI generated"):
                st.code(sql, language="sql")

            left, right = st.columns([1, 1])

            with left:
                st.subheader("Data")
                st.dataframe(result, width="stretch", height=300)
                st.download_button(
                    "⬇️ Download CSV",
                    result.to_csv(index=False).encode("utf-8"),
                    "analysis.csv", "text/csv",
                )

            with right:
                numeric_cols = result.select_dtypes(include="number").columns.tolist()
                if result.shape[0] >= 2 and result.shape[1] >= 2 and numeric_cols:
                    st.subheader("Chart")
                    x_col = list(result.columns)[0]
                    y_col = numeric_cols[0]
                    fig, ax = plt.subplots(figsize=(6, 4))
                    ax.bar(result[x_col].astype(str), result[y_col])
                    ax.set_xlabel(x_col); ax.set_ylabel(y_col)
                    plt.xticks(rotation=45, ha="right")
                    plt.tight_layout()
                    st.pyplot(fig)

            with st.spinner("Writing the business insight…"):
                insight = explain_result(question, sql, result)
            st.subheader("💡 Insight")
            st.info(insight)