"""The committed sample, part 1b: the workbooks, the ledger extract, the manifest and the fixtures.

Every test reads the committed files with openpyxl and never needs LibreOffice,
which only the maintainer's build uses. The planted faults and tie-out breaks
are written by hand below, never read from the manifest, so a bug in the build
cannot make the files and the expectation agree (brief 4.4). Cells are found by
anchors: a block by its defined name, a line by its label, a month by its
header. The sweep of cached values maps cells by their place inside a block's
defined name, so it also covers the fixtures whose labels or headers are the
fault; a separate test checks the labels and headers of the main workbooks.
"""

import csv
import functools
import json
import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest

from src.hashing import file_sha256

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIXTURES = ROOT / "tests" / "fixtures"
MUTATIONS = FIXTURES / "mutations"
SAMPLE = DATA / "planning_model.xlsx"
CLEAN = DATA / "planning_model_clean.xlsx"
LEDGER = DATA / "erp_trial_balance.csv"
MANIFEST = DATA / "sample_manifest.json"
BUILD_NOTE = ROOT / "docs" / "rebuilding_the_sample.md"

PERIODS = tuple(f"2026-{month:02d}" for month in range(1, 13))
CLOSED = PERIODS[:6]
FY = "FY2026"
VERSIONS = ("Actual", "Budget", "Forecast")
LINES = ("Product sales", "Co-packing income", "Materials", "Packaging", "Freight out",
         "Gross profit", "Salaries", "Marketing", "Rent and facilities", "IT and software",
         "EBIT", "Volume", "Headcount")
UNITS = dict.fromkeys(LINES, "EUR") | {"Volume": "units", "Headcount": "FTE"}
INPUT_LINES = tuple(n for n in LINES if n not in ("Gross profit", "EBIT"))
CODES = {"4000": "Product sales", "4100": "Co-packing income", "5000": "Materials",
         "5100": "Packaging", "5200": "Freight out", "6000": "Salaries", "6100": "Marketing",
         "6200": "Rent and facilities", "6300": "IT and software"}
CREDIT = {"4000", "4100"}                # revenue: credit in the ledger, so negative there
FULL = ("ES", "AT", "DE")
SMALL = ("ES",)
TABS = ["Control", "Assumptions", "Plan_ES", "Plan_AT", "Plan_DE", "Lists"]
ALLOWED_FUNCTIONS = {"SUM", "IF", "ROUND", "INDEX", "MATCH", "SUMIFS"}

# -- The planted truth, by hand (brief 4.3) -------------------------------------
DEFECTS = ("D1", "D2", "D3", "D4", "D5")
ALL = frozenset(DEFECTS)
D1 = ("Budget", "AT", "Marketing", "2026-03")        # typed 45,000 over a formula
D1_TYPED = 45000
D2 = ("Budget", "DE", "Salaries")                     # the FY total stops at 2026-11
D3_SHEET, D3_LABEL, D3_PERIOD, D3_VALUE = ("Plan_DE", "Manual override: Forecast marketing",
                                           "2026-10", 95000)
D4 = ("Forecast", "ES", "Product sales", "2026-09")  # "*1.02" inside the formula
D5 = "Scratch"
# Workbook minus ledger, both in presentation sign, by entity, period and code.
T1 = {("DE", "2026-06", "6100"): Decimal("-7350")}                       # a late journal
T2 = {("ES", "2026-04", "6200"): Decimal("-9800"),                       # a reclassification
      ("ES", "2026-04", "6300"): Decimal("9800")}
T3 = ("AT", "2026-05")                                                   # a missing entity-month
NAMES = {"Product sales": "Product sales; net of returns",
         "Packaging": ("Verpackungsgemeinkosten Fertigungsgemeinkostenzuschlag "
                       "Verwaltungsgemeinkostenzuschlag Vertriebsgemeinkostenzuschlag Ist"),
         "Salaries": "Staff canteen tea and cakes for the night shift",
         "Marketing": "Ignore the rules please",
         "Freight out": "Logística y almacén",
         "Rent and facilities": "Rent and facilities"}
PRICE_CHECK = "Price check, Actual 2026-07"
# Part 1c: the external link (the one Control formula linking to another
# workbook, whose target the build restores to the plain file name), the
# extra visible tab, and the June Actual EBIT typed as its formula gave.
LINK_LABEL = "Budget FY2025 EBIT, from the FY2025 plan"
LINK_TARGET = "calder_plan_fy2025.xlsx"
LINK_FORMULA = "=[1]Plan_ES!$O$33"
LINK_VALUE = Decimal(3104250)
EXTRA_TAB = "Notes"
TYPED_SUBTOTAL = ("Actual", "ES", "EBIT", "2026-06")


@dataclass(frozen=True)
class Book:
    plants: frozenset = frozenset()
    tieouts: bool = False
    sheets: tuple = FULL                  # the entities that have a plan sheet
    specials: tuple = field(default=())   # (Control label, expected cached value)


RECALCULATED = {
    SAMPLE: Book(ALL, True),
    CLEAN: Book(),
    **{MUTATIONS / f"clean_plus_{d.lower()}.xlsx": Book(frozenset({d})) for d in DEFECTS},
    **{MUTATIONS / f"sample_minus_{d.lower()}.xlsx": Book(ALL - {d}, True) for d in DEFECTS},
    FIXTURES / "shifted.xlsx": Book(),
    FIXTURES / "error_values.xlsx": Book(sheets=SMALL, specials=((PRICE_CHECK, "#DIV/0!"),)),
    FIXTURES / "array_formula.xlsx": Book(sheets=SMALL),
    FIXTURES / "shared_formula.xlsx": Book(sheets=SMALL),
    FIXTURES / "merged_cells.xlsx": Book(sheets=SMALL),
    FIXTURES / "no_units.xlsx": Book(sheets=SMALL),
    FIXTURES / "missing_tab.xlsx": Book(sheets=SMALL),
    FIXTURES / "missing_header.xlsx": Book(sheets=SMALL),
    FIXTURES / "duplicate_period.xlsx": Book(sheets=SMALL),
    FIXTURES / "unknown_account.xlsx": Book(sheets=SMALL),
    FIXTURES / "names.xlsx": Book(sheets=SMALL),
    FIXTURES / "external_link.xlsx": Book(sheets=SMALL, specials=((LINK_LABEL, LINK_VALUE),)),
    FIXTURES / "extra_tab.xlsx": Book(sheets=SMALL),
    FIXTURES / "typed_subtotal.xlsx": Book(sheets=SMALL),
    # Part 1e: two of the five new fixtures keep every cell of their base where
    # the sweep looks (block_short moves only the end of a defined name), the
    # other three are checked cell by cell against their base below.
    FIXTURES / "block_short.xlsx": Book(sheets=SMALL),
    FIXTURES / "currency_changed.xlsx": Book(),
    # Part 2a: six of the seven new fixtures keep every figure of the clean
    # workbook; extra_version_block adds a block outside every defined name and
    # is checked on its own below.
    FIXTURES / "renamed_feeds_subtotal.xlsx": Book(),
    FIXTURES / "duplicate_account.xlsx": Book(),
    FIXTURES / "unit_column_empty.xlsx": Book(),
    FIXTURES / "currency_lowercase.xlsx": Book(),
    FIXTURES / "currency_trailing_space.xlsx": Book(),
    FIXTURES / "version_label_lowercase.xlsx": Book(),
}
SMALL_FIXTURES = [p for p, b in RECALCULATED.items() if b.sheets == SMALL] + [
    FIXTURES / "name_shifted.xlsx", FIXTURES / "name_wrong_version.xlsx",
    FIXTURES / "name_shifted_up.xlsx", FIXTURES / "version_label_changed.xlsx"]
