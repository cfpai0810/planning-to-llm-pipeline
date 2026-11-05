# =============================================================================
# src/batch.py: the batch record and what the three readers share
# =============================================================================
"""
Every file the pipeline reads becomes one batch. The batch record states what
the file is (its name, SHA-256 and size), what it says about itself (sheets
and their visibility, creator, last modified by, created and modified times,
the application that last saved it, links to other workbooks) and whether its
figures were calculated by the sample build or arrived as they are.

- The batch id is the first 16 hex digits of the file's SHA-256, so the same
  bytes are always the same batch, whatever the file is called.
- File times and the application are kept as the text the file holds. They
  are facts about the file, never compared with the plan's as-of date.
- External link targets are data, kept as text, and never put into a prompt
  or into anything the star schema reads.
- `calculation` is "recalculated by the sample build" only when the file's
  hash equals a workbook the sample build recalculated and checked (an entry
  under "checks" in data/sample_manifest.json); any other workbook is "as
  received". A CSV has no formulas to calculate. The pipeline never
  recalculates a file it was given.

The control totals (per batch, version and period: the row count and the sum
of the amounts) are computed here in Python from the rows a reader returns,
before anything is landed, so that part 1d can tie what landed to what was read.

The two CSV readers share the rules below. A leading byte order mark is
accepted and the text must be UTF-8. The header must be exactly the
contract's. An amount is a plain decimal: an optional minus sign, digits, and
an optional point followed by digits. Anything else (a thousands separator,
brackets, a currency sign, an exponent, spaces, words) is a typed row error
that keeps the text and gives no amount: it is never coerced.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "data" / "sample_manifest.json"
BATCH_ID_DIGITS = 16

CALCULATED_BY_BUILD = "recalculated by the sample build"
AS_RECEIVED = "as received"
NO_FORMULAS = "not applicable: the source has no formulas"


class SourceError(Exception):
    """A file that cannot be read at all, told in one plain line with a fixed kind."""

    def __init__(self, kind: str, file_name: str, detail: str):
        self.kind = kind
        self.file_name = file_name
        super().__init__(f"{file_name}: {detail}")


@dataclass(frozen=True)
class SheetState:
    name: str
    visibility: str          # visible, hidden or veryHidden, as the workbook says


@dataclass(frozen=True)
class BatchRecord:
    batch_id: str
    source: str              # workbook, ledger or long_format
    file_name: str
    file_sha256: str
    file_bytes: int
    sheets: tuple            # of SheetState, in workbook order; () for a CSV
    creator: Optional[str]
    last_modified_by: Optional[str]
    created: Optional[str]   # the file's own text, never compared with the as-of date
    modified: Optional[str]
    application: Optional[str]
    calculation: str
    has_formulas: bool       # whether the source can hold formulas at all
    external_links: tuple    # the targets of links to other workbooks, as text


@dataclass(frozen=True)
class Pair:
    """A plan batch with the ledger batch it is reconciled against: what is acknowledged and promoted."""
    plan_batch_id: str
    ledger_batch_id: str


@dataclass(frozen=True)
class ControlTotal:
    batch_id: str
    version: Optional[str]   # None for the ledger, which has no version
    period: str
    row_count: int
    amount_sum: Decimal


PLAIN_AMOUNT = re.compile(r"-?\d+(\.\d+)?")
THOUSANDS = re.compile(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?")
BRACKETS = re.compile(r"\(.*\)")
PERIOD = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
# The typed row errors, as fixed codes.
ROW_ERRORS = ("fields", "version_missing", "entity_missing", "account_missing",
              "period_not_yyyy_mm", "amount_missing", "amount_thousands_separator",
              "amount_brackets", "amount_not_plain")


def batch_id_for(sha256: str) -> str:
    return sha256[:BATCH_ID_DIGITS]


def calculation_for(sha256: str, manifest: Path | str | None = DEFAULT_MANIFEST) -> str:
    """Recalculated by the sample build only for a file the build recalculated and checked."""
    if manifest is None:
        return AS_RECEIVED
    try:
        recorded = json.loads(Path(manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AS_RECEIVED
    checked = set(recorded.get("checks", {}))
    for name, entry in recorded.get("files", {}).items():
        if name in checked and entry.get("sha256") == sha256:
            return CALCULATED_BY_BUILD
    return AS_RECEIVED


def control_totals(batch_id: str, rows: Iterable, version_of=lambda row: row.version) -> tuple:
    """Per version and period, in order of first appearance: the row count and the amount sum.

    Every row is counted, an empty cell or a row error included; the sum is of
    the amounts there are. It is a checksum over every figure of the version
    and period, whatever its unit, not a financial total.
    """
    totals: OrderedDict = OrderedDict()
    for row in rows:
        key = (version_of(row), row.period)
        count, amount = totals.get(key, (0, Decimal(0)))
        totals[key] = (count + 1, amount + (row.amount if row.amount is not None else 0))
    return tuple(ControlTotal(batch_id, version, period, count, amount)
                 for (version, period), (count, amount) in totals.items())


def parse_amount(text: str) -> tuple[Optional[Decimal], Optional[str]]:
    """(amount, None) for a plain decimal; (None, the row error) for anything else."""
    if text == "":
        return None, "amount_missing"
    if PLAIN_AMOUNT.fullmatch(text):
        return Decimal(text), None
    if THOUSANDS.fullmatch(text):
        return None, "amount_thousands_separator"
    if BRACKETS.fullmatch(text):
        return None, "amount_brackets"
    return None, "amount_not_plain"


def read_csv(path: Path, header: tuple) -> list[tuple[int, list[str]]]:
    """(line number, fields) of every line after the header; blank lines are skipped."""
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SourceError("encoding", Path(path).name, "the file is not UTF-8 text") from None
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        first = next(reader, None)
        if first is None or tuple(first) != header:
            raise SourceError("header", Path(path).name,
                              f"the first line must be exactly {','.join(header)}")
        return [(reader.line_num, fields) for fields in reader if fields]
    except csv.Error as error:
        raise SourceError("unreadable", Path(path).name,
                          f"the file cannot be read as CSV ({error})") from None


def csv_batch(path: Path, source: str, sha256: str) -> BatchRecord:
    """The batch record of a CSV source: it has no sheets, no properties and no formulas."""
    path = Path(path)
    return BatchRecord(
        batch_id=batch_id_for(sha256), source=source, file_name=path.name, file_sha256=sha256,
        file_bytes=path.stat().st_size, sheets=(), creator=None, last_modified_by=None,
        created=None, modified=None, application=None, calculation=NO_FORMULAS,
        has_formulas=False, external_links=())
