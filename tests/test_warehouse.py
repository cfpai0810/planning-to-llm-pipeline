"""Landing in the warehouse (src/warehouse.py), part 1c, brief 5.4, with the 1d follow-up.

DuckDB opens in one function, warehouse.connect, and nowhere else (the
boundary test in tests/test_boundaries.py). The four raw tables are append
only: a batch is landed once, its id comes from the file's content hash, and
landing the same file again returns a typed "already landed" and adds no row.
Money is stored as DECIMAL(38, 12), which holds every amount the readers give
exactly; an amount it could not hold exactly refuses the batch before any row
is written. The warehouse is built from the committed sample and ledger into
a fresh folder, closed and reopened.

The 1d follow-up (the reviewer's ruling on the build time): a batch lands in
one transaction with one INSERT per table, every value a bound parameter; the
CREATE statements declare no key and no index, and the build creates the
unique index on raw_batch's batch id after the load; the build lands the
sample and the ledger only. The suite builds the committed sources once per
session (tests/conftest.py, built_warehouse, with the start-up build of
src/startup.py, which tests/test_startup.py tests): tests that only read open
that warehouse, and tests that write use a copy of its file or a small
warehouse of their own.

Part 2 lands a workbook's Lists too, in raw_list_account and raw_list_entity,
so that reconcile and the star schema read the accounts, their ledger signs
and the entities from the warehouse itself. The append-only scan covers the
gate tables of the promotion gate as well as the raw tables.

DuckDB has an HTTP client of its own, which the suite's network guard cannot
see. So the connection is opened with extension autoinstall and autoload off,
community extensions refused, external access off and the configuration
locked, and TestTheNetworkSettings reads those settings back from DuckDB
itself: that test is the guard.
"""

import dataclasses
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from src.hashing import file_sha256
from tests.extractions import extraction

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "planning_model.xlsx"
CLEAN = ROOT / "data" / "planning_model_clean.xlsx"
LEDGER = ROOT / "data" / "erp_trial_balance.csv"
FIXTURES = ROOT / "tests" / "fixtures"
READERS = FIXTURES / "readers"
SMALL_BOOK = FIXTURES / "extra_tab.xlsx"         # the small base: one entity, 507 cells
RAW_TABLES = ("raw_batch", "raw_plan_cell", "raw_ledger_line", "raw_control_total",
              "raw_list_account", "raw_list_entity")
