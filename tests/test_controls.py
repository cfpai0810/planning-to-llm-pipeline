"""The five spreadsheet controls (src/controls.py), part 1d, brief 6.1.

Every expectation is written by hand below and never read from the manifest,
so a bug in the build cannot make the files and the expectation agree. The
sample gives exactly D1 to D5 (D4 once, with two reasons) and nothing else; the
clean workbook gives none; each fixture gives exactly its own break. The
mutation proof then shows "exactly" from both sides over the committed
mutation workbooks: the clean workbook plus one fault gains exactly that
fault's break, and the sample less one fault loses exactly that break. Two
more mutations are made here: the sample's hidden Scratch tab made visible
(inside the zip, because openpyxl's save drops cached values), and the
Subtotal clause of the formula-row rule, on extracted rows built in memory.

Part 1e (the reviewer's verdict of 5 November, section 5) adds: every block
holds every account on Lists; each block read through a defined name agrees
with the sheet (the version label above its header row, the tab's Entity
cell), and a block that cannot be read is one break with its reason; each
plan tab's Currency cell is the unit of the money accounts on Lists; a run of
neighbouring hidden rows or columns is one break, and nothing hidden on a
hidden tab is reported apart from the tab; and one test pins a known blind
spot, a row fetching the wrong driver in all twelve months.

The reviewer's notes on the 1e brief (6 November) add: a line not on Lists
pairs with one missing account (the k-th such line in row order with the k-th
missing account in Lists order) and its break names the account it displaced;
the version label is found through the block's header row, by its labels; and
fixtures for a changed Entity cell, a block name on another entity's tab, a
changed version label and a block name one row up.

Each committed workbook is extracted once per session (tests/extractions.py).
"""

import dataclasses
import hashlib
import json
import re
import zipfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from tests.extractions import (CLEAN, FIXTURES, MUTATIONS, ROOT, SAMPLE, breaks, extraction,
                               report)

CONTROLS_SOURCE = ROOT / "src" / "controls.py"
CONTROLS_DOC = ROOT / "docs" / "controls.md"
BUILD_NOTE = ROOT / "docs" / "rebuilding_the_sample.md"
READERS = FIXTURES / "readers"
ENTITIES = ("ES", "AT", "DE")
ACCOUNTS = ("4000", "4100", "5000", "5100", "5200", "GP", "6000", "6100", "6200", "6300", "EBIT",
            "VOL", "HC")
LINK_TARGET = "calder_plan_fy2025.xlsx"
CONTROL_NAMES = ("structure", "calculation_state", "formula_integrity", "totals", "hidden_content")
LIMITS = ("These controls detect; they do not prevent. Nothing here replaces an independent "
          "review of the model by a second person: an acknowledgement records that a named "
          "person saw each break, not that anyone re-performed the work.")

# The severity of each kind, as proposed in docs/controls.md: high when a figure
# as read is wrong or cannot be read, medium when a figure may be wrong or a
# member is left out, low when a person should look but nothing is shown wrong.
SEVERITY = {
    "missing_tab": "high", "header_not_found": "high", "duplicate_period": "high",
    "period_gap": "high", "entity_not_on_lists": "medium", "account_not_on_lists": "medium",
    "rejected_name": "medium", "no_unit": "medium", "merged_cells": "medium",
    "tab_not_on_list": "low", "no_cached_value": "high", "error_value": "high",
    "typed_constant": "medium", "embedded_constant": "medium", "pattern": "low",
    "fy_total": "high", "subtotal": "high", "hidden_sheet": "medium", "hidden_row": "medium",
    "hidden_column": "medium", "external_link": "medium",
    # Part 1e: the anchors, the blocks' accounts and the currency.
    "heading_not_found": "high", "anchor_disagreement": "high", "block_unreadable": "high",
    "accounts_not_in_block": "medium", "currency_disagreement": "high",
    "money_units_differ": "high",
    # Part 2a: one fault one break, and the three versions of the star.
    "account_twice_in_block": "medium", "block_without_units": "medium",
    "version_not_admitted": "medium",
    # Part 2a2: no cell in two blocks, blank figures, and a header not read.
    "blocks_overlap": "high", "blank_figure": "medium", "blank_month": "medium",
    "header_not_read": "low",
}
# Part 2a: the clause a line-level structure break adds, for a block of the
# committed Lists, whose subtotals are Gross profit and EBIT.
NOT_CHECKED = ' The subtotals of block "{}" are not checked: "Gross profit", "EBIT".'
# Part 2a2: the narrower rule leaves EBIT alone unchecked for an operating expense.
EBIT_NOT_CHECKED = ' The subtotal of block "{}" is not checked: "EBIT".'
# Part 2a2: the sentence of the break that explains an Actual block not built.
TIE_OUT = ' Reconcile leaves the Actual figures of "{}" out of the ledger tie-out.'
# Part 2a2: two blocks on one range (names_same_range, and name_wrong_version on the
# small base), with the Forecast header the misdirected name leaves unread.
OVERLAP_ES = ('The blocks "Budget_ES" (A22:O35) and "Forecast_ES" (A22:O35) on "Plan_ES" share '
              '169 cells, from C23 to O35: no cell may belong to two blocks, so none of them is '
              'built. The header in A38, under the label "Forecast", is not read, because the '
              'defined name "Forecast_ES" reads that version elsewhere. The defined name '
              '"Forecast_ES" names version "Forecast", but the label above its header row, in A21 '
              'on "Plan_ES", reads "Budget".')

# -- The sample's breaks, by hand -------------------------------------------------------------
# (control, kind, reasons, severity, sheet, block, entity, account, period, address, message)
D1 = ("formula_integrity", "typed_constant", ("typed_constant",), "medium",
      "Plan_AT", "Budget_AT", "AT", "6100", "2026-03", "E30",
      'The cell E30 on "Plan_AT" holds a typed constant, 45,000, in a formula row of block '
      '"Budget_AT".')
D2 = ("totals", "fy_total", ("fy_total",), "high",
      "Plan_DE", "Budget_DE", "DE", "6000", "FY2026", "O29",
      'The FY2026 total of "Salaries" in block "Budget_DE" is 6,334,880 as the file holds it; '
      'its months add up to 6,926,720; the difference is -591,840.')
D3 = ("hidden_content", "hidden_row", ("hidden_row",), "medium",
      "Plan_DE", None, None, None, None, "53:53",
      'Row 53 on "Plan_DE" is hidden.')
D4 = ("formula_integrity", "embedded_constant", ("embedded_constant", "pattern"), "medium",
      "Plan_ES", "Forecast_ES", "ES", "4000", "2026-09", "K39",
      'The formula in K39 on "Plan_ES" holds the numeric constant 1.02. For review: the formula '
      'in K39 on "Plan_ES" differs from the pattern of its row.')
D5 = ("hidden_content", "hidden_sheet", ("hidden_sheet",), "medium",
      "Scratch", None, None, None, None, None,
      'The tab "Scratch" is hidden.')
PLANTED = {"D1": D1, "D2": D2, "D3": D3, "D4": D4, "D5": D5}

# -- Each fixture's breaks, by hand -------------------------------------------------------------
# (control, kind, reasons, severity, sheet, address, message)
FIXTURE_BREAKS = {
    "error_values": [
        ("calculation_state", "error_value", ("error_value",), "high", "Control", "C21",
         'The cell C21 on "Control" holds the error value #DIV/0!.')],
    "array_formula": [],
    "shared_formula": [],
    "shifted": [],
    "merged_cells": [
        ("structure", "merged_cells", ("merged_cells",), "medium", "Plan_ES", "M7:N7",
         'Cells M7:N7 on "Plan_ES" are merged inside block "Actual_ES".')],
    "no_units": [
        ("structure", "no_unit", ("no_unit",), "medium", "Plan_ES", "B31",
         'The line item "Rent and facilities" in block "Budget_ES" has no unit.')],
    "missing_tab": [
        # Part 2a2 (the reviewer's ruling of 8 November, item 3): AT's Actual block is not
        # read, so reconcile leaves AT out and this break says so; its id stays.
        ("structure", "missing_tab", ("missing_tab",), "high", "Plan_AT", None,
         'The tab "Plan_AT" is missing: it is on the expected list.' + TIE_OUT.format("AT"))],
    "missing_header": [
        ("structure", "header_not_found", ("header_not_found",), "high", "Plan_ES", "O22",
         'A header was not found in block "Budget_ES": column O on "Plan_ES" has no header, so '
         'its cells are not read.')],
    "duplicate_period": [
        ("structure", "duplicate_period", ("duplicate_period",), "high", "Plan_ES", "I6,J6",
         'The period 2026-07 heads 2 columns of block "Actual_ES": I, J.')],
    "unknown_account": [
        # The one change the reviewer asked for in an earlier fixture's result:
        # the message names the account the renamed line displaced. Part 2a adds
        # the subtotals the line leaves unchecked; its id, kind and severity stay.
        ("structure", "account_not_on_lists", ("account_not_on_lists",), "medium", "Plan_ES",
         "A50", 'The line item "Cases sold" in block "Forecast_ES" is not an account on Lists; '
                'it takes the place of "Volume", an account the block lacks.'
                + NOT_CHECKED.format("Forecast_ES"))],
    "external_link": [
        ("hidden_content", "external_link", ("external_link",), "medium", None, None,
         "The workbook links to another workbook (link 1).")],
    "names": [
        ("structure", "rejected_name", ("rejected_name",), "medium", "Lists", "B4",
         'The account name in B4 on "Lists" fails the member-name allow-list (characters): the '
         'member is excluded from the star schema and from every prompt.'),
        ("structure", "rejected_name", ("rejected_name",), "medium", "Lists", "B7",
         'The account name in B7 on "Lists" fails the member-name allow-list (length): the '
         'member is excluded from the star schema and from every prompt.'),
        ("structure", "rejected_name", ("rejected_name",), "medium", "Lists", "B10",
         'The account name in B10 on "Lists" fails the member-name allow-list (words): the '
         'member is excluded from the star schema and from every prompt.')],
    "extra_tab": [
        ("structure", "tab_not_on_list", ("tab_not_on_list",), "low", "Notes", None,
         'The visible tab "Notes" is a tab not on the expected list.')],
    "typed_subtotal": [
        ("formula_integrity", "typed_constant", ("typed_constant",), "medium", "Plan_ES", "H17",
         'The cell H17 on "Plan_ES" holds a typed constant, 278,142, in a formula row of block '
         '"Actual_ES".')],
    # Part 1e.
    "block_short": [
        # Part 2a2, the narrower subtotal rule: Volume and Headcount feed no subtotal, so
        # the block's subtotals are checked and the message loses its second sentence.
        ("structure", "accounts_not_in_block", ("accounts_not_in_block",), "medium", "Plan_ES",
         "A22:O33", 'The block "Budget_ES" does not hold 2 of the accounts on Lists: "Volume", '
         '"Headcount".')],
    "name_shifted": [
        ("structure", "block_unreadable", ("block_unreadable",), "high", "Plan_ES", "A23:O36",
         'The block "Budget_ES" on "Plan_ES" cannot be read: the first row of its range, '
         'A23:O23, has no "Line item" header and no month header.')],
    "name_wrong_version": [
        # Changed on purpose in part 2a2 (the coordinator's brief, Trap 1): Forecast_ES and
        # Budget_ES on one range are an overlap, one break with the name's disagreement
        # folded in. At 1e and 2a it was ST-4d69025f5d6d, anchor_disagreement.
        ("structure", "blocks_overlap", ("blocks_overlap", "anchor_disagreement"), "high",
         "Plan_ES", "C23:O35", OVERLAP_ES)],
    "currency_changed": [
        ("structure", "currency_disagreement", ("currency_disagreement",), "high", "Plan_AT", "B3",
         'The Currency cell of "Plan_AT", B3, reads "USD", but the money accounts on Lists are '
         'in "EUR".')],
    "wrong_driver_row": [],
    # The reviewer's notes on the 1e brief.
    "entity_changed": [
        ("structure", "anchor_disagreement", ("anchor_disagreement",), "high", "Plan_ES", "B2",
         'The Entity cell of "Plan_ES", B2, reads "AT", but the defined names of its blocks, '
         '"Actual_ES", "Budget_ES", "Forecast_ES", say "ES".')],
    "name_other_tab": [
        ("structure", "anchor_disagreement", ("anchor_disagreement",), "high", "Plan_AT", None,
         'The defined name "Budget_AT" has the form of a block on "Plan_AT" but is not used, '
         'because it refers to "Plan_ES!$A$22:$O$35".')],
    "version_label_changed": [
        ("structure", "anchor_disagreement", ("anchor_disagreement",), "high", "Plan_ES",
         "A22:O35",
         'The defined name "Budget_ES" names version "Budget", but the label above its header '
         'row, in A21 on "Plan_ES", reads "Plan B".')],
    "name_shifted_up": [
        ("structure", "block_unreadable", ("block_unreadable",), "high", "Plan_ES", "A21:O34",
         'The block "Budget_ES" on "Plan_ES" cannot be read: the first row of its range, '
         'A21:O21, has no "Line item" header and no month header.')],
    # Part 2a: the reviewer's Phase 2 brief, section 3. Each is the full clean
    # workbook with one change, and each gives exactly one break or none.
    "renamed_feeds_subtotal": [
        ("structure", "account_not_on_lists", ("account_not_on_lists",), "medium", "Plan_ES",
         "A45", 'The line item "Staff costs" in block "Forecast_ES" is not an account on Lists; '
                'it takes the place of "Salaries", an account the block lacks.'
                + NOT_CHECKED.format("Forecast_ES"))],
    "duplicate_account": [
        ("structure", "account_twice_in_block", ("account_twice_in_block",), "medium", "Plan_ES",
         "A29", 'The line item "Salaries" appears twice in block "Budget_ES", on rows 25 and 29; '
                'the one on row 29 takes the place of "Materials", an account the block lacks.'
                + NOT_CHECKED.format("Budget_ES"))],
    "unit_column_empty": [
        ("structure", "block_without_units", ("block_without_units",), "medium", "Plan_ES",
         "B23:B35", 'No line item in block "Budget_ES" has a unit: the "Unit" column, B, is '
                    'empty on all 13 lines.')],
    "extra_version_block": [
        ("structure", "version_not_admitted", ("version_not_admitted",), "medium", "Plan_ES",
         "A54:O67", 'The version of block "Plan B_ES" on "Plan_ES", "Plan B", is not one of '
                    'Actual, Budget, Forecast; the block is landed and not built.')],
    "currency_lowercase": [],
    "currency_trailing_space": [],
    "version_label_lowercase": [],
    # Part 2a2: the reviewer's Stop 1 verdict, section 5, and the ruling of 8 November.
    "names_same_range": [
        ("structure", "blocks_overlap", ("blocks_overlap", "anchor_disagreement"), "high",
         "Plan_ES", "C23:O35", OVERLAP_ES)],
    "names_partly_overlap": [
        ("structure", "block_unreadable", ("block_unreadable",), "high", "Plan_ES", "A30:O51",
         'The block "Forecast_ES" on "Plan_ES" cannot be read: the first row of its range, '
         'A30:O30, has no "Line item" header and no month header. Its range overlaps the block '
         '"Budget_ES" (A22:O35), whose cells are read as that block\'s alone; nothing is built '
         'for "Forecast_ES".')],
    "blank_cell_forecast": [
        ("structure", "blank_figure", ("blank_figure",), "medium", "Plan_ES", "E41",
         'The line item "Materials" in block "Forecast_ES" is blank in 2026-03, in E41, while '
         'other lines of the block hold figures for that month; nothing is built for it.')],
    "blank_run_budget": [
        ("structure", "blank_figure", ("blank_figure",), "medium", "Plan_DE", "F30:H30",
         'The line item "Marketing" in block "Budget_DE" is blank from 2026-04 to 2026-06, from '
         'F30, while other lines of the block hold figures for those months; nothing is built '
         'for them.')],
    "blank_month_budget": [
        ("structure", "blank_month", ("blank_month",), "medium", "Plan_DE", "J23:J35",
         'Every account and statistic line of block "Budget_DE" is blank in 2026-08, from J23; '
         'nothing is built for the block in that month.')],
    "unit_header_empty": [
        ("structure", "block_without_units", ("block_without_units",), "medium", "Plan_DE",
         "B22", 'No line item in block "Budget_DE" has a unit: the "Unit" header, B22, is empty, '
                'so column B is not read as the units.')],
    "stray_line_item": [
        ("structure", "header_not_read", ("header_not_read",), "low", "Plan_ES", "A4",
         'The cell A4 on "Plan_ES" reads "Line item", but no block is read from it: the block it '
         'heads, A4:A19, under the label "Currency", overlaps the block "Actual_ES", which is '
         'read through its defined name.')],
    "header_only_block": [
        ("structure", "header_not_read", ("header_not_read",), "low", "Plan_ES", "A60",
         'The cell A60 on "Plan_ES" reads "Line item", but no block is read from it: the cell '
         'above it, A59, is empty, so nothing gives the block\'s version.')],
    "block_short_wrong_ebit": [
        # Three breaks, as the reviewer's ruling of 8 November expects: Volume and Headcount
        # feed no subtotal, so the EBIT left wrong in March is a subtotal break, and its
        # formula is outside its row's pattern; nothing on Gross profit.
        ("structure", "accounts_not_in_block", ("accounts_not_in_block",), "medium", "Plan_ES",
         "A38:O49", 'The block "Forecast_ES" does not hold 2 of the accounts on Lists: "Volume", '
                    '"Headcount".'),
        ("formula_integrity", "pattern", ("pattern",), "low", "Plan_ES", "E49",
         'For review: the formula in E49 on "Plan_ES" differs from the pattern of its row.'),
        ("totals", "subtotal", ("subtotal",), "high", "Plan_ES", "E49",
         'The 2026-03 figure of "EBIT" in block "Forecast_ES" is 211,072 as the file holds it; '
         'recomputed from its accounts on Lists it is 189,672; the difference is 21,400.')],
    "actual_account_displaced": [
        ("structure", "account_twice_in_block", ("account_twice_in_block",), "medium", "Plan_AT",
         "A10", 'The line item "Materials" appears twice in block "Actual_AT", on rows 9 and 10; '
                'the one on row 10 takes the place of "Packaging", an account the block lacks.'
                + NOT_CHECKED.format("Actual_AT")
                + ' Reconcile leaves "Materials" and "Packaging" of "AT" out of the ledger '
                  'tie-out.')],
    "landed_block_short": [
        ("structure", "version_not_admitted", ("version_not_admitted",), "medium", "Plan_ES",
         "A54:C59", 'The version of block "Scenario_ES" on "Plan_ES", "Scenario", is not one of '
                    'Actual, Budget, Forecast; the block is landed and not built.')],
    "version_label_padded": [
        ("structure", "version_not_admitted", ("version_not_admitted",), "medium", "Plan_ES",
         "A54:O67", 'The version of block (a name that fails the allow-list) on "Plan_ES", a '
                    'value that starts or ends with a space (shown encoded in the evidence), is '
                    'not one of Actual, Budget, Forecast; the block is landed and not built.')],
}


