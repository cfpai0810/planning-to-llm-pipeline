"""The compiler (src/compiler.py), part 2d, the reviewer's brief 6.3 and verdict Q2.

compile(validated, registry, role) emits one SELECT from fixed templates and the registry's
structured formulas. Nothing of a specification enters the SQL but a canonical member name,
through the one quoting function. The role filter is injected: the query starts with the CTE
plan, the only reference to fact_plan, filtered to the role's entities and the versions asked
for, and the CTE permitted, the only reference to dim_entity, filtered to the same entities.
The same specification, registry and role give the same SQL byte for byte, in another
process and under another hash seed too. The compiler runs the guard on its own output.

The integer route (Q2). DuckDB divides two DECIMAL(38, 12) values as a DOUBLE, so the
compiler never uses a plain division: one helper, integer_route(numerator, denominator),
gives the fragment that scales both to HUGEINT, divides with rounding half away from zero and
scales back, so a ratio is the exact quotient rounded once to twelve places. A zero
denominator gives NULL ("not meaningful"); a numerator too large for the scaling raises an
error in the engine, never a wrong figure. Its tests compare it with Python's exact
arithmetic on more than 3,000 cases, negatives and exact ties among them.
"""

import json
import random
import re
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from tests.conftest import subprocess_env

ROOT = Path(__file__).resolve().parent.parent
TWELVE = Decimal("0.000000000001")
ENTITIES = ("ES", "AT", "DE")
FY = tuple(f"2026-{m:02d}" for m in range(1, 13))
METRICS = ("revenue", "cost_of_sales", "gross_profit", "operating_expenses", "ebit",
           "gross_margin_pct", "ebit_margin_pct", "marketing_pct_of_revenue", "volume",
           "headcount", "revenue_per_unit")
RATIOS = ("gross_margin_pct", "ebit_margin_pct", "marketing_pct_of_revenue", "revenue_per_unit")


@pytest.fixture(scope="module")
def compiler():
    from src import compiler
    return compiler


@pytest.fixture(scope="module")
def loaded():
    from src import registry
    return registry.load()


def validated(metric="revenue", version="Budget", dimensions=(), filters=(), period=("fy",
              "FY2026"), comparison="none", sort=None, limit=10, entities=ENTITIES, months=FY):
    """A validated specification built by hand, as validate() gives it."""
    from src import spec
    s = spec.Specification(metric=metric, version=version, dimensions=tuple(dimensions),
                           filters=tuple(spec.Filter(d, tuple(v)) for d, v in filters),
                           period=spec.Period(*period), comparison=comparison,
                           sort=None if sort is None else spec.Sort(*sort), limit=limit)
    return spec.Validated(spec=s, entities=tuple(entities), months=tuple(months))


def group():
    from src import roles
    return roles.group_controller


