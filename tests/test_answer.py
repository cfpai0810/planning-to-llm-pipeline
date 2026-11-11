"""Execution: session.answer (part 2d, the reviewer's brief 6.2, 6.5 and section 8).

session.answer(spec_or_json, role) reads the specification, validates it, compiles it (the
compiler checks its own output with the guard), asks DuckDB's own parser that the text is one
SELECT, and runs it on a cursor of the session's read-only connection, under the read lock,
closed after. It returns an Answer, or a typed outcome: no_data carries its coverage and gaps,
and any error of the engine is engine_error, in fixed wording, with no SQL echoed.

The synthetic star is built here by INSERTs into the star tables of an empty warehouse, with
no workbook: two entities with the scope's 100 and 50, 900 and 270 figures (32.0%, not 40%),
a headcount series (closing in time, summed across entities), a blank Materials cell (a gap
for cost of sales and none for revenue), Gross profit rows that hold nonsense and are never
read, and a revenue of 2 times 10 to the 14 that the integer route cannot scale.

Role leakage: an entity controller's answers hold that entity's figures alone, equal to the
oracle's for that entity, and no SQL, row, coverage, outcome or provenance holds another
entity's code, name or region (searched as text). A TEMP table named fact_plan made on one
cursor never reaches an answer (Trap 2).

Commit 3a (the reviewer's verdict of 11 November, F22): on the clean pair with one Budget cell
deleted in each metric that sums several accounts, a filter on accounts chooses what is
summed and nothing else, so the slices add up to the total and the gaps do not move.
"""

import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ORACLE = ROOT / "tests" / "generated" / "oracle_expected.csv"
NOON = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
FY = tuple(f"2026-{m:02d}" for m in range(1, 13))
H1 = FY[:6]
ENTITIES = {"ES": ("Calder Foods Spain", "Southern Europe"),
            "AT": ("Calder Foods Austria", "Central Europe"),
            "DE": ("Calder Foods Germany", "Central Europe")}


def ask(metric="revenue", version="Budget", dimensions=(), filters=(), period=("fy", "FY2026"),
        comparison="none", sort=None, limit=100) -> dict:
    return {"metric": metric, "version": version, "dimensions": list(dimensions),
            "filters": [{"dimension": d, "values": list(v)} for d, v in filters],
            "period": {"kind": period[0], "value": period[1]}, "comparison": comparison,
            "sort": None if sort is None else {"by": sort[0], "direction": sort[1]},
            "limit": limit}


@pytest.fixture(scope="module")
def pool(template, tmp_path_factory):
    from src import session
    sessions = session.Sessions(template, root=tmp_path_factory.mktemp("answer"))
    yield sessions
    sessions.close_all()


@pytest.fixture(scope="module")
def clean(pool):
    s = pool.open()
    s.promote("clean", "P. Promoter", clock=lambda: NOON)
    return s


@pytest.fixture(scope="module")
def sample(pool):
    s = pool.open()
    s.acknowledge_many("sample", [(b.break_id, "A. Reviewer", "accepted")
                                  for b in s.breaks("sample")], clock=lambda: NOON)
    s.promote("sample", "P. Promoter", clock=lambda: NOON)
    return s


@pytest.fixture(scope="module")
def expected():
    """The oracle's committed rows, by (pair, metric, version, comparison, entities, period,
    group_by)."""
    found = defaultdict(list)
    with ORACLE.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            found[(row["pair"], row["metric"], row["version"], row["comparison"],
                   row["entities"], row["period"], row["group_by"])].append(row)
    return found


def group_role():
    from src import roles
    return roles.group_controller


def figures(answer) -> list:
    """Each row's group (or "") and value as a Decimal or the marker."""
    names = [c[0] for c in answer.columns]
    out = []
    for row in answer.rows:
        cells = dict(zip(names, row))
        dims = [cells[n] for n, kind, _ in answer.columns if kind == "dimension"]
        value = cells["value"]
        out.append((" ".join(dims), value if value == "not meaningful" else Decimal(value)))
    return out


