# =============================================================================
# src/controls.py: the five spreadsheet controls
# =============================================================================
"""
The five controls of brief 6.1 over what the extractor read. No control opens
the file again, recalculates it or changes it: each reads the rows and facts
of an extraction (src/extract.py, src/long_format.py) and returns typed breaks.

- Structure: the tabs, the block headers, the periods, accounts not on Lists,
  merged cells inside a plan block, line items with no unit, and member names
  that fail the allow-list.
- Calculation state: formulas with no cached value, and error values.
- Formula integrity: typed constants in formula rows, numeric constants inside
  formulas, and formulas that differ from the pattern of their row.
- Totals: each FY cell against its own row's months, and the subtotals month
  by month, recomputed from the extracted cells and Lists.
- Hidden content and links: hidden sheets, rows and columns, and links to
  other workbooks.

A break carries a stable id built from the control and the anchors, the
control, its kind and every reason that caught it, a severity, the location
(sheet, block, entity, account, period, and the cell address where there is
one), a message made of fixed templates filled with values, and the evidence
(both patterns, or both totals). There is one break per cell per control: a
cell caught by two rules of one control is one break that lists both reasons.

The rules, the severities and the templates are stated in docs/controls.md.
Facts about the file (its times, the application that saved it, its creator)
are data, never findings, and this module does not read them. An external
link's target appears in its break's evidence and nowhere else.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from openpyxl.formula.tokenizer import Token, Tokenizer, TokenizerError
from openpyxl.formula.translate import Translator, TranslatorError
from openpyxl.utils import column_index_from_string, get_column_letter, range_boundaries
from openpyxl.utils.cell import coordinate_from_string

from src import names as allow_list
from src.extract import LABEL_HEADER, PLAN_SHEET, UNIT_HEADER

__all__ = ["CONTROLS", "KINDS", "SEVERITY", "Break", "ControlReport", "ControlResult",
           "RowPattern", "calculation_state", "formula_integrity", "formula_row",
           "hidden_content", "numeric_literals", "pattern_of", "row_pattern", "run_controls",
           "structure", "totals"]

STRUCTURE = "structure"
CALCULATION_STATE = "calculation_state"
FORMULA_INTEGRITY = "formula_integrity"
TOTALS = "totals"
HIDDEN_CONTENT = "hidden_content"
CONTROLS = (STRUCTURE, CALCULATION_STATE, FORMULA_INTEGRITY, TOTALS, HIDDEN_CONTENT)
CONTROL_NAMES = {STRUCTURE: "Structure", CALCULATION_STATE: "Calculation state",
                 FORMULA_INTEGRITY: "Formula integrity", TOTALS: "Totals",
                 HIDDEN_CONTENT: "Hidden content and links"}
ID_PREFIX = {STRUCTURE: "ST", CALCULATION_STATE: "CS", FORMULA_INTEGRITY: "FI", TOTALS: "TO",
             HIDDEN_CONTENT: "HC"}

# The kinds of each control, in the fixed order in which a break lists its reasons.
KINDS = {
    STRUCTURE: ("missing_tab", "entity_not_on_lists", "tab_not_on_list", "header_not_found",
                "duplicate_period", "period_gap", "account_not_on_lists", "rejected_name",
                "no_unit", "merged_cells"),
    CALCULATION_STATE: ("no_cached_value", "error_value"),
    FORMULA_INTEGRITY: ("typed_constant", "embedded_constant", "pattern"),
    TOTALS: ("fy_total", "subtotal"),
    HIDDEN_CONTENT: ("hidden_sheet", "hidden_row", "hidden_column", "external_link"),
}

# Severity: high when a figure as read is wrong or cannot be read, medium when
# a figure may be wrong or a member is left out, low when a person should look
# but nothing is shown to be wrong. A break with several reasons takes the
# highest. A hidden row or column that crosses a plan block is high: it hides
# figures the plan is read from.
HIGH, MEDIUM, LOW = "high", "medium", "low"
SEVERITIES = (HIGH, MEDIUM, LOW)
SEVERITY = {
    "missing_tab": HIGH, "header_not_found": HIGH, "duplicate_period": HIGH,
    "period_gap": HIGH, "entity_not_on_lists": MEDIUM, "account_not_on_lists": MEDIUM,
    "rejected_name": MEDIUM, "no_unit": MEDIUM, "merged_cells": MEDIUM, "tab_not_on_list": LOW,
    "no_cached_value": HIGH, "error_value": HIGH,
    "typed_constant": MEDIUM, "embedded_constant": MEDIUM, "pattern": LOW,
    "fy_total": HIGH, "subtotal": HIGH,
    "hidden_sheet": MEDIUM, "hidden_row": MEDIUM, "hidden_column": MEDIUM,
    "external_link": MEDIUM,
}
HIDDEN_IN_A_BLOCK = HIGH

RUN, NOT_RUN = "run", "not run"
NOT_RUN_NO_FORMULAS = "not run: the source has no formulas"
NOT_RUN_NO_TOTALS = "not run: the source has no total cells"
NOT_RUN_NO_SHEETS = "not run: the source has no sheets"
WORKBOOK = "workbook"

EXPECTED_TABS = ("Control", "Assumptions", "Lists")
EXPECTED_VERSIONS = ("Actual", "Budget", "Forecast")
PATTERN_VERSIONS = ("Budget", "Forecast")
SUBTOTAL, STOCK = "Subtotal", "Stock"
SIGNS = {"Credit": 1, "Debit": -1}
FORMULA_KINDS = ("formula", "array_formula")
# The fixed origin of the pattern check: the last column a worksheet can have.
FAR_RIGHT = "XFD"
NOT_TRANSLATED = "not translated: "
ARRAY_NOTE = "array formula: recorded, not read as text, not compared with the pattern of its row"
NAME_WITHHELD = "(a name that fails the allow-list)"
MEMBER_WORDS = {"account": "account name", "account code": "account code",
                "parent": "parent name", "entity": "entity name", "entity code": "entity code",
                "version": "version name"}

# The fixed templates. A name is filled in only as data (see _member and _quote).
MESSAGES = {
    "missing_tab": "The tab {tab} is missing: it is on the expected list.",
    "entity_not_on_lists": "The tab {tab} is a plan tab for the entity {entity}, which is not "
                           "on Lists.",
    "tab_not_on_list": "The visible tab {tab} is a tab not on the expected list.",
    "header_not_found": "A header was not found in block {block}: column {column} on {tab} "
                        "has no header, so its cells are not read.",
    "block_not_found": "The header of block {block} was not found on the tab {tab}, so the "
                       "block is not read.",
    "duplicate_period": "The period {period} heads {count} columns of block {block}: "
                        "{columns}.",
    "period_gap": "The periods of block {block} have a gap: no column for {missing}.",
    "account_not_on_lists": "The line item {line} in block {block} is not an account on Lists.",
    "rejected_name": "The {member} in {address} on {tab} fails the member-name allow-list "
                     "({reasons}): the member is excluded from the star schema and from every "
                     "prompt.",
    "no_unit": "The line item {line} in block {block} has no unit.",
    "merged_cells": "Cells {cells} on {tab} are merged inside block {block}.",
    "no_cached_value": "The formula in {address} on {tab} has no cached value.",
    "error_value": "The cell {address} on {tab} holds the error value {value}.",
    "typed_constant": "The cell {address} on {tab} holds a typed constant, {value}, in {where} "
                      "of block {block}.",
    "embedded_constant": "The formula in {address} on {tab} holds the numeric {constants}.",
    "pattern": "For review: the formula in {address} on {tab} differs from the pattern of its "
               "row.",
    "fy_total": "The {period} total of {line} in block {block} is {total} as the file holds "
                "it; its months add up to {computed}; the difference is {difference}.",
    "fy_total_stock": "The {period} figure of {line} in block {block} is {total} as the file "
                      "holds it; its last month with a figure, {month}, holds {computed}; the "
                      "difference is {difference}.",
    "subtotal": "The {period} figure of {line} in block {block} is {total} as the file holds "
                "it; recomputed from its accounts on Lists it is {computed}; the difference is "
                "{difference}.",
    "hidden_sheet": "The tab {tab} is hidden.",
    "hidden_row": "Row {index} on {tab} is hidden.",
    "hidden_column": "Column {index} on {tab} is hidden.",
    "external_link": "The workbook links to another workbook (link {number}).",
    "rejected_name_long": "The {member} on line {line} fails the member-name allow-list "
                          "({reasons}): the member is excluded from the star schema and from "
                          "every prompt.",
    "account_not_on_lists_long": "The account {account} is not on the accounts the source was "
                                 "checked against (lines {lines}).",
    "entity_not_on_lists_long": "The entity {entity} is not on the entities the source was "
                                "checked against (lines {lines}).",
}


# == The break and the results ====================================================

@dataclass(frozen=True)
class Break:
    """One finding of one control: typed, located, and worded by a fixed template."""
    break_id: str            # the control's prefix and 12 hex digits of SHA-256 of its anchors
    batch_id: str
    control: str             # one of CONTROLS
    kind: str                # the first of its reasons, in the control's fixed order
    reasons: tuple           # every rule of the control that caught it, in that order
    severity: str            # high, medium or low; the highest of its reasons'
    # The location; None where it does not apply.
    sheet: Optional[str]
    block: Optional[str]
    entity: Optional[str]
    account: Optional[str]   # the code on Lists
    period: Optional[str]
    address: Optional[str]   # a cell, a range, or a row or column such as "53:53"
    message: str             # one fixed template per reason, filled with values
    evidence: tuple          # (label, value) pairs of text: both patterns, both totals, ...


@dataclass(frozen=True)
class ControlResult:
    control: str
    status: str              # run, or not run
    reason: Optional[str]    # why it did not run, as fixed text; None when it ran
    breaks: tuple
    recorded: tuple          # (where, note) for what the control saw and set aside

    @property
    def passed(self) -> bool:
        """True only for a control that ran and found nothing: a control not run never passes."""
        return self.status == RUN and not self.breaks


@dataclass(frozen=True)
class ControlReport:
    batch_id: str
    results: tuple           # one ControlResult per control, in the order of CONTROLS

    def result(self, control: str) -> ControlResult:
        return next(r for r in self.results if r.control == control)

    def breaks(self) -> tuple:
        return tuple(b for r in self.results for b in r.breaks)


@dataclass(frozen=True)
class RowPattern:
    """The pattern check of one row: its pattern, how many cells hold it, and the rest."""
    pattern: Optional[str]   # the row's pattern; None when no formula is compared
    size: int                # the cells that hold it
    outside: tuple           # (cell, its own pattern) for each formula outside it, left to right
    arrays: tuple            # the array formulas: recorded, never compared


@dataclass
class _Finding:
    kind: str
    severity: str
    cell: Optional[tuple]    # (sheet, address) when the finding is on one cell
    anchors: tuple
    location: dict
    sentence: str
    evidence: tuple


# == Shared helpers ===================================================================

def _quote(text) -> str:
    """A name shown as data: JSON-encoded, so quotes and control characters are escaped."""
    return json.dumps(text, ensure_ascii=False)


def _member(name: Optional[str]) -> str:
    """A member name in a message: as data when it passes the allow-list, otherwise withheld."""
    if name is None:
        return "(no name)"
    return _quote(name) if allow_list.allowed(name) else NAME_WITHHELD


def _block_name(name: Optional[str], version: Optional[str], entity: Optional[str]) -> str:
    if name is None:
        return "(no block)"
    if all(allow_list.allowed(part) for part in (version, entity) if part):
        return _quote(name)
    return NAME_WITHHELD


def _figure(amount: Decimal) -> str:
    return f"{amount:,f}"


def _plain(amount: Decimal) -> str:
    return f"{amount:f}"


def _row_of(address: str) -> int:
    return coordinate_from_string(address)[1]


def _column_of(address: str) -> int:
    return column_index_from_string(coordinate_from_string(address)[0])


def _letter_of(address: str) -> str:
    return coordinate_from_string(address)[0]


def _at(sheet=None, block=None, entity=None, account=None, period=None, address=None) -> dict:
    return {"sheet": sheet, "block": block, "entity": entity, "account": account,
            "period": period, "address": address}


def _at_cell(c) -> dict:
    return _at(c.sheet, c.block, c.entity, c.account, c.period, c.address)


def _cell_anchors(c) -> tuple:
    """A block cell's anchors: its block, its line's label, and its column's header."""
    return ("cell", c.block, c.line, c.period)


