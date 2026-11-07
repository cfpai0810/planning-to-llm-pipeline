"""The independent oracle (tests/oracle.py), part 2b, brief 4.3.

The oracle computes every metric of the registry for every question of the grid, by the
finance rules of brief 4.2, before any compiler exists and without importing the registry,
the warehouse, the extractor, the star schema, a compiler or the SQL files. It builds a
pandas DataFrame of the plan's figures: for the clean pair from src/sample_model.py, the
pure-Python Decimal figures that prove the workbook; for the sample pair from the committed
sample workbook's cached values, read with openpyxl at fixed addresses (allowed here and
only here). Its results are committed as tests/generated/oracle_expected.csv, so the
figures can be read without running anything, and a test regenerates the file and
compares, so the two cannot drift apart.

Two proofs that the oracle itself is right: its Gross profit and EBIT equal the workbook's
own subtotal cells in every month, for all three versions and all three entities, on the
clean pair and on the sample pair; and the scope's ratio-of-sums example, 32.0% and not
40%, on a tiny frame built by hand. The rules the reviewer left open follow the
coordinator's leans, stated in the oracle's docstring and on the stop page.

Every expectation below is computed by a second route (the model's FY totals, the
workbook's cells) or written by hand, never read from the oracle's own file.
"""

import csv
import io
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from src import sample_model as model

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / "tests" / "generated" / "oracle_expected.csv"
SAMPLE = ROOT / "data" / "planning_model.xlsx"
CLEAN = ROOT / "data" / "planning_model_clean.xlsx"
HEADER = ["pair", "metric", "version", "comparison", "entities", "period", "group_by", "group",
          "outcome", "value", "comparator", "variance", "variance_pct", "label", "months_asked",
          "months_held", "gaps"]
METRICS = ("revenue", "cost_of_sales", "gross_profit", "operating_expenses", "ebit",
           "gross_margin_pct", "ebit_margin_pct", "marketing_pct_of_revenue", "volume",
           "headcount", "revenue_per_unit")
ENTITIES = ("ES", "AT", "DE")
TWELVE = Decimal("0.000000000001")
REVENUE = ("Product sales", "Co-packing income")
COST = ("Materials", "Packaging", "Freight out")
OPEX = ("Salaries", "Marketing", "Rent and facilities", "IT and software")


@pytest.fixture(scope="module")
def oracle():
    from tests import oracle
    return oracle


@pytest.fixture(scope="module")
def clean(oracle):
    return oracle.prepare(oracle.clean_frame())


@pytest.fixture(scope="module")
def sample(oracle):
    return oracle.prepare(oracle.sample_frame())


def ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    """The exact quotient, quantised to 12 places, ties away from zero: a second route."""
    exact = Fraction(numerator) / Fraction(denominator)
    return Decimal(round_half_away(exact * 10 ** 12)) * TWELVE


def round_half_away(value: Fraction) -> int:
    whole, rest = divmod(abs(value.numerator), value.denominator)
    whole += 2 * rest >= value.denominator
    return whole if value >= 0 else -whole


def fy(entity: str, version: str, names) -> Decimal:
    """A version's FY2026 total of some lines for one entity, from the model's FY column."""
    block = model.block(entity, version)
    return sum((block[n][model.FY] for n in names), Decimal(0))


def months(entity: str, version: str, names, periods) -> Decimal:
    block = model.block(entity, version)
    return sum((block[n][p] or Decimal(0) for n in names for p in periods), Decimal(0))


# -- The frames ----------------------------------------------------------------------------------

