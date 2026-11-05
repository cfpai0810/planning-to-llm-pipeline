# =============================================================================
# src/extract.py: the workbook extractor
# =============================================================================
"""
Reads a planning workbook into typed rows and the facts the controls need
(brief 5.1). It reads by anchors and never by a fixed address:

- plan sheets by their name, Plan_ followed by the entity code;
- blocks by their defined names, <version>_<entity> on that entity's plan
  sheet; where a block has no defined name, by its header labels: a
  "Line item" header with the block's title in the cell above it, running
  right to the last header and down to the first empty row;
- months by their header cells (2026-01 to 2026-12; a date header is read as
  its month) and the total column by its FY header; the label and unit
  columns by their "Line item" and "Unit" headers;
- accounts by their labels, matched to the names on Lists, whose own tables
  are found by the defined names Accounts and Entities, or by their headers.

The workbook is read twice, once for formulas and once for the cached values
the file holds, and the two are joined cell by cell. The pipeline never
recalculates a file: a formula with no cached value is recorded as such.
Every block cell becomes one row: version, entity, account, period, amount and
unit, plus the sheet, block, address, kind (formula, constant, array_formula,
or empty), formula text and whether a cached value exists. An array formula
is its own kind and never a constant. An error value (#DIV/0!) is recorded as
an error, with its code as text and no amount.

Money is Decimal from the first cell read: openpyxl gives a float for a
number with a decimal point, so a number goes through its text
(Decimal(str(x))), never Decimal(float).

Beside the rows, the extraction records every fact part 1d's controls read,
so that no control opens the file again: the sheets and their visibility,
hidden rows and columns and what they hold, merged ranges, each block's
columns, lines and units and its header problems (a missing header, a
duplicate period), labels not on Lists, the Lists tables, the defined names,
every formula in the workbook with its cached state, member names the
allow-list rejects, and the external link targets. It reports facts and never
judges them: breaks, severities and messages are part 1d's.

Some of the workbook's own XML (document properties, relationships) is read
here with the standard library. Before any XML in the file is parsed, here or
by openpyxl, every part is checked: a part larger than MAX_PART_BYTES, or a
workbook whose parts add up to more than MAX_TOTAL_BYTES, is refused (a
decompression bomb), and an XML part that declares a document type is
refused, because a DTD is the only route to entity expansion and to external
entities, and a workbook never needs one.
"""

from __future__ import annotations

import math
import posixpath
import re
import warnings
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.worksheet.formula import ArrayFormula
from openpyxl.worksheet.worksheet import Worksheet

from src import names as allow_list
from src.batch import (AS_RECEIVED, CALCULATED_BY_BUILD, DEFAULT_MANIFEST, NO_FORMULAS,
                       BatchRecord, ControlTotal, SheetState, SourceError, batch_id_for,
                       calculation_for, control_totals)
from src.hashing import file_sha256

__all__ = ["AS_RECEIVED", "CALCULATED_BY_BUILD", "NO_FORMULAS", "ExtractError", "Extraction",
           "PlanCell", "BatchRecord", "ControlTotal", "extract_workbook", "to_decimal"]

ExtractError = SourceError

PLAN_SHEET = re.compile(r"^Plan_(?P<entity>.+)$")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TOTAL = re.compile(r"^FY\d{4}$")
LABEL_HEADER, UNIT_HEADER = "Line item", "Unit"
LISTS_SHEET = "Lists"
ACCOUNT_HEADERS = {"Code": "code", "Name": "name", "Parent": "parent", "Section": "section",
                   "Polarity": "polarity", "Unit": "unit", "Ledger sign": "ledger_sign",
                   "Kind": "kind"}
ENTITY_HEADERS = {"Code": "code", "Name": "name", "Region": "region"}
BY_NAME, BY_LABELS = "defined name", "header labels"

MAX_PART_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
DOCUMENT_TYPE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
NS_CORE = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
NS_DC = "{http://purl.org/dc/elements/1.1/}"
NS_TERMS = "{http://purl.org/dc/terms/}"
NS_APP = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"
NS_RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
EXTERNAL_LINK = "/externalLink"


# == The rows and the facts =======================================================