def _finding(kind: str, cell, anchors: tuple, location: dict, template: str, values: dict,
             evidence=(), severity: Optional[str] = None) -> _Finding:
    return _Finding(kind, severity or SEVERITY[kind], cell, anchors, location,
                    MESSAGES[template].format(**values), tuple(evidence))


def _break_id(control: str, anchors: tuple) -> str:
    text = json.dumps([control, *anchors], ensure_ascii=False, separators=(",", ":"),
                      default=str)
    return f"{ID_PREFIX[control]}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


def _merge(control: str, batch_id: str, findings: list) -> tuple:
    """One break per cell (or per anchored thing) per control, its reasons in the fixed order."""
    groups: dict = {}
    for f in findings:
        key = ("cell", *f.cell) if f.cell else ("anchors", *f.anchors)
        groups.setdefault(key, []).append(f)
    order = KINDS[control]
    used: Counter = Counter()
    breaks = []
    for group in groups.values():
        group.sort(key=lambda f: order.index(f.kind))
        first = group[0]
        anchors = first.anchors
        used[anchors] += 1
        if used[anchors] > 1:                    # the same anchors twice: keep the ids unique
            anchors = (*anchors, used[anchors])
        breaks.append(Break(
            break_id=_break_id(control, anchors), batch_id=batch_id, control=control,
            kind=first.kind, reasons=tuple(dict.fromkeys(f.kind for f in group)),
            severity=min((f.severity for f in group), key=SEVERITIES.index),
            **first.location,
            message=" ".join(dict.fromkeys(f.sentence for f in group)),
            evidence=tuple(dict.fromkeys(pair for f in group for pair in f.evidence))))
    return tuple(breaks)


