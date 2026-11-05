"""The workbook extractor (src/extract.py), part 1c, brief 5.1.

The extractor reads a planning workbook by anchors (plan sheets by name, blocks
by their defined names with the header labels as the fallback, months by their
header cells, accounts by their labels), reads it twice (formulas, then cached
values) and joins the two into one row per block cell. Beside the rows it
records the facts the five controls of part 1d need, so that 1d never opens the
file again: sheets and their visibility, hidden rows and columns and what they
hold, merged ranges, units, header problems, labels not on Lists, defined
names, every formula in the workbook with its cached state, the Lists tab, and
the external link targets. It reports facts and never judges them.

The expected figures come from src/sample_model.py, the independent
calculation; the expected anchors and faults are written by hand below.
"""

import dataclasses
import re
import shutil
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from src.hashing import file_sha256

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIXTURES = ROOT / "tests" / "fixtures"
SAMPLE = DATA / "planning_model.xlsx"
CLEAN = DATA / "planning_model_clean.xlsx"
EXTRACT_SOURCE = ROOT / "src" / "extract.py"

PERIODS = tuple(f"2026-{month:02d}" for month in range(1, 13))
CLOSED = PERIODS[:6]
FY = "FY2026"
VERSIONS = ("Actual", "Budget", "Forecast")
LINES = ("Product sales", "Co-packing income", "Materials", "Packaging", "Freight out",
         "Gross profit", "Salaries", "Marketing", "Rent and facilities", "IT and software",
         "EBIT", "Volume", "Headcount")
CODES = ("4000", "4100", "5000", "5100", "5200", "GP", "6000", "6100", "6200", "6300", "EBIT",
         "VOL", "HC")
TABS = ("Control", "Assumptions", "Plan_ES", "Plan_AT", "Plan_DE", "Lists")
SMALL_TABS = ("Control", "Assumptions", "Plan_ES", "Lists")
GROUP = "Group planning team"
# Every field of a row but the three that move with a shifted layout or a new
# file: the batch id (from the file's hash), the cell address and the formula text.
MOVING = {"batch_id", "address", "formula"}


@pytest.fixture(scope="module")
def extract():
    from src import extract
    return extract


@pytest.fixture(scope="module")
def model():
    from src import sample_model
    return sample_model


_CACHE: dict = {}


def extracted(extract, path: Path, **options):
    key = (path, tuple(sorted(options.items())))
    if key not in _CACHE:
        _CACHE[key] = extract.extract_workbook(path, **options)
    return _CACHE[key]


def fields(row, leave_out=frozenset({"batch_id"})) -> tuple:
    return tuple((k, v) for k, v in dataclasses.asdict(row).items() if k not in leave_out)


def rows_of(extraction, version=None, entity=None, line=None, period=None) -> list:
    return [r for r in extraction.cells
            if (version is None or r.version == version) and (entity is None or r.entity == entity)
            and (line is None or r.line == line) and (period is None or r.period == period)]


def one(extraction, version, entity, line, period):
    found = rows_of(extraction, version, entity, line, period)
    assert len(found) == 1, (version, entity, line, period, len(found))
    return found[0]


def zip_copy(source: Path, target: Path, edit=None, comment: bytes = b"") -> Path:
    """A copy of a workbook, every part kept but those edit(name, data) changes."""
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as new:
        for info in old.infolist():
            data = old.read(info.filename)
            if edit is not None:
                data = edit(info.filename, data)
            new.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        new.comment = comment
    return target


def part(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name).decode("utf-8")


def core_text(path: Path, tag: str) -> str:
    return re.search(rf"<{tag}[^>]*>([^<]*)</{tag}>", part(path, "docProps/core.xml"))[1]


# -- The batch record -----------------------------------------------------------------

