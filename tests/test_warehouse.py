"""Landing in the warehouse (src/warehouse.py), part 1c, brief 5.4.

DuckDB opens in one function, warehouse.connect, and nowhere else (the
boundary test in tests/test_boundaries.py). The four raw tables are append
only: a batch is landed once, its id comes from the file's content hash, and
landing the same file again returns a typed "already landed" and adds no row.
Money is stored as DECIMAL(38, 12), which holds every amount the readers give
exactly; an amount it could not hold exactly refuses the batch before any row
is written. The warehouse is built from the committed sources into a fresh
folder, closed and reopened.

DuckDB has an HTTP client of its own, which the suite's network guard cannot
see. So the connection is opened with extension autoinstall and autoload off,
community extensions refused, external access off and the configuration
locked, and TestTheNetworkSettings reads those settings back from DuckDB
itself: that test is the guard.
"""

import dataclasses
import json
import re
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from src.hashing import file_sha256

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "planning_model.xlsx"
CLEAN = ROOT / "data" / "planning_model_clean.xlsx"
LEDGER = ROOT / "data" / "erp_trial_balance.csv"
READERS = ROOT / "tests" / "fixtures" / "readers"
RAW_TABLES = ("raw_batch", "raw_plan_cell", "raw_ledger_line", "raw_control_total")
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
}
# The statements that would change or remove a landed row, against a raw table.
CHANGES_A_RAW_ROW = re.compile(
    r"\b(?:UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?|DROP\s+TABLE(?:\s+IF\s+EXISTS)?|"
    r"ALTER\s+TABLE|INSERT\s+OR\s+REPLACE\s+INTO|REPLACE\s+INTO)\s+[\"'`]?raw_\w+"
    r"|\bON\s+CONFLICT\b", re.IGNORECASE)


@pytest.fixture(scope="module")
def warehouse():
    from src import warehouse
    return warehouse


@pytest.fixture(scope="module")
def extract():
    from src import extract
    return extract


@pytest.fixture(scope="module")
def sample(extract):
    return extract.extract_workbook(SAMPLE)


@pytest.fixture(scope="module")
def ledger_lines(sample):
    from src import ledger
    return ledger.read_ledger(LEDGER, entities=sample.lists.entity_codes(),
                              accounts=sample.lists.ledger_accounts())


@pytest.fixture
def con(warehouse, tmp_path):
    connection = warehouse.connect(tmp_path / "warehouse.duckdb")
    warehouse.create_tables(connection)
    yield connection
    connection.close()


def counts(con) -> dict:
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in RAW_TABLES}


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