def _result(control: str, batch_id: str, findings: list, recorded=()) -> ControlResult:
    return ControlResult(control, RUN, None, _merge(control, batch_id, findings), tuple(recorded))


def _not_run(control: str, reason: str) -> ControlResult:
    return ControlResult(control, NOT_RUN, reason, (), ())


def _rows(cells) -> dict:
    """Block cells by (block, row number), each row in column order."""
    rows: dict = defaultdict(list)
    for c in cells:
        if c.block is not None and c.address is not None:
            rows[(c.block, _row_of(c.address))].append(c)
    for row in rows.values():
        row.sort(key=lambda c: _column_of(c.address))
    return rows


def _unreadable(c) -> bool:
    """A cell that holds something but no figure: no cached value, an error value, or text."""
    return c.kind != "empty" and c.amount is None


def _subtotal_codes(lists) -> set:
    return {a.code for a in lists.accounts if a.code and a.section == SUBTOTAL}


# == Formula integrity: the pattern check, the constants and the formula rows ===========

def pattern_of(formula: str, address: str) -> str:
    """The formula translated by openpyxl's translator from its cell to XFD on the same row."""
    row = _row_of(address)
    try:
        return Translator(formula, origin=address).translate_formula(f"{FAR_RIGHT}{row}")
    except (TranslatorError, TokenizerError, ValueError):
        return NOT_TRANSLATED + formula


