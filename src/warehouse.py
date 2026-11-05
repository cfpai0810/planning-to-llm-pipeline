# =============================================================================
# src/warehouse.py: landing in the warehouse (DuckDB)
# =============================================================================
"""
Lands what the readers return in raw tables (brief 5.4):

- raw_batch: one row per file, its batch record;
- raw_plan_cell: one row per plan cell (a workbook's block cells, or a
  long-format file's lines), in the order the reader returned them;
- raw_ledger_line: one row per line of a ledger extract;
- raw_control_total: per batch, version and period, the row count and the
  sum of the amounts, as the reader computed them in Python before landing;
- raw_list_account and raw_list_entity (part 1d): a workbook's Lists, its
  accounts and its entities as the extractor read them, each with whether
  its names pass the allow-list. Reconcile and the star schema read the
  accounts' ledger signs and the entities from here, so the warehouse holds
  everything they need.

The raw tables are append only: this module only ever inserts into them, and
a test scans src/ and sql/ for any statement that would change or remove a
landed row. A batch id comes from the file's content hash, so landing the
same file twice returns AlreadyLanded and adds no row.

How a batch lands (the reviewer's ruling on the build time, 5 November 2026):
one transaction for the whole batch, every table of it included, so all of it
lands or none; and one INSERT per table, which sends each column as one bound
list and unnests the lists side by side. Every value is a bound parameter,
never part of the SQL text. The CREATE statements declare no key and no
index: the build creates its indexes after the load (a unique index on
raw_batch's batch id, so DuckDB itself refuses a batch landed twice by any
route; land() still checks first and returns AlreadyLanded).

Money is DECIMAL(38, 12): 26 digits before the point and 12 after, which
holds whole euros, cents, a headcount of 63.5 and any amount the readers give
exactly. Amounts are passed to DuckDB as text and cast, so no float is ever
involved. An amount the type could not hold exactly refuses the whole batch
before any row is written (AmountOutOfRange); nothing is rounded.

DuckDB is opened in one function, connect(), and nowhere else
(tests/test_boundaries.py). DuckDB has an HTTP client of its own, which the
test suite's network guard cannot see, so every connection is opened with
extension autoinstall and autoload off, community and unsigned extensions
refused, external access off (no file but the database itself, so no
read_csv, COPY or ATTACH) and the configuration locked, so that no SQL can
switch any of it back on. tests/test_warehouse.py reads those settings back
from DuckDB itself. The read-only connection factory of Phase 2 will sit
beside connect().

The start-up build (src/startup.py) lands the committed sample workbook and
the ledger, and nothing else: the clean workbook and the fixtures are landed
by the tests only. It builds into a fresh folder and closes the file; it is
reopened with connect(). No database file is committed.

DuckDB's Python binding imports numpy, pandas and pyarrow, when they are
installed, the first time a statement is given a bound parameter. That is
import time spent once per process, inside the first landing; this module
never imports them (scripts/time_build.py measures it).
"""

from __future__ import annotations

import json
from dataclasses import astuple, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional, Union

import duckdb

from src import names as allow_list
from src.batch import BatchRecord
from src.extract import Extraction
from src.ledger import LedgerExtraction
from src.long_format import LongFormatExtraction

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_WORKBOOK = ROOT / "data" / "planning_model.xlsx"
COMMITTED_LEDGER = ROOT / "data" / "erp_trial_balance.csv"
DATABASE_NAME = "warehouse.duckdb"

MONEY_DIGITS, MONEY_SCALE = 38, 12
MONEY = f"DECIMAL({MONEY_DIGITS},{MONEY_SCALE})"
SETTINGS = {
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "allow_community_extensions": False,
    "allow_unsigned_extensions": False,
    "enable_external_access": False,
    "lock_configuration": True,
}