MONEY = "DECIMAL(38,12)"
COLUMNS = {
    "raw_batch": [
        ("batch_id", "VARCHAR"), ("source", "VARCHAR"), ("file_name", "VARCHAR"),
        ("file_sha256", "VARCHAR"), ("file_bytes", "BIGINT"), ("sheets", "VARCHAR"),
        ("creator", "VARCHAR"), ("last_modified_by", "VARCHAR"), ("created", "VARCHAR"),
        ("modified", "VARCHAR"), ("application", "VARCHAR"), ("calculation", "VARCHAR"),
        ("has_formulas", "BOOLEAN"), ("external_links", "VARCHAR"), ("row_count", "BIGINT")],
    "raw_plan_cell": [
        ("batch_id", "VARCHAR"), ("row_number", "INTEGER"), ("sheet", "VARCHAR"),
        ("address", "VARCHAR"), ("block", "VARCHAR"), ("source_line", "INTEGER"),
        ("version", "VARCHAR"), ("entity", "VARCHAR"), ("line", "VARCHAR"),
        ("account", "VARCHAR"), ("period", "VARCHAR"), ("column_kind", "VARCHAR"),
        ("unit", "VARCHAR"), ("kind", "VARCHAR"), ("formula", "VARCHAR"), ("cached", "BOOLEAN"),
        ("value_type", "VARCHAR"), ("value_text", "VARCHAR"), ("amount", MONEY),
        ("row_error", "VARCHAR"), ("duplicate", "BOOLEAN"), ("unknown_entity", "BOOLEAN"),
        ("unknown_account", "BOOLEAN"), ("name_allowed", "BOOLEAN")],
    "raw_ledger_line": [
        ("batch_id", "VARCHAR"), ("line_number", "INTEGER"), ("entity", "VARCHAR"),
        ("period", "VARCHAR"), ("account", "VARCHAR"), ("amount", MONEY),
        ("amount_text", "VARCHAR"), ("row_error", "VARCHAR"), ("duplicate", "BOOLEAN"),
        ("unknown_entity", "BOOLEAN"), ("unknown_account", "BOOLEAN")],
    "raw_control_total": [
        ("batch_id", "VARCHAR"), ("version", "VARCHAR"), ("period", "VARCHAR"),
        ("row_count", "BIGINT"), ("amount_sum", MONEY)],
    "raw_list_account": [
        ("batch_id", "VARCHAR"), ("list_row", "INTEGER"), ("code", "VARCHAR"),
        ("name", "VARCHAR"), ("parent", "VARCHAR"), ("section", "VARCHAR"),
        ("polarity", "VARCHAR"), ("unit", "VARCHAR"), ("ledger_sign", "VARCHAR"),
        ("kind", "VARCHAR"), ("address", "VARCHAR"), ("name_allowed", "BOOLEAN")],
    "raw_list_entity": [
        ("batch_id", "VARCHAR"), ("list_row", "INTEGER"), ("code", "VARCHAR"),
        ("name", "VARCHAR"), ("region", "VARCHAR"), ("address", "VARCHAR"),
        ("name_allowed", "BOOLEAN")],
}
# The statements that would change or remove a landed row, against a raw table or
# a table of the promotion gate (the breaks, acknowledgements and promotions).
CHANGES_A_RAW_ROW = re.compile(
    r"\b(?:UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?|DROP\s+TABLE(?:\s+IF\s+EXISTS)?|"
    r"ALTER\s+TABLE|INSERT\s+OR\s+REPLACE\s+INTO|REPLACE\s+INTO|"
    r"CREATE\s+OR\s+REPLACE\s+TABLE)\s+[\"'`]?(?:raw|gate)_\w+"
    r"|\bON\s+CONFLICT\b", re.IGNORECASE)


@pytest.fixture(scope="module")
def warehouse():
    from src import warehouse
    return warehouse


@pytest.fixture(scope="module")
def sample():
    return extraction(SAMPLE)


@pytest.fixture(scope="module")
def ledger_lines(sample):
    from src import ledger
    return ledger.read_ledger(LEDGER, entities=sample.lists.entity_codes(),
                              accounts=sample.lists.ledger_accounts())


@pytest.fixture
def con(warehouse, tmp_path):
    """An empty warehouse of the test's own, for landing small or in-memory sources."""
    connection = warehouse.connect(tmp_path / "warehouse.duckdb")
    warehouse.create_tables(connection)
    yield connection
    connection.close()


@pytest.fixture
def built(warehouse, built_warehouse):
    """The session warehouse, opened for reading."""
    with warehouse.connect(built_warehouse.path) as connection:
        yield connection


def counts(con) -> dict:
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in RAW_TABLES}


class Recording:
    """A connection that writes down every statement it is given, then passes it on."""

    def __init__(self, connection):
        self._connection = connection
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append((statement, parameters))
        if parameters is None:
            return self._connection.execute(statement)
        return self._connection.execute(statement, parameters)

    def begin(self):
        self.statements.append(("BEGIN", None))
        return self._connection.begin()

    def commit(self):
        self.statements.append(("COMMIT", None))
        return self._connection.commit()

    def rollback(self):
        self.statements.append(("ROLLBACK", None))
        return self._connection.rollback()

    def close(self):
        return self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._connection.close()

    def inserts(self) -> list:
        return [re.match(r"INSERT INTO (\w+)", s).group(1) for s, _ in self.statements
                if s.startswith("INSERT INTO")]


