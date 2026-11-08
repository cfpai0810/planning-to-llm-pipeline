"""The guard (src/guard.py) and its hostile corpus, part 2d, the reviewer's brief 6.4.

check(sql, *, allowed_relations, role_entities=None) parses the text with sqlglot's DuckDB
dialect and returns "ok" or one reason code. It refuses anything that is not exactly one
statement; any statement that is not a SELECT or a UNION of SELECTs; a comment anywhere; a
literal that is not a plain string or number (a dollar-quoted string, a parameter); a schema-
or catalog-qualified name; a table that is not a plain identifier (a table function, UNNEST,
LATERAL, VALUES, PIVOT, a quoted name or a file name); a CTE named like a real relation; any
relation not in allowed_relations, compared lower-cased; a function the compiler never uses;
the same checks inside every CTE body and subquery. Given role_entities, fact_plan and
dim_entity must each appear exactly once, in a SELECT that reads that table alone and whose
WHERE holds entity IN (...) for exactly that set.

The corpus (tests/guard_corpus.txt) holds every hostile entry with the reason it must get,
and the valid statements that must pass. The guard is a second line: the read-only
connection with external access off is the first, and each entry is also run on a cursor of
a session's read-only connection, where it fails, changes nothing a later request sees (a
TEMP object lives on the one cursor that made it: Trap 2), or reads. A read is not stopped
by the connection: for those the guard and the compiler are the controls.
"""

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "guard_corpus.txt"
ENTITIES = ("ES", "AT", "DE")
NOON = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
REASONS = ("parse_error", "not_one_statement", "not_select", "comment", "literal_not_plain",
           "qualified_name", "table_not_plain", "cte_shadows", "relation_not_allowed",
           "function_not_allowed", "role_filter")


def corpus() -> list:
    """(reason, connection, sql) for every entry, in file order."""
    entries = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        reason, connection, sql = line.split("\t")
        entries.append((reason, connection, json.loads(sql)))
    return entries


ENTRIES = corpus()
HOSTILE = [e for e in ENTRIES if e[0] != "ok"]
VALID = [e for e in ENTRIES if e[0] == "ok"]


@pytest.fixture(scope="module")
def guard():
    from src import guard
    return guard


def check(guard, sql, role_entities=ENTITIES):
    from src import compiler
    return guard.check(sql, allowed_relations=compiler.ALLOWED_RELATIONS,
                       role_entities=role_entities)


class TestTheCorpus:
    def test_its_size(self):
        assert len(HOSTILE) >= 60 and len(VALID) >= 15

    def test_every_reason_is_in_it(self, guard):
        assert guard.REASONS == REASONS
        assert set(r for r, _, _ in HOSTILE) == set(REASONS)

    def test_the_cases_the_reviewer_and_the_coordinator_named_are_in_it(self):
        texts = [sql for _, _, sql in HOSTILE]
        for needle in ("DROP TABLE", "DELETE FROM", "UPDATE fact_plan", "INSERT INTO", "COPY ",
                       "ATTACH ", "PRAGMA ", "SET ", "DESCRIBE ", "SHOW ", "EXPLAIN ", "PIVOT ",
                       "VALUES (1)", "read_csv(", "glob(", "range(", "generate_series(",
                       "duckdb_tables(", "query_table(", "UNNEST(", "LATERAL", '"RAW_BATCH"',
                       "FROM RAW_BATCH", "main.fact_plan", "warehouse.main.", "-- a note\n;",
                       "FROM 'data/erp_trial_balance.csv'", "WITH fact_plan AS",
                       "CREATE TEMP TABLE fact_plan", "CREATE TEMP VIEW", "CREATE TEMP MACRO",
                       "CREATE TEMP SEQUENCE", "CHECKPOINT", "INSTALL ", "fact_actual_erp",
                       "IN (SELECT code FROM raw_list_account)", "(SELECT max(file_name)",
                       "entity IN ('ES', 'AT'))", "FROM FACT_PLAN"):
            assert any(needle in t for t in texts), needle
        # The integer-route fragment is among the valid statements.
        from src import compiler
        assert any(compiler.integer_route("n", "d") in sql for _, _, sql in VALID)

    def test_the_file_is_lf_with_three_fields_a_line(self):
        data = CORPUS.read_bytes()
        assert b"\r" not in data and data.endswith(b"\n")
        connections = Counter(c for _, c, _ in ENTRIES)
        assert set(connections) == {"fails", "nothing", "reads"}

    @pytest.mark.parametrize("reason, connection, sql", HOSTILE,
                             ids=[f"{i:03d}_{e[0]}" for i, e in enumerate(HOSTILE)])
    def test_each_hostile_entry_is_refused_for_its_reason(self, guard, reason, connection,
                                                          sql):
        assert check(guard, sql) == reason

    @pytest.mark.parametrize("reason, connection, sql", VALID,
                             ids=[f"{i:03d}_ok" for i in range(len(VALID))])
    def test_each_valid_statement_passes(self, guard, reason, connection, sql):
        assert check(guard, sql) == "ok"


