"""Read Indian bank / UPI / card alert emails (or SMS text) into a ParsedTxn.

Bank wordings differ, so instead of one template per bank this parser looks for the parts every
alert has (amount, debit/credit, account, payee, UPI reference, date) with tolerant patterns.
Known wordings it is tested on (see tests/test_alert_parser.py): HDFC, ICICI, SBI, Axis, Kotak UPI
alerts, card spends, ATM withdrawals, credits, reversals, and Razorpay payment receipts.
If a real email isn't understood it is kept as "failed" and shown on the Admin screen — never lost.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


@dataclass
class ParsedTxn:
    amount: Decimal
    direction: str  # "debit" or "credit"
    txn_date: date | None = None
    currency: str = "INR"
    account_last4: str | None = None
    payee_name: str | None = None
    payee_vpa: str | None = None
    payer_vpa: str | None = None  # your own UPI ID, when the email names it (receipts)
    upi_ref: str | None = None
    note: str | None = None
    payment_method: str | None = None  # upi | card | netbanking | cash | other
    is_atm: bool = False
    is_refund: bool = False
    is_reversal: bool = False
    template: str = "generic"
    warnings: list[str] = field(default_factory=list)


class NotAPayment(Exception):
    """Offers, OTPs, 'statement ready' and other non-payment emails."""


class CannotParse(Exception):
    """Looks like a payment alert but a required part (amount/direction) wasn't found."""


# --------------------------------------------------------------------------- helpers

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def clean_text(text: str) -> str:
    if "<" in text and ">" in text:
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</td>", " ", text)
        text = _TAG.sub(" ", text)
    text = html.unescape(text).replace(" ", " ").replace("‌", "").replace("​", "")
    return _WS.sub(" ", text).strip()


_AMOUNT = re.compile(
    r"(?P<cur>INR|Rs\.?|₹|USD|US\$|\$)\s*(?P<num>\d[\d,]*(?:\.\d{1,2})?)"
    r"|(?P<num2>\d[\d,]*(?:\.\d{1,2})?)\s*(?P<cur2>INR|USD)\b",
    re.I,
)
_BARE_AMOUNT = re.compile(r"\b(?:debited|credited|withdrawn|sent|paid)\s+(?:by|with|for|of)?\s*(\d[\d,]*(?:\.\d{1,2})?)\b", re.I)
_DEBIT_WORDS = re.compile(
    r"\b(debited|has been debited|is debited|spent|paid|sent|withdrawn|withdrawal|purchase|deducted|"
    r"used for|txn of|transaction of|payment of|charged|a charge of|charge of)\b",
    re.I,
)
_CREDIT_WORDS = re.compile(r"\b(credited|received|deposited|refund(?:ed)?|reversed|reversal|cashback)\b", re.I)
_VPA = re.compile(r"(?<![\w.@-])([a-z0-9][\w.\-]{1,60}@[a-z][a-z0-9]{1,20})(?![\w@-])", re.I)
_EMAIL_DOMAINS = re.compile(r"@(gmail|yahoo|outlook|hotmail|icloud|rediffmail|live)\b", re.I)
_ACCT = re.compile(
    r"(?:a/?c|acct|account|card)\s*(?:no\.?|number)?\s*(?:ending\s*(?:with|in)?)?\s*[:#]?\s*"
    r"(?:[xX*.\s]*)(\d{3,4})\b",
    re.I,
)
_REF_LABELLED = re.compile(
    r"(?:UPI\s*(?:transaction\s*)?(?:ref(?:erence)?\.?\s*(?:no\.?|number|id)?(?:\s*is)?|txn\s*id)|"
    r"RRN|UTR|Ref\.?\s*No\.?|Refno|Reference\s*(?:No\.?|Number)?(?:\s*is)?)\s*[:.\-]?\s*(\d{12})",
    re.I,
)
_REF_ANY = re.compile(r"(?<!\d)(\d{12})(?!\d)")
_NOTE = re.compile(r"(?:remarks?|note|message|purpose)\s*[:\-]\s*(.{1,80}?)(?:\.\s|$|\s{2})", re.I)

_DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"), "dmy4"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2})\b"), "dmy2"),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[-\s]?([A-Za-z]{3})[a-z]*[,\-\s]+(\d{2,4})\b"), "dMy"),
    (re.compile(r"\b(\d{2})([A-Za-z]{3})(\d{2})\b"), "dMy_compact"),  # SBI: 14Oct26
    (re.compile(r"\b([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s+(\d{4})\b"), "Mdy"),
]


def _to_decimal(num: str) -> Decimal | None:
    try:
        return Decimal(num.replace(",", ""))
    except InvalidOperation:
        return None