# -- The answer ---------------------------------------------------------------------------------

class TestTheAnswer:
    def test_fy_budget_revenue_for_the_group(self, clean, expected):
        from src import answer as answer_module
        a = clean.answer(ask(), group_role())
        assert isinstance(a, answer_module.Answer) and a.outcome is None
        (row,) = expected[("clean", "revenue", "Budget", "none", "group", "FY2026", "none")]
        assert figures(a) == [("", Decimal(row["value"]))]
        assert a.columns == (("value", "flow", "EUR"),)
        assert (a.row_count, a.truncated) == (1, False)
        assert a.coverage.months_asked == FY and a.coverage.months_held == FY
        assert a.coverage.compared_with is None and a.gaps == ()
        assert a.sql.startswith("WITH plan AS (") and "fact_plan" in a.sql
        assert a.sql_sha256 == hashlib.sha256(a.sql.encode("utf-8")).hexdigest()
        assert a.result_sha256 == hashlib.sha256(json.dumps(
            [list(r) for r in a.rows], ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8")).hexdigest()
        assert a.provenance == clean.provenance(group_role())
        assert a.provenance["view"] == "group view"
        assert a.spec.metric == "revenue" and a.spec.version == "Budget"

    def test_rows_carry_decimals_as_text(self, clean):
        a = clean.answer(ask(metric="gross_margin_pct", dimensions=("entity",)), group_role())
        for row in a.rows:
            assert all(isinstance(cell, str) for cell in row)
            assert re.fullmatch(r"-?\d+\.\d{12}", row[1])

    def test_the_specification_as_json_text(self, clean):
        a = clean.answer(json.dumps(ask()), group_role())
        assert a.outcome is None and a.row_count == 1

    def test_a_malformed_specification_is_an_outcome(self, clean):
        result = clean.answer("{not json", group_role())
        assert result.code == "specification_invalid"

    def test_nothing_promoted(self, pool):
        result = pool.open().answer(ask(), group_role())
        assert (result.code, result.sentence) == (
            "nothing_promoted", "Nothing has been promoted yet. Review a batch first.")

    def test_truncated_fetches_one_more_than_the_limit(self, clean):
        a = clean.answer(ask(dimensions=("month",), limit=5), group_role())
        assert (a.row_count, a.truncated) == (5, True)
        assert [g for g, _ in figures(a)] == list(FY[:5])
        full = clean.answer(ask(dimensions=("month",), limit=12), group_role())
        assert (full.row_count, full.truncated) == (12, False)

    def test_sorted_by_value(self, clean):
        a = clean.answer(ask(dimensions=("entity",), sort=("value", "desc")), group_role())
        values = [v for _, v in figures(a)]
        assert values == sorted(values, reverse=True) and len(values) == 3
        b = clean.answer(ask(dimensions=("entity",), sort=("entity", "desc")), group_role())
        assert [g for g, _ in figures(b)] == ["DE", "AT", "ES"]

    def test_no_held_month_is_no_data_never_a_zero(self, clean):
        a = clean.answer(ask(version="Actual", period=("quarter", "Q3")), group_role())
        assert (a.outcome.code, a.rows, a.gaps) == ("no_data", (), ())
        assert a.coverage.months_asked == ("2026-07", "2026-08", "2026-09")
        assert a.coverage.months_held == ()

    def test_the_at_may_actual_gap(self, sample, expected):
        a = sample.answer(ask(version="Actual", period=("month", "2026-05")), group_role())
        (row,) = expected[("sample", "revenue", "Actual", "none", "group", "2026-05", "none")]
        assert figures(a) == [("", Decimal(row["value"]))] and a.gaps == (("AT", "2026-05"),)
        alone = sample.answer(ask(version="Actual", period=("month", "2026-05"),
                                  filters=(("entity", ("AT",)),)), group_role())
        assert (alone.outcome.code, alone.gaps) == ("no_data", (("AT", "2026-05"),))

    def test_actual_against_budget_for_the_year_compares_january_to_june(self, clean,
                                                                          expected):
        a = clean.answer(ask(version="Actual", comparison="budget", metric="ebit"),
                         group_role())
        assert a.coverage.compared_with == "Budget" and a.coverage.months_held == H1
        (row,) = expected[("clean", "ebit", "Actual", "budget", "group", "FY2026", "none")]
        names = [c[0] for c in a.columns]
        assert names == ["value", "comparator", "variance", "variance_pct", "label"]
        assert [Decimal(x) for x in a.rows[0][:4]] == [Decimal(row[k]) for k in (
            "value", "comparator", "variance", "variance_pct")]
        assert a.rows[0][4] == row["label"]

    def test_an_entity_controller_whose_entity_the_pair_lacks_is_refused_first(
            self, clean, monkeypatch):
        from src import compiler, roles

        def never(*args, **kwargs):
            raise AssertionError("nothing may be compiled")
        monkeypatch.setattr(compiler, "compile", never)
        for entity in ("E S", "FR"):
            result = clean.answer(ask(), roles.entity_controller(entity))
            assert result.code == "entity_not_permitted"

    def test_the_engines_own_parser_must_see_one_select(self, clean):
        from src import warehouse
        with clean.reading() as cursor:
            assert warehouse.statement_types(cursor, "SELECT 1") == ("SELECT",)
            assert warehouse.statement_types(cursor, "SELECT 1; DROP TABLE x") == (
                "SELECT", "DROP")
            assert warehouse.statement_types(cursor, "WITH a AS (SELECT 1) SELECT * FROM a "
                                                     "UNION ALL SELECT 2") == ("SELECT",)

    def test_a_query_the_engine_does_not_see_as_one_select_is_not_run(self, clean,
                                                                       monkeypatch):
        from src import compiler, warehouse
        real = compiler.compile

        def doubled(*args, **kwargs):
            q = real(*args, **kwargs)
            return compiler.CompiledQuery(**{**q.__dict__, "sql": q.sql + "; SELECT 1"})
        monkeypatch.setattr(compiler, "compile", doubled)
        ran = []
        monkeypatch.setattr(warehouse, "statement_types",
                            lambda cursor, sql: ran.append(sql) or ("SELECT", "SELECT"))
        result = clean.answer(ask(), group_role())
        assert result.code == "engine_error" and len(ran) == 1


class TestTwoSessionsThroughAnswer:
    def test_two_sessions_from_one_template_give_their_own_answers(self, pool, expected):
        # The reviewer's required test (5.3), run again through session.answer (part 2d).
        first, second = pool.open(), pool.open()
        first.promote("clean", "P. Promoter", clock=lambda: NOON)
        question = ask(version="Forecast")
        assert second.answer(question, group_role()).code == "nothing_promoted"
        second.acknowledge_many("sample", [(b.break_id, "A. Reviewer", "accepted")
                                           for b in second.breaks("sample")],
                                clock=lambda: NOON)
        second.promote("sample", "P. Promoter", clock=lambda: NOON)
        clean_answer, sample_answer = first.answer(question, group_role()), second.answer(
            question, group_role())
        key = ("revenue", "Forecast", "none", "group", "FY2026", "none")
        assert figures(clean_answer) == [("", Decimal(expected[("clean", *key)][0]["value"]))]
        assert figures(sample_answer) == [("", Decimal(expected[("sample", *key)][0]["value"]))]
        assert figures(clean_answer) == [("", Decimal(63686455))]
        assert figures(sample_answer) == [("", Decimal(63720173))]
        assert clean_answer.provenance["plan_file_name"] == "planning_model_clean.xlsx"
        assert sample_answer.provenance["plan_file_name"] == "planning_model.xlsx"


# -- The synthetic star --------------------------------------------------------------------------

LINES = (("4000", "Product sales", "Revenue", "Profit and loss", "Credit", -1,
          "Favourable when higher", "EUR", "flow"),
         ("5000", "Materials", "Cost of sales", "Profit and loss", "Debit", 1,
          "Favourable when lower", "EUR", "flow"),
         ("GP", "Gross profit", None, "Subtotal", None, None, "Favourable when higher", "EUR",
          "flow"),
         ("6100", "Marketing", "Operating expenses", "Profit and loss", "Debit", 1,
          "Favourable when lower", "EUR", "flow"),
         ("VOL", "Volume", None, "Statistics", None, None, "Favourable when higher", "units",
          "flow"),
         ("HC", "Headcount", None, "Statistics", None, None, "Favourable when lower", "FTE",
          "stock"))
Q1 = ("2026-01", "2026-02", "2026-03")


def synthetic_rows() -> list:
    """(version, entity, account, period, amount) of the synthetic star."""
    rows = []
    headcount = {"A": (10, 12, 11), "B": (20, 25, 30)}
    for version in ("Budget", "Forecast"):
        for entity, revenue, cost in (("A", 100, 50), ("B", 900, 630)):
            for i, period in enumerate(Q1):
                if not (version == "Forecast" and entity == "B" and period == "2026-02"):
                    rows.append((version, entity, "5000", period, cost))
                rows += [(version, entity, "4000", period, revenue),
                         (version, entity, "6100", period, 5),
                         (version, entity, "VOL", period, 10),
                         (version, entity, "HC", period, headcount[entity][i]),
                         # A subtotal row holding nonsense: never read.
                         (version, entity, "GP", period, 999999)]
    # Actual: a revenue the integer route cannot scale.
    for account, amount in (("4000", 200000000000000), ("5000", 0), ("6100", 0), ("VOL", 1),
                            ("HC", 1)):
        rows.append(("Actual", "A", account, "2026-01", amount))
    return rows


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    """A session over a star built by INSERTs: no workbook, no extraction, no controls."""
    import os
    import stat

    from src import model, promotion, session, warehouse
    from src.batch import Pair
    from src.startup import Template
    folder = tmp_path_factory.mktemp("synthetic")
    path = folder / "synthetic.duckdb"
    con = warehouse.connect(path)
    try:
        warehouse.create_tables(con)
        promotion.create_tables(con)
        model.build_model(con)               # the star's tables, empty: nothing promoted
        pair = Pair("5717e7ac5717e7ac", "1ed6e71ed6e71ed6")
        con.execute("INSERT INTO gate_promotion VALUES (1, ?, ?, 'S. Builder', "
                    "'2026-11-08T12:00:00Z', NULL, NULL)", [pair.plan_batch_id,
                                                            pair.ledger_batch_id])
        for batch, source, name in ((pair.plan_batch_id, "workbook", "synthetic.xlsx"),
                                    (pair.ledger_batch_id, "ledger", "synthetic.csv")):
            con.execute("INSERT INTO raw_batch VALUES (?, ?, ?, ?, 0, '[]', NULL, NULL, NULL, "
                        "NULL, NULL, 'none', false, '[]', 0)", [batch, source, name, "0" * 64])
        con.execute("INSERT INTO dim_entity VALUES ('A', 'Alpha Foods', 'North', 1), "
                    "('B', 'Beta Foods', 'South', 2)")
        for order, line in enumerate(LINES, start=1):
            con.execute("INSERT INTO dim_account VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [*line, order])
        for version, entity, account, period, amount in synthetic_rows():
            con.execute("INSERT INTO fact_plan VALUES ('5717e7ac5717e7ac', ?, ?, ?, ?, "
                        "CAST(? AS DECIMAL(38, 12)), NULL, NULL)",
                        [version, entity, account, period, str(amount)])
    finally:
        con.close()
    os.chmod(path, stat.S_IREAD)
    template = Template(path, {"synthetic": pair}, {"synthetic": ()}, ())
    pool = session.Sessions(template, root=folder / "sessions")
    yield pool.open()
    pool.close_all()
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)


class TestTheSyntheticStar:
    def test_the_ratio_of_sums_is_32_per_cent_not_40(self, synthetic):
        group = synthetic.answer(ask(metric="gross_margin_pct", period=("month", "2026-01")),
                                 group_role())
        by_entity = synthetic.answer(ask(metric="gross_margin_pct", dimensions=("entity",),
                                         period=("month", "2026-01")), group_role())
        assert figures(group) == [("", Decimal("0.320000000000"))]
        assert figures(by_entity) == [("A", Decimal("0.500000000000")),
                                      ("B", Decimal("0.300000000000"))]

    def test_gross_profit_is_recomputed_and_the_subtotal_row_never_read(self, synthetic):
        a = synthetic.answer(ask(metric="gross_profit", period=("quarter", "Q1")), group_role())
        assert figures(a) == [("", Decimal(3 * 320))]

    def test_a_stock_closes_in_time_and_sums_across_entities(self, synthetic):
        group = synthetic.answer(ask(metric="headcount", period=("quarter", "Q1")),
                                 group_role())
        assert figures(group) == [("", Decimal(41))]               # 11 and 30, in March
        by_month = synthetic.answer(ask(metric="headcount", period=("quarter", "Q1"),
                                        dimensions=("month",)), group_role())
        assert figures(by_month) == [("2026-01", Decimal(30)), ("2026-02", Decimal(37)),
                                     ("2026-03", Decimal(41))]
        by_entity = synthetic.answer(ask(metric="headcount", period=("quarter", "Q1"),
                                         dimensions=("entity",)), group_role())
        assert figures(by_entity) == [("A", Decimal(11)), ("B", Decimal(30))]
        # Never the sum over the months (108) nor an average.
        assert figures(group)[0][1] not in (Decimal(108), Decimal(36))

    def test_a_blank_cell_is_a_gap_for_cost_of_sales_and_not_for_revenue(self, synthetic):
        cost = synthetic.answer(ask(metric="cost_of_sales", version="Forecast",
                                    period=("quarter", "Q1")), group_role())
        revenue = synthetic.answer(ask(metric="revenue", version="Forecast",
                                       period=("quarter", "Q1")), group_role())
        assert (figures(cost), cost.gaps) == ([("", Decimal(150 + 2 * 630))],
                                              (("B", "2026-02"),))
        assert (figures(revenue), revenue.gaps) == ([("", Decimal(3000))], ())
        margin = synthetic.answer(ask(metric="gross_margin_pct", version="Forecast",
                                      period=("quarter", "Q1")), group_role())
        assert margin.gaps == (("B", "2026-02"),)

    def test_an_overflow_is_engine_error_with_no_sql_echoed(self, synthetic):
        from src import outcomes
        result = synthetic.answer(ask(metric="gross_margin_pct", version="Actual",
                                      period=("month", "2026-01")), group_role())
        assert result == outcomes.outcome("engine_error")
        assert "SELECT" not in repr(result) and "fact_plan" not in repr(result)
        # The sum itself is fine: revenue alone is answered.
        revenue = synthetic.answer(ask(version="Actual", period=("month", "2026-01")),
                                   group_role())
        assert figures(revenue) == [("", Decimal(200000000000000))]


# -- Role leakage ---------------------------------------------------------------------------------

def other_names(entity: str) -> list:
    """Every code, name and region of the other entities that the entity does not share."""
    own_region = ENTITIES[entity][1]
    found = []
    for code, (name, region) in ENTITIES.items():
        if code != entity:
            found += [rf"\b{code}\b", re.escape(name)]
            if region != own_region:
                found.append(re.escape(region))
    return found


def text_of(result) -> str:
    from src import answer as answer_module
    if isinstance(result, answer_module.Answer):
        return json.dumps([result.sql, list(result.rows), list(result.coverage.months_held),
                           list(result.gaps), result.provenance,
                           None if result.outcome is None else result.outcome.sentence],
                          ensure_ascii=False, default=str)
    return json.dumps([result.code, result.sentence], ensure_ascii=False)


class TestRoleLeakage:
    @pytest.mark.parametrize("entity", ["AT", "ES"])
    def test_an_entity_controller_gets_its_entitys_figures_alone(self, sample, expected,
                                                                 entity):
        from src import registry, roles
        role = roles.entity_controller(entity)
        loaded = registry.load()
        patterns = other_names(entity)
        compared = 0
        for m in loaded.metrics:
            for version, comparison in (("Actual", "none"), ("Budget", "forecast"),
                                        ("Actual", "budget")):
                for group_by in ("none",) + tuple(m.dimensions):
                    for period in (("fy", "FY2026"), ("month", "2026-05")):
                        a = sample.answer(ask(metric=m.id, version=version,
                                              comparison=comparison,
                                              dimensions=() if group_by == "none" else
                                              (group_by,), period=period), role)
                        text = text_of(a)
                        assert not [p for p in patterns if re.search(p, text)], (m.id, text)
                        period_text = period[1]
                        rows = expected[("sample", m.id, version, comparison, entity,
                                         period_text, group_by)]
                        if rows[0]["outcome"] == "no_data":
                            assert a.outcome.code == "no_data"
                        else:
                            assert [(g, v) for g, v in figures(a)] == [
                                (r["group"], r["value"] if r["value"] == "not meaningful"
                                 else Decimal(r["value"])) for r in rows], (m.id, group_by)
                        compared += 1
        assert compared > 300

    def test_an_at_controller_grouping_by_region_sees_central_europe_alone(self, sample,
                                                                          expected):
        from src import roles
        a = sample.answer(ask(metric="ebit", dimensions=("region",)), roles.entity_controller(
            "AT"))
        (own,) = expected[("sample", "ebit", "Budget", "none", "AT", "FY2026", "none")]
        assert figures(a) == [("Central Europe", Decimal(own["value"]))]
        assert a.provenance["view"] == "entity view: AT"

    def test_refusals_never_name_another_entity(self, sample):
        from src import roles
        at = roles.entity_controller("AT")
        for filters in ((("entity", ("DE",)),), (("entity", ("Calder Foods Spain",)),),
                        (("region", ("Southern Europe",)),), (("entity", ("AT", "ES")),)):
            result = sample.answer(ask(filters=filters), at)
            assert result.code == "entity_not_permitted"
            assert not [p for p in other_names("AT") if re.search(p, text_of(result))]


# -- F22: an account filter chooses what is summed, nothing else (verdict of 11 November) -----------

# One blank Budget cell in each metric that sums several accounts, deleted in a write cycle
# after the clean pair is promoted: AT's Salaries for March (the reviewer's own case), ES's
# 4100 for July and DE's 5100 for November.
BLANKS = (("AT", "6000", "2026-03"), ("ES", "4100", "2026-07"), ("DE", "5100", "2026-11"))
SUMMING = {"revenue": ("4000", "4100"), "cost_of_sales": ("5000", "5100", "5200"),
           "operating_expenses": ("6000", "6100", "6200", "6300")}


@pytest.fixture(scope="module")
def blanked(pool):
    """The clean pair promoted, then the three cells of BLANKS deleted from the star."""
    s = pool.open()
    s.promote("clean", "P. Promoter", clock=lambda: NOON)
    with s.writing() as con:
        for entity, account, period in BLANKS:
            gone = con.execute("DELETE FROM fact_plan WHERE version = 'Budget' AND entity = ? "
                               "AND account = ? AND period = ? RETURNING amount",
                               [entity, account, period]).fetchall()
            assert len(gone) == 1
    return s


def filtered(session, metric, accounts, **changes):
    return session.answer(ask(metric=metric, filters=(("account", tuple(accounts)),),
                              **changes), group_role())


class TestAnAccountFilterChoosesWhatIsSummed:
    """F22 (verdict, section 5, item 3): held is judged on every account the metric reads,
    whatever the filter; a filter on accounts chooses which of them are summed, nothing else.
    So the slices add up to the total, and a gap is a gap with or without the filter."""

    def test_the_reviewers_case_a_filter_on_6100_keeps_the_gap(self, blanked):
        # Operating expenses for FY2026 in Budget, with AT's Salaries for March deleted. Before
        # the ruling a filter on 6100 gave 2,790,870 and no gap: it summed AT's March
        # Marketing, in a month the metric itself calls a gap.
        total = blanked.answer(ask(metric="operating_expenses"), group_role())
        assert (figures(total), total.gaps) == ([("", Decimal(17393631))], (("AT", "2026-03"),))
        by_account = blanked.answer(ask(metric="operating_expenses", dimensions=("account",)),
                                    group_role())
        assert ("6100", Decimal(2750771)) in figures(by_account)
        alone = filtered(blanked, "operating_expenses", ("6100",))
        assert (figures(alone), alone.gaps) == ([("", Decimal(2750771))], (("AT", "2026-03"),))
        assert alone.coverage == total.coverage

    @pytest.mark.parametrize("comparison", ["none", "forecast"])
    @pytest.mark.parametrize("metric", list(SUMMING))
    def test_the_slices_add_up_and_the_gaps_are_the_same(self, blanked, metric, comparison):
        accounts = SUMMING[metric]
        total = blanked.answer(ask(metric=metric, comparison=comparison), group_role())
        grouped = blanked.answer(ask(metric=metric, comparison=comparison,
                                     dimensions=("account",)), group_role())
        singles = {a: filtered(blanked, metric, (a,), comparison=comparison) for a in accounts}
        assert total.outcome is None and len(total.gaps) == 1        # the blank is a gap
        # The gaps and the months held are identical in all three.
        for answered in (grouped, *singles.values()):
            assert (answered.gaps, answered.coverage) == (total.gaps, total.coverage)
        # A grouping by account has the same rows as the single filters.
        assert [row[0] for row in grouped.rows] == list(accounts)
        assert [row[1:] for row in grouped.rows] == [singles[a].rows[0] for a in accounts]
        # The single filters add up to the unfiltered total (value, comparator, variance).
        names = [name for name, _, _ in total.columns]
        for i, name in enumerate(names):
            if name in ("value", "comparator", "variance"):
                assert sum(Decimal(singles[a].rows[0][i]) for a in accounts) == Decimal(
                    total.rows[0][i]), name
        # A filter that names every account the metric reads is the unfiltered answer.
        every = filtered(blanked, metric, accounts, comparison=comparison)
        assert (every.rows, every.gaps, every.coverage) == (total.rows, total.gaps,
                                                            total.coverage)

    def test_a_grouping_and_a_filter_together_give_the_filtered_accounts_alone(self, blanked):
        both = blanked.answer(ask(metric="operating_expenses", dimensions=("account",),
                                  filters=(("account", ("6300", "6100")),)), group_role())
        total = blanked.answer(ask(metric="operating_expenses"), group_role())
        assert [row[0] for row in both.rows] == ["6100", "6300"]
        assert [row[1:] for row in both.rows] == [
            filtered(blanked, "operating_expenses", (a,)).rows[0] for a in ("6100", "6300")]
        assert (both.gaps, both.coverage) == (total.gaps, total.coverage)


# -- Trap 2: a TEMP object lives on one cursor -------------------------------------------------------

class TestATempTableNeverReachesAnAnswer:
    def test_a_temp_fact_plan_on_one_cursor_does_not_change_the_answer(self, clean, expected):
        (row,) = expected[("clean", "revenue", "Budget", "none", "group", "FY2026", "none")]
        real = Decimal(row["value"])
        with clean.reading() as cursor:
            cursor.execute("CREATE TEMP TABLE fact_plan AS SELECT version, entity, account, "
                           "period, amount * 1000 AS amount FROM fact_plan")
            cursor.execute("CREATE TEMP VIEW dim_entity AS SELECT 'XX' AS entity, "
                           "'Nobody' AS entity_name, 'Nowhere' AS region, 1 AS list_order")
            shadowed = cursor.execute("SELECT sum(amount) FROM fact_plan WHERE account IN "
                                      "('4000', '4100') AND version = 'Budget'").fetchone()[0]
            assert shadowed == real * 1000             # the cursor that made it sees it
            # While that cursor is still open, the next request has its own cursor.
            assert figures(clean.answer(ask(), group_role())) == [("", real)]
        assert figures(clean.answer(ask(), group_role())) == [("", real)]
