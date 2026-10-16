"""Expense Tracker web app. Run: streamlit run app/main.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Expense Tracker", page_icon="₹", layout="wide")

from app.common import current_member, is_admin  # noqa: E402

me = current_member()
st.session_state["me_id"] = me.id

pages = [
    st.Page("pages/month.py", title="Month", icon="📅", default=True),
    st.Page("pages/year.py", title="Year", icon="📈"),
    st.Page("pages/what_was_this.py", title="What was this?", icon="❓"),
    st.Page("pages/quick_add.py", title="Quick add", icon="➕"),
    st.Page("pages/transactions.py", title="Transactions", icon="📋"),
]
if is_admin(me):
    pages.append(st.Page("pages/admin.py", title="Admin", icon="⚙️"))

with st.sidebar:
    st.caption(f"Signed in as **{me.name}** ({'Admin' if is_admin(me) else 'Member'})")
    try:
        if "auth" in st.secrets and st.button("Sign out"):
            st.logout()
    except Exception:
        pass

st.navigation(pages).run()
