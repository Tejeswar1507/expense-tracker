"""Shared helpers for the Streamlit screens: sign-in, the current member, formatting."""
from __future__ import annotations

import os
from datetime import date, datetime

import streamlit as st
from sqlalchemy import select

from core.calc import inr  # noqa: F401  (re-exported for pages)
from core.config import settings
from core.db import Category, Member, Role, init_db, member_by_email, session_scope
from core.seed import seed


@st.cache_resource
def _bootstrap() -> bool:
    init_db()
    with session_scope() as s:
        seed(s)
    return True


def _auth_configured() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def signed_in_email() -> str | None:
    if _auth_configured():
        if not st.user.is_logged_in:
            st.title("Expense Tracker")
            st.write("Sign in with the Google account you use for the tracker.")
            if st.button("Sign in with Google", type="primary"):
                st.login()
            st.stop()
        return (st.user.get("email") or "").lower()
    # Local development only: no Google sign-in configured
    if os.environ.get("ALLOW_DEV_LOGIN") == "1":
        cfg = settings()
        return (cfg.dev_user_email or cfg.admin_email or "admin@example.com").lower()
    st.error("Google sign-in is not configured. Add the [auth] section to Streamlit secrets (SETUP.md, step 4).")
    st.stop()


def current_member() -> Member:
    _bootstrap()
    email = signed_in_email()
    with session_scope() as s:
        m = member_by_email(s, email or "")
        if not m:
            st.error(f"{email} is not allowed to use this tracker.")
            if _auth_configured() and st.button("Sign out"):
                st.logout()
            st.stop()
        s.expunge(m)
        return m


def is_admin(m: Member) -> bool:
    return m.role == Role.admin


def today_local() -> date:
    return datetime.now(settings().tz).date()


def categories(s, active_only: bool = True) -> list[Category]:
    q = select(Category).order_by(Category.group, Category.name)
    if active_only:
        q = q.where(Category.active == True)  # noqa: E712
    return list(s.scalars(q))


def members(s) -> list[Member]:
    return list(s.scalars(select(Member).order_by(Member.role, Member.name)))


def month_options(n: int = 24) -> list[tuple[int, int]]:
    t = today_local()
    out, y, m = [], t.year, t.month
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out