class TestTheNetworkSettings:
    """Read back from DuckDB itself: the Python network guard cannot see DuckDB's downloads."""

    def test_extensions_are_never_installed_or_loaded_automatically(self, warehouse, tmp_path):
        with warehouse.connect(tmp_path / "w.duckdb") as connection:
            def setting(name):
                return connection.execute("SELECT current_setting(?)", [name]).fetchone()[0]
            assert setting("autoinstall_known_extensions") is False
            assert setting("autoload_known_extensions") is False
            assert setting("allow_community_extensions") is False
            listed = dict(connection.execute(
                "SELECT name, value FROM duckdb_settings() WHERE name IN "
                "('autoinstall_known_extensions', 'autoload_known_extensions', "
                "'allow_community_extensions', 'allow_unsigned_extensions')").fetchall())
            assert listed == {"autoinstall_known_extensions": "false",
                              "autoload_known_extensions": "false",
                              "allow_community_extensions": "false",
                              "allow_unsigned_extensions": "false"}

    def test_external_access_is_off_and_the_configuration_locked(self, warehouse, tmp_path):
        import duckdb
        with warehouse.connect(tmp_path / "w.duckdb") as connection:
            def setting(name):
                return connection.execute("SELECT current_setting(?)", [name]).fetchone()[0]
            assert setting("enable_external_access") is False
            assert setting("lock_configuration") is True
            with pytest.raises(duckdb.Error):
                connection.execute("SET autoinstall_known_extensions = true")
            with pytest.raises(duckdb.Error):
                connection.execute("SET enable_external_access = true")
            assert setting("autoinstall_known_extensions") is False

    def test_a_local_file_cannot_be_read_through_sql(self, warehouse, tmp_path):
        import duckdb
        csv = tmp_path / "local.csv"
        csv.write_text("a\n1\n", encoding="utf-8")
        with warehouse.connect(tmp_path / "w.duckdb") as connection:
            with pytest.raises(duckdb.Error):
                connection.execute(f"SELECT * FROM read_csv('{csv.as_posix()}')").fetchall()

    def test_the_settings_are_the_documented_ones(self, warehouse):
        assert warehouse.SETTINGS == {"autoinstall_known_extensions": False,
                                      "autoload_known_extensions": False,
                                      "allow_community_extensions": False,
                                      "allow_unsigned_extensions": False,
                                      "enable_external_access": False,
                                      "lock_configuration": True}


class TestTheTables:
    def test_the_raw_tables_and_their_types(self, con, warehouse):
        assert warehouse.RAW_TABLES == RAW_TABLES
        for table, columns in COLUMNS.items():
            found = con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = ? ORDER BY ordinal_position", [table]).fetchall()
            assert found == columns, table
        assert warehouse.MONEY == MONEY

    def test_creating_the_tables_twice_changes_nothing(self, con, warehouse):
        warehouse.create_tables(con)
        assert counts(con) == dict.fromkeys(RAW_TABLES, 0)

    def test_the_create_statements_declare_no_key_and_no_index(self, con, warehouse):
        # The ruling: indexes after the load. A key declared on the table would
        # be checked row by row while the batch lands.
        for statement in warehouse.TABLES:
            assert not re.search(r"PRIMARY\s+KEY|UNIQUE|REFERENCES|FOREIGN\s+KEY|\bINDEX\b",
                                 statement, re.IGNORECASE), statement
        assert con.execute(
            "SELECT count(*) FROM duckdb_constraints() WHERE constraint_type IN "
            "('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM duckdb_indexes()").fetchone()[0] == 0


