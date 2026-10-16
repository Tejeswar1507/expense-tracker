import calendar

import altair as alt
import pandas as pd
import streamlit as st

from app.common import current_member, inr, members, month_options
from core.calc import month_summary
from core.db import session_scope

me = current_member()
opts = month_options()
c1, c2 = st.columns([2, 1])
ym = c1.selectbox("Month", opts, format_func=lambda x: f"{calendar.month_name[x[1]]} {x[0]}")
with session_scope() as s:
    people = ["Both of us"] + [m.name for m in members(s)]
    who = c2.selectbox("Person", people)
    person = None if who == "Both of us" else who
    ms = month_summary(s, ym[0], ym[1], person)
    py, pm = (ym[0], ym[1] - 1) if ym[1] > 1 else (ym[0] - 1, 12)
    prev = month_summary(s, py, pm, person)

st.title(f"{calendar.month_name[ym[1]]} {ym[0]}")
k = st.columns(4)
delta = ms.money_out - prev.money_out
k[0].metric("Money out", inr(ms.money_out), f"{'+' if delta >= 0 else '−'}{inr(abs(delta))} vs last month", delta_color="inverse")
k[1].metric("Living", inr(ms.by_group.get("Living", 0)))
k[2].metric("Family support", inr(ms.by_group.get("Family support", 0)))
k[3].metric("Savings and investments", inr(ms.by_group.get("Savings and investments", 0)))
k2 = st.columns(4)
k2[0].metric("Loans and EMIs", inr(ms.by_group.get("Loans and EMIs", 0)))
k2[1].metric("Waiting for a category", ms.waiting_count)
k2[2].metric("Cash not yet explained", inr(ms.cash_unexplained), help="ATM withdrawals minus cash spends added in Quick add")
k2[3].metric("Not counted (own-account moves)", inr(ms.self_transfers), help="Card bills, transfers between you, ATM cash")

if ms.waiting_count:
    st.info(f"{ms.waiting_count} payment(s) need a category — open **What was this?**")

left, right = st.columns([3, 2])
with left:
    st.subheader("By category")
    if ms.by_category:
        df = pd.DataFrame({"Category": list(ms.by_category), "₹": [float(v) for v in ms.by_category.values()]})
        chart = alt.Chart(df).mark_bar().encode(
            x=alt.X("₹:Q", title=None), y=alt.Y("Category:N", sort="-x", title=None),
            tooltip=["Category", alt.Tooltip("₹:Q", format=",.0f")])
        st.altair_chart(chart, width="stretch")
    else:
        st.caption("No spending recorded this month yet.")
with right:
    st.subheader("By person")
    for p, v in ms.by_person.items():
        st.write(f"**{p}**: {inr(v)}")
    if ms.income:
        st.subheader("Income")
        st.write(f"Income {inr(ms.income)} · left over **{inr(ms.left_over)}**")

st.subheader("Budgets this month")
if not ms.budgets:
    st.caption("No budgets set. Add them on Admin → Budgets & categories.")
bars = []
for cat, who_b, budget, actual in ms.budgets:
    pct = float(actual / budget) if budget else 0
    color = "#d92d20" if pct > 1 else "#dc8a00" if pct >= 0.8 else "#12a150"
    label = f"{cat}{' · ' + who_b if who_b else ''}"
    bars.append(
        f'<div style="margin:6px 0 10px"><div style="display:flex;justify-content:space-between;font-size:14px">'
        f'<span>{label}</span><span>{inr(actual)} of {inr(budget)} · {pct:.0%}</span></div>'
        f'<div style="background:#eceef2;border-radius:4px;height:8px"><div style="width:{min(pct, 1) * 100:.1f}%;'
        f'background:{color};height:8px;border-radius:4px"></div></div></div>')
if bars:
    st.markdown("".join(bars), unsafe_allow_html=True)
    st.caption("Green under 80% · amber 80–100% · red over budget")