def row_pattern(cells) -> RowPattern:
    """The month formulas of one row, grouped by their pattern; the largest group is the row's.

    When two groups are equally large, the one whose first cell is further left
    is the row's pattern. A typed constant and an empty cell hold no formula and
    are not compared; an array formula is recorded and never read as text.
    """
    months = sorted((c for c in cells if c.column_kind == "month"),
                    key=lambda c: _column_of(c.address))
    arrays = tuple(c for c in months if c.kind == "array_formula")
    translated = [(c, pattern_of(c.formula, c.address)) for c in months if c.kind == "formula"]
    if not translated:
        return RowPattern(None, 0, (), arrays)
    counts = Counter(p for _, p in translated)
    first: dict = {}
    for index, (_, p) in enumerate(translated):
        first.setdefault(p, index)
    pattern = max(counts, key=lambda p: (counts[p], -first[p]))
    return RowPattern(pattern, counts[pattern],
                      tuple((c, p) for c, p in translated if p != pattern), arrays)


def _neighbour(tokens, index: int, step: int) -> Optional[int]:
    """The index of the next token that is not white space, or None at either end."""
    index += step
    while 0 <= index < len(tokens) and tokens[index].type == Token.WSPACE:
        index += step
    return index if 0 <= index < len(tokens) else None


def _is_round_digits(tokens, index: int, frame) -> bool:
    """True for a number that is the whole digits argument of ROUND, a sign allowed."""
    if frame != ["ROUND", 1]:
        return False
    before = _neighbour(tokens, index, -1)
    if before is not None and tokens[before].type == Token.OP_PRE:
        before = _neighbour(tokens, before, -1)
    after = _neighbour(tokens, index, 1)
    return (before is not None and tokens[before].type == Token.SEP
            and tokens[before].subtype == Token.ARG
            and after is not None and tokens[after].type == Token.FUNC
            and tokens[after].subtype == Token.CLOSE)


def numeric_literals(formula: str) -> tuple:
    """Every number typed inside a formula, ROUND's digits argument apart, as it is written."""
    try:
        tokens = Tokenizer(formula).items
    except (TokenizerError, ValueError):
        return ()
    frames: list = []
    found = []
    for index, token in enumerate(tokens):
        if token.type == Token.FUNC and token.subtype == Token.OPEN:
            name = token.value[:-1].upper()
            frames.append([name.removeprefix("_XLFN."), 0])
        elif token.type == Token.PAREN and token.subtype == Token.OPEN:
            frames.append(["(", 0])
        elif token.type in (Token.FUNC, Token.PAREN) and token.subtype == Token.CLOSE:
            if frames:
                frames.pop()
        elif token.type == Token.SEP and token.subtype == Token.ARG:
            if frames:
                frames[-1][1] += 1
        elif token.type == Token.OPERAND and token.subtype == Token.NUMBER:
            if not _is_round_digits(tokens, index, frames[-1] if frames else None):
                found.append(token.value)
    return tuple(found)


def formula_row(cells, subtotal: bool = False) -> bool:
    """The reviewer's rule for a formula row (or, by column, a formula column).

    Among the cells that hold something (a formula or a typed value), at least
    half are formulas; empty cells are not counted, a tie counts, and an array
    formula counts as a formula. A row whose account is a Subtotal on Lists is
    a formula row whatever the counts.
    """
    holding = [c for c in cells if c.kind != "empty"]
    formulas = sum(1 for c in holding if c.kind in FORMULA_KINDS)
    return subtotal or (bool(holding) and 2 * formulas >= len(holding))