# Each raw table's columns, in order: (name, type, NOT NULL or "").
COLUMNS = {
    "raw_batch": (
        ("batch_id", "VARCHAR", "NOT NULL"), ("source", "VARCHAR", "NOT NULL"),
        ("file_name", "VARCHAR", "NOT NULL"), ("file_sha256", "VARCHAR", "NOT NULL"),
        ("file_bytes", "BIGINT", "NOT NULL"), ("sheets", "VARCHAR", "NOT NULL"),
        ("creator", "VARCHAR", ""), ("last_modified_by", "VARCHAR", ""),
        ("created", "VARCHAR", ""), ("modified", "VARCHAR", ""),
        ("application", "VARCHAR", ""), ("calculation", "VARCHAR", "NOT NULL"),
        ("has_formulas", "BOOLEAN", "NOT NULL"), ("external_links", "VARCHAR", "NOT NULL"),
        ("row_count", "BIGINT", "NOT NULL")),
    "raw_plan_cell": (
        ("batch_id", "VARCHAR", "NOT NULL"), ("row_number", "INTEGER", "NOT NULL"),
        ("sheet", "VARCHAR", ""), ("address", "VARCHAR", ""), ("block", "VARCHAR", ""),
        ("source_line", "INTEGER", ""), ("version", "VARCHAR", ""), ("entity", "VARCHAR", ""),
        ("line", "VARCHAR", ""), ("account", "VARCHAR", ""), ("period", "VARCHAR", ""),
        ("column_kind", "VARCHAR", ""), ("unit", "VARCHAR", ""), ("kind", "VARCHAR", "NOT NULL"),
        ("formula", "VARCHAR", ""), ("cached", "BOOLEAN", ""),
        ("value_type", "VARCHAR", "NOT NULL"), ("value_text", "VARCHAR", ""),
        ("amount", MONEY, ""), ("row_error", "VARCHAR", ""), ("duplicate", "BOOLEAN", "NOT NULL"),
        ("unknown_entity", "BOOLEAN", "NOT NULL"), ("unknown_account", "BOOLEAN", "NOT NULL"),
        ("name_allowed", "BOOLEAN", "NOT NULL")),
    "raw_ledger_line": (
        ("batch_id", "VARCHAR", "NOT NULL"), ("line_number", "INTEGER", "NOT NULL"),
        ("entity", "VARCHAR", ""), ("period", "VARCHAR", ""), ("account", "VARCHAR", ""),
        ("amount", MONEY, ""), ("amount_text", "VARCHAR", ""), ("row_error", "VARCHAR", ""),
        ("duplicate", "BOOLEAN", "NOT NULL"), ("unknown_entity", "BOOLEAN", "NOT NULL"),
        ("unknown_account", "BOOLEAN", "NOT NULL")),
    "raw_control_total": (
        ("batch_id", "VARCHAR", "NOT NULL"), ("version", "VARCHAR", ""),
        ("period", "VARCHAR", ""), ("row_count", "BIGINT", "NOT NULL"),
        ("amount_sum", MONEY, "NOT NULL")),
    "raw_list_account": (
        ("batch_id", "VARCHAR", "NOT NULL"), ("list_row", "INTEGER", "NOT NULL"),
        ("code", "VARCHAR", ""), ("name", "VARCHAR", ""), ("parent", "VARCHAR", ""),
        ("section", "VARCHAR", ""), ("polarity", "VARCHAR", ""), ("unit", "VARCHAR", ""),
        ("ledger_sign", "VARCHAR", ""), ("kind", "VARCHAR", ""), ("address", "VARCHAR", ""),
        ("name_allowed", "BOOLEAN", "NOT NULL")),
    "raw_list_entity": (
        ("batch_id", "VARCHAR", "NOT NULL"), ("list_row", "INTEGER", "NOT NULL"),
        ("code", "VARCHAR", ""), ("name", "VARCHAR", ""), ("region", "VARCHAR", ""),
        ("address", "VARCHAR", ""), ("name_allowed", "BOOLEAN", "NOT NULL")),
}
RAW_TABLES = tuple(COLUMNS)

# No key and no index here: the build creates the indexes after the load.
TABLES = tuple(
    f"CREATE TABLE IF NOT EXISTS {table} (\n"
    + ",\n".join(f"    {name} {kind}{' ' + rule if rule else ''}" for name, kind, rule in columns)
    + "\n)" for table, columns in COLUMNS.items())
INDEXES = ("CREATE UNIQUE INDEX IF NOT EXISTS raw_batch_batch_id ON raw_batch (batch_id)",)

# The position of the money column in each table's rows, as landed.
PLAN_AMOUNT, LEDGER_AMOUNT, TOTAL_AMOUNT = 18, 5, 4
Source = Union[Extraction, LedgerExtraction, LongFormatExtraction]


class WarehouseError(Exception):
    """A warehouse that cannot be built or landed in, told in one plain line."""


class AmountOutOfRange(WarehouseError):
    """An amount the money type cannot hold exactly: the batch is refused and nothing lands."""


@dataclass(frozen=True)
class Landed:
    batch_id: str
    file_name: str
    rows: int


@dataclass(frozen=True)
class AlreadyLanded:
    batch_id: str
    file_name: str


def connect(path: Path | str) -> duckdb.DuckDBPyConnection:
    """The one place DuckDB is opened: no extension downloads, no file but the database itself."""
    return duckdb.connect(str(path), config=dict(SETTINGS))


def create_tables(con: duckdb.DuckDBPyConnection) -> None:
    for statement in TABLES:
        con.execute(statement)


def create_indexes(con: duckdb.DuckDBPyConnection) -> None:
    """The raw tables' indexes, created once the batches have landed."""
    for statement in INDEXES:
        con.execute(statement)


# == Money ===================================================================================

def fits(amount: Decimal) -> bool:
    """True when DECIMAL(38, 12) holds the amount exactly."""
    if not amount.is_finite():
        return False
    _, digits, exponent = amount.normalize().as_tuple()
    places = max(0, -exponent)
    whole_digits = max(0, len(digits) + exponent)
    return places <= MONEY_SCALE and whole_digits <= MONEY_DIGITS - MONEY_SCALE


def _money(amount: Optional[Decimal]) -> Optional[str]:
    return None if amount is None else format(amount, "f")