class TestTheBatchRecord:
    def test_the_samples_record(self, extract):
        batch = extracted(extract, SAMPLE).batch
        sha = file_sha256(SAMPLE)
        assert batch.batch_id == sha[:16] and batch.file_sha256 == sha
        assert batch.file_name == "planning_model.xlsx"
        assert batch.file_bytes == SAMPLE.stat().st_size
        assert batch.source == "workbook" and batch.has_formulas is True
        assert [(s.name, s.visibility) for s in batch.sheets] == [
            *[(t, "visible") for t in TABS], ("Scratch", "hidden")]
        assert (batch.creator, batch.last_modified_by) == (GROUP, GROUP)
        assert batch.calculation == extract.CALCULATED_BY_BUILD == "recalculated by the sample build"
        assert batch.external_links == ()

    def test_file_times_and_application_are_the_files_own_text(self, extract):
        # Facts about the file: these files say 5 November 2026. They are kept
        # as the file states them and never compared with the as-of date.
        batch = extracted(extract, SAMPLE).batch
        assert batch.created == core_text(SAMPLE, "dcterms:created")
        assert batch.modified == core_text(SAMPLE, "dcterms:modified")
        assert re.fullmatch(r"2026-11-05T\d\d:\d\d:\d\dZ", batch.modified)
        application = re.search(r"<Application>([^<]*)</Application>",
                                part(SAMPLE, "docProps/app.xml"))[1]
        assert batch.application == application
        assert batch.application.startswith("LibreOffice/26.8.2.2$Windows_X86_64")

    def test_the_extractor_never_reads_the_as_of_date(self):
        source = EXTRACT_SOURCE.read_text(encoding="utf-8")
        assert not re.search(r"as_of|AS_OF|2026-06-30|sample_model", source)

    def test_a_workbook_never_recalculated_is_as_received(self, extract):
        batch = extracted(extract, FIXTURES / "no_cache.xlsx").batch
        assert batch.calculation == extract.AS_RECEIVED == "as received"
        assert batch.application == "Microsoft Excel Compatible / Openpyxl 3.1.5"

    def test_a_changed_byte_makes_it_as_received(self, extract, tmp_path):
        copy = zip_copy(CLEAN, tmp_path / "planning_model_clean.xlsx", comment=b"changed")
        assert file_sha256(copy) != file_sha256(CLEAN)
        batch = extract.extract_workbook(copy).batch
        assert batch.calculation == extract.AS_RECEIVED
        assert batch.batch_id == file_sha256(copy)[:16]

    def test_without_the_manifest_it_is_as_received(self, extract, tmp_path):
        batch = extract.extract_workbook(CLEAN, manifest=tmp_path / "absent.json").batch
        assert batch.calculation == extract.AS_RECEIVED

    def test_the_external_link_target_is_recorded_as_text(self, extract):
        extraction = extracted(extract, FIXTURES / "external_link.xlsx")
        assert extraction.batch.external_links == ("calder_plan_fy2025.xlsx",)
        # Data only: it is in no row, so nothing built from the rows carries it.
        assert not any("calder_plan" in str(v) for r in extraction.cells
                       for v in dataclasses.asdict(r).values())
        linked = [f for f in extraction.formulas if f.formula == "=[1]Plan_ES!$O$33"]
        assert len(linked) == 1 and linked[0].sheet == "Control"
        assert linked[0].amount == Decimal(3104250) and linked[0].block is None

    def test_an_unreadable_file_is_one_plain_line(self, extract, tmp_path):
        bad = tmp_path / "not_a_workbook.xlsx"
        bad.write_bytes(b"entity,period\n")
        with pytest.raises(extract.ExtractError) as caught:
            extract.extract_workbook(bad)
        assert caught.value.kind == "unreadable" and "\n" not in str(caught.value)
        assert "not_a_workbook.xlsx" in str(caught.value)

    def test_a_part_with_a_document_type_is_refused(self, extract, tmp_path):
        # The workbook's own XML is read with the standard library only after a
        # part with a DTD (the route to entity expansion) has been refused.
        def edit(name, data):
            if name == "docProps/app.xml":
                return data.replace(b"<Properties", b'<!DOCTYPE x [<!ENTITY a "a">]><Properties', 1)
            return data
        copy = zip_copy(CLEAN, tmp_path / "doctype.xlsx", edit=edit)
        with pytest.raises(extract.ExtractError) as caught:
            extract.extract_workbook(copy)
        assert caught.value.kind == "unsafe_xml"