class TestTheFrames:
    def test_the_clean_frame_is_the_sample_models_figures(self, oracle):
        import pandas
        frame = oracle.clean_frame()
        assert isinstance(frame, pandas.DataFrame)
        assert list(frame.columns) == ["version", "entity", "region", "account", "name",
                                       "parent", "section", "kind", "unit", "period", "amount"]
        # Actual: six months of thirteen lines; Budget and Forecast: twelve. Every figure.
        assert len(frame) == 3 * 13 * (6 + 12 + 12) == 1170
        assert all(isinstance(a, Decimal) for a in frame["amount"])
        row = frame[(frame.version == "Budget") & (frame.entity == "AT")
                    & (frame.account == "6100") & (frame.period == "2026-03")]
        assert list(row["amount"]) == [model.block("AT", "Budget")["Marketing"]["2026-03"]]

    def test_the_sample_frame_is_the_committed_workbooks_cached_values(self, oracle):
        from openpyxl import load_workbook
        frame = oracle.sample_frame()
        # T3: the AT May Actual inputs are empty; the subtotals there hold 0.
        assert len(frame) == 1170 - 11
        ws = load_workbook(SAMPLE, data_only=True)["Plan_AT"]
        row = frame[(frame.version == "Budget") & (frame.entity == "AT")
                    & (frame.account == "6100") & (frame.period == "2026-03")]
        assert list(row["amount"]) == [Decimal(45000)] == [Decimal(str(ws["E30"].value))]
        missing = frame[(frame.version == "Actual") & (frame.entity == "AT")
                        & (frame.period == "2026-05")]
        assert sorted(missing["account"]) == ["EBIT", "GP"]
        assert set(missing["amount"]) == {Decimal(0)}

    def test_the_sample_frame_reads_its_lists_from_the_workbook(self, oracle):
        frame = oracle.sample_frame()
        lines = frame.drop_duplicates("account")[["account", "name", "parent", "section", "kind",
                                                  "unit"]]
        assert [tuple(r) for r in lines.itertuples(index=False)] == [
            (l.code, l.name, l.parent, l.section, l.kind, l.unit) for l in model.LINES]
        regions = dict(frame.drop_duplicates("entity")[["entity", "region"]].itertuples(
            index=False))
        assert regions == {e.code: e.region for e in model.ENTITIES}

    def test_the_two_frames_differ_only_where_the_sample_was_changed(self, oracle):
        def figures(frame):
            return {(r.version, r.entity, r.account, r.period): r.amount
                    for r in frame.itertuples(index=False)}
        c, s = figures(oracle.clean_frame()), figures(oracle.sample_frame())
        differ = {k for k in set(c) | set(s) if c.get(k) != s.get(k)}
        # D1, D4 and its knock-on lines, and T1 to T3 with theirs; D2 is an FY cell, which
        # the frames do not hold, D3 and D5 are outside every block.
        assert {(v, e) for v, e, _, _ in differ} == {("Budget", "AT"), ("Forecast", "ES"),
                                                    ("Actual", "DE"), ("Actual", "ES"),
                                                    ("Actual", "AT")}
        assert {k for k in differ if k[0] == "Actual" and k[1] == "AT"} == {
            ("Actual", "AT", line.code, "2026-05") for line in model.LINES}


# -- The finance rules ---------------------------------------------------------------------------

