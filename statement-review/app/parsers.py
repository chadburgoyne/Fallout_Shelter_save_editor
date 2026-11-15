"""Turn a bank statement PDF into an editable draft.

The parser is deliberately forgiving: it extracts what it can recognise and
reports everything else so the review page can show it. Each draft carries:

  * header fields (bank, account, period, balances, stated totals)
  * transactions, each signed from the account holder's point of view
    (+ money in / payment to a card, - money out / charge on a card)
  * ``unparsed_lines``: lines that contain a dollar amount or start with a
    date but did not become a transaction or a header field - the usual
    place where a missed row shows up
  * ``warnings``: anything the reviewer should look at

Banks with specific handling: Discover, Chase, Zions. Anything else goes
through the same generic line parser and is labelled "Other".
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import pdfplumber

# ---------------------------------------------------------------- patterns

_NUM = r"\d{1,3}(?:,\d{3})*\.\d{2}|\d+\.\d{2}"
# Optional sign, optional $, optional parentheses, optional trailing CR or minus.
MONEY = rf"(?:[-+]\s?)?\$?\s?\(?(?:[-+])?\$?(?:{_NUM})\)?(?:\s?CR\b|-(?!\d))?"
MONEY_RE = re.compile(MONEY)
DATE_TOKEN = r"\d{1,2}/\d{1,2}(?:/\d{2,4})?"

TXN_RE = re.compile(
    rf"^(?P<d1>{DATE_TOKEN})\s+"
    rf"(?:(?P<d2>{DATE_TOKEN})\s+)?"
    rf"(?P<desc>.*?\S)\s+"
    rf"(?P<amt>{MONEY})"
    rf"(?:\s+(?P<bal>{MONEY}))?\s*$"
)
DATE_START_RE = re.compile(rf"^{DATE_TOKEN}\b")

LONG_DATE = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
RANGE_SEP = r"\s*(?:-|–|—|to|through|thru)\s*"

PERIOD_PATTERNS = [
    re.compile(
        rf"(?:opening/closing date|statement period|billing period|open date)[:\s]*"
        rf"({DATE_TOKEN}){RANGE_SEP}(?:close date[:\s]*)?({DATE_TOKEN})",
        re.I,
    ),
    re.compile(rf"({LONG_DATE}){RANGE_SEP}({LONG_DATE})", re.I),
    re.compile(rf"(\d{{1,2}}/\d{{1,2}}/\d{{2,4}}){RANGE_SEP}(\d{{1,2}}/\d{{1,2}}/\d{{2,4}})"),
]

AMOUNT_AFTER = rf"[^\d\n$()+-]{{0,40}}?(?P<amt>{MONEY})"
HEADER_PATTERNS: dict[str, list[re.Pattern]] = {
    "opening_balance": [
        re.compile(rf"(?:previous balance|beginning balance|opening balance|balance last statement|starting balance){AMOUNT_AFTER}", re.I),
    ],
    "closing_balance": [
        re.compile(rf"(?:new balance|ending balance|closing balance|balance this statement){AMOUNT_AFTER}", re.I),
    ],
    "total_credits": [
        re.compile(rf"(?:payments?,? (?:and|&) (?:other )?credits|total deposits(?: and additions)?|deposits and additions|deposits/credits|total credits){AMOUNT_AFTER}", re.I),
    ],
    "total_debits": [
        re.compile(rf"(?:total withdrawals(?: and debits)?|withdrawals/debits|total debits|purchases(?: and adjustments)?){AMOUNT_AFTER}", re.I),
    ],
}

LAST4_RE = re.compile(
    r"(?:account|acct|card)\s*(?:number|no\.?|#)?\s*(?:ending(?:\s+in)?)?[:\s#]*[xX*\-\s\d]*?(\d{4})\b",
    re.I,
)

CREDIT_SECTION_WORDS = ("deposit", "addition", "credit", "payments and", "interest earned", "interest paid")
DEBIT_SECTION_WORDS = (
    "withdrawal", "debit", "purchase", "checks paid", "checks", "fee", "interest charged",
    "cash advance", "charges", "atm",
)
CREDIT_DESC_HINTS = (
    "deposit", "payroll", "direct dep", "transfer from", "interest paid", "interest earned",
    "refund", "payment - thank you", "payment thank you", "autopay payment", "credit", "reversal",
)

BANKS = {"discover": "Discover", "chase": "Chase", "zions": "Zions"}


# ---------------------------------------------------------------- helpers

def parse_money(token: str) -> Decimal | None:
    """'$1,234.56' -> 1234.56 ; '-$5.00', '(5.00)', '5.00-', '5.00 CR' -> -5.00"""
    if token is None:
        return None
    s = token.strip()
    negative = False
    if s.upper().endswith("CR"):
        negative = True
        s = s[:-2].strip()
    if s.endswith("-"):
        negative = True
        s = s[:-1]
    if "(" in s and ")" in s:
        negative = True
    if "-" in s:
        negative = True
    digits = re.sub(r"[^\d.]", "", s)
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


def has_explicit_sign(token: str) -> bool:
    t = token.strip().upper()
    return t.startswith(("-", "+")) or "(" in t or t.endswith(("-", "CR")) or "$-" in t


def parse_date(token: str, period_end: date | None, period_start: date | None = None) -> date | None:
    token = token.strip().rstrip(",")
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            pass
    for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y", "%b. %d, %Y"):
        try:
            return datetime.strptime(token.replace("Sept", "Sep"), fmt).date()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", token)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    anchor = period_end or date.today()
    for year in (anchor.year, anchor.year - 1, anchor.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        # A MM/DD date belongs to the year that puts it at or shortly before the period end.
        if candidate <= anchor + timedelta(days=7):
            if period_start is None or candidate >= period_start - timedelta(days=45):
                return candidate
    try:
        return date(anchor.year, month, day)
    except ValueError:
        return None


def detect_bank(text: str) -> str:
    lower = text.lower()
    positions = {name: lower.find(key) for key, name in BANKS.items() if key in lower}
    if not positions:
        if "jpmorgan" in lower:
            return "Chase"
        return "Other"
    # The bank named earliest in the document is almost always the issuer.
    return min(positions, key=positions.get)


def detect_account_type(text: str) -> str:
    lower = text.lower()
    card_markers = ("minimum payment due", "credit limit", "credit access line", "payment due date", "cash advance")
    if sum(marker in lower for marker in card_markers) >= 2:
        return "credit_card"
    if re.search(r"savings", lower[:2000]):
        return "savings"
    return "checking"


def section_sign(line: str) -> int | None:
    """If a line looks like a section heading, return +1 (credits) / -1 (debits)."""
    lower = line.lower().strip()
    if len(lower) > 70 or MONEY_RE.search(line) or DATE_START_RE.match(lower):
        return None
    if lower.startswith("total"):
        return None
    hits = []
    for word in CREDIT_SECTION_WORDS:
        i = lower.find(word)
        if i >= 0:
            hits.append((i, 1))
    for word in DEBIT_SECTION_WORDS:
        i = lower.find(word)
        if i >= 0:
            hits.append((i, -1))
    if not hits:
        return None
    return min(hits)[1]


# ---------------------------------------------------------------- data

@dataclass
class DraftTransaction:
    txn_date: str | None
    post_date: str | None
    description: str
    amount: str | None
    category: str | None = None
    page: int | None = None
    source_line: str | None = None
    sign_source: str = "explicit"  # explicit | balance | section | guess
    extra: dict = field(default_factory=dict)


@dataclass
class Draft:
    filename: str
    file_sha256: str
    bank: str
    account_type: str
    account_name: str | None
    account_last4: str | None
    period_start: str | None
    period_end: str | None
    opening_balance: str | None
    closing_balance: str | None
    total_credits: str | None
    total_debits: str | None
    transactions: list[DraftTransaction]
    unparsed_lines: list[dict]
    warnings: list[str]
    raw_text: str
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _fmt(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _money_str(v: Decimal | None) -> str | None:
    return None if v is None else f"{v:.2f}"


# ---------------------------------------------------------------- main entry

def extract_pages(data: bytes) -> list[str]:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


def parse_statement(data: bytes, filename: str) -> Draft:
    sha = hashlib.sha256(data).hexdigest()
    warnings: list[str] = []
    try:
        pages = extract_pages(data)
    except Exception as exc:  # pdfplumber raises a variety of errors for bad files
        return Draft(
            filename=filename, file_sha256=sha, bank="Other", account_type="checking",
            account_name=None, account_last4=None, period_start=None, period_end=None,
            opening_balance=None, closing_balance=None, total_credits=None, total_debits=None,
            transactions=[], unparsed_lines=[], raw_text="",
            warnings=[f"Could not read this file as a PDF: {exc}"],
        )
    return parse_text_pages(pages, filename, sha, warnings)


def parse_text_pages(pages: list[str], filename: str, sha: str = "", warnings: list[str] | None = None) -> Draft:
    warnings = list(warnings or [])
    full_text = "\n".join(pages)
    if len(full_text.strip()) < 40:
        warnings.append(
            "Almost no text could be extracted - this looks like a scanned/image PDF. "
            "Enter the transactions by hand or download a text-based statement from the bank."
        )

    bank = detect_bank(full_text)
    account_type = detect_account_type(full_text)
    is_card = account_type == "credit_card"

    # Period
    period_start = period_end = None
    for pattern in PERIOD_PATTERNS:
        m = pattern.search(full_text)
        if m:
            period_end = parse_date(m.group(2), None)
            period_start = parse_date(m.group(1), period_end)
            if period_start and period_end:
                break
    if not period_end:
        warnings.append("Statement period was not found - fill in the period dates.")

    # Header amounts (first match wins - summary boxes are near the top)
    header: dict[str, Decimal | None] = {}
    header_lines: set[str] = set()
    for key, patterns in HEADER_PATTERNS.items():
        header[key] = None
        for pattern in patterns:
            m = pattern.search(full_text)
            if m:
                value = parse_money(m.group("amt"))
                header[key] = abs(value) if value is not None and key.startswith("total") else value
                line_start = full_text.rfind("\n", 0, m.start()) + 1
                line_end = full_text.find("\n", m.end())
                header_lines.add(full_text[line_start: line_end if line_end >= 0 else None].strip())
                break
    for key in ("opening_balance", "closing_balance"):
        if header[key] is None:
            warnings.append(f"{key.replace('_', ' ').capitalize()} was not found - fill it in to reconcile.")

    last4 = None
    m = LAST4_RE.search(full_text)
    if m:
        last4 = m.group(1)

    # Transactions
    transactions: list[DraftTransaction] = []
    unparsed: list[dict] = []
    current_section: int | None = None
    prev_balance: Decimal | None = header["opening_balance"]

    for page_no, page_text in enumerate(pages, start=1):
        for raw in page_text.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if not line:
                continue

            sign = section_sign(line)
            if sign is not None:
                current_section = sign
                continue

            m = TXN_RE.match(line)
            if m:
                txn = _build_transaction(m, line, page_no, period_start, period_end, is_card,
                                         current_section, prev_balance)
                if txn is not None:
                    if m.group("bal"):
                        prev_balance = parse_money(m.group("bal"))
                    transactions.append(txn)
                    continue

            if line in header_lines or line.lower().startswith(("total", "subtotal")):
                continue
            if MONEY_RE.search(line) or DATE_START_RE.match(line):
                # A dollar amount or a leading date that we did not use - possibly a missed row.
                if re.search(r"\.\d{2}\b", line) or DATE_START_RE.match(line):
                    unparsed.append({"page": page_no, "text": line})

    if not transactions:
        warnings.append("No transactions were recognised. Add rows manually or use the 'Possibly missed' lines.")
    guessed = sum(1 for t in transactions if t.sign_source == "guess")
    if guessed:
        warnings.append(f"{guessed} transaction(s) had their +/- sign guessed from the description - check them (marked ±?).")

    return Draft(
        filename=filename,
        file_sha256=sha,
        bank=bank,
        account_type=account_type,
        account_name=None,
        account_last4=last4,
        period_start=_fmt(period_start),
        period_end=_fmt(period_end),
        opening_balance=_money_str(header["opening_balance"]),
        closing_balance=_money_str(header["closing_balance"]),
        total_credits=_money_str(header["total_credits"]),
        total_debits=_money_str(header["total_debits"]),
        transactions=transactions,
        unparsed_lines=unparsed,
        warnings=warnings,
        raw_text=full_text,
    )


def _build_transaction(m, line, page_no, period_start, period_end, is_card, section, prev_balance):
    txn_date = parse_date(m.group("d1"), period_end, period_start)
    if txn_date is None:
        return None
    post_date = parse_date(m.group("d2"), period_end, period_start) if m.group("d2") else None
    amount_token = m.group("amt")
    printed = parse_money(amount_token)
    if printed is None:
        return None
    magnitude = abs(printed)
    description = m.group("desc").strip()

    # Work out the sign from the most reliable evidence available.
    amount: Decimal
    sign_source: str
    balance = parse_money(m.group("bal")) if m.group("bal") else None
    if balance is not None and prev_balance is not None and abs(abs(balance - prev_balance) - magnitude) < Decimal("0.005"):
        delta = balance - prev_balance
        if is_card:
            delta = -delta  # card balance going up means a charge
        amount, sign_source = (magnitude if delta > 0 else -magnitude), "balance"
    elif is_card and has_explicit_sign(amount_token):
        # Card statements print payments/credits as negative numbers.
        amount, sign_source = -printed, "explicit"
    elif is_card and section is not None:
        amount, sign_source = magnitude * section, "section"
    elif is_card:
        amount, sign_source = -printed, "explicit"  # unsigned amount on a card = a charge
    elif has_explicit_sign(amount_token):
        amount, sign_source = printed, "explicit"
    elif section is not None:
        amount, sign_source = magnitude * section, "section"
    else:
        lower = description.lower()
        is_credit = any(h in lower for h in CREDIT_DESC_HINTS)
        amount, sign_source = (magnitude if is_credit else -magnitude), "guess"

    return DraftTransaction(
        txn_date=_fmt(txn_date),
        post_date=_fmt(post_date),
        description=description,
        amount=_money_str(amount),
        page=page_no,
        source_line=line,
        sign_source=sign_source,
    )
