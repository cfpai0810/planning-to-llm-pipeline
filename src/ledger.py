# =============================================================================
# src/ledger.py: the ledger extract reader
# =============================================================================
"""
Reads a ledger extract (brief 5.2): one CSV line per entity, period and
account code, with the amount in ledger sign (debit positive). The header is
exactly entity,period,account,amount.

- A leading byte order mark is accepted; the text must be UTF-8.
- An amount is a plain decimal. Anything else is kept as a typed row error
  with its text and no amount, never coerced (src/batch.py has the rules).
- A period is text of the form 2026-06; anything else is a row error.
- Lines with the same entity, period and account are all kept and all
  flagged as duplicates.
- An entity or account code not on the lists passed in is flagged. The
  caller passes the codes of the Lists tab of the workbook the ledger is
  checked with: its entities, and its accounts that carry a ledger sign.
- When a line has several faults, the row error is the first of: a wrong
  number of fields, an empty entity, an empty account, a period not of the
  form 2026-06, an amount that is not a plain decimal.

The reader reports facts; reconciliation (part 1d) turns them into breaks.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from src.batch import (PERIOD, BatchRecord, SourceError, control_totals, csv_batch, parse_amount,
                       read_csv)
from src.hashing import file_sha256

__all__ = ["HEADER", "LedgerLine", "LedgerExtraction", "SourceError", "read_ledger"]

HEADER = ("entity", "period", "account", "amount")


@dataclass(frozen=True)
class LedgerLine:
    batch_id: str
    line_number: int                # the line of the file, the header being line 1
    entity: Optional[str]
    period: Optional[str]
    account: Optional[str]
    amount: Optional[Decimal]       # in ledger sign; None when the text is not a plain decimal
    amount_text: Optional[str]      # the amount exactly as the file holds it
    row_error: Optional[str]
    duplicate: bool
    unknown_entity: bool
    unknown_account: bool


@dataclass(frozen=True)
class LedgerExtraction:
    batch: BatchRecord
    lines: tuple
    control_totals: tuple           # per period; the ledger has no version


def _line(number: int, fields: list[str]) -> dict:
    if len(fields) != len(HEADER):
        entity, period, account = (fields + [None, None, None])[:3]
        return {"line_number": number, "entity": entity, "period": period, "account": account,
                "amount": None, "amount_text": None, "row_error": "fields"}
    entity, period, account, amount_text = fields
    amount, amount_error = parse_amount(amount_text)
    errors = [("entity_missing", entity == ""), ("account_missing", account == ""),
              ("period_not_yyyy_mm", not PERIOD.fullmatch(period)),
              (amount_error, amount_error is not None)]
    return {"line_number": number, "entity": entity, "period": period, "account": account,
            "amount": amount, "amount_text": amount_text,
            "row_error": next((code for code, found in errors if found), None)}


def read_ledger(path: Path | str, *, entities: Iterable[str],
                accounts: Iterable[str]) -> LedgerExtraction:
    """Every line of a ledger extract, flagged but never changed, with its batch record."""
    path = Path(path)
    sha = file_sha256(path)
    batch = csv_batch(path, "ledger", sha)
    rows = [_line(number, fields) for number, fields in read_csv(path, HEADER)]
    known_entities, known_accounts = set(entities), set(accounts)
    keys = Counter((r["entity"], r["period"], r["account"]) for r in rows)
    lines = tuple(LedgerLine(
        batch_id=batch.batch_id, **r,
        duplicate=keys[(r["entity"], r["period"], r["account"])] > 1,
        unknown_entity=bool(r["entity"]) and r["entity"] not in known_entities,
        unknown_account=bool(r["account"]) and r["account"] not in known_accounts)
        for r in rows)
    return LedgerExtraction(batch, lines,
                            control_totals(batch.batch_id, lines, version_of=lambda line: None))
