# =============================================================================
# src/controls.py: the five spreadsheet controls
# =============================================================================
"""
The five controls of brief 6.1 over what the extractor read. No control opens
the file again, recalculates it or changes it: each reads the rows and facts
of an extraction (src/extract.py, src/long_format.py) and returns typed breaks.

- Structure: the tabs, the block headers, the periods, accounts not on Lists,
  merged cells inside a plan block, line items with no unit, and member names
  that fail the allow-list; and (part 1e) each block against its anchors (the
  defined name against the sheet's version label and Entity cell), a block
  that cannot be read, a block that lacks accounts on Lists, and each plan
  tab's Currency cell against the unit of the money accounts on Lists. Part
  2a adds: an account on two lines of one block, a block in which no line has
  a unit (one break, not one per line), and a block whose version is not one
  of Actual, Budget, Forecast (landed and not built). The Currency cell, the
  version label and the Entity cell are compared after trimming and ignoring
  case. Part 2a2 adds: blocks that share a cell (one break per group, none of
  them built, and nothing else in them examined by structure, formula
  integrity or totals); a blank figure in a Budget or Forecast block, by line
  and run of months, and a month blank on every line; an emptied Unit header
  as the block without units alone; a "Line item" cell that heads no block
  read; and, for an entity whose Actual block is not built, or an account a
  built Actual block lacks, the sentence that reconcile leaves it out of the
  ledger tie-out (actual_left_out, which reconcile reads).
- Calculation state: formulas with no cached value, and error values.
- Formula integrity: typed constants in formula rows, numeric constants inside
  formulas, and formulas that differ from the pattern of their row.
- Totals: each FY cell against its own row's months, and the subtotals month
  by month, recomputed from the extracted cells and Lists. A block with a
  line-level structure break (a line not on Lists, an account missing, an
  account twice) has some subtotals left unchecked, months and FY cell
  alike, and that break says which (part 2a): one fault, one break. Since
  part 2a2 they are only the subtotals the accounts concerned feed on Lists;
  a line not on Lists still leaves every subtotal of its block unchecked.
- Hidden content and links: hidden sheets, rows and columns, and links to
  other workbooks. A run of neighbouring hidden rows or columns is one break,
  and nothing hidden on a hidden tab is reported apart from the tab.

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
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from openpyxl.formula.tokenizer import Token, Tokenizer, TokenizerError
from openpyxl.formula.translate import Translator, TranslatorError
from openpyxl.utils import column_index_from_string, get_column_letter, range_boundaries
from openpyxl.utils.cell import coordinate_from_string

from src import names as allow_list
from src.extract import (BY_NAME, FIRST_ROW, LABEL_HEADER, NO_LABEL, PLAN_SHEET, UNIT_HEADER,
                         VERSION_READ, VERSIONS, reads_block)

__all__ = ["CONTROLS", "KINDS", "SEVERITY", "Break", "ControlReport", "ControlResult",
           "RowPattern", "actual_left_out", "calculation_state", "formula_integrity",
           "formula_row", "hidden_content", "numeric_literals", "pattern_of", "row_pattern",
           "run_controls", "structure", "totals"]

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
    # blocks_overlap comes before anchor_disagreement, so the overlap is the kind of the
    # break a name's disagreement is folded into (part 2a2).
    STRUCTURE: ("missing_tab", "entity_not_on_lists", "tab_not_on_list", "heading_not_found",
                "blocks_overlap", "anchor_disagreement", "block_unreadable",
                "version_not_admitted", "header_not_found", "header_not_read",
                "duplicate_period", "period_gap", "accounts_not_in_block",
                "account_not_on_lists", "account_twice_in_block", "rejected_name",
                "block_without_units", "no_unit", "blank_figure", "blank_month", "merged_cells",
                "currency_disagreement", "money_units_differ"),
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
    "heading_not_found": HIGH, "anchor_disagreement": HIGH, "block_unreadable": HIGH,
    "accounts_not_in_block": MEDIUM, "currency_disagreement": HIGH, "money_units_differ": HIGH,
    "account_twice_in_block": MEDIUM, "block_without_units": MEDIUM,
    "version_not_admitted": MEDIUM,
    "blocks_overlap": HIGH, "blank_figure": MEDIUM, "blank_month": MEDIUM,
    "header_not_read": LOW,
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
EXPECTED_VERSIONS = VERSIONS          # the versions the star admits (part 2a)
PATTERN_VERSIONS = ("Budget", "Forecast")
ACTUAL = "Actual"
BLANK_VERSIONS = PATTERN_VERSIONS     # the blank checks of part 2a2: Budget and Forecast
SUBTOTAL, STOCK = "Subtotal", "Stock"
# The money accounts are every account on Lists outside this section (part 1e).
STATISTICS = "Statistics"
SIGNS = {"Credit": 1, "Debit": -1}
FORMULA_KINDS = ("formula", "array_formula")
# The fixed origin of the pattern check: the last column a worksheet can have.
FAR_RIGHT = "XFD"
NOT_TRANSLATED = "not translated: "
ARRAY_NOTE = "array formula: recorded, not read as text, not compared with the pattern of its row"
NAME_WITHHELD = "(a name that fails the allow-list)"
# Part 2a: a value read from the Currency cell, the version label or the Entity
# cell that fails the allow-list is described by its reasons and never printed;
# the evidence shows it JSON-encoded with every character outside ASCII escaped,
# bounded at EVIDENCE_LIMIT encoded characters.
WITHHELD_REASONS = {
    "empty": "is empty",
    "characters": "has characters outside the allowed set",
    "length": f"is longer than {allow_list.MAX_CHARACTERS} characters",
    "words": "has more than six words",          # allow_list.MAX_WORDS is 6
    "space": "starts or ends with a space",
}
EVIDENCE_LIMIT = 60
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
    "account_not_on_lists_displaced": "The line item {line} in block {block} is not an account "
                                      "on Lists; it takes the place of {account}, an account the "
                                      "block lacks.",
    "account_twice_in_block_displaced": "The line item {line} appears {count} in block {block}, "
                                        "on rows {rows}; the one on row {row} takes the place of "
                                        "{account}, an account the block lacks.",
    "account_twice_in_block": "The line item {line} appears {count} in block {block}, on rows "
                              "{rows}; the one on row {row} repeats the line on row {first}.",
    "subtotals_not_checked": "The {what} of block {block} {verb} not checked: {subtotals}.",
    "block_without_units_empty": "No line item in block {block} has a unit: the {unit} column, "
                                 "{column}, is empty on all {count} lines.",
    "block_without_units_header": "No line item in block {block} has a unit: the block has no "
                                  "{unit} header.",
    "version_not_admitted": "The version of block {block} on {tab}, {version}, is not one of "
                            "Actual, Budget, Forecast; the block is landed and not built.",
    "rejected_name": "The {member} in {address} on {tab} fails the member-name allow-list "
                     "({reasons}): the member is excluded from the star schema and from every "
                     "prompt.",
    "no_unit": "The line item {line} in block {block} has no unit.",
    "merged_cells": "Cells {cells} on {tab} are merged inside block {block}.",
    "heading_not_found": "The tab {tab} has no cell labelled {labels}, so the tab's {what} "
                         "{verb} not checked.",
    "anchor_version": "The defined name {name} names version {version}, but the label above "
                      "its header row, in {cell} on {tab}, reads {label}.",
    "anchor_entity": "The Entity cell of {tab}, {cell}, reads {value}, but the defined names "
                     "of its blocks, {names}, say {entity}.",
    "anchor_entity_tab": "The Entity cell of {tab}, {cell}, reads {value}, but the tab is the "
                         "plan tab of {entity}.",
    "anchor_name_unused": "The defined name {name} has the form of a block on {tab} but is not "
                          "used, because it refers to {where}{scope}.",
    "block_unreadable": "The block {block} on {tab} cannot be read: the first row of its range, "
                        "{row}, has {reasons}.",
    "accounts_not_in_block": "The block {block} does not hold {count} of the accounts on Lists: "
                             "{accounts}.",
    "currency_disagreement": "The Currency cell of {tab}, {cell}, reads {value}, but the "
                             "money accounts on Lists are in {unit}.",
    "money_units_differ": "The money accounts on Lists do not share one unit ({units}), so no "
                          "plan tab's currency is checked.",
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
    "hidden_rows": "Rows {first} to {last} on {tab} are hidden.",
    "hidden_columns": "Columns {first} to {last} on {tab} are hidden.",
    "external_link": "The workbook links to another workbook (link {number}).",
    "rejected_name_long": "The {member} on line {line} fails the member-name allow-list "
                          "({reasons}): the member is excluded from the star schema and from "
                          "every prompt.",
    "account_not_on_lists_long": "The account {account} is not on the accounts the source was "
                                 "checked against (lines {lines}).",
    "entity_not_on_lists_long": "The entity {entity} is not on the entities the source was "
                                "checked against (lines {lines}).",
    # Part 2a2.
    "blocks_overlap": "The blocks {blocks} on {tab} share {shared}: no cell may belong to two "
                      "blocks, so none of them is built.",
    "overlap_header": "The header in {cell}, under the label {label}, is not read, because the "
                      "defined name {name} reads that version elsewhere.",
    "unreadable_overlap": "Its range overlaps {blocks}, whose cells are read as {owner} alone; "
                          "nothing is built for {block}.",
    "tie_out_entity": "Reconcile leaves the Actual figures of {entity} out of the ledger "
                      "tie-out.",
    "tie_out_accounts": "Reconcile leaves {accounts} of {entity} out of the ledger tie-out.",
    "blank_figure": "The line item {line} in block {block} is blank {months}, {cell}, while "
                    "other lines of the block hold figures for {those}; nothing is built for "
                    "{them}.",
    "blank_month": "Every account and statistic line of block {block} is blank {months}, from "
                   "{cell}; nothing is built for the block in {those}.",
    "block_without_units_header_empty": "No line item in block {block} has a unit: the {unit} "
                                        "header, {cell}, is empty, so column {column} is not "
                                        "read as the units.",
    "header_not_read": "The cell {cell} on {tab} reads {header}, but no block is read from "
                       "it: {why}.",
}
# The reason a header_not_read break gives, by the extractor's reason (part 2a2).
HEADER_WHY = {
    FIRST_ROW: "it is in the first row, so no cell above it gives the block's version",
    NO_LABEL: "the cell above it, {above}, is empty, so nothing gives the block's version",
    VERSION_READ: "the label above it, {label}, names version {version}, which the tab already "
                  "reads from the block {block}",
    "overlaps a named block": "the block it heads, {ref}, under the label {label}, overlaps "
                              "{blocks}, {which} read through {its} defined {names}",
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


def _same(a: Optional[str], b: Optional[str]) -> bool:
    """Two values compared as part 2a compares them: str.strip() and casefold() on both sides.

    Python's strip also removes tabs, line breaks and the no-break space; it keeps a
    zero-width character, so a value holding one still differs.
    """
    if a is None or b is None:
        return a is b
    return a.strip().casefold() == b.strip().casefold()


def _shown(value: Optional[str]) -> str:
    """A value read from a cell, in a message: as data when it passes the allow-list; otherwise
    described by the allow-list's reasons and never printed (part 2a)."""
    if value is None:
        return "nothing"
    reasons = allow_list.check(value)
    if not reasons:
        return _quote(value)
    return ("a value that " + " and ".join(WITHHELD_REASONS[r] for r in reasons)
            + " (shown encoded in the evidence)")