@dataclass(frozen=True)
class PlanCell:
    """One cell of a plan block (or one row of a long-format file): a fact, never a judgement."""
    batch_id: str
    sheet: Optional[str]            # None for a long-format row
    address: Optional[str]          # the cell, such as "E30"; None for a long-format row
    block: Optional[str]            # <version>_<entity>; None for a long-format row
    source_line: Optional[int]      # the CSV line of a long-format row; None for a cell
    version: str
    entity: str
    line: Optional[str]             # the line item's label as the source shows it
    account: Optional[str]          # its code on Lists; None when the label is not on Lists
    period: str                     # 2026-01 to 2026-12, or the total column's header
    column_kind: Optional[str]      # month or total
    unit: Optional[str]
    kind: str                       # formula, constant, array_formula or empty
    formula: Optional[str]          # the formula text, an array formula's included
    cached: Optional[bool]          # formulas: whether the file holds a cached value
    value_type: str                 # number, error, text, boolean, date or none
    value_text: Optional[str]       # an error code or the text of a value that is not a number
    amount: Optional[Decimal]
    row_error: Optional[str]        # long format only: a typed row error
    duplicate: bool                 # another row has the same version, entity, line and period
    unknown_entity: bool            # the entity is not on Lists
    unknown_account: bool           # the label is not on Lists
    name_allowed: bool              # every member name of the row passes the allow-list


@dataclass(frozen=True)
class FormulaCell:
    """Every formula in the workbook, Control and hidden sheets included, with its cached state."""
    sheet: str
    address: str
    kind: str                       # formula or array_formula
    formula: str
    array_ref: Optional[str]
    cached: bool
    value_type: str
    value_text: Optional[str]
    amount: Optional[Decimal]
    block: Optional[str]            # the block it lies in, if any


@dataclass(frozen=True)
class HiddenLine:
    sheet: str
    axis: str                       # row or column
    index: str                      # "53", or "C"
    cells: tuple                    # the addresses of the cells in it that hold something
    blocks: tuple                   # the blocks it crosses


@dataclass(frozen=True)
class SheetFacts:
    name: str
    visibility: str
    hidden_rows: tuple
    hidden_columns: tuple
    merged: tuple                   # merged ranges, such as "M7:N7"
    cells: int                      # cells that hold something
    formulas: int


@dataclass(frozen=True)
class Column:
    letter: str
    header: Optional[str]
    role: str                       # label, unit, month, total, other or missing
    cells: int                      # cells below the header, in the block, that hold something


@dataclass(frozen=True)
class Line:
    row: int
    label: Optional[str]
    unit: Optional[str]
    account: Optional[str]          # the code on Lists, or None


@dataclass(frozen=True)
class HeaderProblem:
    kind: str                       # missing or duplicate
    header: Optional[str]
    columns: tuple


@dataclass(frozen=True)
class Block:
    name: str
    sheet: str
    version: str
    entity: str
    found_by: str                   # defined name or header labels
    ref: str                        # its range, the header row included
    title: Optional[str]            # the label above the header
    columns: tuple
    lines: tuple
    header_problems: tuple
    merged: tuple


@dataclass(frozen=True)
class Account:
    code: Optional[str]
    name: Optional[str]
    parent: Optional[str]
    section: Optional[str]
    polarity: Optional[str]
    unit: Optional[str]
    ledger_sign: Optional[str]
    kind: Optional[str]
    address: str                    # the cell of its name on Lists


@dataclass(frozen=True)
class Entity:
    code: Optional[str]
    name: Optional[str]
    region: Optional[str]
    address: str


@dataclass(frozen=True)
class Lists:
    accounts: tuple
    entities: tuple
    found_by: str                   # defined name, header labels, both, or not found

    def entity_codes(self) -> tuple:
        return tuple(e.code for e in self.entities if e.code)

    def account_codes(self) -> tuple:
        return tuple(a.code for a in self.accounts if a.code)

    def ledger_accounts(self) -> tuple:
        """The accounts with a ledger sign: the ones a ledger extract can hold."""
        return tuple(a.code for a in self.accounts if a.code and a.ledger_sign)

    def code_for(self, name: Optional[str]) -> Optional[str]:
        return next((a.code for a in self.accounts if name is not None and a.name == name), None)


@dataclass(frozen=True)
class DefinedNameFact:
    name: str
    scope: Optional[str]            # the sheet of a sheet-scoped name
    text: str
    destinations: tuple             # "Sheet!$A$1:$B$2" for each range it refers to


@dataclass(frozen=True)
class LabelFact:
    sheet: str
    block: str
    address: str
    label: Optional[str]


