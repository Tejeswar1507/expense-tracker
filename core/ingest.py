"""Turn a parsed email / statement row into exactly one transaction.

Order (matches the flowchart in the v2 doc):
  read -> UPI ref already saved? (merge) -> near match from another source? (merge) -> decide kind/category -> save
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.categorize import decide, get_or_create_payee
from core.db import Account, ChangeLog, Kind, Member, Role, Source, Status, Transaction
from core.fx import Fetcher, frankfurter_fetch, inr_rate, to_inr
from core.parsers.alerts import ParsedTxn


@dataclass
class Outcome:
    result: str  # saved | merged | duplicate
    txn: Transaction
    reason: str = ""


def snapshot(t: Transaction) -> dict:
    return {
        "txn_date": t.txn_date.isoformat() if t.txn_date else None,
        "kind": t.kind.value if t.kind else None,
        "amount": str(t.amount),
        "currency": t.currency,
        "amount_inr": str(t.amount_inr),
        "category_id": t.category_id,
        "description": t.description,
        "spent_by_id": t.spent_by_id,
        "status": t.status.value if t.status else None,
    }


def log(s: Session, t: Transaction, action: str, by: Member | None, before: dict | None = None) -> None:
    s.add(ChangeLog(transaction_id=t.id, action=action, before=before, after=snapshot(t), by_id=by.id if by else None))


def find_account(s: Session, p: ParsedTxn) -> Account | None:
    if p.account_last4:
        acc = s.scalar(select(Account).where(Account.last4 == p.account_last4))
        if acc:
            return acc
        acc = s.scalar(select(Account).where(Account.last4.like(f"%{p.account_last4[-3:]}")))
        if acc:
            return acc
    if p.payer_vpa:
        for a in s.scalars(select(Account).where(Account.own_upi_ids.isnot(None))):
            if p.payer_vpa in [x.strip().lower() for x in a.own_upi_ids.split(",")]:
                return a
    return None


def _merge(existing: Transaction, p: ParsedTxn, acc: Account | None) -> None:
    if p.is_reversal and existing.kind == Kind.spent:
        existing.kind = Kind.failed
        existing.status = Status.confirmed
        existing.description = (existing.description or "") + " (failed, reversed)"
        return
    default_desc = existing.payee.display_name if existing.payee else None
    if p.note and (not existing.description or existing.description == default_desc):
        existing.description = p.note
    elif p.note and p.note not in (existing.description or ""):
        existing.description = f"{existing.description} · {p.note}"
    if acc and not existing.account_id:
        existing.account_id = acc.id
    if p.upi_ref and not existing.upi_ref:
        existing.upi_ref = p.upi_ref
    if p.payee_name and not existing.payee_raw:
        existing.payee_raw = p.payee_name


def find_near_match(s: Session, *, amount_inr: Decimal, d: date, spent_by_id: int, direction: str,
                    upi_ref: str | None, origin: str, source: Source) -> Transaction | None:
    kinds = [Kind.spent, Kind.self_transfer] if direction == "debit" else [Kind.income, Kind.refund, Kind.self_transfer]
    q = select(Transaction).where(
        Transaction.amount_inr == amount_inr,
        Transaction.txn_date.between(d - timedelta(days=1), d + timedelta(days=1)),
        Transaction.spent_by_id == spent_by_id,
        Transaction.kind.in_(kinds),
        Transaction.status != Status.deleted,
    )
    for t in s.scalars(q):
        both_have_refs = bool(t.upi_ref and upi_ref)
        different_origin = (t.source != source) or (t.origin != origin and "receipt" in (t.origin, origin))
        if not both_have_refs and different_origin:
            return t
    return None


def ingest(s: Session, p: ParsedTxn, *, source: Source, spent_by: Member | None = None,
           account: Account | None = None, added_by: Member | None = None,
           fetch: Fetcher = frankfurter_fetch) -> Outcome:
    acc = account or find_account(s, p)
    member = (acc.member if acc else None) or spent_by or s.scalar(select(Member).where(Member.role == Role.admin))
    d = p.txn_date or date.today()

    # 1. same UPI reference = same payment (e.g. bank email + UPI app email, or a debit and its reversal)
    if p.upi_ref:
        existing = s.scalar(select(Transaction).where(Transaction.upi_ref == p.upi_ref))
        if existing:
            before = snapshot(existing)
            _merge(existing, p, acc)
            s.flush()
            log(s, existing, "merged", added_by, before)
            return Outcome("merged", existing, "same UPI reference")

    currency = (acc.currency if acc and acc.currency != "INR" else p.currency) or "INR"
    if p.template == "statement" and acc and acc.currency:
        currency = acc.currency
    rate = inr_rate(s, d, currency, fetch)
    amount_inr = to_inr(p.amount, rate)

    # 2. same payment seen through another source (receipt + bank email, email + statement)
    near = find_near_match(s, amount_inr=amount_inr, d=d, spent_by_id=member.id, direction=p.direction,
                           upi_ref=p.upi_ref, origin=p.template, source=source)
    if near:
        before = snapshot(near)
        _merge(near, p, acc)
        s.flush()
        log(s, near, "merged", added_by, before)
        return Outcome("duplicate" if source == Source.statement else "merged", near, "same amount and date from another source")

    # 3-4. what is it?
    payee = None if p.is_atm else get_or_create_payee(s, p.payee_name, p.payee_vpa)
    dec = decide(s, direction=p.direction, name=p.payee_name, vpa=p.payee_vpa, payee=payee,
                 is_atm=p.is_atm, is_refund=p.is_refund, is_reversal=p.is_reversal)
    t = Transaction(
        txn_date=d, spent_by_id=member.id, account_id=acc.id if acc else None, kind=dec.kind,
        payee_id=payee.id if payee else None, payee_raw=(p.payee_name or p.payee_vpa or ("ATM" if p.is_atm else None)),
        amount=p.amount, currency=currency, fx_rate=rate, amount_inr=amount_inr,
        category_id=dec.category_id, decided_by=dec.decided_by,
        description=p.note or (payee.display_name if payee else ("ATM cash withdrawal" if p.is_atm else None)),
        payment_method=p.payment_method, upi_ref=p.upi_ref, is_cash_withdrawal=p.is_atm,
        source=source, origin=p.template, status=dec.status, added_by_id=added_by.id if added_by else None,
    )
    s.add(t)
    s.flush()
    log(s, t, "created", added_by)
    return Outcome("saved", t, dec.reason)


def suggested_category(s: Session, t: Transaction) -> int | None:
    """Pre-selected choice on the 'What was this?' screen."""
    from core.categorize import match_rule

    if t.payee and t.payee.category_id:
        return t.payee.category_id
    rule = match_rule(s, t.payee_raw, t.payee.match_text if t.payee and "@" in (t.payee.match_text or "") else None)
    return rule.category_id if rule else None
