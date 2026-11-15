# Statement Review

Upload monthly bank statement PDFs (Discover, Chase, Zions, others), review and fix
what was read, check that each statement balances, and commit it to your **dev** or
**prod** database.

## Run it

```bash
cd statement-review
pip install -r requirements.txt
cp .env.example .env          # fill in DEV_DATABASE_URL / PROD_DATABASE_URL
uvicorn app.main:app --port 8000
```

Open http://localhost:8000. With no `DEV_DATABASE_URL`, dev uses a local SQLite file
(`statements_dev.db`), so you can try it before pointing it at a real database. Prod
has no fallback and appears as "not configured" until `PROD_DATABASE_URL` is set.

The app has no login. Run it on your own machine (uvicorn binds to 127.0.0.1 by
default), and don't expose it on a network while it holds prod credentials.

To try it without real statements: `python tests/sample_pdfs.py` writes three made-up
PDFs to `samples/`.

## Using the page

1. **Pick the database** in the top bar. When you switch to PROD, the bar turns red.
2. **Drop one or more PDFs** on the upload box. Each one opens as a draft in the left panel,
   with a balanced / off-by-$X badge.
3. **Review and edit:**
   - Every header field can be edited: bank, account type, name, last 4, period, opening and
     closing balance, and the stated totals. **+ Add field** adds your own key/value fields.
   - Transactions: edit any cell. You can add a row (at the top, at the bottom, or below a
     row), delete a row (with undo), flip a row's +/− sign, sort by date, and filter.
     **+ Add column** adds your own column, such as Memo or Tax category.
   - Amounts are signed from your side: **+ money in / payments to a card, − money out / charges**.
4. **Reconciliation** updates as you type:
   - Checking/savings: opening + money in − money out = closing.
   - Credit card: previous balance − payments/credits + charges = new balance.
   - The panel also flags rows with a missing or invalid date, description or amount, $0
     amounts, dates outside the statement period, likely duplicates, and rows whose +/−
     sign was guessed. It also compares your totals with the totals printed on the
     statement. Click an issue to jump to it.
5. **Possibly missed lines**: these are lines in the PDF that have a date or dollar amount but
   weren't turned into a transaction. **Add as row** turns one into a transaction, and
   **Dismiss** hides it.
6. **Show original** shows the PDF (or its extracted text) next to the editor. Each row has
   a `pN` button that jumps to its page.
7. **Commit**. The page won't commit while there are errors. If the statement doesn't balance,
   it asks before committing. Prod commits and deletes make you type `PROD`.
   - If the same file, or the same bank, last 4 and period end, is already in the
     database, you're asked whether to replace it. Nothing is duplicated silently.
   - Each save runs in a single transaction, so a failure saves nothing.
8. **Saved in DEV/PROD** lists what's in the selected database. Click a statement to reopen
   and edit it, then commit again to update it.

## Database

There are two tables: `bank_statements` and `bank_statement_transactions`. See `schema.sql`.
They're created automatically if they're missing (existing tables are never altered). Set
`AUTO_CREATE_TABLES=0` if you manage the schema yourself. Custom fields and custom columns
are stored in the JSON `extra` column. Any SQLAlchemy URL works. Install the matching
driver from `requirements.txt` (PostgreSQL by default).

## How parsing works and its limits

`app/parsers.py` reads the PDF text with pdfplumber, detects the bank and account type, and
then pulls out:
- the statement period, balances and stated totals, using common label wording
  ("Previous/New Balance", "Beginning/Ending Balance", "Opening/Closing Date", …);
- transaction lines: a date (and an optional post date), a description, an amount, and an
  optional running balance.

Each transaction's +/− sign comes from the best evidence available, in this order: the
change in the running balance, the printed sign on card statements, the section heading
(Deposits / Withdrawals / Payments / Purchases), and finally a guess from the description.
Guessed signs are flagged for review.

The parser was built and tested against synthetic statements laid out like each bank's, not
real ones. Expect to tune the patterns after your first real Discover, Chase and Zions
uploads; anything it misses lands in "Possibly missed lines" rather than disappearing.
Scanned (image-only) PDFs have no text to read. The page warns about them, and you can
enter rows by hand.

## Tests

```bash
python -m pytest -q
```