def _encoded(value: Optional[str]) -> str:
    """The same value in the evidence: as it is when it passes the allow-list; otherwise
    json.dumps with ensure_ascii, so an invisible character shows as an escape, bounded at
    EVIDENCE_LIMIT encoded characters (an escape is never cut) and then its length."""
    if value is None:
        return ""
    if allow_list.allowed(value):
        return value
    whole = json.dumps(value, ensure_ascii=True)
    if len(whole) <= EVIDENCE_LIMIT:
        return whole
    pieces, used = ['"'], 2
    for character in value:
        piece = json.dumps(character, ensure_ascii=True)[1:-1]
        if used + len(piece) > EVIDENCE_LIMIT:
            break
        pieces.append(piece)
        used += len(piece)
    return "".join(pieces) + f'" ({len(value)} characters in all)'


def _times(count: int) -> str:
    return "twice" if count == 2 else f"{count} times"


def _and(items: list) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


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
        if cells[0].overlap:
            continue             # a block that shares a cell is not examined (part 2a2)
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
    left_out, accounts_left_out = actual_left_out(x)
    for tab in expected:
        if tab not in present:
            match = PLAN_SHEET.match(tab)
            entity = match["entity"] if match else None
            findings.append(_with_clause(_finding(
                "missing_tab", None, ("tab", tab), _at(sheet=tab, entity=entity), "missing_tab",
                {"tab": _quote(tab)}, (("expected tabs", listed),)),
                _tie_out(entity) if entity in left_out else ""))
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

    findings += _tab_anchors(x, entities)
    findings += _names_not_used(x, entities)

    versions_on: dict = defaultdict(set)
    for b in x.blocks:
        versions_on[b.sheet].add(b.version)
    for sheet, entity in x.plan_sheets:
        if entity not in entities:
            continue             # the tab itself is the break
        for version in EXPECTED_VERSIONS:
            if version not in versions_on[sheet]:
                name = f"{version}_{entity}"
                findings.append(_with_clause(_finding(
                    "header_not_found", None, ("block", sheet, version),
                    _at(sheet=sheet, block=name, entity=entity), "block_not_found",
                    {"block": _block_name(name, version, entity), "tab": _quote(sheet)},
                    (("block", name), ("expected blocks", ", ".join(EXPECTED_VERSIONS)))),
                    _tie_out(entity) if version == ACTUAL and entity in left_out else ""))

    groups = {(o.sheet, name): o for o in x.overlaps for name in o.blocks}
    explained, unexplained = _headers_explained(x, groups)
    for o in x.overlaps:
        findings.append(_overlap_finding(x, o, explained, left_out))
    rows = _rows(x.cells)

    quiet, displaced = {}, {}
    for b in x.blocks:
        first, top, last, bottom = range_boundaries(b.ref)
        header_row = f"{get_column_letter(first)}{top}:{get_column_letter(last)}{top}"
        block = _block_name(b.name, b.version, b.entity)
        group = groups.get((b.sheet, b.name))
        anchors = _overlap_anchors(group) if group else ("anchor", b.name)
        unread = () if group else explained.get((b.sheet, b.name), ())
        if (b.found_by == BY_NAME and b.header_row is not None
                and not _same(b.version_label, b.version)):
            findings.append(_version_disagrees(x, b, block, anchors, unread))
        if b.version not in EXPECTED_VERSIONS:
            findings.append(_version_not_admitted(b, block, anchors))
        why = _why_unreadable(b)
        if why or group or b.version not in EXPECTED_VERSIONS:
            # Nothing else in the block is examined: it cannot be read, or it shares a
            # cell with another block (one break for the group), or it is landed and not
            # built (part 2a2), so no other break stands in for that one.
            quiet[b.name] = (b.sheet, (first, top, last, bottom))
        if why and not group:
            findings.append(_block_unreadable(x, b, block, anchors, header_row, why, unread,
                                              b.entity in left_out))
        if b.name in quiet:
            continue
        absent = _absent_months(b)
        unit_header = _empty_unit_header(b)
        problems = []
        for p in b.header_problems:
            if p.kind == "missing":
                letter = p.columns[0]
                if unit_header is not None and letter == unit_header.letter:
                    continue     # the Unit column's header: block_without_units says it
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
        findings += _units(b, block, unit_header)
        for merged in b.merged:
            findings.append(_finding(
                "merged_cells", None, ("merged", b.name, merged),
                _at(b.sheet, b.name, b.entity, address=merged), "merged_cells",
                {"cells": merged, "tab": _quote(b.sheet), "block": block},
                (("merged range", merged),)))
        faults = _line_faults(x, b)
        displaced.update(_displaced(b, faults))
        findings += _accounts_twice(x, b, block, faults, displaced)
        findings += _accounts_not_in_block(x, b, block, faults)
        findings += _blanks(x, b, block, rows)

    def in_quiet(sheet, address) -> bool:
        if sheet is None or address is None:
            return False
        row, column = _row_of(address), _column_of(address)
        return any(s == sheet and first <= column <= last and top <= row <= bottom
                   for s, (first, top, last, bottom) in quiet.values())

    blocks = {b.name: b for b in x.blocks}
    labels = {}
    for label in x.labels_not_on_lists:
        if label.block in quiet:
            continue
        b = blocks.get(label.block)
        labels[(label.sheet, label.address)] = label
        account = displaced.get((label.block, _row_of(label.address)))
        values = {"line": _member(label.label) if label.label is not None else "with no label",
                  "block": _block_name(label.block, b.version if b else None,
                                       b.entity if b else None)}
        evidence = (("line item", label.label or ""),)
        if account is not None:
            values["account"] = _member(account.name) if account.name else _member(account.code)
            evidence += (("account it displaced", _account_text(account)),)
        finding = _with_clause(_finding(
            "account_not_on_lists", (label.sheet, label.address),
            ("cell", label.block, label.label, LABEL_HEADER),
            _at(label.sheet, label.block, b.entity if b else None, address=label.address),
            "account_not_on_lists_displaced" if account is not None else "account_not_on_lists",
            values, evidence), _not_checked(x, values["block"], _subtotal_codes(x.lists)))
        if b is not None and b.version == ACTUAL and account is not None:
            finding = _tie_out_accounts(x, finding, b, [account.code])
        findings.append(finding)
    for n in x.rejected_names:
        if in_quiet(n.sheet, n.address) or n.member == "version":
            # A version that fails the allow-list is never admitted, so its block's
            # version_not_admitted break describes it (part 2a2): one break, not two.
            continue
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
    entity_of = dict(x.plan_sheets)
    findings += [_header_not_read(h, entity_of.get(h.sheet)) for h in unexplained]
    return _result(STRUCTURE, x.batch.batch_id, findings)