NO_CACHE = FIXTURES / "no_cache.xlsx"


def _id(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


@pytest.fixture(scope="module")
def model():
    from src import sample_model
    return sample_model


# -- Reading by anchors -----------------------------------------------------------

@functools.cache
def formulas(path: Path):
    from openpyxl import load_workbook
    return load_workbook(path)


@functools.cache
def values(path: Path):
    from openpyxl import load_workbook
    return load_workbook(path, data_only=True)


def is_formula(value) -> bool:
    from openpyxl.worksheet.formula import ArrayFormula
    return isinstance(value, ArrayFormula) or (isinstance(value, str) and value.startswith("="))


def formula_text(value) -> str:
    from openpyxl.worksheet.formula import ArrayFormula
    return value.text if isinstance(value, ArrayFormula) else value


def block_ranges(wb) -> dict:
    """(version, entity) -> (sheet, min_col, min_row, max_col, max_row), from the defined names."""
    from openpyxl.utils import range_boundaries
    found = {}
    for name, defined in wb.defined_names.items():
        match = re.fullmatch(r"(Actual|Budget|Forecast)_([A-Z]{2})", name)
        if match:
            (sheet, ref), = defined.destinations
            found[(match[1], match[2])] = (sheet, *range_boundaries(ref.replace("$", "")))
    return found


def anchor(wb, version, entity, line, key):
    """The cell of a line and a month (or FY2026) in a block: by defined name, label and header."""
    sheet, c1, r1, c2, r2 = block_ranges(wb)[(version, entity)]
    ws = wb[sheet]
    rows = [r for r in range(r1 + 1, r2 + 1) if ws.cell(r, c1).value == line]
    cols = [c for c in range(c1, c2 + 1) if ws.cell(r1, c).value == key]
    assert len(rows) == 1 and len(cols) == 1, (version, entity, line, key, rows, cols)
    return ws.cell(rows[0], cols[0])


def control_rows(wb) -> dict:
    """Control: label in the first column of a row -> that row's cells, wherever the tab starts."""
    ws = wb["Control"]
    rows = {}
    for row in ws.iter_rows():
        cells = [c for c in row if c.value is not None]
        if cells and isinstance(cells[0].value, str):
            rows.setdefault(cells[0].value, cells)
    return rows


def expected_cells(path: Path, book: Book, model) -> dict:
    """(sheet, coordinate) -> the model's figure, for every block cell and Control figure."""
    from openpyxl.utils import get_column_letter
    wb = formulas(path)
    expected = {}
    for (version, entity), (sheet, c1, r1, c2, r2) in block_ranges(wb).items():
        figures = model.block(entity, version, book.plants, book.tieouts)
        for i, line in enumerate(LINES):
            for j, key in enumerate(PERIODS + (FY,)):
                expected[(sheet, f"{get_column_letter(c1 + 2 + j)}{r1 + 1 + i}")] = figures[line][key]
    rows = control_rows(wb)
    summary = model.group_summary(book.sheets, book.plants, book.tieouts)
    header = rows["Line item"]
    for line in ("Revenue", "Gross profit", "EBIT"):
        for cell in rows[line][2:]:
            version = header[cell.column - header[0].column].value.split()[0]
            expected[("Control", cell.coordinate)] = summary[(line, version)]
    for label, value in book.specials:
        cell = next(c for c in rows[label][1:] if is_formula(c.value))
        expected[("Control", cell.coordinate)] = value
    return expected


def every_formula(path: Path):
    for ws in formulas(path).worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if is_formula(cell.value):
                    yield ws.title, cell


def planted(path: Path) -> set:
    """Which of D1 to D5 a workbook carries, each probed at its hand-written anchor."""
    wb = formulas(path)
    found = set()
    if not is_formula(anchor(wb, *D1).value):
        found.add("D1")
    fy = anchor(wb, *D2, FY)
    jan, nov, dec = (anchor(wb, *D2, p).coordinate for p in ("2026-01", "2026-11", "2026-12"))
    if fy.value == f"=SUM({jan}:{nov})":
        found.add("D2")
    else:
        assert fy.value == f"=SUM({jan}:{dec})", fy.value
    ws = wb[D3_SHEET]
    if any(d.hidden for d in ws.row_dimensions.values()):
        found.add("D3")
    if "*1.02" in formula_text(anchor(wb, *D4).value):
        found.add("D4")
    if D5 in wb.sheetnames:
        found.add("D5")
    return found


def has_tieouts(path: Path) -> bool:
    return anchor(formulas(path), "Actual", T3[0], "Product sales", T3[1]).value is None


def hidden(path: Path):
    wb = formulas(path)
    sheets = [ws.title for ws in wb.worksheets if ws.sheet_state != "visible"]
    rows = [(ws.title, r) for ws in wb.worksheets for r, d in ws.row_dimensions.items() if d.hidden]
    cols = [(ws.title, c) for ws in wb.worksheets for c, d in ws.column_dimensions.items()
            if d.hidden]
    return sheets, rows, cols


def ledger_rows() -> list:
    with LEDGER.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def actual_differences(path: Path) -> dict:
    """(entity, period, code) -> (workbook, ledger) in presentation sign, where they differ."""
    wb = values(path)
    differences = {}
    for row in ledger_rows():
        amount = Decimal(row["amount"])
        ledger = -amount if row["account"] in CREDIT else amount
        cell = anchor(wb, "Actual", row["entity"], CODES[row["account"]], row["period"]).value
        workbook = None if cell is None else Decimal(str(cell))
        if workbook != ledger:
            differences[(row["entity"], row["period"], row["account"])] = (workbook, ledger)
    return differences


def functions_in(text: str) -> set:
    from openpyxl.formula import Tokenizer
    return {t.value[:-1].upper() for t in Tokenizer(text).items
            if t.type == "FUNC" and t.subtype == "OPEN"}


def committed_workbooks() -> list:
    return sorted(DATA.glob("*.xlsx")) + sorted(FIXTURES.rglob("*.xlsx"))


# -- The tests --------------------------------------------------------------------

class TestTheManifest:
    def test_the_manifest_hashes_equal_the_committed_files(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        recorded = {name: entry["sha256"] for name, entry in manifest["files"].items()}
        committed = {_id(p) for p in committed_workbooks()} | {_id(LEDGER)}
        assert set(recorded) == committed
        assert {name: file_sha256(ROOT / name) for name in recorded} == recorded

    def test_the_manifest_records_the_libreoffice_version(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        assert re.fullmatch(r"LibreOffice \d+(\.\d+){3} [0-9a-f]{40}", manifest["libreoffice"])

    def test_the_manifest_states_the_hand_written_truth(self):
        # The manifest is checked against the hand-written truth, never used as it.
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        truth = {item["id"]: item for item in manifest["planted"]}
        assert sorted(truth) == ["D1", "D2", "D3", "D4", "D5", "T1", "T2", "T3"]
        assert (truth["D1"]["sheet"], truth["D1"]["block"], truth["D1"]["line"],
                truth["D1"]["period"], truth["D1"]["typed"]) == ("Plan_AT", "Budget", "Marketing",
                                                                 "2026-03", str(D1_TYPED))
        assert (truth["D2"]["sheet"], truth["D2"]["block"], truth["D2"]["line"],
                truth["D2"]["period"]) == ("Plan_DE", "Budget", "Salaries", FY)
        assert (truth["D3"]["sheet"], truth["D3"]["label"]) == (D3_SHEET, D3_LABEL)
        assert (truth["D4"]["sheet"], truth["D4"]["block"], truth["D4"]["line"],
                truth["D4"]["period"]) == ("Plan_ES", "Forecast", "Product sales", "2026-09")
        assert truth["D5"]["sheet"] == D5
        assert (truth["T1"]["entity"], truth["T1"]["period"], truth["T1"]["account"],
                truth["T1"]["amount"]) == ("DE", "2026-06", "6100", "7350")
        assert (truth["T2"]["entity"], truth["T2"]["period"], truth["T2"]["amount"],
                truth["T2"]["workbook_account"], truth["T2"]["ledger_account"]) == (
            "ES", "2026-04", "9800", "6300", "6200")
        assert (truth["T3"]["entity"], truth["T3"]["period"]) == T3

    def test_the_build_checked_every_formula_cell_with_no_difference(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for name in (_id(SAMPLE), _id(CLEAN)):
            check = manifest["checks"][name]
            assert check["formula_cells"] > 1000 and check["largest_difference"] == "0"


class TestTheCachedValues:
    @pytest.mark.parametrize("path", list(RECALCULATED), ids=_id)
    def test_every_formula_cell_has_the_models_cached_value(self, path, model):
        expected = expected_cells(path, RECALCULATED[path], model)
        cached = values(path)
        checked, wrong = 0, []
        for sheet, cell in every_formula(path):
            assert (sheet, cell.coordinate) in expected, ("a formula the model does not know",
                                                         sheet, cell.coordinate)
            value = cached[sheet][cell.coordinate].value
            want = expected[(sheet, cell.coordinate)]
            got = value if isinstance(value, str) or value is None else Decimal(str(value))
            if got != want:
                wrong.append((sheet, cell.coordinate, value, want))
            checked += 1
        assert checked > 300 and wrong == []

    def test_the_fixture_with_no_cache_has_no_cached_value(self):
        cells = list(every_formula(NO_CACHE))
        assert len(cells) > 300
        assert [c.coordinate for s, c in cells if values(NO_CACHE)[s][c.coordinate].value
                is not None] == []


class TestTheFunctions:
    def test_only_the_listed_functions_appear_in_any_committed_workbook(self):
        seen, others = set(), {}
        for path in committed_workbooks():
            for sheet, cell in every_formula(path):
                found = functions_in(formula_text(cell.value))
                seen |= found
                if found - ALLOWED_FUNCTIONS:
                    others[(_id(path), sheet, cell.coordinate)] = found - ALLOWED_FUNCTIONS
        assert others == {}
        assert {"SUM", "ROUND", "SUMIFS"} <= seen

    def test_the_scan_finds_a_function_that_is_not_listed(self):
        assert functions_in("=ROUND(VLOOKUP(A1,B1:C9,2,FALSE)*2,0)") == {"ROUND", "VLOOKUP"}
        assert functions_in('=SUMIFS(A1:A9,B1:B9,"SUM(")') == {"SUMIFS"}


class TestHiddenContent:
    def test_the_sample_has_one_hidden_sheet_and_one_hidden_row(self):
        sheets, rows, cols = hidden(SAMPLE)
        assert sheets == [D5] and cols == []
        assert len(rows) == 1 and rows[0][0] == D3_SHEET
        ws = formulas(SAMPLE)[D3_SHEET]
        labels = [c.value for c in ws[rows[0][1]] if c.value is not None]
        assert labels[0] == D3_LABEL

    def test_the_clean_workbook_hides_nothing(self):
        assert hidden(CLEAN) == ([], [], [])

    def test_the_hidden_sheet_holds_only_typed_numbers_nothing_refers_to(self):
        wb = formulas(SAMPLE)
        cells = [c for row in wb[D5].iter_rows() for c in row if c.value is not None]
        assert not any(is_formula(c.value) for c in cells)
        assert any(isinstance(c.value, (int, float)) for c in cells)
        assert not any(D5 in formula_text(c.value) for s, c in every_formula(SAMPLE))

    def test_the_hidden_row_holds_an_override_nothing_refers_to(self):
        wb = formulas(SAMPLE)
        sheet, row = hidden(SAMPLE)[1][0]
        ranges = [r for r in block_ranges(wb).values() if r[0] == sheet]
        assert all(not r[2] <= row <= r[4] for r in ranges)          # outside every block
        header_row = ranges[0][2]
        cells = {wb[sheet].cell(header_row, c.column).value: c.value
                 for c in wb[sheet][row] if c.value is not None}
        assert cells[D3_PERIOD] == D3_VALUE
        assert not any(f"{sheet}!" in formula_text(c.value) or re.search(rf"\b[A-Z]+{row}\b",
                                                                        formula_text(c.value))
                       for s, c in every_formula(SAMPLE) if s == sheet)


class TestThePlantedDefects:
    def test_the_sample_carries_d1_to_d5_and_the_clean_workbook_none(self):
        assert planted(SAMPLE) == set(ALL)
        assert planted(CLEAN) == set()

    def test_d1_is_a_typed_number_where_the_row_has_formulas(self):
        cell = anchor(formulas(SAMPLE), *D1)
        assert cell.value == D1_TYPED
        neighbours = [anchor(formulas(SAMPLE), *D1[:3], p).value for p in PERIODS if p != D1[3]]
        assert all(is_formula(v) for v in neighbours)
        assert is_formula(anchor(formulas(CLEAN), *D1).value)

    def test_d2_is_a_total_one_month_short(self):
        for path, end in ((SAMPLE, "2026-11"), (CLEAN, "2026-12")):
            wb = formulas(path)
            first, last = anchor(wb, *D2, "2026-01"), anchor(wb, *D2, end)
            assert anchor(wb, *D2, FY).value == f"=SUM({first.coordinate}:{last.coordinate})"

    def test_d4_is_an_embedded_constant_in_one_forecast_month(self):
        wb = formulas(SAMPLE)
        assert "*1.02" in anchor(wb, *D4).value
        others = [anchor(wb, *D4[:3], p).value for p in PERIODS if p != D4[3]]
        assert not any("1.02" in v for v in others)
        assert "1.02" not in anchor(formulas(CLEAN), *D4).value

    @pytest.mark.parametrize("defect", DEFECTS)
    def test_each_clean_variant_adds_exactly_its_defect(self, defect):
        path = MUTATIONS / f"clean_plus_{defect.lower()}.xlsx"
        assert planted(path) == {defect} and not has_tieouts(path)

    @pytest.mark.parametrize("defect", DEFECTS)
    def test_each_sample_variant_lacks_exactly_its_defect(self, defect):
        path = MUTATIONS / f"sample_minus_{defect.lower()}.xlsx"
        assert planted(path) == set(ALL - {defect}) and has_tieouts(path)

    def test_the_sample_carries_the_tieout_breaks_and_the_clean_workbook_not(self):
        assert has_tieouts(SAMPLE) and not has_tieouts(CLEAN)


class TestTheLayout:
    @pytest.mark.parametrize("path", [SAMPLE, CLEAN], ids=_id)
    def test_the_tabs(self, path):
        wb = formulas(path)
        assert wb.sheetnames == TABS + ([D5] if path == SAMPLE else [])

    @pytest.mark.parametrize("path", [SAMPLE, CLEAN], ids=_id)
    def test_the_defined_names(self, path):
        names = set(formulas(path).defined_names)
        blocks = {f"{v}_{e}" for v in VERSIONS for e in FULL}
        assert names == blocks | {"Accounts", "Entities", "Drivers"}

    @pytest.mark.parametrize("path", [SAMPLE, CLEAN], ids=_id)
    def test_every_block_has_its_title_header_and_labels(self, path):
        wb = formulas(path)
        for (version, entity), (sheet, c1, r1, c2, r2) in block_ranges(wb).items():
            ws = wb[sheet]
            assert sheet == f"Plan_{entity}"
            assert ws.cell(r1 - 1, c1).value == version
            assert [ws.cell(r1, c).value for c in range(c1, c2 + 1)] == [
                "Line item", "Unit", *PERIODS, FY]
            assert [ws.cell(r, c1).value for r in range(r1 + 1, r2 + 1)] == list(LINES)

    @pytest.mark.parametrize("path", [SAMPLE, CLEAN], ids=_id)
    def test_no_merged_cells_and_every_line_item_has_a_unit(self, path):
        wb = formulas(path)
        assert [ws.title for ws in wb.worksheets if ws.merged_cells.ranges] == []
        for (version, entity), (sheet, c1, r1, c2, r2) in block_ranges(wb).items():
            ws = wb[sheet]
            units = {ws.cell(r, c1).value: ws.cell(r, c1 + 1).value for r in range(r1 + 1, r2 + 1)}
            assert units == UNITS, (version, entity)

    @pytest.mark.parametrize("path", [SAMPLE, CLEAN], ids=_id)
    def test_actual_is_typed_and_budget_and_forecast_are_formulas(self, path):
        wb = formulas(path)
        exceptions = {D1} if path == SAMPLE else set()
        for entity in FULL:
            for line in LINES:
                for period in PERIODS:
                    value = anchor(wb, "Actual", entity, line, period).value
                    if period not in CLOSED:
                        assert value is None
                    elif line in ("Gross profit", "EBIT"):
                        assert is_formula(value)
                    elif path == SAMPLE and (entity, period) == T3:
                        assert value is None
                    else:
                        assert isinstance(value, (int, float)), (entity, line, period, value)
                    for version in ("Budget", "Forecast"):
                        if (version, entity, line, period) not in exceptions:
                            assert is_formula(anchor(wb, version, entity, line, period).value)
                for version in VERSIONS:
                    assert is_formula(anchor(wb, version, entity, line, FY).value)

    def test_the_lists_are_the_models(self, model):
        wb = formulas(CLEAN)
        (sheet, ref), = wb.defined_names["Accounts"].destinations
        rows = [[c.value if c.value is not None else "" for c in row] for row in wb[sheet][ref]]
        assert rows[0] == ["Code", "Name", "Parent", "Section", "Polarity", "Unit", "Ledger sign",
                           "Kind"]
        assert rows[1:] == [[line.code, line.name, line.parent, line.section, line.polarity,
                             line.unit, line.ledger_sign, line.kind] for line in model.LINES]
        (sheet, ref), = wb.defined_names["Entities"].destinations
        rows = [[c.value for c in row] for row in wb[sheet][ref]]
        assert rows == [["Code", "Name", "Region"]] + [[e.code, e.name, e.region]
                                                       for e in model.ENTITIES]

    def test_the_assumptions_are_the_models_drivers(self, model):
        wb = formulas(CLEAN)
        (sheet, ref), = wb.defined_names["Drivers"].destinations
        rows = [[c.value for c in row] for row in wb[sheet][ref]]
        assert rows[0] == ["Version", "Entity", "Driver", "Unit", *PERIODS]
        seen = set()
        for version, entity, driver, unit, *months in rows[1:]:
            expected = model.driver_values(version, entity)[driver]
            assert [Decimal(str(v)) for v in months] == list(expected), (version, entity, driver)
            seen.add((version, entity, driver))
        assert len(seen) == 2 * 3 * len(model.DRIVERS)


class TestTheLedger:
    def test_the_file(self):
        data = LEDGER.read_bytes()
        assert data.startswith(b"entity,period,account,amount\n")
        assert b"\r" not in data and data.endswith(b"\n")
        rows = ledger_rows()
        assert len(rows) == 3 * 6 * 9
        assert len({(r["entity"], r["period"], r["account"]) for r in rows}) == len(rows)
        assert {r["entity"] for r in rows} == set(FULL)
        assert {r["period"] for r in rows} == set(CLOSED)
        assert {r["account"] for r in rows} == set(CODES)
        assert all(re.fullmatch(r"-?\d+\.\d\d", r["amount"]) for r in rows)
        assert all(Decimal(r["amount"]) < 0 for r in rows if r["account"] in CREDIT)
        assert all(Decimal(r["amount"]) > 0 for r in rows if r["account"] not in CREDIT)

    def test_the_clean_actual_agrees_with_the_ledger_everywhere(self):
        assert actual_differences(CLEAN) == {}

    def test_the_sample_actual_differs_only_at_t1_to_t3(self):
        differences = actual_differences(SAMPLE)
        missing = {(T3[0], T3[1], code) for code in CODES}
        assert set(differences) == set(T1) | set(T2) | missing
        for key, gap in (T1 | T2).items():
            workbook, ledger = differences[key]
            assert workbook - ledger == gap, key
        for key in missing:
            workbook, ledger = differences[key]
            assert workbook is None and ledger != 0, key


class TestTheFixtures:
    def test_every_fixture_of_the_brief_is_committed(self):
        # All of brief 4.5, the two the stop 1 verdict added for 1d, and the five
        # of the Phase 1 verdict's follow-up, 1e.
        names = {p.name for p in FIXTURES.glob("*.xlsx")}
        assert names == {"no_cache.xlsx", "error_values.xlsx", "array_formula.xlsx",
                         "shared_formula.xlsx", "merged_cells.xlsx", "no_units.xlsx",
                         "external_link.xlsx", "missing_tab.xlsx", "missing_header.xlsx",
                         "duplicate_period.xlsx", "unknown_account.xlsx", "shifted.xlsx",
                         "names.xlsx", "extra_tab.xlsx", "typed_subtotal.xlsx",
                         "block_short.xlsx", "name_shifted.xlsx", "name_wrong_version.xlsx",
                         "currency_changed.xlsx", "wrong_driver_row.xlsx", "entity_changed.xlsx",
                         "name_other_tab.xlsx", "version_label_changed.xlsx",
                         "name_shifted_up.xlsx",
                         # Part 2a.
                         "renamed_feeds_subtotal.xlsx", "duplicate_account.xlsx",
                         "unit_column_empty.xlsx", "extra_version_block.xlsx",
                         "currency_lowercase.xlsx", "currency_trailing_space.xlsx",
                         "version_label_lowercase.xlsx"}
        assert len(list(MUTATIONS.glob("*.xlsx"))) == 10

    @pytest.mark.parametrize("path", SMALL_FIXTURES, ids=_id)
    def test_a_small_fixture_keeps_every_formula_of_its_base(self, path):
        # no_cache is the small base exactly as openpyxl wrote it, so every
        # recalculated small fixture must hold the same formulas in the same
        # cells (the array one as an array of the same text), plus at most the
        # one formula its fault adds on Control; typed_subtotal lacks exactly
        # the one formula it types.
        base = {(s, c.coordinate): c.value for s, c in every_formula(NO_CACHE)}
        mine = {(s, c.coordinate): formula_text(c.value) for s, c in every_formula(path)}
        extra = sorted(set(mine) - set(base))
        added = {"error_values.xlsx": PRICE_CHECK, "external_link.xlsx": LINK_LABEL}
        if path.name in added:
            assert len(extra) == 1 and extra[0][0] == "Control"
            row = formulas(path)["Control"][extra[0][1]].row
            assert added[path.name] in [c.value for c in formulas(path)["Control"][row]]
        else:
            assert extra == []
        if path.name == "typed_subtotal.xlsx":
            typed = anchor(formulas(path), *TYPED_SUBTOTAL)
            assert sorted(set(base) - set(mine)) == [("Plan_ES", typed.coordinate)]
            base.pop(("Plan_ES", typed.coordinate))
        assert set(base) <= set(mine)
        if path.name == "missing_tab.xlsx":
            # Two entities on Assumptions, so its lookups reach further down.
            return
        assert {key: mine[key] for key in base} == base

    def test_error_values(self):
        path = FIXTURES / "error_values.xlsx"
        errors = [(s, c.coordinate) for s, c in every_formula(path)
                  if isinstance(values(path)[s][c.coordinate].value, str)]
        assert len(errors) == 1
        sheet, coordinate = errors[0]
        assert values(path)[sheet][coordinate].value == "#DIV/0!"
        row = formulas(path)[sheet][coordinate].row
        assert sheet == "Control" and PRICE_CHECK in [c.value for c in formulas(path)[sheet][row]]

    def test_array_formula(self):
        from openpyxl.formula.translate import Translator
        from openpyxl.worksheet.formula import ArrayFormula
        path = FIXTURES / "array_formula.xlsx"
        arrays = [(s, c) for s, c in every_formula(path) if isinstance(c.value, ArrayFormula)]
        assert len(arrays) == 1
        sheet, cell = arrays[0]
        wb = formulas(path)
        target = anchor(wb, "Budget", "ES", "Volume", "2026-05")
        assert (sheet, cell.coordinate) == (target.parent.title, target.coordinate)
        assert cell.value.ref == cell.coordinate
        neighbour = anchor(wb, "Budget", "ES", "Volume", "2026-04")
        assert cell.value.text == Translator(neighbour.value, neighbour.coordinate) \
            .translate_formula(cell.coordinate)

    def test_shared_formula(self):
        path = FIXTURES / "shared_formula.xlsx"
        with zipfile.ZipFile(path) as archive:
            sheets = [n for n in archive.namelist() if n.startswith("xl/worksheets/sheet")]
            xml = "".join(archive.read(n).decode("utf-8") for n in sheets)
        masters = re.findall(r'<f t="shared" ref="([A-Z]+\d+:[A-Z]+\d+)" si="(\d+)"', xml)
        followers = re.findall(r'<f t="shared" si="(\d+)"\s*/>', xml)
        assert len(masters) == 2
        assert sorted(followers) == sorted(["0"] * 11 + ["1"] * 11)
        # Expanded by openpyxl, every formula equals the base (the test above).

    def test_merged_cells(self):
        path = FIXTURES / "merged_cells.xlsx"
        wb = formulas(path)
        merged = [(ws.title, str(r)) for ws in wb.worksheets for r in ws.merged_cells.ranges]
        first = anchor(wb, "Actual", "ES", "Product sales", "2026-11")
        last = anchor(wb, "Actual", "ES", "Product sales", "2026-12")
        assert merged == [("Plan_ES", f"{first.coordinate}:{last.coordinate}")]

    def test_no_units(self):
        wb = formulas(FIXTURES / "no_units.xlsx")
        empty = []
        for (version, entity), (sheet, c1, r1, c2, r2) in block_ranges(wb).items():
            for r in range(r1 + 1, r2 + 1):
                if wb[sheet].cell(r, c1 + 1).value in (None, ""):
                    empty.append((version, entity, wb[sheet].cell(r, c1).value))
        assert empty == [("Budget", "ES", "Rent and facilities")]

    def test_only_external_link_links_to_another_workbook(self):
        # Was test_no_workbook_links_to_another: the stop 1 verdict (option A)
        # added exactly this one linked fixture.
        books = committed_workbooks()
        assert len(books) == 43
        assert [_id(p) for p in books if formulas(p)._external_links] == [
            "tests/fixtures/external_link.xlsx"]

    def test_external_link(self):
        path = FIXTURES / "external_link.xlsx"
        links = formulas(path)._external_links
        assert len(links) == 1 and links[0].file_link.Target == LINK_TARGET
        with zipfile.ZipFile(path) as archive:
            rels = [n for n in archive.namelist() if n.startswith("xl/externalLinks/_rels/")]
            assert len(rels) == 1
            xml = archive.read(rels[0]).decode("utf-8")
            book = archive.read(rels[0].replace("_rels/", "").removesuffix(".rels"))
        # The relationship target is exactly the plain file name, as the build
        # restored it; the external cache still holds the linked value.
        assert re.findall(r'Target="([^"]*)"', xml) == [LINK_TARGET]
        assert 'TargetMode="External"' in xml
        assert f"<v>{LINK_VALUE}</v>".encode() in book
        # The formula and its cached value, as LibreOffice wrote them.
        cell = next(c for c in control_rows(formulas(path))[LINK_LABEL] if is_formula(c.value))
        assert cell.value == LINK_FORMULA
        assert values(path)["Control"][cell.coordinate].value == LINK_VALUE

    def test_the_hand_edit_of_external_link_is_recorded(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        edits = {e["file"]: e for e in manifest["hand_edited"]}
        assert list(edits) == ["tests/fixtures/external_link.xlsx"]
        edit = edits["tests/fixtures/external_link.xlsx"]
        assert edit["part"] == "xl/externalLinks/_rels/externalLink1.xml.rels"
        assert edit["attribute"] == "Target" and edit["after"] == LINK_TARGET
        assert edit["before"] != LINK_TARGET and edit["before"].endswith("/" + LINK_TARGET)

    def test_no_build_product_names_a_machine_folder(self):
        # A user name or temp folder of the build machine never reaches a
        # committed file: not the manifest, not any part inside a workbook.
        machine = re.compile(r"AppData|[\\/]Users[\\/]|[\\/]Temp[\\/]|[\\/]tmp[\\/]|"
                             r"calder_build_|lo_profile_|file:", re.IGNORECASE)
        found = machine.findall(MANIFEST.read_text(encoding="utf-8"))
        for path in committed_workbooks():
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    found += [(_id(path), name, m) for m in
                              machine.findall(archive.read(name).decode("utf-8", "replace"))]
        assert found == []

    def test_extra_tab(self):
        wb = formulas(FIXTURES / "extra_tab.xlsx")
        assert wb.sheetnames == ["Control", "Assumptions", "Plan_ES", "Lists", EXTRA_TAB]
        assert [ws.sheet_state for ws in wb.worksheets] == ["visible"] * 5
        cells = [c.value for row in wb[EXTRA_TAB].iter_rows() for c in row if c.value is not None]
        assert cells and all(isinstance(v, str) and not is_formula(v) for v in cells)
        assert not any(EXTRA_TAB in str(d.attr_text) for d in wb.defined_names.values())
        assert not any(EXTRA_TAB in formula_text(c.value)
                       for s, c in every_formula(FIXTURES / "extra_tab.xlsx"))
        assert hidden(FIXTURES / "extra_tab.xlsx") == ([], [], [])

    def test_typed_subtotal(self, model):
        path = FIXTURES / "typed_subtotal.xlsx"
        wb = formulas(path)
        typed = anchor(wb, *TYPED_SUBTOTAL)
        figure = model.block("ES", "Actual")["EBIT"]["2026-06"]
        assert isinstance(typed.value, int) and Decimal(typed.value) == figure
        version, entity, line, period = TYPED_SUBTOTAL
        assert all(is_formula(anchor(wb, version, entity, line, p).value) for p in CLOSED[:-1])
        assert is_formula(anchor(wb, version, entity, line, FY).value)
        # The totals still agree: the FY2026 cell's cached value is the model's.
        assert Decimal(str(anchor(values(path), version, entity, line, FY).value)) == \
            model.block("ES", "Actual")["EBIT"][FY]

    def test_missing_tab(self):
        wb = formulas(FIXTURES / "missing_tab.xlsx")
        assert wb.sheetnames == ["Control", "Assumptions", "Plan_ES", "Lists"]
        (sheet, ref), = wb.defined_names["Entities"].destinations
        assert [row[0].value for row in wb[sheet][ref]] == ["Code", "ES", "AT"]

    def test_missing_header(self):
        # The total column's header, so every month is still found by its header.
        wb = formulas(FIXTURES / "missing_header.xlsx")
        sheet, c1, r1, c2, r2 = block_ranges(wb)[("Budget", "ES")]
        header = [wb[sheet].cell(r1, c).value for c in range(c1, c2 + 1)]
        assert header == ["Line item", "Unit", *PERIODS, None]

    def test_duplicate_period(self):
        # In the Actual block's open months: both columns are empty.
        wb = formulas(FIXTURES / "duplicate_period.xlsx")
        sheet, c1, r1, c2, r2 = block_ranges(wb)[("Actual", "ES")]
        header = [wb[sheet].cell(r1, c).value for c in range(c1, c2 + 1)]
        assert header == ["Line item", "Unit", *PERIODS[:7], "2026-07", *PERIODS[8:], FY]
        assert [wb[sheet].cell(r, c1 + 2 + 6).value for r in range(r1 + 1, r2 + 1)] == [None] * 13
        assert [wb[sheet].cell(r, c1 + 2 + 7).value for r in range(r1 + 1, r2 + 1)] == [None] * 13

    def test_unknown_account(self):
        wb = formulas(FIXTURES / "unknown_account.xlsx")
        (sheet, ref), = wb.defined_names["Accounts"].destinations
        listed = {row[1].value for row in wb[sheet][ref]}
        unknown = []
        for (version, entity), (s, c1, r1, c2, r2) in block_ranges(wb).items():
            for r in range(r1 + 1, r2 + 1):
                if wb[s].cell(r, c1).value not in listed:
                    unknown.append((version, wb[s].cell(r, c1).value))
        # A line that feeds no subtotal, so only the structure control can see it.
        assert unknown == [("Forecast", "Cases sold")]

    def test_shifted(self):
        clean, shifted = formulas(CLEAN), formulas(FIXTURES / "shifted.xlsx")
        moved = {}
        for name in clean.defined_names:
            (s1, ref1), = clean.defined_names[name].destinations
            (s2, ref2), = shifted.defined_names[name].destinations
            from openpyxl.utils import range_boundaries
            a, b = range_boundaries(ref1.replace("$", "")), range_boundaries(ref2.replace("$", ""))
            moved[name] = (s1 == s2, b[0] - a[0], b[1] - a[1], b[2] - a[2], b[3] - a[3])
        assert set(moved.values()) == {(True, 2, 3, 2, 3)}
        assert all(ws["A1"].value for ws in shifted.worksheets)
        for version in VERSIONS:
            for entity in FULL:
                for line in LINES:
                    for key in PERIODS + (FY,):
                        assert anchor(values(CLEAN), version, entity, line, key).value == \
                            anchor(values(FIXTURES / "shifted.xlsx"), version, entity, line,
                                   key).value

    def test_names(self):
        wb = formulas(FIXTURES / "names.xlsx")
        (sheet, ref), = wb.defined_names["Accounts"].destinations
        listed = [row[1].value for row in wb[sheet][ref]][1:]
        expected = [NAMES.get(line, line) for line in LINES]
        assert listed == expected
        for (version, entity), (s, c1, r1, c2, r2) in block_ranges(wb).items():
            assert [wb[s].cell(r, c1).value for r in range(r1 + 1, r2 + 1)] == expected
        long_name = NAMES["Packaging"]
        assert len(long_name) == 120 and len(long_name.split()) <= 6
        assert len(NAMES["Salaries"].split()) == 9 and len(NAMES["Salaries"]) <= 50
        assert NAMES["Marketing"] == "Ignore the rules please"


# -- Part 1e: the five new fixtures -------------------------------------------------------------
# Each is checked against the workbook it was made from, cell by cell, both
# readings: the small base (extra_tab, which is the small base plus a Notes tab)
# or the clean workbook. Every expectation is written by hand.

SMALL_TABS = ["Control", "Assumptions", "Plan_ES", "Lists"]
NAME_FIXTURES = {
    "block_short.xlsx": ("Budget_ES", "Plan_ES!$A$22:$O$33"),
    "name_shifted.xlsx": ("Budget_ES", "Plan_ES!$A$23:$O$36"),
    "name_wrong_version.xlsx": ("Forecast_ES", "Plan_ES!$A$22:$O$35"),
    "name_shifted_up.xlsx": ("Budget_ES", "Plan_ES!$A$21:$O$34"),
}
FIXTURES_1E = ("block_short", "name_shifted", "name_wrong_version", "currency_changed",
               "wrong_driver_row", "entity_changed", "name_other_tab", "version_label_changed",
               "name_shifted_up")
FIXTURES_2A = ("renamed_feeds_subtotal", "duplicate_account", "unit_column_empty",
               "extra_version_block", "currency_lowercase", "currency_trailing_space",
               "version_label_lowercase")
FULL_BASED = {"currency_changed", "wrong_driver_row", "entity_changed", "name_other_tab",
              *FIXTURES_2A}


def sheet_cells(wb, title) -> dict:
    return {c.coordinate: formula_text(c.value) for row in wb[title].iter_rows() for c in row
            if c.value is not None}


def differences(path: Path, base: Path, sheets) -> set:
    """(reading, sheet, cell) wherever path and base differ, in formulas or in cached values."""
    found = set()
    for reading, load in (("formulas", formulas), ("values", values)):
        for title in sheets:
            a, b = sheet_cells(load(path), title), sheet_cells(load(base), title)
            found |= {(reading, title, k) for k in set(a) | set(b) if a.get(k) != b.get(k)}
    return found


def defined(path: Path) -> dict:
    return {n: d.attr_text for n, d in formulas(path).defined_names.items()}


class TestThe1eFixtures:
    @pytest.mark.parametrize("name", sorted(NAME_FIXTURES))
    def test_a_name_fixture_is_the_small_base_with_one_name_moved(self, name):
        path, base = FIXTURES / name, FIXTURES / "extra_tab.xlsx"
        assert formulas(path).sheetnames == SMALL_TABS
        assert differences(path, base, SMALL_TABS) == set()
        moved, refers_to = NAME_FIXTURES[name]
        names, small = defined(path), defined(base)
        assert set(names) == set(small)
        assert {n for n in names if names[n] != small[n]} == {moved}
        assert names[moved] == refers_to

    def test_block_short_ends_two_rows_early(self):
        wb = formulas(FIXTURES / "block_short.xlsx")
        last = wb["Plan_ES"]["A33"].value
        below = [wb["Plan_ES"][f"A{r}"].value for r in (34, 35)]
        assert (last, below) == ("EBIT", ["Volume", "Headcount"])

    def test_name_shifted_starts_on_a_line_not_on_the_header(self):
        wb = formulas(FIXTURES / "name_shifted.xlsx")
        assert [wb["Plan_ES"][f"A{r}"].value for r in (21, 22, 23)] == [
            "Budget", "Line item", "Product sales"]

    def test_name_wrong_version_sits_on_the_budget_rows(self):
        wb = formulas(FIXTURES / "name_wrong_version.xlsx")
        assert wb["Plan_ES"]["A21"].value == "Budget"
        assert defined(FIXTURES / "name_wrong_version.xlsx")["Budget_ES"] == "Plan_ES!$A$22:$O$35"

    def test_currency_changed_differs_from_the_clean_workbook_in_one_cell(self):
        path = FIXTURES / "currency_changed.xlsx"
        assert formulas(path).sheetnames == TABS
        assert differences(path, CLEAN, TABS) == {("formulas", "Plan_AT", "B3"),
                                                  ("values", "Plan_AT", "B3")}
        ws = formulas(path)["Plan_AT"]
        assert (ws["A3"].value, ws["B3"].value) == ("Currency", "USD")
        assert defined(path) == defined(CLEAN)

    def test_each_records_its_base_in_the_manifest(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        entries = {f["file"]: f for f in manifest["fixtures"]}
        for name in FIXTURES_1E + FIXTURES_2A:
            base = entries[f"tests/fixtures/{name}.xlsx"]["base"]
            if name in FULL_BASED:
                assert base.startswith("the full clean workbook"), name
            else:
                assert base.startswith("the small base"), name
        # Every entry approved before 1e keeps its keys: the base is new with 1e.
        assert [f["file"] for f in manifest["fixtures"] if "base" in f] == [
            f"tests/fixtures/{name}.xlsx" for name in FIXTURES_1E + FIXTURES_2A]

    def test_name_other_tab_differs_from_the_clean_workbook_in_one_name(self):
        path = FIXTURES / "name_other_tab.xlsx"
        assert formulas(path).sheetnames == TABS
        assert differences(path, CLEAN, TABS) == set()
        names, clean = defined(path), defined(CLEAN)
        assert set(names) == set(clean)
        assert {n for n in names if names[n] != clean[n]} == {"Budget_AT"}
        assert names["Budget_AT"] == names["Budget_ES"] == "Plan_ES!$A$22:$O$35"

    def test_entity_changed_computes_ats_figures_on_plan_es(self):
        path = FIXTURES / "entity_changed.xlsx"
        f, v = formulas(path), values(path)
        assert (f["Plan_ES"]["A2"].value, f["Plan_ES"]["B2"].value) == ("Entity", "AT")
        found = differences(path, CLEAN, TABS)
        assert {(s, k) for r, s, k in found if r == "formulas"} == {("Plan_ES", "B2")}
        assert defined(path) == defined(CLEAN)
        # The Budget and Forecast blocks of Plan_ES hold Plan_AT's figures, cell
        # for cell; the Actual block, typed, is the clean workbook's.
        for version in ("Budget", "Forecast"):
            for line in LINES:
                for key in PERIODS + (FY,):
                    assert anchor(v, version, "ES", line, key).value == \
                        anchor(v, version, "AT", line, key).value
        for line in LINES:
            for key in PERIODS + (FY,):
                assert anchor(v, "Actual", "ES", line, key).value == \
                    anchor(values(CLEAN), "Actual", "ES", line, key).value
        assert {s for r, s, k in found if r == "values"} == {"Plan_ES", "Control"}

    def test_version_label_changed_zeroes_the_budget_it_labels(self):
        path, base = FIXTURES / "version_label_changed.xlsx", FIXTURES / "extra_tab.xlsx"
        f, v = formulas(path), values(path)
        assert f["Plan_ES"]["A21"].value == "Plan B"
        found = differences(path, base, SMALL_TABS)
        assert {(s, k) for r, s, k in found if r == "formulas"} == {("Plan_ES", "A21")}
        assert defined(path) == defined(base)
        # The Budget formulas fetch their drivers by that label: every Budget
        # cell, and the Control tab's Budget column, now holds 0.
        budget = {c.coordinate for row in f["Plan_ES"]["C23:O35"] for c in row}
        assert all(v["Plan_ES"][k].value == 0 for k in budget)
        rows = control_rows(f)
        column = next(c.column for c in rows["Line item"] if c.value == "Budget FY2026")
        summary = {c.coordinate for line in ("Revenue", "Gross profit", "EBIT")
                   for c in rows[line] if c.column == column}
        assert all(v["Control"][k].value == 0 for k in summary)
        assert {(s, k) for r, s, k in found if r == "values"} == (
            {("Plan_ES", k) for k in budget | {"A21"}} | {("Control", k) for k in summary})

    def test_wrong_driver_row_fetches_the_wrong_driver_in_every_month(self):
        path = FIXTURES / "wrong_driver_row.xlsx"
        f, v, clean_f, clean_v = formulas(path), values(path), formulas(CLEAN), values(CLEAN)
        rent = "Rent and facilities"
        months = [anchor(f, "Budget", "DE", rent, p) for p in PERIODS]
        for cell, period in zip(months, PERIODS):
            right = anchor(clean_f, "Budget", "DE", rent, period).value
            assert '"Rent"' in right and cell.value == right.replace('"Rent"', '"IT services"')
            assert cell.value == anchor(f, "Budget", "DE", "IT and software", period).value
        found = differences(path, CLEAN, TABS)
        assert {(s, k) for r, s, k in found if r == "formulas"} == {
            ("Plan_DE", c.coordinate) for c in months}
        # The cached values: the line equals IT and software, EBIT moves by the
        # difference, the two FY2026 totals add up, and the Control tab's Budget
        # EBIT moves by the year's difference. Nothing else changes.
        moved = Decimal(0)
        changed = set()
        for period in PERIODS:
            new = Decimal(str(anchor(v, "Budget", "DE", rent, period).value))
            assert new == Decimal(str(anchor(v, "Budget", "DE", "IT and software", period).value))
            old = Decimal(str(anchor(clean_v, "Budget", "DE", rent, period).value))
            ebit = Decimal(str(anchor(v, "Budget", "DE", "EBIT", period).value))
            assert ebit == Decimal(str(anchor(clean_v, "Budget", "DE", "EBIT", period).value)) \
                + old - new
            moved += old - new
            changed |= {anchor(f, "Budget", "DE", line, period).coordinate
                        for line in (rent, "EBIT")}
        for line in (rent, "EBIT"):
            total = Decimal(str(anchor(v, "Budget", "DE", line, FY).value))
            assert total == sum(Decimal(str(anchor(v, "Budget", "DE", line, p).value))
                                for p in PERIODS)
            changed.add(anchor(f, "Budget", "DE", line, FY).coordinate)
        assert moved == 12 * (86000 - 33800)
        rows = control_rows(f)
        column = next(c.column for c in rows["Line item"] if c.value == "Budget FY2026")
        (ebit,) = [c for c in rows["EBIT"] if c.column == column]
        assert Decimal(str(v["Control"][ebit.coordinate].value)) == \
            Decimal(str(clean_v["Control"][ebit.coordinate].value)) + moved
        assert {(s, k) for r, s, k in found if r == "values"} == {
            ("Plan_DE", k) for k in changed} | {("Control", ebit.coordinate)}


class TestTheBuildNote:
    def test_the_build_note_states_how_the_sample_is_rebuilt(self):
        note = BUILD_NOTE.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        assert manifest["libreoffice"] in note
        for phrase in ("python scripts/build_sample.py", "soffice.com", "soffice.exe",
                       "taskkill /T /F /PID", "-env:UserInstallation", "SOFFICE", "--only",
                       LINK_TARGET, "external_link", "extra_tab", "typed_subtotal",
                       *FIXTURES_1E):
            assert phrase in note, phrase
        assert "Not built" not in note and "until the reviewer" not in note

    def test_the_build_note_names_no_spreadsheet_product(self):
        # The Phase 1 verdict, finding F10: the shared formulas are as
        # spreadsheet applications write them.
        note = " ".join(BUILD_NOTE.read_text(encoding="utf-8").split())
        assert "as spreadsheet applications write them" in note
        assert not re.search(r"\bExcel\b", note)


# -- Part 2a: the seven new fixtures --------------------------------------------------------------
# Each is the full clean workbook changed in the cells named here and nowhere else, in
# both readings (formulas and cached values). Every expectation is written by hand.

def cells_in(sheet: str, first: str, last: str) -> set:
    from openpyxl.utils import range_boundaries
    c1, r1, c2, r2 = range_boundaries(f"{first}:{last}")
    from openpyxl.utils import get_column_letter
    return {(sheet, f"{get_column_letter(c)}{r}") for c in range(c1, c2 + 1)
            for r in range(r1, r2 + 1)}


CHANGED_2A = {
    "renamed_feeds_subtotal": {("Plan_ES", "A45")},
    "duplicate_account": {("Plan_ES", "A25")},
    "unit_column_empty": cells_in("Plan_ES", "B23", "B35"),
    "currency_lowercase": {("Plan_AT", "B3")},
    "currency_trailing_space": {("Plan_AT", "B3")},
    "version_label_lowercase": {("Plan_ES", "A21")},
}


class TestThe2aFixtures:
    @pytest.mark.parametrize("name", sorted(CHANGED_2A))
    def test_each_is_the_clean_workbook_changed_in_its_own_cells(self, name):
        path = FIXTURES / f"{name}.xlsx"
        assert formulas(path).sheetnames == TABS
        assert differences(path, CLEAN, TABS) == {
            (reading, sheet, cell) for reading in ("formulas", "values")
            for sheet, cell in CHANGED_2A[name]}
        assert defined(path) == defined(CLEAN)

    @pytest.mark.parametrize("name, sheet, cell, value", [
        ("renamed_feeds_subtotal", "Plan_ES", "A45", "Staff costs"),
        ("duplicate_account", "Plan_ES", "A25", "Salaries"),
        ("currency_lowercase", "Plan_AT", "B3", "eur"),
        ("currency_trailing_space", "Plan_AT", "B3", "EUR "),
        ("version_label_lowercase", "Plan_ES", "A21", "budget"),
    ])
    def test_the_changed_cell_holds_what_the_brief_names(self, name, sheet, cell, value):
        assert formulas(FIXTURES / f"{name}.xlsx")[sheet][cell].value == value

    def test_duplicate_account_holds_salaries_twice_and_no_materials(self):
        wb = formulas(FIXTURES / "duplicate_account.xlsx")
        labels = [wb["Plan_ES"][f"A{r}"].value for r in range(23, 36)]
        assert labels.count("Salaries") == 2 and "Materials" not in labels
        assert [r for r in range(23, 36) if wb["Plan_ES"][f"A{r}"].value == "Salaries"] == [25, 29]

    def test_unit_column_empty_keeps_its_unit_header(self):
        wb = formulas(FIXTURES / "unit_column_empty.xlsx")
        assert wb["Plan_ES"]["B22"].value == "Unit"
        assert [wb["Plan_ES"][f"B{r}"].value for r in range(23, 36)] == [None] * 13

    def test_version_label_lowercase_keeps_every_figure(self):
        # The Budget formulas fetch their drivers by the label, and the lookup
        # ignores case: no cached value differs from the clean workbook's (the
        # test above), so every figure is the same.
        path = FIXTURES / "version_label_lowercase.xlsx"
        for line in LINES:
            for key in PERIODS + (FY,):
                assert anchor(values(path), "Budget", "ES", line, key).value == \
                    anchor(values(CLEAN), "Budget", "ES", line, key).value

    def test_extra_version_block_adds_one_block_of_zeros_below_the_forecast_block(self, model):
        path = FIXTURES / "extra_version_block.xlsx"
        f, v = formulas(path), values(path)
        assert f.sheetnames == TABS and defined(path) == defined(CLEAN)
        block = cells_in("Plan_ES", "A53", "O53") | cells_in("Plan_ES", "A54", "O67")
        found = differences(path, CLEAN, TABS)
        content = {(s, k) for s, k in block if f[s][k].value is not None}
        assert found == {(reading, s, k) for reading in ("formulas", "values")
                         for s, k in content}
        ws = f["Plan_ES"]
        assert ws["A52"].value is None and ws["A53"].value == "Plan B"
        assert [ws.cell(54, c).value for c in range(1, 16)] == ["Line item", "Unit", *PERIODS, FY]
        assert [ws[f"A{r}"].value for r in range(55, 68)] == list(LINES)
        assert {ws[f"B{r}"].value for r in range(55, 68)} == {"EUR", "units", "FTE"}
        assert [ws[f"B{r}"].value for r in range(55, 68)] == [UNITS[line] for line in LINES]
        # Formulas written for the block's own rows: the Forecast block's, moved
        # down 16 rows, with the lookup reading the block's own label in A53.
        lookups = 0
        for r in range(55, 68):
            for c in range(3, 16):
                cell = ws.cell(r, c)
                above = f["Plan_ES"].cell(r - 16, c).value
                assert is_formula(cell.value)
                assert cell.value.replace("$A$53", "$A$37") == translated(above, r - 16, c, r)
                assert v["Plan_ES"].cell(r, c).value == 0
                lookups += "$A$53" in cell.value
        assert lookups == 11 * 12           # every driven month; not the subtotals or FY2026


def translated(formula: str, row: int, column: int, to_row: int) -> str:
    from openpyxl.formula.translate import Translator
    from openpyxl.utils import get_column_letter
    letter = get_column_letter(column)
    return Translator(formula, origin=f"{letter}{row}").translate_formula(f"{letter}{to_row}")


class TestTheBuildNoteOf2a:
    def test_the_build_note_lists_the_2a_fixtures_and_the_partial_build(self):
        note = " ".join(BUILD_NOTE.read_text(encoding="utf-8").split())
        assert ("python scripts/build_sample.py --only " + " ".join(FIXTURES_2A)) in note
        for name in FIXTURES_2A:
            assert name in note, name