@pytest.fixture(scope="module")
def controls():
    from src import controls
    return controls


def located(b) -> tuple:
    return (b.control, b.kind, b.reasons, b.severity, b.sheet, b.block, b.entity, b.account,
            b.period, b.address, b.message)


def short(b) -> tuple:
    return (b.control, b.kind, b.reasons, b.severity, b.sheet, b.address, b.message)


def whole(b) -> tuple:
    """Every field of a break but its batch id: the same break in two files compares equal."""
    return tuple((k, v) for k, v in dataclasses.asdict(b).items() if k != "batch_id")


def zip_copy(source: Path, target: Path, edit) -> Path:
    """A copy of a workbook edited inside the zip: openpyxl's save would drop the cache."""
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as new:
        for info in old.infolist():
            new.writestr(info, edit(info.filename, old.read(info.filename)),
                         compress_type=zipfile.ZIP_DEFLATED)
    return target


def cells_of(x, block=None, line=None, column_kind=None) -> list:
    return [c for c in x.cells if (block is None or c.block == block)
            and (line is None or c.line == line)
            and (column_kind is None or c.column_kind == column_kind)]


def with_cells(base, changed: dict):
    """The extraction with some block cells replaced in memory, keyed by (sheet, address)."""
    return dataclasses.replace(base, cells=tuple(changed.get((c.sheet, c.address), c)
                                                 for c in base.cells))


def typed(c, amount=None):
    """A formula cell turned into a typed constant holding the same (or another) figure."""
    return dataclasses.replace(c, kind="constant", formula=None, cached=None,
                               amount=c.amount if amount is None else amount)


# -- The break ---------------------------------------------------------------------------------

class TestTheBreak:
    def test_the_fields(self, controls):
        assert [f.name for f in dataclasses.fields(controls.Break)] == [
            "break_id", "batch_id", "control", "kind", "reasons", "severity", "sheet", "block",
            "entity", "account", "period", "address", "message", "evidence"]

    def test_the_severity_rules_are_the_documented_ones(self, controls):
        assert controls.SEVERITY == SEVERITY
        assert set(SEVERITY) == {k for kinds in controls.KINDS.values() for k in kinds}
        assert controls.CONTROLS == CONTROL_NAMES

    def test_the_id_is_built_from_the_control_and_the_anchors(self):
        # D1's anchors: its block, its line's label and its column's header.
        (d1,) = [b for b in breaks(SAMPLE) if b.address == "E30"]
        text = json.dumps(["formula_integrity", "cell", "Budget_AT", "Marketing", "2026-03"],
                          ensure_ascii=False, separators=(",", ":"))
        assert d1.break_id == "FI-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

    def test_the_id_is_stable_across_files_and_unique_within_one(self):
        sample = {b.address or b.sheet: b.break_id for b in breaks(SAMPLE)}
        assert len(set(sample.values())) == len(sample) == 5
        for n, key in ((1, "E30"), (2, "O29"), (3, "53:53"), (4, "K39"), (5, "Scratch")):
            (alone,) = breaks(MUTATIONS / f"clean_plus_d{n}.xlsx")
            assert alone.break_id == sample[key]
        ids = [b.break_id for b in breaks(FIXTURES / "no_cache.xlsx")]
        assert len(set(ids)) == len(ids) == 372
        assert all(re.fullmatch(r"(ST|CS|FI|TO|HC)-[0-9a-f]{12}", i)
                   for i in ids + list(sample.values()))

    def test_every_break_carries_its_batch(self):
        assert {b.batch_id for b in breaks(SAMPLE)} == {extraction(SAMPLE).batch.batch_id}


# -- The sample and the clean workbook --------------------------------------------------------

class TestTheSample:
    def test_exactly_d1_to_d5_and_nothing_else(self):
        assert Counter(located(b) for b in breaks(SAMPLE)) == Counter(PLANTED.values())

    def test_d4_is_one_break_with_two_reasons(self):
        d4 = [b for b in breaks(SAMPLE) if b.address == "K39"]
        assert len(d4) == 1 and d4[0].reasons == ("embedded_constant", "pattern")

    def test_the_evidence(self):
        found = {b.address or b.sheet: dict(b.evidence) for b in breaks(SAMPLE)}
        assert found["E30"] == {
            "typed value": "45000",
            "why it is a formula row": "11 of the 12 month cells that hold something are formulas"}
        assert found["O29"] == {"as the file holds it": "6334880", "months added up": "6926720",
                                "difference": "-591840"}
        assert found["53:53"] == {"cells with content": "A53, B53, L53",
                                  "blocks it crosses": "none"}
        assert found["Scratch"] == {"visibility": "hidden", "cells with content": "13",
                                    "formulas": "0"}
        assert found["K39"]["numeric constants"] == "1.02"
        assert list(found["K39"]) == ["numeric constants", "formula", "pattern of its row",
                                      "pattern of this cell", "cells in the row's pattern",
                                      "origin"]

    def test_every_control_ran(self):
        for path in (SAMPLE, CLEAN):
            assert [(r.control, r.status, r.reason) for r in report(path).results] == [
                (name, "run", None) for name in CONTROL_NAMES]

    def test_the_clean_workbook_gives_zero(self):
        assert breaks(CLEAN) == ()
        assert all(r.passed for r in report(CLEAN).results)

    def test_the_hidden_tab_is_not_a_structure_break(self):
        assert report(SAMPLE).result("structure").breaks == ()


# -- The fixtures --------------------------------------------------------------------------------

class TestTheFixtures:
    @pytest.mark.parametrize("name", sorted(FIXTURE_BREAKS))
    def test_each_fixture_gives_exactly_its_own_break(self, name):
        assert [short(b) for b in breaks(FIXTURES / f"{name}.xlsx")] == FIXTURE_BREAKS[name]

    def test_no_cache_gives_a_break_for_every_formula_cell(self):
        found = breaks(FIXTURES / "no_cache.xlsx")
        assert len(found) == 372
        assert Counter((b.control, b.kind, b.reasons, b.severity, b.sheet) for b in found) == {
            ("calculation_state", "no_cached_value", ("no_cached_value",), "high", "Plan_ES"): 363,
            ("calculation_state", "no_cached_value", ("no_cached_value",), "high", "Control"): 9}
        first = next(b for b in found if b.address == "O7")
        assert first.message == 'The formula in O7 on "Plan_ES" has no cached value.'
        assert (first.block, first.account, first.period) == ("Actual_ES", "4000", "FY2026")

    def test_the_array_formula_is_recorded_as_an_array_not_a_constant(self):
        x = extraction(FIXTURES / "array_formula.xlsx")
        (cell,) = [c for c in x.cells if c.address == "G34" and c.sheet == "Plan_ES"]
        assert cell.kind == "array_formula"
        assert report(FIXTURES / "array_formula.xlsx").result("formula_integrity").recorded == (
            ("Plan_ES!G34", "array formula: recorded, not read as text, not compared with the "
                            "pattern of its row"),)

    def test_the_duplicate_period_names_the_month_it_displaces(self):
        (b,) = breaks(FIXTURES / "duplicate_period.xlsx")
        assert dict(b.evidence) == {"header cells": "I6, J6", "months with no column": "2026-08"}
        assert (b.block, b.period) == ("Actual_ES", "2026-07")

    def test_the_rejected_names_keep_the_name_in_the_evidence_only(self):
        found = breaks(FIXTURES / "names.xlsx")
        names = [dict(b.evidence)["name"] for b in found]
        assert names == ["Product sales; net of returns",
                         "Verpackungsgemeinkosten Fertigungsgemeinkostenzuschlag "
                         "Verwaltungsgemeinkostenzuschlag Vertriebsgemeinkostenzuschlag Ist",
                         "Staff canteen tea and cakes for the night shift"]
        for b, name in zip(found, names):
            assert name not in b.message and "excluded from the star schema" in b.message

    def test_the_external_link_target_is_evidence_and_nothing_else(self):
        (b,) = breaks(FIXTURES / "external_link.xlsx")
        assert dict(b.evidence) == {"target": LINK_TARGET, "cells that use it": "Control!C21"}
        rest = {k: v for k, v in dataclasses.asdict(b).items() if k != "evidence"}
        assert not any(LINK_TARGET in str(v) for v in rest.values())


# -- The mutation proof ----------------------------------------------------------------------------

class TestTheMutationProof:
    @pytest.mark.parametrize("name", list(PLANTED))
    def test_the_clean_workbook_plus_one_fault_gains_exactly_its_break(self, name):
        plus = {whole(b) for b in breaks(MUTATIONS / f"clean_plus_{name.lower()}.xlsx")}
        clean = {whole(b) for b in breaks(CLEAN)}
        gained, lost = plus - clean, clean - plus
        assert lost == set() and len(gained) == 1
        (fault,) = [b for b in breaks(MUTATIONS / f"clean_plus_{name.lower()}.xlsx")]
        assert located(fault) == PLANTED[name]
        assert gained == {whole(b) for b in breaks(SAMPLE) if located(b) == PLANTED[name]}

    @pytest.mark.parametrize("name", list(PLANTED))
    def test_the_sample_less_one_fault_loses_exactly_its_break(self, name):
        minus = {whole(b) for b in breaks(MUTATIONS / f"sample_minus_{name.lower()}.xlsx")}
        sample = {whole(b) for b in breaks(SAMPLE)}
        lost, gained = sample - minus, minus - sample
        assert gained == set() and len(lost) == 1
        assert lost == {whole(b) for b in breaks(SAMPLE) if located(b) == PLANTED[name]}


class TestTwoMoreMutations:
    def test_scratch_made_visible(self, controls, tmp_path):
        def show(name, data):
            if name == "xl/workbook.xml":
                return data.replace(b'<sheet name="Scratch" sheetId="7" state="hidden"',
                                    b'<sheet name="Scratch" sheetId="7" state="visible"')
            return data
        from src.extract import extract_workbook
        base = MUTATIONS / "clean_plus_d5.xlsx"
        visible = extract_workbook(zip_copy(base, tmp_path / "scratch_visible.xlsx", show))
        assert [(s.name, s.visibility) for s in visible.batch.sheets][-1] == ("Scratch", "visible")
        before = {located(b) for b in breaks(base)}
        after = {located(b) for b in controls.run_controls(visible).breaks()}
        assert before - after == {D5}
        assert after - before == {
            ("structure", "tab_not_on_list", ("tab_not_on_list",), "low", "Scratch", None, None,
             None, None, None, 'The visible tab "Scratch" is a tab not on the expected list.')}

    def test_a_subtotal_typed_across_a_whole_row_is_still_found(self, controls):
        base = extraction(CLEAN)
        row = [c for c in cells_of(base, "Actual_ES", "EBIT", "month") if c.kind == "formula"]
        assert [c.address for c in row] == [f"{c}17" for c in "CDEFGH"]
        changed = {(c.sheet, c.address): typed(c) for c in row}
        typed_row = [changed.get((c.sheet, c.address), c)
                     for c in cells_of(base, "Actual_ES", "EBIT", "month")]
        # By the counts alone it is not a formula row; the Subtotal clause makes it one.
        assert controls.formula_row(typed_row) is False
        assert controls.formula_row(typed_row, subtotal=True) is True
        found = controls.formula_integrity(with_cells(base, changed)).breaks
        assert [(b.address, b.reasons, b.severity) for b in found] == [
            (f"{c}17", ("typed_constant",), "medium") for c in "CDEFGH"]
        assert {dict(b.evidence)["why it is a formula row"] for b in found} == {
            "its account is a Subtotal on Lists"}

    def test_an_input_row_typed_across_is_not_a_formula_row(self, controls):
        # The clause is for Subtotals only: a Budget line typed across all
        # twelve months is not a formula row, so it gives nothing.
        base = extraction(CLEAN)
        months = cells_of(base, "Budget_ES", "Salaries", "month")
        changed = {(c.sheet, c.address): typed(c) for c in months}
        assert controls.formula_integrity(with_cells(base, changed)).breaks == ()

    def test_typed_subtotal_gives_one_break(self):
        assert [b.address for b in breaks(FIXTURES / "typed_subtotal.xlsx")] == ["H17"]

    @pytest.mark.parametrize("path", [CLEAN, SAMPLE, *sorted(MUTATIONS.glob("*.xlsx"))],
                             ids=lambda p: p.name)
    def test_the_subtotal_clause_changes_nothing_in_the_committed_workbooks(self, controls, path):
        x = extraction(path)
        subtotals = {a.code for a in x.lists.accounts if a.section == "Subtotal"}
        rows: dict = {}
        for c in x.cells:
            if c.column_kind == "month":
                rows.setdefault((c.block, c.line), []).append(c)
        assert len(rows) == 9 * 13
        for (block, line), months in rows.items():
            subtotal = months[0].account in subtotals
            assert controls.formula_row(months, subtotal) == controls.formula_row(months)


# -- The formula-row rule and the embedded constants -------------------------------------------

class TestTheFormulaRowRule:
    def test_the_counts(self, controls):
        base = cells_of(extraction(CLEAN), "Budget_ES", "Marketing", "month")
        one_typed = [typed(c) if i == 0 else c for i, c in enumerate(base)]
        half = [typed(c) if i < 6 else c for i, c in enumerate(base)]
        most = [typed(c) if i < 7 else c for i, c in enumerate(base)]
        assert controls.formula_row(one_typed) and controls.formula_row(half)
        assert not controls.formula_row(most)
        actual = cells_of(extraction(CLEAN), "Actual_ES", "Marketing", "month")
        assert Counter(c.kind for c in actual) == {"constant": 6, "empty": 6}
        assert not controls.formula_row(actual)
        assert not controls.formula_row([c for c in actual if c.kind == "empty"])

    def test_an_array_formula_counts_as_a_formula(self, controls):
        base = cells_of(extraction(CLEAN), "Budget_ES", "Marketing", "month")
        cells = [typed(c) if i < 6 else dataclasses.replace(c, kind="array_formula")
                 for i, c in enumerate(base)]
        assert controls.formula_row(cells)

    def test_the_fy_column_follows_the_same_rule_by_column(self, controls):
        base = extraction(CLEAN)
        (fy,) = [c for c in cells_of(base, "Budget_DE", "Salaries") if c.column_kind == "total"]
        found = controls.formula_integrity(with_cells(base, {(fy.sheet, fy.address): typed(fy)}))
        assert [(b.address, b.message) for b in found.breaks] == [
            ("O29", 'The cell O29 on "Plan_DE" holds a typed constant, 6,926,720, in the formula '
                    'column FY2026 of block "Budget_DE".')]
        assert dict(found.breaks[0].evidence)["why it is a formula column"] == \
            "12 of the 13 cells of the column that hold something are formulas"

    def test_a_typed_fy_column_still_gives_its_subtotals(self, controls):
        # Typed throughout, the FY column is not a formula column; the two
        # Subtotal rows are formula rows whatever the counts, FY cell included.
        base = extraction(CLEAN)
        column = [c for c in cells_of(base, "Budget_ES") if c.column_kind == "total"]
        changed = {(c.sheet, c.address): typed(c) for c in column}
        found = controls.formula_integrity(with_cells(base, changed)).breaks
        assert [(b.address, b.account) for b in found] == [("O28", "GP"), ("O33", "EBIT")]
        assert {dict(b.evidence)["why it is a formula row"] for b in found} == {
            "its account is a Subtotal on Lists"}
        assert found[0].message == ('The cell O28 on "Plan_ES" holds a typed constant, '
                                    f'{column[5].amount:,}, in a formula row of block '
                                    '"Budget_ES".')

    def test_typed_constants_are_found_in_actual_budget_and_forecast(self, controls):
        base = extraction(CLEAN)
        changed = {}
        for block in ("Actual_DE", "Budget_DE", "Forecast_DE"):
            (c,) = [c for c in cells_of(base, block, "Gross profit") if c.period == "2026-02"]
            changed[(c.sheet, c.address)] = typed(c)
        found = controls.formula_integrity(with_cells(base, changed)).breaks
        assert sorted((b.block, b.kind) for b in found) == [
            ("Actual_DE", "typed_constant"), ("Budget_DE", "typed_constant"),
            ("Forecast_DE", "typed_constant")]