# == Part 2a2: what a block shares, what it leaves blank, and what is left unread ===========

def _overlap_anchors(o) -> tuple:
    """A group of blocks that share a cell: its tab and the blocks' names, sorted."""
    return ("overlap", o.sheet, o.blocks)


def _has_high_break(x, b, groups) -> bool:
    """The block read through a name has a high break of its own: an overlap, a block that
    cannot be read, or a version label that disagrees with the name."""
    return bool((b.sheet, b.name) in groups or _why_unreadable(b) or (
        b.header_row is not None and not _same(b.version_label, b.version)))


def _headers_explained(x, groups) -> tuple:
    """({(tab, block): headers}, the rest): the "Line item" cells left unread that another
    break explains, by the block read through a defined name whose own high break it is.

    The coordinator's rule for item 4 (lean b): a header skipped because the label above it
    names a version the tab reads through a defined name, when that name's block has a
    high break of its own (an overlap, a block that cannot be read, a name that disagrees
    with the label), is named by that break, in its message or its evidence, and is silent
    here. Every other header left unread is one low header_not_read break.
    """
    blocks = {(b.sheet, b.name): b for b in x.blocks}
    explained, rest = defaultdict(list), []
    for h in x.headers_not_read:
        b = blocks.get((h.sheet, h.block)) if (h.reason == VERSION_READ
                                               and h.read_by == BY_NAME) else None
        if b is not None and b.found_by == BY_NAME and _has_high_break(x, b, groups):
            explained[(h.sheet, b.name)].append(h)
        else:
            rest.append(h)
    return explained, rest


def _named_ranges(blocks, names, refs) -> str:
    return _and([f"{_block_name(n, blocks[n].version, blocks[n].entity)} ({ref})"
                 for n, ref in zip(names, refs)])


