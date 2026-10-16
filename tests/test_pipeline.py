"""End-to-end: the worked month from the v2 doc must give ₹40,920 money out."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.calc import inr, month_summary, waiting_for, year_table
from core.db import Category, Kind, Source, Status, Transaction
from core.ingest import ingest
from core.parsers.alerts import parse_alert
from core.services import NotAllowed, answer, delete, edit, quick_add
from tests.conftest import fake_fx

R = date(2026, 11, 20)


def email(s, body, subject="", member=None):
    return ingest(s, parse_alert(body, subject, R), source=Source.email, spent_by=member, fetch=fake_fx)


def cat(s, name):
    return s.scalar(select(Category).where(Category.name == name)).id


def test_worked_month(s, people):
    teja, babbi = people
    # 1-3 wife's UPI payments
    o1 = email(s, "Dear UPI user A/C X9876 debited by 450.0 on date 02Nov26 trf to SWIGGY Refno 431800000001. -SBI")
    assert o1.txn.kind == Kind.spent and o1.txn.status == Status.confirmed and o1.txn.spent_by_id == babbi.id
    assert o1.txn.category.name == "Food & Groceries"
    o2 = email(s, "Dear UPI user A/C X9876 debited by 320.0 on date 03Nov26 trf to UBER INDIA Refno 431800000002. -SBI")
    assert o2.txn.category.name == "Travel / Fuel"
    o3 = email(s, "Dear UPI user A/C X9876 debited by 200.0 on date 04Nov26 trf to RAMESH K Refno 431800000003. -SBI")
    assert o3.txn.status == Status.waiting  # new payee -> "What was this?"
    assert [t.id for t in waiting_for(s, babbi)] == [o3.txn.id]
    answer(s, babbi, o3.txn.id, category_id=cat(s, "Food & Groceries"), note="vegetables", learn=True)
    # the same vendor next time is recognised
    o3b = email(s, "Dear UPI user A/C X9876 debited by 60.0 on date 25Oct26 trf to RAMESH K Refno 431800000099. -SBI")
    assert o3b.txn.status == Status.confirmed and o3b.txn.decided_by.value == "learned_payee"

    # 4-6 your payments
    o4 = email(s, "Rs.1700.00 has been debited from account **1234 to VPA srisai@okhdfcbank SRI SAI RESIDENCY on 05-11-26. "
                  "Your UPI transaction reference number is 431800000004.")
    answer(s, teja, o4.txn.id, category_id=cat(s, "Monthly Bills"), note="Flat maintenance")
    o5 = email(s, "Rs.20000.00 has been debited from account **1234 to VPA father@oksbi SUNKARA RAO on 06-11-26. "
                  "UPI transaction reference number is 431800000005.")
    answer(s, teja, o5.txn.id, category_id=cat(s, "Family support"))
    o6 = email(s, "Rs.10000.00 has been debited from account **1234 to VPA zerodha@hdfcbank ZERODHA BROKING on 07-11-26. "
                  "UPI transaction reference number is 431800000006.")
    assert o6.txn.category.name == "Investments"

    # 7-8 Amazon on credit card, then a refund
    o7 = email(s, "Your HDFC Bank Credit Card no. XX4321 has been used for a transaction of INR 8,000.00 at AMAZON on 08-11-2026.")
    assert o7.txn.status == Status.waiting  # Amazon always asks
    answer(s, teja, o7.txn.id, category_id=cat(s, "Shopping"))
    o8 = email(s, "Refund of INR 500.00 has been credited to your HDFC Bank Credit Card XX4321 at AMAZON on 12-11-2026.")
    assert o8.txn.kind == Kind.refund
    answer(s, teja, o8.txn.id, category_id=cat(s, "Shopping"))

    # 9-10 ATM cash and cash spends
    o9 = email(s, "Rs.2000.00 withdrawn at ATM HYD123 from A/c XX9876 on 09-11-2026.")
    assert o9.txn.kind == Kind.self_transfer and o9.txn.is_cash_withdrawal
    quick_add(s, babbi, amount=Decimal(150), category_id=cat(s, "Travel / Fuel"), note="auto", txn_date=date(2026, 11, 10))
    quick_add(s, babbi, amount=Decimal(600), category_id=cat(s, "Food & Groceries"), note="milk", txn_date=date(2026, 11, 10))

    # 11 credit card bill via CRED -> self-transfer
    o11 = email(s, "Rs.8000.00 has been debited from account **1234 to VPA cred.club@axisb CRED on 15-11-26. "
                   "UPI transaction reference number is 431800000011.")
    assert o11.txn.kind == Kind.self_transfer

    # 12 you -> wife: two emails, one record
    o12a = email(s, "Rs.15000.00 has been debited from account **1234 to VPA babbi@oksbi BABITHA on 16-11-26. "
                    "UPI transaction reference number is 431800000012.")
    assert o12a.txn.kind == Kind.self_transfer
    o12b = email(s, "Rs.15000.00 credited to your A/c XX9876 by VPA teja.s@okicici on 16-11-26. UPI Ref No 431800000012.")
    assert o12b.result == "merged" and o12b.txn.id == o12a.txn.id
    s.commit()

    m = month_summary(s, 2026, 11)
    assert m.money_out == Decimal("40920.00")
    assert m.by_person == {"Teja": Decimal("39200.00"), "Babbi": Decimal("1720.00")}
    assert m.by_group["Family support"] == Decimal("20000.00")
    assert m.by_group["Savings and investments"] == Decimal("10000.00")
    assert m.by_group["Living"] == Decimal("10920.00")
    assert m.self_transfers == Decimal("25000.00")
    assert m.cash_unexplained == Decimal("1250.00")
    assert inr(m.money_out) == "₹40,920"
    yt = year_table(s, 2026)
    assert yt.loc["Money out", "Nov"] == 40920


def test_two_emails_one_payment_merge_note(s, people):
    a = email(s, "Rs.450.00 has been debited from account **1234 to VPA swiggy@icici SWIGGY on 14-11-26. "
                 "Your UPI transaction reference number is 431829117765.")
    b = email(s, "You paid ₹450 to Swiggy. UPI Ref 431829117765. Note: dinner")
    assert b.result == "merged" and b.txn.id == a.txn.id
    assert "dinner" in b.txn.description
    assert s.scalar(select(Transaction).where(Transaction.upi_ref == "431829117765")).id == a.txn.id


def test_reversal_marks_failed(s, people):
    a = email(s, "Rs.500.00 has been debited from account **1234 to VPA shop@ybl SHOP on 11-11-26. UPI Ref 431899990000.")
    email(s, "Rs.500.00 has been reversed to your account **1234 for the failed transaction. UPI Ref 431899990000 on 11-11-26.")
    s.refresh(a.txn)
    assert a.txn.kind == Kind.failed
    assert month_summary(s, 2026, 11).money_out == 0


def test_receipt_and_bank_email_merge(s, people):
    r = ingest(s, parse_alert("Hostinger ₹1532.82 Paid Successfully Payment Id pay_X Method UPI tejeswarsunkara@ybl "
                              "Paid On 30th Jun, 2026 07:29:21 AM IST", "Payment successful for Hostinger", R),
               source=Source.email, fetch=fake_fx)
    assert r.txn.kind == Kind.spent  # payer UPI is yours, but the payee is Hostinger: not a self-transfer
    b = email(s, "Rs.1532.82 has been debited from account **1234 to VPA hostinger.rzp@icici HOSTINGER on 30-06-26. "
                 "UPI transaction reference number is 431811110000.")
    assert b.result == "merged" and b.txn.id == r.txn.id


def test_usd_uses_transaction_date_rate(s, people):
    o = email(s, "A charge of USD 100.00 was made on your card ending in 7788 at NETFLIX.COM on Jan 15, 2026.")
    assert o.txn.currency == "USD" and o.txn.fx_rate == Decimal("93.00") and o.txn.amount_inr == Decimal("9300.00")


def test_member_cannot_edit_or_delete(s, people):
    teja, babbi = people
    t = quick_add(s, babbi, amount=Decimal(100), category_id=cat(s, "Food & Groceries"), txn_date=date(2026, 11, 1))
    with pytest.raises(NotAllowed):
        edit(s, babbi, t.id, amount=Decimal(1))
    with pytest.raises(NotAllowed):
        delete(s, babbi, t.id)
    with pytest.raises(NotAllowed):
        quick_add(s, babbi, amount=Decimal(10), category_id=cat(s, "Food & Groceries"), txn_date=date(2026, 11, 1), spent_by=teja)
    edit(s, teja, t.id, amount=Decimal(120), fetch=fake_fx)
    assert t.amount_inr == Decimal("120.00")


def test_member_can_only_answer_own_waiting(s, people):
    teja, babbi = people
    o = email(s, "Rs.300.00 has been debited from account **1234 to VPA someone@ybl SOMEONE on 14-11-26. UPI Ref 431800004444.")
    with pytest.raises(NotAllowed):
        answer(s, babbi, o.txn.id, category_id=cat(s, "Shopping"))
    answer(s, teja, o.txn.id, category_id=cat(s, "Shopping"))
    o2 = email(s, "Dear UPI user A/C X9876 debited by 90.0 on date 14Nov26 trf to NEW SHOP Refno 431800005555. -SBI")
    answer(s, babbi, o2.txn.id, category_id=cat(s, "Shopping"))
    with pytest.raises(NotAllowed):  # already saved -> only admin can change
        answer(s, babbi, o2.txn.id, category_id=cat(s, "Medical"))
