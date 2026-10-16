"""Read bank / card statements (CSV, Excel, PDF) into ParsedTxn rows.

Columns are found by their header words, so most Indian bank exports work without a per-bank template:
date · narration/description/particulars · withdrawal/debit · deposit/credit · (or amount + Dr/Cr) · ref.
UPI narrations are mined for the payee, UPI ID, 12-digit reference and the note you typed, e.g.
  HDFC:  UPI-SWIGGY-swiggy@icici-ICIC0DC0099-431829117765-dinner
  SBI:   UPI/DR/431829117765/RAMESH K/SBIN/ramesh.k@oksbi/vegetables
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

from core.parsers.alerts import ParsedTxn, parse_date

HEADER_WORDS = {
    "date": ["txn date", "transaction date", "value date", "date", "posting date"],
    "narration": ["narration", "description", "particulars", "remarks", "details", "transaction details", "merchant"],
    "debit": ["withdrawal amt", "withdrawal amount", "withdrawal", "debit amount", "debit", "dr amount", "amount (dr)"],
    "credit": ["deposit amt", "deposit amount", "deposit", "credit amount", "credit", "cr amount", "amount (cr)"],
    "amount": ["amount", "transaction amount", "amount (inr)", "amount(inr)", "amount (usd)"],
    "drcr": ["dr/cr", "cr/dr", "debit/credit", "type", "transaction type"],
    "ref": ["chq./ref.no.", "ref no", "ref no./cheque no.", "reference", "utr", "chq/ref number", "ref"],
    "currency": ["currency"],
}


@dataclass
class StatementRow:
    parsed: ParsedTxn
    raw: str


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _num(v) -> Decimal | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).replace(",", "").replace("₹", "").replace("INR", "").replace("$", "").strip()
    s = s.replace("(", "-").replace(")", "")
    if s in ("", "-", "nan", "None"):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _find_header_row(df: pd.DataFrame) -> pd.DataFrame:
    """Bank exports often have a few title lines before the real header row."""
    def score(cells) -> int:
        cells = [str(c).strip().lower() for c in cells]
        return sum(any(c == w or c.startswith(w) for w in ws) for c in cells for ws in HEADER_WORDS.values())

    if score(df.columns) >= 2:
        return df
    for i in range(min(len(df), 30)):
        if score(df.iloc[i].tolist()) >= 2:
            new = df.iloc[i + 1:].copy()
            new.columns = [str(c).strip() for c in df.iloc[i].tolist()]
            return new.reset_index(drop=True)
    return df


def _map_columns(columns) -> dict[str, str]:
    mapping: dict[str, str] = {}
    lowered = {c: str(c).strip().lower() for c in columns}
    for field, words in HEADER_WORDS.items():
        for w in words:  # most specific words first
            for col, low in lowered.items():
                if col in mapping.values():
                    continue
                if low == w or low.startswith(w):
                    mapping.setdefault(field, col)
            if field in mapping:
                break
    return mapping


UPI_HDFC = re.compile(r"^UPI-(?P<name>[^-]*)-(?P<vpa>[^-]+@[^-]+)-(?P<ifsc>[^-]*)-(?P<ref>\d{12})-?(?P<note>.*)$", re.I)
UPI_SLASH = re.compile(r"^UPI/(?:DR|CR)?/?(?P<ref>\d{12})/(?P<name>[^/]*)/(?P<bank>[^/]*)/(?P<vpa>[^/]*@[^/]*)/?(?P<note>.*)$", re.I)


def parse_narration(text: str) -> dict:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    out: dict = {"payee_name": None, "payee_vpa": None, "upi_ref": None, "note": None, "method": None, "atm": False}
    for rx in (UPI_HDFC, UPI_SLASH):
        m = rx.match(t)
        if m:
            out.update(payee_name=m["name"].strip() or None, payee_vpa=m["vpa"].strip().lower(), upi_ref=m["ref"],
                       method="upi")
            note = (m["note"] or "").strip(" -/")
            if note and not re.fullmatch(r"(upi|payment from phone|pay to .*|sent using .*|na|null)", note, re.I):
                out["note"] = note
            return out
    if re.search(r"\bATM\b|\bATW\b|NWD|CASH WDL|cash withdrawal", t, re.I):
        out.update(atm=True, method="cash", payee_name="ATM")
        return out
    vpa = re.search(r"([\w.\-]+@[a-z][a-z0-9]+)", t, re.I)
    ref = re.search(r"(?<!\d)(\d{12})(?!\d)", t)
    out["payee_vpa"] = vpa.group(1).lower() if vpa else None
    out["upi_ref"] = ref.group(1) if ref else None
    if re.search(r"\bupi\b", t, re.I):
        out["method"] = "upi"
    elif re.search(r"\b(neft|imps|rtgs)\b", t, re.I):
        out["method"] = "netbanking"
    elif re.search(r"\b(pos|card|ecom)\b", t, re.I):
        out["method"] = "card"
    name = re.sub(r"(UPI|NEFT|IMPS|RTGS|POS|ECOM)[/\- ]*", "", t, flags=re.I)
    name = re.sub(r"[\d/\-*]{6,}", " ", name)
    out["payee_name"] = re.sub(r"\s+", " ", name).strip(" -/")[:80] or None
    return out


def read_table(data: bytes, file_name: str) -> pd.DataFrame:
    name = file_name.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data), header=None, dtype=str, skip_blank_lines=True, on_bad_lines="skip")
        df.columns = [str(c) for c in df.iloc[0]]
        df = df.iloc[1:]
    elif name.endswith((".xls", ".xlsx")):
        df = pd.read_excel(io.BytesIO(data), header=None, dtype=str)
        df.columns = [str(c) for c in df.iloc[0]]
        df = df.iloc[1:]
    elif name.endswith(".pdf"):
        df = _read_pdf(data)
    else:
        raise ValueError("Upload a CSV, Excel or PDF statement")
    return _find_header_row(df.reset_index(drop=True))


def _read_pdf(data: bytes) -> pd.DataFrame:
    import pdfplumber

    rows: list[list] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                rows.extend(table)
    if not rows:
        raise ValueError("No table found in this PDF. If it is a scanned image, download the CSV/Excel version instead.")
    width = max(len(r) for r in rows)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    df = pd.DataFrame(rows[1:], columns=[str(c) for c in rows[0]])
    return df


def parse_statement(data: bytes, file_name: str, default_currency: str = "INR") -> tuple[list[StatementRow], list[str]]:
    df = read_table(data, file_name)
    cols = _map_columns(df.columns)
    problems: list[str] = []
    if "date" not in cols or not ({"debit", "credit"} & cols.keys() or "amount" in cols):
        raise ValueError(f"Could not find date and amount columns. Columns seen: {list(df.columns)[:12]}")
    rows: list[StatementRow] = []
    for _, r in df.iterrows():
        d = parse_date(str(r[cols["date"]]))
        if not d:
            continue  # totals, blank and footer lines
        debit = _num(r[cols["debit"]]) if "debit" in cols else None
        credit = _num(r[cols["credit"]]) if "credit" in cols else None
        if debit is None and credit is None and "amount" in cols:
            amt = _num(r[cols["amount"]])
            if amt is None:
                continue
            drcr = str(r[cols["drcr"]]).lower() if "drcr" in cols else ""
            if "cr" in drcr or "credit" in drcr or amt < 0 and "dr" not in drcr:
                credit = abs(amt)
            else:
                debit = abs(amt)
        if not debit and not credit:
            continue
        narration = str(r[cols["narration"]]) if "narration" in cols else ""
        info = parse_narration(narration)
        currency = (str(r[cols["currency"]]).strip().upper()[:3] if "currency" in cols else default_currency) or default_currency
        p = ParsedTxn(
            amount=abs(debit or credit),
            direction="debit" if debit else "credit",
            txn_date=d,
            currency=currency if currency in ("INR", "USD") else default_currency,
            payee_name=info["payee_name"],
            payee_vpa=info["payee_vpa"],
            upi_ref=info["upi_ref"] or (str(r[cols["ref"]]).strip() if "ref" in cols and re.fullmatch(r"\d{12}", str(r[cols["ref"]]).strip()) else None),
            note=info["note"],
            payment_method=info["method"] or "other",
            is_atm=info["atm"] and bool(debit),
            is_refund=bool(credit) and bool(re.search(r"refund|reversal|rev\b", narration, re.I)),
            is_reversal=bool(credit) and bool(re.search(r"reversal|reversed|\brev\b", narration, re.I)),
            template="statement",
        )
        rows.append(StatementRow(parsed=p, raw=narration))
    if not rows:
        problems.append("No transactions found in the file.")
    return rows, problems
