"""The star schema (sql/, run by src/model.py), part 1d, brief 6.4.

Plain SQL files run in order from Python: dim_version, dim_time (FY2026),
dim_entity, dim_account, fact_plan and fact_actual_erp, then the indexes. The
facts come from the promoted pair only, so at a fresh start they are empty.
The data tests run on a copy of the session warehouse in which every break of
the sample pair is acknowledged and the pair promoted, and on copies in which
a fixture is promoted instead, for the members the star schema must leave
out: names that fail the allow-list, unknown entities and accounts, and
external link targets.
"""

import shutil
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tests.extractions import FIXTURES, ROOT, SAMPLE, extraction, ledger

SQL = ROOT / "sql"
STAR = ("dim_version", "dim_time", "dim_entity", "dim_account", "fact_plan", "fact_actual_erp")
NOON = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
LINK_TARGET = "calder_plan_fy2025.xlsx"


def promote_in(con, plan, lines):
    """Lands plan beside the ledger, stores its breaks, acknowledges each one, and promotes.

    Promoting rebuilds the star schema from the promoted pair (part 1e), so no
    separate build follows.
    """
    from src import controls, promotion, reconcile, warehouse
    warehouse.land(con, plan)
    pair = promotion.Pair(plan.batch.batch_id, lines.batch.batch_id)
    promotion.store_breaks(con, pair, controls.run_controls(plan).breaks()
                           + reconcile.reconcile(con, plan, lines).breaks)
    for break_id in promotion.unacknowledged(con, pair):
        promotion.acknowledge(con, pair, break_id, "Test reviewer", "accepted",
                              clock=lambda: NOON)
    promotion.promote(con, pair, "Test promoter", clock=lambda: NOON)
    return pair


@pytest.fixture(scope="module")
def promoted(built_warehouse, tmp_path_factory):
    """A copy of the session warehouse with the sample pair promoted and the star schema built."""
    from src import warehouse
    path = tmp_path_factory.mktemp("promoted") / built_warehouse.path.name
    shutil.copyfile(built_warehouse.path, path)
    with warehouse.connect(path) as con:
        promote_in(con, extraction(SAMPLE), ledger())
    return path


@pytest.fixture
def star(promoted):
    from src import warehouse
    with warehouse.connect(promoted) as con:
        yield con


def promoted_fixture(tmp_path_factory, built_warehouse, name: str) -> Path:
    from src import warehouse
    path = tmp_path_factory.mktemp(name) / built_warehouse.path.name
    shutil.copyfile(built_warehouse.path, path)
    with warehouse.connect(path) as con:
        promote_in(con, extraction(FIXTURES / f"{name}.xlsx"), ledger())
    return path


def rows(con, sql, *parameters):
    return con.execute(sql, list(parameters)).fetchall()


class TestTheBuild:
    def test_the_sql_files_run_in_order(self):
        from src import model
        names = [p.name for p in model.sql_files()]
        assert names == sorted(names) == [
            "01_dim_version.sql", "02_dim_time.sql", "03_dim_entity.sql", "04_dim_account.sql",
            "05_fact_plan.sql", "06_fact_actual_erp.sql", "90_indexes.sql"]
        assert all(p.parent == SQL for p in model.sql_files())

    def test_at_a_fresh_start_the_facts_are_empty(self, built_warehouse):
        # Part 2a: dim_version is a fixed table of the three versions, like
        # dim_time, so it is not empty before a promotion; at 1e it was built
        # from the promoted batch and was empty until then.
        from src import warehouse
        with warehouse.connect(built_warehouse.path) as con:
            for table in STAR:
                count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                assert count == {"dim_time": 12, "dim_version": 3}.get(table, 0), table
            assert rows(con, "SELECT version, version_order, is_actual FROM dim_version ORDER BY "
                             "version_order") == [("Actual", 1, True), ("Budget", 2, False),
                                                  ("Forecast", 3, False)]

    def test_dim_version_is_written_like_dim_time(self):
        # The three versions are fixed in the SQL file; nothing in it reads the
        # promoted batch, so no other version can reach the star.
        text = (SQL / "01_dim_version.sql").read_text(encoding="utf-8")
        body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))
        for word in ("raw_plan_cell", "gate_promotion", "ELSE"):
            assert word not in body, word
        assert "('Actual', 1)" in body and "('Budget', 2)" in body and "('Forecast', 3)" in body

    def test_fact_plan_leaves_out_every_cell_of_blocks_that_overlap(self):
        # Part 2a2: a cell that belongs to two blocks is landed raw and never built; the
        # extractor flags every cell of each block in such a group (raw_plan_cell.overlap).
        text = (SQL / "05_fact_plan.sql").read_text(encoding="utf-8")
        body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))
        assert "AND NOT c.overlap" in body

    def test_the_star_tables_declare_no_key_and_the_indexes_come_last(self):
        texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(SQL.glob("*.sql"))}
        for name, text in texts.items():
            if name != "90_indexes.sql":
                assert "PRIMARY KEY" not in text.upper() and "INDEX" not in text.upper(), name
        assert texts["90_indexes.sql"].upper().count("CREATE UNIQUE INDEX") == 6

    def test_the_star_schema_reads_no_file_fact_and_changes_no_raw_row(self):
        for path in sorted(SQL.glob("*.sql")):
            text = path.read_text(encoding="utf-8")
            assert "raw_batch" not in text, path.name
            for word in ("UPDATE", "DELETE", "INSERT OR REPLACE", "TRUNCATE", "ALTER"):
                assert word not in text.upper(), (path.name, word)

    def test_building_twice_gives_the_same_tables(self, promoted, tmp_path):
        from src import model, warehouse
        copy = tmp_path / "again.duckdb"
        shutil.copyfile(promoted, copy)
        with warehouse.connect(copy) as con:
            before = {t: rows(con, f"SELECT * FROM {t} ORDER BY ALL") for t in STAR}
            model.build_model(con)
            assert {t: rows(con, f"SELECT * FROM {t} ORDER BY ALL") for t in STAR} == before


