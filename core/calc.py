"""All the numbers on the dashboard and in the emails.

Money out (month) = everything Spent − Refunds, by transaction date. Includes family support,
investments and EMIs. Excludes self-transfers (own accounts, card bills, ATM cash) and failed payments.
Payments still waiting for a category are counted too (under "Not yet categorized").
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import Account, Budget, Category, Kind, Member, Status, Transaction

UNCATEGORIZED = "Not yet categorized"
ZERO = Decimal("0")


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def frame(s: Session, start: date, end: date) -> pd.DataFrame:
    q = (
        select(
            Transaction.id, Transaction.txn_date, Transaction.kind, Transaction.status, Transaction.amount_inr,
            Transaction.is_cash_withdrawal, Transaction.description, Transaction.payee_raw,
            Member.name.label("person"), Category.name.label("category"), Category.group.label("group"),
            Account.kind.label("account_kind"),
        )
        .join(Member, Member.id == Transaction.spent_by_id)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .outerjoin(Account, Account.id == Transaction.account_id)
        .where(Transaction.txn_date.between(start, end), Transaction.status != Status.deleted)
    )
    df = pd.DataFrame(s.execute(q).all(), columns=[
        "id", "txn_date", "kind", "status", "amount_inr", "is_cash_withdrawal", "description", "payee",
        "person", "category", "group", "account_kind"])
    if df.empty:
        return df
    df["kind"] = df["kind"].map(lambda k: k.value if hasattr(k, "value") else k)
    df["amount_inr"] = df["amount_inr"].map(Decimal)
    df["category"] = df["category"].fillna(UNCATEGORIZED)
    df["group"] = df["group"].fillna("Living")
    # signed amount that counts toward money out
    df["out"] = df.apply(lambda r: r.amount_inr if r.kind == "spent" else (-r.amount_inr if r.kind == "refund" else ZERO), axis=1)
    return df


@dataclass
class MonthSummary:
    year: int
    month: int
    money_out: Decimal = ZERO
    income: Decimal = ZERO
    self_transfers: Decimal = ZERO
    failed: Decimal = ZERO
    by_group: dict = field(default_factory=dict)
    by_category: dict = field(default_factory=dict)
    by_person: dict = field(default_factory=dict)
    cash_withdrawn: Decimal = ZERO
    cash_explained: Decimal = ZERO
    waiting_count: int = 0
    budgets: list = field(default_factory=list)  # (category, person or None, budget, actual)
    transactions: int = 0

    @property
    def left_over(self) -> Decimal | None:
        return self.income - self.money_out if self.income else None

    @property
    def cash_unexplained(self) -> Decimal:
        return max(ZERO, self.cash_withdrawn - self.cash_explained)


def _sumd(series) -> Decimal:
    return sum((Decimal(x) for x in series), ZERO)


def month_summary(s: Session, year: int, month: int, person: str | None = None) -> MonthSummary:
    start, end = month_bounds(year, month)
    df = frame(s, start, end)
    ms = MonthSummary(year, month)
    if df.empty:
        ms.budgets = budget_rows(s, df, person)
        return ms
    if person:
        df = df[df["person"] == person]
    counted = df[df["kind"].isin(["spent", "refund"])]
    ms.transactions = len(counted)
    ms.money_out = _sumd(counted["out"])
    ms.income = _sumd(df[df["kind"] == "income"]["amount_inr"])
    ms.self_transfers = _sumd(df[df["kind"] == "self_transfer"]["amount_inr"])
    ms.failed = _sumd(df[df["kind"] == "failed"]["amount_inr"])
    ms.by_group = {k: _sumd(v) for k, v in counted.groupby("group")["out"]}
    ms.by_category = dict(sorted(((k, _sumd(v)) for k, v in counted.groupby("category")["out"]), key=lambda kv: -kv[1]))
    ms.by_person = {k: _sumd(v) for k, v in counted.groupby("person")["out"]}
    ms.cash_withdrawn = _sumd(df[df["is_cash_withdrawal"] == True]["amount_inr"])  # noqa: E712
    ms.cash_explained = _sumd(counted[(counted["account_kind"] == "cash") & (counted["kind"] == "spent")]["amount_inr"])
    ms.waiting_count = int((df["status"].map(lambda x: getattr(x, "value", x)) == "waiting").sum())
    ms.budgets = budget_rows(s, counted, person)
    return ms


def budget_rows(s: Session, counted: pd.DataFrame, person: str | None) -> list[tuple[str, str | None, Decimal, Decimal]]:
    rows = []
    for b in s.scalars(select(Budget)):
        who = b.member.name if b.member else None
        if person and who and who != person:
            continue
        if counted.empty:
            actual = ZERO
        else:
            m = counted[counted["category"] == b.category.name]
            if who:
                m = m[m["person"] == who]
            actual = _sumd(m["out"])
        rows.append((b.category.name, who, Decimal(b.monthly_inr), actual))
    rows.sort(key=lambda r: -(r[3] / r[2] if r[2] else 0))
    return rows


def year_table(s: Session, year: int, by: str = "group", person: str | None = None) -> pd.DataFrame:
    """Rows = groups or categories, columns = Jan..Dec + Total. Values in ₹."""
    df = frame(s, date(year, 1, 1), date(year, 12, 31))
    months = [calendar.month_abbr[i] for i in range(1, 13)]
    if df.empty:
        return pd.DataFrame(columns=months + ["Total"])
    if person:
        df = df[df["person"] == person]
    df = df[df["kind"].isin(["spent", "refund"])].copy()
    df["m"] = df["txn_date"].map(lambda d: calendar.month_abbr[d.month])
    df["out_f"] = df["out"].astype(float)
    t = df.pivot_table(index=by, columns="m", values="out_f", aggfunc="sum", fill_value=0.0)
    t = t.reindex(columns=months, fill_value=0.0)
    t["Total"] = t.sum(axis=1)
    t = t.sort_values("Total", ascending=False)
    t.loc["Money out"] = t.sum(axis=0)
    return t.round(0)


def waiting_for(s: Session, member: Member | None = None) -> list[Transaction]:
    q = select(Transaction).where(Transaction.status == Status.waiting).order_by(Transaction.txn_date.desc())
    if member:
        q = q.where(Transaction.spent_by_id == member.id)
    return list(s.scalars(q))


def inr(x) -> str:
    """₹1,23,456 Indian grouping."""
    n = int(round(float(x or 0)))
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"{sign}₹{s}"