@dataclass(frozen=True)
class RejectedName:
    member: str                     # account, account code, parent, entity, entity code, version
    name: str
    reasons: tuple                  # the allow-list's fixed codes
    sheet: Optional[str]
    address: Optional[str]
    source_line: Optional[int] = None


@dataclass(frozen=True)
class Extraction:
    batch: BatchRecord
    cells: tuple
    formulas: tuple
    sheets: tuple
    blocks: tuple
    plan_sheets: tuple              # (sheet, entity) for every Plan_ sheet
    lists: Lists
    defined_names: tuple
    labels_not_on_lists: tuple
    rejected_names: tuple
    control_totals: tuple


# == Values ============================================================================

def to_decimal(value) -> Decimal:
    """A number read from a cell as an exact Decimal, through its text; never Decimal(float)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"not a number: {type(value).__name__}")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("not a finite number")
        return value
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("not a finite number")
    return Decimal(str(value))


def _classify(value, data_type: Optional[str]) -> tuple:
    """(value type, value text, amount) of a value as the file holds it."""
    if value is None:
        return "none", None, None
    if data_type == "e":
        return "error", str(value), None
    if isinstance(value, bool):
        return "boolean", "TRUE" if value else "FALSE", None
    if isinstance(value, (int, float, Decimal)):
        try:
            return "number", None, to_decimal(value)
        except ValueError:
            return "text", str(value), None
    if isinstance(value, (datetime, date, time)):
        return "date", value.isoformat(), None
    if isinstance(value, timedelta):
        return "date", str(value), None
    return "text", str(value), None


def _text(value) -> Optional[str]:
    """A label, code or header as text: a whole float as its digits; empty text as None."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (datetime, date)):
        return f"{value.year:04d}-{value.month:02d}"
    text = str(value)
    return text if text != "" else None


def _formula_text(value) -> str:
    if isinstance(value, ArrayFormula):
        return value.text
    return value if isinstance(value, str) else str(value)


# == The archive: refused parts, document properties and external links ==============

def _archive_facts(path: Path) -> dict:
    """Checks every part, then reads the document properties and the external link targets."""
    name = path.name
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise SourceError("unreadable", name, "not a workbook that can be read "
                          f"({type(error).__name__})") from None
    with archive:
        infos = archive.infolist()
        if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            raise SourceError("too_large", name, "the workbook's parts are too large to read")
        parts = {}
        for info in infos:
            if info.file_size > MAX_PART_BYTES:
                raise SourceError("too_large", name, f"the part {info.filename} is too large to read")
            if info.filename.endswith((".xml", ".rels")) or info.filename.startswith("["):
                try:
                    data = archive.read(info)
                except (zipfile.BadZipFile, OSError, ValueError) as error:
                    raise SourceError("unreadable", name, f"the part {info.filename} cannot be "
                                      f"read ({type(error).__name__})") from None
                if DOCUMENT_TYPE.search(data):
                    raise SourceError("unsafe_xml", name, f"the part {info.filename} declares "
                                      "a document type, which a workbook never needs")
                parts[info.filename] = data
    return {"core": _core(parts.get("docProps/core.xml")),
            "application": _application(parts.get("docProps/app.xml")),
            "external_links": _external_links(parts)}


def _xml(data: Optional[bytes]):
    if data is None:
        return None
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return None


def _core(data: Optional[bytes]) -> dict:
    root = _xml(data)
    tags = {"creator": NS_DC + "creator", "last_modified_by": NS_CORE + "lastModifiedBy",
            "created": NS_TERMS + "created", "modified": NS_TERMS + "modified"}
    found = {}
    for key, tag in tags.items():
        element = root.find(tag) if root is not None else None
        found[key] = element.text if element is not None and element.text else None
    return found


def _application(data: Optional[bytes]) -> Optional[str]:
    root = _xml(data)
    element = root.find(NS_APP + "Application") if root is not None else None
    return element.text if element is not None and element.text else None


def _rels_of(part: str) -> str:
    folder, base = posixpath.split(part)
    return posixpath.join(folder, "_rels", base + ".rels")


def _resolve(base_folder: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(base_folder, target))