def strip_literals(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


# -- The quoting function ---------------------------------------------------------------------

class TestTheQuotingFunction:
    def test_it_doubles_single_quotes(self, compiler):
        assert compiler.quote("ES") == "'ES'"
        assert compiler.quote("O'Brien") == "'O''Brien'"
        assert compiler.quote("''") == "''''''"
        assert compiler.quote("Logística y almacén") == "'Logística y almacén'"

    def test_it_takes_text_only(self, compiler):
        for value in (None, 7, b"ES", ("ES",)):
            with pytest.raises(TypeError):
                compiler.quote(value)

    def test_a_quoted_name_reads_back_as_itself(self, compiler, tmp_path):
        from src import warehouse
        con = warehouse.connect(tmp_path / "q.duckdb")
        try:
            for name in ("ES", "O'Brien", "a''b'", "R & D (1/2) 50%", "'; DROP TABLE x; --"):
                assert con.execute(f"SELECT {compiler.quote(name)}").fetchone() == (name,)
        finally:
            con.close()

    def test_it_is_the_only_way_a_name_enters_the_sql(self, compiler, loaded):
        # A member name of a specification reaches the SQL only quoted: an entity filter
        # value appears once per use, always inside quotes.
        q = compiler.compile(validated(filters=(("entity", ("AT",)),)), loaded, group())
        assert re.findall(r"\bAT\b", strip_literals(q.sql)) == []
        assert "'AT'" in q.sql


# -- The integer route -------------------------------------------------------------------------

def exact(n: Decimal, d: Decimal):
    """The exact quotient rounded once to twelve places, half away from zero; None for d = 0."""
    if d == 0:
        return None
    q = Fraction(n) / Fraction(d) * 10 ** 12
    whole, rest = divmod(abs(q.numerator), q.denominator)
    whole += 2 * rest >= q.denominator
    # Built from text, so no context precision rounds a quotient of more than 28 digits.
    return Decimal(f"{whole if q >= 0 else -whole}E-12")


def cases() -> list:
    """More than 3,000 (n, d): random, negative on either side, exact ties, zero denominators."""
    rng = random.Random(20261108)
    found = []
    scales = (1, 10, 100, 1000, 10 ** 6, 10 ** 12)
    for _ in range(3000):
        n = Decimal(rng.randint(-10 ** 13, 10 ** 13)) / rng.choice(scales)
        d = Decimal(rng.randint(1, 10 ** 11)) / rng.choice(scales) * rng.choice((1, -1))
        found.append((n, d))
    # Exact ties: n / d times 10 to the 12 is k + 0.5 exactly, positive and negative.
    for i in range(120):
        m, k = rng.randint(1, 10 ** 6), rng.randint(0, 10 ** 9)
        n = Decimal((2 * k + 1) * m) * TWELVE
        d = Decimal(2 * m)
        signs = ((1, 1), (-1, 1), (1, -1), (-1, -1))[i % 4]
        found.append((n * signs[0], d * signs[1]))
    found += [(Decimal(320), Decimal(1000)), (Decimal(-2), Decimal(3)), (Decimal(2), Decimal(3)),
              (Decimal(0), Decimal(5)), (Decimal(1), Decimal("0.000000000001")),
              (Decimal("0.000000000001"), Decimal(3)), (Decimal("-0.000000000001"), Decimal(2)),
              (Decimal("0.000000000001"), Decimal(2))]
    found += [(Decimal(rng.randint(-10 ** 9, 10 ** 9)), Decimal(0)) for _ in range(10)]
    return found


@pytest.fixture(scope="module")
def routed(compiler, tmp_path_factory):
    """Every case through the fragment, in one query: (n, d, what the engine gave)."""
    from src import warehouse
    con = warehouse.connect(tmp_path_factory.mktemp("route") / "route.duckdb")
    try:
        all_cases = cases()
        con.execute("CREATE TABLE cases (id INTEGER, n DECIMAL(38, 12), d DECIMAL(38, 12))")
        con.execute("INSERT INTO cases SELECT unnest(?::INTEGER[]), "
                    "CAST(unnest(?::VARCHAR[]) AS DECIMAL(38, 12)), "
                    "CAST(unnest(?::VARCHAR[]) AS DECIMAL(38, 12))",
                    [list(range(len(all_cases))), [format(n, "f") for n, _ in all_cases],
                     [format(d, "f") for _, d in all_cases]])
        got = con.execute(f"SELECT id, {compiler.integer_route('n', 'd')} AS q FROM cases "
                          "ORDER BY id").fetchall()
        types = con.execute(f"SELECT typeof({compiler.integer_route('n', 'd')}) FROM cases "
                            "LIMIT 1").fetchone()
    finally:
        con.close()
    return [(n, d, q) for (n, d), (_, q) in zip(all_cases, got)], types


class TestTheIntegerRoute:
    def test_it_is_exact_on_every_case(self, routed):
        rows, _ = routed
        wrong = [(n, d, q, exact(n, d)) for n, d, q in rows if q != exact(n, d)]
        assert wrong == []

    def test_the_counts_the_brief_asks_for(self, routed):
        rows, _ = routed
        assert len(rows) >= 3000
        ties = [(n, d) for n, d, _ in rows if d != 0
                and (Fraction(n) / Fraction(d) * 10 ** 12).denominator == 2]
        assert len(ties) >= 50
        assert sum(1 for n, d in ties if (n < 0) != (d < 0)) >= 25          # negative ties
        assert sum(1 for n, d in ties if (n < 0) == (d < 0)) >= 25          # positive ties
        assert sum(1 for n, d, _ in rows if n < 0) >= 1000
        assert sum(1 for n, d, _ in rows if d < 0) >= 1000

    def test_a_tie_rounds_away_from_zero(self, routed):
        rows, _ = routed
        for n, d, q in rows:
            if d != 0 and (Fraction(n) / Fraction(d) * 10 ** 12).denominator == 2:
                assert abs(q) > abs(Decimal(n) / Decimal(d)), (n, d, q)

    def test_a_zero_denominator_is_null(self, routed):
        rows, _ = routed
        zeros = [q for n, d, q in rows if d == 0]
        assert len(zeros) == 10 and set(zeros) == {None}

    def test_the_result_is_decimal_38_12(self, routed):
        assert routed[1] == ("DECIMAL(38,12)",)

    def test_a_numerator_of_two_times_ten_to_the_fourteen_is_an_engine_error(self, compiler,
                                                                            tmp_path):
        from src import warehouse
        con = warehouse.connect(tmp_path / "big.duckdb")
        try:
            with pytest.raises(warehouse.ENGINE_ERRORS):
                con.execute(f"SELECT {compiler.integer_route('n', 'd')} FROM (SELECT "
                            "CAST('200000000000000' AS DECIMAL(38, 12)) AS n, "
                            "CAST(3 AS DECIMAL(38, 12)) AS d)").fetchall()
            # The route holds a numerator below about 8.5 times 10 to the 13 (2 times the
            # scaled numerator times 10 to the 12 must fit a HUGEINT); the largest sums of
            # the sample are about 10 to the 8, far inside it.
            assert con.execute(f"SELECT {compiler.integer_route('n', 'd')} FROM (SELECT "
                               "CAST('10000000000000' AS DECIMAL(38, 12)) AS n, "
                               "CAST(3 AS DECIMAL(38, 12)) AS d)").fetchone() == (
                Decimal("3333333333333.333333333333"),)
        finally:
            con.close()

    def test_the_fragment_is_the_one_the_reviewer_reproduced(self, compiler):
        assert compiler.integer_route("n", "d") == (
            "CASE WHEN (d) = 0 THEN NULL ELSE CAST(sign(CAST((n) * 1000000000000 AS HUGEINT)) "
            "* sign(CAST((d) * 1000000000000 AS HUGEINT)) * ((2 * abs(CAST((n) * "
            "1000000000000 AS HUGEINT)) * 1000000000000 + abs(CAST((d) * 1000000000000 AS "
            "HUGEINT))) // (2 * abs(CAST((d) * 1000000000000 AS HUGEINT)))) AS DECIMAL(38, 0)) "
            "* CAST('0.000000000001' AS DECIMAL(13, 12)) END")

    def test_every_ratio_and_variance_percentage_uses_it_and_nothing_else_divides(
            self, compiler, loaded):
        for metric in METRICS:
            for comparison in ("none", "budget"):
                version = "Forecast" if comparison == "budget" else "Budget"
                q = compiler.compile(validated(metric=metric, version=version,
                                               comparison=comparison), loaded, group())
                text = strip_literals(q.sql)
                uses = (2 if comparison != "none" else 1) if metric in RATIOS else (
                    1 if comparison != "none" else 0)
                assert text.count("//") == uses, (metric, comparison)
                assert "/" not in text.replace("//", ""), (metric, comparison)
        source = (ROOT / "src" / "compiler.py").read_text(encoding="utf-8")
        assert source.count("//") == 1                 # in integer_route alone


# -- The SQL ------------------------------------------------------------------------------------

class TestTheSql:
    def test_the_role_is_injected_in_the_first_ctes(self, compiler, loaded):
        q = compiler.compile(validated(), loaded, group())
        assert q.sql.startswith("WITH plan AS (SELECT version, entity, account, period, amount "
                                "FROM fact_plan WHERE entity IN ('ES', 'AT', 'DE') AND version "
                                "IN ('Budget')),\npermitted AS (SELECT entity, region, "
                                "list_order FROM dim_entity WHERE entity IN ('ES', 'AT', "
                                "'DE')),\n")

    def test_fact_plan_and_dim_entity_appear_exactly_once(self, compiler, loaded):
        from src import roles
        for role, entities in ((group(), ENTITIES), (roles.entity_controller("AT"), ("AT",))):
            for metric in METRICS:
                for dimensions in ((), ("entity",), ("region",), ("month",), ("quarter",)):
                    q = compiler.compile(validated(metric=metric, version="Actual",
                                                   comparison="budget",
                                                   dimensions=dimensions, entities=entities),
                                         loaded, role)
                    assert len(re.findall(r"\bfact_plan\b", q.sql)) == 1
                    assert len(re.findall(r"\bdim_entity\b", q.sql)) == 1
                    assert "fact_actual_erp" not in q.sql and "raw_" not in q.sql

    def test_an_entity_controller_is_compiled_with_its_entity_alone(self, compiler, loaded):
        from src import roles
        q = compiler.compile(validated(entities=("AT",)), loaded, roles.entity_controller("AT"))
        assert "entity IN ('AT')" in q.sql
        assert not re.search(r"'(ES|DE)'", q.sql)

    def test_a_role_and_entities_that_disagree_are_refused(self, compiler, loaded):
        from src import roles
        with pytest.raises(compiler.CompileError):
            compiler.compile(validated(), loaded, roles.entity_controller("AT"))

    def test_the_limit_is_one_more_than_asked(self, compiler, loaded):
        q = compiler.compile(validated(dimensions=("month",), limit=5), loaded, group())
        assert re.search(r"\bLIMIT 6$", q.sql)

    def test_the_columns(self, compiler, loaded):
        q = compiler.compile(validated(metric="ebit", dimensions=("entity",),
                                       comparison="forecast"), loaded, group())
        assert q.columns == (("entity", "dimension", None), ("value", "flow", "EUR"),
                             ("comparator", "flow", "EUR"), ("variance", "flow", "EUR"),
                             ("variance_pct", "ratio", "%"), ("label", "label", None))
        r = compiler.compile(validated(metric="gross_margin_pct", comparison="budget",
                                       version="Forecast"), loaded, group())
        assert r.columns == (("value", "ratio", "%"), ("comparator", "ratio", "%"),
                             ("variance", "ratio", "pp"), ("label", "label", None))
        u = compiler.compile(validated(metric="revenue_per_unit"), loaded, group())
        assert u.columns == (("value", "ratio", "EUR per unit"),)

    def test_the_sha256_is_of_the_sql(self, compiler, loaded):
        import hashlib
        q = compiler.compile(validated(), loaded, group())
        assert q.sql_sha256 == hashlib.sha256(q.sql.encode("utf-8")).hexdigest()

    def test_no_comment_and_no_semicolon(self, compiler, loaded):
        q = compiler.compile(validated(metric="ebit_margin_pct", dimensions=("quarter",),
                                       comparison="forecast", version="Actual"), loaded, group())
        assert ";" not in q.sql and "--" not in q.sql and "/*" not in q.sql


# -- The same SQL, byte for byte ---------------------------------------------------------------

def specimens() -> list:
    """(metric, version, dimensions, filters, comparison, sort, entities) for the
    determinism tests: every metric, with groupings, filters, comparisons and sorts."""
    found = []
    for i, metric in enumerate(METRICS):
        found.append((metric, "Budget", (), (), "none", None, ENTITIES))
        found.append((metric, "Actual", ("entity", "month"), (("region", ("Central Europe",)),),
                      "forecast", ("value", "desc"), ENTITIES))
        found.append((metric, "Forecast", ("quarter",), (), "budget", ("quarter", "asc"),
                      ("AT",)))
    found.append(("revenue", "Budget", ("account",), (("account", ("4100", "4000")),), "none",
                  ("account", "desc"), ENTITIES))
    return found


SCRIPT = """
import hashlib, json, sys
sys.path.insert(0, sys.argv[1])
from src import compiler, registry, roles, spec
loaded = registry.load()
out = []
for metric, version, dims, filters, comparison, sort, entities in json.loads(sys.argv[2]):
    s = spec.Specification(metric=metric, version=version, dimensions=tuple(dims),
                           filters=tuple(spec.Filter(d, tuple(v)) for d, v in filters),
                           period=spec.Period("fy", "FY2026"), comparison=comparison,
                           sort=None if sort is None else spec.Sort(*sort), limit=10)
    role = roles.group_controller if len(entities) > 1 else roles.entity_controller(entities[0])
    v = spec.Validated(spec=s, entities=tuple(entities),
                       months=tuple(f"2026-{m:02d}" for m in range(1, 13)))
    out.append(compiler.compile(v, loaded, role).sql_sha256)
print(json.dumps(out))
"""


class TestDeterminism:
    def hashes_here(self, compiler, loaded):
        from src import roles
        out = []
        for metric, version, dims, filters, comparison, sort, entities in specimens():
            role = group() if len(entities) > 1 else roles.entity_controller(entities[0])
            out.append(compiler.compile(validated(metric=metric, version=version,
                                                  dimensions=dims, filters=filters,
                                                  comparison=comparison, sort=sort,
                                                  entities=entities), loaded, role).sql_sha256)
        return out

    def test_twice_in_one_process(self, compiler, loaded):
        first, second = self.hashes_here(compiler, loaded), self.hashes_here(compiler, loaded)
        assert first == second and len(set(first)) == len(first)

    @pytest.mark.parametrize("seed", ["1", "4242"])
    def test_in_a_fresh_process_with_another_hash_seed(self, compiler, loaded, seed):
        done = subprocess.run([sys.executable, "-c", SCRIPT, str(ROOT), json.dumps(specimens())],
                              capture_output=True, text=True, timeout=180,
                              env=subprocess_env(PYTHONHASHSEED=seed))
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout) == self.hashes_here(compiler, loaded)