class TestEmbeddedConstants:
    @pytest.mark.parametrize("formula, found", [
        ("=ROUND(A1*B1,0)", ()),
        ("=ROUND(A1,-2)", ()),
        ("=ROUND( A1 , 2 )", ()),
        ("=ROUND(A1*1.02,0)", ("1.02",)),
        ("=A1*2", ("2",)),
        ("=ROUND(A1,1+1)", ("1", "1")),
        ("=SUM(ROUND(A1,0),3)", ("3",)),
        ('=SUMIFS(B$5:B$70,C$5:C$70,"1.02")', ()),
        ("=IF(A1>0,A1,0)", ("0", "0")),
        ("=A1*1E3", ("1E3",)),
        ("=Plan_ES!O33+Sheet2!$A$1", ()),
    ])
    def test_numeric_literals(self, controls, formula, found):
        assert controls.numeric_literals(formula) == found

    def test_every_committed_clean_formula_holds_none(self, controls):
        for f in extraction(CLEAN).formulas:
            assert controls.numeric_literals(f.formula) == (), f.address


# -- Totals ------------------------------------------------------------------------------------

class TestTheTotals:
    def test_a_stock_is_checked_against_its_last_month_with_a_figure(self, controls):
        base = extraction(CLEAN)
        row = cells_of(base, "Budget_ES", "Headcount")
        december = next(c for c in row if c.period == "2026-12")
        fy = next(c for c in row if c.period == "FY2026")
        changed = {(c.sheet, c.address): dataclasses.replace(c, amount=amount)
                   for c, amount in ((december, Decimal(70)), (fy, Decimal(64)))}
        (b,) = controls.totals(with_cells(base, changed)).breaks
        assert (b.address, b.kind, b.severity) == ("O35", "fy_total", "high")
        assert b.message == ('The FY2026 figure of "Headcount" in block "Budget_ES" is 64 as the '
                             'file holds it; its last month with a figure, 2026-12, holds 70; '
                             'the difference is -6.')

    def test_in_the_actual_block_the_last_month_with_a_figure_is_june(self, controls):
        base = extraction(CLEAN)
        fy = next(c for c in cells_of(base, "Actual_ES", "Headcount") if c.period == "FY2026")
        assert fy.formula == "=H19" and fy.amount == Decimal(64)
        changed = {(fy.sheet, fy.address): dataclasses.replace(fy, amount=Decimal("63.5"))}
        (b,) = controls.totals(with_cells(base, changed)).breaks
        assert b.message == ('The FY2026 figure of "Headcount" in block "Actual_ES" is 63.5 as '
                             'the file holds it; its last month with a figure, 2026-06, holds '
                             '64; the difference is -0.5.')

    def test_a_subtotal_is_recomputed_month_by_month_from_lists(self, controls):
        base = extraction(CLEAN)
        row = cells_of(base, "Budget_ES", "Gross profit")
        january = next(c for c in row if c.period == "2026-01")
        fy = next(c for c in row if c.period == "FY2026")
        changed = {(c.sheet, c.address): dataclasses.replace(c, amount=c.amount + 1000)
                   for c in (january, fy)}
        (b,) = controls.totals(with_cells(base, changed)).breaks
        held, computed = january.amount + 1000, january.amount
        assert (b.address, b.kind, b.account, b.period) == ("C28", "subtotal", "GP", "2026-01")
        assert b.message == (
            f'The 2026-01 figure of "Gross profit" in block "Budget_ES" is {held:,} as the file '
            f'holds it; recomputed from its accounts on Lists it is {computed:,}; the difference '
            'is 1,000.')
        assert dict(b.evidence)["accounts on Lists"] == "+4000 +4100 -5000 -5100 -5200"

    def test_ebit_is_recomputed_from_every_signed_account_above_it(self, controls):
        base = extraction(CLEAN)
        (rent,) = [c for c in cells_of(base, "Forecast_AT", "Rent and facilities")
                   if c.period == "2026-04"]
        (fy,) = [c for c in cells_of(base, "Forecast_AT", "Rent and facilities")
                 if c.period == "FY2026"]
        changed = {(c.sheet, c.address): dataclasses.replace(c, amount=c.amount + 5)
                   for c in (rent, fy)}
        found = controls.totals(with_cells(base, changed)).breaks
        assert [(b.account, b.period, dict(b.evidence)["difference"]) for b in found] == [
            ("EBIT", "2026-04", "5")]

    def test_the_control_tab_is_not_read(self, controls):
        base = extraction(CLEAN)
        wrong = tuple(dataclasses.replace(f, amount=Decimal(1)) if f.sheet == "Control" else f
                      for f in base.formulas)
        assert controls.totals(dataclasses.replace(base, formulas=wrong)).breaks == ()


# -- Hidden content, names as data, and one break per cell ---------------------------------------

class TestHiddenContentAndNames:
    def test_a_hidden_column_that_crosses_a_block_is_high(self, controls):
        from src.extract import HiddenLine
        base = extraction(CLEAN)
        sheets = tuple(dataclasses.replace(s, hidden_columns=(HiddenLine(
            "Plan_ES", "column", "E", ("E6", "E7"), ("Actual_ES", "Budget_ES", "Forecast_ES")),))
            if s.name == "Plan_ES" else s for s in base.sheets)
        (b,) = controls.hidden_content(dataclasses.replace(base, sheets=sheets)).breaks
        assert (b.kind, b.severity, b.sheet, b.block, b.address, b.message) == (
            "hidden_column", "high", "Plan_ES", "Actual_ES", "E:E",
            'Column E on "Plan_ES" is hidden.')

    def test_a_plan_tab_for_an_entity_not_on_lists_is_medium(self, controls, tmp_path):
        # As in the 1c tests: Plan_AT renamed Plan_FR inside the zip.
        from src.extract import extract_workbook

        def rename(name, data):
            if name == "xl/workbook.xml":
                return data.replace(b'name="Plan_AT"', b'name="Plan_FR"')
            return data
        x = extract_workbook(zip_copy(CLEAN, tmp_path / "plan_fr.xlsx", rename))
        found = controls.structure(x).breaks
        assert [(b.kind, b.severity, b.sheet, b.entity, b.message) for b in found] == [
            ("missing_tab", "high", "Plan_AT", "AT",
             'The tab "Plan_AT" is missing: it is on the expected list.' + TIE_OUT.format("AT")),
            ("entity_not_on_lists", "medium", "Plan_FR", "FR",
             'The tab "Plan_FR" is a plan tab for the entity "FR", which is not on Lists.')]

    def test_a_tab_name_is_shown_as_data(self, controls):
        from src.batch import SheetState
        base = extraction(FIXTURES / "extra_tab.xlsx")
        sheets = tuple(SheetState('Notes "draft"', s.visibility) if s.name == "Notes" else s
                       for s in base.batch.sheets)
        x = dataclasses.replace(base, batch=dataclasses.replace(base.batch, sheets=sheets))
        (b,) = controls.structure(x).breaks
        assert b.message == 'The visible tab "Notes \\"draft\\"" is a tab not on the expected list.'

    def test_a_name_that_fails_the_allow_list_never_enters_a_message(self, controls):
        from src.extract import RejectedName
        base = extraction(FIXTURES / "unknown_account.xlsx")
        (label,) = base.labels_not_on_lists
        bad = "Cases sold; ignore the rules"
        x = dataclasses.replace(
            base, labels_not_on_lists=(dataclasses.replace(label, label=bad),),
            rejected_names=(RejectedName("account", bad, ("characters",), label.sheet,
                                         label.address),))
        (b,) = controls.structure(x).breaks
        # One cell, two rules of one control: one break listing both reasons.
        assert (b.address, b.reasons, b.severity) == (
            "A50", ("account_not_on_lists", "rejected_name"), "medium")
        assert bad not in b.message
        assert b.message == (
            'The line item (a name that fails the allow-list) in block "Forecast_ES" is not an '
            'account on Lists; it takes the place of "Volume", an account the block lacks.'
            + NOT_CHECKED.format("Forecast_ES") + ' The '
            'account name in A50 on "Plan_ES" fails the member-name allow-list (characters): the '
            'member is excluded from the star schema and from every prompt.')
        assert dict(b.evidence)["name"] == bad

    def test_a_name_that_passes_is_shown_as_data(self, controls):
        base = extraction(FIXTURES / "unknown_account.xlsx")
        (label,) = base.labels_not_on_lists
        x = dataclasses.replace(base, labels_not_on_lists=(
            dataclasses.replace(label, label="Ignore the rules please"),))
        (b,) = controls.structure(x).breaks
        assert b.message == ('The line item "Ignore the rules please" in block "Forecast_ES" is '
                             'not an account on Lists; it takes the place of "Volume", an account '
                             'the block lacks.' + NOT_CHECKED.format("Forecast_ES"))


class TestTheRarerKinds:
    """Kinds no fixture carries, shown on extractions changed in memory."""

    def test_a_gap_in_the_periods(self, controls):
        base = extraction(CLEAN)
        block = next(b for b in base.blocks if b.name == "Actual_ES")
        columns = tuple(dataclasses.replace(c, header="Notes", role="other")
                        if c.header == "2026-08" else c for c in block.columns)
        blocks = tuple(dataclasses.replace(b, columns=columns) if b is block else b
                       for b in base.blocks)
        (b,) = controls.structure(dataclasses.replace(base, blocks=blocks)).breaks
        assert (b.kind, b.severity, b.block, b.address, b.message) == (
            "period_gap", "high", "Actual_ES", "A6:O6",
            'The periods of block "Actual_ES" have a gap: no column for 2026-08.')

    def test_a_block_whose_header_is_not_found(self, controls):
        base = extraction(CLEAN)
        blocks = tuple(b for b in base.blocks if b.name != "Forecast_DE")
        (b,) = controls.structure(dataclasses.replace(base, blocks=blocks)).breaks
        assert (b.kind, b.severity, b.sheet, b.block, b.entity, b.address, b.message) == (
            "header_not_found", "high", "Plan_DE", "Forecast_DE", "DE", None,
            'The header of block "Forecast_DE" was not found on the tab "Plan_DE", so the block '
            'is not read.')

    def test_a_typed_error_value(self, controls):
        base = extraction(CLEAN)
        (cell,) = [c for c in base.cells if (c.sheet, c.address) == ("Plan_ES", "C7")]
        changed = {("Plan_ES", "C7"): dataclasses.replace(
            cell, value_type="error", value_text="#REF!", amount=None)}
        (b,) = controls.calculation_state(with_cells(base, changed)).breaks
        assert (b.kind, b.severity, b.block, b.account, b.period, b.message) == (
            "error_value", "high", "Actual_ES", "4000", "2026-01",
            'The cell C7 on "Plan_ES" holds the error value #REF!.')


# -- A long-format source -----------------------------------------------------------------------

class TestALongFormatSource:
    def test_the_formula_controls_are_not_run_never_passed(self, controls):
        from src.long_format import read_long_format
        x = read_long_format(READERS / "long_format.csv", entities=ENTITIES, accounts=ACCOUNTS)
        result = controls.run_controls(x)
        assert [(r.control, r.status, r.reason) for r in result.results] == [
            ("structure", "run", None),
            ("calculation_state", "not run", "not run: the source has no formulas"),
            ("formula_integrity", "not run", "not run: the source has no formulas"),
            ("totals", "not run", "not run: the source has no total cells"),
            ("hidden_content", "not run", "not run: the source has no sheets")]
        assert [r.passed for r in result.results] == [True, False, False, False, False]
        assert result.breaks() == ()

    def test_names_and_codes_are_checked(self, controls):
        from src.long_format import read_long_format
        x = read_long_format(READERS / "long_format_cases.csv", entities=ENTITIES,
                             accounts=ACCOUNTS)
        found = controls.run_controls(x).breaks()
        assert [(b.control, b.kind, b.severity, b.entity, b.account, b.message) for b in found] == [
            ("structure", "rejected_name", "medium", None, None,
             "The version name on line 10 fails the member-name allow-list (characters): the "
             "member is excluded from the star schema and from every prompt."),
            ("structure", "entity_not_on_lists", "medium", "FR", None,
             'The entity "FR" is not on the entities the source was checked against (lines 4).'),
            ("structure", "account_not_on_lists", "medium", None, "9999",
             'The account "9999" is not on the accounts the source was checked against '
             '(lines 5).')]


# -- Facts about the file are never findings ----------------------------------------------------

class TestFactsAreNotFindings:
    def test_a_file_modified_after_the_as_of_date_gives_no_break(self, controls, tmp_path):
        assert extraction(CLEAN).batch.modified.startswith("2026-11-05")
        assert breaks(CLEAN) == ()

        def edit(name, data):
            if name == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:(created|modified)[^>]*>)[^<]*",
                              rb"\g<1>2027-03-01T08:00:00Z", data)
                data = data.replace(b"<dc:creator>Group planning team", b"<dc:creator>Someone else")
            if name == "docProps/app.xml":
                data = re.sub(rb"<Application>[^<]*", b"<Application>Another application/9.9", data)
            return data
        from src.extract import extract_workbook
        x = extract_workbook(zip_copy(CLEAN, tmp_path / "late.xlsx", edit))
        assert (x.batch.modified, x.batch.creator, x.batch.application) == (
            "2027-03-01T08:00:00Z", "Someone else", "Another application/9.9")
        assert x.batch.calculation == "as received"
        assert controls.run_controls(x).breaks() == ()

    def test_the_controls_never_read_the_files_own_facts(self):
        source = CONTROLS_SOURCE.read_text(encoding="utf-8")
        assert not re.search(r"\.(created|modified|creator|last_modified_by|application|"
                             r"calculation)\b", source)
        assert not re.search(r"as_of|AS_OF|2026-06-30|sample_model|manifest", source)


# -- The docs --------------------------------------------------------------------------------------

class TestTheDocs:
    def text(self) -> str:
        return " ".join(CONTROLS_DOC.read_text(encoding="utf-8").split())

    def test_each_control_has_a_section(self):
        doc = CONTROLS_DOC.read_text(encoding="utf-8")
        for title in ("Structure", "Calculation state", "Formula integrity", "Totals",
                      "Hidden content and links"):
            assert f"\n### {title}\n" in doc, title

    def test_every_kind_is_stated_with_its_severity(self):
        doc = self.text()
        for kind, severity in SEVERITY.items():
            assert re.search(rf"\| `{kind}` \| {severity} \|", doc), kind

    def test_the_formula_row_rule_is_the_reviewers(self):
        assert ("A row is a formula row when, among its month cells that hold something (a "
                "formula or a typed value), at least half are formulas. Empty months are not "
                "counted, and a tie counts as a formula row. A typed constant in a formula row "
                "is a break, in Actual, Budget and Forecast. The FY column follows the same rule "
                "by column. In addition, a row whose account is a Subtotal on Lists is a formula "
                "row in every block whatever the counts, so a subtotal typed across a whole row "
                "is still found.") in self.text()

    def test_the_limits_sentence_verbatim(self):
        assert LIMITS in self.text()

    def test_the_published_figures_are_the_reason_a_person_reviews(self):
        doc = self.text()
        for phrase in ("mean precision of 64.1%", "mean recall of 62.1%", "70 workbooks",
                       "ExceLint", "OOPSLA 2018", "for review"):
            assert phrase in doc, phrase

    def test_no_detection_rate_beyond_the_planted_set(self):
        doc = self.text()
        assert sorted(re.findall(r"\d+(?:\.\d+)?\s?%", doc)) == ["62.1%", "64.1%"]
        assert "planted" in doc

    def test_the_fast_rules_are_related_and_carry_no_number(self):
        doc = self.text()
        assert "related to" in doc and "FAST" in doc
        # The reviewer's words: rule numbers are added once the current published
        # version has been consulted.
        assert ("rule numbers are added once the current published version of the standard "
                "has been consulted") in doc
        assert "checked against the current version" not in doc
        assert not re.search(r"FAST\W{0,3}(rule\s*)?\d", doc)

    def test_the_build_time_is_the_measured_one(self):
        note = " ".join(BUILD_NOTE.read_text(encoding="utf-8").split())
        assert "about 40 seconds" not in note
        for phrase in ("73.8 seconds", "27 workbooks", "33.7 seconds", "30.8 seconds"):
            assert phrase in note, phrase


