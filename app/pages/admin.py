import calendar
from decimal import Decimal

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.common import categories, current_member, inr, is_admin, members, month_options, today_local
from core import google_api
from core.calc import month_bounds, month_summary
from core.db import (Account, Budget, ChangeLog, Kind, MerchantRule, Payee, RawEmail, get_setting, session_scope,
                     set_setting)
from core.email_ingest import reprocess_failed
from core.seed import GROUPS
from core.services import (add_category, add_rule, close_cash, commit_statement, preview_statement, require_admin,
                           save_account, set_payee, upsert_budget)

me = current_member()
if not is_admin(me):
    st.error("Admin only.")
    st.stop()
st.title("Admin")
tabs = st.tabs(["Statements", "Import 2026 sheet", "Payees", "Merchant rules", "Budgets & categories", "Accounts", "Emails", "Cash", "Change log"])

with session_scope() as s:
    require_admin(me)
    cats = categories(s, active_only=False)
    cat_names = [c.name for c in cats]
    ms = members(s)

    # ---------------------------------------------------------------- statements
    with tabs[0]:
        st.caption("Upload a CSV, Excel or PDF statement. Rows already captured from emails are skipped. Nothing is saved until you press Save.")
        accs = list(s.scalars(select(Account).where(Account.kind != "cash")))
        if not accs:
            st.warning("Add your bank accounts and cards on the Accounts tab first.")
        acc = st.selectbox("Account", accs, format_func=lambda a: f"{a.name} ··{a.last4 or ''} ({a.member.name}, {a.currency})") if accs else None
        up = st.file_uploader("Statement", type=["csv", "xls", "xlsx", "pdf"])
        if up and acc:
            data = up.getvalue()
            try:
                h, rows, problems = preview_statement(s, me, data, up.name, acc)
                for p in problems:
                    st.warning(p)
                prev = pd.DataFrame([{"Date": r.parsed.txn_date, "In/Out": r.parsed.direction, "Amount": float(r.parsed.amount),
                                      "Payee": r.parsed.payee_name, "UPI ref": r.parsed.upi_ref, "Note": r.parsed.note,
                                      "Narration": r.raw} for r in rows])
                st.write(f"{len(rows)} rows found")
                st.dataframe(prev, hide_index=True, width="stretch")
                if st.button("Save to tracker", type="primary"):
                    drive_id = None
                    try:
                        drive_id = google_api.save_statement(up.name, data, rows[0].parsed.txn_date.year if rows else today_local().year)
                    except Exception as e:
                        st.info(f"Original file not saved to Drive ({e}). Transactions are still imported.")
                    counts = commit_statement(s, me, h, up.name, rows, acc, drive_id)
                    s.commit()
                    st.success(f"New: {counts['saved']} · already captured: {counts['merged'] + counts['duplicate']} · "
                               f"waiting for a category: {counts['waiting']}")
            except (ValueError, RuntimeError) as e:
                st.error(str(e))

    # ---------------------------------------------------------------- old sheet
    with tabs[1]:
        st.caption("One-time: download your '2026 Expense Tracker' Google Sheet as Excel (File → Download → .xlsx) and upload it. "
                   "Every month is checked against the sheet's own total.")
        old = st.file_uploader("2026 sheet (.xlsx)", type=["xlsx"], key="old_sheet")
        replace = st.checkbox("Replace an earlier import of this sheet")
        if old and st.button("Import", type="primary"):
            import io

            from scripts.import_2026_sheet import run as import_sheet

            report = import_sheet(io.BytesIO(old.getvalue()), replace=replace)
            st.markdown(report)

    # ---------------------------------------------------------------- payees
    with tabs[2]:
        st.caption("Everyone you have paid. Set a category so future payments are recognised; 'always ask' for payees like Amazon.")
        payees = list(s.scalars(select(Payee).order_by(Payee.display_name)))
        if payees:
            pick = st.selectbox("Payee", payees, format_func=lambda p: f"{p.display_name} ({p.match_text}) → {p.category.name if p.category else '—'}")
            with st.form("payee"):
                name = st.text_input("Display name", pick.display_name)
                c = st.selectbox("Category", cat_names, index=cat_names.index(pick.category.name) if pick.category else None)
                ask = st.checkbox("Always ask", pick.always_ask)
                fix = st.checkbox("Also fix past payments to this payee")
                if st.form_submit_button("Save", type="primary"):
                    n = set_payee(s, me, pick.id, category_id=next((x.id for x in cats if x.name == c), None),
                                  always_ask=ask, display_name=name, fix_past=fix)
                    s.commit()
                    st.success(f"Saved. {n} past payment(s) updated.")
        else:
            st.caption("No payees yet; they appear as payments arrive.")

    # ---------------------------------------------------------------- rules
    with tabs[3]:
        st.caption("If a payee name or UPI ID contains the keyword, the category is set automatically.")
        rules = pd.DataFrame([{"Keyword": r.keyword, "Category": r.category.name, "Kind": r.kind.value, "Always ask": r.always_ask}
                              for r in s.scalars(select(MerchantRule).order_by(MerchantRule.keyword))])
        st.dataframe(rules, hide_index=True, width="stretch", height=300)
        with st.form("rule"):
            c = st.columns(4)
            kw = c[0].text_input("Keyword", placeholder="e.g. ratnadeep")
            rc = c[1].selectbox("Category", cat_names)
            rk = c[2].selectbox("Kind", ["spent", "self_transfer"])
            ra = c[3].checkbox("Always ask")
            if st.form_submit_button("Add / update rule") and kw.strip():
                add_rule(s, me, kw, next(x.id for x in cats if x.name == rc), Kind(rk), ra)
                s.commit()
                st.rerun()

    # ---------------------------------------------------------------- budgets & categories
    with tabs[4]:
        st.caption("Monthly budget per category. 'Both' applies to your combined spending; a name applies to that person only.")
        rows = []
        for c in cats:
            for who in [None] + ms:
                b = s.scalar(select(Budget).where(Budget.category_id == c.id, Budget.member_id == (who.id if who else None)))
                rows.append({"Category": c.name, "Group": c.group, "For": who.name if who else "Both",
                             "Monthly budget ₹": float(b.monthly_inr) if b else 0.0})
        bdf = pd.DataFrame(rows)
        edited = st.data_editor(bdf, hide_index=True, width="stretch", disabled=["Category", "Group", "For"], height=420)
        if st.button("Save budgets", type="primary"):
            for _, r in edited.iterrows():
                cid = next(x.id for x in cats if x.name == r["Category"])
                mid = None if r["For"] == "Both" else next(m.id for m in ms if m.name == r["For"])
                upsert_budget(s, me, cid, mid, Decimal(str(r["Monthly budget ₹"] or 0)))
            s.commit()
            st.success("Budgets saved.")
        with st.form("cat"):
            c = st.columns(2)
            nn = c[0].text_input("New category")
            ng = c[1].selectbox("Group", GROUPS)
            if st.form_submit_button("Add category") and nn.strip():
                add_category(s, me, nn, ng)
                s.commit()
                st.rerun()

    # ---------------------------------------------------------------- accounts
    with tabs[5]:
        st.caption("Your bank accounts and cards. Last 4 digits tell the app whose payment an alert is; your own UPI IDs "
                   "and names tell it when money is just moving between your own accounts.")
        accs_all = list(s.scalars(select(Account).order_by(Account.member_id)))
        st.dataframe(pd.DataFrame([{"Name": a.name, "Who": a.member.name, "Kind": a.kind, "Bank": a.bank, "Last 4": a.last4,
                                    "Currency": a.currency, "Own UPI IDs": a.own_upi_ids} for a in accs_all]),
                     hide_index=True, width="stretch")
        pick = st.selectbox("Edit or add", [None] + accs_all, format_func=lambda a: "➕ New account" if a is None else a.name)
        with st.form("acc"):
            c = st.columns(3)
            name = c[0].text_input("Name", pick.name if pick else "", placeholder="HDFC savings")
            bank = c[1].text_input("Bank", (pick.bank or "") if pick else "")
            who = c[2].selectbox("Belongs to", [m.name for m in ms], index=[m.id for m in ms].index(pick.member_id) if pick else 0)
            c = st.columns(3)
            kind = c[0].selectbox("Kind", ["bank", "card", "cash"], index=["bank", "card", "cash"].index(pick.kind) if pick else 0)
            last4 = c[1].text_input("Last 4 digits", (pick.last4 or "") if pick else "", max_chars=4)
            cur = c[2].selectbox("Currency", ["INR", "USD"], index=["INR", "USD"].index(pick.currency) if pick else 0)
            upis = st.text_input("Your own UPI IDs on this account (comma separated)", (pick.own_upi_ids or "") if pick else "",
                                 placeholder="tejeswarsunkara@ybl, teja.s@okicici")
            if st.form_submit_button("Save account", type="primary") and name.strip():
                save_account(s, me, account_id=pick.id if pick else None, member_id=next(m.id for m in ms if m.name == who),
                             name=name, bank=bank, kind=kind, last4=last4, currency=cur, own_upi_ids=upis)
                s.commit()
                st.rerun()
        own = st.text_input("Your own names as banks print them (comma separated)", get_setting(s, "own_names", "") or "",
                            help="Payments to these names are treated as transfers between your own accounts.")
        if st.button("Save names"):
            set_setting(s, "own_names", own)
            s.commit()
            st.success("Saved.")

    # ---------------------------------------------------------------- emails
    with tabs[6]:
        status = st.radio("Show", ["failed", "ignored", "parsed", "merged"], horizontal=True)
        raws = list(s.scalars(select(RawEmail).where(RawEmail.status == status).order_by(RawEmail.id.desc()).limit(200)))
        st.dataframe(pd.DataFrame([{"Received": r.received_at, "From": r.sender, "Subject": r.subject, "Why": r.note,
                                    "Forwarded from": r.forwarded_from} for r in raws]), hide_index=True, width="stretch")
        if status == "failed" and raws:
            with st.expander("Show the text of the newest failed email"):
                st.text(raws[0].body[:3000])
            if st.button("Re-read failed emails (after a parser fix)"):
                n = reprocess_failed(s)
                s.commit()
                st.success(f"{n} now understood.")

    # ---------------------------------------------------------------- cash
    with tabs[7]:
        st.caption("Cash withdrawn but not explained in Quick add. Book the rest as 'Cash, not itemized' so the month total includes it.")
        ym = st.selectbox("Month", month_options(), format_func=lambda x: f"{calendar.month_name[x[1]]} {x[0]}", key="cash_m")
        for m in ms:
            sm = month_summary(s, ym[0], ym[1], m.name)
            c = st.columns([3, 1])
            c[0].write(f"**{m.name}**: withdrew {inr(sm.cash_withdrawn)}, explained {inr(sm.cash_explained)}, "
                       f"**not explained {inr(sm.cash_unexplained)}**")
            if sm.cash_unexplained > 0 and c[1].button("Close as not itemized", key=f"close{m.id}"):
                close_cash(s, me, m, sm.cash_unexplained, month_bounds(*ym)[1])
                s.commit()
                st.rerun()

    # ---------------------------------------------------------------- change log
    with tabs[8]:
        names = {m.id: m.name for m in ms}
        logs = list(s.scalars(select(ChangeLog).order_by(ChangeLog.id.desc()).limit(300)))
        st.dataframe(pd.DataFrame([{"When": l.at, "Who": names.get(l.by_id, "system"), "Action": l.action,
                                    "Transaction": l.transaction_id, "Before": l.before, "After": l.after} for l in logs]),
                     hide_index=True, width="stretch")
