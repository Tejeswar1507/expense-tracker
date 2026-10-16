"""Every change goes through here, and every function checks who is asking.

Admin (you): everything. Member (your wife): add her own cash spends, and explain her own payments
while they are waiting. She can never edit or delete a saved transaction. Hiding buttons in the UI is
not the protection; these checks are.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.categorize import get_or_create_payee
from core.db import (
    Account,
    Budget,
    Category,
    DecidedBy,
    Kind,
    Member,
    MerchantRule,
    Payee,
    Role,
    Source,
    StatementImport,
    Status,
    Transaction,
)
from core.fx import Fetcher, frankfurter_fetch, inr_rate, to_inr
from core.ingest import Outcome, ingest, log, snapshot
from core.parsers.statements import file_hash, parse_statement


class NotAllowed(PermissionError):
    pass


def require_admin(actor: Member) -> None:
    if not actor or actor.role != Role.admin:
        raise NotAllowed("Only the admin can do this.")


def cash_wallet(s: Session, member: Member) -> Account:
    acc = s.scalar(select(Account).where(Account.member_id == member.id, Account.kind == "cash"))
    if not acc:
        acc = Account(member_id=member.id, name=f"Cash wallet ({member.name})", kind="cash", currency="INR")
        s.add(acc)
        s.flush()
    return acc


# ------------------------------------------------------------------ adding


def quick_add(s: Session, actor: Member, *, amount: Decimal, category_id: int, note: str = "",
              txn_date: date, spent_by: Member | None = None, cash: bool = True) -> Transaction:
    spent_by = spent_by or actor
    if actor.role != Role.admin and spent_by.id != actor.id:
        raise NotAllowed("You can only add your own spends.")
    if Decimal(amount) <= 0:
        raise ValueError("Amount must be more than zero.")
    cat = s.get(Category, category_id)
    if not cat:
        raise ValueError("Pick a category.")
    acc = cash_wallet(s, spent_by) if cash else None
    t = Transaction(
        txn_date=txn_date, spent_by_id=spent_by.id, account_id=acc.id if acc else None, kind=Kind.spent,
        amount=Decimal(amount), currency="INR", fx_rate=Decimal(1), amount_inr=Decimal(amount),
        category_id=cat.id, decided_by=DecidedBy.person, description=note.strip() or cat.name,
        payment_method="cash" if cash else "other", source=Source.quick_add, origin="quick_add",
        status=Status.confirmed, added_by_id=actor.id,
    )
    s.add(t)
    s.flush()
    log(s, t, "created", actor)
    return t


def answer(s: Session, actor: Member, txn_id: int, *, category_id: int, note: str = "", learn: bool = True) -> Transaction:
    t = s.get(Transaction, txn_id)
    if not t:
        raise ValueError("Transaction not found.")
    if actor.role != Role.admin:
        if t.spent_by_id != actor.id:
            raise NotAllowed("You can only explain your own payments.")
        if t.status != Status.waiting:
            raise NotAllowed("This payment is already saved; only the admin can change it.")
    before = snapshot(t)
    t.category_id = category_id
    t.decided_by = DecidedBy.person
    t.status = Status.confirmed
    if note.strip():
        t.description = note.strip()
    t.edited_by_id = actor.id
    if learn and t.payee and not t.payee.always_ask:
        t.payee.category_id = category_id
    log(s, t, "answered", actor, before)
    return t


# ------------------------------------------------------------------ admin-only changes

EDITABLE = {"txn_date", "amount", "currency", "category_id", "description", "kind", "spent_by_id", "status"}


def edit(s: Session, actor: Member, txn_id: int, fetch: Fetcher = frankfurter_fetch, **changes) -> Transaction:
    require_admin(actor)
    t = s.get(Transaction, txn_id)
    if not t:
        raise ValueError("Transaction not found.")
    bad = set(changes) - EDITABLE
    if bad:
        raise ValueError(f"Can't edit {bad}")
    before = snapshot(t)
    for k, v in changes.items():
        if k == "kind" and isinstance(v, str):
            v = Kind(v)
        if k == "status" and isinstance(v, str):
            v = Status(v)
        setattr(t, k, v)
    if {"amount", "currency", "txn_date"} & changes.keys():
        t.fx_rate = inr_rate(s, t.txn_date, t.currency, fetch)
        t.amount_inr = to_inr(t.amount, t.fx_rate)
    if "category_id" in changes and t.status == Status.waiting and t.category_id:
        t.status = Status.confirmed
        t.decided_by = DecidedBy.person
    t.edited_by_id = actor.id
    log(s, t, "edited", actor, before)
    return t


def delete(s: Session, actor: Member, txn_id: int) -> Transaction:
    require_admin(actor)
    t = s.get(Transaction, txn_id)
    before = snapshot(t)
    t.status = Status.deleted
    t.edited_by_id = actor.id
    log(s, t, "deleted", actor, before)
    return t


def restore(s: Session, actor: Member, txn_id: int) -> Transaction:
    require_admin(actor)
    t = s.get(Transaction, txn_id)
    before = snapshot(t)
    t.status = Status.confirmed if t.category_id or t.kind != Kind.spent else Status.waiting
    log(s, t, "restored", actor, before)
    return t


def set_payee(s: Session, actor: Member, payee_id: int, *, category_id: int | None = None,
              always_ask: bool | None = None, display_name: str | None = None, fix_past: bool = False) -> int:
    """Returns how many past payments were changed."""
    require_admin(actor)
    p = s.get(Payee, payee_id)
    if display_name:
        p.display_name = display_name
    if always_ask is not None:
        p.always_ask = always_ask
    if category_id is not None:
        p.category_id = category_id
    changed = 0
    if fix_past and category_id is not None:
        for t in s.scalars(select(Transaction).where(Transaction.payee_id == p.id, Transaction.status != Status.deleted,
                                                     Transaction.kind.in_([Kind.spent, Kind.refund]))):
            if t.category_id != category_id:
                before = snapshot(t)
                t.category_id = category_id
                t.status = Status.confirmed
                t.decided_by = DecidedBy.person
                log(s, t, "payee fix", actor, before)
                changed += 1
    return changed


def close_cash(s: Session, actor: Member, member: Member, amount: Decimal, month_end: date) -> Transaction:
    """Book unexplained cash as 'Cash, not itemized' so the month total still includes every rupee."""
    require_admin(actor)
    cat = s.scalar(select(Category).where(Category.name == "Cash, not itemized"))
    return quick_add(s, actor, amount=amount, category_id=cat.id, note="Cash, not itemized (month close)",
                     txn_date=month_end, spent_by=member, cash=True)


def upsert_budget(s: Session, actor: Member, category_id: int, member_id: int | None, monthly_inr: Decimal) -> None:
    require_admin(actor)
    b = s.scalar(select(Budget).where(Budget.category_id == category_id, Budget.member_id == member_id))
    if Decimal(monthly_inr) <= 0:
        if b:
            s.delete(b)
        return
    if b:
        b.monthly_inr = Decimal(monthly_inr)
    else:
        s.add(Budget(category_id=category_id, member_id=member_id, monthly_inr=Decimal(monthly_inr)))


def add_rule(s: Session, actor: Member, keyword: str, category_id: int, kind: Kind = Kind.spent, always_ask: bool = False) -> None:
    require_admin(actor)
    kw = keyword.strip().lower()
    r = s.scalar(select(MerchantRule).where(MerchantRule.keyword == kw))
    if r:
        r.category_id, r.kind, r.always_ask = category_id, kind, always_ask
    else:
        s.add(MerchantRule(keyword=kw, category_id=category_id, kind=kind, always_ask=always_ask))


def save_account(s: Session, actor: Member, *, account_id: int | None, member_id: int, name: str, bank: str,
                 kind: str, last4: str, currency: str, own_upi_ids: str) -> Account:
    require_admin(actor)
    a = s.get(Account, account_id) if account_id else Account()
    a.member_id, a.name, a.bank, a.kind = member_id, name, bank or None, kind
    a.last4 = (last4 or "").strip()[-4:] or None
    a.currency, a.own_upi_ids = currency, (own_upi_ids or "").lower() or None
    s.add(a)
    s.flush()
    return a


def add_category(s: Session, actor: Member, name: str, group: str) -> Category:
    require_admin(actor)
    c = Category(name=name.strip(), group=group)
    s.add(c)
    s.flush()
    return c


# ------------------------------------------------------------------ statements


def preview_statement(s: Session, actor: Member, data: bytes, file_name: str, account: Account | None):
    require_admin(actor)
    h = file_hash(data)
    seen = s.scalar(select(StatementImport).where(StatementImport.file_hash == h))
    if seen:
        raise ValueError(f"This file was already imported on {seen.uploaded_at:%d %b %Y}.")
    rows, problems = parse_statement(data, file_name, account.currency if account else "INR")
    return h, rows, problems


def commit_statement(s: Session, actor: Member, file_hash_: str, file_name: str, rows, account: Account | None,
                     drive_file_id: str | None = None, fetch: Fetcher = frankfurter_fetch) -> dict:
    require_admin(actor)
    counts = {"saved": 0, "merged": 0, "duplicate": 0, "waiting": 0}
    for row in rows:
        out: Outcome = ingest(s, row.parsed, source=Source.statement, account=account,
                              spent_by=account.member if account else actor, added_by=actor, fetch=fetch)
        counts[out.result] += 1
        if out.result == "saved" and out.txn.status == Status.waiting:
            counts["waiting"] += 1
    s.add(StatementImport(file_name=file_name, file_hash=file_hash_, drive_file_id=drive_file_id,
                          account_id=account.id if account else None, uploaded_by_id=actor.id,
                          rows_new=counts["saved"], rows_duplicate=counts["merged"] + counts["duplicate"]))
    return counts
