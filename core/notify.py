"""The three emails: 9 PM 'What was this?' (+ budget alerts, admin warnings), Monday summary, month close."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import escape

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.calc import inr, month_summary, waiting_for
from core.config import settings
from core.db import Member, RawEmail, Role, Transaction

CSS = ("font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;color:#1f2328;font-size:14px;line-height:1.45")


def _wrap(title: str, body: str) -> str:
    url = settings().app_url
    return (f'<div style="{CSS}"><h2 style="margin:0 0 4px;font-size:18px">{escape(title)}</h2>{body}'
            f'<p style="margin-top:20px"><a href="{url}" style="background:#1a56db;color:#fff;padding:8px 14px;'
            f'border-radius:6px;text-decoration:none">Open Expense Tracker</a></p>'
            f'<p style="color:#888;font-size:11px">Sent by your Expense Tracker.</p></div>')


def _table(rows: list[tuple[str, str]]) -> str:
    tr = "".join(f'<tr><td style="padding:4px 0;border-bottom:1px solid #eee">{escape(a)}</td>'
                 f'<td style="padding:4px 0;border-bottom:1px solid #eee;text-align:right">{escape(b)}</td></tr>' for a, b in rows)
    return f'<table style="width:100%;border-collapse:collapse">{tr}</table>'


def budget_alerts(s: Session, today: date) -> list[str]:
    ms = month_summary(s, today.year, today.month)
    out = []
    for cat, who, budget, actual in ms.budgets:
        if budget and actual >= budget * Decimal("0.8"):
            pct = int(actual / budget * 100)
            out.append(f"{cat}{' (' + who + ')' if who else ''}: {inr(actual)} of {inr(budget)} ({pct}%)")
    return out


def daily_email(s: Session, member: Member, today: date) -> tuple[str, str] | None:
    """Returns (subject, html) or None when there is nothing to say."""
    items = waiting_for(s, member)
    parts = []
    if items:
        rows = [(f"{t.txn_date:%d %b} · {t.payee_raw or 'Unknown'}", inr(t.amount_inr)) for t in items[:25]]
        parts.append(f"<p>{len(items)} payment{'s' if len(items) > 1 else ''} need a category:</p>" + _table(rows))
    alerts = budget_alerts(s, today)
    if alerts:
        parts.append("<p><b>Budgets near or over the limit this month</b></p><ul>" +
                     "".join(f"<li>{escape(a)}</li>" for a in alerts) + "</ul>")
    if member.role == Role.admin:
        failed = s.scalar(select(func.count()).select_from(RawEmail).where(RawEmail.status == "failed")) or 0
        if failed:
            parts.append(f"<p>⚠️ {failed} alert email(s) couldn't be read. See Admin → Emails.</p>")
        for other in s.scalars(select(Member).where(Member.role != Role.admin)):
            last = s.scalar(select(func.max(Transaction.txn_date)).where(Transaction.spent_by_id == other.id,
                                                                         Transaction.source == "email"))
            if last and (today - last).days >= 3:
                parts.append(f"<p>⚠️ No alert emails for {escape(other.name)} since {last:%d %b}. "
                             f"Is her Gmail forwarding filter still on?</p>")
    if not parts:
        return None
    subject = f"What was this? {len(items)} to explain" if items else "Expense Tracker: budget alert"
    return subject, _wrap(f"Hi {member.name}", "".join(parts))


def weekly_email(s: Session, today: date) -> tuple[str, str]:
    start = today - timedelta(days=7)
    from core.calc import frame

    df = frame(s, start, today - timedelta(days=1))
    rows, total = [], Decimal(0)
    if not df.empty:
        counted = df[df["kind"].isin(["spent", "refund"])]
        total = sum((Decimal(x) for x in counted["out"]), Decimal(0))
        by_cat = counted.groupby("category")["out"].apply(lambda v: sum((Decimal(x) for x in v), Decimal(0)))
        rows = [(c, inr(v)) for c, v in by_cat.sort_values(ascending=False).head(6).items()]
    ms = month_summary(s, today.year, today.month)
    body = (f"<p>Last 7 days: <b>{inr(total)}</b> money out.</p>" + (_table(rows) if rows else "") +
            f"<p>This month so far: <b>{inr(ms.money_out)}</b>.</p>")
    if ms.cash_unexplained > 0:
        body += f"<p>Cash not yet explained this month: {inr(ms.cash_unexplained)}. Add cash spends in Quick add.</p>"
    alerts = budget_alerts(s, today)
    if alerts:
        body += "<p><b>Budgets near the limit</b></p><ul>" + "".join(f"<li>{escape(a)}</li>" for a in alerts) + "</ul>"
    return f"Weekly summary · {inr(total)}", _wrap("Your week", body)


def month_close_email(s: Session, today: date) -> tuple[str, str]:
    last_day = today.replace(day=1) - timedelta(days=1)
    ms = month_summary(s, last_day.year, last_day.month)
    prev_last = last_day.replace(day=1) - timedelta(days=1)
    prev = month_summary(s, prev_last.year, prev_last.month)
    name = f"{calendar.month_name[last_day.month]} {last_day.year}"
    diff = ms.money_out - prev.money_out
    body = (f"<p>Money out: <b>{inr(ms.money_out)}</b> "
            f"({'+' if diff >= 0 else '−'}{inr(abs(diff))} vs {calendar.month_name[prev_last.month]}).</p>"
            "<p><b>By group</b></p>" + _table([(g, inr(v)) for g, v in ms.by_group.items()]) +
            "<p><b>By person</b></p>" + _table([(p, inr(v)) for p, v in ms.by_person.items()]) +
            "<p><b>Top categories</b></p>" + _table([(c, inr(v)) for c, v in list(ms.by_category.items())[:8]]))
    over = [(c, w, b, a) for c, w, b, a in ms.budgets if b and a > b]
    if over:
        body += "<p><b>Over budget</b></p><ul>" + "".join(
            f"<li>{escape(c)}{' (' + w + ')' if w else ''}: {inr(a)} of {inr(b)}</li>" for c, w, b, a in over) + "</ul>"
    if ms.cash_unexplained > 0:
        body += f"<p>Cash not explained: {inr(ms.cash_unexplained)} (Admin → Cash can book it as 'Cash, not itemized').</p>"
    return f"{name}: {inr(ms.money_out)} money out", _wrap(name, body)