def parse_date(text: str, received: date | None = None) -> date | None:
    for rx, kind in _DATE_PATTERNS:
        for m in rx.finditer(text):
            try:
                if kind == "dmy4":
                    d = date(int(m[3]), int(m[2]), int(m[1]))
                elif kind == "ymd":
                    d = date(int(m[1]), int(m[2]), int(m[3]))
                elif kind == "dmy2":
                    d = date(2000 + int(m[3]), int(m[2]), int(m[1]))
                elif kind in ("dMy", "dMy_compact"):
                    mon = MONTHS.get(m[2][:3].lower())
                    if not mon:
                        continue
                    y = int(m[3])
                    d = date(y + 2000 if y < 100 else y, mon, int(m[1]))
                else:  # Mdy
                    mon = MONTHS.get(m[1][:3].lower())
                    if not mon:
                        continue
                    d = date(int(m[3]), mon, int(m[2]))
            except ValueError:
                continue
            # sanity: an alert is about a recent payment, never far in the future
            if received and (d > received.replace(year=received.year) and (d - received).days > 2):
                continue
            if d.year < 2015:
                continue
            return d
    return None


def _clean_payee(name: str | None) -> str | None:
    if not name:
        return None
    name = re.sub(r"\b(on|dated|ref|refno|upi|info|avl|available|bal|balance|if|not|you|thank).*$", "", name, flags=re.I)
    name = re.sub(r"[^\w&'.\- ]", " ", name)
    name = _WS.sub(" ", name).strip(" .-")
    return name[:80] or None


# --------------------------------------------------------------------------- main entry


def parse_alert(text: str, subject: str = "", received: date | None = None) -> ParsedTxn:
    body = clean_text(text)
    subj = clean_text(subject or "")
    full = f"{subj} . {body}"
    low = full.lower()

    if re.search(r"\b(otp|one[- ]time password|verification code)\b", low) and not re.search(
        r"\b(debited|credited)\b", low
    ):
        raise NotAPayment("OTP or verification email")
    if re.search(r"statement (is )?(ready|generated)|e-?statement|offer|cashback offer|apply now|pre-approved", low) and not re.search(
        r"\b(debited|credited|paid successfully|withdrawn)\b", low
    ):
        raise NotAPayment("statement notice or promotion")

    if "razorpay" in low or re.search(r"payment successful for|refund successful for", low):
        return _parse_receipt(subj, body, received)

    m = _AMOUNT.search(body) or _AMOUNT.search(subj)
    if m:
        num = m.group("num") or m.group("num2")
        cur = (m.group("cur") or m.group("cur2") or "INR").upper()
    else:
        # SBI style: "debited by 200.0"
        bare = _BARE_AMOUNT.search(body)
        if not bare:
            raise NotAPayment("no amount found")
        num, cur = bare.group(1), "INR"
    amount = _to_decimal(num)
    if amount is None or amount <= 0:
        raise CannotParse("amount unreadable")
    currency = "USD" if cur in ("USD", "US$", "$") else "INR"

    d_hit, c_hit = _DEBIT_WORDS.search(full), _CREDIT_WORDS.search(full)
    if not d_hit and not c_hit:
        raise CannotParse("could not tell debit from credit")
    if d_hit and c_hit:
        direction = "debit" if d_hit.start() <= c_hit.start() else "credit"
    else:
        direction = "debit" if d_hit else "credit"

    p = ParsedTxn(amount=amount, direction=direction, currency=currency)
    p.is_atm = bool(re.search(r"\batm\b|cash withdrawal|withdrawn at", low)) and direction == "debit"
    p.is_reversal = bool(re.search(r"\breversed\b|\breversal\b|failed transaction", low)) and direction == "credit"
    p.is_refund = bool(re.search(r"\brefund", low)) and direction == "credit"

    acct = _ACCT.search(full)
    if acct:
        p.account_last4 = acct.group(1)[-4:]

    # UPI IDs: ignore plain email addresses (gmail etc.)
    vpas = [v for v in _VPA.findall(body) if not _EMAIL_DOMAINS.search(v) and "." not in v.split("@")[1]]
    if vpas:
        target = None
        if direction == "debit":
            t = re.search(r"(?:to|trf to|credited to)\s*(?:vpa|upi id)?\s*[:\-]?\s*([\w.\-]+@[a-z][a-z0-9]+)", body, re.I)
        else:
            t = re.search(r"(?:by|from)\s*(?:vpa|upi id)?\s*[:\-]?\s*([\w.\-]+@[a-z][a-z0-9]+)", body, re.I)
        target = t.group(1) if t else vpas[0]
        p.payee_vpa = target.lower()

    p.payee_name = _find_payee_name(body, p.payee_vpa, direction)
    ref = _REF_LABELLED.search(full) or _REF_ANY.search(body)
    if ref:
        p.upi_ref = ref.group(1)
    note = _NOTE.search(body)
    if note:
        p.note = note.group(1).strip()

    if p.is_atm:
        p.payment_method = "cash"
    elif p.payee_vpa or re.search(r"\bupi\b", low):
        p.payment_method = "upi"
    elif re.search(r"credit card|debit card|\bcard\b", low):
        p.payment_method = "card"
    elif re.search(r"\b(neft|imps|rtgs|net ?banking)\b", low):
        p.payment_method = "netbanking"
    else:
        p.payment_method = "other"

    p.txn_date = parse_date(body, received) or received
    if not parse_date(body, received):
        p.warnings.append("date not in text; used the email's received date")
    p.template = _guess_bank(low)
    return p