def _formula_count(cells, what: str) -> str:
    holding = [c for c in cells if c.kind != "empty"]
    formulas = sum(1 for c in holding if c.kind in FORMULA_KINDS)
    return f"{formulas} of the {len(holding)} {what} that hold something are formulas"


def _value_of(c) -> str:
    return _figure(c.amount) if c.amount is not None else _quote(c.value_text)


def _typed(c, axis: str, because: str) -> _Finding:
    """A typed constant in a formula row (axis "row") or in a formula column (axis "column")."""
    where = "a formula row" if axis == "row" else f"the formula column {c.period}"
    return _finding(
        "typed_constant", (c.sheet, c.address), _cell_anchors(c), _at_cell(c), "typed_constant",
        {"address": c.address, "tab": _quote(c.sheet), "value": _value_of(c), "where": where,
         "block": _block_name(c.block, c.version, c.entity)},
        (("typed value", _plain(c.amount) if c.amount is not None else c.value_text or ""),
         (f"why it is a formula {axis}", because)))


def formula_integrity(x) -> ControlResult:
    """Typed constants in formula rows, numeric constants inside formulas, and the pattern check."""
    if not x.batch.has_formulas:
        return _not_run(FORMULA_INTEGRITY, NOT_RUN_NO_FORMULAS)
    subtotals = _subtotal_codes(x.lists)
    findings, recorded = [], []
    totals_by_column: dict = defaultdict(list)
    for (block, _), cells in _rows(x.cells).items():
        months = [c for c in cells if c.column_kind == "month"]
        for c in cells:
            if c.column_kind == "total":
                totals_by_column[(block, c.period)].append(c)
        subtotal = cells[0].account in subtotals
        if formula_row(months, subtotal):
            because = (_formula_count(months, "month cells") if formula_row(months)
                       else "its account is a Subtotal on Lists")
            findings += [_typed(c, "row", because) for c in months if c.kind == "constant"]
        for c in cells:
            if c.kind == "array_formula":
                recorded.append((f"{c.sheet}!{c.address}", ARRAY_NOTE))
            elif c.kind == "formula":
                literals = numeric_literals(c.formula)
                if literals:
                    findings.append(_finding(
                        "embedded_constant", (c.sheet, c.address), _cell_anchors(c), _at_cell(c),
                        "embedded_constant",
                        {"address": c.address, "tab": _quote(c.sheet),
                         "constants": ("constants " if len(literals) > 1 else "constant ")
                         + ", ".join(literals)},
                        (("numeric constants", ", ".join(literals)), ("formula", c.formula))))
        if cells[0].version in PATTERN_VERSIONS:
            found = row_pattern(cells)
            for c, own in found.outside:
                findings.append(_finding(
                    "pattern", (c.sheet, c.address), _cell_anchors(c), _at_cell(c), "pattern",
                    {"address": c.address, "tab": _quote(c.sheet)},
                    (("formula", c.formula), ("pattern of its row", found.pattern),
                     ("pattern of this cell", own), ("cells in the row's pattern", str(found.size)),
                     ("origin", f"{FAR_RIGHT}{_row_of(c.address)}"))))
    for cells in totals_by_column.values():
        column = formula_row(cells)
        for c in cells:
            if c.kind != "constant":
                continue
            if column:
                findings.append(_typed(c, "column", _formula_count(cells, "cells of the column")))
            elif c.account in subtotals:
                findings.append(_typed(c, "row", "its account is a Subtotal on Lists"))
    return _result(FORMULA_INTEGRITY, x.batch.batch_id, findings, recorded)


# == Structure ===========================================================================

def _absent_months(block) -> tuple:
    months = [c.header for c in block.columns if c.role == "month"]
    if not months:
        return ()
    year = months[0][:4]
    present = set(months)
    return tuple(m for m in (f"{year}-{n:02d}" for n in range(1, 13)) if m not in present)