class TestTheDimensions:
    def test_dim_time_is_the_fy2026_calendar(self, star):
        found = rows(star, "SELECT period, fiscal_year, fiscal_quarter, month_number, month_name, "
                           "first_day, last_day FROM dim_time ORDER BY period")
        assert len(found) == 12
        assert found[0][:5] == ("2026-01", 2026, 1, 1, "January")
        assert found[1][6].isoformat() == "2026-02-28"
        assert [r[2] for r in found] == [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4]

    def test_dim_account_carries_what_lists_says(self, star):
        found = {r[0]: r[1:] for r in rows(
            star, "SELECT account, account_name, parent, section, ledger_sign, "
                  "sign_to_presentation, polarity, unit, line_kind FROM dim_account")}
        assert len(found) == 13
        assert found["4000"] == ("Product sales", "Revenue", "Profit and loss", "Credit", -1,
                                 "Favourable when higher", "EUR", "flow")
        assert found["6000"][3:5] == ("Debit", 1)
        assert found["GP"][2:5] == ("Subtotal", None, None)
        assert found["HC"] == ("Headcount", None, "Statistics", None, None,
                               "Favourable when lower", "FTE", "stock")

    def test_dim_entity_and_dim_version(self, star):
        assert rows(star, "SELECT entity, entity_name, region FROM dim_entity ORDER BY "
                          "list_order") == [
            ("ES", "Calder Foods Spain", "Southern Europe"),
            ("AT", "Calder Foods Austria", "Central Europe"),
            ("DE", "Calder Foods Germany", "Central Europe")]
        assert rows(star, "SELECT version, is_actual FROM dim_version ORDER BY version_order") \
            == [("Actual", True), ("Budget", False), ("Forecast", False)]


class TestTheFacts:
    def test_the_grain_is_unique(self, star):
        assert rows(star, "SELECT version, entity, account, period, count(*) FROM fact_plan "
                          "GROUP BY ALL HAVING count(*) > 1") == []
        assert rows(star, "SELECT entity, account, period, count(*) FROM fact_actual_erp "
                          "GROUP BY ALL HAVING count(*) > 1") == []

    def test_every_fact_key_is_in_its_dimension(self, star):
        for fact, keys in (("fact_plan", ("version", "entity", "account", "period")),
                           ("fact_actual_erp", ("entity", "account", "period"))):
            for key in keys:
                dim = {"version": "dim_version", "entity": "dim_entity",
                       "account": "dim_account", "period": "dim_time"}[key]
                orphans = rows(star, f"SELECT count(*) FROM {fact} f ANTI JOIN {dim} d "
                                     f"ON f.{key} = d.{key}")[0][0]
                assert orphans == 0, (fact, key)

    def test_the_facts_come_from_the_promoted_pair(self, star):
        promoted = rows(star, "SELECT plan_batch_id, ledger_batch_id FROM gate_promotion")
        assert promoted == [(extraction(SAMPLE).batch.batch_id, ledger().batch.batch_id)]
        assert rows(star, "SELECT DISTINCT batch_id FROM fact_plan") == [(promoted[0][0],)]
        assert rows(star, "SELECT DISTINCT batch_id FROM fact_actual_erp") == [(promoted[0][1],)]

    def test_fact_plan_totals_equal_the_raw_totals_for_its_batch_and_period(self, star):
        untied = rows(star, """
            SELECT t.version, t.period, t.amount_sum, f.total
            FROM raw_control_total t
            JOIN (SELECT batch_id, version, period, sum(amount) AS total FROM fact_plan
                  GROUP BY ALL) f
              ON f.batch_id = t.batch_id AND f.version = t.version AND f.period = t.period
            WHERE t.amount_sum <> f.total""")
        assert untied == []
        months = rows(star, "SELECT count(*) FROM raw_control_total t JOIN gate_promotion p "
                            "ON t.batch_id = p.plan_batch_id WHERE t.period LIKE '2026-%'")
        assert months == [(36,)]       # three versions, twelve months, all tied above

    def test_fact_actual_erp_totals_equal_the_raw_totals(self, star):
        untied = rows(star, """
            SELECT t.period FROM raw_control_total t
            JOIN (SELECT batch_id, period, sum(ledger_amount) AS total FROM fact_actual_erp
                  GROUP BY ALL) f ON f.batch_id = t.batch_id AND f.period = t.period
            WHERE t.amount_sum <> f.total""")
        assert untied == []
        assert rows(star, "SELECT count(DISTINCT period) FROM fact_actual_erp") == [(6,)]

    def test_the_ledger_sign_becomes_the_presentation_sign(self, star):
        revenue = rows(star, "SELECT min(f.amount), min(f.ledger_amount), max(f.ledger_amount) "
                             "FROM fact_actual_erp f JOIN dim_account a USING (account) "
                             "WHERE a.parent = 'Revenue'")[0]
        assert revenue[0] > 0 and revenue[2] < 0          # revenue positive, credit negative
        assert rows(star, "SELECT count(*) FROM fact_actual_erp f JOIN dim_account a "
                          "USING (account) WHERE f.amount <> f.ledger_amount * "
                          "a.sign_to_presentation") == [(0,)]
        t1 = rows(star, "SELECT amount, ledger_amount FROM fact_actual_erp WHERE entity = 'DE' "
                        "AND account = '6100' AND period = '2026-06'")
        assert t1 == [(Decimal("110995"), Decimal("110995"))]
        sales = rows(star, "SELECT amount FROM fact_actual_erp WHERE entity = 'ES' AND "
                           "account = '4000' AND period = '2026-01'")
        assert sales == [(Decimal("1445550"),)]

    def test_row_counts_equal_what_landed_less_what_is_left_out(self, star):
        plan = extraction(SAMPLE)
        landed = len(plan.cells)
        fy = sum(1 for c in plan.cells if c.column_kind == "total")
        empty = sum(1 for c in plan.cells if c.column_kind == "month" and c.amount is None)
        excluded = 0
        assert (landed, fy, empty) == (1521, 117, 245)
        assert rows(star, "SELECT count(*) FROM fact_plan") == [(landed - fy - empty - excluded,)]
        assert rows(star, "SELECT count(*) FROM fact_actual_erp") == [(162,)]


