"""The independent calculation of the sample workbook (src/sample_model.py), part 1b.

The model computes every figure of the Calder Foods Group sample from the
drivers, in pure Python with Decimal. The build has LibreOffice recalculate the
workbook's formulas and compares every cached value with these figures, so the
model must never read a workbook: it imports no spreadsheet library, and the
web layer never imports it. The figures checked by hand below were worked out
on paper from the drivers, not printed by the model.
"""

import ast
import re
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODEL_FILE = ROOT / "src" / "sample_model.py"
PERIODS = tuple(f"2026-{month:02d}" for month in range(1, 13))
CLOSED = PERIODS[:6]
MONETARY = ("Product sales", "Co-packing income", "Materials", "Packaging", "Freight out",
            "Salaries", "Marketing", "Rent and facilities", "IT and software")
REVENUE = ("Product sales", "Co-packing income")
COST_OF_SALES = ("Materials", "Packaging", "Freight out")
OPEX = ("Salaries", "Marketing", "Rent and facilities", "IT and software")
INPUTS = MONETARY + ("Volume", "Headcount")
# The member-name allow-list of brief 5.3, written out here: src/names.py
# arrives in part 1c, and the sample must already pass it.
ALLOWED_NAME = re.compile(r"^[\w &.,\-/()%]+$")


def passes_allow_list(name: str) -> bool:
    return (bool(ALLOWED_NAME.match(name)) and "_" not in name and len(name) <= 50
            and len(name.split()) <= 6 and name == name.strip())


@pytest.fixture(scope="module")
def model():
    from src import sample_model
    return sample_model


def _changed(before: dict, after: dict) -> set:
    return {(line, key) for line in before for key in before[line]
            if before[line][key] != after[line][key]}


class TestTheCompany:
    def test_the_company_and_the_calendar(self, model):
        assert model.COMPANY == "Calder Foods Group"
        assert (model.CURRENCY, model.FISCAL_YEAR, model.AS_OF) == ("EUR", "FY2026", "2026-06-30")
        assert model.PERIODS == PERIODS
        assert model.CLOSED_PERIODS == CLOSED
        assert model.VERSIONS == ("Actual", "Budget", "Forecast")

    def test_the_entities(self, model):
        assert [(e.code, e.name, e.region) for e in model.ENTITIES] == [
            ("ES", "Calder Foods Spain", "Southern Europe"),
            ("AT", "Calder Foods Austria", "Central Europe"),
            ("DE", "Calder Foods Germany", "Central Europe")]

    def test_no_name_from_an_earlier_project(self, model):
        text = MODEL_FILE.read_text(encoding="utf-8")
        for name in ("Valencia Operations", "Harmwell Foods"):
            assert name not in text

    def test_the_line_items(self, model):
        lines = {line.name: line for line in model.LINES}
        assert tuple(lines) == ("Product sales", "Co-packing income", "Materials", "Packaging",
                                "Freight out", "Gross profit", "Salaries", "Marketing",
                                "Rent and facilities", "IT and software", "EBIT", "Volume",
                                "Headcount")
        parents = {name: lines[name].parent for name in MONETARY}
        assert {parents[n] for n in REVENUE} == {"Revenue"}
        assert {parents[n] for n in COST_OF_SALES} == {"Cost of sales"}
        assert {parents[n] for n in OPEX} == {"Operating expenses"}
        assert len(set(line.code for line in model.LINES)) == 13
        for name in MONETARY:
            assert lines[name].unit == "EUR" and lines[name].kind == "Flow"
            assert re.fullmatch(r"\d{4}", lines[name].code)
        assert {lines[n].ledger_sign for n in REVENUE} == {"Credit"}
        assert {lines[n].ledger_sign for n in COST_OF_SALES + OPEX} == {"Debit"}
        for name in ("Gross profit", "EBIT"):
            assert (lines[name].section, lines[name].ledger_sign, lines[name].unit) == (
                "Subtotal", "", "EUR")
        assert (lines["Volume"].unit, lines["Volume"].kind) == ("units", "Flow")
        assert (lines["Headcount"].unit, lines["Headcount"].kind) == ("FTE", "Stock")
        for name in ("Volume", "Headcount"):
            assert (lines[name].section, lines[name].ledger_sign) == ("Statistics", "")

    def test_polarity_is_stated_for_every_line(self, model):
        polarity = {line.name: line.polarity for line in model.LINES}
        assert set(polarity.values()) == {"Favourable when higher", "Favourable when lower"}
        for name in REVENUE + ("Gross profit", "EBIT", "Volume"):
            assert polarity[name] == "Favourable when higher", name
        for name in COST_OF_SALES + OPEX + ("Headcount",):
            assert polarity[name] == "Favourable when lower", name

    def test_every_member_name_passes_the_allow_list(self, model):
        names = [line.name for line in model.LINES] + [e.name for e in model.ENTITIES]
        assert [n for n in names if not passes_allow_list(n)] == []

    def test_the_allow_list_written_here_is_the_brief_rule(self):
        assert passes_allow_list("Ignore the rules please")
        assert passes_allow_list("Logística y almacén")
        assert not passes_allow_list("Product sales; net of returns")
        assert not passes_allow_list("Staff canteen tea and cakes for the night shift")
        assert not passes_allow_list("x" * 51)
        assert not passes_allow_list(" Rent")