def _external_links(parts: dict) -> tuple:
    """The target of every link to another workbook, as the file states it."""
    root = _xml(parts.get("xl/_rels/workbook.xml.rels"))
    if root is None:
        return ()
    targets = []
    for relationship in root.iter(NS_RELS + "Relationship"):
        if not relationship.get("Type", "").endswith(EXTERNAL_LINK):
            continue
        link = _resolve("xl", relationship.get("Target", ""))
        link_rels = _xml(parts.get(_rels_of(link)))
        if link_rels is None:
            continue
        for inner in link_rels.iter(NS_RELS + "Relationship"):
            if inner.get("TargetMode") == "External" and inner.get("Target"):
                targets.append(inner.get("Target"))
    return tuple(targets)


# == Reading one sheet =================================================================

@dataclass
class _Sheet:
    """One worksheet's cells from both readings, by (row, column)."""
    ws: Worksheet
    cells: dict          # (row, column) -> openpyxl cell holding something (formulas reading)
    cached: dict         # (row, column) -> (value, data type) (values reading)

    def text(self, row: int, column: int) -> Optional[str]:
        cell = self.cells.get((row, column))
        return _text(cell.value) if cell is not None else None


def _read_sheet(ws, values_ws) -> _Sheet:
    cells = {(c.row, c.column): c for row in ws.iter_rows() for c in row if c.value is not None}
    cached = {}
    if values_ws is not None:
        cached = {(c.row, c.column): (c.value, c.data_type) for row in values_ws.iter_rows()
                  for c in row if c.value is not None}
    return _Sheet(ws, cells, cached)


def _kind(cell) -> str:
    if cell is None:
        return "empty"
    if cell.data_type == "f":
        return "array_formula" if isinstance(cell.value, ArrayFormula) else "formula"
    return "constant"


def _read(sheet: _Sheet, row: int, column: int) -> dict:
    """Kind, formula text, cached state, value type, value text and amount of one cell."""
    cell = sheet.cells.get((row, column))
    kind = _kind(cell)
    if kind == "empty":
        return {"kind": kind, "formula": None, "cached": None, "value_type": "none",
                "value_text": None, "amount": None}
    if kind == "constant":
        value_type, value_text, amount = _classify(cell.value, cell.data_type)
        return {"kind": kind, "formula": None, "cached": None, "value_type": value_type,
                "value_text": value_text, "amount": amount}
    value, data_type = sheet.cached.get((row, column), (None, None))
    value_type, value_text, amount = _classify(value, data_type)
    return {"kind": kind, "formula": _formula_text(cell.value), "cached": value is not None,
            "value_type": value_type, "value_text": value_text, "amount": amount}


# == The Lists tab ======================================================================

def _table(sheet: _Sheet, header_row: int, first: int, last: int, last_row: int,
           headers: dict, stop_at_empty: bool) -> list[dict]:
    """The rows of a Lists table: to the end of its defined name, or to its first empty row."""
    columns = {}
    for column in range(first, last + 1):
        key = headers.get(sheet.text(header_row, column) or "")
        if key and key not in columns:
            columns[key] = column
    rows = []
    for row in range(header_row + 1, last_row + 1):
        record = {key: sheet.text(row, column) for key, column in columns.items()}
        if record.get("code") is None and record.get("name") is None:
            if stop_at_empty:
                break
            continue
        name_column = columns.get("name", first)
        record["address"] = f"{get_column_letter(name_column)}{row}"
        rows.append(record)
    return rows


def _find_table(sheet: _Sheet, headers: dict, required: tuple) -> Optional[tuple]:
    """(header row, first column, last column) of the first row holding every required header."""
    rows: dict = {}
    for (row, column), cell in sheet.cells.items():
        rows.setdefault(row, {})[_text(cell.value)] = column
    for row in sorted(rows):
        if all(label in rows[row] for label in required):
            first = min(rows[row][label] for label in required)
            last = max(c for label, c in rows[row].items() if label in headers)
            return row, first, last
    return None


