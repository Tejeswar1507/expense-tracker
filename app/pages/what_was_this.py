import streamlit as st

from app.common import categories, current_member, inr, is_admin
from core.calc import waiting_for
from core.db import session_scope
from core.ingest import suggested_category
from core.services import NotAllowed, answer

me = current_member()
st.title("What was this?")
st.caption("Payments the app couldn't recognise. Pick a category once; tick the box and that payee is recognised next time.")

with session_scope() as s:
    items = waiting_for(s, None if is_admin(me) else me)
    cats = categories(s)
    if not items:
        st.success("Nothing waiting. 🎉")
        st.stop()
    names = [c.name for c in cats]
    for t in items:
        with st.form(f"w{t.id}"):
            who = f" · {t.spent_by.name}" if is_admin(me) else ""
            st.markdown(f"**{inr(t.amount_inr)}** to **{t.payee_raw or 'Unknown'}** · {t.txn_date:%d %b %Y}{who}")
            if t.currency != "INR":
                st.caption(f"{t.amount} {t.currency} at {t.fx_rate}")
            if t.payee and t.payee.match_text and "@" in t.payee.match_text:
                st.caption(f"UPI ID: {t.payee.match_text}")
            sug = suggested_category(s, t)
            idx = next((i for i, c in enumerate(cats) if c.id == sug), None)
            c1, c2 = st.columns([1, 2])
            cat = c1.selectbox("Category", names, index=idx, placeholder="Choose…", key=f"c{t.id}")
            note = c2.text_input("Note (optional)", value="" if (t.description or "") == (t.payee.display_name if t.payee else "") else (t.description or ""), key=f"n{t.id}")
            always_ask = bool(t.payee and t.payee.always_ask)
            learn = st.checkbox(f"Always use this for {t.payee.display_name if t.payee else 'this payee'}",
                                value=not always_ask, disabled=always_ask or not t.payee, key=f"l{t.id}",
                                help="Off for payees like Amazon that sell many kinds of things.")
            if st.form_submit_button("Save", type="primary"):
                if not cat:
                    st.warning("Pick a category first.")
                else:
                    try:
                        answer(s, me, t.id, category_id=next(c.id for c in cats if c.name == cat), note=note, learn=learn)
                        s.commit()
                        st.toast("Saved")
                        st.rerun()
                    except NotAllowed as e:
                        st.error(str(e))
