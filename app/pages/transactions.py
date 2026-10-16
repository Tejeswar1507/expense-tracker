import calendar
from decimal import Decimal

import pandas as pd
import streamlit as st
from sqlalchemy import or_, select

from app.common import categories, current_member, inr, is_admin, members, month_options
from core.calc import month_bounds
from core.db import Kind, Member, Status, Transaction, session_scope
from core.services import NotAllowed, delete, edit, restore

me = current_member()
admin = is_admin(me)
st.title("Transactions")

with session_scope() as s:
    cats = categories(s, active_only=False)
    ms = members(s)
    f = st.columns(5)
    ym = f[0].selectbox("Month", [None] + month_options(), format_func=lambda x: "All" if x is None else f"{calendar.month_abbr[x[1]]} {x[0]}", index=1)
    who = f[1].selectbox("Person", ["Both"] + [m.name for m in ms])
    cat = f[2].selectbox("Category", ["All"] + [c.name for c in cats])
    kind = f[3].selectbox("Kind", ["All"] + [k.value for k in Kind])
    show_bin = f[4].checkbox("Show bin", disabled=not admin)
    q = st.text_input("Search payee, note or UPI ref")

    stmt = select(Transaction).order_by(Transaction.txn_date.desc(), Transaction.id.desc())
    if ym:
        a, b = month_bounds(*ym)
        stmt = stmt.where(Transaction.txn_date.between(a, b))
    if who != "Both":
        stmt = stmt.join(Member, Member.id == Transaction.spent_by_id).where(Member.name == who)
    if cat != "All":
        stmt = stmt.where(Transaction.category_id == next(c.id for c in cats if c.name == cat))
    if kind != "All":
        stmt = stmt.where(Transaction.kind == Kind(kind))
    stmt = stmt.where(Transaction.status == Status.deleted) if show_bin else stmt.where(Transaction.status != Status.deleted)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Transaction.payee_raw.ilike(like), Transaction.description.ilike(like), Transaction.upi_ref.ilike(like)))
    rows = list(s.scalars(stmt.limit(2000)))

    df = pd.DataFrame([{
        "ID": t.id, "Date": t.txn_date, "Who": t.spent_by.name, "Kind": t.kind.value,
        "Category": t.category.name if t.category else "— waiting —", "Payee": t.payee_raw or "",
        "Note": t.description or "", "₹": float(t.amount_inr),
        "Original": f"{t.amount} {t.currency}" if t.currency != "INR" else "", "Via": t.source.value,
        "Status": t.status.value,
    } for t in rows])
    st.caption(f"{len(df)} shown")
    if df.empty:
        st.stop()
    st.dataframe(df, hide_index=True, width="stretch", column_config={"₹": st.column_config.NumberColumn(format="₹%,.0f")})
    st.download_button("Download CSV", df.to_csv(index=False).encode(), "transactions.csv", "text/csv")

    if not admin:
        st.caption("Only the admin can change or delete saved transactions.")
        st.stop()

    st.subheader("Edit a transaction")
    tid = st.selectbox("Transaction", df["ID"].tolist(),
                       format_func=lambda i: next(f"#{r.ID} · {r.Date} · {r.Payee or r.Note} · {inr(r['₹'])}" for _, r in df[df.ID == i].iterrows()))
    t = s.get(Transaction, tid)
    with st.form("edit"):
        c = st.columns(3)
        d = c[0].date_input("Date", t.txn_date)
        amt = c[1].number_input("Amount", value=float(t.amount), step=1.0)
        cur = c[2].selectbox("Currency", ["INR", "USD"], index=["INR", "USD"].index(t.currency) if t.currency in ("INR", "USD") else 0)
        c = st.columns(3)
        cat_names = [x.name for x in cats]
        ncat = c[0].selectbox("Category", cat_names, index=cat_names.index(t.category.name) if t.category else None, placeholder="—")
        nkind = c[1].selectbox("Kind", [k.value for k in Kind], index=[k.value for k in Kind].index(t.kind.value),
                               help="self_transfer and failed are not counted as money out")
        nwho = c[2].selectbox("Spent by", [m.name for m in ms], index=[m.id for m in ms].index(t.spent_by_id))
        note = st.text_input("Note", t.description or "")
        b1, b2 = st.columns(2)
        if b1.form_submit_button("Save changes", type="primary"):
            changes = {}
            if d != t.txn_date: changes["txn_date"] = d
            if Decimal(str(amt)) != t.amount: changes["amount"] = Decimal(str(amt))
            if cur != t.currency: changes["currency"] = cur
            new_cat = next((x.id for x in cats if x.name == ncat), None)
            if new_cat != t.category_id: changes["category_id"] = new_cat
            if nkind != t.kind.value: changes["kind"] = nkind
            new_who = next(m.id for m in ms if m.name == nwho)
            if new_who != t.spent_by_id: changes["spent_by_id"] = new_who
            if note != (t.description or ""): changes["description"] = note
            if changes:
                try:
                    edit(s, me, t.id, **changes)
                    s.commit()
                    st.success("Saved.")
                    st.rerun()
                except (NotAllowed, RuntimeError, ValueError) as e:
                    st.error(str(e))
        if t.status == Status.deleted:
            if b2.form_submit_button("Restore from bin"):
                restore(s, me, t.id); s.commit(); st.rerun()
        elif b2.form_submit_button("Move to bin"):
            delete(s, me, t.id); s.commit(); st.rerun()