def _read_lists(sheets: dict, names: list) -> Lists:
    found_by = set()

    def table(name: str, headers: dict, required: tuple) -> list[dict]:
        destination = _single_destination(names, name)
        if destination and destination[0] in sheets:
            sheet = sheets[destination[0]]
            first, top, last, bottom = destination[1]
            found_by.add(BY_NAME)
            return _table(sheet, top, first, last, bottom, headers, stop_at_empty=False)
        if LISTS_SHEET in sheets:
            sheet = sheets[LISTS_SHEET]
            located = _find_table(sheet, headers, required)
            if located:
                found_by.add(BY_LABELS)
                row, first, last = located
                return _table(sheet, row, first, last, sheet.ws.max_row, headers,
                              stop_at_empty=True)
        return []

    accounts = table("Accounts", ACCOUNT_HEADERS, ("Code", "Name", "Parent"))
    entities = table("Entities", ENTITY_HEADERS, ("Code", "Name", "Region"))
    label = (BY_NAME if found_by == {BY_NAME} else BY_LABELS if found_by == {BY_LABELS}
             else "both" if found_by else "not found")
    return Lists(
        accounts=tuple(Account(**{k: r.get(k) for k in ACCOUNT_HEADERS.values()},
                               address=r["address"]) for r in accounts),
        entities=tuple(Entity(**{k: r.get(k) for k in ENTITY_HEADERS.values()},
                              address=r["address"]) for r in entities),
        found_by=label)


# == Defined names and blocks ============================================================

def _defined_names(wb) -> list[DefinedNameFact]:
    found = []

    def fact(defined, scope):
        try:
            destinations = tuple(f"{sheet}!{ref}" for sheet, ref in defined.destinations)
        except (ValueError, TypeError, AttributeError):
            destinations = ()
        return DefinedNameFact(defined.name, scope, str(defined.attr_text), destinations)

    for defined in wb.defined_names.values():
        found.append(fact(defined, None))
    for ws in wb.worksheets:
        for defined in ws.defined_names.values():
            found.append(fact(defined, ws.title))
    return found


def _bounds(destination: str) -> Optional[tuple]:
    sheet, _, ref = destination.rpartition("!")
    try:
        first, top, last, bottom = range_boundaries(ref.replace("$", ""))
    except (ValueError, TypeError):
        return None
    if None in (first, top, last, bottom):
        return None
    return sheet, (first, top, last, bottom)


def _single_destination(names: list, name: str) -> Optional[tuple]:
    matches = [n for n in names if n.name == name and len(n.destinations) == 1]
    return _bounds(matches[0].destinations[0]) if len(matches) == 1 else None


def _role(header: Optional[str]) -> str:
    if header is None:
        return "missing"
    if header == LABEL_HEADER:
        return "label"
    if header == UNIT_HEADER:
        return "unit"
    if MONTH.match(header):
        return "month"
    if TOTAL.match(header):
        return "total"
    return "other"


def _block_ranges(sheet: _Sheet, entity: str, names: list) -> list[tuple]:
    """(version, found by, (first, top, last, bottom)) of every block on a plan sheet."""
    title = sheet.ws.title
    pattern = re.compile(rf"^(?P<version>.+)_{re.escape(entity)}$")
    blocks = []
    for name in names:
        match = pattern.match(name.name)
        if match and len(name.destinations) == 1 and name.scope in (None, title):
            bounds = _bounds(name.destinations[0])
            if bounds and bounds[0] == title:
                blocks.append((match["version"], BY_NAME, bounds[1]))
    named = {version for version, _, _ in blocks}
    for (row, column), cell in sorted(sheet.cells.items()):
        if _text(cell.value) != LABEL_HEADER or row == 1:
            continue
        version = sheet.text(row - 1, column)
        if version is None or version in named:
            continue
        headers = [c for (r, c) in sheet.cells if r == row and c >= column]
        last = max(headers)
        bottom = row
        while any((bottom + 1, c) in sheet.cells for c in range(column, last + 1)):
            bottom += 1
        blocks.append((version, BY_LABELS, (column, row, last, bottom)))
        named.add(version)
    return sorted(blocks, key=lambda b: (b[2][1], b[2][0]))


def _overlaps(merged: str, bounds: tuple) -> bool:
    first, top, last, bottom = range_boundaries(merged)
    b_first, b_top, b_last, b_bottom = bounds
    return not (last < b_first or first > b_last or bottom < b_top or top > b_bottom)