def _overlap_finding(x, o, explained: dict, left_out) -> _Finding:
    """One high break for a group of blocks that share a cell: none of them is built."""
    blocks = {b.name: b for b in x.blocks if b.sheet == o.sheet}
    shared = (f"{o.cells} cells, from {o.first} to {o.last}" if o.cells > 1
              else f"one cell, {o.first}")
    heads = [h for name in o.blocks for h in explained.get((o.sheet, name), ())]
    evidence = (("blocks", "; ".join(f"{n} {ref}" for n, ref in zip(o.blocks, o.refs))),
                ("cells in more than one block", str(o.cells)),
                ("first and last shared cell", f"{o.first}, {o.last}"))
    if heads:
        evidence += (("headers not read", ", ".join(h.address for h in heads)),)
    entity = blocks[o.blocks[0]].entity
    finding = _finding(
        "blocks_overlap", None, _overlap_anchors(o),
        _at(o.sheet, o.blocks[0], entity, address=o.box), "blocks_overlap",
        {"blocks": _named_ranges(blocks, o.blocks, o.refs), "tab": _quote(o.sheet),
         "shared": shared}, evidence)
    for h in heads:
        b = blocks[h.block]
        finding = _with_clause(finding, " " + MESSAGES["overlap_header"].format(
            cell=h.address, label=_shown(h.label), name=_block_name(b.name, b.version, b.entity)))
    if any(blocks[n].version == ACTUAL for n in o.blocks) and entity in left_out:
        finding = _with_clause(finding, _tie_out(entity))
    return finding


def _block_unreadable(x, b, block: str, anchors: tuple, header_row: str, why: tuple, unread,
                      left_out: bool) -> _Finding:
    """A block that cannot be read: one high break with its reasons (part 1e). Since 2a2 it
    also says when its range overlaps blocks that are read, and that nothing is built for it,
    names in its evidence a header its name leaves unread, and, for an Actual block, says
    that reconcile leaves the entity out of the ledger tie-out."""
    evidence = (("why it cannot be read", ", ".join(why)),)
    bounds = range_boundaries(b.ref)
    over = [o for o in x.blocks if o.sheet == b.sheet and o.name != b.name
            and not _why_unreadable(o) and _ranges_meet(bounds, range_boundaries(o.ref))]
    sentences = []
    if over:
        blocks = {o.name: o for o in over}
        names = [o.name for o in over]
        sentences.append(MESSAGES["unreadable_overlap"].format(
            blocks=("the block " if len(over) == 1 else "the blocks ")
            + _named_ranges(blocks, names, [o.ref for o in over]),
            owner="that block's" if len(over) == 1 else "those blocks'", block=block))
        evidence += (("blocks its range overlaps", "; ".join(f"{o.name} {o.ref}" for o in over)),)
    if unread:
        evidence += (("headers not read", ", ".join(h.address for h in unread)),)
    if b.version == ACTUAL and left_out:
        sentences.append(_tie_out(b.entity).strip())
    finding = _finding(
        "block_unreadable", None, anchors, _at(b.sheet, b.name, b.entity, address=b.ref),
        "block_unreadable",
        {"block": block, "tab": _quote(b.sheet), "row": header_row, "reasons": " and ".join(why)},
        evidence)
    return _with_clause(finding, "".join(" " + s for s in sentences))


def _ranges_meet(a: tuple, b: tuple) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _tie_out(entity: Optional[str]) -> str:
    """The sentence of the break that explains an entity's Actual block not built (2a2)."""
    return " " + MESSAGES["tie_out_entity"].format(entity=_member(entity))


def _tie_out_accounts(x, finding: _Finding, b, codes) -> _Finding:
    """A line-level break of a built Actual block: the accounts reconcile leaves out (2a2).

    They are the ledger accounts among codes: an account the block lacks has no figure to
    compare, and an account on two lines holds its key twice, which reconcile never
    compares; both would otherwise be a flood of differences (the reviewer's F19).
    """
    ledger = [a for a in x.lists.accounts if a.code in set(codes) and a.ledger_sign in SIGNS]
    if not ledger:
        return finding
    finding.evidence += (("left out of the ledger tie-out", ", ".join(map(_account_text,
                                                                          ledger))),)
    return _with_clause(finding, " " + MESSAGES["tie_out_accounts"].format(
        accounts=_and([_member(a.name or a.code) for a in ledger]), entity=_member(b.entity)))


def actual_left_out(x) -> tuple:
    """(entities, (entity, account) pairs) that reconcile leaves out of the ledger tie-out.

    The reviewer's ruling of 8 November (item 3): an entity on Lists whose Actual block is
    not built or not read (its plan tab or its Actual block is not found, the block cannot
    be read, or it shares a cell with another block) is left out whole, and the structure
    break that explains the block says so. The optional account-level case (F19): in a
    built Actual block, an account the block lacks, or holds on two lines, is left out for
    that entity. Both in Lists order. A built block with an empty month is untouched.
    """
    if x.batch.source != WORKBOOK:
        return (), ()
    overlapping = {(o.sheet, name) for o in getattr(x, "overlaps", ()) for name in o.blocks}
    built = {}
    for b in x.blocks:
        if (b.version == ACTUAL and not _why_unreadable(b)
                and (b.sheet, b.name) not in overlapping):
            built.setdefault(b.entity, b)
    entities = tuple(e for e in x.lists.entity_codes() if e not in built)
    ledger = [a.code for a in x.lists.accounts if a.code and a.ledger_sign in SIGNS]
    pairs = []
    for entity in x.lists.entity_codes():
        if entity in built:
            _, extras, missing = _line_faults(x, built[entity])
            codes = {a.code for a in missing} | {line.account for line in extras}
            pairs += [(entity, code) for code in ledger if code in codes]
    return entities, tuple(dict.fromkeys(pairs))


def _empty_unit_header(b):
    """The column read as the Unit column whose header is empty (part 2a2, F16), or None.

    The coordinator's lean: with no "Unit" header in the block, the empty header cell
    immediately right of the label column, every cell below which, in the block, holds
    text and no figure or formula. Any other empty header cell keeps its own break.
    """
    if any(c.role == "unit" for c in b.columns):
        return None
    label = next((i for i, c in enumerate(b.columns) if c.role == "label"), None)
    if label is None or label + 1 >= len(b.columns):
        return None
    column = b.columns[label + 1]
    return column if column.role == "missing" and column.text_only else None


def _is_blank(c) -> bool:
    """A cell with no value and no formula: absent, or empty text (part 2a2). A formula with
    no cached value, an error value, text and an explicit 0 are not blank."""
    if c is None:
        return True
    return c.kind == "empty" or (c.kind == "constant" and c.value_type == "text"
                                 and not c.value_text)