def _find_payee_name(body: str, vpa: str | None, direction: str) -> str | None:
    candidates = []
    if vpa:
        # HDFC: "to VPA swiggy@icici SWIGGY on 14-11-26"
        m = re.search(re.escape(vpa) + r"\s*\(?\s*([A-Za-z][A-Za-z0-9&'. \-]{1,60}?)\)?\s+(?:on|dated|ref|upi|\.)", body, re.I)
        if m:
            candidates.append(m.group(1))
    pats = [
        r"\btrf to\s+([A-Za-z][A-Za-z0-9&'. \-]{1,60}?)\s+(?:ref|on|upi|\.)",  # SBI
        r"\bat\s+([A-Za-z0-9][A-Za-z0-9&'*. \-]{1,60}?)\s+(?:on|dated|\.|via|using|for)",  # card spends
        r"\b(?:paid|sent) to\s+([A-Za-z][A-Za-z0-9&'. \-]{1,60}?)\s+(?:on|via|using|ref|\.|upi)",
        r"\bto\s+([A-Z][A-Z0-9&'. \-]{2,60}?)\s+(?:on|ref|upi|\.)",  # uppercase merchant names
        r"\b(?:from|by)\s+([A-Za-z][A-Za-z0-9&'. \-]{1,60}?)\s+(?:on|ref|upi|\.|via)",
        r"Info:\s*(?:UPI|IMPS|NEFT)?[/\-]?(?:\d{12})?[/\-]?([A-Za-z][A-Za-z0-9&'. \-]{1,60}?)(?:[/\-]|\s+on|\.)",
    ]
    for rx in pats:
        m = re.search(rx, body, re.I if "A-Z][A-Z0-9" not in rx else 0)
        if m:
            candidates.append(m.group(1))
    for c in candidates:
        name = _clean_payee(c)
        if name and not re.fullmatch(r"(vpa|account|a/c|your|the|upi|you|ac|acct|card|xx\d*)", name, re.I) and not re.fullmatch(r"[\dXx* ]+", name):
            return name
    if vpa:
        return vpa.split("@")[0]
    return None


def _guess_bank(low: str) -> str:
    for bank in ("hdfc", "icici", "sbi", "axis", "kotak", "yes bank", "idfc", "pnb", "bank of baroda", "canara", "union bank", "indusind", "federal"):
        if bank in low:
            return bank.replace(" ", "_")
    return "generic"


def _parse_receipt(subj: str, body: str, received: date | None) -> ParsedTxn:
    """Razorpay-style merchant receipts: 'Payment successful for Hostinger' / 'Refund successful for …'."""
    refund = bool(re.search(r"refund", subj + " " + body[:200], re.I))
    m = re.search(r"(?:payment|refund) successful for\s+(.+)", subj, re.I)
    merchant = m.group(1).strip() if m else None
    am = _AMOUNT.search(body)
    if not am:
        raise CannotParse("receipt without amount")
    amount = _to_decimal(am.group("num") or am.group("num2"))
    p = ParsedTxn(amount=amount, direction="credit" if refund else "debit", template="receipt")
    p.payee_name = merchant
    p.is_refund = refund
    pv = re.search(r"Method\s+UPI\s+([\w.\-]+@[a-z][a-z0-9]+)", body, re.I)
    if pv:
        p.payer_vpa = pv.group(1).lower()
        p.payment_method = "upi"
    elif re.search(r"Method\s+Card", body, re.I):
        p.payment_method = "card"
    ref = re.search(r"\bRRN\b[^\d]{0,20}(\d{12})", body) or re.search(r"(?<!\d)(\d{12})\s*RRN", body)
    if ref:
        p.upi_ref = ref.group(1)
    dm = re.search(r"Paid On\s+(.+?\d{4})", body, re.I)
    p.txn_date = (parse_date(dm.group(1)) if dm else None) or parse_date(body, received) or received
    return p