class TestTheDrivers:
    def test_the_drivers_of_the_scope_are_there(self, model):
        names = [driver.name for driver in model.DRIVERS]
        for name in ("Volume", "Price", "Unit cost", "Headcount", "Cost per head",
                     "Marketing share"):
            assert name in names
        assert len(names) == len(set(names))

    def test_twelve_decimal_values_per_version_entity_and_driver(self, model):
        for version in ("Budget", "Forecast"):
            for entity in model.ENTITIES:
                values = model.driver_values(version, entity.code)
                assert set(values) == {driver.name for driver in model.DRIVERS}
                for series in values.values():
                    assert len(series) == 12
                    assert all(type(v) is Decimal for v in series)

    def test_budget_and_forecast_differ_because_their_drivers_differ(self, model):
        for entity in model.ENTITIES:
            budget = model.driver_values("Budget", entity.code)
            forecast = model.driver_values("Forecast", entity.code)
            assert budget != forecast, entity.code
            assert model.block(entity.code, "Budget") != model.block(entity.code, "Forecast")


class TestTheFigures:
    def test_every_figure_is_a_decimal_and_money_is_whole_euros(self, model):
        for entity in model.ENTITIES:
            for version in model.VERSIONS:
                for name, cells in model.block(entity.code, version).items():
                    assert set(cells) == set(PERIODS) | {"FY2026"}
                    for value in cells.values():
                        assert value is None or type(value) is Decimal
                        if value is not None and name not in ("Volume", "Headcount"):
                            assert value == value.to_integral_value(), (name, value)

    def test_one_line_worked_by_hand(self, model):
        # ES Budget, January: 480,000 units a month times the seasonal 0.90 is
        # 432,000 units; at 3.45 that is 1,490,400 of product sales. With
        # 42,000 of co-packing income, revenue is 1,532,400, and marketing at
        # 4.5% of revenue is 68,958. 64 FTE at 3,850 is 246,400 of salaries.
        block = model.block("ES", "Budget")
        assert block["Volume"]["2026-01"] == Decimal("432000")
        assert block["Product sales"]["2026-01"] == Decimal("1490400")
        assert block["Co-packing income"]["2026-01"] == Decimal("42000")
        assert block["Marketing"]["2026-01"] == Decimal("68958")
        assert block["Salaries"]["2026-01"] == Decimal("246400")

    def test_gross_profit_and_ebit(self, model):
        for entity in model.ENTITIES:
            for version in model.VERSIONS:
                block = model.block(entity.code, version)
                for key in PERIODS + ("FY2026",):
                    def total(names):
                        return sum((block[n][key] or Decimal(0) for n in names), Decimal(0))
                    if block["Gross profit"][key] is None:
                        continue
                    assert block["Gross profit"][key] == total(REVENUE) - total(COST_OF_SALES)
                    assert block["EBIT"][key] == block["Gross profit"][key] - total(OPEX)

    def test_flows_sum_and_the_stock_takes_its_closing_value(self, model):
        for entity in model.ENTITIES:
            for version in model.VERSIONS:
                block = model.block(entity.code, version)
                for name in block:
                    months = [block[name][p] or Decimal(0) for p in PERIODS]
                    if name == "Headcount":
                        closing = "2026-06" if version == "Actual" else "2026-12"
                        assert block[name]["FY2026"] == block[name][closing]
                        assert block[name]["FY2026"] != sum(months)
                    else:
                        assert block[name]["FY2026"] == sum(months, Decimal(0)), name

    def test_the_actual_block_is_the_first_six_months_of_the_forecast(self, model):
        # The June forecast is six months of actuals and six of reforecast.
        for entity in model.ENTITIES:
            actual, forecast = model.block(entity.code, "Actual"), model.block(entity.code, "Forecast")
            for name in actual:
                for period in PERIODS:
                    expected = forecast[name][period] if period in CLOSED else None
                    assert actual[name][period] == expected, (entity.code, name, period)

    def test_a_rounding_tie_is_refused(self, model):
        assert model.round_whole(Decimal("68957.9")) == Decimal("68958")
        assert model.round_whole(Decimal("-12.4")) == Decimal("-12")
        with pytest.raises(ValueError):
            model.round_whole(Decimal("2.5"))

    def test_no_float_in_the_model(self):
        tree = ast.parse(MODEL_FILE.read_text(encoding="utf-8"))
        floats = [node.value for node in ast.walk(tree)
                  if isinstance(node, ast.Constant) and isinstance(node.value, float)]
        names = [node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
                 and node.id == "float"]
        assert floats == [] and names == []

    def test_the_group_summary(self, model):
        summary = model.group_summary(("ES", "AT", "DE"))
        for version in model.VERSIONS:
            blocks = [model.block(code, version) for code in ("ES", "AT", "DE")]
            assert summary[("Revenue", version)] == sum(
                b["Product sales"]["FY2026"] + b["Co-packing income"]["FY2026"] for b in blocks)
            assert summary[("EBIT", version)] == sum(b["EBIT"]["FY2026"] for b in blocks)