def structure(x) -> ControlResult:
    """The tabs, headers, periods, accounts, units, merged cells and member names."""
    if x.batch.source != WORKBOOK:
        return _long_format_structure(x)
    findings = []
    entities = x.lists.entity_codes()
    expected = EXPECTED_TABS + tuple(f"Plan_{e}" for e in entities)
    present = [s.name for s in x.batch.sheets]
    listed = ", ".join(expected)
    for tab in expected:
        if tab not in present:
            match = PLAN_SHEET.match(tab)
            findings.append(_finding(
                "missing_tab", None, ("tab", tab),
                _at(sheet=tab, entity=match["entity"] if match else None), "missing_tab",
                {"tab": _quote(tab)}, (("expected tabs", listed),)))
    for s in x.batch.sheets:
        if s.visibility != "visible" or s.name in expected:
            continue             # a hidden tab is the hidden-content control's, never this one's
        match = PLAN_SHEET.match(s.name)
        if match:
            findings.append(_finding(
                "entity_not_on_lists", None, ("tab", s.name),
                _at(sheet=s.name, entity=match["entity"]), "entity_not_on_lists",
                {"tab": _quote(s.name), "entity": _member(match["entity"])},
                (("entity", match["entity"]), ("entities on Lists", ", ".join(entities)))))
        else:
            findings.append(_finding(
                "tab_not_on_list", None, ("tab", s.name), _at(sheet=s.name), "tab_not_on_list",
                {"tab": _quote(s.name)}, (("expected tabs", listed),)))

    versions_on: dict = defaultdict(set)
    for b in x.blocks:
        versions_on[b.sheet].add(b.version)
    for sheet, entity in x.plan_sheets:
        if entity not in entities:
            continue             # the tab itself is the break
        for version in EXPECTED_VERSIONS:
            if version not in versions_on[sheet]:
                name = f"{version}_{entity}"
                findings.append(_finding(
                    "header_not_found", None, ("block", sheet, version),
                    _at(sheet=sheet, block=name, entity=entity), "block_not_found",
                    {"block": _block_name(name, version, entity), "tab": _quote(sheet)},
                    (("block", name), ("expected blocks", ", ".join(EXPECTED_VERSIONS)))))

    for b in x.blocks:
        first, top, last, _ = range_boundaries(b.ref)
        header_row = f"{get_column_letter(first)}{top}:{get_column_letter(last)}{top}"
        block = _block_name(b.name, b.version, b.entity)
        absent = _absent_months(b)
        problems = []
        for p in b.header_problems:
            if p.kind == "missing":
                letter = p.columns[0]
                problems.append(_finding(
                    "header_not_found", (b.sheet, f"{letter}{top}"),
                    ("header", b.name, column_index_from_string(letter) - first),
                    _at(b.sheet, b.name, b.entity, address=f"{letter}{top}"), "header_not_found",
                    {"block": block, "column": letter, "tab": _quote(b.sheet)},
                    (("header cell", f"{letter}{top}"),)))
            else:
                cells = [f"{letter}{top}" for letter in p.columns]
                problems.append(_finding(
                    "duplicate_period", None, ("periods", b.name, p.header),
                    _at(b.sheet, b.name, b.entity, period=p.header, address=",".join(cells)),
                    "duplicate_period",
                    {"period": p.header, "count": len(p.columns), "block": block,
                     "columns": ", ".join(p.columns)},
                    (("header cells", ", ".join(cells)),)))
        if absent and problems:
            problems[0].evidence += (("months with no column", ", ".join(absent)),)
        elif absent:
            problems.append(_finding(
                "period_gap", None, ("periods", b.name),
                _at(b.sheet, b.name, b.entity, address=header_row), "period_gap",
                {"block": block, "missing": ", ".join(absent)},
                (("months with no column", ", ".join(absent)),)))
        findings += problems
        unit_letter = next((c.letter for c in b.columns if c.role == "unit"), None)
        for line in b.lines:
            if line.unit is None:
                address = f"{unit_letter}{line.row}" if unit_letter else None
                findings.append(_finding(
                    "no_unit", (b.sheet, address) if address else None,
                    ("cell", b.name, line.label, UNIT_HEADER),
                    _at(b.sheet, b.name, b.entity, line.account, address=address), "no_unit",
                    {"line": _member(line.label), "block": block},
                    (("line item", line.label or ""),)))
        for merged in b.merged:
            findings.append(_finding(
                "merged_cells", None, ("merged", b.name, merged),
                _at(b.sheet, b.name, b.entity, address=merged), "merged_cells",
                {"cells": merged, "tab": _quote(b.sheet), "block": block},
                (("merged range", merged),)))

    blocks = {b.name: b for b in x.blocks}
    labels = {}
    for label in x.labels_not_on_lists:
        b = blocks.get(label.block)
        labels[(label.sheet, label.address)] = label
        findings.append(_finding(
            "account_not_on_lists", (label.sheet, label.address),
            ("cell", label.block, label.label, LABEL_HEADER),
            _at(label.sheet, label.block, b.entity if b else None, address=label.address),
            "account_not_on_lists",
            {"line": _member(label.label) if label.label is not None else "with no label",
             "block": _block_name(label.block, b.version if b else None,
                                  b.entity if b else None)},
            (("line item", label.label or ""),)))
    for n in x.rejected_names:
        label = labels.get((n.sheet, n.address))
        if label is not None:
            cell, anchors = (n.sheet, n.address), ("cell", label.block, label.label, LABEL_HEADER)
        else:
            cell, anchors = None, ("name", n.member, n.name)
        findings.append(_finding(
            "rejected_name", cell, anchors,
            _at(n.sheet, label.block if label else None, address=n.address), "rejected_name",
            {"member": MEMBER_WORDS.get(n.member, n.member), "address": n.address,
             "tab": _quote(n.sheet), "reasons": ", ".join(n.reasons)},
            (("member", n.member), ("name", n.name), ("reasons", ", ".join(n.reasons)),
             ("what the reasons mean", "; ".join(allow_list.REASONS[r] for r in n.reasons)))))
    return _result(STRUCTURE, x.batch.batch_id, findings)