class TestLanding:
    def test_the_sample_lands_row_for_row(self, built, sample):
        stored = built.execute("SELECT * EXCLUDE (row_number) FROM raw_plan_cell "
                               "WHERE batch_id = ? ORDER BY row_number",
                               [sample.batch.batch_id]).fetchall()
        assert stored == [tuple(dataclasses.asdict(c).values()) for c in sample.cells]

    def test_the_batch_record_lands(self, built, sample):
        row = built.execute("SELECT * FROM raw_batch WHERE batch_id = ?",
                            [sample.batch.batch_id]).fetchall()
        assert len(row) == 1
        record = dict(zip([c for c, _ in COLUMNS["raw_batch"]], row[0]))
        b = sample.batch
        assert (record["batch_id"], record["file_sha256"], record["file_name"]) == (
            b.batch_id, file_sha256(SAMPLE), "planning_model.xlsx")
        assert json.loads(record["sheets"])[-1] == {"name": "Scratch", "visibility": "hidden"}
        assert (record["created"], record["modified"], record["application"]) == (
            b.created, b.modified, b.application)
        assert record["calculation"] == "recalculated by the sample build"
        assert record["has_formulas"] is True and json.loads(record["external_links"]) == []
        assert record["row_count"] == len(sample.cells)

    def test_the_control_totals_are_pythons_and_tie_to_the_rows(self, built, sample):
        batch = sample.batch.batch_id
        stored = built.execute("SELECT version, period, row_count, amount_sum FROM "
                               "raw_control_total WHERE batch_id = ? ORDER BY version, period",
                               [batch]).fetchall()
        assert stored == sorted((t.version, t.period, t.row_count, t.amount_sum)
                                for t in sample.control_totals)
        recomputed = built.execute(
            "SELECT version, period, count(*), coalesce(sum(amount), 0) FROM raw_plan_cell "
            "WHERE batch_id = ? GROUP BY version, period ORDER BY version, period",
            [batch]).fetchall()
        assert recomputed == stored

    def test_the_lists_land_with_the_workbook(self, built, sample):
        batch = sample.batch.batch_id
        accounts = built.execute("SELECT * EXCLUDE (batch_id) FROM raw_list_account "
                                 "WHERE batch_id = ? ORDER BY list_row", [batch]).fetchall()
        assert accounts == [(n, a.code, a.name, a.parent, a.section, a.polarity, a.unit,
                             a.ledger_sign, a.kind, a.address, True)
                            for n, a in enumerate(sample.lists.accounts, start=1)]
        entities = built.execute("SELECT * EXCLUDE (batch_id) FROM raw_list_entity "
                                 "WHERE batch_id = ? ORDER BY list_row", [batch]).fetchall()
        assert entities == [(n, e.code, e.name, e.region, e.address, True)
                            for n, e in enumerate(sample.lists.entities, start=1)]

    def test_a_name_that_fails_the_allow_list_lands_raw_and_flagged(self, con, warehouse):
        names = extraction(FIXTURES / "names.xlsx")
        warehouse.land(con, names)
        refused = con.execute("SELECT code, name FROM raw_list_account WHERE NOT name_allowed "
                              "ORDER BY list_row").fetchall()
        assert [code for code, _ in refused] == ["4000", "5100", "6000"]
        assert {name for _, name in refused} == {r.name for r in names.rejected_names}

    def test_landing_the_same_file_twice_adds_no_rows(self, warehouse, warehouse_copy, sample):
        with warehouse.connect(warehouse_copy) as con:
            before = counts(con)
            again = warehouse.land(con, sample)
            assert isinstance(again, warehouse.AlreadyLanded)
            assert (again.batch_id, again.file_name) == (sample.batch.batch_id,
                                                         "planning_model.xlsx")
            assert counts(con) == before

    def test_a_duplicate_landed_later_is_still_refused_by_the_index(self, warehouse,
                                                                     warehouse_copy):
        # land() refuses a batch already there itself; the unique index the
        # build creates after the load refuses one that goes round it.
        import duckdb
        with warehouse.connect(warehouse_copy) as con:
            before = counts(con)
            with pytest.raises(duckdb.ConstraintException):
                con.execute("INSERT INTO raw_batch SELECT * FROM raw_batch "
                            "WHERE source = 'workbook'")
            assert counts(con) == before

    def test_the_ledger_lands(self, built, ledger_lines):
        batch = ledger_lines.batch.batch_id
        stored = built.execute("SELECT * FROM raw_ledger_line WHERE batch_id = ? "
                               "ORDER BY line_number", [batch]).fetchall()
        assert len(stored) == 162
        assert stored == [tuple(dataclasses.asdict(l).values()) for l in ledger_lines.lines]
        assert built.execute("SELECT source, has_formulas, calculation FROM raw_batch "
                             "WHERE batch_id = ?", [batch]).fetchone() \
            == ("ledger", False, "not applicable: the source has no formulas")
        assert built.execute("SELECT count(*) FROM raw_control_total WHERE version IS NULL") \
            .fetchone()[0] == 6

    def test_landing_the_ledger_twice_adds_no_rows(self, warehouse, warehouse_copy, ledger_lines):
        with warehouse.connect(warehouse_copy) as con:
            before = counts(con)
            assert warehouse.land(con, ledger_lines) == warehouse.AlreadyLanded(
                ledger_lines.batch.batch_id, "erp_trial_balance.csv")
            assert counts(con) == before

    def test_a_long_format_file_lands_as_plan_cells(self, con, warehouse):
        from src import long_format
        source = long_format.read_long_format(READERS / "long_format_cases.csv",
                                              entities=("ES", "AT", "DE"), accounts=("4000",))
        warehouse.land(con, source)
        stored = con.execute("SELECT * EXCLUDE (row_number) FROM raw_plan_cell "
                             "ORDER BY row_number").fetchall()
        assert stored == [tuple(dataclasses.asdict(c).values()) for c in source.cells]
        assert con.execute("SELECT has_formulas FROM raw_batch").fetchone() == (False,)

    def test_amounts_round_trip_exactly(self, con, built, warehouse, sample):
        headcount = built.execute(
            "SELECT amount FROM raw_plan_cell WHERE version = 'Forecast' AND entity = 'ES' "
            "AND line = 'Headcount' AND period = '2026-01'").fetchone()[0]
        assert headcount == Decimal("63.5") and headcount.normalize() == Decimal("63.5")
        cell = sample.cells[0]
        made = [dataclasses.replace(cell, batch_id="made", address=f"Z{i}", amount=Decimal(text))
                for i, text in enumerate(["0.1", "9007199254740993", "-1490400.00",
                                          "12345678901234567890123456.123456789012",
                                          "-0.000000000001", "0"])]
        made.append(dataclasses.replace(cell, batch_id="made", address="Z9", amount=None))
        warehouse.land(con, dataclasses.replace(
            sample, batch=dataclasses.replace(sample.batch, batch_id="made"), cells=tuple(made),
            control_totals=()))
        back = [r[0] for r in con.execute("SELECT amount FROM raw_plan_cell WHERE batch_id = "
                                          "'made' ORDER BY row_number").fetchall()]
        assert back == [c.amount for c in made]

    def test_rows_of_another_batch_are_refused(self, con, warehouse, sample):
        other = dataclasses.replace(sample, batch=dataclasses.replace(sample.batch,
                                                                      batch_id="other"))
        with pytest.raises(warehouse.WarehouseError):
            warehouse.land(con, other)
        assert counts(con) == dict.fromkeys(RAW_TABLES, 0)

    @pytest.mark.parametrize("text", ["0.1234567890123", "123456789012345678901234567"],
                             ids=["13 places", "27 digits"])
    def test_an_amount_it_cannot_hold_exactly_refuses_the_batch(self, con, warehouse, sample,
                                                                text):
        cells = list(sample.cells)
        cells[-1] = dataclasses.replace(cells[-1], amount=Decimal(text))
        with pytest.raises(warehouse.AmountOutOfRange) as caught:
            warehouse.land(con, dataclasses.replace(sample, cells=tuple(cells)))
        assert "\n" not in str(caught.value) and cells[-1].address in str(caught.value)
        assert counts(con) == dict.fromkeys(RAW_TABLES, 0)


