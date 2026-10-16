"""The four checks that decide what a payment is (see the v2 doc, 'Categories and descriptions').

1. Paid to your own account?        -> self-transfer, not counted
2. Merchant rule (keyword) matches? -> category set (or 'always ask' with a suggestion)
3. Payee labelled before?           -> category set
4. Nothing matched                  -> waits in "What was this?" (optional AI guess first)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import Account, DecidedBy, Kind, MerchantRule, Payee, Status, get_setting


@dataclass
class Decision:
    kind: Kind
    status: Status
    category_id: int | None
    decided_by: DecidedBy | None
    suggestion_id: int | None = None
    reason: str = ""


def norm(text: str | None) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip() + " "


def payee_key(name: str | None, vpa: str | None) -> str | None:
    if vpa:
        return vpa.lower().strip()
    n = norm(name).strip()
    return n or None


def own_upi_ids(s: Session) -> set[str]:
    ids: set[str] = set()
    for a in s.scalars(select(Account)):
        if a.own_upi_ids:
            ids |= {x.strip().lower() for x in a.own_upi_ids.split(",") if x.strip()}
    return ids


def own_names(s: Session) -> list[str]:
    raw = get_setting(s, "own_names", "") or ""
    return [norm(x).strip() for x in raw.split(",") if x.strip()]


def is_own(s: Session, name: str | None, vpa: str | None) -> bool:
    if vpa and vpa.lower() in own_upi_ids(s):
        return True
    n = norm(name).strip()
    return bool(n) and any(n == o or (len(o) > 5 and o in n) for o in own_names(s))


def match_rule(s: Session, name: str | None, vpa: str | None) -> MerchantRule | None:
    hay = norm(name) + norm(vpa.split("@")[0] if vpa else "")
    best = None
    for r in s.scalars(select(MerchantRule)):
        kw = norm(r.keyword)
        if kw.strip() and kw in hay and (best is None or len(r.keyword) > len(best.keyword)):
            best = r  # longest keyword wins ("paytm money" beats "paytm")
    return best


def get_or_create_payee(s: Session, name: str | None, vpa: str | None) -> Payee | None:
    key = payee_key(name, vpa)
    if not key:
        return None
    p = s.scalar(select(Payee).where(Payee.match_text == key))
    if not p:
        display = (name or (vpa.split("@")[0] if vpa else key)).strip().title()[:120]
        p = Payee(display_name=display, match_text=key)
        s.add(p)
        s.flush()
    return p


def decide(s: Session, *, direction: str, name: str | None, vpa: str | None, payee: Payee | None,
           is_atm: bool = False, is_refund: bool = False, is_reversal: bool = False) -> Decision:
    if is_atm:
        return Decision(Kind.self_transfer, Status.confirmed, None, DecidedBy.own_account, reason="ATM cash into Cash wallet")
    if is_reversal:
        return Decision(Kind.failed, Status.confirmed, None, DecidedBy.own_account, reason="failed payment reversed")
    if is_own(s, name, vpa):
        return Decision(Kind.self_transfer, Status.confirmed, None, DecidedBy.own_account, reason="your own account")

    rule = match_rule(s, name, vpa)
    if rule and rule.kind == Kind.self_transfer:
        return Decision(Kind.self_transfer, Status.confirmed, None, DecidedBy.merchant_rule, reason=f"rule '{rule.keyword}'")

    if direction == "credit" and not is_refund:
        return Decision(Kind.income, Status.confirmed, None, None, reason="money received")

    kind = Kind.refund if direction == "credit" else Kind.spent
    if payee and payee.always_ask:
        return Decision(kind, Status.waiting, None, None, suggestion_id=payee.category_id, reason="payee set to always ask")
    if rule:
        if rule.always_ask:
            return Decision(kind, Status.waiting, None, None, suggestion_id=rule.category_id, reason=f"'{rule.keyword}' always asks")
        return Decision(kind, Status.confirmed, rule.category_id, DecidedBy.merchant_rule, reason=f"rule '{rule.keyword}'")
    if payee and payee.category_id:
        return Decision(kind, Status.confirmed, payee.category_id, DecidedBy.learned_payee, reason="payee labelled before")
    return Decision(kind, Status.waiting, None, None, reason="new payee")
