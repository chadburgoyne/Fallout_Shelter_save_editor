"""Statement Review: upload bank statement PDFs, review/fix them, commit to dev or prod.

Run:  uvicorn app.main:app --reload   (from the statement-review folder)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError

from . import db
from .parsers import parse_statement

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Statement Review")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

Env = Literal["dev", "prod"]


# ---------------------------------------------------------------- schemas

class TransactionIn(BaseModel):
    txn_date: date
    post_date: date | None = None
    description: str = Field(min_length=1)
    category: str | None = None
    amount: Decimal
    extra: dict = Field(default_factory=dict)

    @field_validator("amount")
    @classmethod
    def two_places(cls, v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.01"))


class StatementIn(BaseModel):
    bank: str = Field(min_length=1)
    account_type: Literal["checking", "savings", "credit_card"]
    account_name: str | None = None
    account_last4: str | None = Field(default=None, max_length=8)
    period_start: date | None = None
    period_end: date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    total_credits: Decimal | None = None
    total_debits: Decimal | None = None
    source_filename: str | None = None
    file_sha256: str | None = None
    extra: dict = Field(default_factory=dict)
    transactions: list[TransactionIn] = Field(default_factory=list)


class CommitRequest(BaseModel):
    statement: StatementIn
    # Id of the row this draft was loaded from in the same environment (an edit).
    id: int | None = None
    # Id of an existing duplicate the user agreed to overwrite.
    replace_id: int | None = None
    confirm_prod: bool = False


# ---------------------------------------------------------------- helpers

def _money(v: Decimal | None) -> str | None:
    return None if v is None else f"{v:.2f}"


def _statement_out(s: db.Statement, with_transactions: bool = True) -> dict:
    out = {
        "id": s.id,
        "bank": s.bank,
        "account_type": s.account_type,
        "account_name": s.account_name,
        "account_last4": s.account_last4,
        "period_start": s.period_start.isoformat() if s.period_start else None,
        "period_end": s.period_end.isoformat() if s.period_end else None,
        "opening_balance": _money(s.opening_balance),
        "closing_balance": _money(s.closing_balance),
        "total_credits": _money(s.total_credits),
        "total_debits": _money(s.total_debits),
        "source_filename": s.source_filename,
        "file_sha256": s.file_sha256,
        "extra": s.extra or {},
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "transaction_count": len(s.transactions),
    }
    if with_transactions:
        out["transactions"] = [
            {
                "txn_date": t.txn_date.isoformat(),
                "post_date": t.post_date.isoformat() if t.post_date else None,
                "description": t.description,
                "category": t.category,
                "amount": _money(t.amount),
                "extra": t.extra or {},
            }
            for t in s.transactions
        ]
    return out


def _session(env: str):
    try:
        return db.session_for(env)
    except db.EnvironmentNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Could not connect to the {env} database: {exc}")


def _apply(statement: db.Statement, data: StatementIn) -> None:
    for name in (
        "bank", "account_type", "account_name", "account_last4", "period_start", "period_end",
        "opening_balance", "closing_balance", "total_credits", "total_debits",
        "source_filename", "file_sha256", "extra",
    ):
        setattr(statement, name, getattr(data, name))
    statement.transactions.clear()
    for i, t in enumerate(data.transactions, start=1):
        statement.transactions.append(
            db.Transaction(
                line_no=i,
                txn_date=t.txn_date,
                post_date=t.post_date,
                description=t.description.strip(),
                category=t.category,
                amount=t.amount,
                extra=t.extra,
            )
        )


# ---------------------------------------------------------------- routes

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/environments")
def environments():
    out = []
    for env in db.ENVIRONMENTS:
        url = db.database_url(env)
        out.append({"name": env, "configured": bool(url), "url": db.masked_url(url)})
    return out


@app.post("/api/parse")
async def parse(files: list[UploadFile] = File(...)):
    drafts = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{f.filename} is larger than 25 MB")
        drafts.append(parse_statement(data, f.filename or "statement.pdf").to_dict())
    return drafts


@app.get("/api/statements")
def list_statements(env: Env = Query("dev")):
    with _session(env) as s:
        rows = s.scalars(
            select(db.Statement).order_by(db.Statement.period_end.desc(), db.Statement.id.desc())
        ).all()
        return [_statement_out(r, with_transactions=False) for r in rows]


@app.get("/api/statements/{statement_id}")
def get_statement(statement_id: int, env: Env = Query("dev")):
    with _session(env) as s:
        row = s.get(db.Statement, statement_id)
        if not row:
            raise HTTPException(404, "Statement not found")
        return _statement_out(row)


@app.post("/api/statements")
def commit_statement(req: CommitRequest, env: Env = Query("dev")):
    if env == "prod" and not req.confirm_prod:
        raise HTTPException(400, "Committing to prod requires confirm_prod=true")
    data = req.statement

    with _session(env) as s:
        try:
            target_id = req.id or req.replace_id
            if target_id is None:
                # Refuse silent duplicates: same file, or same bank/account/period.
                dup_conditions = []
                if data.file_sha256:
                    dup_conditions.append(db.Statement.file_sha256 == data.file_sha256)
                if data.period_end:
                    dup_conditions.append(
                        and_(
                            db.Statement.bank == data.bank,
                            db.Statement.account_last4 == data.account_last4,
                            db.Statement.period_end == data.period_end,
                        )
                    )
                if dup_conditions:
                    existing = s.scalars(select(db.Statement).where(or_(*dup_conditions))).first()
                    if existing:
                        raise HTTPException(
                            409,
                            {
                                "message": "This statement already exists in the database.",
                                "existing": _statement_out(existing, with_transactions=False),
                            },
                        )
                row = db.Statement()
                s.add(row)
            else:
                row = s.get(db.Statement, target_id)
                if not row:
                    raise HTTPException(404, f"Statement {target_id} no longer exists in {env}")
            _apply(row, data)
            s.commit()
            s.refresh(row)
            return _statement_out(row)
        except HTTPException:
            s.rollback()
            raise
        except SQLAlchemyError as exc:
            s.rollback()
            raise HTTPException(502, f"Database error, nothing was saved: {exc}")


@app.delete("/api/statements/{statement_id}")
def delete_statement(statement_id: int, env: Env = Query("dev"), confirm_prod: bool = False):
    if env == "prod" and not confirm_prod:
        raise HTTPException(400, "Deleting from prod requires confirm_prod=true")
    with _session(env) as s:
        row = s.get(db.Statement, statement_id)
        if not row:
            raise HTTPException(404, "Statement not found")
        s.delete(row)
        s.commit()
        return {"deleted": statement_id}