def _long_format_structure(x) -> ControlResult:
    """A long-format source: rejected member names, and codes not on the lists passed in."""
    findings = []
    for n in x.rejected_names:
        findings.append(_finding(
            "rejected_name", None, ("name", n.member, n.name), _at(), "rejected_name_long",
            {"member": MEMBER_WORDS.get(n.member, n.member), "line": n.source_line,
             "reasons": ", ".join(n.reasons)},
            (("member", n.member), ("name", n.name), ("reasons", ", ".join(n.reasons)),
             ("line", str(n.source_line)))))
    unknown = {"entity": defaultdict(list), "account": defaultdict(list)}
    for c in x.cells:
        if c.unknown_entity:
            unknown["entity"][c.entity].append(c.source_line)
        if c.unknown_account:
            unknown["account"][c.line].append(c.source_line)
    for member, codes in unknown.items():
        for code, lines in codes.items():
            kind = f"{member}_not_on_lists"
            findings.append(_finding(
                kind, None, ("code", member, code),
                _at(entity=code if member == "entity" else None,
                    account=code if member == "account" else None),
                f"{kind}_long", {member: _member(code), "lines": ", ".join(map(str, lines))},
                ((member, code), ("lines", ", ".join(map(str, lines))))))
    return _result(STRUCTURE, x.batch.batch_id, findings)


# == Calculation state ======================================================================

def calculation_state(x) -> ControlResult:
    """Every formula in the workbook with no cached value, and every error value."""
    if not x.batch.has_formulas:
        return _not_run(CALCULATION_STATE, NOT_RUN_NO_FORMULAS)
    cells = {(c.sheet, c.address): c for c in x.cells}
    findings = []
    for f in x.formulas:
        c = cells.get((f.sheet, f.address))
        location = _at_cell(c) if c else _at(sheet=f.sheet, block=f.block, address=f.address)
        anchors = _cell_anchors(c) if c else ("cell", f.sheet, f.address)
        evidence = (("formula", f.formula),) if f.kind == "formula" else (("kind", ARRAY_NOTE),)
        if not f.cached:
            findings.append(_finding("no_cached_value", (f.sheet, f.address), anchors, location,
                                     "no_cached_value",
                                     {"address": f.address, "tab": _quote(f.sheet)}, evidence))
        elif f.value_type == "error":
            findings.append(_finding("error_value", (f.sheet, f.address), anchors, location,
                                     "error_value",
                                     {"address": f.address, "tab": _quote(f.sheet),
                                      "value": f.value_text},
                                     (("error value", f.value_text), *evidence)))
    for c in x.cells:
        if c.kind == "constant" and c.value_type == "error":
            findings.append(_finding("error_value", (c.sheet, c.address), _cell_anchors(c),
                                     _at_cell(c), "error_value",
                                     {"address": c.address, "tab": _quote(c.sheet),
                                      "value": c.value_text},
                                     (("error value", c.value_text), ("kind", "typed"))))
    return _result(CALCULATION_STATE, x.batch.batch_id, findings)


# == Totals =================================================================================

def _subtotal_parts(lists) -> dict:
    """For each Subtotal on Lists, the accounts above it that carry a ledger sign, signed.

    A Subtotal is the running total of the accounts above it on Lists: a Credit
    account adds and a Debit account subtracts, so Gross profit is revenue less
    cost of sales and EBIT is Gross profit less operating expenses.
    """
    parts, running = {}, []
    for a in lists.accounts:
        if a.code and a.section == SUBTOTAL:
            parts[a.code] = tuple(running)
        elif a.code and a.ledger_sign in SIGNS:
            running.append((a.code, SIGNS[a.ledger_sign]))
    return parts


def _tie(kind, template, c, computed: Decimal, label: str, extra=(), values=None) -> _Finding:
    difference = c.amount - computed
    return _finding(
        kind, (c.sheet, c.address), _cell_anchors(c), _at_cell(c), template,
        {"period": c.period, "line": _member(c.line),
         "block": _block_name(c.block, c.version, c.entity), "total": _figure(c.amount),
         "computed": _figure(computed), "difference": _figure(difference), **(values or {})},
        (("as the file holds it", _plain(c.amount)), *extra, (label, _plain(computed)),
         ("difference", _plain(difference))))


