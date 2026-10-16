from datetime import date, datetime, timezone

from sqlalchemy import select

from core.calc import waiting_for
from core.db import RawEmail, Transaction
from core.email_ingest import process_new, reprocess_failed
from core.notify import daily_email, month_close_email, weekly_email
from tests.conftest import fake_fx

INBOX = {
    "m1": {"id": "m1", "subject": "UPI txn", "sender": "alerts@hdfcbank.net", "to": "teja@example.com",
           "received": datetime(2026, 11, 14, 10, tzinfo=timezone.utc),
           "body": "Rs.450.00 has been debited from account **1234 to VPA swiggy@icici SWIGGY on 14-11-26. "
                   "Your UPI transaction reference number is 431829117765."},
    "m2": {"id": "m2", "subject": "UPI txn", "sender": "noreply@phonepe.com", "to": "teja@example.com",
           "received": datetime(2026, 11, 14, 10, 1, tzinfo=timezone.utc),
           "body": "You paid ₹450 to Swiggy. UPI Ref 431829117765. Note: dinner"},
    "m3": {"id": "m3", "subject": "OTP", "sender": "alerts@hdfcbank.net", "to": "teja@example.com",
           "received": datetime(2026, 11, 14, 11, tzinfo=timezone.utc), "body": "Your OTP is 123456"},
    # forwarded from your wife's inbox, account not set up yet -> spent by her via the To: header
    "m4": {"id": "m4", "subject": "Alert", "sender": "sbi@alerts.sbi.co.in", "to": "Babbi <babbi@example.com>",
           "received": datetime(2026, 11, 14, 12, tzinfo=timezone.utc),
           "body": "Dear UPI user A/C X0001 debited by 200.0 on date 14Nov26 trf to RAMESH K Refno 431800000003. -SBI"},
    "m5": {"id": "m5", "subject": "Alert", "sender": "x@bank", "to": "teja@example.com",
           "received": datetime(2026, 11, 14, 12, tzinfo=timezone.utc), "body": "Alert: Rs. 120 regarding your account XX1234"},
}


def test_process_inbox(s, people):
    teja, babbi = people
    counts = process_new(s, lambda: list(INBOX), INBOX.get, fetch=fake_fx)
    assert counts == {"parsed": 2, "merged": 1, "ignored": 1, "failed": 1}
    assert s.scalar(select(Transaction).where(Transaction.upi_ref == "431829117765")).description == "dinner"
    ramesh = s.scalar(select(Transaction).where(Transaction.upi_ref == "431800000003"))
    assert ramesh.spent_by_id == babbi.id
    # running again does nothing (each email once)
    assert process_new(s, lambda: list(INBOX), INBOX.get, fetch=fake_fx) == {"parsed": 0, "merged": 0, "ignored": 0, "failed": 0}
    assert reprocess_failed(s, fetch=fake_fx) == 0
    assert s.scalar(select(RawEmail).where(RawEmail.gmail_message_id == "m5")).status == "failed"

    # emails
    d = daily_email(s, babbi, date(2026, 11, 14))
    assert d and "RAMESH K" in d[1] and "1 to explain" in d[0]
    assert daily_email(s, teja, date(2026, 11, 14))  # admin hears about the failed email
    assert "money out" in weekly_email(s, date(2026, 11, 16))[1]
    subj, html = month_close_email(s, date(2026, 12, 1))
    assert "November 2026" in subj and "₹650" in subj
    assert len(waiting_for(s, babbi)) == 1