# == Part 1e ======================================================================================
# The reviewer's verdict of 5 November, section 5: items 1, 2, 3, 6 and 7(b).

LEDGER = ROOT / "data" / "erp_trial_balance.csv"


def with_blocks(base, change):
    """The extraction with each block replaced by change(block), in memory."""
    return dataclasses.replace(base, blocks=tuple(change(b) for b in base.blocks))


def with_headings(base, change):
    """The extraction with each tab heading replaced by change(heading), in memory."""
    return dataclasses.replace(base, headings=tuple(change(h) for h in base.headings))


def edit_name(source: Path, target: Path, name: str, refers_to: str) -> Path:
    """A copy of a workbook with one defined name pointing elsewhere, edited inside the zip."""
    def edit(part, data):
        if part == "xl/workbook.xml":
            pattern = rf'(<definedName[^>]*name="{name}"[^>]*>)[^<]*'.encode()
            data, count = re.subn(pattern, lambda m: m[1] + refers_to.encode(), data)
            assert count == 1, name
        return data
    return zip_copy(source, target, edit)


def start_up(tmp_path, workbook: Path):
    """The whole start-up build of one workbook against the committed ledger."""
    from src import startup
    return startup.build_warehouse(tmp_path / "warehouse", workbook=workbook, ledger=LEDGER)


def promote_all(built):
    """A connection to a built warehouse with every break of its pair acknowledged and the
    pair promoted (part 2a); the caller closes it."""
    from datetime import datetime, timezone

    from src import promotion, warehouse
    noon = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
    con = warehouse.connect(built.path)
    for break_id in promotion.unacknowledged(con, built.pair):
        promotion.acknowledge(con, built.pair, break_id, "A. Reviewer", "accepted",
                              clock=lambda: noon)
    promotion.promote(con, built.pair, "P. Promoter", clock=lambda: noon)
    return con


class TestBlockCompleteness:
    """Item 1: every block holds every account on Lists."""

    def test_block_short_gives_one_break_naming_the_last_two_accounts(self):
        (b,) = breaks(FIXTURES / "block_short.xlsx")
        assert (b.control, b.kind, b.severity, b.sheet, b.block, b.entity) == (
            "structure", "accounts_not_in_block", "medium", "Plan_ES", "Budget_ES", "ES")
        assert dict(b.evidence) == {"accounts not in the block": "VOL Volume, HC Headcount",
                                    "lines in the block not on Lists": "0"}
        text = json.dumps(["structure", "accounts", "Budget_ES"], separators=(",", ":"))
        assert b.break_id == "ST-" + hashlib.sha256(text.encode()).hexdigest()[:12]

    def test_a_renamed_line_names_the_account_it_displaced(self):
        # unknown_account: Volume relabelled Cases sold. One line not on Lists,
        # one account missing: they pair, so the account-not-on-Lists break is
        # the fault's one, and it names Volume; its id, kind and severity stay.
        (b,) = breaks(FIXTURES / "unknown_account.xlsx")
        assert (b.kind, b.severity, b.break_id, b.address) == (
            "account_not_on_lists", "medium", "ST-aa0af6e33957", "A50")
        assert dict(b.evidence) == {"line item": "Cases sold", "account it displaced": "VOL Volume"}

    def test_one_renamed_line_and_a_block_two_rows_short(self, controls):
        # The reviewer's case: block_short (Budget_ES lacks Volume and
        # Headcount) with Marketing relabelled. The renamed line pairs with the
        # first missing account in Lists order, Marketing; the other two are
        # still reported, in one break that lists them alone.
        base = extraction(FIXTURES / "block_short.xlsx")

        def relabel(b):
            if b.name != "Budget_ES":
                return b
            return dataclasses.replace(b, lines=tuple(
                dataclasses.replace(l, label="Brand spend", account=None)
                if l.account == "6100" else l for l in b.lines))
        (row,) = [l.row for b in base.blocks if b.name == "Budget_ES" for l in b.lines
                  if l.account == "6100"]
        from src.extract import LabelFact
        x = dataclasses.replace(with_blocks(base, relabel), labels_not_on_lists=(
            LabelFact("Plan_ES", "Budget_ES", f"A{row}", "Brand spend"),))
        found = controls.structure(x).breaks
        # Part 2a2: Volume and Headcount feed no subtotal, so the accounts break names
        # none; the line not on Lists still leaves every subtotal of the block unchecked.
        assert [(b.kind, b.message) for b in found] == [
            ("accounts_not_in_block",
             'The block "Budget_ES" does not hold 2 of the accounts on Lists: "Volume", '
             '"Headcount".'),
            ("account_not_on_lists",
             'The line item "Brand spend" in block "Budget_ES" is not an account on Lists; it '
             'takes the place of "Marketing", an account the block lacks.'
             + NOT_CHECKED.format("Budget_ES"))]
        assert dict(found[0].evidence) == {
            "accounts not in the block": "VOL Volume, HC Headcount",
            "lines in the block not on Lists": "1",
            "missing accounts paired with lines not on Lists": "6100 Marketing"}
        assert dict(found[1].evidence)["account it displaced"] == "6100 Marketing"

    def test_a_block_renamed_and_short_reports_the_account_left_unpaired(self, controls):
        base = extraction(FIXTURES / "unknown_account.xlsx")

        def drop_headcount(b):
            if b.name != "Forecast_ES":
                return b
            return dataclasses.replace(b, lines=tuple(l for l in b.lines if l.account != "HC"))
        found = controls.structure(with_blocks(base, drop_headcount)).breaks
        assert [(b.kind, b.block) for b in found] == [
            ("accounts_not_in_block", "Forecast_ES"), ("account_not_on_lists", "Forecast_ES")]
        assert found[0].message == ('The block "Forecast_ES" does not hold 1 of the accounts on '
                                    'Lists: "Headcount".')       # feeds no subtotal (2a2)
        assert "Volume" in found[1].message

    def test_a_line_relabelled_as_another_account_is_the_account_twice(self, controls):
        # Co-packing income relabelled Product sales. At 1e no line was off
        # Lists, so the missing account could not pass as a renamed one and
        # the block gave the accounts break. Part 2a: the label is now on two
        # lines, and the later one is an extra that pairs with the missing
        # account, so the fault is one account-twice break naming it.
        base = extraction(CLEAN)

        def relabel(b):
            if b.name != "Budget_AT":
                return b
            return dataclasses.replace(b, lines=tuple(
                dataclasses.replace(l, label="Product sales", account="4000")
                if l.account == "4100" else l for l in b.lines))
        (b,) = controls.structure(with_blocks(base, relabel)).breaks
        assert (b.kind, b.block, b.account, b.address) == (
            "account_twice_in_block", "Budget_AT", "4000", "A24")
        assert b.message == ('The line item "Product sales" appears twice in block "Budget_AT", '
                             'on rows 23 and 24; the one on row 24 takes the place of '
                             '"Co-packing income", an account the block lacks.'
                             + NOT_CHECKED.format("Budget_AT"))

    def test_every_committed_block_holds_every_account(self):
        for path in (CLEAN, SAMPLE, FIXTURES / "shifted.xlsx", FIXTURES / "names.xlsx"):
            assert not [b for b in breaks(path) if b.kind == "accounts_not_in_block"]


class TestAnchorConsistency:
    """Item 2: a block's defined name against the sheet, and blocks that cannot be read."""

    @pytest.mark.parametrize("name, address", [("name_shifted", "A23:O36"),
                                               ("name_shifted_up", "A21:O34")])
    def test_a_name_one_row_out_is_one_high_break_and_no_flood(self, name, address):
        # One row down or one row up: one break, never a no-unit or
        # header-not-found flood (one row up gave 28 breaks at afdd35e).
        found = breaks(FIXTURES / f"{name}.xlsx")
        assert [(b.kind, b.reasons, b.severity, b.address) for b in found] == [
            ("block_unreadable", ("block_unreadable",), "high", address)]
        # Part 2a2: one row down, the name leaves the Budget header in A22 unread, and
        # the break names it in its evidence; one row up, A22 is the name's header row.
        unread = {"headers not read": "A22"} if name == "name_shifted" else {}
        assert dict(found[0].evidence) == {
            "why it cannot be read": 'no "Line item" header, no month header', **unread}
        text = json.dumps(["structure", "anchor", "Budget_ES"], separators=(",", ":"))
        assert found[0].break_id == "ST-" + hashlib.sha256(text.encode()).hexdigest()[:12]

    def test_the_version_label_is_found_through_the_header_row(self):
        # name_shifted_up: the cell above the name's first row (A20) is empty,
        # but the header row is found by its labels in the range (row 22) and
        # the label above it reads Budget, as the name does: no disagreement.
        x = extraction(FIXTURES / "name_shifted_up.xlsx")
        (block,) = [b for b in x.blocks if b.name == "Budget_ES"]
        assert (block.header_row, block.version_label_address, block.version_label) == (
            22, "A21", "Budget")
        # name_shifted: no header row in the range, so no label to compare.
        (block,) = [b for b in extraction(FIXTURES / "name_shifted.xlsx").blocks
                    if b.name == "Budget_ES"]
        assert (block.header_row, block.version_label_address, block.version_label) == (
            None, None, None)

    def test_a_name_that_disagrees_and_cannot_be_read_is_one_break_with_both_reasons(
            self, controls, tmp_path):
        # The Forecast name one row up on the Budget rows: the header row in
        # its range is under the label Budget, and its first row is a title.
        from src.extract import extract_workbook
        path = edit_name(FIXTURES / "extra_tab.xlsx", tmp_path / "both.xlsx", "Forecast_ES",
                         "Plan_ES!$A$21:$O$34")
        found = controls.structure(extract_workbook(path)).breaks
        (b,) = [k for k in found if k.block == "Forecast_ES"]
        assert (b.kind, b.reasons, b.severity) == (
            "anchor_disagreement", ("anchor_disagreement", "block_unreadable"), "high")
        assert b.message == (
            'The defined name "Forecast_ES" names version "Forecast", but the label above its '
            'header row, in A21 on "Plan_ES", reads "Budget". The block "Forecast_ES" on '
            '"Plan_ES" cannot be read: the first row of its range, A21:O21, has no "Line item" '
            'header and no month header. Its range overlaps the block "Budget_ES" (A22:O35), '
            'whose cells are read as that block\'s alone; nothing is built for "Forecast_ES".')
        # Part 2a2: the sentence after the reasons is new, and the evidence names the
        # Forecast header in A38, which the name leaves unread.
        assert dict(b.evidence)["headers not read"] == "A38"
        assert [k.kind for k in found] == ["tab_not_on_list", "anchor_disagreement"]

    def test_name_shifted_lands_nothing_from_the_block(self):
        x = extraction(FIXTURES / "name_shifted.xlsx")
        assert cells_of(x, "Budget_ES") == []
        assert [r.control for r in report(FIXTURES / "name_shifted.xlsx").results
                if r.breaks] == ["structure"]

    def test_version_label_changed_reads_its_rows_once_and_promotes_three_versions(
            self, tmp_path):
        # Changed on purpose in part 2a (the 1e verdict, F12). At 1e this test
        # pinned the Budget rows read twice: through the name Budget_ES, and by
        # their labels as a block called Plan B, which put a fourth version and
        # the same 156 rows into the star. Now a block found by its labels whose
        # range overlaps a block read through a defined name is not read, and
        # the star admits Actual, Budget and Forecast alone. The one high break
        # holds promotion; once it and the pair's reconcile breaks are
        # acknowledged, the promoted star has three versions, a unique grain,
        # and the ES Budget rows once: 13 lines times 12 months.
        path = FIXTURES / "version_label_changed.xlsx"
        x = extraction(path)
        assert [(b.name, b.found_by, b.ref) for b in x.blocks if b.ref == "A22:O35"] == [
            ("Budget_ES", "defined name", "A22:O35")]
        assert [(n.sheet, n.version, n.ref, n.overlaps) for n in x.blocks_not_read] == [
            ("Plan_ES", "Plan B", "A22:O35", ("Budget_ES",))]
        (b,) = breaks(path)
        assert dict(b.evidence)["label above the header row"] == "A21: Plan B"
        built = start_up(tmp_path, path)
        con = promote_all(built)
        try:
            assert con.execute("SELECT version FROM dim_version ORDER BY version_order"
                               ).fetchall() == [("Actual",), ("Budget",), ("Forecast",)]
            assert con.execute("SELECT version, entity, account, period FROM fact_plan "
                               "GROUP BY ALL HAVING count(*) > 1").fetchall() == []
            assert con.execute("SELECT count(*) FROM fact_plan WHERE version = 'Budget' AND "
                               "entity = 'ES'").fetchone() == (156,)
            assert con.execute("SELECT DISTINCT version FROM fact_plan ORDER BY ALL"
                               ).fetchall() == [("Actual",), ("Budget",), ("Forecast",)]
        finally:
            con.close()

    def test_name_other_tab_and_entity_changed_give_their_break_alone_in_the_whole_build(
            self, tmp_path):
        for name in ("name_other_tab", "entity_changed"):
            built = start_up(tmp_path / name, FIXTURES / f"{name}.xlsx")
            assert [short(b) for b in built.breaks] == FIXTURE_BREAKS[name], name

    def test_entity_changed_lands_what_the_formulas_give(self):
        # Plan_ES's Budget and Forecast blocks compute AT's figures; the lean:
        # what lands is unchanged, and the high break holds promotion.
        x = extraction(FIXTURES / "entity_changed.xlsx")
        for version in ("Budget", "Forecast"):
            es = [c.amount for c in cells_of(x, f"{version}_ES")]
            at = [c.amount for c in cells_of(x, f"{version}_AT")]
            assert es == at

    def test_name_wrong_version_names_both(self):
        # Changed on purpose in part 2a2: the two names on one range are one overlap
        # break that carries the name's disagreement with the label (it was one
        # anchor_disagreement, ST-4d69025f5d6d, at 1e and 2a).
        (b,) = breaks(FIXTURES / "name_wrong_version.xlsx")
        assert (b.kind, b.reasons, b.severity, b.block, b.entity) == (
            "blocks_overlap", ("blocks_overlap", "anchor_disagreement"), "high", "Budget_ES",
            "ES")
        assert "Forecast_ES" in b.message and '"Budget"' in b.message
        assert dict(b.evidence)["label above the header row"] == "A21: Budget"
        assert b.break_id == the_id("overlap", "Plan_ES", ["Budget_ES", "Forecast_ES"])

    def test_what_lands_for_a_block_whose_anchor_disagrees_is_unchanged(self):
        # The lean: the extractor reads the name as it is; the high break holds
        # promotion. Forecast_ES is read from the Budget rows, as the name says.
        x = extraction(FIXTURES / "name_wrong_version.xlsx")
        forecast = cells_of(x, "Forecast_ES")
        budget = cells_of(x, "Budget_ES")
        assert [c.address for c in forecast] == [c.address for c in budget]
        assert [c.amount for c in forecast] == [c.amount for c in budget]
        assert {c.version for c in forecast} == {"Forecast"}
        # Part 2a2: both blocks land raw, every cell flagged as in an overlap, never built.
        assert {c.overlap for c in forecast + budget} == {True}
        assert not [c for c in x.cells if c.overlap and c.block not in ("Budget_ES",
                                                                       "Forecast_ES")]

    def test_a_block_name_pointing_into_another_tab_is_one_high_break(self, controls):
        x = extraction(FIXTURES / "name_other_tab.xlsx")
        (b,) = controls.run_controls(x).breaks()
        assert (b.control, b.kind, b.severity, b.sheet, b.block, b.entity) == (
            "structure", "anchor_disagreement", "high", "Plan_AT", "Budget_AT", "AT")
        assert b.message == ('The defined name "Budget_AT" has the form of a block on "Plan_AT" '
                             'but is not used, because it refers to "Plan_ES!$A$22:$O$35".')
        # What lands is unchanged: the block is read by its labels, as before.
        (block,) = [k for k in x.blocks if k.name == "Budget_AT"]
        assert (block.found_by, block.ref) == ("header labels", "A22:O35")
        assert [(c.address, c.amount) for c in cells_of(x, "Budget_AT")] == [
            (c.address, c.amount) for c in cells_of(extraction(CLEAN), "Budget_AT")]

    def test_a_block_name_with_two_ranges_is_one_high_break(self, controls, tmp_path):
        from src.extract import extract_workbook
        path = edit_name(CLEAN, tmp_path / "two.xlsx", "Budget_AT",
                         "Plan_AT!$A$22:$O$35,Plan_AT!$A$38:$O$51")
        (b,) = controls.run_controls(extract_workbook(path)).breaks()
        assert (b.kind, b.severity) == ("anchor_disagreement", "high")
        assert b.message.endswith('because it refers to "Plan_AT!$A$22:$O$35", '
                                  '"Plan_AT!$A$38:$O$51".')

    def test_a_block_name_elsewhere_and_no_block_found_is_one_break_with_both_reasons(
            self, controls, tmp_path):
        # One fault: the name points away and the tab's own block is not found
        # (here its labels are taken out in memory), so one break, as D4 is.
        from src.extract import extract_workbook
        path = edit_name(CLEAN, tmp_path / "elsewhere.xlsx", "Budget_AT", "Plan_ES!$A$22:$O$35")
        base = extract_workbook(path)
        x = dataclasses.replace(base, blocks=tuple(b for b in base.blocks
                                                   if b.name != "Budget_AT"))
        (b,) = controls.structure(x).breaks
        assert (b.kind, b.reasons, b.severity, b.sheet, b.block) == (
            "anchor_disagreement", ("anchor_disagreement", "header_not_found"), "high", "Plan_AT",
            "Budget_AT")
        assert dict(b.evidence)["the block is read"] == "not at all"
        text = json.dumps(["structure", "block", "Plan_AT", "Budget"], separators=(",", ":"))
        assert b.break_id == "ST-" + hashlib.sha256(text.encode()).hexdigest()[:12]

    def test_an_entity_cell_that_disagrees_is_one_break_for_the_tab(self, controls):
        base = extraction(CLEAN)
        x = with_headings(base, lambda h: dataclasses.replace(h, value="AT") if (
            h.sheet, h.label) == ("Plan_ES", "Entity") else h)
        (b,) = controls.structure(x).breaks
        assert (b.kind, b.severity, b.sheet, b.entity, b.address) == (
            "anchor_disagreement", "high", "Plan_ES", "ES", "B2")
        assert b.message == ('The Entity cell of "Plan_ES", B2, reads "AT", but the defined '
                             'names of its blocks, "Actual_ES", "Budget_ES", "Forecast_ES", say '
                             '"ES".')

    def test_a_block_with_no_month_header_is_one_break_and_nothing_else(self, controls):
        base = extraction(CLEAN)

        def no_months(b):
            if b.name != "Budget_DE":
                return b
            return dataclasses.replace(b, columns=tuple(
                dataclasses.replace(c, role="other") if c.role == "month" else c
                for c in b.columns))
        (b,) = controls.structure(with_blocks(base, no_months)).breaks
        assert (b.kind, b.reasons, b.severity, b.block, b.address) == (
            "block_unreadable", ("block_unreadable",), "high", "Budget_DE", "A22:O35")
        assert b.message == ('The block "Budget_DE" on "Plan_DE" cannot be read: the first row '
                             'of its range, A22:O22, has no month header.')

    def test_every_committed_block_agrees_with_its_anchors(self):
        for path in (CLEAN, SAMPLE, FIXTURES / "shifted.xlsx", *sorted(MUTATIONS.glob("*.xlsx"))):
            assert not [b for b in breaks(path) if b.kind in (
                "anchor_disagreement", "block_unreadable", "heading_not_found")], path.name


