"""What GitHub Actions runs every hour.

Every run:      read new alert emails.
21:00 IST:      'What was this?' emails (only to whoever has something waiting / budget alerts).
Mon 08:00 IST:  weekly summary to both.
1st 09:00 IST:  last month's summary to both.
02:00 IST:      refresh the read-only Google Sheet copy.
A run that GitHub delays still catches up: each email type is sent once per day/week/month (tracked in app_settings).
Run locally:  python -m jobs.hourly --dry-run   (prints instead of sending)
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import select

from core import google_api
from core.config import settings
from core.db import Member, Transaction, get_setting, init_db, session_scope, set_setting
from core.email_ingest import process_new
from core.notify import daily_email, month_close_email, weekly_email
from core.seed import seed


def export_csv(s) -> bytes:
    rows = []
    for t in s.scalars(select(Transaction).order_by(Transaction.txn_date)):
        rows.append({
            "Date": t.txn_date.isoformat(), "Who": t.spent_by.name, "Kind": t.kind.value,
            "Category": t.category.name if t.category else "", "Group": t.category.group if t.category else "",
            "Payee": t.payee_raw or "", "Description": t.description or "", "Amount": float(t.amount),
            "Currency": t.currency, "Amount INR": float(t.amount_inr), "Status": t.status.value,
            "Method": t.payment_method or "", "UPI ref": t.upi_ref or "", "Source": t.source.value,
        })
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    return buf.getvalue().encode()


def run(now: datetime | None = None, dry_run: bool = False, force: str | None = None) -> dict:
    cfg = settings()
    now = now or datetime.now(cfg.tz)
    today = now.date()
    report: dict = {"at": now.isoformat()}
    init_db()
    with session_scope() as s:
        seed(s)
        send = (lambda to, subj, html: print(f"--- to {to}: {subj}\n{html[:400]}…")) if dry_run else google_api.send_email

        # 1. new alert emails
        try:
            report["emails"] = process_new(s, lambda: google_api.list_alert_ids(cfg.gmail_label),
                                           google_api.get_message)
        except RuntimeError as e:  # Google not set up yet
            report["emails"] = f"skipped: {e}"
        s.commit()

        members = list(s.scalars(select(Member)))
        # 2. 9 PM daily
        if (now.hour >= 21 or force == "daily") and get_setting(s, "sent_daily") != today.isoformat():
            for m in members:
                msg = daily_email(s, m, today)
                if msg:
                    send([m.google_email], *msg)
            set_setting(s, "sent_daily", today.isoformat())
            report["daily"] = "sent"
        # 3. Monday 8 AM weekly
        week = f"{today.isocalendar().year}-W{today.isocalendar().week}"
        if ((today.weekday() == 0 and now.hour >= 8) or force == "weekly") and get_setting(s, "sent_weekly") != week:
            send([m.google_email for m in members], *weekly_email(s, today))
            set_setting(s, "sent_weekly", week)
            report["weekly"] = "sent"
        # 4. 1st of the month 9 AM
        month = today.strftime("%Y-%m")
        if ((today.day == 1 and now.hour >= 9) or force == "month") and get_setting(s, "sent_month") != month:
            send([m.google_email for m in members], *month_close_email(s, today))
            set_setting(s, "sent_month", month)
            report["month_close"] = "sent"
        # 5. nightly Sheet copy
        if (now.hour >= 2 or force == "sheet") and get_setting(s, "sheet_copy_date") != today.isoformat() and not dry_run:
            try:
                fid = google_api.write_sheet_copy(export_csv(s), get_setting(s, "sheet_copy_id"))
                if fid != get_setting(s, "sheet_copy_id"):
                    for m in members:
                        if m.google_email != cfg.admin_email:
                            google_api.share_read_only(fid, m.google_email)
                set_setting(s, "sheet_copy_id", fid)
                set_setting(s, "sheet_copy_date", today.isoformat())
                report["sheet_copy"] = "updated"
            except RuntimeError as e:
                report["sheet_copy"] = f"skipped: {e}"
        if dry_run:
            s.rollback()  # a dry run changes nothing
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", choices=["daily", "weekly", "month", "sheet"])
    a = ap.parse_args()
    print(run(dry_run=a.dry_run, force=a.force))
    sys.exit(0)
