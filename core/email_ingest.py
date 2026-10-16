"""Read every new alert email under the Gmail label and run it through the pipeline. Nothing is ever lost:
each email is stored in raw_emails as parsed / merged / ignored / failed, and failed ones can be re-run."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import ai
from core.db import Member, RawEmail, Source, Status
from core.google_api import email_address_in
from core.ingest import ingest
from core.parsers.alerts import CannotParse, NotAPayment, parse_alert


def _member_for(s: Session, msg: dict) -> Member | None:
    """Your wife's alerts are auto-forwarded: the original To: (or X-Forwarded-For) is her address."""
    members = list(s.scalars(select(Member)))
    for header in (msg.get("x_forwarded_for", ""), msg.get("to", "")):
        for part in (header or "").split(","):
            addr = email_address_in(part)
            for m in members:
                if addr and addr == m.google_email:
                    return m
    return None


def process_message(s: Session, msg: dict, fetch=None) -> RawEmail:
    raw = RawEmail(gmail_message_id=msg["id"], received_at=msg.get("received"), sender=(msg.get("sender") or "")[:200],
                   subject=(msg.get("subject") or "")[:300], body=msg.get("body") or "", status="failed")
    member = _member_for(s, msg)
    raw.forwarded_from = member.google_email if member else None
    received = msg["received"].date() if isinstance(msg.get("received"), datetime) else None
    try:
        with s.begin_nested():  # a failure rolls back only this email's partial work
            p = parse_alert(raw.body, raw.subject, received)
            kwargs = {"fetch": fetch} if fetch else {}
            out = ingest(s, p, source=Source.email, spent_by=member, **kwargs)
        raw.transaction_id = out.txn.id
        raw.status = "merged" if out.result != "saved" else "parsed"
        raw.note = out.reason + ("; " + "; ".join(p.warnings) if p.warnings else "")
        if out.result == "saved" and out.txn.status == Status.waiting:
            try:
                ai.try_guess(s, out.txn)
            except Exception:
                pass
    except NotAPayment as e:
        raw.status, raw.note = "ignored", str(e)
    except (CannotParse, Exception) as e:  # keep it, show it on Admin, re-run after a parser fix
        raw.status, raw.note = "failed", f"{type(e).__name__}: {e}"[:500]
    s.add(raw)
    s.flush()
    return raw


def process_new(s: Session, list_ids: Callable[[], list[str]], get_message: Callable[[str], dict], fetch=None) -> dict:
    seen = set(s.scalars(select(RawEmail.gmail_message_id)))
    counts = {"parsed": 0, "merged": 0, "ignored": 0, "failed": 0}
    for mid in list_ids():
        if mid in seen:
            continue
        raw = process_message(s, get_message(mid), fetch)
        counts[raw.status] += 1
    return counts


def reprocess_failed(s: Session, fetch=None) -> int:
    """After a parser fix: run failed emails again."""
    fixed = 0
    for raw in list(s.scalars(select(RawEmail).where(RawEmail.status == "failed"))):
        msg = {"id": raw.gmail_message_id, "subject": raw.subject, "body": raw.body, "received": raw.received_at,
               "sender": raw.sender, "to": raw.forwarded_from or ""}
        s.delete(raw)
        s.flush()
        new = process_message(s, msg, fetch)
        fixed += new.status != "failed"
    return fixed