class TestTheCurrency:
    """Item 3: each plan tab's Currency cell, found by its label, against the money accounts."""

    def test_currency_changed_gives_one_break(self):
        (b,) = breaks(FIXTURES / "currency_changed.xlsx")
        assert dict(b.evidence) == {
            "Currency cell": "B3", "the tab's currency": "USD",
            "unit of the money accounts on Lists": "EUR",
            "money accounts": "every account on Lists outside the Statistics section"}

    def test_currency_changed_gives_that_break_alone_in_the_whole_start_up_build(self, tmp_path):
        built = start_up(tmp_path, FIXTURES / "currency_changed.xlsx")
        assert [short(b) for b in built.breaks] == FIXTURE_BREAKS["currency_changed"]

    def test_the_heading_is_found_by_its_label_wherever_it_is(self):
        # shifted: the labels in C5 and C6, the cells in D5 and D6; no break.
        assert breaks(FIXTURES / "shifted.xlsx") == ()

    def test_an_empty_currency_cell(self, controls):
        base = extraction(FIXTURES / "extra_tab.xlsx")
        x = with_headings(base, lambda h: dataclasses.replace(h, value=None)
                          if h.label == "Currency" else h)
        found = [b for b in controls.structure(x).breaks if b.kind == "currency_disagreement"]
        assert [b.message for b in found] == [
            'The Currency cell of "Plan_ES", B3, reads nothing, but the money accounts on Lists '
            'are in "EUR".']

    def test_a_tab_with_no_currency_label(self, controls):
        base = extraction(FIXTURES / "typed_subtotal.xlsx")
        x = with_headings(base, lambda h: dataclasses.replace(
            h, label_address=None, address=None, value=None) if h.label == "Currency" else h)
        found = [b for b in controls.structure(x).breaks if b.control == "structure"]
        assert [(b.kind, b.severity, b.sheet, b.message) for b in found] == [
            ("heading_not_found", "high", "Plan_ES",
             'The tab "Plan_ES" has no cell labelled "Currency", so the tab\'s currency is not '
             'checked.')]

    def test_a_tab_with_neither_label_is_one_break(self, controls):
        base = extraction(CLEAN)
        x = with_headings(base, lambda h: dataclasses.replace(
            h, label_address=None, address=None, value=None) if h.sheet == "Plan_DE" else h)
        (b,) = controls.structure(x).breaks
        assert (b.kind, b.sheet, b.message) == (
            "heading_not_found", "Plan_DE",
            'The tab "Plan_DE" has no cell labelled "Entity" or "Currency", so the tab\'s entity '
            'and currency are not checked.')

    def test_money_accounts_with_two_units_are_one_lists_break(self, controls):
        base = extraction(FIXTURES / "currency_changed.xlsx")
        accounts = tuple(dataclasses.replace(a, unit="USD") if a.code == "6300" else a
                         for a in base.lists.accounts)
        x = dataclasses.replace(base, lists=dataclasses.replace(base.lists, accounts=accounts))
        (b,) = controls.structure(x).breaks
        assert (b.kind, b.severity, b.sheet) == ("money_units_differ", "high", "Lists")
        assert b.message == ('The money accounts on Lists do not share one unit ("EUR", "USD"), '
                             'so no plan tab\'s currency is checked.')


class TestHiddenRuns:
    """Item 7(b): a run of hidden rows or columns is one break; a hidden tab's rows are its own."""

    @staticmethod
    def hide(base, sheet, rows=(), columns=(), visibility=None):
        from openpyxl.utils import column_index_from_string, range_boundaries

        from src.extract import HiddenLine
        blocks = [(b.name, range_boundaries(b.ref)) for b in base.blocks if b.sheet == sheet]

        def crossing(axis, index):
            at = int(index) if axis == "row" else column_index_from_string(index)
            return tuple(name for name, (first, top, last, bottom) in blocks
                         if ((top <= at <= bottom) if axis == "row" else (first <= at <= last)))
        hidden = {"row": tuple(HiddenLine(sheet, "row", str(r), (), crossing("row", str(r)))
                               for r in rows),
                  "column": tuple(HiddenLine(sheet, "column", c, (), crossing("column", c))
                                  for c in columns)}
        sheets = tuple(dataclasses.replace(
            s, hidden_rows=hidden["row"], hidden_columns=hidden["column"],
            visibility=visibility or s.visibility) if s.name == sheet else s
            for s in base.sheets)
        return dataclasses.replace(base, sheets=sheets)

    def test_a_single_hidden_row_is_todays_break_byte_for_byte(self):
        (d3,) = [b for b in breaks(SAMPLE) if b.kind == "hidden_row"]
        assert d3.break_id == "HC-b04c76a19ed4"
        assert located(d3) == D3
        assert d3.evidence == (("cells with content", "A53, B53, L53"),
                               ("blocks it crosses", "none"))

    def test_a_run_of_rows_is_one_break_with_its_range(self, controls):
        x = self.hide(extraction(CLEAN), "Plan_DE", rows=(53, 54, 55))
        (b,) = controls.hidden_content(x).breaks
        assert (b.kind, b.reasons, b.severity, b.sheet, b.block, b.address, b.message) == (
            "hidden_row", ("hidden_row",), "medium", "Plan_DE", None, "53:55",
            'Rows 53 to 55 on "Plan_DE" are hidden.')
        assert dict(b.evidence) == {"hidden range": "53:55", "hidden rows": "3",
                                    "cells with content": "none", "blocks it crosses": "none"}
        text = json.dumps(["hidden_content", "row", "Plan_DE", "53:55"], separators=(",", ":"))
        assert b.break_id == "HC-" + hashlib.sha256(text.encode()).hexdigest()[:12]

    def test_a_run_that_crosses_a_block_is_high(self, controls):
        x = self.hide(extraction(CLEAN), "Plan_ES", rows=(34, 35, 36))
        (b,) = controls.hidden_content(x).breaks
        assert (b.severity, b.block, b.address) == ("high", "Budget_ES", "34:36")
        assert dict(b.evidence)["blocks it crosses"] == "Budget_ES"

    def test_runs_are_split_where_a_row_is_shown(self, controls):
        x = self.hide(extraction(CLEAN), "Plan_DE", rows=(60, 61, 63))
        found = controls.hidden_content(x).breaks
        assert [(b.address, b.message) for b in found] == [
            ("60:61", 'Rows 60 to 61 on "Plan_DE" are hidden.'),
            ("63:63", 'Row 63 on "Plan_DE" is hidden.')]

    def test_a_run_of_columns_is_one_break(self, controls):
        x = self.hide(extraction(CLEAN), "Plan_ES", columns=("C", "D", "E"))
        (b,) = controls.hidden_content(x).breaks
        assert (b.kind, b.severity, b.block, b.address, b.message) == (
            "hidden_column", "high", "Actual_ES", "C:E", 'Columns C to E on "Plan_ES" are hidden.')
        assert dict(b.evidence)["hidden columns"] == "3"
        assert dict(b.evidence)["blocks it crosses"] == "Actual_ES, Budget_ES, Forecast_ES"

    def test_thousands_of_hidden_rows_in_one_run_are_one_break(self, controls):
        x = self.hide(extraction(CLEAN), "Plan_AT", rows=range(100, 5100))
        (b,) = controls.hidden_content(x).breaks
        assert (b.address, b.message) == ("100:5099", 'Rows 100 to 5099 on "Plan_AT" are hidden.')

    def test_rows_and_columns_hidden_on_a_hidden_tab_are_not_reported_apart(self, controls):
        base = extraction(MUTATIONS / "clean_plus_d5.xlsx")
        x = self.hide(base, "Scratch", rows=(2, 4, 6, 8), columns=("B", "D"))
        assert [b.kind for b in controls.hidden_content(x).breaks] == ["hidden_sheet"]
        y = self.hide(extraction(CLEAN), "Plan_AT", rows=(53, 60), visibility="hidden")
        assert [(b.kind, b.sheet) for b in controls.hidden_content(y).breaks] == [
            ("hidden_sheet", "Plan_AT")]


class TestWhatTheControlsDoNotSee:
    """Item 6: one known blind spot, pinned."""

    def test_known_blind_spot_consistent_wrong_driver(self, tmp_path):
        """Pins a known blind spot: a whole row fetching the wrong driver in all twelve months.

        The DE Budget Rent and facilities line of wrong_driver_row fetches the IT services
        driver in every month. Every month holds the same formula, so no cell differs from
        the pattern of its row; every total is computed from the row, so every total
        agrees; and reconcile reads Actual only. The whole start-up build against the
        committed ledger gives no break. docs/controls.md says so under "What the
        controls do not see". A control that learns to see this must change this test on
        purpose.
        """
        path = FIXTURES / "wrong_driver_row.xlsx"
        x = extraction(path)
        rent = cells_of(x, "Budget_DE", "Rent and facilities", "month")
        it = cells_of(x, "Budget_DE", "IT and software", "month")
        clean = cells_of(extraction(CLEAN), "Budget_DE", "Rent and facilities", "month")
        assert len(rent) == 12 and all('"IT services"' in c.formula for c in rent)
        assert [c.amount for c in rent] == [c.amount for c in it]
        assert all(wrong.amount != right.amount for wrong, right in zip(rent, clean))
        built = start_up(tmp_path, path)
        assert built.breaks == ()


class TestTheDocsOf1e:
    def text(self) -> str:
        return " ".join(CONTROLS_DOC.read_text(encoding="utf-8").split())

    def test_what_the_controls_do_not_see(self):
        doc = CONTROLS_DOC.read_text(encoding="utf-8")
        assert "\n### What the controls do not see\n" in doc
        section = " ".join(doc.split("\n### What the controls do not see\n", 1)[1]
                           .split("\n## ", 1)[0].split())
        for phrase in ("all twelve months", "Assumptions", "by the same amount",
                       "outside the blocks", "test_known_blind_spot_consistent_wrong_driver"):
            assert phrase in section, phrase

    def test_a_bad_name_is_never_repaired_by_the_labels(self):
        assert ("The extractor never falls back to reading a block by its labels to repair a "
                "bad name") in self.text()

    def test_the_pairing_rule_and_the_version_label_are_stated(self):
        text = self.text()
        assert ("the k-th line not on Lists, in row order, pairs with the k-th missing account, "
                "in Lists order") in text
        assert "found through the block's header row" in text

    def test_the_new_kinds_are_described(self):
        text = self.text()
        for phrase in ("accounts not in the block", "Currency cell", "Entity cell",
                       "cannot be read", "a run of neighbouring hidden rows"):
            assert phrase in text, phrase


# == Part 2a ======================================================================================
# The reviewer's Phase 2 brief, section 3 (the 1e verdict, findings F11 to F13): one fault
# gives one break; the star admits three versions and no cell is read twice; the Currency
# cell, the version label and the Entity cell are compared after trimming and ignoring case.

ADMITTED = "Actual, Budget, Forecast"
COMMITTED = sorted([*(ROOT / "data").glob("*.xlsx"), *FIXTURES.glob("*.xlsx"),
                    *MUTATIONS.glob("*.xlsx")])


def with_lines(base, block: str, change):
    """The extraction with one block's lines replaced by change(lines), in memory."""
    return with_blocks(base, lambda b: dataclasses.replace(b, lines=tuple(change(b.lines)))
                       if b.name == block else b)


def relabelled(lines, rows: dict):
    """Lines with the label (and the account it names on Lists) of some rows changed."""
    return [dataclasses.replace(l, label=rows[l.row][0], account=rows[l.row][1])
            if l.row in rows else l for l in lines]


def the_id(*anchors) -> str:
    text = json.dumps(["structure", *anchors], ensure_ascii=False, separators=(",", ":"))
    return "ST-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def cell_of(x, block: str, line: str, period: str):
    (c,) = [c for c in cells_of(x, block, line) if c.period == period]
    return c


