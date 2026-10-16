import io
from datetime import datetime

import pandas as pd

from scripts.import_2026_sheet import run


def _xlsx() -> io.BytesIO:
    buf = io.BytesIO()
    responses = pd.DataFrame({
        "Timestamp": [datetime(2026, 2, 3)] * 4,
        "Who spent": ["Babbi", "Teja", "Babbi", "Babbi"],
        "  Date of Expense  ": [datetime(2026, 2, 3), datetime(2026, 2, 5), datetime(2026, 3, 1), datetime(2026, 3, 9)],
        "Expense Month": ["2026-02", "2026-02", "2026-03", "2026-03"],
        "  Expense Category  ": ["Monthly Bills", "Savings / Investments", "Miscellaneous", "Food & Groceries"],
        " Description  ": ["Flat maintenance", "SIP", "Dad mobile recharge", "Vegetables"],
        "  Amount  ": [1700, 10000, 355, 200],
        "  Mode of Payment  ": ["UPI", "UPI", "UPI", "Cash"],
        "  Remarks  ": [None, None, None, None],
    })
    budgets = pd.DataFrame({"Person": ["Babbi"], "Category": ["Food & Groceries"], "Monthly Budget (₹)": [8000]})
    with pd.ExcelWriter(buf) as w:
        responses.to_excel(w, sheet_name="Form Responses", index=False)
        budgets.to_excel(w, sheet_name="Budgets", index=False)
    buf.seek(0)
    return buf


def test_import_reconciles(s, people):
    s.commit()
    report = run(_xlsx())
    assert "Imported 4 transactions and 1 budgets" in report
    assert "✅ all months match" in report
    assert "Dad mobile recharge" in report  # moved to Family support
    assert run(_xlsx()).startswith("Already imported")
    assert "Imported 4" in run(_xlsx(), replace=True)
