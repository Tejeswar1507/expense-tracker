"""Alert wordings modelled on common Indian bank / UPI / card alerts (amounts and IDs are made up).
When you share a real (masked) email, add it here as a new case."""
from datetime import date
from decimal import Decimal

import pytest

from core.parsers.alerts import CannotParse, NotAPayment, parse_alert

RECEIVED = date(2026, 11, 14)

CASES = [
    (
        "hdfc_upi_debit",
        "❗ You have done a UPI txn. Check details!",
        "Dear Customer, Rs.450.00 has been debited from account **1234 to VPA swiggy@icici SWIGGY on 14-11-26. "
        "Your UPI transaction reference number is 431829117765. If you did not authorize this transaction, please report it.",
        dict(amount=Decimal("450.00"), direction="debit", account_last4="1234", payee_vpa="swiggy@icici",
             payee_name="SWIGGY", upi_ref="431829117765", txn_date=date(2026, 11, 14), payment_method="upi"),
    ),
    (
        "icici_upi_debit",
        "Transaction alert for your ICICI Bank account",
        "Dear Customer, Your ICICI Bank Account XX567 has been debited with INR 320.00 on 13-Nov-26. "
        "Info: UPI/431812345678/Payment/uber@axisbank. The Available Balance is INR 12,345.00.",
        dict(amount=Decimal("320.00"), direction="debit", account_last4="567", payee_vpa="uber@axisbank",
             upi_ref="431812345678", txn_date=date(2026, 11, 13)),
    ),
    (
        "sbi_upi_debit",
        "",
        "Dear UPI user A/C X9876 debited by 200.0 on date 12Nov26 trf to RAMESH K Refno 431800001111. If not u? call 1800111109. -SBI",
        dict(amount=Decimal("200.0"), direction="debit", account_last4="9876", payee_name="RAMESH K",
             upi_ref="431800001111", txn_date=date(2026, 11, 12)),
    ),
    (
        "axis_card_spend",
        "Transaction alert on your Axis Bank Credit Card",
        "Your Axis Bank Credit Card no. XX4321 has been used for a transaction of INR 2,499.00 at AMAZON on 10-11-2026 18:42:10 IST.",
        dict(amount=Decimal("2499.00"), direction="debit", account_last4="4321", payee_name="AMAZON",
             txn_date=date(2026, 11, 10), payment_method="card"),
    ),
    (
        "kotak_upi_sent",
        "Kotak Bank: Sent Rs.1700.00",
        "Sent Rs.1700.00 from Kotak Bank AC X5555 to sri.sai.residency@okhdfcbank on 05-11-26. UPI Ref 431855556666. Not you, call 18602662666",
        dict(amount=Decimal("1700.00"), direction="debit", account_last4="5555",
             payee_vpa="sri.sai.residency@okhdfcbank", upi_ref="431855556666", txn_date=date(2026, 11, 5)),
    ),
    (
        "atm_withdrawal",
        "ATM withdrawal alert",
        "Rs.2000.00 withdrawn at ATM HYD123 from A/c XX1234 on 09-11-2026. Avl Bal Rs.8,000.00",
        dict(amount=Decimal("2000.00"), direction="debit", is_atm=True, account_last4="1234", payment_method="cash"),
    ),
    (
        "credit_received",
        "Money received",
        "Rs.15000.00 credited to your A/c XX9876 by VPA teja.s@okicici on 08-11-26. UPI Ref No 431877778888.",
        dict(amount=Decimal("15000.00"), direction="credit", payee_vpa="teja.s@okicici", upi_ref="431877778888"),
    ),
    (
        "reversal",
        "Transaction reversed",
        "Rs.500.00 has been reversed to your account **1234 for the failed transaction. UPI Ref 431899990000 on 11-11-26.",
        dict(amount=Decimal("500.00"), direction="credit", is_reversal=True, upi_ref="431899990000"),
    ),
    (
        "razorpay_receipt",
        "Payment successful for Hostinger",
        "Hostinger ₹1532.82 Paid Successfully Payment Id pay_T7fah7dobYCIn9 Method UPI someone@ybl "
        "Paid On 30th Jun, 2026 07:29:21 AM IST Email x@gmail.com",
        dict(amount=Decimal("1532.82"), direction="debit", payee_name="Hostinger", payer_vpa="someone@ybl",
             txn_date=date(2026, 6, 30), template="receipt"),
    ),
    (
        "usd_card",
        "Card transaction",
        "A charge of USD 15.49 was made on your card ending in 7788 at NETFLIX.COM on Nov 3, 2026.",
        dict(amount=Decimal("15.49"), currency="USD", direction="debit", account_last4="7788", txn_date=date(2026, 11, 3)),
    ),
]


@pytest.mark.parametrize("name,subject,body,expected", CASES, ids=[c[0] for c in CASES])
def test_parse(name, subject, body, expected):
    p = parse_alert(body, subject, RECEIVED)
    for k, v in expected.items():
        assert getattr(p, k) == v, f"{name}: {k} = {getattr(p, k)!r}, expected {v!r}"


def test_otp_is_ignored():
    with pytest.raises(NotAPayment):
        parse_alert("Your OTP for login is 123456. Do not share it.", "OTP", RECEIVED)


def test_statement_notice_is_ignored():
    with pytest.raises(NotAPayment):
        parse_alert("Your e-statement for October is ready. Click to view.", "Statement", RECEIVED)


def test_payment_without_direction_fails_loudly():
    with pytest.raises(CannotParse):
        parse_alert("Transaction of note: Rs. 120 at shop", "", RECEIVED) if False else parse_alert(
            "Alert: Rs. 120 regarding your account XX1234", "", RECEIVED
        )


def test_html_body():
    p = parse_alert("<html><body><p>Rs.99.00 has been debited from account **1234 to VPA "
                    "zepto@ybl ZEPTO on 01-11-26.</p><p>UPI ref 431811112222</p></body></html>", "", RECEIVED)
    assert p.amount == Decimal("99.00") and p.payee_vpa == "zepto@ybl" and p.upi_ref == "431811112222"
