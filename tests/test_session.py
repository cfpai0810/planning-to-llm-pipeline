"""The template warehouse, the session and provenance (part 2c, the reviewer's brief 5.2, 5.3, 5.5).

The template holds both pairs the Review page will offer, the sample workbook and the clean
control workbook, each with the one committed ledger, landed once: each pair's breaks are
stored apart and nothing is promoted. It is built once per test session (tests/conftest.py)
and left read-only on disk. A session is one visitor's copy of it, opened read-only; a write
runs as a cycle (close, open read-write, work, close, reopen read-only) under a lock no query
runs through. The figures of the two-session test are read from the oracle's committed
expected figures (tests/generated/oracle_expected.csv), never summed here.

Commit 3a (the reviewer's verdict of 11 November, F25): what only a user other than root can
show, that no one may write to the template, is a test of its own, skipped when the suite
runs as root.

Part 3b (the reviewer's Phase 3 brief, 4.7): provenance takes a role, always; every provenance
says its break counts are pair-wide (F27), and an entity view's names no other entity; the
engine's message reaches the caller who asks for it, for the audit, while the outcome a reader
sees stays the one fixed object; and a pool needs a root of its own and never removes a
folder under another root (F26).
"""

import csv
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tests.conftest import subprocess_env

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "planning_model.xlsx"
CLEAN = ROOT / "data" / "planning_model_clean.xlsx"
LEDGER = ROOT / "data" / "erp_trial_balance.csv"
ORACLE = ROOT / "tests" / "generated" / "oracle_expected.csv"
REGISTRY_FILE = ROOT / "registry" / "metrics.toml"
NOON = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
# The sample's eight breaks, by hand (part 1d, kept through 2a2).
SAMPLE_IDS = {"FI-64e07c883fff", "FI-facc450f1682", "HC-b04c76a19ed4", "HC-d72689858228",
              "TO-417d669f6dd1", "RC-86947f88dac3", "RC-99097fa7fd05", "RC-a87231377067"}
# One fixed read-only question, before the compiler exists (the coordinator's brief): the
# group's FY2026 revenue in one version, from fact_plan, accounts 4000 and 4100.
QUESTION = ("SELECT sum(amount) FROM fact_plan WHERE version = ? AND account IN ('4000', '4100') "
            "AND period LIKE '2026-%'")
# A run as root (a container, say): root may write to any file whatever its mode (F25).
RUN_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def oracle(pair: str, metric: str, version: str, period: str = "FY2026") -> Decimal:
    """The oracle's committed figure for the group, no grouping, no comparison."""
    with ORACLE.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row["pair"], row["metric"], row["version"], row["comparison"], row["entities"],
                    row["period"], row["group_by"]) == (pair, metric, version, "none", "group",
                                                         period, "none"):
                assert row["outcome"] == "ok"
                return Decimal(row["value"])
    raise LookupError((pair, metric, version, period))


def ask(session, version: str) -> Decimal:
    with session.reading() as cursor:
        return cursor.execute(QUESTION, [version]).fetchone()[0]


def acknowledge_all(session, name: str) -> None:
    pair = session.pairs[name]
    session.acknowledge_many(pair, [(b.break_id, "A. Reviewer", "accepted")
                                    for b in session.breaks(pair)], clock=lambda: NOON)


@pytest.fixture(scope="module")
def session_module():
    from src import session
    return session


@pytest.fixture
def sessions(template, tmp_path, session_module):
    pool = session_module.Sessions(template, root=tmp_path / "sessions")
    yield pool
    pool.close_all()


# -- 5.2 The template -----------------------------------------------------------------------