def _index_runs(indexes: list) -> list:
    """Neighbouring indexes in runs, each in order."""
    found: list = []
    for i in indexes:
        if found and i == found[-1][-1] + 1:
            found[-1].append(i)
        else:
            found.append([i])
    return found


def _blanks(x, b, block: str, rows: dict) -> list:
    """Blank figures in a Budget or Forecast block that is built (part 2a2, F15).

    Only the lines whose label is an account on Lists that is not a Subtotal (the leaf
    accounts and the statistics) and only the month columns, in order. A month in which
    every such line is blank is one blank_month break per run of such months; any other
    blank cell is one blank_figure break per line and run of months, a run ending at a
    month that holds something or at a blank month. Actual is left to the tie-out, and a
    line of an account the block holds twice to that break.
    """
    if b.version not in BLANK_VERSIONS:
        return []
    leaf = {a.code for a in x.lists.accounts if a.code and a.section != SUBTOTAL}
    # An account on two lines is the account_twice_in_block break's, and its key is never
    # built either way, so neither line is looked at here.
    once = Counter(line.account for line in b.lines)
    lines = sorted((line for line in b.lines if line.account in leaf and once[line.account] == 1),
                   key=lambda l: l.row)
    months = [c for c in b.columns if c.role == "month"]
    if not lines or not months:
        return []
    held = {}
    for line in lines:
        cells = {_letter_of(c.address): c for c in rows.get((b.name, line.row), ())}
        for i, month in enumerate(months):
            held[(line.row, i)] = not _is_blank(cells.get(month.letter))
    empty = [i for i in range(len(months)) if not any(held[(l.row, i)] for l in lines)]

    def spoken(run: list) -> tuple:
        one = len(run) == 1
        first, last = months[run[0]].header, months[run[-1]].header
        return (f"in {first}" if one else f"from {first} to {last}",
                "that month" if one else "those months", one)

    findings = []
    for line in lines:
        gaps = [i for i in range(len(months)) if not held[(line.row, i)] and i not in empty]
        for run in _index_runs(gaps):
            when, those, one = spoken(run)
            cells = [f"{months[i].letter}{line.row}" for i in run]
            address = cells[0] if one else f"{cells[0]}:{cells[-1]}"
            findings.append(_finding(
                "blank_figure", None, ("blank", b.name, line.account, months[run[0]].header),
                _at(b.sheet, b.name, b.entity, line.account, months[run[0]].header, address),
                "blank_figure",
                {"line": _member(line.label), "block": block, "months": when,
                 "cell": f"in {cells[0]}" if one else f"from {cells[0]}", "those": those,
                 "them": "it" if one else "them"},
                (("line item", line.label or ""),
                 ("months", ", ".join(months[i].header for i in run)),
                 ("cells", ", ".join(cells)))))
    top, bottom = lines[0].row, lines[-1].row
    for run in _index_runs(empty):
        when, those, _ = spoken(run)
        first, last = months[run[0]].letter, months[run[-1]].letter
        address = f"{first}{top}:{last}{bottom}"
        findings.append(_finding(
            "blank_month", None, ("blank_month", b.name, months[run[0]].header),
            _at(b.sheet, b.name, b.entity, period=months[run[0]].header, address=address),
            "blank_month",
            {"block": block, "months": when, "cell": f"{first}{top}", "those": those},
            (("months", ", ".join(months[i].header for i in run)),
             ("lines that are blank", str(len(lines))), ("cells", address))))
    return findings


def _header_not_read(h, entity: Optional[str]) -> _Finding:
    """A "Line item" cell that heads no block read, and that no other break explains: one
    low break (part 2a2, F17), naming the cell and why."""
    above = (f"{_letter_of(h.address)}{_row_of(h.address) - 1}"
             if _row_of(h.address) > 1 else None)
    values = {"above": above, "label": _shown(h.label), "version": _shown(h.version),
              "ref": h.ref, "block": _quote(h.block) if h.block else None}
    evidence = (("Line item cell", h.address),)
    if above is not None:
        evidence += (("cell above it", f"{above}, empty" if h.label is None
                      else f"{above}: {_encoded(h.label)}"),)
    if h.overlaps:
        many = len(h.overlaps) > 1
        values.update(blocks=("the blocks " if many else "the block ") + _and(
            [_quote(n) for n in h.overlaps]), which="which are" if many else "which is",
            its="their" if many else "its", names="names" if many else "name")
        evidence += (("range it would head", h.ref), ("blocks it overlaps", ", ".join(h.overlaps)))
    elif h.block:
        evidence += (("block that reads the version", h.block), ("read by", h.read_by or ""))
    return _finding(
        "header_not_read", None, ("header", h.sheet, h.address),
        _at(h.sheet, entity=entity, address=h.address), "header_not_read",
        {"cell": h.address, "tab": _quote(h.sheet), "header": _quote(LABEL_HEADER),
         "why": HEADER_WHY[h.reason].format(**values)}, evidence)


def _why_unreadable(b) -> tuple:
    """Why a block cannot be read: its first row has no label header, or no month header."""
    roles = {c.role for c in b.columns}
    why = []
    if "label" not in roles:
        why.append(f"no {_quote(LABEL_HEADER)} header")
    if "month" not in roles:
        why.append("no month header")
    return tuple(why)


def _name_fact(x, b):
    """The defined name a block was read through."""
    return next((n for n in x.defined_names if n.name == b.name and len(n.destinations) == 1
                 and n.scope in (None, b.sheet)), None)


def _version_disagrees(x, b, block: str, anchors: tuple = (), unread=()) -> _Finding:
    """A block read through a defined name whose version is not the label above its header row.

    The header row is found by its "Line item" label in the name's range, and
    the version label is the cell above that label (src/extract.py): never the
    cell above wherever the name happens to start. Part 2a2: a block that
    shares a cell with another takes the group's anchors, so the disagreement
    is folded into the group's one break; and a header the name leaves unread
    is named in the evidence.
    """
    cell = b.version_label_address or "(no row above it)"
    fact = _name_fact(x, b)
    title = _encoded(b.version_label) if b.version_label is not None else "nothing"
    evidence = (("defined name", b.name), ("refers to", fact.destinations[0] if fact else b.ref),
                ("version the name gives", b.version),
                ("label above the header row", f"{cell}: {title}"))
    if unread:
        evidence += (("headers not read", ", ".join(h.address for h in unread)),)
    return _finding(
        "anchor_disagreement", None, anchors or ("anchor", b.name),
        _at(b.sheet, b.name, b.entity, address=b.ref), "anchor_version",
        {"name": block, "version": _member(b.version), "cell": cell, "tab": _quote(b.sheet),
         "label": _shown(b.version_label)}, evidence)