class TestTheRules:
    def test_a_flow_sums_its_months(self, oracle, clean):
        a = oracle.ask(clean, "revenue", "Budget", "group", ("fy", "FY2026"), "none", "none")
        assert a.outcome == "ok" and len(a.rows) == 1
        assert a.rows[0].value == sum(fy(e, "Budget", REVENUE) for e in ENTITIES)

    def test_a_difference_is_first_less_the_others(self, oracle, clean):
        a = oracle.ask(clean, "ebit", "Budget", "ES", ("fy", "FY2026"), "none", "none")
        assert a.rows[0].value == fy("ES", "Budget", ("EBIT",))

    def test_a_stock_is_its_closing_value_and_sums_across_entities(self, oracle, clean):
        a = oracle.ask(clean, "headcount", "Budget", "group", ("fy", "FY2026"), "none", "none")
        december = sum(model.block(e, "Budget")["Headcount"]["2026-12"] for e in ENTITIES)
        summed = sum(model.block(e, "Budget")["Headcount"][p] for e in ENTITIES
                     for p in model.PERIODS)
        assert a.rows[0].value == december != summed
        assert a.closing == "2026-12"
        actual = oracle.ask(clean, "headcount", "Actual", "group", ("fy", "FY2026"), "none",
                            "none")
        assert actual.closing == "2026-06"
        assert actual.rows[0].value == sum(model.block(e, "Actual")["Headcount"]["2026-06"]
                                           for e in ENTITIES)

    def test_a_stock_by_quarter_closes_each_quarter(self, oracle, clean):
        a = oracle.ask(clean, "headcount", "Forecast", "DE", ("fy", "FY2026"), "quarter", "none")
        assert [(r.group, r.value) for r in a.rows] == [
            (q, model.block("DE", "Forecast")["Headcount"][f"2026-{m:02d}"])
            for q, m in (("Q1", 3), ("Q2", 6), ("Q3", 9), ("Q4", 12))]

    def test_a_ratio_is_the_ratio_of_sums_never_the_average_of_ratios(self, oracle, clean):
        group = oracle.ask(clean, "gross_margin_pct", "Budget", "group", ("fy", "FY2026"),
                           "none", "none").rows[0].value
        gross = sum(fy(e, "Budget", ("Gross profit",)) for e in ENTITIES)
        revenue = sum(fy(e, "Budget", REVENUE) for e in ENTITIES)
        assert group == ratio(gross, revenue)
        each = [oracle.ask(clean, "gross_margin_pct", "Budget", e, ("fy", "FY2026"), "none",
                           "none").rows[0].value for e in ENTITIES]
        assert group != sum(each) / 3

    def test_the_inline_operand_of_the_marketing_ratio(self, oracle, clean):
        a = oracle.ask(clean, "marketing_pct_of_revenue", "Forecast", "AT", ("quarter", "Q2"),
                       "none", "none")
        q2 = ("2026-04", "2026-05", "2026-06")
        assert a.rows[0].value == ratio(months("AT", "Forecast", ("Marketing",), q2),
                                        months("AT", "Forecast", REVENUE, q2))

    def test_revenue_per_unit(self, oracle, clean):
        a = oracle.ask(clean, "revenue_per_unit", "Actual", "group", ("ytd", "2026-06"), "none",
                       "none")
        h1 = model.CLOSED_PERIODS
        assert a.rows[0].value == ratio(
            sum(months(e, "Actual", REVENUE, h1) for e in ENTITIES),
            sum(months(e, "Actual", ("Volume",), h1) for e in ENTITIES))

    def test_a_window_with_no_held_month_is_no_data(self, oracle, clean):
        a = oracle.ask(clean, "revenue", "Actual", "group", ("quarter", "Q3"), "none", "none")
        assert (a.outcome, a.rows, a.gaps) == ("no_data", (), ())
        assert a.months_asked == ("2026-07", "2026-08", "2026-09") and a.months_held == ()

    def test_months_after_the_last_held_month_are_not_gaps(self, oracle, clean):
        a = oracle.ask(clean, "revenue", "Actual", "group", ("fy", "FY2026"), "none", "none")
        assert a.months_held == model.CLOSED_PERIODS and a.gaps == ()

    def test_the_at_may_actual_gap_is_reported_on_the_sample(self, oracle, sample):
        a = oracle.ask(sample, "revenue", "Actual", "group", ("month", "2026-05"), "none", "none")
        assert a.outcome == "ok" and a.gaps == (("AT", "2026-05"),)
        sample_wb = oracle.sample_frame()
        may = sample_wb[(sample_wb.version == "Actual") & (sample_wb.period == "2026-05")
                        & sample_wb.parent.eq("Revenue")]
        assert a.rows[0].value == sum(may["amount"]) and set(may["entity"]) == {"ES", "DE"}
        alone = oracle.ask(sample, "revenue", "Actual", "AT", ("month", "2026-05"), "none",
                           "none")
        assert (alone.outcome, alone.gaps) == ("no_data", (("AT", "2026-05"),))

    def test_a_grouped_row_with_no_held_month_is_left_out_and_listed(self, oracle, sample):
        a = oracle.ask(sample, "revenue", "Actual", "group", ("month", "2026-05"), "entity",
                       "none")
        assert [r.group for r in a.rows] == ["ES", "DE"] and a.gaps == (("AT", "2026-05"),)
        b = oracle.ask(sample, "revenue", "Actual", "AT", ("ytd", "2026-06"), "month", "none")
        assert [r.group for r in b.rows] == ["2026-01", "2026-02", "2026-03", "2026-04",
                                             "2026-06"]

    def test_a_comparison_uses_the_months_both_versions_hold(self, oracle, clean):
        a = oracle.ask(clean, "revenue", "Actual", "group", ("fy", "FY2026"), "none", "budget")
        assert a.months_held == model.CLOSED_PERIODS
        (row,) = a.rows
        h1 = model.CLOSED_PERIODS
        assert row.value == sum(months(e, "Actual", REVENUE, h1) for e in ENTITIES)
        assert row.comparator == sum(months(e, "Budget", REVENUE, h1) for e in ENTITIES)
        assert row.comparator != sum(fy(e, "Budget", REVENUE) for e in ENTITIES)

    def test_matched_by_entity_month_a_gap_leaves_both_sides(self, oracle, sample):
        # The lean, question 5 on the stop page: AT's May is left out of the Budget side too.
        a = oracle.ask(sample, "revenue", "Actual", "group", ("month", "2026-05"), "none",
                       "budget")
        (row,) = a.rows
        assert row.comparator == sum(months(e, "Budget", REVENUE, ("2026-05",))
                                     for e in ("ES", "DE"))
        assert a.gaps == (("AT", "2026-05"),)

    def test_the_variance_its_label_and_its_percentage(self, oracle, clean):
        a = oracle.ask(clean, "ebit", "Actual", "group", ("ytd", "2026-06"), "none", "budget")
        (row,) = a.rows
        h1 = model.CLOSED_PERIODS
        actual = sum(months(e, "Actual", ("EBIT",), h1) for e in ENTITIES)
        budget = sum(months(e, "Budget", ("EBIT",), h1) for e in ENTITIES)
        assert (row.value, row.comparator, row.variance) == (actual, budget, actual - budget)
        assert row.variance_pct == ratio(actual - budget, abs(budget))
        assert row.label == ("favourable" if actual > budget else "adverse")

    def test_a_cost_increase_is_adverse(self, oracle, clean):
        a = oracle.ask(clean, "cost_of_sales", "Forecast", "group", ("fy", "FY2026"), "none",
                       "budget")
        (row,) = a.rows
        assert row.variance == row.value - row.comparator
        assert row.label == ("adverse" if row.variance > 0 else "favourable")

    def test_actual_against_forecast_is_no_variance_in_a_closed_month(self, oracle, clean):
        a = oracle.ask(clean, "revenue", "Actual", "ES", ("month", "2026-02"), "none", "forecast")
        (row,) = a.rows
        assert (row.variance, row.label, row.variance_pct) == (0, "no variance", Decimal(0))

    def test_a_percentage_ratios_variance_is_in_percentage_points(self, oracle, clean):
        a = oracle.ask(clean, "gross_margin_pct", "Forecast", "group", ("fy", "FY2026"), "none",
                       "budget")
        (row,) = a.rows
        assert row.variance == (row.value - row.comparator) * 100
        assert row.variance_pct is None

    def test_headcount_is_ungraded(self, oracle, clean):
        a = oracle.ask(clean, "headcount", "Forecast", "group", ("fy", "FY2026"), "none",
                       "budget")
        assert a.rows[0].label == "" and a.rows[0].variance != 0

    def test_a_zero_denominator_is_not_meaningful(self, oracle):
        frame = oracle.frame_from([("Budget", "A", "R", "4000", "Revenue", "2026-01", "0"),
                                   ("Budget", "A", "R", "5000", "Cost of sales", "2026-01", "10"),
                                   ("Forecast", "A", "R", "4000", "Revenue", "2026-01", "5"),
                                   ("Forecast", "A", "R", "5000", "Cost of sales", "2026-01",
                                    "0")])
        data = oracle.prepare(frame)
        a = oracle.ask(data, "gross_margin_pct", "Budget", "group", ("month", "2026-01"), "none",
                       "none")
        assert a.rows[0].value is oracle.NOT_MEANINGFUL
        b = oracle.ask(data, "revenue", "Forecast", "group", ("month", "2026-01"), "none",
                       "budget")
        assert (b.rows[0].variance, b.rows[0].variance_pct) == (5, oracle.NOT_MEANINGFUL)
        assert oracle.NOT_MEANINGFUL == "not meaningful"