# -- The guard on the compiler's own output ------------------------------------------------------

class TestTheCompilerChecksItself:
    def test_a_template_without_the_role_filter_is_refused(self, compiler, loaded, monkeypatch):
        monkeypatch.setattr(compiler, "PLAN", "SELECT version, entity, account, period, amount "
                                              "FROM fact_plan WHERE version IN ({versions})")
        with pytest.raises(compiler.CompileError) as refused:
            compiler.compile(validated(), loaded, group())
        assert refused.value.reason == "role_filter"

    def test_a_template_that_reads_the_ledger_is_refused(self, compiler, loaded, monkeypatch):
        monkeypatch.setattr(compiler, "PLAN", "SELECT version, entity, account, period, amount "
                                              "FROM fact_actual_erp WHERE entity IN ({entities}) "
                                              "AND version IN ({versions})")
        with pytest.raises(compiler.CompileError) as refused:
            compiler.compile(validated(), loaded, group())
        assert refused.value.reason == "relation_not_allowed"

    def test_the_guard_is_called_on_every_compile(self, compiler, loaded, monkeypatch):
        from src import guard
        seen = []
        real = guard.check

        def spy(sql, **kwargs):
            seen.append((sql, kwargs))
            return real(sql, **kwargs)
        monkeypatch.setattr(guard, "check", spy)
        q = compiler.compile(validated(), loaded, group())
        assert seen == [(q.sql, {"allowed_relations": compiler.ALLOWED_RELATIONS,
                                 "role_entities": ENTITIES})]
        assert compiler.ALLOWED_RELATIONS == ("fact_plan", "dim_version", "dim_time",
                                              "dim_entity", "dim_account")