def _money_unit(lists) -> tuple:
    """(the one unit of the money accounts on Lists, or None; the units if they are not one)."""
    money = [a for a in lists.accounts if a.code and a.section != STATISTICS]
    if not money:
        return None, ()
    units = sorted({a.unit for a in money}, key=lambda u: (u is None, u or ""))
    if len(units) == 1 and units[0] is not None:
        return units[0], ()
    by_unit = [(u, [a.code for a in money if a.unit == u]) for u in units]
    return None, by_unit


def _tab_anchors(x, entities) -> list:
    """Each plan tab's Entity and Currency cells, found by their labels, against its anchors."""
    findings = []
    unit, mixed = _money_unit(x.lists)
    if mixed:
        findings.append(_finding(
            "money_units_differ", None, ("units", "Lists"), _at(sheet="Lists"),
            "money_units_differ",
            {"units": ", ".join(_member(u) if u is not None else "none" for u, _ in mixed)},
            (("units of the money accounts", "; ".join(
                f"{u if u is not None else 'none'}: {', '.join(codes)}" for u, codes in mixed)),
             ("money accounts", "every account on Lists outside the Statistics section"))))
    headings: dict = defaultdict(dict)
    for h in x.headings:
        headings[h.sheet].setdefault(h.label, h)
    for sheet, entity in x.plan_sheets:
        if entity not in entities:
            continue             # the tab itself is the break
        entity_cell, currency_cell = (headings[sheet].get(k) for k in ("Entity", "Currency"))
        missing = [k for k, h in (("Entity", entity_cell), ("Currency", currency_cell))
                   if h is None or h.label_address is None]
        if missing:
            findings.append(_finding(
                "heading_not_found", None, ("heading", sheet), _at(sheet=sheet, entity=entity),
                "heading_not_found",
                {"tab": _quote(sheet), "labels": " or ".join(_quote(k) for k in missing),
                 "what": " and ".join(k.lower() for k in missing),
                 "verb": "are" if len(missing) > 1 else "is"},
                (("labels not found", ", ".join(missing)),)))
        if "Entity" not in missing and not _same(entity_cell.value, entity):
            names = [b for b in x.blocks if b.sheet == sheet and b.found_by == BY_NAME]
            values = {"tab": _quote(sheet), "cell": entity_cell.address,
                      "value": _shown(entity_cell.value),
                      "entity": _member(entity)}
            if names:
                values["names"] = ", ".join(_block_name(b.name, b.version, b.entity)
                                            for b in names)
            findings.append(_finding(
                "anchor_disagreement", None, ("entity cell", sheet),
                _at(sheet=sheet, entity=entity, address=entity_cell.address),
                "anchor_entity" if names else "anchor_entity_tab", values,
                (("Entity cell", entity_cell.address),
                 ("the tab's entity", _encoded(entity_cell.value)),
                 ("entity the tab and its defined names give", entity),
                 ("defined names on the tab", ", ".join(b.name for b in names) or "none"))))
        if ("Currency" not in missing and unit is not None
                and not _same(currency_cell.value, unit)):
            findings.append(_finding(
                "currency_disagreement", None, ("currency", sheet),
                _at(sheet=sheet, entity=entity, address=currency_cell.address),
                "currency_disagreement",
                {"tab": _quote(sheet), "cell": currency_cell.address,
                 "value": _shown(currency_cell.value), "unit": _member(unit)},
                (("Currency cell", currency_cell.address),
                 ("the tab's currency", _encoded(currency_cell.value)),
                 ("unit of the money accounts on Lists", unit),
                 ("money accounts", "every account on Lists outside the Statistics section"))))
    return findings


def _names_not_used(x, entities) -> list:
    """A defined name of a block's form that the extractor does not read a block through.

    The extractor reads a block through <version>_<entity> only when the name
    refers to one range on that entity's plan tab; otherwise it reads the
    block by its labels, and the name is reported here, so it is never ignored
    in silence. Where no block of that name is found on the tab at all, this
    break and the block's header-not-found break are one fault, so they share
    their anchors and become one break with both reasons.
    """
    findings = []
    for n in x.defined_names:
        for sheet, entity in x.plan_sheets:
            match = re.fullmatch(rf"(?P<version>.+)_{re.escape(entity)}", n.name)
            if not match or entity not in entities or reads_block(n, sheet):
                continue
            where = ", ".join(_quote(d) for d in n.destinations) or "no range"
            scope = (f" and belongs to the tab {_quote(n.scope)}"
                     if n.scope not in (None, sheet) else "")
            found = next((b for b in x.blocks if b.sheet == sheet and b.name == n.name), None)
            anchors = ("anchor", n.name) if found else ("block", sheet, match["version"])
            findings.append(_finding(
                "anchor_disagreement", None, anchors,
                _at(sheet=sheet, block=n.name, entity=entity), "anchor_name_unused",
                {"name": _block_name(n.name, match["version"], entity), "tab": _quote(sheet),
                 "where": where, "scope": scope},
                (("defined name", n.name), ("refers to", n.text),
                 ("scope", n.scope or "the workbook"),
                 ("the block is read", "by its header labels" if found else "not at all"))))
    return findings


def _missing_accounts(x, b) -> list:
    """The accounts on Lists a block lacks, in Lists order."""
    held = {line.account for line in b.lines if line.account}
    return list({a.code: a for a in x.lists.accounts if a.code and a.code not in held}.values())


def _account_text(a) -> str:
    return f"{a.code} {a.name}" if a.name else a.code


def _line_faults(x, b) -> tuple:
    """(lines not on Lists, extra lines, missing accounts) of a block: its line-level faults.

    A label on Lists that is on more than one line of the block: the first, in
    row order, is the account's line, and each later one is an extra (part 2a).
    The missing accounts are in Lists order.
    """
    lines = sorted(b.lines, key=lambda line: line.row)
    off_lists = [line for line in lines if line.account is None]
    seen, extras = set(), []
    for line in lines:
        if line.account is None:
            continue
        if line.account in seen:
            extras.append(line)
        seen.add(line.account)
    return off_lists, extras, _missing_accounts(x, b)


def _not_checked(x, block: str, codes) -> str:
    """The sentence a line-level structure break adds: the block's subtotals, not checked.

    The subtotals are those of codes, in Lists order (part 2a2: the ones the
    accounts concerned feed; every one for a line not on Lists); "" when none.
    """
    subtotals = [a for a in x.lists.accounts if a.code and a.section == SUBTOTAL
                 and a.code in codes]
    if not subtotals:
        return ""
    return " " + MESSAGES["subtotals_not_checked"].format(
        what="subtotals" if len(subtotals) > 1 else "subtotal", block=block,
        verb="are" if len(subtotals) > 1 else "is",
        subtotals=", ".join(_member(a.name or a.code) for a in subtotals))