# -- The rows ---------------------------------------------------------------------------

class TestTheRows:
    def test_one_row_per_block_cell(self, extract):
        cells = extracted(extract, CLEAN).cells
        assert len(cells) == 3 * 3 * 13 * 13
        assert {(r.version, r.entity) for r in cells} == {(v, e) for v in VERSIONS
                                                         for e in ("ES", "AT", "DE")}
        assert {r.period for r in cells} == set(PERIODS) | {FY}
        assert {r.column_kind for r in cells if r.period == FY} == {"total"}
        assert {r.column_kind for r in cells if r.period != FY} == {"month"}

    def test_one_row_in_full(self, extract, model):
        from openpyxl import load_workbook
        wb = load_workbook(CLEAN)
        (sheet, ref), = wb.defined_names["Budget_ES"].destinations
        row = one(extracted(extract, CLEAN), "Budget", "ES", "Product sales", "2026-01")
        header_row = int(re.search(r"\d+", ref)[0])
        assert row.sheet == sheet == "Plan_ES" and row.address == f"C{header_row + 1}"
        assert row.batch_id == file_sha256(CLEAN)[:16]
        assert (row.block, row.account, row.unit, row.kind) == ("Budget_ES", "4000", "EUR", "formula")
        assert row.formula == wb[sheet][row.address].value and row.formula.startswith("=ROUND(")
        assert (row.cached, row.value_type, row.value_text) == (True, "number", None)
        assert row.amount == model.block("ES", "Budget")["Product sales"]["2026-01"]
        assert (row.source_line, row.row_error, row.duplicate) == (None, None, False)
        assert (row.unknown_entity, row.unknown_account, row.name_allowed) == (False, False, True)

    @pytest.mark.parametrize("path, plants, tieouts", [
        (CLEAN, frozenset(), False),
        (SAMPLE, frozenset({"D1", "D2", "D3", "D4", "D5"}), True)], ids=["clean", "sample"])
    def test_every_amount_is_the_models_figure(self, extract, model, path, plants, tieouts):
        for row in extracted(extract, path).cells:
            want = model.block(row.entity, row.version, plants, tieouts)[row.line][row.period]
            assert row.amount == want, (row.version, row.entity, row.line, row.period)

    def test_the_kinds_of_one_entity(self, extract):
        from collections import Counter
        cells = rows_of(extracted(extract, CLEAN), entity="AT")
        kinds = {v: Counter(r.kind for r in cells if r.version == v) for v in VERSIONS}
        assert kinds["Actual"] == {"constant": 66, "empty": 78, "formula": 25}
        assert kinds["Budget"] == kinds["Forecast"] == {"formula": 169}
        for r in cells:
            if r.kind == "empty":
                assert (r.formula, r.cached, r.value_type, r.amount) == (None, None, "none", None)
            if r.kind == "constant":
                assert r.formula is None and r.cached is None and r.value_type == "number"

    def test_every_amount_is_a_decimal_never_a_float(self, extract):
        for path in (SAMPLE, CLEAN, FIXTURES / "shifted.xlsx"):
            extraction = extracted(extract, path)
            for r in extraction.cells:
                assert r.amount is None or type(r.amount) is Decimal
            for f in extraction.formulas:
                assert f.amount is None or type(f.amount) is Decimal
            for t in extraction.control_totals:
                assert type(t.amount_sum) is Decimal

    def test_headcount_of_63_5_survives_exactly(self, extract):
        extraction = extracted(extract, CLEAN)
        for version in ("Actual", "Forecast"):
            amount = one(extraction, version, "ES", "Headcount", "2026-01").amount
            assert amount == Decimal("63.5") and str(amount) == "63.5"

    def test_typed_figures_survive_exactly(self, extract, tmp_path):
        # openpyxl reads a number with a decimal point as a float: the
        # extractor goes through its text, never Decimal(float). The large
        # whole amount has 15 digits, a spreadsheet number's precision: openpyxl
        # itself writes 9007199254740993 as 9007199254740992, a double.
        from openpyxl import load_workbook
        wb = load_workbook(CLEAN)
        typed = {"2026-01": 0.1, "2026-02": 123456789012345, "2026-03": 12345678901.25,
                 "2026-04": 63.5, "2026-05": 1490400}
        (sheet, ref), = wb.defined_names["Actual_ES"].destinations
        header = int(re.search(r"\d+", ref)[0])
        for i, value in enumerate(typed.values()):
            wb[sheet].cell(header + 1, 3 + i).value = value
        wb.save(tmp_path / "typed.xlsx")
        extraction = extract.extract_workbook(tmp_path / "typed.xlsx")
        got = {p: one(extraction, "Actual", "ES", "Product sales", p).amount for p in typed}
        assert got == {"2026-01": Decimal("0.1"), "2026-02": Decimal("123456789012345"),
                       "2026-03": Decimal("12345678901.25"), "2026-04": Decimal("63.5"),
                       "2026-05": Decimal("1490400")}
        assert str(got["2026-01"]) == "0.1"

    def test_the_conversion(self, extract):
        assert extract.to_decimal(0.1) == Decimal("0.1") != Decimal(0.1)
        assert extract.to_decimal(63.5) == Decimal("63.5")
        assert extract.to_decimal(9007199254740993) == Decimal("9007199254740993")
        assert extract.to_decimal(Decimal("1.10")) == Decimal("1.10")
        for value in (True, "1", None, float("nan"), float("inf")):
            with pytest.raises((TypeError, ValueError)):
                extract.to_decimal(value)

    def test_the_control_totals(self, extract):
        extraction = extracted(extract, CLEAN)
        totals = {(t.version, t.period): t for t in extraction.control_totals}
        assert set(totals) == {(v, p) for v in VERSIONS for p in PERIODS + (FY,)}
        for (version, period), total in totals.items():
            rows = rows_of(extraction, version=version, period=period)
            assert total.batch_id == extraction.batch.batch_id
            assert total.row_count == len(rows) == 3 * 13
            assert total.amount_sum == sum((r.amount for r in rows if r.amount is not None),
                                           Decimal(0))