class TestOneStatementPerTable:
    """The ruling: each table of a batch lands with one batched INSERT, in one transaction."""

    @pytest.fixture
    def small(self):
        return extraction(SMALL_BOOK)

    def test_a_batch_is_one_transaction_and_one_insert_per_table(self, warehouse, con, small):
        recording = Recording(con)
        assert len(small.cells) > 250            # more rows than 9741b0e put in one INSERT
        warehouse.land(recording, small)
        transaction = [s for s, _ in recording.statements if s in ("BEGIN", "COMMIT")]
        assert transaction == ["BEGIN", "COMMIT"]
        assert recording.inserts() == ["raw_batch", "raw_plan_cell", "raw_control_total",
                                       "raw_list_account", "raw_list_entity"]
        assert counts(con) == {"raw_batch": 1, "raw_plan_cell": len(small.cells),
                               "raw_ledger_line": 0,
                               "raw_control_total": len(small.control_totals),
                               "raw_list_account": 13, "raw_list_entity": 1}

    def test_every_value_is_a_bound_parameter(self, warehouse, con, small):
        recording = Recording(con)
        warehouse.land(recording, small)
        inserts = [(s, p) for s, p in recording.statements if s.startswith("INSERT INTO")]
        assert inserts
        for statement, parameters in inserts:
            assert parameters and statement.count("?") == len(parameters), statement
            # No value is pasted into the text: no batch id, no figure.
            assert small.batch.batch_id not in statement
            assert not re.search(r"\d{3}", statement), statement

    def test_money_goes_as_text_and_is_cast(self, warehouse, con, small):
        recording = Recording(con)
        warehouse.land(recording, small)
        inserts = [(s, p) for s, p in recording.statements
                   if s.startswith("INSERT INTO raw_plan_cell")]
        assert inserts and all(f"AS {warehouse.MONEY})" in s for s, _ in inserts)
        sent = [x for _, parameters in inserts for p in parameters
                for x in (p if isinstance(p, list) else [p])]
        assert not [v for v in sent if isinstance(v, float)]
        amounts = {warehouse._money(c.amount) for c in small.cells if c.amount is not None}
        assert amounts <= {v for v in sent if isinstance(v, str)}

    def test_the_ledger_lands_the_same_way(self, warehouse, con):
        from src import ledger
        lines = ledger.read_ledger(READERS / "ledger_cases.csv", entities=("ES", "AT", "DE"),
                                   accounts=("4000", "4100", "5000"))
        recording = Recording(con)
        warehouse.land(recording, lines)
        assert recording.inserts() == ["raw_batch", "raw_ledger_line", "raw_control_total"]
        stored = con.execute("SELECT * FROM raw_ledger_line ORDER BY line_number").fetchall()
        assert stored == [tuple(dataclasses.asdict(l).values()) for l in lines.lines]

    def test_a_failed_insert_lands_nothing(self, warehouse, con, small):
        recording = Recording(con)
        real = recording.execute

        def fail_on_totals(statement, parameters=None):
            if statement.startswith("INSERT INTO raw_control_total"):
                raise RuntimeError("stopped on purpose")
            return real(statement, parameters)

        recording.execute = fail_on_totals
        with pytest.raises(RuntimeError):
            warehouse.land(recording, small)
        assert counts(con) == dict.fromkeys(RAW_TABLES, 0)