class TestThePlants:
    """Each planted fault changes its own cell and what the formulas carry from it, nothing else."""

    def test_d1_a_typed_number_in_a_budget_formula_row(self, model):
        clean, planted = model.block("AT", "Budget"), model.block("AT", "Budget", {"D1"})
        assert planted["Marketing"]["2026-03"] == Decimal("45000")
        assert clean["Marketing"]["2026-03"] != Decimal("45000")
        assert _changed(clean, planted) == {("Marketing", "2026-03"), ("EBIT", "2026-03"),
                                            ("Marketing", "FY2026"), ("EBIT", "FY2026")}

    def test_d2_a_total_one_month_short(self, model):
        clean, planted = model.block("DE", "Budget"), model.block("DE", "Budget", {"D2"})
        assert _changed(clean, planted) == {("Salaries", "FY2026")}
        assert clean["Salaries"]["FY2026"] - planted["Salaries"]["FY2026"] == \
            clean["Salaries"]["2026-12"]

    def test_d4_an_embedded_constant(self, model):
        clean, planted = model.block("ES", "Forecast"), model.block("ES", "Forecast", {"D4"})
        assert planted["Product sales"]["2026-09"] == model.round_whole(
            clean["Product sales"]["2026-09"] * Decimal("1.02"))
        assert _changed(clean, planted) == {
            (name, key) for name in ("Product sales", "Marketing", "Gross profit", "EBIT")
            for key in ("2026-09", "FY2026")}

    def test_d3_and_d5_change_no_block_figure(self, model):
        for entity in model.ENTITIES:
            for version in model.VERSIONS:
                assert model.block(entity.code, version, {"D3", "D5"}) == \
                    model.block(entity.code, version)

    def test_the_plants_touch_only_their_own_entity_and_version(self, model):
        everything = {"D1", "D2", "D3", "D4", "D5"}
        touched = {(e.code, v) for e in model.ENTITIES for v in model.VERSIONS
                   if model.block(e.code, v, everything) != model.block(e.code, v)}
        assert touched == {("AT", "Budget"), ("DE", "Budget"), ("ES", "Forecast")}