def _with_clause(finding: _Finding, clause: str) -> _Finding:
    finding.sentence += clause
    return finding


def _displaced(b, faults) -> dict:
    """{(block, row): the missing account} for each line that cannot be placed and pairs with one.

    The reviewer's rule (notes on the 1e brief, and part 2a): the lines of a
    block that cannot be placed, those not on Lists and the extra lines of an
    account already in the block, pair with its missing accounts one to one,
    the k-th such line in row order with the k-th missing account in Lists
    order, so n such lines excuse at most n missing accounts.
    """
    off_lists, extras, missing = faults
    unplaced = sorted(off_lists + extras, key=lambda line: line.row)
    return {(b.name, line.row): account for line, account in zip(unplaced, missing)}


def _fed(lists) -> dict:
    """{account: the Subtotals on Lists it feeds}, from _subtotal_parts (part 2a2).

    A Subtotal is the running total of the accounts above it that carry a ledger
    sign, so an account feeds every Subtotal below it on Lists: Revenue and Cost
    of sales feed Gross profit and EBIT, Operating expenses EBIT alone, and a
    statistic, with no ledger sign, feeds none. A different Lists gives other sets.
    """
    fed: dict = defaultdict(set)
    for code, signed in _subtotal_parts(lists).items():
        for part, _ in signed:
            fed[part].add(code)
    return fed


def _unchecked_subtotals(x) -> dict:
    """{block: the Subtotals the totals control leaves unchecked in it} (part 2a2).

    The narrower rule: a line not on Lists stops every Subtotal of its block; an
    extra line of an account stops those the account and the account it takes
    the place of feed; an account the block lacks stops those it feeds.
    """
    every, fed, found = _subtotal_codes(x.lists), _fed(x.lists), {}
    for b in x.blocks:
        if _why_unreadable(b):
            continue
        faults = _line_faults(x, b)
        off_lists, extras, missing = faults
        codes = set(every) if off_lists else set()
        displaced = _displaced(b, faults)
        for line in extras:
            codes |= fed[line.account]
            account = displaced.get((b.name, line.row))
            if account is not None:
                codes |= fed[account.code]
        for account in missing[len(off_lists) + len(extras):]:
            codes |= fed[account.code]
        if codes:
            found[b.name] = codes
    return found


def _accounts_twice(x, b, block: str, faults, displaced: dict) -> list:
    """One break for each extra line of an account the block already holds (part 2a).

    The message lists every row that carries the label and names the account
    the extra line takes the place of, when it pairs with one; it is anchored
    as a line not on Lists is: its block, its label and the label header.
    """
    _, extras, _ = faults
    if not extras:
        return []
    letter = next((c.letter for c in b.columns if c.role == "label"), b.columns[0].letter)
    carriers: dict = defaultdict(list)
    for line in sorted(b.lines, key=lambda line: line.row):
        if line.account is not None:
            carriers[line.account].append(line)
    findings = []
    for line in extras:
        rows = carriers[line.account]
        values = {"line": _member(line.label), "count": _times(len(rows)), "block": block,
                  "rows": _and([str(r.row) for r in rows]), "row": line.row}
        evidence = (("line item", line.label or ""),
                    ("cells that carry it", ", ".join(f"{letter}{r.row}" for r in rows)))
        account = displaced.get((b.name, line.row))
        if account is not None:
            values["account"] = _member(account.name) if account.name else _member(account.code)
            evidence += (("account it displaced", _account_text(account)),)
        else:
            values["first"] = rows[0].row
        address = f"{letter}{line.row}"
        fed = _fed(x.lists)
        codes = fed[line.account] | (fed[account.code] if account is not None else set())
        finding = _with_clause(_finding(
            "account_twice_in_block", (b.sheet, address), ("cell", b.name, line.label, LABEL_HEADER),
            _at(b.sheet, b.name, b.entity, line.account, address=address),
            "account_twice_in_block_displaced" if account is not None
            else "account_twice_in_block", values, evidence), _not_checked(x, block, codes))
        if b.version == ACTUAL:
            finding = _tie_out_accounts(x, finding, b, [line.account] + (
                [account.code] if account is not None else []))
        findings.append(finding)
    return findings


def _accounts_not_in_block(x, b, block: str, faults) -> list:
    """The accounts on Lists a block lacks that no line that cannot be placed pairs with.

    A line whose label is not on Lists, or an extra line of an account the
    block already holds, is already one break (one fault, one break), and it
    pairs with one missing account (_displaced), which its break names. Every
    missing account left unpaired is listed in one break.
    """
    off_lists, extras, missing = faults
    placed = len(off_lists) + len(extras)
    paired, unpaired = missing[:placed], missing[placed:]
    if not unpaired:
        return []
    evidence = (("accounts not in the block", ", ".join(map(_account_text, unpaired))),
                ("lines in the block not on Lists", str(len(off_lists))))
    if extras:
        evidence += (("extra lines of accounts the block holds", str(len(extras))),)
    if paired:
        evidence += (("missing accounts paired with lines not on Lists" if not extras
                      else "missing accounts paired with lines that cannot be placed",
                      ", ".join(map(_account_text, paired))),)
    fed = _fed(x.lists)
    finding = _with_clause(_finding(
        "accounts_not_in_block", None, ("accounts", b.name),
        _at(b.sheet, b.name, b.entity, address=b.ref), "accounts_not_in_block",
        {"block": block, "count": len(unpaired),
         "accounts": ", ".join(_member(a.name) for a in unpaired)},
        evidence), _not_checked(x, block, set().union(*(fed[a.code] for a in unpaired))))
    if b.version == ACTUAL:
        finding = _tie_out_accounts(x, finding, b, [a.code for a in unpaired])
    return [finding]