class TestOneFaultOneBreak:
    """Item 1: a line-level structure break, and a block without units, are one break."""

    def test_renamed_feeds_subtotal_is_one_break_and_no_subtotal_break(self):
        path = FIXTURES / "renamed_feeds_subtotal.xlsx"
        (b,) = breaks(path)
        assert dict(b.evidence) == {"line item": "Staff costs",
                                    "account it displaced": "6000 Salaries"}
        assert b.break_id == the_id("cell", "Forecast_ES", "Staff costs", "Line item")
        assert report(path).result("totals").breaks == ()

    def test_the_other_blocks_subtotals_and_the_leaf_totals_are_still_checked(self, controls):
        # In the block with the renamed line, neither a subtotal month nor a
        # subtotal's FY cell is examined; a leaf line's FY cell still is, and
        # so is a subtotal of every other block.
        base = extraction(FIXTURES / "renamed_feeds_subtotal.xlsx")
        moved = {}
        for block, line, period, change in (("Budget_ES", "Gross profit", "2026-01", 1000),
                                            ("Budget_ES", "Gross profit", "FY2026", 1000),
                                            ("Forecast_ES", "Marketing", "FY2026", 5),
                                            ("Forecast_ES", "EBIT", "2026-03", 9),
                                            ("Forecast_ES", "EBIT", "FY2026", 7),
                                            ("Forecast_ES", "Gross profit", "2026-04", 3)):
            c = cell_of(base, block, line, period)
            moved[(c.sheet, c.address)] = dataclasses.replace(c, amount=c.amount + change)
        found = controls.totals(with_cells(base, moved)).breaks
        assert [(b.kind, b.block, b.account, b.period) for b in found] == [
            ("fy_total", "Forecast_ES", "6100", "FY2026"),
            ("subtotal", "Budget_ES", "GP", "2026-01")]

    @pytest.mark.parametrize("fault, kind", [
        ("not on Lists", "account_not_on_lists"),
        ("missing", "accounts_not_in_block"),
        ("twice", "account_twice_in_block")])
    def test_each_line_level_break_leaves_its_blocks_subtotals_unchecked(self, controls, fault,
                                                                         kind):
        # Budget_DE, changed in memory: Rent relabelled to a label not on Lists,
        # IT and software dropped, or Rent relabelled IT and software. A wrong
        # EBIT month in the block then gives no subtotal break; the break says so.
        from src.extract import LabelFact
        base = extraction(CLEAN)
        rent = next(l for b in base.blocks if b.name == "Budget_DE" for l in b.lines
                    if l.account == "6200")
        if fault == "not on Lists":
            x = with_lines(base, "Budget_DE",
                           lambda ls: relabelled(ls, {rent.row: ("Premises", None)}))
            x = dataclasses.replace(x, labels_not_on_lists=(
                LabelFact("Plan_DE", "Budget_DE", f"A{rent.row}", "Premises"),))
        elif fault == "missing":
            x = with_lines(base, "Budget_DE", lambda ls: [l for l in ls if l.account != "6300"])
        else:
            x = with_lines(base, "Budget_DE", lambda ls: relabelled(
                ls, {rent.row: ("IT and software", "6300")}))
        ebit = cell_of(x, "Budget_DE", "EBIT", "2026-05")
        x = with_cells(x, {(ebit.sheet, ebit.address): dataclasses.replace(
            ebit, amount=ebit.amount + 1)})
        assert controls.totals(x).breaks == ()
        (b,) = controls.structure(x).breaks
        # Part 2a2, the narrower rule: a label not on Lists still stops every subtotal; a
        # missing IT and software, or Rent relabelled IT and software (both operating
        # expenses), stops EBIT alone.
        clause = NOT_CHECKED if fault == "not on Lists" else EBIT_NOT_CHECKED
        assert b.kind == kind and b.message.endswith(clause.format("Budget_DE"))

    def test_duplicate_account_is_one_break_naming_both_rows_and_the_displaced_account(self):
        path = FIXTURES / "duplicate_account.xlsx"
        (b,) = breaks(path)
        assert (b.control, b.kind, b.severity, b.sheet, b.block, b.entity, b.account,
                b.address) == ("structure", "account_twice_in_block", "medium", "Plan_ES",
                               "Budget_ES", "ES", "6000", "A29")
        assert dict(b.evidence) == {"line item": "Salaries", "cells that carry it": "A25, A29",
                                    "account it displaced": "5000 Materials"}
        assert b.break_id == the_id("cell", "Budget_ES", "Salaries", "Line item")
        assert report(path).result("totals").breaks == ()

    def test_an_extra_line_with_nothing_to_pair_is_still_the_break(self, controls):
        from src.extract import Line
        base = extraction(CLEAN)
        x = with_lines(base, "Budget_ES", lambda ls: [*ls, Line(36, "Salaries", "EUR", "6000")])
        (b,) = controls.structure(x).breaks
        assert (b.kind, b.severity, b.account, b.address) == (
            "account_twice_in_block", "medium", "6000", "A36")
        assert b.message == ('The line item "Salaries" appears twice in block "Budget_ES", on '
                             'rows 29 and 36; the one on row 36 repeats the line on row 29.'
                             + EBIT_NOT_CHECKED.format("Budget_ES"))
        assert dict(b.evidence) == {"line item": "Salaries", "cells that carry it": "A29, A36"}

    def test_extras_pair_with_missing_accounts_one_to_one_in_order(self, controls):
        # Materials and Packaging both relabelled Salaries: the label is on rows
        # 25, 26 and 29. The first is the account's line; the extras, in row
        # order, take the missing accounts in Lists order.
        base = extraction(CLEAN)
        x = with_lines(base, "Budget_ES", lambda ls: relabelled(
            ls, {25: ("Salaries", "6000"), 26: ("Salaries", "6000")}))
        found = controls.structure(x).breaks
        assert [(b.kind, b.address, dict(b.evidence)["account it displaced"]) for b in found] == [
            ("account_twice_in_block", "A26", "5000 Materials"),
            ("account_twice_in_block", "A29", "5100 Packaging")]
        assert found[0].message == (
            'The line item "Salaries" appears 3 times in block "Budget_ES", on rows 25, 26 and '
            '29; the one on row 26 takes the place of "Materials", an account the block lacks.'
            + NOT_CHECKED.format("Budget_ES"))
        assert len({b.break_id for b in found}) == 2

    def test_a_line_not_on_lists_and_an_extra_pair_in_row_order(self, controls):
        # Product sales relabelled Gross sales (row 23) and Packaging relabelled
        # Salaries (row 26, so the Salaries line on row 29 is the extra). The
        # two lines that cannot be placed, in row order, take the two missing
        # accounts in Lists order: Product sales, then Packaging.
        from src.extract import LabelFact
        base = extraction(CLEAN)
        x = with_lines(base, "Budget_ES", lambda ls: relabelled(
            ls, {23: ("Gross sales", None), 26: ("Salaries", "6000")}))
        x = dataclasses.replace(x, labels_not_on_lists=(
            LabelFact("Plan_ES", "Budget_ES", "A23", "Gross sales"),))
        found = sorted(controls.structure(x).breaks, key=lambda b: b.address)
        assert [(b.kind, b.address, dict(b.evidence)["account it displaced"]) for b in found] == [
            ("account_not_on_lists", "A23", "4000 Product sales"),
            ("account_twice_in_block", "A29", "5100 Packaging")]

    def test_unit_column_empty_is_one_break(self):
        (b,) = breaks(FIXTURES / "unit_column_empty.xlsx")
        assert (b.kind, b.severity, b.block, b.entity, b.address) == (
            "block_without_units", "medium", "Budget_ES", "ES", "B23:B35")
        assert dict(b.evidence) == {"Unit header": "B22", "line items with no unit": "13"}
        assert b.break_id == the_id("units", "Budget_ES")

    def test_a_block_whose_unit_header_is_not_found_is_one_break(self, controls):
        base = extraction(CLEAN)

        def no_header(b):
            if b.name != "Budget_DE":
                return b
            return dataclasses.replace(
                b, columns=tuple(dataclasses.replace(c, header="Units", role="other")
                                 if c.role == "unit" else c for c in b.columns),
                lines=tuple(dataclasses.replace(l, unit=None) for l in b.lines))
        (b,) = controls.structure(with_blocks(base, no_header)).breaks
        assert (b.kind, b.severity, b.block, b.address, b.message) == (
            "block_without_units", "medium", "Budget_DE", "A22:O35",
            'No line item in block "Budget_DE" has a unit: the block has no "Unit" header.')
        assert dict(b.evidence) == {"Unit header": "not found", "line items with no unit": "13"}

    def test_a_block_with_some_units_missing_keeps_one_break_per_line(self, controls):
        base = extraction(CLEAN)
        x = with_lines(base, "Budget_DE", lambda ls: [
            dataclasses.replace(l, unit=None) if l.account in ("6000", "VOL") else l for l in ls])
        found = controls.structure(x).breaks
        assert [(b.kind, b.address) for b in found] == [("no_unit", "B29"), ("no_unit", "B34")]

    def test_no_workbook_committed_before_2a_gives_a_new_kind(self):
        new = {"account_twice_in_block", "block_without_units", "version_not_admitted"}
        for path in (CLEAN, SAMPLE, FIXTURES / "shifted.xlsx", FIXTURES / "no_units.xlsx",
                     *sorted(MUTATIONS.glob("*.xlsx"))):
            assert not [b for b in breaks(path) if b.kind in new], path.name


class TestThreeVersions:
    """Item 2: the star admits Actual, Budget and Forecast, and no cell is read twice."""

    def test_extra_version_block_lands_raw_and_is_one_break(self):
        path = FIXTURES / "extra_version_block.xlsx"
        x = extraction(path)
        (block,) = [b for b in x.blocks if b.version not in ("Actual", "Budget", "Forecast")]
        assert (block.name, block.version, block.found_by, block.ref) == (
            "Plan B_ES", "Plan B", "header labels", "A54:O67")
        cells = cells_of(x, "Plan B_ES")
        assert len(cells) == 13 * 13 and {c.amount for c in cells} == {Decimal(0)}
        (b,) = breaks(path)
        assert (b.kind, b.severity, b.block, b.entity) == (
            "version_not_admitted", "medium", "Plan B_ES", "ES")
        assert dict(b.evidence) == {"version": "Plan B", "found by": "header labels",
                                    "versions admitted": ADMITTED}
        assert b.break_id == the_id("anchor", "Plan B_ES")

    def test_extra_version_block_promoted_builds_no_fourth_version(self, tmp_path):
        built = start_up(tmp_path, FIXTURES / "extra_version_block.xlsx")
        assert [short(b) for b in built.breaks] == FIXTURE_BREAKS["extra_version_block"]
        con = promote_all(built)
        try:
            assert con.execute("SELECT version, version_order, is_actual FROM dim_version "
                               "ORDER BY version_order").fetchall() == [
                ("Actual", 1, True), ("Budget", 2, False), ("Forecast", 3, False)]
            assert con.execute("SELECT count(*) FROM raw_plan_cell WHERE version = 'Plan B' "
                               "AND batch_id = ?", [built.pair.plan_batch_id]).fetchone() == (169,)
            assert con.execute("SELECT count(*) FROM fact_plan WHERE version NOT IN "
                               "('Actual', 'Budget', 'Forecast')").fetchone() == (0,)
            assert con.execute("SELECT count(*) FROM fact_plan").fetchone() == (1170,)
        finally:
            con.close()

    def test_a_version_that_fails_the_allow_list_is_withheld(self, controls):
        base = extraction(FIXTURES / "extra_version_block.xlsx")
        x = with_blocks(base, lambda b: dataclasses.replace(b, version="Plan B; draft")
                        if b.name == "Plan B_ES" else b)
        (b,) = controls.structure(x).breaks
        assert b.message == (
            'The version of block (a name that fails the allow-list) on "Plan_ES", a value that '
            'has characters outside the allowed set (shown encoded in the evidence), is not one '
            'of Actual, Budget, Forecast; the block is landed and not built.')
        assert dict(b.evidence)["version"] == '"Plan B; draft"'

    @pytest.mark.parametrize("text, admitted", [
        ("Budget", "Budget"), ("budget", "Budget"), (" FORECAST ", "Forecast"),
        ("Actual\u00a0", "Actual"), ("Plan B", None), ("Budget 2", None), ("", None)])
    def test_a_version_is_admitted_after_trimming_and_ignoring_case(self, text, admitted):
        from src.extract import VERSIONS, admitted_version
        assert VERSIONS == ("Actual", "Budget", "Forecast")
        assert admitted_version(text) == admitted

    def test_version_label_lowercase_reads_three_blocks_on_its_tab(self):
        x = extraction(FIXTURES / "version_label_lowercase.xlsx")
        assert [(b.name, b.version, b.found_by) for b in x.blocks if b.sheet == "Plan_ES"] == [
            ("Actual_ES", "Actual", "defined name"), ("Budget_ES", "Budget", "defined name"),
            ("Forecast_ES", "Forecast", "defined name")]
        (budget,) = [b for b in x.blocks if b.name == "Budget_ES"]
        assert (budget.version_label_address, budget.version_label) == ("A21", "budget")

    @pytest.mark.parametrize("path", COMMITTED, ids=lambda p: p.name)
    def test_no_cell_of_a_named_block_is_read_again_by_its_labels(self, path):
        x = extraction(path)
        kinds = {b.name: b.found_by for b in x.blocks}
        named = {(c.sheet, c.address) for c in x.cells if kinds.get(c.block) == "defined name"}
        labelled = {(c.sheet, c.address) for c in x.cells
                    if kinds.get(c.block) == "header labels"}
        assert named & labelled == set()

    def test_every_committed_workbook_with_a_block_not_read_has_another_break(self):
        # The coordinator's check: in the committed workbooks, an overlap that
        # leaves a block unread arises only where another break stands: the high
        # anchor break of version_label_changed, and (part 2a2) stray_line_item's
        # one low header_not_read break, the cell that heads the block not read.
        found = {p.name: len(breaks(p)) for p in COMMITTED if extraction(p).blocks_not_read}
        # Part 2a2: stray_line_item's block not read is its one header_not_read break.
        assert found == {"version_label_changed.xlsx": 1, "stray_line_item.xlsx": 1}

    def test_a_stray_line_item_label_over_a_named_block_is_not_read_and_gives_one_low_break(
            self, controls, tmp_path):
        # Found while checking the rule, and reported: a "Line item" cell typed in
        # A4 of Plan_ES, below the Currency cell, reads as the header of a block
        # labelled Currency whose rows run down over the named Actual block. At
        # 1e that block was read, could not be read, and gave one high break. At 2a
        # it overlapped Actual_ES, so it was not read, and nothing was reported;
        # no figure is lost, because every cell under it is read through the name.
        from src.extract import extract_workbook

        def stray(name, data):
            if name == "xl/worksheets/sheet3.xml":
                row = (b'<row r="4"><c r="A4" t="inlineStr"><is><t>Line item</t></is></c>'
                       b'</row>')
                data, count = re.subn(rb'(<row r="5")', row + rb"\1", data, count=1)
                assert count == 1
            return data
        x = extract_workbook(zip_copy(CLEAN, tmp_path / "stray.xlsx", stray))
        assert [(n.sheet, n.version, n.ref, n.overlaps) for n in x.blocks_not_read] == [
            ("Plan_ES", "Currency", "A4:A19", ("Actual_ES",))]
        assert [b.name for b in x.blocks] == [b.name for b in extraction(CLEAN).blocks]
        # Changed on purpose in part 2a2 (the 1e verdict's F17): nothing seen and skipped
        # is silent, so the cell is one low header_not_read break.
        assert [(b.kind, b.severity, b.address) for b in controls.run_controls(x).breaks()] == [
            ("header_not_read", "low", "A4")]


class TestCaseAndSpaces:
    """Item 3: the Currency cell, the version label and the Entity cell, trimmed, any case."""

    @pytest.mark.parametrize("name, value", [("currency_lowercase", "eur"),
                                             ("currency_trailing_space", "EUR ")])
    def test_the_fixtures_hold_the_value_the_brief_names(self, name, value):
        x = extraction(FIXTURES / f"{name}.xlsx")
        (h,) = [h for h in x.headings if (h.sheet, h.label) == ("Plan_AT", "Currency")]
        assert (h.address, h.value) == ("B3", value)

    @pytest.mark.parametrize("name", ["currency_lowercase", "currency_trailing_space",
                                      "version_label_lowercase"])
    def test_the_three_cases_give_no_break_in_the_whole_start_up_build(self, name, tmp_path):
        assert start_up(tmp_path, FIXTURES / f"{name}.xlsx").breaks == ()

    @pytest.mark.parametrize("value", [" EUR", "Eur", "EUR\t", "EUR\u00a0", "\neur  "])
    def test_a_currency_differing_only_in_case_or_white_space(self, controls, value):
        x = with_headings(extraction(CLEAN), lambda h: dataclasses.replace(h, value=value)
                          if (h.sheet, h.label) == ("Plan_AT", "Currency") else h)
        assert controls.structure(x).breaks == ()

    @pytest.mark.parametrize("value", [" es", "Es", "ES\u00a0"])
    def test_an_entity_cell_differing_only_in_case_or_white_space(self, controls, value):
        x = with_headings(extraction(CLEAN), lambda h: dataclasses.replace(h, value=value)
                          if (h.sheet, h.label) == ("Plan_ES", "Entity") else h)
        assert controls.structure(x).breaks == ()

    @pytest.mark.parametrize("label", [" BUDGET", "budget ", "Budget\t"])
    def test_a_version_label_differing_only_in_case_or_white_space(self, controls, label):
        x = with_blocks(extraction(CLEAN), lambda b: dataclasses.replace(b, version_label=label)
                        if b.name == "Budget_AT" else b)
        assert controls.structure(x).breaks == ()

    def test_a_value_that_passes_the_allow_list_is_shown_beside_the_other(self, controls):
        x = with_headings(extraction(CLEAN), lambda h: dataclasses.replace(h, value="FR")
                          if (h.sheet, h.label) == ("Plan_ES", "Entity") else h)
        (b,) = controls.structure(x).breaks
        assert b.message == ('The Entity cell of "Plan_ES", B2, reads "FR", but the defined '
                             'names of its blocks, "Actual_ES", "Budget_ES", "Forecast_ES", say '
                             '"ES".')
        assert dict(b.evidence)["the tab's entity"] == "FR"

    def test_a_currency_that_fails_the_allow_list_is_withheld_and_encoded(self, controls):
        value = "EUR\u200b"                      # a zero-width space, which strip keeps
        x = with_headings(extraction(CLEAN), lambda h: dataclasses.replace(h, value=value)
                          if (h.sheet, h.label) == ("Plan_AT", "Currency") else h)
        (b,) = controls.structure(x).breaks
        assert (b.kind, b.severity) == ("currency_disagreement", "high")
        assert b.message == ('The Currency cell of "Plan_AT", B3, reads a value that has '
                             'characters outside the allowed set (shown encoded in the '
                             'evidence), but the money accounts on Lists are in "EUR".')
        assert value not in b.message
        assert dict(b.evidence)["the tab's currency"] == '"EUR\\u200b"'

    def test_an_entity_cell_that_fails_the_allow_list_is_withheld_and_encoded(self, controls):
        x = with_headings(extraction(CLEAN), lambda h: dataclasses.replace(h, value="ES;")
                          if (h.sheet, h.label) == ("Plan_ES", "Entity") else h)
        (b,) = controls.structure(x).breaks
        assert b.message == ('The Entity cell of "Plan_ES", B2, reads a value that has '
                             'characters outside the allowed set (shown encoded in the '
                             'evidence), but the defined names of its blocks, "Actual_ES", '
                             '"Budget_ES", "Forecast_ES", say "ES".')
        assert dict(b.evidence)["the tab's entity"] == '"ES;"'

    def test_a_version_label_that_fails_the_allow_list_is_withheld_and_encoded(self, controls):
        x = with_blocks(extraction(CLEAN), lambda b: dataclasses.replace(
            b, version_label="Budget\u200b") if b.name == "Budget_AT" else b)
        (b,) = controls.structure(x).breaks
        assert b.message == ('The defined name "Budget_AT" names version "Budget", but the '
                             'label above its header row, in A21 on "Plan_AT", reads a value '
                             'that has characters outside the allowed set (shown encoded in the '
                             'evidence).')
        assert dict(b.evidence)["label above the header row"] == 'A21: "Budget\\u200b"'

    def test_a_long_value_is_bounded_at_60_encoded_characters(self, controls):
        value = "Euro " * 20
        x = with_headings(extraction(CLEAN), lambda h: dataclasses.replace(h, value=value)
                          if (h.sheet, h.label) == ("Plan_AT", "Currency") else h)
        (b,) = controls.structure(x).breaks
        assert b.message == ('The Currency cell of "Plan_AT", B3, reads a value that is longer '
                             'than 50 characters and has more than six words and starts or ends '
                             'with a space (shown encoded in the evidence), but the money '
                             'accounts on Lists are in "EUR".')
        shown = dict(b.evidence)["the tab's currency"]
        assert shown == '"' + "Euro " * 11 + 'Eur" (100 characters in all)'
        assert len(shown.split(" (")[0]) == 60

    def test_an_escape_is_never_cut_in_two(self, controls):
        value = "\u200b" * 30
        x = with_headings(extraction(CLEAN), lambda h: dataclasses.replace(h, value=value)
                          if (h.sheet, h.label) == ("Plan_AT", "Currency") else h)
        (b,) = controls.structure(x).breaks
        assert dict(b.evidence)["the tab's currency"] == ('"' + "\\u200b" * 9
                                                          + '" (30 characters in all)')


