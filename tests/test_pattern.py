"""The pattern check of the formula-integrity control (src/controls.py), prototyped first, part 1d.

The pattern check reads the month columns of the Budget and Forecast blocks
only. Each formula is translated with openpyxl's own translator to a fixed
origin on its own row: the far-right column XFD, the last a worksheet can
have, so every relative reference moves by the same amount and none can fall
off the left edge. Identical results form one group. The pattern of a row is
its largest group; when two groups are equally large, the one whose first cell
is further left. A formula outside it "differs from the pattern of its row",
is shown with both patterns, and is "for review": never worded as an error or
as a rate. An array formula is recorded, never read as text, and not compared.
A typed constant holds no formula, so it is never a pattern finding: the rule
for typed constants in a formula row finds it (D1).

The prototype must find D1 and D4 in the sample and nothing in the clean
workbook. The expected patterns are written out by hand below, from the
formulas in the committed files.
"""

import dataclasses

import pytest

from tests.extractions import CLEAN, FIXTURES, MUTATIONS, SAMPLE, extraction, report

COLUMNS = "CDEFGHIJKLMN"
MONTHS = {letter: f"2026-{i:02d}" for i, letter in enumerate(COLUMNS, start=1)}
FORMULA_INTEGRITY = "formula_integrity"

# D4, written out by hand: Plan_ES K39, Forecast Product sales for 2026-09. Its
# neighbour J39 and the cell itself, translated from column J (and K) to XFD on
# row 39: J50 and K50 become XFD50, the Assumptions column L (and M) two to the
# right becomes XFF, and every absolute reference stays as it is.
D4_ROW_PATTERN = ('=ROUND(XFD50*SUMIFS(Assumptions!XFF$5:XFF$70,Assumptions!$A$5:$A$70,$A$37,'
                  'Assumptions!$B$5:$B$70,$B$2,Assumptions!$C$5:$C$70,"Price"),0)')
D4_CELL_PATTERN = ('=ROUND(XFD50*SUMIFS(Assumptions!XFF$5:XFF$70,Assumptions!$A$5:$A$70,$A$37,'
                   'Assumptions!$B$5:$B$70,$B$2,Assumptions!$C$5:$C$70,"Price")*1.02,0)')
D4_FORMULA = ('=ROUND(K50*SUMIFS(Assumptions!M$5:M$70,Assumptions!$A$5:$A$70,$A$37,'
              'Assumptions!$B$5:$B$70,$B$2,Assumptions!$C$5:$C$70,"Price")*1.02,0)')

# Every committed workbook but the sample and its five "minus" variants
# without D4: none of them holds a formula outside the pattern of its row.
NO_PATTERN_FINDING = [CLEAN, FIXTURES / "shifted.xlsx", FIXTURES / "shared_formula.xlsx",
                      FIXTURES / "array_formula.xlsx", FIXTURES / "no_cache.xlsx",
                      MUTATIONS / "sample_minus_d4.xlsx",
                      *[MUTATIONS / f"clean_plus_d{n}.xlsx" for n in (1, 2, 3, 5)],
                      *[FIXTURES / f"{name}.xlsx" for name in (
                          "error_values", "merged_cells", "no_units", "missing_tab",
                          "missing_header", "duplicate_period", "unknown_account", "names",
                          "external_link", "extra_tab", "typed_subtotal")]]


@pytest.fixture(scope="module")
def controls():
    from src import controls
    return controls


def plan_cell(address: str, formula=None, kind="formula", version="Budget", line="Product sales",
              account="4000", amount=None):
    """One block cell built in memory, as the extractor would give it."""
    from src.extract import PlanCell
    letter = address.rstrip("0123456789")
    month = MONTHS.get(letter)
    return PlanCell(
        batch_id="0" * 16, sheet="Plan_ES", address=address, block=f"{version}_ES",
        source_line=None, version=version, entity="ES", line=line, account=account,
        period=month or "FY2026", column_kind="month" if month else "total", unit="EUR",
        kind=kind, formula=formula, cached=True if kind != "constant" else None,
        value_type="number", value_text=None, amount=amount, row_error=None, duplicate=False,
        unknown_entity=False, unknown_account=False, name_allowed=True)