class TestTheBuild:
    """What the start-up build lands (src/startup.py; its steps are tests/test_startup.py's)."""

    def test_the_build_lands_the_sample_and_the_ledger_only(self, built_warehouse, built):
        assert [b.batch_id for b in built_warehouse.landed] == [file_sha256(p)[:16]
                                                                for p in (SAMPLE, LEDGER)]
        assert counts(built) == {"raw_batch": 2, "raw_plan_cell": 1521, "raw_ledger_line": 162,
                                 "raw_control_total": 39 + 6, "raw_list_account": 13,
                                 "raw_list_entity": 3}
        assert built.execute("SELECT file_name FROM raw_batch ORDER BY file_name").fetchall() \
            == [("erp_trial_balance.csv",), ("planning_model.xlsx",)]

    def test_raw_equals_what_was_read(self, built):
        untied = built.execute("""
            WITH raw AS (
                SELECT batch_id, version, period, count(*) AS n,
                       coalesce(sum(amount), 0) AS total FROM raw_plan_cell
                GROUP BY ALL
                UNION ALL
                SELECT batch_id, NULL, period, count(*), coalesce(sum(amount), 0)
                FROM raw_ledger_line GROUP BY ALL)
            SELECT count(*) FROM raw_control_total t FULL JOIN raw r
              ON t.batch_id = r.batch_id AND t.version IS NOT DISTINCT FROM r.version
             AND t.period = r.period
            WHERE t.row_count IS DISTINCT FROM r.n OR t.amount_sum IS DISTINCT FROM r.total
            """).fetchone()[0]
        assert untied == 0

    def test_the_unique_index_on_the_batch_id_is_there_after_the_build(self, built):
        indexes = built.execute("SELECT index_name, is_unique, expressions FROM duckdb_indexes() "
                                "WHERE table_name = 'raw_batch'").fetchall()
        assert len(indexes) == 1
        name, unique, expressions = indexes[0]
        assert unique and "batch_id" in str(expressions)

    def test_no_database_file_is_committed(self, warehouse):
        rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert {"*.duckdb", "*.duckdb.wal"} <= set(rules)
        assert warehouse.DATABASE_NAME.endswith(".duckdb")
        assert not [p for p in ROOT.rglob("*.duckdb*")
                    if not {".git", ".venv", "venv"} & set(p.relative_to(ROOT).parts)]

    def test_the_build_is_no_longer_in_this_module(self, warehouse):
        # One build, the start-up build of src/startup.py: landing is a step of it.
        assert not hasattr(warehouse, "build_warehouse")


