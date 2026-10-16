from decimal import Decimal

import streamlit as st
from sqlalchemy import func, select

from app.common import categories, current_member, inr, is_admin, members, today_local
from core.db import Source, Transaction, session_scope
from core.services import quick_add

me = current_member()
st.title("Quick add")
st.caption("For cash, which never sends an email. Bank and UPI payments arrive by themselves.")

with session_scope() as s:
    cats = categories(s)
    names = [c.name for c in cats]
    # frequent cash items (templates), most used first
    freq = s.execute(
        select(Transaction.description, Transaction.category_id, Transaction.amount, func.count())
        .where(Transaction.source == Source.quick_add, Transaction.spent_by_id == me.id)
        .group_by(Transaction.description, Transaction.category_id, Transaction.amount)
        .order_by(func.count().desc()).limit(6)
    ).all()
    if freq:
        st.write("Frequent:")
        cols = st.columns(len(freq))
        for i, (desc, cid, amt, _) in enumerate(freq):
            if cols[i].button(f"{desc} {inr(amt)}", key=f"f{i}"):
                st.session_state.update(qa_amt=float(amt), qa_cat=next(c.name for c in cats if c.id == cid), qa_note=desc)

    with st.form("qa", clear_on_submit=True):
        amt = st.number_input("Amount (₹)", min_value=0.0, step=10.0, value=st.session_state.get("qa_amt", 0.0))
        cat = st.selectbox("Category", names, index=names.index(st.session_state["qa_cat"]) if st.session_state.get("qa_cat") in names else None,
                           placeholder="Choose…")
        note = st.text_input("Note", value=st.session_state.get("qa_note", ""), placeholder="milk, auto, vegetables…")
        d = st.date_input("Date", value=today_local(), max_value=today_local())
        who = me
        if is_admin(me):
            ms = members(s)
            who_name = st.selectbox("Spent by", [m.name for m in ms], index=[m.id for m in ms].index(me.id))
            who = next(m for m in ms if m.name == who_name)
        if st.form_submit_button("Add", type="primary"):
            if amt <= 0 or not cat:
                st.warning("Enter an amount and pick a category.")
            else:
                quick_add(s, me, amount=Decimal(str(amt)), category_id=next(c.id for c in cats if c.name == cat),
                          note=note, txn_date=d, spent_by=s.merge(who))
                s.commit()
                for k in ("qa_amt", "qa_cat", "qa_note"):
                    st.session_state.pop(k, None)
                st.success(f"Added {inr(amt)} · {cat}")
