from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import select

from core.db import Account, Kind, Source, Status
from core.ingest import ingest
from core.parsers.alerts import parse_alert
from core.parsers.statements import parse_narration, parse_statement
from core.services import commit_statement, preview_statement
from tests.conftest import fake_fx

HDFC_CSV = """HDFC BANK Ltd.,,,,,,
Statement of account,,,,,,
Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
01/11/26,UPI-SWIGGY-swiggy@icici-ICIC0DC0099-431829117765-dinner,0000431829117765,01/11/26,450.00,,10000.00
02/11/26,UPI-RAMESH K-ramesh.k@okaxis-UTIB0000123-431800000777-vegetables,0000431800000777,02/11/26,200.00,,9800.00
03/11/26,NWD-XXXX1234-ATM HYD123,000000000000,03/11/26,2000.00,,7800.00
04/11/26,NEFT CR-SALARY ACME LTD,N123,04/11/26,,50000.00,57800.00
,,,,,,
"""


def test_narration_parsing():
    n = parse_narration("UPI-SWIGGY-swiggy@icici-ICIC0DC0099-431829117765-dinner")
    assert n["payee_vpa"] == "swiggy@icici" and n["upi_ref"] == "431829117765" and n["note"] == "dinner"
    n = parse_narration("UPI/DR/431829117765/RAMESH K/SBIN/ramesh.k@oksbi/vegetables")
    assert n["payee_name"] == "RAMESH K" and n["note"] == "vegetables"
    assert parse_narration("NWD-XXXX1234-ATM HYD123")["atm"]


def test_parse_hdfc_csv():
    rows, problems = parse_statement(HDFC_CSV.encode(), "hdfc.csv")
    assert not problems and len(rows) == 4
    assert rows[0].parsed.amount == Decimal("450.00") and rows[0].parsed.note == "dinner"
    assert rows[2].parsed.is_atm and rows[3].parsed.direction == "credit"


def test_excel_amount_drcr(tmp_path):
    df = pd.DataFrame({"Txn Date": ["05-11-2026", "06-11-2026"], "Description": ["UPI/DR/431800009999/ZEPTO/YESB/zepto@ybl/milk", "Refund AMAZON"],
                       "Amount": ["99.00", "500.00"], "Dr/Cr": ["DR", "CR"]})
    p = tmp_path / "s.xlsx"
    df.to_excel(p, index=False)
    rows, _ = parse_statement(p.read_bytes(), "s.xlsx")
    assert rows[0].parsed.direction == "debit" and rows[1].parsed.direction == "credit" and rows[1].parsed.is_refund


def test_statement_import_skips_email_duplicates_and_refuses_reupload(s, people):
    teja, babbi = people
    acc = s.scalar(select(Account).where(Account.last4 == "1234"))
    # the Swiggy payment already arrived by email
    ingest(s, parse_alert("Rs.450.00 has been debited from account **1234 to VPA swiggy@icici SWIGGY on 01-11-26. "
                          "Your UPI transaction reference number is 431829117765.", "", date(2026, 11, 2)),
           source=Source.email, fetch=fake_fx)
    h, rows, _ = preview_statement(s, teja, HDFC_CSV.encode(), "hdfc.csv", acc)
    counts = commit_statement(s, teja, h, "hdfc.csv", rows, acc, fetch=fake_fx)
    assert counts["merged"] == 1 and counts["saved"] == 3
    s.commit()
    with pytest.raises(ValueError, match="already imported"):
        preview_statement(s, teja, HDFC_CSV.encode(), "hdfc.csv", acc)