def a_row(formula_of_column, row=28, **options) -> list:
    return [plan_cell(f"{letter}{row}", formula_of_column(letter), **options) for letter in COLUMNS]


def gross_profit(letter: str) -> str:
    return f"=SUM({letter}23:{letter}24)-SUM({letter}25:{letter}27)"


def with_cells(base, changed: dict):
    """The extraction with some block cells replaced in memory, keyed by (sheet, address)."""
    cells = tuple(changed.get((c.sheet, c.address), c) for c in base.cells)
    return dataclasses.replace(base, cells=cells)


def pattern_breaks(result) -> list:
    return [b for b in result.breaks if "pattern" in b.reasons]


# -- The translation ----------------------------------------------------------------------

class TestTheTranslation:
    def test_the_origin_is_the_last_column_on_the_cells_own_row(self, controls):
        assert controls.FAR_RIGHT == "XFD"
        assert controls.pattern_of("=SUM(C23:C24)-SUM(C25:C27)", "C28") == \
            "=SUM(XFD23:XFD24)-SUM(XFD25:XFD27)"
        assert controls.pattern_of("=SUM(N23:N24)-SUM(N25:N27)", "N28") == \
            "=SUM(XFD23:XFD24)-SUM(XFD25:XFD27)"

    def test_absolute_references_do_not_move(self, controls):
        formula = ('=ROUND(C34*SUMIFS(Assumptions!E$5:E$70,Assumptions!$A$5:$A$70,$A$21,'
                   'Assumptions!$B$5:$B$70,$B$2,Assumptions!$C$5:$C$70,"Price"),0)')
        assert controls.pattern_of(formula, "C23") == (
            '=ROUND(XFD34*SUMIFS(Assumptions!XFF$5:XFF$70,Assumptions!$A$5:$A$70,$A$21,'
            'Assumptions!$B$5:$B$70,$B$2,Assumptions!$C$5:$C$70,"Price"),0)')

    def test_a_reference_to_the_left_never_falls_off_the_edge(self, controls):
        assert controls.pattern_of("=A23+B23", "C23") == "=XFB23+XFC23"

    def test_the_twelve_months_of_a_clean_row_give_one_pattern(self, controls):
        cells = [c for c in extraction(CLEAN).cells
                 if (c.block, c.line, c.column_kind) == ("Budget_ES", "Product sales", "month")]
        assert len(cells) == 12
        assert len({controls.pattern_of(c.formula, c.address) for c in cells}) == 1

    def test_d4_translated(self, controls):
        assert controls.pattern_of(D4_FORMULA, "K39") == D4_CELL_PATTERN


# -- The grouping, on rows built in memory ----------------------------------------------------

class TestTheGrouping:
    def test_one_formula_outside_the_pattern(self, controls):
        cells = a_row(lambda c: gross_profit(c) + ("*1.02" if c == "H" else ""))
        found = controls.row_pattern(cells)
        assert found.pattern == "=SUM(XFD23:XFD24)-SUM(XFD25:XFD27)" and found.size == 11
        assert [(c.address, p) for c, p in found.outside] == [
            ("H28", "=SUM(XFD23:XFD24)-SUM(XFD25:XFD27)*1.02")]

    def test_a_row_of_one_pattern_has_nothing_outside(self, controls):
        found = controls.row_pattern(a_row(gross_profit))
        assert found.size == 12 and found.outside == ()

    def test_a_tie_goes_to_the_group_further_left(self, controls):
        cells = a_row(lambda c: f"={c}23" if c in "CDEFGH" else f"={c}24")
        found = controls.row_pattern(cells)
        assert found.pattern == "=XFD23" and found.size == 6
        assert [c.address for c, _ in found.outside] == [f"{c}28" for c in "IJKLMN"]

    def test_a_typed_constant_is_not_a_pattern_finding(self, controls):
        cells = a_row(gross_profit)
        cells[2] = plan_cell("E28", kind="constant", amount=45000)
        found = controls.row_pattern(cells)
        assert found.size == 11 and found.outside == ()

    def test_an_array_formula_is_recorded_and_never_compared(self, controls):
        # Its text would differ from the row's, but it is never read as text.
        cells = a_row(gross_profit)
        cells[4] = plan_cell("G28", "=SUM(G23:G27)*2", kind="array_formula")
        found = controls.row_pattern(cells)
        assert found.outside == () and found.size == 11
        assert [c.address for c in found.arrays] == ["G28"]

    def test_the_total_column_is_not_part_of_the_row(self, controls):
        cells = a_row(gross_profit) + [plan_cell("O28", "=SUM(C28:M28)")]
        found = controls.row_pattern(cells)
        assert found.size == 12 and found.outside == ()