class TestTheTemplate:
    def test_both_pairs_land_with_one_ledger_landed_once(self, template):
        from src import warehouse
        assert list(template.pairs) == ["sample", "clean"]
        sample, clean = template.pairs["sample"], template.pairs["clean"]
        assert sample.ledger_batch_id == clean.ledger_batch_id
        with warehouse.connect(template.path, read_only=True) as con:
            batches = con.execute("SELECT batch_id, source, file_name FROM raw_batch "
                                  "ORDER BY file_name").fetchall()
        assert batches == [(sample.ledger_batch_id, "ledger", "erp_trial_balance.csv"),
                           (sample.plan_batch_id, "workbook", "planning_model.xlsx"),
                           (clean.plan_batch_id, "workbook", "planning_model_clean.xlsx")]

    def test_each_pairs_breaks_are_kept_apart(self, template):
        from src import promotion, warehouse
        with warehouse.connect(template.path, read_only=True) as con:
            sample = promotion.breaks_of(con, template.pairs["sample"])
            clean = promotion.breaks_of(con, template.pairs["clean"])
            reviews = con.execute("SELECT plan_batch_id, breaks FROM gate_review "
                                  "ORDER BY breaks").fetchall()
        assert {b.break_id for b in sample} == SAMPLE_IDS and len(sample) == 8
        assert clean == ()
        assert reviews == [(template.pairs["clean"].plan_batch_id, 0),
                           (template.pairs["sample"].plan_batch_id, 8)]
        assert [b.break_id for b in template.breaks["sample"]] == [b.break_id for b in sample]
        assert template.breaks["clean"] == ()

    def test_nothing_is_promoted(self, template):
        from src import promotion, warehouse
        with warehouse.connect(template.path, read_only=True) as con:
            assert promotion.promoted_batch(con) is None
            assert con.execute("SELECT count(*) FROM fact_plan").fetchone() == (0,)
            assert con.execute("SELECT count(*) FROM dim_version").fetchone() == (3,)

    def test_the_file_is_closed_and_read_only_on_disk(self, template):
        # Its mode has no write bit for anyone, and the folder holds the file alone (closed:
        # no write-ahead log beside it). Both hold whoever runs the suite.
        assert not stat.S_IMODE(os.stat(template.path).st_mode) & (
            stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        assert sorted(p.name for p in template.path.parent.iterdir()) == [template.path.name]

    @pytest.mark.skipif(RUN_AS_ROOT, reason="the effective user is root, and root may write to "
                                            "any file whatever its mode: os.access and a "
                                            "read-write open say nothing about the file there")
    def test_no_one_but_root_can_write_to_it(self, template):
        # F25 (the reviewer's verdict of 11 November): split from the test above, so that a
        # run as root skips only what root changes.
        import duckdb

        from src import warehouse
        assert not os.access(template.path, os.W_OK)
        with pytest.raises(duckdb.IOException):
            warehouse.connect(template.path)
        assert sorted(p.name for p in template.path.parent.iterdir()) == [template.path.name]

    def test_its_steps_are_timed_in_order(self, template):
        from src import startup
        assert [step for step, _ in template.steps] == list(startup.TEMPLATE_STEPS)
        assert all(seconds >= 0 for _, seconds in template.steps)

    def test_a_template_is_built_fresh_never_on_top_of_another(self, template):
        from src import startup, warehouse
        with pytest.raises(warehouse.WarehouseError, match="already exists"):
            startup.build_template(template.path.parent, sample=SAMPLE, clean=CLEAN,
                                   ledger=LEDGER)


# -- 5.3 The session ------------------------------------------------------------------------

class TestTheSession:
    def test_open_copies_the_template_and_opens_it_read_only(self, sessions, template,
                                                             session_module):
        before = hashlib.sha256(template.path.read_bytes()).hexdigest()
        s = sessions.open()
        assert s.path != template.path and s.path.parent.name.startswith(session_module.PREFIX)
        assert s.path.read_bytes() == template.path.read_bytes()
        # The copy is shutil.copyfile's, so it does not carry the template's read-only bit.
        assert os.access(s.path, os.W_OK)
        with s.reading() as cursor:
            assert cursor.execute("SELECT current_setting('access_mode')").fetchone() == (
                "read_only",)
        assert hashlib.sha256(template.path.read_bytes()).hexdigest() == before

    def test_each_request_takes_a_cursor_of_its_own(self, sessions):
        s = sessions.open()
        with s.reading() as first:
            with s.reading() as second:
                assert first is not second
                assert first.execute("SELECT 1").fetchone() == (1,)
                assert second.execute("SELECT 2").fetchone() == (2,)
        with pytest.raises(Exception):
            first.execute("SELECT 1")

    def test_nothing_is_promoted_at_start(self, sessions):
        from src import outcomes
        s = sessions.open()
        assert s.promoted() == outcomes.NOTHING_PROMOTED
        assert (s.promoted().code, s.promoted().sentence) == (
            "nothing_promoted", "Nothing has been promoted yet. Review a batch first.")

    def test_a_write_is_a_cycle_and_the_session_is_read_only_again_after(self, sessions):
        s = sessions.open()
        with s.writing() as con:
            assert con.execute("SELECT current_setting('access_mode')").fetchone() == (
                "automatic",)
            con.execute("CREATE TABLE scratch_note (a INTEGER)")
        with s.reading() as cursor:
            assert cursor.execute("SELECT current_setting('access_mode')").fetchone() == (
                "read_only",)
            assert cursor.execute("SELECT count(*) FROM scratch_note").fetchone() == (0,)
            with pytest.raises(Exception, match="read-only mode"):
                cursor.execute("INSERT INTO scratch_note VALUES (1)")

    def test_acknowledge_many_uses_one_cycle(self, sessions, monkeypatch):
        from src import warehouse
        s = sessions.open()
        opened = []
        real = warehouse.connect

        def counting(path, *, read_only=False):
            opened.append(read_only)
            return real(path, read_only=read_only)
        monkeypatch.setattr(warehouse, "connect", counting)
        acknowledge_all(s, "sample")
        assert opened == [False, True]                # one read-write, then read-only again
        from src import promotion
        with s.reading() as cursor:
            assert promotion.unacknowledged(cursor, s.pairs["sample"]) == ()
            given = promotion.acknowledgements(cursor, s.pairs["sample"])
        assert len(given) == 8 and {a.person for a in given} == {"A. Reviewer"}

    def test_promote_wraps_promotion_promote_in_a_cycle(self, sessions):
        from src import promotion
        s = sessions.open()
        with pytest.raises(promotion.PromotionError, match="8 breaks have no acknowledgement"):
            s.promote(s.pairs["sample"], "P. Promoter", clock=lambda: NOON)
        acknowledge_all(s, "sample")
        done = s.promote("sample", "P. Promoter", clock=lambda: NOON)
        assert (done.number, done.pair, done.promoted_by) == (1, s.pairs["sample"], "P. Promoter")
        assert s.promoted() == done
        with s.reading() as cursor:
            # The sample's month cells with a figure (tests/test_model.py): 1521 less 117 FY
            # cells and 245 empty ones.
            assert cursor.execute("SELECT count(*) FROM fact_plan").fetchone() == (1159,)

    def test_two_sessions_from_one_template_give_their_own_answers(self, sessions):
        # The reviewer's required test (5.3). One promotes the clean pair; the other still
        # answers nothing_promoted; then the second promotes the sample pair and the two give
        # different, correct answers to the same question.
        from src import outcomes
        first, second = sessions.open(), sessions.open()
        first.promote("clean", "P. Promoter", clock=lambda: NOON)
        assert second.promoted() == outcomes.NOTHING_PROMOTED
        assert ask(second, "Forecast") is None
        acknowledge_all(second, "sample")
        second.promote("sample", "P. Promoter", clock=lambda: NOON)
        clean, sample = ask(first, "Forecast"), ask(second, "Forecast")
        assert clean == oracle("clean", "revenue", "Forecast") == 63686455
        assert sample == oracle("sample", "revenue", "Forecast") == 63720173
        assert clean != sample
        assert first.promoted().pair == first.pairs["clean"]
        assert second.promoted().pair == second.pairs["sample"]

    def test_a_break_of_one_pair_is_not_a_break_of_the_other(self, sessions):
        from src import promotion
        s = sessions.open()
        some = next(iter(SAMPLE_IDS))
        with pytest.raises(promotion.PromotionError, match="not one of this pair's breaks"):
            s.acknowledge_many("clean", [(some, "A. Reviewer", "accepted")])


# -- 5.3 Bounds ------------------------------------------------------------------------------

class TestTheBounds:
    def test_at_most_twenty_sessions_by_default(self, template, tmp_path, session_module):
        import config
        assert config.MAX_SESSIONS == 20
        assert session_module.Sessions(template, root=tmp_path).max_sessions == 20

    def test_one_more_closes_the_least_recently_used(self, template, tmp_path, session_module):
        pool = session_module.Sessions(template, root=tmp_path / "s", max_sessions=2)
        try:
            a, b = pool.open(), pool.open()
            ask(a, "Budget")                       # a is now the most recently used
            c = pool.open()
            assert b.closed and not b.path.parent.exists()
            assert not a.closed and not c.closed and len(pool) == 2
            ask(c, "Budget")
            d = pool.open()
            assert a.closed and not c.closed and not d.closed
        finally:
            pool.close_all()

    def test_a_folder_is_removed_on_close(self, sessions):
        s = sessions.open()
        folder = s.path.parent
        s.close()
        assert s.closed and not folder.exists() and len(sessions) == 0
        s.close()                                   # closing twice is harmless

    def test_folders_left_at_exit_are_removed(self, template, tmp_path):
        root = tmp_path / "exit"
        code = ("import sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "from src.batch import Pair\n"
                "from src.session import Sessions\n"
                "from src.startup import Template\n"
                "pairs = {'sample': Pair(sys.argv[3], sys.argv[5]),"
                " 'clean': Pair(sys.argv[4], sys.argv[5])}\n"
                "pool = Sessions(Template(Path(sys.argv[2]), pairs, {}, ()), root=Path(sys.argv[6]))\n"
                "s = pool.open()\n"
                "print(s.path.parent)\n")
        p = template.pairs
        done = subprocess.run(
            [sys.executable, "-c", code, str(ROOT), str(template.path), p["sample"].plan_batch_id,
             p["clean"].plan_batch_id, p["sample"].ledger_batch_id, str(root)],
            capture_output=True, text=True, env=subprocess_env(), timeout=120)
        assert done.returncode == 0, done.stderr
        folder = Path(done.stdout.strip())
        assert folder.parent == root and not folder.exists()

    def test_old_folders_with_the_prefix_are_removed_at_start(self, template, tmp_path,
                                                             session_module):
        root = tmp_path / "start"
        old = root / f"{session_module.PREFIX}old"
        fresh = root / f"{session_module.PREFIX}fresh"
        other = root / "someone_elses_old_folder"
        for folder in (old, fresh, other):
            folder.mkdir(parents=True)
            (folder / "warehouse.duckdb").write_bytes(b"x")
        two_days = time.time() - 2 * 24 * 3600
        for folder in (old, other):
            os.utime(folder, (two_days, two_days))
        pool = session_module.Sessions(template, root=root)
        try:
            assert not old.exists() and fresh.exists() and other.exists()
        finally:
            pool.close_all()


# -- 5.3 The lock -----------------------------------------------------------------------------

class TestTheLock:
    def test_no_query_runs_during_a_write_cycle(self, sessions):
        s = sessions.open()
        order, inside = [], threading.Event()

        def write():
            with s.writing():
                order.append("write starts")
                inside.set()
                time.sleep(0.4)
                order.append("write ends")

        writer = threading.Thread(target=write)
        writer.start()
        assert inside.wait(30)

        def read():
            with s.reading() as cursor:
                order.append("query runs")
                cursor.execute("SELECT count(*) FROM raw_batch").fetchone()

        reader = threading.Thread(target=read)
        reader.start()
        writer.join(30)
        reader.join(30)
        assert order == ["write starts", "write ends", "query runs"]

    def test_a_write_cycle_waits_for_a_query_that_is_running(self, sessions):
        s = sessions.open()
        order, inside = [], threading.Event()

        def read():
            with s.reading() as cursor:
                order.append("query starts")
                inside.set()
                time.sleep(0.4)
                cursor.execute("SELECT count(*) FROM raw_batch").fetchone()
                order.append("query ends")

        reader = threading.Thread(target=read)
        reader.start()
        assert inside.wait(30)

        def write():
            with s.writing():
                order.append("write runs")

        writer = threading.Thread(target=write)
        writer.start()
        reader.join(30)
        writer.join(30)
        assert order == ["query starts", "query ends", "write runs"]


# -- 5.5 Provenance ---------------------------------------------------------------------------

class TestProvenance:
    # Changed on purpose in 3b: provenance takes a role (the group controller here, which the
    # session's own code would pass), and every provenance carries breaks_scope.
    def test_with_nothing_promoted_it_says_so(self, sessions):
        from src import roles, warehouse
        p = sessions.open().provenance(roles.group_controller)
        registry = hashlib.sha256(REGISTRY_FILE.read_bytes()).hexdigest()
        assert p == {"promoted": False, "outcome": "nothing_promoted",
                     "sentence": "Nothing has been promoted yet. Review a batch first.",
                     "breaks_scope": "pair-wide", "view": "group view",
                     "registry_version": "1.0.0", "registry_hash": registry,
                     "engine": "DuckDB", "engine_version": warehouse.ENGINE_VERSION}
        assert registry.startswith("1e682403") and registry.endswith("7de4c0")

    def test_after_promoting_the_sample_pair(self, sessions):
        from src import roles
        from src.hashing import file_sha256
        s = sessions.open()
        acknowledge_all(s, "sample")
        s.promote("sample", "P. Promoter", clock=lambda: NOON)
        p = s.provenance(roles.group_controller)
        pair = s.pairs["sample"]
        assert {k: v for k, v in p.items() if not k.startswith(("registry", "engine"))} == {
            "promoted": True, "plan_batch_id": pair.plan_batch_id,
            "plan_file_name": "planning_model.xlsx", "plan_file_sha256": file_sha256(SAMPLE),
            "ledger_batch_id": pair.ledger_batch_id, "ledger_file_name": "erp_trial_balance.csv",
            "ledger_file_sha256": file_sha256(LEDGER), "promotion_number": 1,
            "promoted_by": "P. Promoter", "promoted_at": "2026-11-08T12:00:00Z",
            "breaks": {"high": 3, "medium": 5, "low": 0}, "breaks_scope": "pair-wide",
            "acknowledged": 8, "accepted": 8, "dismissed": 0, "investigated": 0,
            "view": "group view"}

    def test_a_breaks_latest_action_is_the_one_counted(self, sessions):
        from src import roles
        s = sessions.open()
        acknowledge_all(s, "sample")
        pair = s.pairs["sample"]
        first, second = sorted(SAMPLE_IDS)[:2]
        s.acknowledge_many(pair, [(first, "B. Checker", "investigated"),
                                  (second, "B. Checker", "dismissed")], clock=lambda: NOON)
        s.promote(pair, "P. Promoter", clock=lambda: NOON)
        p = s.provenance(roles.group_controller)
        assert (p["acknowledged"], p["accepted"], p["dismissed"], p["investigated"]) == (8, 6, 1, 1)

    def test_it_names_no_cell_content(self, sessions):
        from src import roles
        s = sessions.open()
        acknowledge_all(s, "sample")
        s.promote("sample", "P. Promoter", clock=lambda: NOON)
        p = s.provenance(roles.group_controller)
        hashes = {k: v for k, v in p.items() if k.endswith(("sha256", "hash", "batch_id"))}
        assert all(re.fullmatch(r"[0-9a-f]{16}|[0-9a-f]{64}", v) for v in hashes.values())
        text = json.dumps({k: v for k, v in p.items() if k not in hashes}, ensure_ascii=False)
        with s.reading() as cursor:
            labels = {r[0] for r in cursor.execute(
                "SELECT DISTINCT line FROM raw_plan_cell WHERE line IS NOT NULL").fetchall()}
            sheets = {r[0] for r in cursor.execute(
                "SELECT DISTINCT sheet FROM raw_plan_cell WHERE sheet IS NOT NULL").fetchall()}
            figures = {format(r[0].normalize(), "f") for r in cursor.execute(
                "SELECT DISTINCT amount FROM raw_plan_cell WHERE amount IS NOT NULL "
                "AND abs(amount) >= 100").fetchall()}
        assert labels and sheets and figures
        assert not [x for x in labels | sheets | figures if x in text]
        assert not re.search(r"\b[A-Z]{1,3}[0-9]{1,5}\b", text)          # no cell address

    def test_the_role_view_is_named(self, sessions):
        # Changed on purpose in 3b: there is no view without a role, because there is no
        # provenance without one.
        from src import roles
        s = sessions.open()
        s.promote("clean", "P. Promoter", clock=lambda: NOON)
        assert s.provenance(roles.entity_controller("ES"))["view"] == "entity view: ES"
        assert s.provenance(roles.group_controller)["view"] == "group view"
        with pytest.raises(TypeError):
            s.provenance()


# -- Part 3b: the session items (the reviewer's Phase 3 brief, 4.7) ---------------------------------

ENTITIES = {"ES": ("Calder Foods Spain", "Southern Europe"),
            "AT": ("Calder Foods Austria", "Central Europe"),
            "DE": ("Calder Foods Germany", "Central Europe")}
REVENUE = {"metric": "revenue", "version": "Budget", "dimensions": [], "filters": [],
           "period": {"kind": "fy", "value": "FY2026"}, "comparison": "none", "sort": None,
           "limit": 100}
NO_COLUMN = "SELECT no_such_column FROM dim_version"


def others_of(entity: str) -> list:
    """Patterns for every code, name and region of the other entities that the entity does not
    share: codes as whole words in capitals, names and regions in any case."""
    own_region = ENTITIES[entity][1]
    found = []
    for code, (name, region) in ENTITIES.items():
        if code != entity:
            found += [rf"\b{code}\b", rf"(?i){re.escape(name)}"]
            if region != own_region:
                found.append(rf"(?i){re.escape(region)}")
    return found


@pytest.fixture
def promoted(sessions):
    s = sessions.open()
    acknowledge_all(s, "sample")
    s.promote("sample", "P. Promoter", clock=lambda: NOON)
    return s


class TestProvenanceTakesARole:
    def test_the_role_is_a_required_argument(self, session_module):
        import inspect
        parameter = inspect.signature(session_module.Session.provenance).parameters["role"]
        assert parameter.default is inspect.Parameter.empty

    def test_no_role_is_a_type_error_before_anything_is_read(self, sessions):
        s = sessions.open()
        with pytest.raises(TypeError):
            s.provenance()

    def test_none_is_not_a_role(self, sessions):
        from src import roles
        with pytest.raises(roles.RoleError):
            sessions.open().provenance(None)


class TestF27TheBreakCountsArePairWide:
    def test_every_provenance_says_so(self, sessions, promoted):
        from src import roles
        empty = sessions.open()
        for s in (empty, promoted):
            for role in (roles.group_controller, roles.entity_controller("ES"),
                         roles.entity_controller("AT")):
                assert s.provenance(role)["breaks_scope"] == "pair-wide"

    def test_the_counts_are_the_same_for_every_role(self, promoted):
        from src import roles
        counted = {key: promoted.provenance(roles.group_controller)[key]
                   for key in ("breaks", "acknowledged", "accepted", "dismissed", "investigated")}
        for entity in ENTITIES:
            view = promoted.provenance(roles.entity_controller(entity))
            assert {key: view[key] for key in counted} == counted

    @pytest.mark.parametrize("entity", list(ENTITIES))
    def test_an_entity_views_provenance_names_no_other_entity(self, promoted, entity):
        from src import roles
        text = json.dumps(promoted.provenance(roles.entity_controller(entity)),
                          ensure_ascii=False)
        assert [p for p in others_of(entity) if re.search(p, text)] == []
        assert f"entity view: {entity}" in text


class TestTheEnginesDetailForTheAudit:
    def _failing(self, monkeypatch, sql=NO_COLUMN):
        from src import compiler
        real = compiler.compile

        def broken(*args, **kwargs):
            q = real(*args, **kwargs)
            return compiler.CompiledQuery(**{**q.__dict__, "sql": sql})
        monkeypatch.setattr(compiler, "compile", broken)

    def test_the_engines_message_is_handed_over_and_the_outcome_is_the_fixed_one(
            self, promoted, monkeypatch):
        from src import outcomes, roles
        self._failing(monkeypatch)
        handed = []
        result = promoted.answer(REVENUE, roles.group_controller, engine_detail=handed.append)
        assert result is outcomes.ENGINE_ERROR
        assert result == outcomes.Outcome("engine_error", outcomes.SENTENCES["engine_error"])
        (message,) = handed
        assert isinstance(message, str) and "no_such_column" in message
        assert "Exception" not in message and "duckdb" not in message
        assert message not in repr(result)

    def test_without_a_receiver_nothing_changes(self, promoted, monkeypatch):
        from src import outcomes, roles
        self._failing(monkeypatch)
        assert promoted.answer(REVENUE, roles.group_controller) is outcomes.ENGINE_ERROR

    def test_a_query_the_engines_parser_reads_as_more_than_one_select(self, promoted,
                                                                       monkeypatch):
        from src import outcomes, roles
        self._failing(monkeypatch, sql="SELECT 1; SELECT 2")
        handed = []
        result = promoted.answer(REVENUE, roles.group_controller, engine_detail=handed.append)
        assert result is outcomes.ENGINE_ERROR
        assert handed == ["the engine's parser read: SELECT, SELECT"]

    def test_an_answer_hands_over_nothing(self, promoted):
        from src import answer, roles
        handed = []
        result = promoted.answer(REVENUE, roles.group_controller, engine_detail=handed.append)
        assert isinstance(result, answer.Answer) and handed == []

    def test_each_caller_gets_its_own_detail_across_threads(self, promoted):
        # One session serves many requests: the detail goes to the caller who asked, never to a
        # shared attribute. One thread runs a query that fails, the other one that does not.
        from src import compiler, roles, spec
        role = roles.group_controller
        validated = spec.validate(spec.from_json(REVENUE), promoted.registry(), promoted, role)
        good = compiler.compile(validated, promoted.registry(), role)
        bad = compiler.CompiledQuery(**{**good.__dict__, "sql": NO_COLUMN})
        handed = {"good": [], "bad": []}

        def run(name, compiled):
            for _ in range(15):
                promoted.run(compiled, validated, role, engine_detail=handed[name].append)

        threads = [threading.Thread(target=run, args=("good", good)),
                   threading.Thread(target=run, args=("bad", bad))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert handed["good"] == [] and len(handed["bad"]) == 15
        assert all("no_such_column" in m for m in handed["bad"])
        assert not hasattr(promoted, "engine_detail") and not hasattr(promoted, "last_error")


class TestF26APoolHasARootOfItsOwn:
    def test_a_pool_needs_a_root(self, template, session_module):
        import inspect
        parameter = inspect.signature(session_module.Sessions).parameters["root"]
        assert parameter.default is inspect.Parameter.empty
        with pytest.raises(TypeError):
            session_module.Sessions(template)

    def test_a_pool_never_removes_a_folder_under_another_root(self, template, tmp_path,
                                                              session_module):
        mine, other = tmp_path / "mine", tmp_path / "other"
        old_mine = mine / f"{session_module.PREFIX}old"
        old_other = other / f"{session_module.PREFIX}old"
        old_beside = tmp_path / f"{session_module.PREFIX}beside"
        for folder in (old_mine, old_other, old_beside):
            folder.mkdir(parents=True)
            (folder / "warehouse.duckdb").write_bytes(b"x")
            two_days = time.time() - 2 * 24 * 3600
            os.utime(folder, (two_days, two_days))
        pool = session_module.Sessions(template, root=mine)
        try:
            assert not old_mine.exists()
            assert old_other.exists() and old_beside.exists()
        finally:
            pool.close_all()
        other_pool = session_module.Sessions(template, root=other)
        other_pool.close_all()
        assert not old_other.exists() and old_beside.exists()