# -- The anchors --------------------------------------------------------------------------

class TestTheAnchors:
    def test_blocks_are_found_by_their_defined_names(self, extract):
        blocks = extracted(extract, CLEAN).blocks
        assert [(b.name, b.sheet, b.version, b.entity, b.found_by) for b in blocks] == [
            (f"{v}_{e}", f"Plan_{e}", v, e, "defined name") for e in ("ES", "AT", "DE")
            for v in VERSIONS]
        assert all(b.title == b.version for b in blocks)

    def test_without_defined_names_the_header_labels_find_the_same_rows(self, extract, tmp_path):
        def strip(name, data):
            if name == "xl/workbook.xml":
                return re.sub(rb"<definedNames>.*?</definedNames>", b"", data, flags=re.DOTALL)
            return data
        copy = zip_copy(CLEAN, tmp_path / "no_names.xlsx", edit=strip)
        extraction = extract.extract_workbook(copy)
        assert extraction.defined_names == ()
        assert {b.found_by for b in extraction.blocks} == {"header labels"}
        assert extraction.lists.found_by == "header labels"
        assert [fields(r) for r in extraction.cells] == \
            [fields(r) for r in extracted(extract, CLEAN).cells]

    def test_shifted_extracts_to_the_clean_workbooks_rows(self, extract):
        """Every field is compared but the batch id, the address and the formula text.

        The shifted fixture is the clean workbook with every sheet's content
        moved three rows down and two columns right under a banner row, so the
        addresses and the references inside every formula move with it. The
        formulas are proved the same another way: with the dollar signs taken
        out, so that openpyxl's translator moves every reference, the clean
        formula translated from its cell to the shifted cell is the shifted
        formula, for each of the 1,089 block formulas.
        """
        from openpyxl.formula.translate import Translator
        clean = extracted(extract, CLEAN).cells
        shifted = extracted(extract, FIXTURES / "shifted.xlsx").cells
        assert len(shifted) == len(clean) == 1521
        assert [fields(r, MOVING) for r in shifted] == [fields(r, MOVING) for r in clean]
        translated = 0
        for c, s in zip(clean, shifted):
            assert s.address != c.address
            if c.formula is None:
                assert s.formula is None
                continue
            moved = Translator(c.formula.replace("$", ""), c.address).translate_formula(s.address)
            assert moved == s.formula.replace("$", ""), (c.address, s.address)
            translated += 1
        assert translated == 1089

    def test_months_are_found_by_their_headers(self, extract):
        extraction = extracted(extract, FIXTURES / "duplicate_period.xlsx")
        block = next(b for b in extraction.blocks if b.name == "Actual_ES")
        assert [(p.kind, p.header, p.columns) for p in block.header_problems] == [
            ("duplicate", "2026-07", ("I", "J"))]
        july = rows_of(extraction, "Actual", "ES", period="2026-07")
        assert len(july) == 26 and all(r.duplicate and r.kind == "empty" for r in july)
        assert rows_of(extraction, "Actual", "ES", period="2026-08") == []
        assert not any(r.duplicate for r in extraction.cells if r not in july)
        assert all(b.header_problems == () for b in extraction.blocks if b.name != "Actual_ES")

    def test_a_missing_header(self, extract):
        extraction = extracted(extract, FIXTURES / "missing_header.xlsx")
        block = next(b for b in extraction.blocks if b.name == "Budget_ES")
        assert [(p.kind, p.header, p.columns) for p in block.header_problems] == [
            ("missing", None, ("O",))]
        column = next(c for c in block.columns if c.letter == "O")
        assert (column.header, column.role, column.cells) == (None, "missing", 13)
        assert rows_of(extraction, "Budget", "ES", period=FY) == []
        assert len(rows_of(extraction, "Budget", "ES")) == 13 * 12

    def test_accounts_are_found_by_their_labels(self, extract):
        extraction = extracted(extract, CLEAN)
        for r in extraction.cells:
            assert r.account == CODES[LINES.index(r.line)]
        assert extraction.labels_not_on_lists == ()

    def test_a_label_not_on_lists(self, extract):
        extraction = extracted(extract, FIXTURES / "unknown_account.xlsx")
        (label,) = extraction.labels_not_on_lists
        assert (label.sheet, label.block, label.label) == ("Plan_ES", "Forecast_ES", "Cases sold")
        rows = rows_of(extraction, "Forecast", "ES", "Cases sold")
        assert len(rows) == 13 and {(r.account, r.unknown_account) for r in rows} == {(None, True)}
        assert all(r.address.endswith(label.address[1:]) for r in rows)
        assert not any(r.unknown_account for r in extraction.cells if r not in rows)