class TestTheDocsOf2a:
    def text(self) -> str:
        return " ".join(CONTROLS_DOC.read_text(encoding="utf-8").split())

    def test_one_fault_one_break_is_stated(self):
        text = self.text()
        for phrase in ("does not examine the block's subtotals", "leaf lines' FY totals",
                       "the first, in row order, is the account's line",
                       "one break naming the block", "no `no_unit` break"):
            assert phrase in text, phrase

    def test_the_three_versions_are_stated(self):
        text = self.text()
        for phrase in ("`dim_version` holds Actual, Budget and Forecast and nothing else",
                       "landed raw and not built",
                       "overlaps a block read through a defined name is not read"):
            assert phrase in text, phrase
        assert "land twice" not in text

    def test_case_and_spaces_are_stated(self):
        text = self.text()
        for phrase in ("`str.strip()`", "`casefold()`", "no-break space", "zero-width",
                       "60 encoded characters", "shown encoded in the evidence"):
            assert phrase in text, phrase


# == Part 2a2 =====================================================================================
# The reviewer's Stop 1 verdict of 8 November, section 5, and the ruling of the same day on three
# additions: no cell belongs to two blocks; a blank figure in a Budget or Forecast block is a
# break; an empty Unit header is one break; nothing seen and skipped is silent; the narrower
# subtotal rule; reconcile leaves out an Actual block that is not built, and an account a built
# Actual block lacks; a block landed and not built gives one break; a padded version gives one.

FIXTURES_2A2 = ("names_same_range", "names_partly_overlap", "blank_cell_forecast",
                "blank_run_budget", "blank_month_budget", "unit_header_empty", "stray_line_item",
                "header_only_block", "block_short_wrong_ebit", "actual_account_displaced",
                "landed_block_short", "version_label_padded")
EARLIER = [p for p in COMMITTED if p.stem not in FIXTURES_2A2]
THREE = [("Actual",), ("Budget",), ("Forecast",)]
KINDS_2A2 = {"blocks_overlap", "blank_figure", "blank_month", "header_not_read"}


def pair_rows(con, where: str = "TRUE") -> list:
    """fact_plan's rows, the batch id left out, so two pairs' rows compare."""
    return con.execute("SELECT version, entity, account, period, amount, sheet, address "
                       f"FROM fact_plan WHERE {where} ORDER BY ALL").fetchall()


def promoted(tmp_path, path: Path) -> tuple:
    """(dim_version, keys held twice, fact_plan rows) of a workbook's pair, promoted."""
    built = start_up(tmp_path, path)
    con = promote_all(built)
    try:
        return (con.execute("SELECT version FROM dim_version ORDER BY version_order").fetchall(),
                con.execute("SELECT version, entity, account, period FROM fact_plan "
                            "GROUP BY ALL HAVING count(*) > 1").fetchall(),
                pair_rows(con))
    finally:
        con.close()


@pytest.fixture(scope="module")
def clean_star(tmp_path_factory):
    """The clean pair's fact_plan rows, built and promoted once for this module."""
    return promoted(tmp_path_factory.mktemp("clean_pair"), CLEAN)[2]


def blank(c):
    """A block cell emptied in memory: no value and no formula."""
    return dataclasses.replace(c, kind="empty", formula=None, cached=None, value_type="none",
                               value_text=None, amount=None)


def with_blanks(base, cells):
    return with_cells(base, {(c.sheet, c.address): blank(c) for c in cells})


def found_blocks_overlap(tmp_path) -> Path:
    """The reviewer's case of the ruling of 8 November: the clean workbook with the defined
    names Budget_ES and Forecast_ES deleted and Note typed in A36 of Plan_ES, so the Budget
    block found by its labels runs from A22 down to A51, over the Forecast block found by its
    labels, A38:O51."""
    def edit(part, data):
        if part == "xl/workbook.xml":
            data, count = re.subn(rb'<definedName[^>]*name="(Budget|Forecast)_ES"[^>]*>[^<]*'
                                  rb'</definedName>', b"", data)
            assert count == 2
        if part == "xl/worksheets/sheet3.xml":
            row = b'<row r="36"><c r="A36" t="inlineStr"><is><t>Note</t></is></c></row>'
            data, count = re.subn(rb'(<row r="37")', row + rb"\1", data, count=1)
            assert count == 1
        return data
    return zip_copy(CLEAN, tmp_path / "found_overlap.xlsx", edit)


class TestNoCellInTwoBlocks:
    """Item 1 (must): blocks that share a cell are landed raw, not built, and one break."""

    def test_names_same_range_is_one_break_naming_both_blocks(self):
        path = FIXTURES / "names_same_range.xlsx"
        (b,) = breaks(path)
        assert located(b) == (
            "structure", "blocks_overlap", ("blocks_overlap", "anchor_disagreement"), "high",
            "Plan_ES", "Budget_ES", "ES", None, None, "C23:O35", OVERLAP_ES)
        assert b.break_id == the_id("overlap", "Plan_ES", ["Budget_ES", "Forecast_ES"])
        assert dict(b.evidence) == {
            "blocks": "Budget_ES A22:O35; Forecast_ES A22:O35",
            "cells in more than one block": "169", "first and last shared cell": "C23, O35",
            "headers not read": "A38", "defined name": "Forecast_ES",
            "refers to": "Plan_ES!$A$22:$O$35", "version the name gives": "Forecast",
            "label above the header row": "A21: Budget"}

    def test_the_overlap_is_a_fact_of_the_extraction_and_every_cell_is_flagged(self):
        x = extraction(FIXTURES / "names_same_range.xlsx")
        assert [(o.sheet, o.blocks, o.refs, o.cells, o.first, o.last) for o in x.overlaps] == [
            ("Plan_ES", ("Budget_ES", "Forecast_ES"), ("A22:O35", "A22:O35"), 169, "C23", "O35")]
        flagged = {c.block for c in x.cells if c.overlap}
        assert flagged == {"Budget_ES", "Forecast_ES"}
        assert all(c.overlap for c in x.cells if c.block in flagged)
        assert [(h.address, h.reason, h.version, h.block) for h in x.headers_not_read] == [
            ("A38", "version read", "Forecast", "Forecast_ES")]

    def test_names_same_range_promoted_builds_neither_block_and_the_rest_as_the_clean_pair(
            self, tmp_path, clean_star):
        built = start_up(tmp_path / "w", FIXTURES / "names_same_range.xlsx")
        assert [short(b) for b in built.breaks] == FIXTURE_BREAKS["names_same_range"]
        versions, twice, rows = promoted(tmp_path / "p", FIXTURES / "names_same_range.xlsx")
        assert versions == THREE and twice == []
        assert not [r for r in rows if r[1] == "ES" and r[0] in ("Budget", "Forecast")]
        assert rows == [r for r in clean_star
                        if not (r[1] == "ES" and r[0] in ("Budget", "Forecast"))]
        assert len(rows) == 3 * 78 + 4 * 156   # three Actual blocks of six months, four more

    def test_name_wrong_version_promoted_builds_neither_es_budget_nor_es_forecast(
            self, tmp_path, clean_star):
        # Trap 1 of the coordinator's brief: the one earlier fixture whose break changes.
        built = start_up(tmp_path / "w", FIXTURES / "name_wrong_version.xlsx")
        assert [(b.kind, b.severity) for b in built.breaks] == [
            ("blocks_overlap", "high"), ("unknown_entity", "medium"), ("unknown_entity", "medium")]
        versions, twice, rows = promoted(tmp_path / "p", FIXTURES / "name_wrong_version.xlsx")
        assert versions == THREE and twice == []
        assert {(r[0], r[1]) for r in rows} == {("Actual", "ES")}
        assert rows == [r for r in clean_star if (r[0], r[1]) == ("Actual", "ES")]

    def test_names_partly_overlap_lands_no_cell_so_overlaps_nothing(self, tmp_path, clean_star):
        path = FIXTURES / "names_partly_overlap.xlsx"
        x = extraction(path)
        assert x.overlaps == () and not [c for c in x.cells if c.overlap]
        assert cells_of(x, "Forecast_ES") == []
        (b,) = breaks(path)
        assert b.break_id == the_id("anchor", "Forecast_ES")
        assert dict(b.evidence) == {
            "why it cannot be read": 'no "Line item" header, no month header',
            "blocks its range overlaps": "Budget_ES A22:O35"}
        # The Forecast header in A38 is the name's own header row (the first row of its
        # range holding "Line item"), so it is silent.
        assert x.headers_not_read == ()
        versions, twice, rows = promoted(tmp_path, path)
        assert versions == THREE and twice == []
        assert [r for r in rows if (r[0], r[1]) == ("Forecast", "ES")] == []
        assert rows == [r for r in clean_star if (r[0], r[1]) != ("Forecast", "ES")]

    def test_two_blocks_found_by_their_labels_that_overlap_are_one_break(self, controls,
                                                                          tmp_path):
        # The reviewer's case (ruling of 8 November, item 2): 19 breaks at 7269627.
        from src.extract import extract_workbook
        path = found_blocks_overlap(tmp_path)
        x = extract_workbook(path)
        assert [(b.name, b.found_by, b.ref) for b in x.blocks if b.sheet == "Plan_ES"] == [
            ("Actual_ES", "defined name", "A6:O19"), ("Budget_ES", "header labels", "A22:O51"),
            ("Forecast_ES", "header labels", "A38:O51")]
        (b,) = controls.run_controls(x).breaks()
        assert (b.kind, b.reasons, b.severity, b.block, b.address) == (
            "blocks_overlap", ("blocks_overlap",), "high", "Budget_ES", "C39:O51")
        assert b.message == (
            'The blocks "Budget_ES" (A22:O51) and "Forecast_ES" (A38:O51) on "Plan_ES" share 169 '
            'cells, from C39 to O51: no cell may belong to two blocks, so none of them is built.')
        assert [short(k) for k in start_up(tmp_path, path).breaks] == [short(b)]

    def test_a_found_block_inside_a_named_block_is_still_not_read(self):
        # The normal case stays: version_label_changed's Plan B block is not read.
        x = extraction(FIXTURES / "version_label_changed.xlsx")
        assert len(x.blocks_not_read) == 1 and x.overlaps == ()

    def test_a_name_not_read_takes_no_part(self):
        # The ruling of 8 November, item 2: Budget_AT, already a name not used, keeps its result.
        x = extraction(FIXTURES / "name_other_tab.xlsx")
        assert x.overlaps == () and not [c for c in x.cells if c.overlap]

    def test_nothing_else_is_examined_in_an_overlap_group(self, controls):
        # A typed constant and a wrong subtotal inside the group, in memory: still one break.
        base = extraction(FIXTURES / "names_same_range.xlsx")
        gp = cell_of(base, "Budget_ES", "Gross profit", "2026-02")
        sales = cell_of(base, "Budget_ES", "Product sales", "2026-05")
        x = with_cells(base, {(gp.sheet, gp.address): dataclasses.replace(gp, amount=gp.amount + 1),
                              (sales.sheet, sales.address): typed(sales)})
        assert [b.kind for b in controls.run_controls(x).breaks()] == ["blocks_overlap"]

    def test_only_the_two_fixtures_on_one_range_hold_an_overlap(self):
        found = {p.name for p in COMMITTED if extraction(p).overlaps}
        assert found == {"names_same_range.xlsx", "name_wrong_version.xlsx"}


class TestAnActualBlockNotBuilt:
    """The ruling of 8 November, item 3: reconcile leaves out an entity whose Actual block is not
    built or not read, and the break that explains the block says so."""

    def test_actual_es_on_the_budget_rows_is_one_break_in_the_whole_build(self, tmp_path):
        # 39 breaks at 7269627: one anchor_disagreement and 38 from reconcile.
        path = edit_name(CLEAN, tmp_path / "actual_on_budget.xlsx", "Actual_ES",
                         "Plan_ES!$A$22:$O$35")
        (b,) = start_up(tmp_path, path).breaks
        assert (b.kind, b.reasons, b.severity) == (
            "blocks_overlap", ("blocks_overlap", "anchor_disagreement"), "high")
        assert b.message == (
            'The blocks "Actual_ES" (A22:O35) and "Budget_ES" (A22:O35) on "Plan_ES" share 169 '
            'cells, from C23 to O35: no cell may belong to two blocks, so none of them is built. '
            'The header in A6, under the label "Actual", is not read, because the defined name '
            '"Actual_ES" reads that version elsewhere.' + TIE_OUT.format("ES")
            + ' The defined name "Actual_ES" names version "Actual", but the label above its '
              'header row, in A21 on "Plan_ES", reads "Budget".')

    def test_actual_es_moved_down_is_one_break_in_the_whole_build(self, tmp_path):
        # 7 breaks at 7269627: block_unreadable and six missing entity-months.
        path = edit_name(CLEAN, tmp_path / "actual_down.xlsx", "Actual_ES", "Plan_ES!$A$7:$O$20")
        (b,) = start_up(tmp_path, path).breaks
        assert (b.kind, b.severity, b.address) == ("block_unreadable", "high", "A7:O20")
        assert b.message == (
            'The block "Actual_ES" on "Plan_ES" cannot be read: the first row of its range, '
            'A7:O7, has no "Line item" header and no month header.' + TIE_OUT.format("ES"))
        assert dict(b.evidence)["headers not read"] == "A6"

    def test_a_missing_plan_tab_leaves_its_entity_out_of_the_tie_out(self, tmp_path):
        # missing_tab: 8 breaks at 7269627, of which six missing entity-months for AT.
        built = start_up(tmp_path, FIXTURES / "missing_tab.xlsx")
        assert [(b.break_id, b.kind) for b in built.breaks] == [
            ("ST-96c76f1deda3", "missing_tab"), ("RC-2a1d657a88d6", "unknown_entity")]

    def test_an_actual_block_not_found_is_left_out_and_its_break_says_so(self, controls):
        base = extraction(CLEAN)
        x = dataclasses.replace(base, blocks=tuple(b for b in base.blocks if b.name != "Actual_DE"),
                                cells=tuple(c for c in base.cells if c.block != "Actual_DE"))
        (b,) = controls.structure(x).breaks
        assert (b.kind, b.block) == ("header_not_found", "Actual_DE")
        assert b.message.endswith(TIE_OUT.format("DE"))
        assert controls.actual_left_out(x) == (("DE",), ())

    def test_a_built_actual_block_with_an_empty_month_is_untouched(self, built_warehouse):
        # The sample's AT May stays one reconcile break, with its id.
        assert "RC-a87231377067" in {b.break_id for b in built_warehouse.breaks}
        assert len(built_warehouse.breaks) == 8

    def test_actual_account_displaced_is_one_break_in_the_whole_build(self, tmp_path):
        # The reviewer's q10 (F19, the optional account-level case): 7 breaks at 7269627.
        built = start_up(tmp_path, FIXTURES / "actual_account_displaced.xlsx")
        assert [short(b) for b in built.breaks] == FIXTURE_BREAKS["actual_account_displaced"]
        (b,) = built.breaks
        assert dict(b.evidence) == {"line item": "Materials", "cells that carry it": "A9, A10",
                                    "account it displaced": "5100 Packaging",
                                    "left out of the ledger tie-out": "5000 Materials, "
                                                                      "5100 Packaging"}

    def test_the_accounts_left_out_are_the_ledger_accounts_a_built_actual_block_lacks(
            self, controls):
        x = extraction(FIXTURES / "actual_account_displaced.xlsx")
        assert controls.actual_left_out(x) == ((), (("AT", "5000"), ("AT", "5100")))
        assert controls.actual_left_out(extraction(SAMPLE)) == ((), ())