# -- The prototype on the committed workbooks ------------------------------------------------

class TestOnTheCommittedWorkbooks:
    def test_d4_is_the_one_pattern_finding_in_the_sample(self):
        found = pattern_breaks(report(SAMPLE).result(FORMULA_INTEGRITY))
        assert [(b.sheet, b.address, b.block, b.period) for b in found] == [
            ("Plan_ES", "K39", "Forecast_ES", "2026-09")]
        evidence = dict(found[0].evidence)
        assert evidence["pattern of its row"] == D4_ROW_PATTERN
        assert evidence["pattern of this cell"] == D4_CELL_PATTERN
        assert evidence["cells in the row's pattern"] == "11"
        assert evidence["origin"] == "XFD39"

    def test_the_pattern_message_is_for_review(self):
        (found,) = pattern_breaks(report(SAMPLE).result(FORMULA_INTEGRITY))
        assert ('For review: the formula in K39 on "Plan_ES" differs from the pattern of its row.'
                in found.message)
        for word in ("error", "rate", "wrong", "%"):
            assert word not in found.message.lower()

    def test_the_formula_integrity_control_finds_d1_and_d4(self):
        found = report(SAMPLE).result(FORMULA_INTEGRITY).breaks
        assert sorted((b.sheet, b.address, b.reasons) for b in found) == [
            ("Plan_AT", "E30", ("typed_constant",)),
            ("Plan_ES", "K39", ("embedded_constant", "pattern"))]

    def test_d1_is_found_by_the_typed_constant_rule_not_by_the_pattern(self):
        (d1,) = [b for b in report(MUTATIONS / "clean_plus_d1.xlsx").breaks()]
        assert (d1.address, d1.reasons) == ("E30", ("typed_constant",))

    def test_d4_alone_on_the_clean_workbook(self):
        (d4,) = report(MUTATIONS / "clean_plus_d4.xlsx").breaks()
        assert (d4.address, d4.reasons) == ("K39", ("embedded_constant", "pattern"))
        assert dict(d4.evidence)["pattern of this cell"] == D4_CELL_PATTERN

    @pytest.mark.parametrize("path", NO_PATTERN_FINDING,
                             ids=lambda p: p.relative_to(p.parents[1]).as_posix())
    def test_no_pattern_finding(self, path):
        assert pattern_breaks(report(path).result(FORMULA_INTEGRITY)) == []

    def test_the_clean_workbook_gives_no_formula_integrity_break(self):
        assert report(CLEAN).result(FORMULA_INTEGRITY).breaks == ()

    def test_the_array_formula_is_recorded_not_compared(self):
        result = report(FIXTURES / "array_formula.xlsx").result(FORMULA_INTEGRITY)
        assert result.breaks == ()
        assert [where for where, _ in result.recorded] == ["Plan_ES!G34"]
        assert "array formula" in result.recorded[0][1]

    def test_only_budget_and_forecast_months_are_compared(self, controls):
        base = extraction(CLEAN)
        odd = {"Actual_ES": ("D12", "=SUM(D7:D8)-SUM(D9:D10)"),
               "Budget_ES": ("D28", "=SUM(D23:D24)-SUM(D25:D26)"),
               "Budget_FY": ("O28", "=SUM(C28:M28)")}
        changed = {}
        for address, formula in odd.values():
            cell = next(c for c in base.cells if (c.sheet, c.address) == ("Plan_ES", address))
            changed[("Plan_ES", address)] = dataclasses.replace(cell, formula=formula)
        result = controls.formula_integrity(with_cells(base, changed))
        assert [(b.address, b.reasons) for b in result.breaks] == [("D28", ("pattern",))]
