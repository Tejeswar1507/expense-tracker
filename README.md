# Expense Tracker (v2)

A household expense tracker for two people, built for UPI-heavy Indian spending.
It reads bank and UPI alert emails every hour, saves each payment exactly once, asks the person who spent it
only when it can't tell what a payment was, and shows every rupee you both spend in a calendar-year dashboard.
Only the admin can edit. Runs on free tiers (₹0/month).

**First time? → [SETUP.md](SETUP.md)** (about 30 minutes).

## How it works
```
Wife's Gmail ──auto-forward──▶ Your Gmail (label Bank-Alerts) ──▶ hourly job (GitHub Actions)
Statement upload, Quick add (cash) ─────────────────────────────▶ web app (Streamlit)
                         both use the same Python core ──▶ Postgres (Neon)
```
1. **Read** amount, payee, UPI ID, 12-digit UPI reference, account, date from the email (`core/parsers/alerts.py`).
2. **Same UPI reference already saved?** → merge (two emails for one payment become one record).
3. **Same amount/date from another source?** → merge (receipt + bank email, email + statement).
4. **Paid to your own account?** → self-transfer, not counted (card bills, transfers between you, ATM cash).
5. **Merchant rule or known payee?** → category set. Otherwise it waits in **What was this?**
   and the person who spent it gets a 9 PM email. Their answer teaches the app that payee.

**Money out** = every rupee spent (incl. family support, investments, EMIs) − refunds.
Self-transfers and failed payments are not counted. USD is converted at the rate on the transaction date.

## Screens
Month · Year · What was this? · Quick add · Transactions · Admin (statements, 2026 sheet import, payees, rules,
budgets, accounts, emails, cash, change log).

## Who can do what
| | Admin (you) | Member (wife) |
|---|---|---|
| View everything | ✅ | ✅ |
| Quick add cash | ✅ (anyone) | ✅ (her own) |
| Explain a waiting payment | ✅ (any) | ✅ (her own, while waiting) |
| Edit / delete / settings / statements | ✅ | ❌ — enforced in `core/services.py` |

## Code
```
app/            Streamlit screens (main.py + pages/)
core/           parsers, ingest (dedupe), categorize, fx, calc, services (role checks), notify, google_api, db
jobs/hourly.py  what GitHub runs every hour
scripts/        import_2026_sheet.py, get_google_token.py
tests/          27 tests on Postgres (parsers, the worked month = ₹40,920, roles, statements, emails, import)
```

## Run locally
```bash
pip install -r requirements.txt pytest
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/expense ALLOW_DEV_LOGIN=1 \
       ADMIN_EMAIL=you@gmail.com MEMBER_EMAIL=wife@gmail.com
streamlit run app/main.py          # web app (dev login, no Google needed)
python -m jobs.hourly --dry-run    # hourly job without sending anything
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/expense_test pytest -q
```

## Adding a new bank email format
Paste a masked example into `tests/test_alert_parser.py` with the expected values, adjust `core/parsers/alerts.py`
until it passes, push, then Admin → Emails → *Re-read failed emails*.