def _block(sheet: _Sheet, entity: str, version: str, found_by: str, bounds: tuple,
           lists: Lists, merged: tuple) -> tuple:
    """The block's facts and its rows (as dictionaries, the batch fields added later)."""
    first, top, last, bottom = bounds
    name = f"{version}_{entity}"
    headers = {c: sheet.text(top, c) for c in range(first, last + 1)}
    roles = {c: _role(h) for c, h in headers.items()}
    label_column = next((c for c, r in roles.items() if r == "label"), first)
    unit_column = next((c for c, r in roles.items() if r == "unit"), None)
    lines, rows = [], []
    for row in range(top + 1, bottom + 1):
        label = sheet.text(row, label_column)
        if label is None and not any((row, c) in sheet.cells for c in range(first, last + 1)):
            continue
        unit = sheet.text(row, unit_column) if unit_column else None
        account = lists.code_for(label)
        lines.append(Line(row, label, unit, account))
        for column in range(first, last + 1):
            if roles[column] not in ("month", "total"):
                continue
            rows.append({"sheet": sheet.ws.title, "address": f"{get_column_letter(column)}{row}",
                         "block": name, "version": version, "entity": entity, "line": label,
                         "account": account, "period": headers[column],
                         "column_kind": roles[column], "unit": unit,
                         **_read(sheet, row, column)})
    columns = tuple(Column(get_column_letter(c), headers[c], roles[c],
                           sum(1 for r in range(top + 1, bottom + 1) if (r, c) in sheet.cells))
                    for c in range(first, last + 1))
    problems = [HeaderProblem("missing", None, (col.letter,)) for col in columns
                if col.role == "missing"]
    seen = Counter(col.header for col in columns if col.role in ("month", "total"))
    for header in [h for h, n in seen.items() if n > 1]:
        problems.append(HeaderProblem("duplicate", header, tuple(
            col.letter for col in columns if col.header == header)))
    problems.sort(key=lambda p: next(i for i, col in enumerate(columns)
                                     if col.letter == p.columns[0]))
    ref = f"{get_column_letter(first)}{top}:{get_column_letter(last)}{bottom}"
    block = Block(name, sheet.ws.title, version, entity, found_by, ref,
                  sheet.text(top - 1, first) if top > 1 else None, columns, tuple(lines),
                  tuple(problems), tuple(m for m in merged if _overlaps(m, bounds)))
    return block, rows


def _hidden(sheet: _Sheet, blocks: list) -> tuple[tuple, tuple]:
    ws = sheet.ws
    rows = sorted(r for r, d in ws.row_dimensions.items() if d.hidden)
    columns = set()
    for key, d in ws.column_dimensions.items():
        if d.hidden:
            low = d.min or range_boundaries(f"{key}1")[0]
            columns.update(range(low, (d.max or low) + 1))
    on_sheet = [(b.name, range_boundaries(b.ref)) for b in blocks if b.sheet == ws.title]
    hidden_rows = tuple(HiddenLine(
        ws.title, "row", str(r),
        tuple(f"{get_column_letter(c)}{r}" for (rr, c) in sorted(sheet.cells) if rr == r),
        tuple(n for n, (_, top, _, bottom) in on_sheet if top <= r <= bottom)) for r in rows)
    hidden_columns = tuple(HiddenLine(
        ws.title, "column", get_column_letter(c),
        tuple(f"{get_column_letter(c)}{r}" for (r, cc) in sorted(sheet.cells) if cc == c),
        tuple(n for n, (first, _, last, _) in on_sheet if first <= c <= last))
        for c in sorted(columns))
    return hidden_rows, hidden_columns


# == The members the allow-list checks ===================================================

def _rejected(lists: Lists, blocks: list, labels: list) -> tuple:
    candidates = []
    for a in lists.accounts:
        candidates += [("account code", a.code, LISTS_SHEET, a.address),
                       ("account", a.name, LISTS_SHEET, a.address),
                       ("parent", a.parent, LISTS_SHEET, a.address)]
    for e in lists.entities:
        candidates += [("entity code", e.code, LISTS_SHEET, e.address),
                       ("entity", e.name, LISTS_SHEET, e.address)]
    for b in blocks:
        candidates.append(("version", b.version, b.sheet, b.ref.split(":")[0]))
    for label in labels:
        candidates.append(("account", label.label, label.sheet, label.address))
    seen, rejected = set(), []
    for member, name, sheet, address in candidates:
        if name is None or (member, name) in seen:
            continue
        seen.add((member, name))
        reasons = allow_list.check(name)
        if reasons:
            rejected.append(RejectedName(member, name, reasons, sheet, address))
    return tuple(rejected)


# == The extractor =========================================================================