class TestAppendOnly:
    def test_no_statement_changes_a_raw_row(self):
        sources = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "sql").rglob("*.sql")) \
            + sorted((ROOT / "scripts").rglob("*.py")) + [ROOT / "main.py"]
        found = {p.relative_to(ROOT).as_posix(): CHANGES_A_RAW_ROW.findall(
            p.read_text(encoding="utf-8")) for p in sources}
        assert {k: v for k, v in found.items() if v} == {}
        assert (ROOT / "src" / "warehouse.py") in sources

    def test_the_scan_finds_each_kind_of_change(self):
        for statement in ("UPDATE raw_plan_cell SET amount = 0",
                          "delete from raw_batch where batch_id = ?",
                          'DELETE FROM "raw_ledger_line"', "TRUNCATE raw_control_total",
                          "DROP TABLE IF EXISTS raw_batch", "ALTER TABLE raw_batch ADD x INT",
                          "INSERT OR REPLACE INTO raw_batch VALUES (1)",
                          "INSERT INTO raw_batch VALUES (1) ON CONFLICT DO UPDATE SET x = 1",
                          "UPDATE gate_acknowledgement SET person = ?",
                          "DELETE FROM gate_promotion", "DROP TABLE gate_break",
                          "CREATE OR REPLACE TABLE raw_list_account AS SELECT 1"):
            assert CHANGES_A_RAW_ROW.search(statement), statement
        for statement in ("INSERT INTO raw_batch VALUES (?)", "SELECT * FROM raw_plan_cell",
                          "UPDATE dim_account SET x = 1", "no row of a raw table is updated"):
            assert not CHANGES_A_RAW_ROW.search(statement), statement
