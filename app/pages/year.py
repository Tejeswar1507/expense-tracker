import altair as alt
import pandas as pd
import streamlit as st

from app.common import current_member, inr, members, today_local
from core.calc import year_table
from core.db import session_scope

me = current_member()
t = today_local()
c1, c2, c3 = st.columns(3)
year = c1.selectbox("Year", list(range(t.year, t.year - 5, -1)))
by = c2.radio("Rows", ["Groups", "Categories"], horizontal=True)
with session_scope() as s:
    who = c3.selectbox("Person", ["Both of us"] + [m.name for m in members(s)])
    tab = year_table(s, year, "group" if by == "Groups" else "category", None if who == "Both of us" else who)
    last = year_table(s, year - 1, "group")

st.title(f"{year} · January to December")
if tab.empty:
    st.caption("No data for this year.")
    st.stop()

total = tab.loc["Money out", "Total"]
prev_total = last.loc["Money out", "Total"] if not last.empty else 0
c = st.columns(3)
c[0].metric("Money out this year", inr(total))
c[1].metric("Average per month so far", inr(total / max(1, (t.month if year == t.year else 12))))
if prev_total:
    c[2].metric(f"{year - 1} total", inr(prev_total))

months = [m for m in tab.columns if m != "Total"]
long = tab.drop(index="Money out")[months].reset_index().melt(id_vars=tab.index.name or "index", var_name="Month", value_name="₹")
long.columns = ["Row", "Month", "₹"]
chart = alt.Chart(long).mark_bar().encode(
    x=alt.X("Month:N", sort=months, title=None), y=alt.Y("₹:Q", stack=True, title=None),
    color=alt.Color("Row:N", title=None, scale=alt.Scale(
        domain=["Living", "Family support", "Savings and investments", "Loans and EMIs"],
        range=["#1a56db", "#12a150", "#7c3aed", "#dc8a00"]) if by == "Groups" else alt.Undefined), tooltip=["Row", "Month", alt.Tooltip("₹:Q", format=",.0f")])
st.altair_chart(chart, width="stretch")
st.dataframe(tab.map(inr).rename_axis("Group" if by == "Groups" else "Category"), width="stretch")