class TestLanding:
    def test_the_sample_lands_row_for_row(self, con, warehouse, sample):
        landed = warehouse.land(con, sample)
        assert isinstance(landed, warehouse.Landed)
        assert (landed.batch_id, landed.rows) == (sample.batch.batch_id, len(sample.cells))
        stored = con.execute("SELECT * EXCLUDE (row_number) FROM raw_plan_cell "
                             "WHERE batch_id = ? ORDER BY row_number",
                             [sample.batch.batch_id]).fetchall()
        assert stored == [tuple(dataclasses.asdict(c).values()) for c in sample.cells]

    def test_the_batch_record_lands(self, con, warehouse, sample):
        warehouse.land(con, sample)
        row = con.execute("SELECT * FROM raw_batch").fetchall()
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

    def test_the_control_totals_are_pythons_and_tie_to_the_rows(self, con, warehouse, sample):
        warehouse.land(con, sample)
        stored = con.execute("SELECT version, period, row_count, amount_sum FROM "
                             "raw_control_total ORDER BY version, period").fetchall()
        assert stored == sorted((t.version, t.period, t.row_count, t.amount_sum)
                                for t in sample.control_totals)
        recomputed = con.execute(
            "SELECT version, period, count(*), coalesce(sum(amount), 0) FROM raw_plan_cell "
            "GROUP BY version, period ORDER BY version, period").fetchall()
        assert recomputed == stored

    def test_landing_the_same_file_twice_adds_no_rows(self, con, warehouse, sample, extract):
        warehouse.land(con, sample)
        before = counts(con)
        again = warehouse.land(con, extract.extract_workbook(SAMPLE))
        assert isinstance(again, warehouse.AlreadyLanded)
        assert (again.batch_id, again.file_name) == (sample.batch.batch_id, "planning_model.xlsx")
        assert counts(con) == before

    def test_the_ledger_lands(self, con, warehouse, ledger_lines):
        landed = warehouse.land(con, ledger_lines)
        assert landed.rows == 162
        stored = con.execute("SELECT * FROM raw_ledger_line ORDER BY line_number").fetchall()
        assert stored == [tuple(dataclasses.asdict(l).values()) for l in ledger_lines.lines]
        assert con.execute("SELECT source, has_formulas, calculation FROM raw_batch").fetchone() \
            == ("ledger", False, "not applicable: the source has no formulas")
        assert con.execute("SELECT count(*) FROM raw_control_total WHERE version IS NULL") \
            .fetchone()[0] == 6
        assert warehouse.land(con, ledger_lines) == warehouse.AlreadyLanded(
            ledger_lines.batch.batch_id, "erp_trial_balance.csv")

    def test_a_long_format_file_lands_as_plan_cells(self, con, warehouse):
        from src import long_format
        source = long_format.read_long_format(READERS / "long_format_cases.csv",
                                              entities=("ES", "AT", "DE"), accounts=("4000",))
        warehouse.land(con, source)
        stored = con.execute("SELECT * EXCLUDE (row_number) FROM raw_plan_cell "
                             "ORDER BY row_number").fetchall()
        assert stored == [tuple(dataclasses.asdict(c).values()) for c in source.cells]
        assert con.execute("SELECT has_formulas FROM raw_batch").fetchone() == (False,)

    def test_amounts_round_trip_exactly(self, con, warehouse, sample, tmp_path, extract):
        warehouse.land(con, sample)
        headcount = con.execute(
            "SELECT amount FROM raw_plan_cell WHERE version = 'Forecast' AND entity = 'ES' "
            "AND line = 'Headcount' AND period = '2026-01'").fetchone()[0]
        assert headcount == Decimal("63.5") and headcount.normalize() == Decimal("63.5")
        cell = sample.cells[0]
        made = [dataclasses.replace(cell, batch_id="made", address=f"Z{i}", amount=Decimal(text))
                for i, text in enumerate(["0.1", "9007199254740993", "-1490400.00",
                                          "12345678901234567890123456.123456789012",
                                          "-0.000000000001"])]
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


class TestTheBuild:
    def test_the_warehouse_is_built_from_the_committed_sources_and_reopened(self, warehouse,
                                                                            tmp_path):
        folder = tmp_path / "built"
        built = warehouse.build_warehouse(folder)
        assert built.path.parent == folder and built.path.is_file()
        assert [b.batch_id for b in built.landed] == [file_sha256(p)[:16]
                                                      for p in (SAMPLE, CLEAN, LEDGER)]
        with warehouse.connect(built.path) as con:
            assert counts(con) == {"raw_batch": 3, "raw_plan_cell": 2 * 1521,
                                   "raw_ledger_line": 162,
                                   "raw_control_total": 2 * 39 + 6}
            assert con.execute("SELECT file_name FROM raw_batch ORDER BY file_name").fetchall() \
                == [("erp_trial_balance.csv",), ("planning_model.xlsx",),
                    ("planning_model_clean.xlsx",)]
            untied = con.execute("""
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
        # Every connection is closed, so the folder can go (Windows keeps an
        # open database file, or its .wal, locked).
        assert not list(folder.glob("*.wal"))
        shutil.rmtree(folder)
        assert not folder.exists()

    def test_no_database_file_is_committed(self, warehouse):
        rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert {"*.duckdb", "*.duckdb.wal"} <= set(rules)
        assert warehouse.DATABASE_NAME.endswith(".duckdb")
        assert not [p for p in ROOT.rglob("*.duckdb*")
                    if not {".git", ".venv", "venv"} & set(p.relative_to(ROOT).parts)]

    def test_a_build_needs_a_fresh_location(self, warehouse, tmp_path):
        warehouse.build_warehouse(tmp_path / "once")
        with pytest.raises(warehouse.WarehouseError):
            warehouse.build_warehouse(tmp_path / "once")


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
                          "INSERT INTO raw_batch VALUES (1) ON CONFLICT DO UPDATE SET x = 1"):
            assert CHANGES_A_RAW_ROW.search(statement), statement
        for statement in ("INSERT INTO raw_batch VALUES (?)", "SELECT * FROM raw_plan_cell",
                          "UPDATE dim_account SET x = 1", "no row of a raw table is updated"):
            assert not CHANGES_A_RAW_ROW.search(statement), statement