def _units(b, block: str, empty_header=None) -> list:
    """One break for a block in which no line has a unit; otherwise one per line with none.

    A block whose "Unit" header is not found, or in which every line lacks a
    unit, is one block_without_units break naming the block (part 2a); a block
    with some units missing keeps one no_unit break per line. Part 2a2: when the
    Unit column's header cell is empty (empty_header, _empty_unit_header), the
    break names that cell, and the column has no header_not_found break.
    """
    column = next((c for c in b.columns if c.role == "unit"), None)
    if column is None or (b.lines and all(line.unit is None for line in b.lines)):
        _, top, _, _ = range_boundaries(b.ref)
        if column is None and empty_header is not None:
            cell = f"{empty_header.letter}{top}"
            return [_finding(
                "block_without_units", None, ("units", b.name),
                _at(b.sheet, b.name, b.entity, address=cell), "block_without_units_header_empty",
                {"block": block, "unit": _quote(UNIT_HEADER), "cell": cell,
                 "column": empty_header.letter},
                (("Unit header", f"{cell}, empty"),
                 ("line items with no unit", str(len(b.lines)))))]
        if column is None:
            address, template, header = b.ref, "block_without_units_header", "not found"
        else:
            rows = [line.row for line in b.lines]
            address = f"{column.letter}{min(rows)}:{column.letter}{max(rows)}"
            template, header = "block_without_units_empty", f"{column.letter}{top}"
        return [_finding(
            "block_without_units", None, ("units", b.name),
            _at(b.sheet, b.name, b.entity, address=address), template,
            {"block": block, "unit": _quote(UNIT_HEADER), "count": len(b.lines),
             "column": column.letter if column else ""},
            (("Unit header", header), ("line items with no unit", str(len(b.lines)))))]
    findings = []
    for line in b.lines:
        if line.unit is None:
            address = f"{column.letter}{line.row}"
            findings.append(_finding(
                "no_unit", (b.sheet, address), ("cell", b.name, line.label, UNIT_HEADER),
                _at(b.sheet, b.name, b.entity, line.account, address=address), "no_unit",
                {"line": _member(line.label), "block": block},
                (("line item", line.label or ""),)))
    return findings


def _version_not_admitted(b, block: str, anchors: tuple = ()) -> _Finding:
    """A block whose version is not Actual, Budget or Forecast: landed, and not built (part 2a).

    Anchored as the block's own anchors are, so a block that also cannot be
    read, or whose defined name disagrees with the sheet, is one break. Part
    2a2: it is the block's one structure break, and a version that fails the
    allow-list gives no rejected_name besides: the evidence holds its reasons.
    """
    evidence = (("version", _encoded(b.version)), ("found by", b.found_by),
                ("versions admitted", ", ".join(EXPECTED_VERSIONS)))
    reasons = allow_list.check(b.version)
    if reasons:
        evidence += (("allow-list reasons", ", ".join(reasons)),)
    return _finding(
        "version_not_admitted", None, anchors or ("anchor", b.name),
        _at(b.sheet, b.name, b.entity, address=b.ref), "version_not_admitted",
        {"block": block, "tab": _quote(b.sheet), "version": _shown(b.version)}, evidence)


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

    A block with a line-level structure break (a line not on Lists, an account
    missing, an account twice) has subtotals left out, months and FY cell
    alike: the control cannot place every line, so a subtotal recomputed from
    Lists would differ for that reason alone, and the structure break says
    which subtotals are not checked (part 2a). Since part 2a2 they are only the
    ones the accounts concerned feed (_unchecked_subtotals); a line not on
    Lists still stops every one. Its leaf lines' FY totals, and every other
    subtotal, are checked as before. A block that shares a cell with another is
    not examined at all.
    """
    if x.batch.source != WORKBOOK:
        return _not_run(TOTALS, NOT_RUN_NO_TOTALS)
    accounts = {a.code: a for a in x.lists.accounts if a.code}
    unchecked = _unchecked_subtotals(x)
    overlapping = {c.block for c in x.cells if c.overlap}
    findings = []
    for cells in _rows(x.cells).values():
        if cells[0].overlap:
            continue             # a block that shares a cell is not examined (part 2a2)
        months = [c for c in cells if c.column_kind == "month"]
        if any(_unreadable(c) for c in months):
            continue
        if cells[0].account in unchecked.get(cells[0].block, ()):
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
    for (block, _), by_account in columns.items():
        if block in overlapping:
            continue
        for code, signed in parts.items():
            if code in unchecked.get(block, ()):
                continue
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
        if s.visibility != "visible":
            continue             # a hidden tab's own break is enough
        for axis, lines in (("row", s.hidden_rows), ("column", s.hidden_columns)):
            for run in _runs(lines, axis):
                findings.append(_hidden_line(s, axis, run[0]) if len(run) == 1
                                else _hidden_run(s, axis, run))
    for number, target in enumerate(x.batch.external_links, start=1):
        users = [f"{f.sheet}!{f.address}" for f in x.formulas
                 if f.kind == "formula" and f"[{number}]" in f.formula]
        findings.append(_finding(
            "external_link", None, ("link", number), _at(), "external_link", {"number": number},
            (("target", target), ("cells that use it", ", ".join(users) or "none"))))
    return _result(HIDDEN_CONTENT, x.batch.batch_id, findings)


def _runs(lines, axis: str) -> list:
    """Hidden rows (or columns) in runs of neighbours, each run in order."""
    def number(h):
        return int(h.index) if axis == "row" else column_index_from_string(h.index)
    runs: list = []
    for h in sorted(lines, key=number):
        if runs and number(h) == number(runs[-1][-1]) + 1:
            runs[-1].append(h)
        else:
            runs.append([h])
    return runs


def _hidden_line(s, axis: str, h) -> _Finding:
    """One hidden row or column with no hidden neighbour: the break of part 1d, unchanged."""
    kind = f"hidden_{axis}"
    return _finding(
        kind, None, (axis, s.name, h.index),
        _at(sheet=s.name, block=h.blocks[0] if h.blocks else None,
            address=f"{h.index}:{h.index}"),
        kind, {"index": h.index, "tab": _quote(s.name)},
        (("cells with content", ", ".join(h.cells) or "none"),
         ("blocks it crosses", ", ".join(h.blocks) or "none")),
        severity=HIDDEN_IN_A_BLOCK if h.blocks else None)


def _hidden_run(s, axis: str, run: list) -> _Finding:
    """A run of neighbouring hidden rows or columns: one break, its range in the evidence."""
    first, last = run[0].index, run[-1].index
    span = f"{first}:{last}"
    blocks = tuple(dict.fromkeys(name for h in run for name in h.blocks))
    cells = [cell for h in run for cell in h.cells]
    return _finding(
        f"hidden_{axis}", None, (axis, s.name, span),
        _at(sheet=s.name, block=blocks[0] if blocks else None, address=span),
        f"hidden_{axis}s", {"first": first, "last": last, "tab": _quote(s.name)},
        (("hidden range", span), (f"hidden {axis}s", str(len(run))),
         ("cells with content", ", ".join(cells) or "none"),
         ("blocks it crosses", ", ".join(blocks) or "none")),
        severity=HIDDEN_IN_A_BLOCK if blocks else None)


# == All five ===============================================================================

def run_controls(x) -> ControlReport:
    """The five controls over one extraction, in a fixed order."""
    return ControlReport(x.batch.batch_id, (structure(x), calculation_state(x),
                                             formula_integrity(x), totals(x),
                                             hidden_content(x)))