# -- The two proofs ------------------------------------------------------------------------------

class TestTheProofs:
    @pytest.mark.parametrize("pair", ["clean", "sample"])
    def test_gross_profit_and_ebit_equal_the_workbook_subtotal_cells(self, oracle, pair):
        proof = oracle.subtotal_proof(pair)
        # Every entity-month a version holds, both subtotals; the Actual block holds six
        # months, Budget and Forecast twelve; the sample's AT May Actual is not held.
        held = 3 * (6 + 12 + 12) - (1 if pair == "sample" else 0)
        assert proof.compared == 2 * held and proof.mismatches == ()
        # Where a version holds no figure, the workbook's subtotal cells hold nothing, or
        # the 0 their formulas give over empty inputs (the sample's AT May Actual).
        assert proof.not_held == (18 if pair == "clean" else 19)
        assert proof.not_held_nonzero == ()

    def test_the_ratio_of_sums_is_32_per_cent_not_40(self, oracle):
        frame = oracle.frame_from([
            ("Budget", "A", "One", "4000", "Revenue", "2026-01", "100"),
            ("Budget", "A", "One", "5000", "Cost of sales", "2026-01", "50"),
            ("Budget", "B", "One", "4000", "Revenue", "2026-01", "900"),
            ("Budget", "B", "One", "5000", "Cost of sales", "2026-01", "630")])
        data = oracle.prepare(frame)
        group = oracle.ask(data, "gross_margin_pct", "Budget", "group", ("month", "2026-01"),
                           "none", "none")
        by_entity = oracle.ask(data, "gross_margin_pct", "Budget", "group", ("month", "2026-01"),
                               "entity", "none")
        assert group.rows[0].value == Decimal("0.320000000000")
        assert [(r.group, r.value) for r in by_entity.rows] == [
            ("A", Decimal("0.500000000000")), ("B", Decimal("0.300000000000"))]
        assert sum(r.value for r in by_entity.rows) / 2 == Decimal("0.4")
        assert oracle.percent(group.rows[0].value) == "32.0%"