def _check_money(batch: BatchRecord, rows, where) -> None:
    for row in rows:
        if row.amount is not None and not fits(row.amount):
            raise AmountOutOfRange(f"{batch.file_name}: the amount {row.amount} at {where(row)} "
                                   f"cannot be stored exactly as {MONEY}; nothing was landed")


# == Landing =================================================================================

def insert_rows(con, table: str, columns, rows: list[tuple], money_column: int = -1) -> None:
    """All the rows of one table in one INSERT: each column is one bound list, unnested.

    columns are the table's (name, type, rule) in order. DuckDB unnests the
    lists of one SELECT side by side, so row i takes the i-th item of every
    list. The money column goes as a list of text and is cast to
    DECIMAL(38, 12) by DuckDB, so no amount is ever a float. The promotion
    gate's tables are written the same way (src/promotion.py).
    """
    if not rows:
        return
    select = ", ".join(f"CAST(unnest(?::VARCHAR[]) AS {MONEY})" if i == money_column
                       else f"unnest(?::{kind}[])" for i, (_, kind, _) in enumerate(columns))
    con.execute(f"INSERT INTO {table} SELECT {select}", [list(column) for column in zip(*rows)])


def _insert(con, table: str, rows: list[tuple], money_column: int) -> None:
    insert_rows(con, table, COLUMNS[table], rows, money_column)


def _allowed(*names) -> bool:
    return all(allow_list.allowed(name) for name in names if name)


def _list_rows(source: Extraction) -> tuple[list, list]:
    """The Lists rows of a workbook batch, with whether each one's names pass the allow-list."""
    batch_id = source.batch.batch_id
    accounts = [(batch_id, number, a.code, a.name, a.parent, a.section, a.polarity, a.unit,
                 a.ledger_sign, a.kind, a.address, _allowed(a.code, a.name, a.parent))
                for number, a in enumerate(source.lists.accounts, start=1)]
    entities = [(batch_id, number, e.code, e.name, e.region, e.address, _allowed(e.code, e.name))
                for number, e in enumerate(source.lists.entities, start=1)]
    return accounts, entities


def _batch_row(batch: BatchRecord, rows: int) -> tuple:
    return (batch.batch_id, batch.source, batch.file_name, batch.file_sha256, batch.file_bytes,
            json.dumps([{"name": s.name, "visibility": s.visibility} for s in batch.sheets],
                       ensure_ascii=False),
            batch.creator, batch.last_modified_by, batch.created, batch.modified,
            batch.application, batch.calculation, batch.has_formulas,
            json.dumps(list(batch.external_links), ensure_ascii=False), rows)


def land(con: duckdb.DuckDBPyConnection, source: Source) -> Union[Landed, AlreadyLanded]:
    """Appends one batch to the raw tables, in one transaction; a batch already there adds nothing."""
    batch = source.batch
    if con.execute("SELECT count(*) FROM raw_batch WHERE batch_id = ?",
                   [batch.batch_id]).fetchone()[0]:
        return AlreadyLanded(batch.batch_id, batch.file_name)
    if isinstance(source, LedgerExtraction):
        rows, table, money = source.lines, "raw_ledger_line", LEDGER_AMOUNT
        _check_money(batch, rows, lambda line: f"line {line.line_number}")
        values = [tuple(astuple(line)) for line in rows]
    else:
        rows, table, money = source.cells, "raw_plan_cell", PLAN_AMOUNT
        _check_money(batch, rows, lambda cell: (f"{cell.sheet}!{cell.address}" if cell.sheet
                                                else f"line {cell.source_line}"))
        values = [(cell.batch_id, number, *astuple(cell)[1:]) for number, cell in
                  enumerate(rows, start=1)]
    totals = source.control_totals
    if (any(row[0] != batch.batch_id for row in values)
            or any(t.batch_id != batch.batch_id for t in totals)):
        raise WarehouseError(f"{batch.file_name}: some rows belong to another batch than "
                             f"{batch.batch_id}; nothing was landed")
    for total in totals:
        if not fits(total.amount_sum):
            raise AmountOutOfRange(f"{batch.file_name}: the control total {total.amount_sum} "
                                   f"cannot be stored exactly as {MONEY}; nothing was landed")
    values = [tuple(_money(v) if i == money else v for i, v in enumerate(row)) for row in values]
    total_rows = [(t.batch_id, t.version, t.period, t.row_count, _money(t.amount_sum))
                  for t in totals]
    accounts, entities = _list_rows(source) if isinstance(source, Extraction) else ([], [])
    con.begin()
    try:
        _insert(con, "raw_batch", [_batch_row(batch, len(rows))], money_column=-1)
        _insert(con, table, values, money)
        _insert(con, "raw_control_total", total_rows, TOTAL_AMOUNT)
        _insert(con, "raw_list_account", accounts, money_column=-1)
        _insert(con, "raw_list_entity", entities, money_column=-1)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return Landed(batch.batch_id, batch.file_name, len(rows))