# -- The kinds of cell ----------------------------------------------------------------------

class TestTheKinds:
    def test_an_array_formula_is_its_own_kind(self, extract, model):
        extraction = extracted(extract, FIXTURES / "array_formula.xlsx")
        arrays = [r for r in extraction.cells if r.kind == "array_formula"]
        assert len(arrays) == 1
        (row,) = arrays
        assert (row.version, row.entity, row.line, row.period) == ("Budget", "ES", "Volume",
                                                                   "2026-05")
        assert row.formula.startswith("=") and row.cached is True
        assert row.amount == model.block("ES", "Budget")["Volume"]["2026-05"]
        assert not any(r.kind == "constant" for r in rows_of(extraction, "Budget"))
        (inventory,) = [f for f in extraction.formulas if f.kind == "array_formula"]
        assert (inventory.address, inventory.array_ref) == (row.address, row.address)

    def test_shared_formulas_extract_to_the_bases_rows(self, extract):
        shared = extracted(extract, FIXTURES / "shared_formula.xlsx").cells
        base = extracted(extract, FIXTURES / "extra_tab.xlsx").cells
        assert len(shared) == 3 * 13 * 13
        assert [fields(r) for r in shared] == [fields(r) for r in base]

    def test_a_typed_subtotal_is_a_constant(self, extract, model):
        typed = extracted(extract, FIXTURES / "typed_subtotal.xlsx").cells
        base = extracted(extract, FIXTURES / "extra_tab.xlsx").cells
        differ = [(t, b) for t, b in zip(typed, base) if fields(t) != fields(b)]
        assert len(typed) == len(base) and len(differ) == 1
        t, b = differ[0]
        assert (t.version, t.entity, t.line, t.period) == ("Actual", "ES", "EBIT", "2026-06")
        assert (t.kind, t.formula, t.cached) == ("constant", None, None)
        assert (b.kind, b.cached) == ("formula", True)
        assert t.amount == b.amount == model.block("ES", "Actual")["EBIT"]["2026-06"]

    def test_no_cache(self, extract):
        extraction = extracted(extract, FIXTURES / "no_cache.xlsx")
        formula_rows = [r for r in extraction.cells if r.kind == "formula"]
        assert len(formula_rows) == 363
        assert {(r.cached, r.value_type, r.amount) for r in formula_rows} == {(False, "none", None)}
        assert len(extraction.formulas) == 372
        assert {f.cached for f in extraction.formulas} == {False}
        assert sum(1 for f in extraction.formulas if f.sheet == "Control") == 9

    def test_an_error_value_is_recorded_with_no_amount(self, extract):
        extraction = extracted(extract, FIXTURES / "error_values.xlsx")
        errors = [f for f in extraction.formulas if f.value_type == "error"]
        assert len(errors) == 1
        (error,) = errors
        assert (error.sheet, error.value_text, error.amount, error.cached, error.block) == (
            "Control", "#DIV/0!", None, True, None)
        assert not any(r.value_type == "error" for r in extraction.cells)

    def test_every_formula_in_the_workbook_is_listed(self, extract):
        extraction = extracted(extract, CLEAN)
        assert len(extraction.formulas) == 1098
        assert {f.sheet for f in extraction.formulas} == {"Control", "Plan_ES", "Plan_AT",
                                                          "Plan_DE"}
        in_blocks = [f for f in extraction.formulas if f.block is not None]
        assert len(in_blocks) == sum(1 for r in extraction.cells if r.kind == "formula")
        assert all(f.cached for f in extraction.formulas)