class TestTheTieOutBreaks:
    def test_t1_to_t3_change_only_the_actual_block_where_designed(self, model):
        changes = {}
        for entity in model.ENTITIES:
            clean = model.block(entity.code, "Actual")
            broken = model.block(entity.code, "Actual", tieouts=True)
            changes[entity.code] = {(n, k): (clean[n][k], broken[n][k])
                                    for n, k in _changed(clean, broken)}
        de = changes["DE"]
        assert {key for key in de if key[1] != "FY2026"} == {("Marketing", "2026-06"),
                                                            ("EBIT", "2026-06")}
        before, after = de[("Marketing", "2026-06")]
        assert before - after == Decimal("7350")
        es = changes["ES"]
        assert {key for key in es if key[1] != "FY2026"} == {("Rent and facilities", "2026-04"),
                                                            ("IT and software", "2026-04")}
        assert es[("Rent and facilities", "2026-04")][1] == \
            es[("Rent and facilities", "2026-04")][0] - Decimal("9800")
        assert es[("IT and software", "2026-04")][1] == \
            es[("IT and software", "2026-04")][0] + Decimal("9800")
        at = model.block("AT", "Actual", tieouts=True)
        for name in INPUTS:
            assert at[name]["2026-05"] is None, name
        assert at["Gross profit"]["2026-05"] == at["EBIT"]["2026-05"] == Decimal(0)

    def test_the_plan_blocks_are_untouched_by_the_tieout_breaks(self, model):
        for entity in model.ENTITIES:
            for version in ("Budget", "Forecast"):
                assert model.block(entity.code, version, tieouts=True) == \
                    model.block(entity.code, version)


class TestTheLedger:
    def test_the_ledger_is_the_clean_actual_in_ledger_sign(self, model):
        lines = model.ledger_lines()
        assert len(lines) == 3 * 6 * 9
        codes = {line.name: line.code for line in model.LINES}
        for entity, period, code, amount in lines:
            assert type(amount) is Decimal and period in CLOSED
            name = next(n for n, c in codes.items() if c == code)
            presented = model.block(entity, "Actual")[name][period]
            sign = Decimal(-1) if name in REVENUE else Decimal(1)
            assert amount == presented * sign, (entity, period, code)
        assert all(amount < 0 for _, _, code, amount in lines if code in ("4000", "4100"))


class TestTheBoundary:
    """The model is the independent calculation: it never touches a workbook or the web layer."""

    def test_the_model_imports_no_spreadsheet_library(self):
        tree = ast.parse(MODEL_FILE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        assert "openpyxl" not in imported
        assert imported <= {"__future__", "dataclasses", "decimal", "typing", "collections"}

    def test_the_web_layer_never_imports_the_model(self):
        importers = [path.relative_to(ROOT).as_posix()
                     for path in sorted((ROOT / "streamlit_app").rglob("*.py"))
                     if imports_the_model(path.read_text(encoding="utf-8"))]
        assert importers == []

    def test_the_import_check_sees_every_form_of_import(self):
        for source in ("from src import sample_model\n", "import src.sample_model as m\n",
                       "from src.sample_model import block\n",
                       "def f():\n    from src import sample_model\n"):
            assert imports_the_model(source), source
        assert not imports_the_model("from src import checks\nimport openpyxl\n")


def imports_the_model(source: str) -> bool:
    """True when source imports src/sample_model.py in any form, at any depth."""
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [f"{node.module}.{alias.name}" for alias in node.names]
        if any("sample_model" in name for name in names):
            return True
    return False
