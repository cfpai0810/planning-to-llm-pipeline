"""The start-up build (src/startup.py), part 1d.

One function runs every step of the reviewer's list, in order: read the
workbook, read the ledger, land, the controls, reconcile (storing the pair's
breaks), and the star schema. It records each step's time with an injected
clock. Phase 2 calls it once per process; scripts/time_build.py profiles it.
At start nothing is promoted, so the star schema's facts are empty.
"""

import inspect
import shutil

import pytest

from tests.extractions import FIXTURES, ROOT, SAMPLE, extraction, ledger
from tests.test_warehouse import Recording

LEDGER = ROOT / "data" / "erp_trial_balance.csv"
SMALL_BOOK = FIXTURES / "extra_tab.xlsx"
SMALL_LEDGER = FIXTURES / "readers" / "ledger_bom.csv"
STEPS = ("read workbook", "read ledger", "land", "controls", "reconcile", "star schema")


@pytest.fixture(scope="module")
def startup():
    from src import startup
    return startup


class TestTheSessionBuild:
    def test_it_ran_every_step_in_order(self, built_warehouse):
        assert tuple(name for name, _ in built_warehouse.steps) == STEPS
        assert all(seconds >= 0 for _, seconds in built_warehouse.steps)

    def test_it_lands_the_sample_and_the_ledger_from_the_committed_files(self, startup,
                                                                          built_warehouse):
        defaults = inspect.signature(startup.build_warehouse).parameters
        assert defaults["workbook"].default == SAMPLE
        assert defaults["ledger"].default == LEDGER
        assert built_warehouse.pair.plan_batch_id == extraction(SAMPLE).batch.batch_id
        assert built_warehouse.pair.ledger_batch_id == ledger().batch.batch_id

    def test_it_stores_the_pairs_breaks_and_promotes_nothing(self, built_warehouse):
        from src import promotion, warehouse
        assert len(built_warehouse.breaks) == 8
        with warehouse.connect(built_warehouse.path) as con:
            assert len(promotion.breaks_of(con, built_warehouse.pair)) == 8
            assert promotion.promoted_batch(con) is None
            assert con.execute("SELECT count(*) FROM fact_plan").fetchone()[0] == 0


class TestTheSteps:
    def test_each_step_is_timed_with_the_clock_it_is_given(self, startup, tmp_path):
        ticks = iter([0.0, 1.0, 2.0, 4.0, 7.0, 11.0, 16.0])
        built = startup.build_warehouse(tmp_path / "small", workbook=SMALL_BOOK,
                                        ledger=SMALL_LEDGER, clock=lambda: next(ticks))
        assert built.steps == tuple(zip(STEPS, (1.0, 1.0, 2.0, 3.0, 4.0, 5.0)))

    def test_every_index_comes_after_its_table_is_loaded(self, startup, tmp_path, monkeypatch):
        from src import warehouse
        recordings = []
        real = warehouse.connect

        def recorded(path):
            recordings.append(Recording(real(path)))
            return recordings[-1]

        monkeypatch.setattr(warehouse, "connect", recorded)
        startup.build_warehouse(tmp_path / "small", workbook=SMALL_BOOK, ledger=SMALL_LEDGER)
        statements = [" ".join(s.split()).upper() for s, _ in recordings[0].statements]
        indexes = [(i, s.split(" ON ")[1].split()[0].lower()) for i, s in enumerate(statements)
                   if s.startswith("CREATE UNIQUE INDEX")]
        assert {table for _, table in indexes} >= {"raw_batch", "gate_break", "fact_plan"}
        for at, table in indexes:
            loads = [i for i, s in enumerate(statements)
                     if s.startswith((f"INSERT INTO {table.upper()} ",
                                      f"CREATE OR REPLACE TABLE {table.upper()} "))]
            assert loads and max(loads) < at, table

    def test_a_build_needs_a_fresh_location(self, startup, tmp_path):
        from src import warehouse
        (tmp_path / warehouse.DATABASE_NAME).write_bytes(b"")
        with pytest.raises(warehouse.WarehouseError):
            startup.build_warehouse(tmp_path, workbook=SMALL_BOOK, ledger=SMALL_LEDGER)

    def test_a_built_folder_can_be_removed(self, startup, tmp_path):
        # Every connection is closed, so the folder can go (Windows keeps an
        # open database file, or its .wal, locked).
        folder = tmp_path / "small"
        built = startup.build_warehouse(folder, workbook=SMALL_BOOK, ledger=SMALL_LEDGER)
        assert built.path.parent == folder and built.path.is_file()
        assert not list(folder.glob("*.wal"))
        shutil.rmtree(folder)
        assert not folder.exists()
