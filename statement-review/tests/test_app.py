from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.parsers import parse_money, parse_statement
from sample_pdfs import CHASE_CHECKING, DISCOVER_CARD, ZIONS_CHECKING, build_pdf


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_DATABASE_URL", f"sqlite:///{tmp_path / 'dev.db'}")
    monkeypatch.setenv("PROD_DATABASE_URL", f"sqlite:///{tmp_path / 'prod.db'}")
    db.reset_engines()
    yield TestClient(app)
    db.reset_engines()


def _sum(draft):
    return sum(Decimal(t["amount"]) for t in draft["transactions"])


# ------------------------------------------------------------- parser

@pytest.mark.parametrize(
    "token, expected",
    [("$1,234.56", "1234.56"), ("-$5.00", "-5.00"), ("(5.00)", "-5.00"), ("5.00-", "-5.00"), ("5.00 CR", "-5.00"), ("+$12.00", "12.00")],
)
def test_parse_money(token, expected):
    assert parse_money(token) == Decimal(expected)


def test_discover_card_balances():
    d = parse_statement(build_pdf(DISCOVER_CARD), "d.pdf").to_dict()
    assert d["bank"] == "Discover" and d["account_type"] == "credit_card"
    assert d["account_last4"] == "4321"
    assert (d["period_start"], d["period_end"]) == ("2024-09-14", "2024-10-13")
    assert len(d["transactions"]) == 4
    # Card: previous balance - (payments - charges) = new balance
    assert Decimal(d["opening_balance"]) - _sum(d) == Decimal(d["closing_balance"])
    payment = d["transactions"][0]
    assert payment["amount"] == "500.00" and payment["post_date"] == "2024-09-20"


def test_chase_checking_signs_from_running_balance():
    d = parse_statement(build_pdf(CHASE_CHECKING), "c.pdf").to_dict()
    assert d["bank"] == "Chase" and d["account_type"] == "checking"
    assert d["account_last4"] == "6789"
    amounts = [t["amount"] for t in d["transactions"]]
    assert amounts[:3] == ["3000.00", "-150.25", "-1000.00"]
    assert [t["sign_source"] for t in d["transactions"][:3]] == ["balance"] * 3
    # The coffee row has no balance column: sign is guessed and the statement is off by 4.50.
    assert d["transactions"][3]["sign_source"] == "guess"
    assert Decimal(d["opening_balance"]) + _sum(d) - Decimal(d["closing_balance"]) == Decimal("-4.50")


def test_zions_checking_sections_and_unparsed():
    d = parse_statement(build_pdf(ZIONS_CHECKING), "z.pdf").to_dict()
    assert d["bank"] == "Zions" and d["account_last4"] == "7788"
    assert Decimal(d["opening_balance"]) + _sum(d) == Decimal(d["closing_balance"])
    assert d["total_debits"] == "140.00"
    assert any("Service charge" in u["text"] for u in d["unparsed_lines"])


def test_garbage_file_does_not_crash():
    d = parse_statement(b"not a pdf", "x.pdf").to_dict()
    assert d["transactions"] == [] and d["warnings"]


# ------------------------------------------------------------- API

def _statement(**over):
    s = {
        "bank": "Zions", "account_type": "checking", "account_last4": "7788",
        "period_start": "2024-10-01", "period_end": "2024-10-31",
        "opening_balance": "100.00", "closing_balance": "90.00", "file_sha256": "abc",
        "extra": {"Reviewed by": "me"},
        "transactions": [
            {"txn_date": "2024-10-03", "description": "Coffee", "amount": "-10.00", "extra": {"Memo": "x"}},
        ],
    }
    s.update(over)
    return s


def test_parse_endpoint(client):
    files = [("files", ("d.pdf", build_pdf(DISCOVER_CARD), "application/pdf")),
             ("files", ("z.pdf", build_pdf(ZIONS_CHECKING), "application/pdf"))]
    r = client.post("/api/parse", files=files)
    assert r.status_code == 200
    assert [d["bank"] for d in r.json()] == ["Discover", "Zions"]


def test_commit_list_get_update_delete(client):
    r = client.post("/api/statements?env=dev", json={"statement": _statement()})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert client.get("/api/statements?env=dev").json()[0]["transaction_count"] == 1

    full = client.get(f"/api/statements/{sid}?env=dev").json()
    assert full["extra"] == {"Reviewed by": "me"}
    assert full["transactions"][0]["extra"] == {"Memo": "x"}

    # Edit: add a row and save over the same id.
    s = _statement(transactions=_statement()["transactions"] + [{"txn_date": "2024-10-04", "description": "Deposit", "amount": "5"}])
    r = client.post("/api/statements?env=dev", json={"statement": s, "id": sid})
    assert r.status_code == 200 and r.json()["transaction_count"] == 2
    assert r.json()["transactions"][1]["amount"] == "5.00"

    assert client.delete(f"/api/statements/{sid}?env=dev").status_code == 200
    assert client.get("/api/statements?env=dev").json() == []


def test_duplicate_is_rejected_then_replaceable(client):
    first = client.post("/api/statements?env=dev", json={"statement": _statement()}).json()
    r = client.post("/api/statements?env=dev", json={"statement": _statement(file_sha256="other")})
    assert r.status_code == 409
    assert r.json()["detail"]["existing"]["id"] == first["id"]
    r = client.post("/api/statements?env=dev", json={"statement": _statement(opening_balance="1.00"), "replace_id": first["id"]})
    assert r.status_code == 200 and r.json()["id"] == first["id"] and r.json()["opening_balance"] == "1.00"
    assert len(client.get("/api/statements?env=dev").json()) == 1


def test_prod_requires_confirmation_and_is_separate(client):
    assert client.post("/api/statements?env=prod", json={"statement": _statement()}).status_code == 400
    assert client.post("/api/statements?env=prod", json={"statement": _statement(), "confirm_prod": True}).status_code == 200
    assert len(client.get("/api/statements?env=prod").json()) == 1
    assert client.get("/api/statements?env=dev").json() == []
    assert client.delete("/api/statements/1?env=prod").status_code == 400


def test_invalid_rows_are_rejected_and_nothing_saved(client):
    bad = _statement(transactions=[{"txn_date": "", "description": "", "amount": "abc"}])
    assert client.post("/api/statements?env=dev", json={"statement": bad}).status_code == 422
    assert client.get("/api/statements?env=dev").json() == []


def test_prod_not_configured(client, monkeypatch):
    monkeypatch.delenv("PROD_DATABASE_URL")
    db.reset_engines()
    envs = {e["name"]: e for e in client.get("/api/environments").json()}
    assert envs["prod"]["configured"] is False
    r = client.get("/api/statements?env=prod")
    assert r.status_code == 400 and "PROD_DATABASE_URL" in r.json()["detail"]


def test_masked_url():
    assert db.masked_url("postgresql://u:secret@h/db") == "postgresql://u:****@h/db"
