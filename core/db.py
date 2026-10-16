"""Database tables (SQLAlchemy 2) and session helpers.

One row in `transactions` is one real payment. Everything else describes it or records where it came from.
Works on PostgreSQL (production, Neon) and SQLite (local demo).
"""
from __future__ import annotations

import enum
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Iterator, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from core.config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


Money = Numeric(14, 2)


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    admin = "admin"
    member = "member"


class Kind(str, enum.Enum):
    spent = "spent"
    refund = "refund"
    income = "income"
    self_transfer = "self_transfer"
    failed = "failed"


class Status(str, enum.Enum):
    waiting = "waiting"  # needs a category ("What was this?")
    confirmed = "confirmed"
    deleted = "deleted"  # in the bin, restorable


class Source(str, enum.Enum):
    email = "email"
    statement = "statement"
    quick_add = "quick_add"
    sheet_import = "sheet_import"
    manual = "manual"


class DecidedBy(str, enum.Enum):
    own_account = "own_account"
    merchant_rule = "merchant_rule"
    learned_payee = "learned_payee"
    person = "person"
    ai = "ai"
    imported = "imported"


class Member(Base):
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    google_email: Mapped[str] = mapped_column(String(200), unique=True)
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False))


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    name: Mapped[str] = mapped_column(String(80))  # "HDFC savings", "Cash wallet"
    bank: Mapped[Optional[str]] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(10), default="bank")  # bank | card | cash
    last4: Mapped[Optional[str]] = mapped_column(String(4))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    own_upi_ids: Mapped[Optional[str]] = mapped_column(Text)  # comma separated, lower case
    member: Mapped[Member] = relationship()


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    group: Mapped[str] = mapped_column(String(40))  # Living | Family support | Savings and investments | Loans and EMIs
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("category_id", "member_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))  # null = both of you
    monthly_inr: Mapped[Decimal] = mapped_column(Money)
    category: Mapped[Category] = relationship()
    member: Mapped[Optional[Member]] = relationship()


class Payee(Base):
    __tablename__ = "payees"
    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    match_text: Mapped[str] = mapped_column(String(200), unique=True)  # UPI ID or cleaned name, lower case
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"))
    always_ask: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[Optional[Category]] = relationship()


class MerchantRule(Base):
    __tablename__ = "merchant_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    keyword: Mapped[str] = mapped_column(String(60), unique=True)  # lower case, matched inside payee name or UPI ID
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    kind: Mapped[Kind] = mapped_column(Enum(Kind, native_enum=False), default=Kind.spent)
    always_ask: Mapped[bool] = mapped_column(Boolean, default=False)  # e.g. Amazon: suggest, but ask a person
    category: Mapped[Category] = relationship()


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("upi_ref", name="uq_transactions_upi_ref"),
        Index("ix_transactions_date", "txn_date"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    txn_date: Mapped[date] = mapped_column(Date)
    spent_by_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[Kind] = mapped_column(Enum(Kind, native_enum=False))
    payee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("payees.id"))
    payee_raw: Mapped[Optional[str]] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1"))
    amount_inr: Mapped[Decimal] = mapped_column(Money)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"))
    decided_by: Mapped[Optional[DecidedBy]] = mapped_column(Enum(DecidedBy, native_enum=False))
    description: Mapped[Optional[str]] = mapped_column(Text)
    payment_method: Mapped[Optional[str]] = mapped_column(String(20))  # upi | card | netbanking | cash | other
    upi_ref: Mapped[Optional[str]] = mapped_column(String(20))
    is_cash_withdrawal: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[Source] = mapped_column(Enum(Source, native_enum=False))
    origin: Mapped[Optional[str]] = mapped_column(String(30))  # parser template: hdfc, sbi, receipt, statement...
    status: Mapped[Status] = mapped_column(Enum(Status, native_enum=False))
    added_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    edited_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    spent_by: Mapped[Member] = relationship(foreign_keys=[spent_by_id])
    account: Mapped[Optional[Account]] = relationship()
    payee: Mapped[Optional[Payee]] = relationship()
    category: Mapped[Optional[Category]] = relationship()


class RawEmail(Base):
    __tablename__ = "raw_emails"
    id: Mapped[int] = mapped_column(primary_key=True)
    gmail_message_id: Mapped[str] = mapped_column(String(64), unique=True)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sender: Mapped[Optional[str]] = mapped_column(String(200))
    subject: Mapped[Optional[str]] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    forwarded_from: Mapped[Optional[str]] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12))  # parsed | merged | ignored | failed
    note: Mapped[Optional[str]] = mapped_column(Text)
    transaction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("transactions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StatementImport(Base):
    __tablename__ = "imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    file_name: Mapped[str] = mapped_column(String(200))
    file_hash: Mapped[str] = mapped_column(String(64), unique=True)
    drive_file_id: Mapped[Optional[str]] = mapped_column(String(100))
    account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id"))
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    rows_new: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("rate_date", "currency"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rate_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    inr_per_unit: Mapped[Decimal] = mapped_column(Numeric(12, 4))


class ChangeLog(Base):
    __tablename__ = "change_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("transactions.id"))
    action: Mapped[str] = mapped_column(String(30))
    before: Mapped[Optional[dict]] = mapped_column(JSON)
    after: Mapped[Optional[dict]] = mapped_column(JSON)
    by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    """Small key/value store: last email sent dates, Sheet copy file id, and similar."""

    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- engine / sessions


@lru_cache(maxsize=4)
def get_engine(url: str | None = None):
    url = url or settings().database_url
    # Neon/Heroku style URLs -> the psycopg 3 driver we install
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix):]
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
    return create_engine(url, **kwargs)


def make_sessionmaker(url: str | None = None):
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    s = make_sessionmaker(url)()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db(url: str | None = None) -> None:
    Base.metadata.create_all(get_engine(url))


def get_setting(s: Session, key: str, default: str | None = None) -> str | None:
    row = s.get(AppSetting, key)
    return row.value if row else default


def set_setting(s: Session, key: str, value: str) -> None:
    row = s.get(AppSetting, key)
    if row:
        row.value = value
    else:
        s.add(AppSetting(key=key, value=value))


def member_by_email(s: Session, email: str) -> Member | None:
    return s.scalar(select(Member).where(Member.google_email == email.lower()))
