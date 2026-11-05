# =============================================================================
# src/long_format.py: the long-format reader (the export contract)
# =============================================================================
"""
Reads a plan supplied in the export contract (brief 5.2): one CSV line per
version, entity, account, period, amount and unit, with exactly that header.
A planning export from any tool can be put in this shape.

The rules are the ledger reader's: a leading byte order mark is accepted, an
amount is a plain decimal or a typed row error that keeps its text, lines
with the same version, entity, account and period are all kept and flagged,
and entity and account codes not on the lists passed in are flagged. The
member names (version, entity, account) go through the allow-list, and a
rejected one is recorded with its line and landed raw.

Each line becomes a plan row, as a workbook cell does, but with no sheet,
address, block or formula: its kind is a constant, or empty when the amount
is. The source has no formulas, so its batch record says so, and the formula
controls of part 1d report "not run: the source has no formulas" rather than
passing.

When a line has several faults, the row error is the first of: a wrong number
of fields, an empty version, entity or account, a period not of the form
2026-06, an amount that is not a plain decimal.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src import names as allow_list
from src.batch import (PERIOD, BatchRecord, SourceError, control_totals, csv_batch, parse_amount,
                       read_csv)
from src.extract import PlanCell, RejectedName
from src.hashing import file_sha256

__all__ = ["HEADER", "LongFormatExtraction", "SourceError", "read_long_format"]

HEADER = ("version", "entity", "account", "period", "amount", "unit")
MEMBERS = ("version", "entity", "account")


@dataclass(frozen=True)
class LongFormatExtraction:
    batch: BatchRecord
    cells: tuple
    rejected_names: tuple
    control_totals: tuple


def _row(number: int, fields: list[str]) -> dict:
    if len(fields) != len(HEADER):
        version, entity, account, period = (fields + [None] * 4)[:4]
        return {"source_line": number, "version": version, "entity": entity, "line": account,
                "period": period, "unit": None, "amount": None, "text": None,
                "row_error": "fields"}
    version, entity, account, period, amount_text, unit = fields
    amount, amount_error = parse_amount(amount_text)
    errors = [("version_missing", version == ""), ("entity_missing", entity == ""),
              ("account_missing", account == ""),
              ("period_not_yyyy_mm", not PERIOD.fullmatch(period)),
              (amount_error, amount_error is not None)]
    return {"source_line": number, "version": version, "entity": entity, "line": account,
            "period": period, "unit": unit or None, "amount": amount,
            "text": amount_text if amount is None and amount_text != "" else None,
            "row_error": next((code for code, found in errors if found), None)}


def read_long_format(path: Path | str, *, entities: Iterable[str],
                     accounts: Iterable[str]) -> LongFormatExtraction:
    """Every line of a long-format file as a plan row, flagged but never changed."""
    path = Path(path)
    sha = file_sha256(path)
    batch = csv_batch(path, "long_format", sha)
    rows = [_row(number, fields) for number, fields in read_csv(path, HEADER)]
    known_entities, known_accounts = set(entities), set(accounts)
    keys = Counter((r["version"], r["entity"], r["line"], r["period"]) for r in rows)
    cells = []
    for r in rows:
        account = r["line"] if r["line"] in known_accounts else None
        if r["amount"] is not None:
            kind, value_type = "constant", "number"
        elif r["text"] is not None:
            kind, value_type = "constant", "text"
        else:
            kind, value_type = "empty", "none"
        cells.append(PlanCell(
            batch_id=batch.batch_id, sheet=None, address=None, block=None,
            source_line=r["source_line"], version=r["version"], entity=r["entity"],
            line=r["line"], account=account, period=r["period"],
            column_kind="month" if r["period"] and PERIOD.fullmatch(r["period"]) else None,
            unit=r["unit"], kind=kind, formula=None, cached=None, value_type=value_type,
            value_text=r["text"], amount=r["amount"], row_error=r["row_error"],
            duplicate=keys[(r["version"], r["entity"], r["line"], r["period"])] > 1,
            unknown_entity=bool(r["entity"]) and r["entity"] not in known_entities,
            unknown_account=bool(r["line"]) and account is None,
            name_allowed=all(allow_list.allowed(r[m]) for m in ("version", "entity", "line")
                             if r[m])))
    rejected, seen = [], set()
    for r in rows:
        for member, key in zip(MEMBERS, ("version", "entity", "line")):
            name = r[key]
            if not name or (member, name) in seen:
                continue
            seen.add((member, name))
            reasons = allow_list.check(name)
            if reasons:
                rejected.append(RejectedName(member, name, reasons, None, None, r["source_line"]))
    return LongFormatExtraction(batch, tuple(cells), tuple(rejected),
                                control_totals(batch.batch_id, cells))