class TestBlankFigures:
    """Item 2 (must): a blank figure in a Budget or Forecast block, and a blank month."""

    @pytest.mark.parametrize("name, anchors, account, period", [
        ("blank_cell_forecast", ("blank", "Forecast_ES", "5000", "2026-03"), "5000", "2026-03"),
        ("blank_run_budget", ("blank", "Budget_DE", "6100", "2026-04"), "6100", "2026-04"),
        ("blank_month_budget", ("blank_month", "Budget_DE", "2026-08"), None, "2026-08")])
    def test_each_fixture_is_one_break_in_the_whole_build(self, tmp_path, name, anchors,
                                                          account, period):
        built = start_up(tmp_path, FIXTURES / f"{name}.xlsx")
        assert [short(b) for b in built.breaks] == FIXTURE_BREAKS[name]
        (b,) = built.breaks
        assert b.break_id == the_id(*anchors)
        assert (b.account, b.period) == (account, period)

    def test_the_evidence(self):
        (one,) = breaks(FIXTURES / "blank_cell_forecast.xlsx")
        assert dict(one.evidence) == {"line item": "Materials", "months": "2026-03",
                                      "cells": "E41"}
        (run,) = breaks(FIXTURES / "blank_run_budget.xlsx")
        assert dict(run.evidence) == {"line item": "Marketing",
                                      "months": "2026-04, 2026-05, 2026-06",
                                      "cells": "F30, G30, H30"}
        (month,) = breaks(FIXTURES / "blank_month_budget.xlsx")
        assert dict(month.evidence) == {"months": "2026-08", "lines that are blank": "11",
                                        "cells": "J23:J35"}

    def test_blank_cell_forecast_promoted_has_no_row_for_the_blank_cell(self, tmp_path,
                                                                        clean_star):
        versions, twice, rows = promoted(tmp_path, FIXTURES / "blank_cell_forecast.xlsx")
        assert versions == THREE and twice == []
        forecast = [r for r in rows if (r[0], r[1]) == ("Forecast", "ES")]
        assert len(forecast) == 155                  # 13 lines times 12 months, less one
        assert [r for r in forecast if (r[2], r[3]) == ("5000", "2026-03")] == []
        assert [r for r in rows if (r[0], r[1]) != ("Forecast", "ES")] == [
            r for r in clean_star if (r[0], r[1]) != ("Forecast", "ES")]

    def test_a_statistic_is_a_line_too(self, controls):
        base = extraction(CLEAN)
        (b,) = controls.structure(with_blanks(base, [cell_of(base, "Budget_AT", "Volume",
                                                             "2026-01")])).breaks
        assert (b.kind, b.account, b.address) == ("blank_figure", "VOL", "C34")

    def test_a_run_is_broken_by_a_month_that_holds_a_figure(self, controls):
        base = extraction(CLEAN)
        cells = [cell_of(base, "Budget_ES", "Marketing", p) for p in
                 ("2026-04", "2026-05", "2026-07")]
        found = controls.structure(with_blanks(base, cells)).breaks
        assert [(b.kind, b.period, b.address) for b in found] == [
            ("blank_figure", "2026-04", "F30:G30"), ("blank_figure", "2026-07", "I30")]

    def test_a_blank_month_is_its_own_break_and_splits_a_lines_run(self, controls):
        base = extraction(CLEAN)
        lines = [l.label for b in base.blocks if b.name == "Budget_DE" for l in b.lines]
        cells = [cell_of(base, "Budget_DE", line, p) for line in lines
                 for p in ("2026-08", "2026-09")]
        cells += [cell_of(base, "Budget_DE", "Salaries", p) for p in ("2026-07", "2026-10")]
        found = controls.structure(with_blanks(base, cells)).breaks
        assert [(b.kind, b.period, b.address) for b in found] == [
            ("blank_figure", "2026-07", "I29"), ("blank_figure", "2026-10", "L29"),
            ("blank_month", "2026-08", "J23:K35")]
        assert found[2].message == (
            'Every account and statistic line of block "Budget_DE" is blank from 2026-08 to '
            '2026-09, from J23; nothing is built for the block in those months.')

    @pytest.mark.parametrize("change", ["no cached value", "zero", "text", "error"])
    def test_a_cell_that_holds_something_is_not_blank(self, controls, change):
        base = extraction(CLEAN)
        c = cell_of(base, "Forecast_ES", "Materials", "2026-03")
        held = {"no cached value": dataclasses.replace(c, cached=False, value_type="none",
                                                       amount=None),
                "zero": dataclasses.replace(c, kind="constant", formula=None, cached=None,
                                            amount=Decimal(0)),
                "text": dataclasses.replace(c, kind="constant", formula=None, cached=None,
                                            value_type="text", value_text="n/a", amount=None),
                "error": dataclasses.replace(c, value_type="error", value_text="#REF!",
                                             amount=None)}[change]
        x = with_cells(base, {(c.sheet, c.address): held})
        assert not [b for b in controls.structure(x).breaks if b.kind in KINDS_2A2]

    def test_an_empty_text_cell_is_blank(self, controls):
        base = extraction(CLEAN)
        c = cell_of(base, "Forecast_ES", "Materials", "2026-03")
        x = with_cells(base, {(c.sheet, c.address): dataclasses.replace(
            c, kind="constant", formula=None, cached=None, value_type="text", value_text="",
            amount=None)})
        assert [b.kind for b in controls.structure(x).breaks] == ["blank_figure"]

    def test_subtotals_actual_blocks_and_the_fy_column_are_not_looked_at(self, controls):
        base = extraction(CLEAN)
        cells = [cell_of(base, "Budget_ES", "EBIT", "2026-02"),
                 cell_of(base, "Budget_ES", "Salaries", "FY2026"),
                 cell_of(base, "Actual_DE", "Marketing", "2026-03")]
        assert controls.structure(with_blanks(base, cells)).breaks == ()

    def test_a_block_in_an_overlap_group_is_not_looked_at(self, controls):
        base = extraction(FIXTURES / "names_same_range.xlsx")
        x = with_blanks(base, [cell_of(base, "Budget_ES", "Marketing", "2026-04")])
        assert [b.kind for b in controls.structure(x).breaks] == ["blocks_overlap"]

    def test_no_earlier_workbook_gains_a_kind_of_2a2(self):
        # name_wrong_version is the one earlier file whose break becomes an overlap.
        for path in EARLIER:
            kinds = {b.kind for b in breaks(path)} & KINDS_2A2
            assert kinds == ({"blocks_overlap"} if path.stem == "name_wrong_version" else set()), \
                path.name


class TestAnEmptyUnitHeader:
    """Item 3 (should, F16): an emptied Unit header is one break, not two."""

    def test_unit_header_empty_is_one_break(self, tmp_path):
        built = start_up(tmp_path, FIXTURES / "unit_header_empty.xlsx")
        assert [short(b) for b in built.breaks] == FIXTURE_BREAKS["unit_header_empty"]
        (b,) = built.breaks
        assert b.break_id == the_id("units", "Budget_DE")
        assert dict(b.evidence) == {"Unit header": "B22, empty", "line items with no unit": "13"}

    def test_the_column_is_read_as_text_only_and_its_header_as_missing(self):
        x = extraction(FIXTURES / "unit_header_empty.xlsx")
        (b,) = [b for b in x.blocks if b.name == "Budget_DE"]
        assert [(c.letter, c.header, c.role, c.text_only) for c in b.columns[:3]] == [
            ("A", "Line item", "label", True), ("B", None, "missing", True),
            ("C", "2026-01", "month", False)]

    def test_an_empty_header_over_figures_keeps_its_header_not_found(self, controls):
        # Right of the label column, but the cells below hold figures: not the Unit column.
        base = extraction(CLEAN)

        from src.extract import HeaderProblem

        def no_unit(b):
            if b.name != "Budget_DE":
                return b
            return dataclasses.replace(
                b, header_problems=(HeaderProblem("missing", None, ("B",)),),
                columns=tuple(dataclasses.replace(c, header=None, role="missing", text_only=False)
                              if c.role == "unit" else c for c in b.columns),
                lines=tuple(dataclasses.replace(l, unit=None) for l in b.lines))
        found = controls.structure(with_blocks(base, no_unit)).breaks
        assert [(b.kind, b.address) for b in found] == [("header_not_found", "B22"),
                                                         ("block_without_units", "A22:O35")]


class TestAHeaderNotRead:
    """Item 4 (should, F17): a "Line item" cell that heads no block read is one low break."""

    @pytest.mark.parametrize("name, address", [("stray_line_item", "A4"),
                                               ("header_only_block", "A60")])
    def test_one_low_break_and_the_star_is_the_clean_pairs(self, tmp_path, clean_star, name,
                                                           address):
        path = FIXTURES / f"{name}.xlsx"
        built = start_up(tmp_path / "w", path)
        assert [short(b) for b in built.breaks] == FIXTURE_BREAKS[name]
        (b,) = built.breaks
        assert b.break_id == the_id("header", "Plan_ES", address)
        versions, twice, rows = promoted(tmp_path / "p", path)
        assert versions == THREE and twice == [] and rows == clean_star

    def test_the_facts(self):
        stray = extraction(FIXTURES / "stray_line_item.xlsx").headers_not_read
        assert [(h.sheet, h.address, h.reason, h.label, h.ref, h.overlaps) for h in stray] == [
            ("Plan_ES", "A4", "overlaps a named block", "Currency", "A4:A19", ("Actual_ES",))]
        only = extraction(FIXTURES / "header_only_block.xlsx").headers_not_read
        assert [(h.address, h.reason, h.label) for h in only] == [("A60", "no label", None)]
        (b,) = breaks(FIXTURES / "header_only_block.xlsx")
        assert dict(b.evidence) == {"Line item cell": "A60", "cell above it": "A59, empty"}

    def test_a_header_row_of_any_block_read_is_silent(self):
        # version_label_changed: A22 is Budget_ES's header row; name_shifted_up: A22 is the
        # header row the name's range holds, though the range starts a row above it.
        for name in ("version_label_changed", "name_shifted_up"):
            assert extraction(FIXTURES / f"{name}.xlsx").headers_not_read == (), name

    def test_a_skip_explained_by_the_names_own_high_break_is_silent(self):
        # name_shifted: A22's version, Budget, is read through Budget_ES, which cannot be read.
        x = extraction(FIXTURES / "name_shifted.xlsx")
        assert [(h.address, h.reason, h.block) for h in x.headers_not_read] == [
            ("A22", "version read", "Budget_ES")]
        assert [b.kind for b in breaks(FIXTURES / "name_shifted.xlsx")] == ["block_unreadable"]

    @pytest.mark.parametrize("reason, row, extra, why", [
        ("version read", 54, {"label": "Budget", "version": "Budget", "block": "Budget_ES",
                              "read_by": "defined name"},
         'the label above it, "Budget", names version "Budget", which the tab already reads '
         'from the block "Budget_ES"'),
        ("version read", 54, {"label": "Plan B", "version": "Plan B", "block": "Plan B_ES",
                              "read_by": "header labels"},
         'the label above it, "Plan B", names version "Plan B", which the tab already reads '
         'from the block "Plan B_ES"'),
        ("first row", 1, {}, "it is in the first row, so no cell above it gives the block's "
                             "version")])
    def test_the_other_reasons(self, controls, reason, row, extra, why):
        from src.extract import HeaderNotRead
        fact = HeaderNotRead("Plan_ES", f"A{row}", reason, **extra)
        x = dataclasses.replace(extraction(CLEAN), headers_not_read=(fact,))
        (b,) = controls.structure(x).breaks
        assert (b.kind, b.severity, b.sheet, b.entity, b.address) == (
            "header_not_read", "low", "Plan_ES", "ES", f"A{row}")
        assert b.message == (f'The cell A{row} on "Plan_ES" reads "Line item", but no block is '
                             f'read from it: {why}.')


class TestTheNarrowerSubtotalRule:
    """Item 5 (should): a fault stops the totals control on the subtotals its account feeds."""

    def test_block_short_wrong_ebit_gives_its_three_breaks_and_none_on_gross_profit(self,
                                                                                     tmp_path):
        path = FIXTURES / "block_short_wrong_ebit.xlsx"
        assert [short(b) for b in breaks(path)] == FIXTURE_BREAKS["block_short_wrong_ebit"]
        assert not [b for b in breaks(path) if b.account == "GP"]
        built = start_up(tmp_path, path)
        assert [b.kind for b in built.breaks] == ["accounts_not_in_block", "pattern", "subtotal",
                                                  "unknown_entity", "unknown_entity"]

    @pytest.mark.parametrize("row, unchecked", [(26, NOT_CHECKED), (31, EBIT_NOT_CHECKED)])
    def test_a_doubled_account_stops_the_subtotals_of_both_accounts(self, controls, row,
                                                                    unchecked):
        # Packaging (row 26) or Rent (row 31) relabelled Salaries: Salaries feeds EBIT;
        # Packaging feeds Gross profit and EBIT, Rent EBIT alone.
        base = extraction(CLEAN)
        x = with_lines(base, "Budget_ES", lambda ls: relabelled(ls, {row: ("Salaries", "6000")}))
        gp = cell_of(x, "Budget_ES", "Gross profit", "2026-05")
        x = with_cells(x, {(gp.sheet, gp.address): dataclasses.replace(gp, amount=gp.amount + 1)})
        found = controls.run_controls(x).breaks()
        assert found[0].message.endswith(unchecked.format("Budget_ES"))
        gross_profit = [(b.kind, b.address) for b in found if b.account == "GP"]
        assert gross_profit == ([] if unchecked is NOT_CHECKED
                                else [("fy_total", "O28"), ("subtotal", "G28")])

    @pytest.mark.parametrize("code, unchecked", [("6300", NOT_CHECKED),
                                                  ("6100", EBIT_NOT_CHECKED)])
    def test_the_subtotals_an_account_feeds_come_from_lists(self, controls, code, unchecked):
        # IT and software moved above Gross profit on Lists, in memory: it now feeds both
        # subtotals, whatever its parent says; Marketing, still below, feeds EBIT alone.
        base = extraction(CLEAN)
        accounts = list(base.lists.accounts)
        it = next(a for a in accounts if a.code == "6300")
        accounts.remove(it)
        accounts.insert(next(i for i, a in enumerate(accounts) if a.code == "GP"), it)
        x = dataclasses.replace(base, lists=dataclasses.replace(base.lists,
                                                                accounts=tuple(accounts)))
        x = with_lines(x, "Budget_DE", lambda ls: [l for l in ls if l.account != code])
        found = [b for b in controls.structure(x).breaks if b.block == "Budget_DE"]
        assert [b.kind for b in found] == ["accounts_not_in_block"]
        assert found[0].message.endswith(unchecked.format("Budget_DE"))


class TestALandedBlockNotBuilt:
    """Items 7 and 8 (may, F20): a block landed and not built gives that break alone."""

    def test_landed_block_short_is_one_break(self, tmp_path):
        built = start_up(tmp_path, FIXTURES / "landed_block_short.xlsx")
        assert [short(b) for b in built.breaks] == FIXTURE_BREAKS["landed_block_short"]

    def test_version_label_padded_is_one_break_with_the_value_encoded(self, tmp_path):
        built = start_up(tmp_path, FIXTURES / "version_label_padded.xlsx")
        assert [short(b) for b in built.breaks] == FIXTURE_BREAKS["version_label_padded"]
        (b,) = built.breaks
        assert dict(b.evidence) == {"version": '" Plan B "', "found by": "header labels",
                                    "versions admitted": ADMITTED, "allow-list reasons": "space"}
        assert " Plan B " not in b.message


class TestTheDocsOf2a2:
    def text(self) -> str:
        return " ".join(CONTROLS_DOC.read_text(encoding="utf-8").split())

    def test_the_new_kinds_and_rules_are_stated(self):
        text = self.text()
        for phrase in ("`blocks_overlap`", "`blank_figure`", "`blank_month`", "`header_not_read`",
                       "no cell may belong to two blocks", "a block that lands no cell overlaps "
                       "nothing", "only on the subtotals that the account feeds",
                       "a statistic feeds none", "leaves the Actual figures of",
                       "an explicit 0 is not blank", "the header row of a block"):
            assert phrase in text, phrase

    def test_what_the_controls_do_not_see_names_a_blank_statistic_in_actual(self):
        doc = CONTROLS_DOC.read_text(encoding="utf-8")
        section = " ".join(doc.split("\n### What the controls do not see\n", 1)[1]
                           .split("\n## ", 1)[0].split())
        for phrase in ("A blank Volume or Headcount figure in an Actual block",
                       "the per-metric held rule"):
            assert phrase in section, phrase