def totals(x) -> ControlResult:
    """Each FY cell against its own row's months; each subtotal, month by month, against Lists.

    Every figure is the one the file holds (the cached value). A comparison is
    made only when the total and every cell it is compared with hold a figure
    or are empty (an empty cell counts as zero): a formula with no cached
    value, an error value or text is the calculation-state control's.
    """
    if x.batch.source != WORKBOOK:
        return _not_run(TOTALS, NOT_RUN_NO_TOTALS)
    accounts = {a.code: a for a in x.lists.accounts if a.code}
    findings = []
    for cells in _rows(x.cells).values():
        months = [c for c in cells if c.column_kind == "month"]
        if any(_unreadable(c) for c in months):
            continue
        for t in (c for c in cells if c.column_kind == "total" and c.amount is not None):
            account = accounts.get(t.account)
            if account is not None and account.kind == STOCK:
                with_figure = [c for c in months if c.amount is not None]
                if not with_figure:
                    continue
                last = with_figure[-1]
                if t.amount != last.amount:
                    findings.append(_tie("fy_total", "fy_total_stock", t, last.amount,
                                         "last month with a figure holds",
                                         (("last month with a figure", last.period),),
                                         {"month": last.period}))
            else:
                computed = sum((c.amount for c in months if c.amount is not None), Decimal(0))
                if t.amount != computed:
                    findings.append(_tie("fy_total", "fy_total", t, computed, "months added up"))

    parts = _subtotal_parts(x.lists)
    columns: dict = defaultdict(dict)
    for c in x.cells:
        if c.block is not None and c.column_kind == "month" and c.account is not None:
            columns[(c.block, _letter_of(c.address))][c.account] = c
    for by_account in columns.values():
        for code, signed in parts.items():
            s = by_account.get(code)
            if s is None or s.amount is None:
                continue
            components = [(by_account.get(part), sign) for part, sign in signed]
            if any(c is not None and _unreadable(c) for c, _ in components):
                continue
            computed = sum((sign * c.amount for c, sign in components
                            if c is not None and c.amount is not None), Decimal(0))
            if s.amount != computed:
                formula = " ".join(f"{'+' if sign > 0 else '-'}{part}" for part, sign in signed)
                findings.append(_tie("subtotal", "subtotal", s, computed, "recomputed from Lists",
                                     (("accounts on Lists", formula),)))
    return _result(TOTALS, x.batch.batch_id, findings)


# == Hidden content and links ===============================================================

def hidden_content(x) -> ControlResult:
    """Hidden sheets, hidden rows and columns, and links to other workbooks."""
    if x.batch.source != WORKBOOK:
        return _not_run(HIDDEN_CONTENT, NOT_RUN_NO_SHEETS)
    findings = []
    for s in x.sheets:
        if s.visibility != "visible":
            match = PLAN_SHEET.match(s.name)
            findings.append(_finding(
                "hidden_sheet", None, ("tab", s.name),
                _at(sheet=s.name, entity=match["entity"] if match else None), "hidden_sheet",
                {"tab": _quote(s.name)},
                (("visibility", s.visibility), ("cells with content", str(s.cells)),
                 ("formulas", str(s.formulas)))))
    for s in x.sheets:
        for h in s.hidden_rows + s.hidden_columns:
            kind = f"hidden_{h.axis}"
            findings.append(_finding(
                kind, None, (h.axis, s.name, h.index),
                _at(sheet=s.name, block=h.blocks[0] if h.blocks else None,
                    address=f"{h.index}:{h.index}"),
                kind, {"index": h.index, "tab": _quote(s.name)},
                (("cells with content", ", ".join(h.cells) or "none"),
                 ("blocks it crosses", ", ".join(h.blocks) or "none")),
                severity=HIDDEN_IN_A_BLOCK if h.blocks else None))
    for number, target in enumerate(x.batch.external_links, start=1):
        users = [f"{f.sheet}!{f.address}" for f in x.formulas
                 if f.kind == "formula" and f"[{number}]" in f.formula]
        findings.append(_finding(
            "external_link", None, ("link", number), _at(), "external_link", {"number": number},
            (("target", target), ("cells that use it", ", ".join(users) or "none"))))
    return _result(HIDDEN_CONTENT, x.batch.batch_id, findings)


# == All five ===============================================================================

def run_controls(x) -> ControlReport:
    """The five controls over one extraction, in a fixed order."""
    return ControlReport(x.batch.batch_id, (structure(x), calculation_state(x),
                                             formula_integrity(x), totals(x),
                                             hidden_content(x)))
