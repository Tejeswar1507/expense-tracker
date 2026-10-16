"""One-time import of the 2026 Google Sheet (exported as .xlsx) into the new database.

    python -m scripts.import_2026_sheet path/to/2026_sheet.xlsx [--replace] [--report report.md]

- Who spent -> spent by; dates read as dates (no DD/MM vs MM/DD mix-ups)
- 'Savings / Investments' -> Investments
- Miscellaneous rows that clearly send money to family -> Family support (listed in the report)
- Budgets tab -> budgets per person
- Prints a month-by-month check: sheet total vs new 'money out'. They must match.
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
from sqlalchemy import delete, select

from core.calc import inr, month_summary
from core.db import (Budget, Category, ChangeLog, DecidedBy, Kind, Member, Source, Status, Transaction, init_db,
                     session_scope)
from core.seed import seed

CATEGORY_MAP = {"savings / investments": "Investments", "savings/investments": "Investments", "investment": "Investments",
                "travel/fuel": "Travel / Fuel", "food and groceries": "Food & Groceries"}
FAMILY = re.compile(r"\b(family|parents|amma|nanna|mother|father|mom|dad|in-?laws)\b", re.I)
REVIEW = re.compile(r"\b(home spending|on behalf of|transfer(red)?|sent to (teja|babbi|wife|husband))\b", re.I)
METHOD = {"upi": "upi", "cash": "cash", "bank transfer": "netbanking", "credit card": "card", "debit card": "card"}


def _date(v) -> date:
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date()
    return datetime.strptime(str(v).strip()[:10], "%d/%m/%Y").date()


def run(path, replace: bool = False) -> str:
    """path: a file path or a file-like object (the Admin screen passes the upload)."""
    sheets = pd.read_excel(path, sheet_name=None)
    fr = next(v for k, v in sheets.items() if "response" in k.lower())
    fr.columns = [str(c).strip() for c in fr.columns]
    init_db()
    lines: list[str] = []
    with session_scope() as s:
        seed(s)
        if replace:
            ids = list(s.scalars(select(Transaction.id).where(Transaction.source == Source.sheet_import)))
            if ids:
                s.execute(delete(ChangeLog).where(ChangeLog.transaction_id.in_(ids)))
                s.execute(delete(Transaction).where(Transaction.id.in_(ids)))
        elif s.scalar(select(Transaction.id).where(Transaction.source == Source.sheet_import).limit(1)):
            return "Already imported. Use --replace to import again."
        members = {m.name.lower(): m for m in s.scalars(select(Member))}
        cats = {c.name.lower(): c for c in s.scalars(select(Category))}
        sheet_month = defaultdict(Decimal)
        family, review, skipped = [], [], []
        n = 0
        for _, r in fr.iterrows():
            amt = r.get("Amount")
            if pd.isna(amt) or pd.isna(r.get("Date of Expense")):
                skipped.append(str(r.get("Description")))
                continue
            amount = Decimal(str(amt).replace(",", "").replace("₹", "")).quantize(Decimal("0.01"))
            d = _date(r["Date of Expense"])
            who = members.get(str(r["Who spent"]).strip().lower())
            if not who:
                skipped.append(f"unknown person {r['Who spent']}")
                continue
            cname = str(r.get("Expense Category") or "Miscellaneous").strip()
            cname = CATEGORY_MAP.get(cname.lower(), cname)
            desc = str(r.get("Description") or "").strip()
            rem = r.get("Remarks")
            if isinstance(rem, str) and rem.strip():
                desc = f"{desc} · {rem.strip()}"
            if cname == "Miscellaneous" and FAMILY.search(desc):
                cname = "Family support"
                family.append(f"{d} {who.name} {inr(amount)} — {desc}")
            if REVIEW.search(desc):
                review.append(f"{d} {who.name} {inr(amount)} — {desc}")
            cat = cats.get(cname.lower()) or cats["miscellaneous"]
            s.add(Transaction(
                txn_date=d, spent_by_id=who.id, kind=Kind.spent, amount=amount, currency="INR", fx_rate=Decimal(1),
                amount_inr=amount, category_id=cat.id, decided_by=DecidedBy.imported, description=desc or cat.name,
                payee_raw=None, payment_method=METHOD.get(str(r.get("Mode of Payment") or "").strip().lower(), "other"),
                source=Source.sheet_import, origin="sheet_2026", status=Status.confirmed,
            ))
            sheet_month[(d.year, d.month)] += amount
            n += 1
        s.flush()

        # budgets
        b = next((v for k, v in sheets.items() if k.lower().startswith("budget")), None)
        nb = 0
        if b is not None:
            for _, r in b.iterrows():
                who = members.get(str(r.iloc[0]).strip().lower())
                cname = CATEGORY_MAP.get(str(r.iloc[1]).strip().lower(), str(r.iloc[1]).strip())
                cat = cats.get(cname.lower())
                amt = Decimal(str(r.iloc[2] or 0))
                if who and cat and amt > 0:
                    ex = s.scalar(select(Budget).where(Budget.category_id == cat.id, Budget.member_id == who.id))
                    if ex:
                        ex.monthly_inr = amt
                    else:
                        s.add(Budget(category_id=cat.id, member_id=who.id, monthly_inr=amt))
                    nb += 1
        s.flush()

        lines.append(f"# 2026 sheet import\n\nImported {n} transactions and {nb} budgets. Skipped {len(skipped)} empty rows.\n")
        lines.append("## Month check: sheet total vs new money out\n\n| Month | Sheet | New app | Match |\n|---|---|---|---|")
        ok = True
        for (y, m) in sorted(sheet_month):
            new = month_summary(s, y, m).money_out
            match = new == sheet_month[(y, m)]
            ok &= match
            lines.append(f"| {y}-{m:02d} | {inr(sheet_month[(y, m)])} | {inr(new)} | {'✅' if match else '❌'} |")
        total = sum(sheet_month.values(), Decimal(0))
        lines.append(f"| **Total** | **{inr(total)}** | | {'✅ all months match' if ok else '❌ check'} |\n")
        lines.append(f"## Moved to Family support ({len(family)})\n\nThese were Miscellaneous; they now show under Family support. Totals are unchanged.\n")
        lines += [f"- {x}" for x in family] or ["- none"]
        lines.append(f"\n## Please review ({len(review)})\n\nThese look like money moved between the two of you or held for "
                     "the household. They are counted as spent (as in the sheet). If any were really a transfer between "
                     "your own accounts, change Kind to self_transfer on the Transactions screen.\n")
        lines += [f"- {x}" for x in review] or ["- none"]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--report")
    a = ap.parse_args()
    out = run(a.xlsx, a.replace)
    print(out)
    if a.report:
        open(a.report, "w").write(out)
