"""USD → INR at the rate on the transaction date (never the upload date). Rates are saved once in fx_rates."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import FxRate

Fetcher = Callable[[date, str], Decimal | None]


def frankfurter_fetch(d: date, currency: str) -> Decimal | None:
    """Free, no key. For weekends/holidays Frankfurter returns the previous working day's rate."""
    urls = [
        f"https://api.frankfurter.app/{d.isoformat()}?from={currency}&to=INR",
        f"https://api.frankfurter.dev/v1/{d.isoformat()}?base={currency}&symbols=INR",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=15)
            if r.ok:
                rate = r.json().get("rates", {}).get("INR")
                if rate:
                    return Decimal(str(rate))
        except requests.RequestException:
            continue
    return None


def inr_rate(s: Session, d: date, currency: str, fetch: Fetcher = frankfurter_fetch) -> Decimal:
    currency = currency.upper()
    if currency == "INR":
        return Decimal("1")
    # saved rate for that day, or the closest earlier saved day within a week (weekends/holidays)
    row = s.scalar(
        select(FxRate)
        .where(FxRate.currency == currency, FxRate.rate_date <= d, FxRate.rate_date >= d - timedelta(days=6))
        .order_by(FxRate.rate_date.desc())
    )
    if row and row.rate_date == d:
        return Decimal(row.inr_per_unit)
    fetched = fetch(d, currency)
    if fetched:
        s.add(FxRate(rate_date=d, currency=currency, inr_per_unit=fetched))
        s.flush()
        return fetched
    if row:
        return Decimal(row.inr_per_unit)
    raise RuntimeError(f"No {currency}→INR rate available for {d}. Add one on the Admin screen.")


def to_inr(amount: Decimal, rate: Decimal) -> Decimal:
    return (Decimal(amount) * Decimal(rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
