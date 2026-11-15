"""Build small synthetic statement PDFs that mimic each bank's text layout.

These are made-up numbers, used only to exercise the parser and the API.
Run directly to write them to ./samples for trying the UI:

    python tests/sample_pdfs.py
"""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

DISCOVER_CARD = [
    "DISCOVER it Card",
    "Account number ending in 4321",
    "Open Date: 09/14/24 - Close Date: 10/13/24",
    "ACCOUNT SUMMARY",
    "Previous Balance $1,000.00",
    "Payments and Credits -$500.00",
    "Purchases +$123.45",
    "New Balance $623.45",
    "Minimum Payment Due $35.00",
    "Payment Due Date 11/10/24",
    "Credit Line $5,000.00",
    "Cash Advance Credit Line $1,000.00",
    "TRANSACTIONS",
    "Trans. Date Post Date Description Amount",
    "PAYMENTS AND CREDITS",
    "09/20 09/20 INTERNET PAYMENT - THANK YOU -$500.00",
    "PURCHASES",
    "09/15 09/16 AMAZON.COM SEATTLE WA $45.67",
    "10/02 10/03 SHELL OIL 57444 SALT LAKE CITY UT $52.78",
    "10/10 10/11 NETFLIX.COM LOS GATOS CA $25.00",
]

CHASE_CHECKING = [
    "JPMorgan Chase Bank, N.A.",
    "October 1, 2024 through October 31, 2024",
    "Account Number: 000000123456789",
    "Chase Total Checking",
    "CHECKING SUMMARY",
    "Beginning Balance $2,500.00",
    "Deposits and Additions 3,000.00",
    "Electronic Withdrawals -1,150.25",
    "Ending Balance $4,349.75",
    "TRANSACTION DETAIL",
    "DATE DESCRIPTION AMOUNT BALANCE",
    "10/01 Payroll Direct Dep Acme Corp 3,000.00 5,500.00",
    "10/05 Zelle Payment To John 150.25 5,349.75",
    "10/15 Rent Payment Online 1,000.00 4,349.75",
    # Deliberately not in the balance chain: exercises the sign fallback and
    # leaves the statement off by $4.50 so the reconciliation panel has something to flag.
    "10/20 Card Purchase Coffee Shop 4.50",
]

ZIONS_CHECKING = [
    "Zions Bank",
    "Statement Period 10/01/2024 - 10/31/2024",
    "Account Number XXXXXX7788",
    "Beginning Balance $1,200.00",
    "Ending Balance $1,560.10",
    "Deposits/Credits",
    "10/03 DEPOSIT MOBILE 500.00",
    "10/25 INTEREST PAID 0.10",
    "Withdrawals/Debits",
    "10/07 POS PURCHASE SMITHS 140.00",
    "Total Withdrawals/Debits 140.00",
    "Daily Balance Summary",
    "Service charge waived 0.00",
]


def build_pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for line in lines:
        c.drawString(50, y, line)
        y -= 16
        if y < 60:
            c.showPage()
            y = 750
    c.save()
    return buf.getvalue()


SAMPLES = {
    "discover_card.pdf": DISCOVER_CARD,
    "chase_checking.pdf": CHASE_CHECKING,
    "zions_checking.pdf": ZIONS_CHECKING,
}

if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "samples"
    out.mkdir(exist_ok=True)
    for name, lines in SAMPLES.items():
        (out / name).write_bytes(build_pdf(lines))
        print("wrote", out / name)
