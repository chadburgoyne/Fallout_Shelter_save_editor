"""Database layer: one engine per environment (dev / prod) and the two tables.

Connection strings come from environment variables so credentials never live
in the code:

    DEV_DATABASE_URL   e.g. postgresql+psycopg://user:pass@dev-host/finance
    PROD_DATABASE_URL  e.g. postgresql+psycopg://user:pass@prod-host/finance

If DEV_DATABASE_URL is unset, dev falls back to a local SQLite file so the app
works out of the box. Prod has no fallback: it must be configured explicitly.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

try:  # optional: load DEV_/PROD_DATABASE_URL from a .env file next to the app
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ENVIRONMENTS = ("dev", "prod")
DEFAULT_DEV_URL = "sqlite:///./statements_dev.db"


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Statement(Base):
    __tablename__ = "bank_statements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank: Mapped[str] = mapped_column(String(64))
    account_type: Mapped[str] = mapped_column(String(32))  # checking | savings | credit_card
    account_name: Mapped[str | None] = mapped_column(String(128))
    account_last4: Mapped[str | None] = mapped_column(String(8))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    opening_balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total_credits: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total_debits: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    source_filename: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)  # user-added custom fields
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="statement",
        cascade="all, delete-orphan",
        order_by="Transaction.line_no",
    )


class Transaction(Base):
    __tablename__ = "bank_statement_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement_id: Mapped[int] = mapped_column(
        ForeignKey("bank_statements.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer)
    txn_date: Mapped[date] = mapped_column(Date)
    post_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(128))
    # Signed from the account holder's point of view:
    #   positive = money in / payment or credit to a card
    #   negative = money out / charge on a card
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    extra: Mapped[dict] = mapped_column(JSON, default=dict)  # user-added custom columns

    statement: Mapped[Statement] = relationship(back_populates="transactions")


_engines: dict[str, Engine] = {}
_sessions: dict[str, sessionmaker] = {}


class EnvironmentNotConfigured(Exception):
    pass


def database_url(env: str) -> str | None:
    if env not in ENVIRONMENTS:
        raise EnvironmentNotConfigured(f"Unknown environment '{env}'")
    url = os.environ.get(f"{env.upper()}_DATABASE_URL")
    if not url and env == "dev":
        url = DEFAULT_DEV_URL
    return url


def masked_url(url: str | None) -> str | None:
    """Hide the password in a connection string for display."""
    if not url:
        return None
    return re.sub(r"(://[^:/@]+:)[^@]+@", r"\1****@", url)


def get_engine(env: str) -> Engine:
    if env in _engines:
        return _engines[env]
    url = database_url(env)
    if not url:
        raise EnvironmentNotConfigured(
            f"{env.upper()}_DATABASE_URL is not set; the {env} database is not configured."
        )
    engine = create_engine(url, pool_pre_ping=True, future=True)
    if os.environ.get("AUTO_CREATE_TABLES", "1") != "0":
        # Only creates the two tables if they are missing; never alters existing ones.
        Base.metadata.create_all(engine)
    _engines[env] = engine
    _sessions[env] = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def session_for(env: str):
    get_engine(env)
    return _sessions[env]()


def reset_engines() -> None:
    """Used by tests to pick up new environment variables."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
    _sessions.clear()
