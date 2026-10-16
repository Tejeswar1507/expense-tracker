"""Settings, read from environment variables (GitHub Actions) or Streamlit secrets (web app)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


def _get(name: str, default: str | None = None) -> str | None:
    if name in os.environ:
        return os.environ[name]
    try:  # Streamlit secrets, only available inside the web app
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _get("DATABASE_URL", "sqlite:///local.db"))
    admin_email: str = field(default_factory=lambda: (_get("ADMIN_EMAIL", "") or "").lower())
    admin_name: str = field(default_factory=lambda: _get("ADMIN_NAME", "Teja"))
    member_email: str = field(default_factory=lambda: (_get("MEMBER_EMAIL", "") or "").lower())
    member_name: str = field(default_factory=lambda: _get("MEMBER_NAME", "Babbi"))
    timezone: str = field(default_factory=lambda: _get("TIMEZONE", "Asia/Kolkata"))
    gmail_label: str = field(default_factory=lambda: _get("GMAIL_LABEL", "Bank-Alerts"))
    google_client_id: str | None = field(default_factory=lambda: _get("GOOGLE_CLIENT_ID"))
    google_client_secret: str | None = field(default_factory=lambda: _get("GOOGLE_CLIENT_SECRET"))
    google_refresh_token: str | None = field(default_factory=lambda: _get("GOOGLE_REFRESH_TOKEN"))
    app_url: str = field(default_factory=lambda: _get("APP_URL", "http://localhost:8501"))
    anthropic_api_key: str | None = field(default_factory=lambda: _get("ANTHROPIC_API_KEY"))
    ai_daily_cap: int = field(default_factory=lambda: int(_get("AI_DAILY_CAP", "50")))
    dev_user_email: str | None = field(default_factory=lambda: _get("DEV_USER_EMAIL"))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def settings() -> Settings:
    return Settings()