class TestTheRules:
    def test_without_a_role_the_role_check_is_off(self, guard):
        from src import compiler
        sql = "SELECT period FROM dim_time"
        assert guard.check(sql, allowed_relations=compiler.ALLOWED_RELATIONS) == "ok"
        assert check(guard, sql) == "role_filter"

    def test_the_role_set_is_compared_exactly(self, guard):
        valid = VALID[0][2]
        assert check(guard, valid, ("ES", "AT", "DE")) == "ok"
        assert check(guard, valid, ("DE", "ES", "AT")) == "ok"
        assert check(guard, valid, ("ES", "AT")) == "role_filter"
        assert check(guard, valid, ("AT",)) == "role_filter"

    def test_allowed_relations_are_compared_lower_cased(self, guard):
        assert guard.check("SELECT * FROM Dim_Time", allowed_relations=("dim_time",)) == "ok"
        assert guard.check("SELECT * FROM dim_time", allowed_relations=("DIM_TIME",)) == "ok"
        assert guard.check("SELECT * FROM raw_batch", allowed_relations=("dim_time",)) == \
            "relation_not_allowed"

    def test_cte_names_are_not_relations_but_their_bodies_are_checked(self, guard):
        allowed = ("dim_time",)
        assert guard.check("WITH t AS (SELECT period FROM dim_time) SELECT * FROM t",
                           allowed_relations=allowed) == "ok"
        assert guard.check("WITH t AS (SELECT * FROM raw_batch) SELECT * FROM t",
                           allowed_relations=allowed) == "relation_not_allowed"

    def test_the_known_relations_are_the_warehouses_tables(self, guard):
        from src import model, promotion, warehouse
        assert set(guard.KNOWN_RELATIONS) == set(warehouse.RAW_TABLES) | set(
            promotion.GATE_TABLES) | set(model.STAR_TABLES)

    def test_it_takes_text_only(self, guard):
        for value in (None, b"SELECT 1", 7):
            with pytest.raises(TypeError):
                guard.check(value, allowed_relations=("dim_time",))


class TestEveryCompiledQueryPasses:
    def test_a_compiled_query_of_each_metric_passes_for_each_role(self, guard):
        from src import compiler, registry, roles, spec
        loaded = registry.load()
        for metric in loaded.ids():
            for role, entities in ((roles.group_controller, ENTITIES),
                                   (roles.entity_controller("DE"), ("DE",))):
                for dims, comparison in (((), "none"), (("region", "quarter"), "budget")):
                    s = spec.Specification(metric=metric, version="Actual", dimensions=dims,
                                           filters=(), period=spec.Period("ytd", "2026-06"),
                                           comparison=comparison, sort=None, limit=100)
                    v = spec.Validated(spec=s, entities=entities,
                                       months=tuple(f"2026-{m:02d}" for m in range(1, 7)))
                    q = compiler.compile(v, loaded, role)
                    assert guard.check(q.sql, allowed_relations=compiler.ALLOWED_RELATIONS,
                                       role_entities=entities) == "ok"


# -- The corpus on the connection itself -----------------------------------------------------------

QUESTION = {"metric": "gross_margin_pct", "version": "Budget", "dimensions": ["entity"],
            "filters": [], "period": {"kind": "fy", "value": "FY2026"}, "comparison": "forecast",
            "sort": None, "limit": 10}


@pytest.fixture(scope="module")
def promoted(template, tmp_path_factory):
    from src import session
    pool = session.Sessions(template, root=tmp_path_factory.mktemp("corpus"))
    s = pool.open()
    s.promote("clean", "P. Promoter", clock=lambda: NOON)
    yield s
    pool.close_all()


# Statements the engine runs that are not reads: what they return (CREATE TABLE AS gives its
# row count) is not data, and what they change lives on their own cursor.
NOT_READS = {"CREATE", "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRANSACTION", "COPY",
             "ATTACH", "DETACH", "SET", "LOAD", "VACUUM", "EXPORT"}


def run_on_a_cursor(session, sql) -> str:
    """fails when the engine refuses it; reads when its last statement is a read that returns
    rows (the engine reads PIVOT as a CREATE of a type, then a SELECT); else nothing."""
    from src import warehouse
    with session.reading() as cursor:
        try:
            kinds = warehouse.statement_types(cursor, sql)
            result = cursor.execute(sql)
            rows = result.fetchall() if result is not None and result.description else []
        except warehouse.ENGINE_ERRORS:
            return "fails"
    return "reads" if rows and kinds and kinds[-1] not in NOT_READS else "nothing"


class TestTheCorpusOnTheReadOnlyConnection:
    def test_each_entry_fails_changes_nothing_or_only_reads(self, promoted):
        from src import roles
        role = roles.group_controller
        before = promoted.answer(QUESTION, role)
        file_before = hashlib.sha256(promoted.path.read_bytes()).hexdigest()
        found = []
        for reason, connection, sql in ENTRIES:
            found.append((connection, run_on_a_cursor(promoted, sql), sql))
            # "Nothing" means just that: the next request's answer is the same, and so is
            # the file. A TEMP object made on one cursor is gone with it.
            after = promoted.answer(QUESTION, role)
            assert (after.rows, after.result_sha256) == (before.rows, before.result_sha256), sql
        assert [f for f in found if f[0] != f[1]] == []
        assert hashlib.sha256(promoted.path.read_bytes()).hexdigest() == file_before
