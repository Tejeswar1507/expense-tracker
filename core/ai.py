"""Optional: guess a category for a brand-new payee with Claude Haiku (Claude API, pay-as-you-go).

Off unless ANTHROPIC_API_KEY is set. A Claude Pro/Max subscription cannot be used for this — API
usage is billed separately in the Claude Console. Capped per day (AI_DAILY_CAP, default 50).
Only confident guesses (>= 0.8) are applied, and they are marked "ai" so you can review them.
"""
from __future__ import annotations

import json
from datetime import date

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import settings
from core.db import Category, DecidedBy, Status, Transaction, get_setting, set_setting

MODEL = "claude-haiku-4-5"


def _ask(prompt: str, key: str) -> dict | None:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 150, "messages": [{"role": "user", "content": prompt}]},
        timeout=30,
    )
    if not r.ok:
        return None
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    try:
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError:
        return None


def try_guess(s: Session, t: Transaction) -> bool:
    cfg = settings()
    if not cfg.anthropic_api_key or (t.payee and t.payee.always_ask):
        return False
    key = f"ai_calls_{date.today().isoformat()}"
    used = int(get_setting(s, key, "0") or 0)
    if used >= cfg.ai_daily_cap:
        return False
    cats = [c.name for c in s.scalars(select(Category).where(Category.active == True))]  # noqa: E712
    prompt = (
        "An Indian household's UPI/bank payment. Pick the best category.\n"
        f"Payee: {t.payee_raw!r}. UPI ID: {t.payee.match_text if t.payee else ''!r}. Amount: INR {t.amount_inr}.\n"
        f"Categories: {json.dumps(cats)}\n"
        'Reply with JSON only: {"category": "<one of the list>", "confidence": <0..1>}'
    )
    set_setting(s, key, str(used + 1))
    res = _ask(prompt, cfg.anthropic_api_key)
    if not res or res.get("category") not in cats or float(res.get("confidence", 0)) < 0.8:
        return False
    cat = s.scalar(select(Category).where(Category.name == res["category"]))
    t.category_id, t.decided_by, t.status = cat.id, DecidedBy.ai, Status.confirmed
    return True