# -- The grid and the committed file -------------------------------------------------------------

class TestTheGrid:
    def test_the_oracles_metrics_are_the_registrys(self, oracle):
        from src import registry
        assert oracle.METRICS == METRICS == tuple(m.id for m in registry.load().metrics)

    def test_the_committed_file_is_what_the_oracle_writes_now(self, oracle):
        assert EXPECTED.read_bytes() == oracle.render().encode("utf-8")

    def test_the_header_and_the_questions(self):
        rows = list(csv.reader(io.StringIO(EXPECTED.read_text(encoding="utf-8"))))
        assert rows[0] == HEADER
        questions = {tuple(r[:7]) for r in rows[1:]}
        # 11 metrics, 7 version and comparison pairs (a comparison never equals the
        # version), 4 entity sets, 18 periods, 5 groupings or 6 for the three metrics that
        # may be grouped by account; on each pair.
        assert len(questions) == 2 * 7 * 4 * 18 * (11 * 5 + 3)
        assert {r[0] for r in rows[1:]} == {"clean", "sample"}
        assert {r[8] for r in rows[1:]} == {"ok", "no_data"}

    def test_the_file_is_lf_and_under_ten_megabytes(self):
        data = EXPECTED.read_bytes()
        assert b"\r" not in data and data.endswith(b"\n")
        assert len(data) < 10 * 1024 * 1024

    def test_the_formatting(self, oracle):
        assert oracle.percent(Decimal("0.32")) == "32.0%"
        assert oracle.percent(Decimal("0.123450000000")) == "12.3%"
        assert oracle.percent(Decimal("0.12350")) == "12.4%"
        assert oracle.percent(Decimal("-0.12350")) == "-12.4%"
        assert oracle.euros(Decimal("1234567")) == "1,234,567"
        assert oracle.per_unit(Decimal("3.455000000000")) == "3.46"