def extract_workbook(path: Path | str, manifest: Path | str | None = DEFAULT_MANIFEST) -> Extraction:
    """Every block cell as a row, the batch record, and the facts the controls read."""
    path = Path(path)
    archive = _archive_facts(path)
    sha = file_sha256(path)
    batch_id = batch_id_for(sha)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            formulas_wb = load_workbook(path, data_only=False, keep_links=False)
            values_wb = load_workbook(path, data_only=True, keep_links=False)
    except Exception as error:      # openpyxl raises many kinds on a damaged file
        raise SourceError("unreadable", path.name, "not a workbook that can be read "
                          f"({type(error).__name__})") from None

    sheet_states = tuple(SheetState(name, formulas_wb[name].sheet_state)
                         for name in formulas_wb.sheetnames)
    sheets = {ws.title: _read_sheet(ws, values_wb[ws.title] if ws.title in values_wb else None)
              for ws in formulas_wb.worksheets}
    names = _defined_names(formulas_wb)
    lists = _read_lists(sheets, names)
    entities_on_lists = set(lists.entity_codes())

    plan_sheets, blocks, raw_rows = [], [], []
    merged_of = {title: tuple(str(m) for m in sorted(s.ws.merged_cells.ranges,
                                                     key=lambda m: (m.min_row, m.min_col)))
                 for title, s in sheets.items()}
    for title, sheet in sheets.items():
        match = PLAN_SHEET.match(title)
        if not match:
            continue
        entity = match["entity"]
        plan_sheets.append((title, entity))
        for version, found_by, bounds in _block_ranges(sheet, entity, names):
            block, rows = _block(sheet, entity, version, found_by, bounds, lists, merged_of[title])
            blocks.append(block)
            raw_rows += rows

    keys = Counter((r["version"], r["entity"], r["line"], r["period"]) for r in raw_rows)
    cells = tuple(PlanCell(
        batch_id=batch_id, source_line=None, row_error=None,
        duplicate=keys[(r["version"], r["entity"], r["line"], r["period"])] > 1,
        unknown_entity=r["entity"] not in entities_on_lists,
        unknown_account=r["account"] is None,
        name_allowed=all(allow_list.allowed(n) for n in (r["version"], r["entity"], r["line"])
                         if n),
        **r) for r in raw_rows)

    def label_letter(block):
        return next((c.letter for c in block.columns if c.role == "label"),
                    block.columns[0].letter)

    labels = tuple(LabelFact(b.sheet, b.name, f"{label_letter(b)}{line.row}", line.label)
                   for b in blocks for line in b.lines if line.account is None)
    block_bounds = [(b.sheet, b.name, range_boundaries(b.ref)) for b in blocks]

    def block_of(sheet_title, row, column):
        return next((name for s, name, (first, top, last, bottom) in block_bounds
                     if s == sheet_title and first <= column <= last and top <= row <= bottom),
                    None)

    formulas, sheet_facts = [], []
    for title, sheet in sheets.items():
        for (row, column), cell in sorted(sheet.cells.items()):
            if cell.data_type != "f":
                continue
            read = _read(sheet, row, column)
            formulas.append(FormulaCell(
                title, cell.coordinate, read["kind"], read["formula"],
                cell.value.ref if isinstance(cell.value, ArrayFormula) else None, read["cached"],
                read["value_type"], read["value_text"], read["amount"],
                block_of(title, row, column)))
        hidden_rows, hidden_columns = _hidden(sheet, blocks)
        sheet_facts.append(SheetFacts(
            title, sheet.ws.sheet_state, hidden_rows, hidden_columns, merged_of[title],
            len(sheet.cells), sum(1 for c in sheet.cells.values() if c.data_type == "f")))

    core = archive["core"]
    batch = BatchRecord(
        batch_id=batch_id, source="workbook", file_name=path.name, file_sha256=sha,
        file_bytes=path.stat().st_size, sheets=sheet_states, creator=core["creator"],
        last_modified_by=core["last_modified_by"], created=core["created"],
        modified=core["modified"], application=archive["application"],
        calculation=calculation_for(sha, manifest), has_formulas=True,
        external_links=archive["external_links"])
    return Extraction(
        batch=batch, cells=cells, formulas=tuple(formulas), sheets=tuple(sheet_facts),
        blocks=tuple(blocks), plan_sheets=tuple(plan_sheets), lists=lists,
        defined_names=tuple(names), labels_not_on_lists=labels,
        rejected_names=_rejected(lists, blocks, labels),
        control_totals=control_totals(batch_id, cells))