class TestWhatIsLeftOut:
    def test_names_that_fail_the_allow_list_are_absent(self, built_warehouse, tmp_path_factory):
        from src import warehouse
        path = promoted_fixture(tmp_path_factory, built_warehouse, "names")
        rejected = {r.name for r in extraction(FIXTURES / "names.xlsx").rejected_names}
        assert len(rejected) == 3
        with warehouse.connect(path) as con:
            accounts = {r[0] for r in rows(con, "SELECT account FROM dim_account")}
            assert {"4000", "5100", "6000"}.isdisjoint(accounts) and len(accounts) == 10
            for fact in ("fact_plan", "fact_actual_erp"):
                assert rows(con, f"SELECT count(*) FROM {fact} WHERE account IN "
                                 "('4000', '5100', '6000')") == [(0,)]
            assert not rejected & star_text(con)
            # Stated: the rows left out are the three accounts' month cells with a figure.
            plan = extraction(FIXTURES / "names.xlsx")
            left_out = sum(1 for c in plan.cells if c.column_kind == "month"
                           and c.amount is not None and not c.name_allowed)
            kept = sum(1 for c in plan.cells if c.column_kind == "month"
                       and c.amount is not None and c.name_allowed)
            assert left_out == 3 * 3 * 12 - 3 * 6
            assert rows(con, "SELECT count(*) FROM fact_plan") == [(kept,)]

    def test_unknown_entities_and_accounts_are_absent(self, built_warehouse, tmp_path_factory):
        from src import warehouse
        path = promoted_fixture(tmp_path_factory, built_warehouse, "unknown_account")
        with warehouse.connect(path) as con:
            assert rows(con, "SELECT DISTINCT entity FROM fact_plan") == [("ES",)]
            # The ledger's AT and DE lines are unknown to this plan batch's Lists.
            assert rows(con, "SELECT DISTINCT entity FROM fact_actual_erp") == [("ES",)]
            assert rows(con, "SELECT count(*) FROM fact_plan WHERE account IS NULL") == [(0,)]
            assert "Cases sold" not in star_text(con)

    def test_external_link_targets_are_absent(self, built_warehouse, tmp_path_factory):
        from src import warehouse
        path = promoted_fixture(tmp_path_factory, built_warehouse, "external_link")
        with warehouse.connect(path) as con:
            assert rows(con, "SELECT count(*) FROM raw_batch WHERE external_links LIKE ?",
                        f"%{LINK_TARGET}%") == [(1,)]
            assert not any(LINK_TARGET in text for text in star_text(con))
            assert rows(con, "SELECT count(*) FROM fact_plan") != [(0,)]


def star_text(con) -> set:
    """Every text value in every star table."""
    found = set()
    for table in STAR:
        columns = [r[0] for r in rows(con, "SELECT column_name FROM information_schema.columns "
                                           "WHERE table_name = ? AND data_type = 'VARCHAR'",
                                      table)]
        for column in columns:
            found |= {r[0] for r in rows(con, f"SELECT DISTINCT {column} FROM {table}")
                      if r[0] is not None}
    return found