# -- The facts for part 1d ---------------------------------------------------------------------

class TestTheFacts:
    def test_hidden_content_in_the_sample(self, extract):
        extraction = extracted(extract, SAMPLE)
        hidden = [(h.sheet, h.axis, h.index, h.cells, h.blocks) for s in extraction.sheets
                  for h in s.hidden_rows + s.hidden_columns]
        assert hidden == [("Plan_DE", "row", "53", ("A53", "B53", "L53"), ())]
        scratch = next(s for s in extraction.sheets if s.name == "Scratch")
        assert scratch.visibility == "hidden" and scratch.formulas == 0 and scratch.cells > 0
        assert not any(b.sheet == "Scratch" for b in extraction.blocks)

    def test_the_clean_workbook_hides_nothing(self, extract):
        extraction = extracted(extract, CLEAN)
        assert all(s.visibility == "visible" and s.hidden_rows == () and s.hidden_columns == ()
                   and s.merged == () for s in extraction.sheets)

    def test_merged_cells(self, extract):
        extraction = extracted(extract, FIXTURES / "merged_cells.xlsx")
        assert [(s.name, s.merged) for s in extraction.sheets if s.merged] == [
            ("Plan_ES", ("M7:N7",))]
        assert [(b.name, b.merged) for b in extraction.blocks if b.merged] == [
            ("Actual_ES", ("M7:N7",))]
        assert {r.kind for r in rows_of(extraction, "Actual", "ES", "Product sales")
                if r.period in ("2026-11", "2026-12")} == {"empty"}

    def test_units(self, extract):
        extraction = extracted(extract, FIXTURES / "no_units.xlsx")
        missing = [(b.name, line.label) for b in extraction.blocks for line in b.lines
                   if line.unit is None]
        assert missing == [("Budget_ES", "Rent and facilities")]
        assert {r.unit for r in rows_of(extraction, "Budget", "ES", "Rent and facilities")} == {None}
        lines = next(b for b in extracted(extract, CLEAN).blocks if b.name == "Budget_DE").lines
        assert [(line.label, line.unit, line.account) for line in lines] == [
            (name, "units" if name == "Volume" else "FTE" if name == "Headcount" else "EUR", code)
            for name, code in zip(LINES, CODES)]

    def test_the_defined_names(self, extract):
        names = {n.name: n.destinations for n in extracted(extract, CLEAN).defined_names}
        assert set(names) == {f"{v}_{e}" for v in VERSIONS for e in ("ES", "AT", "DE")} | {
            "Accounts", "Entities", "Drivers"}
        assert names["Budget_ES"] == ("Plan_ES!$A$22:$O$35",)

    def test_the_lists(self, extract, model):
        lists = extracted(extract, CLEAN).lists
        assert [(a.code, a.name, a.parent, a.section, a.polarity, a.unit, a.ledger_sign, a.kind)
                for a in lists.accounts] == [
            (line.code, line.name, line.parent or None, line.section, line.polarity, line.unit,
             line.ledger_sign or None, line.kind) for line in model.LINES]
        assert [(e.code, e.name, e.region) for e in lists.entities] == [
            (e.code, e.name, e.region) for e in model.ENTITIES]
        assert lists.found_by == "defined name"
        assert lists.ledger_accounts() == ("4000", "4100", "5000", "5100", "5200", "6000", "6100",
                                           "6200", "6300")
        assert lists.entity_codes() == ("ES", "AT", "DE")

    def test_a_missing_tab(self, extract):
        extraction = extracted(extract, FIXTURES / "missing_tab.xlsx")
        assert extraction.plan_sheets == (("Plan_ES", "ES"),)
        assert extraction.lists.entity_codes() == ("ES", "AT")
        assert [s.name for s in extraction.sheets] == list(SMALL_TABS)

    def test_an_extra_tab(self, extract):
        extraction = extracted(extract, FIXTURES / "extra_tab.xlsx")
        assert [(s.name, s.visibility) for s in extraction.batch.sheets] == [
            *[(t, "visible") for t in SMALL_TABS], ("Notes", "visible")]
        assert extraction.plan_sheets == (("Plan_ES", "ES"),)

    def test_rejected_names_are_recorded_and_landed_raw(self, extract):
        extraction = extracted(extract, FIXTURES / "names.xlsx")
        rejected = [(n.member, n.name, n.reasons, n.sheet) for n in extraction.rejected_names]
        assert rejected == [
            ("account", "Product sales; net of returns", ("characters",), "Lists"),
            ("account", "Verpackungsgemeinkosten Fertigungsgemeinkostenzuschlag "
                        "Verwaltungsgemeinkostenzuschlag Vertriebsgemeinkostenzuschlag Ist",
             ("length",), "Lists"),
            ("account", "Staff canteen tea and cakes for the night shift", ("words",), "Lists")]
        refused = {n.name for n in extraction.rejected_names}
        for r in extraction.cells:
            assert r.name_allowed is (r.line not in refused)
        assert {r.line for r in extraction.cells if not r.name_allowed} == refused
        assert sum(1 for r in extraction.cells if r.line in refused) == 3 * 3 * 13

    def test_the_sample_names_pass(self, extract):
        assert extracted(extract, SAMPLE).rejected_names == ()
        assert all(r.name_allowed for r in extracted(extract, SAMPLE).cells)

    def test_an_entity_not_on_lists(self, extract, tmp_path):
        def rename(name, data):
            if name == "xl/workbook.xml":
                return data.replace(b'name="Plan_AT"', b'name="Plan_FR"')
            return data
        copy = zip_copy(CLEAN, tmp_path / "plan_fr.xlsx", edit=rename)
        extraction = extract.extract_workbook(copy)
        assert extraction.plan_sheets == (("Plan_ES", "ES"), ("Plan_FR", "FR"), ("Plan_DE", "DE"))
        flagged = {r.entity for r in extraction.cells if r.unknown_entity}
        assert flagged == {"FR"}

    def test_the_extraction_holds_facts_and_no_judgement(self, extract):
        names = {f.name for cls in (extract.Extraction, extract.PlanCell, extract.BatchRecord)
                 for f in dataclasses.fields(cls)}
        assert not names & {"break", "breaks", "severity", "message", "finding", "findings"}
