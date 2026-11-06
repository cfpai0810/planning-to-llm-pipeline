"""The promotion gate (src/promotion.py), part 1d, brief 6.3.

What is promoted is a pair: a plan batch with the ledger batch it was
reconciled against. Its breaks are the plan batch's control breaks and the
pair's reconcile breaks, the ledger's own row breaks included; the start-up
build stores them in the warehouse. A named person acknowledges each break
(accepted, dismissed or investigated, never a default), and the pair is
promoted only once every break has an acknowledgement. Acknowledgements
belong to the pair: the break ids are the same in every file by design, so an
acknowledgement given for one pair never counts for another.

Part 1e (the reviewer's verdict of 5 November, items 4 and 5): a named person
promotes, with the rules of an acknowledger's name, whatever the break count,
and the promotion row records promoted_by beside the time; and promote()
records the promotion and rebuilds the star schema's facts from the promoted
pair in one transaction, so a failed rebuild leaves no promotion row and the
previous facts in place.

The breaks, the acknowledgements and the promotions are append-only tables
in the warehouse (tests/test_warehouse.py scans for any change to them). The
clock is injected, so the times are exact.
"""

import dataclasses
import inspect
from datetime import datetime, timedelta, timezone

import pytest

from tests.extractions import CLEAN, SAMPLE, extraction, ledger

SAMPLE_BREAKS = {"FI-facc450f1682", "TO-417d669f6dd1", "HC-b04c76a19ed4", "FI-64e07c883fff",
                 "HC-d72689858228"}
NOON = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
PROMOTER = "P. Promoter"


class Clock:
    """A clock that moves on a minute each time it is read."""

    def __init__(self, start=NOON):
        self.now = start - timedelta(minutes=1)

    def __call__(self):
        self.now += timedelta(minutes=1)
        return self.now


@pytest.fixture(scope="module")
def promotion():
    from src import promotion
    return promotion


@pytest.fixture
def con(warehouse_copy):
    from src import warehouse
    with warehouse.connect(warehouse_copy) as connection:
        yield connection


@pytest.fixture(scope="module")
def pair(promotion):
    return promotion.Pair(extraction(SAMPLE).batch.batch_id, ledger().batch.batch_id)


def acknowledge_all(promotion, con, pair, person="A. Reviewer", clock=None, but=()):
    for break_id in promotion.unacknowledged(con, pair):
        if break_id not in but:
            promotion.acknowledge(con, pair, break_id, person, "accepted", clock=clock or Clock())


class TestAtStart:
    def test_nothing_is_promoted_at_a_fresh_start(self, promotion, built_warehouse):
        from src import warehouse
        with warehouse.connect(built_warehouse.path) as con:
            assert promotion.promoted_batch(con) is None

    def test_the_sample_pairs_breaks_are_stored(self, promotion, con, pair):
        stored = promotion.breaks_of(con, pair)
        ids = {b.break_id for b in stored}
        assert len(stored) == 8 and SAMPLE_BREAKS <= ids
        assert sorted(b.control for b in stored) == ["formula_integrity"] * 2 + \
            ["hidden_content"] * 2 + ["reconcile"] * 3 + ["totals"]
        assert set(promotion.unacknowledged(con, pair)) == ids

    def test_the_breaks_are_stored_once(self, promotion, con, pair):
        assert isinstance(promotion.store_breaks(con, pair, ()), promotion.AlreadyStored)
        assert len(promotion.breaks_of(con, pair)) == 8


class TestAcknowledge:
    def test_an_acknowledgement_appends_a_row(self, promotion, con, pair):
        found = promotion.acknowledge(con, pair, "FI-facc450f1682", "  A. Reviewer ", "accepted",
                                      "Typed on purpose.", clock=lambda: NOON)
        assert (found.person, found.action, found.note, found.acknowledged_at) == (
            "A. Reviewer", "accepted", "Typed on purpose.", "2026-11-05T12:00:00Z")
        assert (found.pair, found.break_id, found.number) == (pair, "FI-facc450f1682", 1)
        assert "FI-facc450f1682" not in promotion.unacknowledged(con, pair)

    def test_the_action_has_no_default(self, promotion):
        parameters = inspect.signature(promotion.acknowledge).parameters
        assert parameters["action"].default is inspect.Parameter.empty
        assert parameters["person"].default is inspect.Parameter.empty
        assert parameters["note"].default is None
        assert promotion.ACTIONS == ("accepted", "dismissed", "investigated")

    @pytest.mark.parametrize("action", [None, "", "Accepted", "approved", "accept"])
    def test_an_action_off_the_list_is_refused(self, promotion, con, pair, action):
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.acknowledge(con, pair, "FI-facc450f1682", "A. Reviewer", action,
                                  clock=lambda: NOON)
        assert refused.value.kind == "action_unknown"
        assert str(refused.value) == "The action must be accepted, dismissed or investigated."

    @pytest.mark.parametrize("person", [None, "", "   ", "\t"])
    def test_a_person_is_required(self, promotion, con, pair, person):
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.acknowledge(con, pair, "FI-facc450f1682", person, "accepted",
                                  clock=lambda: NOON)
        assert refused.value.kind == "person_missing"
        assert str(refused.value) == "A person is required: enter a name."

    def test_a_person_is_at_most_60_characters_once_trimmed(self, promotion, con, pair):
        ok = promotion.acknowledge(con, pair, "FI-facc450f1682", "  " + "x" * 60 + "  ",
                                   "dismissed", clock=lambda: NOON)
        assert ok.person == "x" * 60
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.acknowledge(con, pair, "FI-facc450f1682", "x" * 61, "dismissed",
                                  clock=lambda: NOON)
        assert refused.value.kind == "person_too_long"

    def test_the_note_is_optional_and_at_most_500_characters(self, promotion, con, pair):
        assert promotion.acknowledge(con, pair, "FI-facc450f1682", "A. Reviewer",
                                     "investigated", clock=lambda: NOON).note is None
        assert promotion.acknowledge(con, pair, "FI-facc450f1682", "A. Reviewer",
                                     "investigated", "n" * 500, clock=lambda: NOON).note \
            == "n" * 500
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.acknowledge(con, pair, "FI-facc450f1682", "A. Reviewer", "investigated",
                                  "n" * 501, clock=lambda: NOON)
        assert refused.value.kind == "note_too_long"

    def test_a_repeat_acknowledgement_appends(self, promotion, con, pair):
        clock = Clock()
        promotion.acknowledge(con, pair, "TO-417d669f6dd1", "A. Reviewer", "investigated",
                              clock=clock)
        promotion.acknowledge(con, pair, "TO-417d669f6dd1", "B. Reviewer", "accepted",
                              "Range fixed in the next version.", clock=clock)
        rows = promotion.acknowledgements(con, pair)
        assert [(a.break_id, a.person, a.action, a.acknowledged_at) for a in rows] == [
            ("TO-417d669f6dd1", "A. Reviewer", "investigated", "2026-11-05T12:00:00Z"),
            ("TO-417d669f6dd1", "B. Reviewer", "accepted", "2026-11-05T12:01:00Z")]
        assert [a.number for a in rows] == [1, 2]

    def test_a_break_not_in_the_pair_is_refused(self, promotion, con, pair):
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.acknowledge(con, pair, "FI-000000000000", "A. Reviewer", "accepted",
                                  clock=lambda: NOON)
        assert refused.value.kind == "break_unknown"

    def test_a_pair_never_reconciled_is_refused(self, promotion, con, pair):
        other = promotion.Pair(pair.plan_batch_id, "0000000000000000")
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.acknowledge(con, other, "FI-facc450f1682", "A. Reviewer", "accepted",
                                  clock=lambda: NOON)
        assert refused.value.kind == "not_reviewed"
        with pytest.raises(promotion.PromotionError):
            promotion.promote(con, other, PROMOTER, clock=lambda: NOON)


class TestPromote:
    def test_promote_is_refused_while_a_break_has_no_acknowledgement(self, promotion, con,
                                                                     pair):
        acknowledge_all(promotion, con, pair, but=("HC-d72689858228",))
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.promote(con, pair, PROMOTER, clock=lambda: NOON)
        assert refused.value.kind == "unacknowledged"
        assert str(refused.value) == ("1 break has no acknowledgement, so the batch is not "
                                      "promoted.")
        assert refused.value.break_ids == ("HC-d72689858228",)
        assert promotion.promoted_batch(con) is None

    def test_promote_records_the_person_and_time_of_the_last_acknowledgement(self, promotion,
                                                                             con, pair):
        clock = Clock()
        acknowledge_all(promotion, con, pair, person="A. Reviewer", clock=clock)
        promotion.acknowledge(con, pair, "HC-d72689858228", "C. Reviewer", "dismissed",
                              clock=clock)
        last = clock.now
        done = promotion.promote(con, pair, "  P. Promoter ",
                                 clock=lambda: NOON + timedelta(hours=1))
        assert (done.pair, done.promoted_by, done.promoted_at, done.last_acknowledged_by,
                done.last_acknowledged_at) == (
            pair, PROMOTER, "2026-11-05T13:00:00Z", "C. Reviewer",
            last.strftime("%Y-%m-%dT%H:%M:%SZ"))
        assert promotion.promoted_batch(con) == done

    def test_the_previous_promotion_stays_current_until_the_next(self, promotion, con, pair):
        from src import controls, reconcile, warehouse
        acknowledge_all(promotion, con, pair)
        first = promotion.promote(con, pair, PROMOTER, clock=lambda: NOON)
        # A second plan batch, the clean workbook, against the same ledger.
        clean = extraction(CLEAN)
        warehouse.land(con, clean)
        second = promotion.Pair(clean.batch.batch_id, pair.ledger_batch_id)
        found = controls.run_controls(clean).breaks() + \
            reconcile.reconcile(con, clean, ledger()).breaks
        assert found == ()
        promotion.store_breaks(con, second, found)
        assert promotion.promoted_batch(con) == first
        later = promotion.promote(con, second, "Q. Promoter",
                                  clock=lambda: NOON + timedelta(days=1))
        assert promotion.promoted_batch(con) == later
        assert (later.pair, later.promoted_by, later.last_acknowledged_by,
                later.last_acknowledged_at) == (second, "Q. Promoter", None, None)
        assert [p.pair for p in promotion.promotions(con)] == [pair, second]

    def test_an_acknowledgement_counts_for_its_own_pair_only(self, promotion, con, pair):
        from src import reconcile, warehouse
        # The same ledger landed again as another batch: the same break ids.
        lines = ledger()
        other_id = "ledger00000000b2"
        other = dataclasses.replace(
            lines, batch=dataclasses.replace(lines.batch, batch_id=other_id),
            lines=tuple(dataclasses.replace(l, batch_id=other_id) for l in lines.lines),
            control_totals=tuple(dataclasses.replace(t, batch_id=other_id)
                                 for t in lines.control_totals))
        warehouse.land(con, other)
        second = promotion.Pair(pair.plan_batch_id, other_id)
        from src.controls import run_controls
        promotion.store_breaks(con, second, run_controls(extraction(SAMPLE)).breaks()
                               + reconcile.reconcile(con, extraction(SAMPLE), other).breaks)
        assert {b.break_id for b in promotion.breaks_of(con, second)} \
            == {b.break_id for b in promotion.breaks_of(con, pair)}
        acknowledge_all(promotion, con, pair)
        assert promotion.unacknowledged(con, pair) == ()
        assert len(promotion.unacknowledged(con, second)) == 8
        with pytest.raises(promotion.PromotionError):
            promotion.promote(con, second, PROMOTER, clock=lambda: NOON)


class TestTheTables:
    def test_the_gate_tables(self, promotion, con):
        assert promotion.GATE_TABLES == ("gate_review", "gate_break", "gate_acknowledgement",
                                         "gate_promotion")
        for table in promotion.GATE_TABLES:
            con.execute(f"SELECT count(*) FROM {table}").fetchone()

    def test_the_times_come_from_the_clock(self, promotion):
        source = inspect.getsource(promotion)
        assert "datetime.now(" in source.split("def utc_now", 1)[1].split("\ndef ", 1)[0]
        for name in ("acknowledge", "promote"):
            assert "clock" in inspect.signature(getattr(promotion, name)).parameters


# == Part 1e ======================================================================================

def clean_pair(promotion, con, pair):
    """The clean workbook landed beside the sample, against the same ledger: no break."""
    from src import controls, reconcile, warehouse
    clean = extraction(CLEAN)
    warehouse.land(con, clean)
    second = promotion.Pair(clean.batch.batch_id, pair.ledger_batch_id)
    found = controls.run_controls(clean).breaks() + reconcile.reconcile(con, clean, ledger()).breaks
    assert found == ()
    promotion.store_breaks(con, second, found)
    return second


STAR = ("dim_version", "dim_time", "dim_entity", "dim_account", "fact_plan", "fact_actual_erp")


def facts(con) -> dict:
    """The row counts and sums of the two fact tables."""
    return {"fact_plan": con.execute("SELECT count(*), sum(amount) FROM fact_plan").fetchone(),
            "fact_actual_erp": con.execute("SELECT count(*), sum(amount), sum(ledger_amount) "
                                           "FROM fact_actual_erp").fetchone()}


def star(con) -> dict:
    """Every row of every star table, in a fixed order."""
    return {t: con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in STAR}


class TestANamedPromoter:
    """Item 4: a named person promotes, with the rules of an acknowledger's name."""

    def test_the_person_is_required_and_has_no_default(self, promotion):
        parameters = inspect.signature(promotion.promote).parameters
        assert list(parameters) == ["con", "pair", "person", "clock"]
        assert parameters["person"].default is inspect.Parameter.empty
        assert parameters["person"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert parameters["clock"].kind is inspect.Parameter.KEYWORD_ONLY

    @pytest.mark.parametrize("person", [None, "", "   ", "\t"])
    def test_a_clean_pair_is_refused_without_a_name(self, promotion, con, pair, person):
        second = clean_pair(promotion, con, pair)
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.promote(con, second, person, clock=lambda: NOON)
        assert refused.value.kind == "person_missing"
        assert str(refused.value) == "A person is required: enter a name."
        assert promotion.promoted_batch(con) is None

    def test_a_clean_pair_promotes_with_a_name(self, promotion, con, pair):
        second = clean_pair(promotion, con, pair)
        done = promotion.promote(con, second, "  P. Promoter ", clock=lambda: NOON)
        assert (done.number, done.pair, done.promoted_by, done.promoted_at) == (
            1, second, PROMOTER, "2026-11-05T12:00:00Z")
        assert promotion.promoted_batch(con) == done

    def test_a_name_longer_than_60_characters_is_refused(self, promotion, con, pair):
        second = clean_pair(promotion, con, pair)
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.promote(con, second, "x" * 61, clock=lambda: NOON)
        assert refused.value.kind == "person_too_long"
        assert str(refused.value) == "The name is longer than 60 characters."
        assert promotion.promotions(con) == ()
        done = promotion.promote(con, second, "  " + "x" * 60 + " ", clock=lambda: NOON)
        assert done.promoted_by == "x" * 60

    def test_the_name_is_checked_before_the_breaks(self, promotion, con, pair):
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.promote(con, pair, " ", clock=lambda: NOON)
        assert refused.value.kind == "person_missing"

    def test_a_pair_with_breaks_refuses_a_named_promoter_until_every_break_is_acknowledged(
            self, promotion, con, pair):
        acknowledge_all(promotion, con, pair, but=("TO-417d669f6dd1",))
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.promote(con, pair, PROMOTER, clock=lambda: NOON)
        assert (refused.value.kind, refused.value.break_ids) == (
            "unacknowledged", ("TO-417d669f6dd1",))
        promotion.acknowledge(con, pair, "TO-417d669f6dd1", "A. Reviewer", "accepted",
                              clock=lambda: NOON)
        assert promotion.promote(con, pair, PROMOTER, clock=lambda: NOON).promoted_by == PROMOTER

    def test_the_row_records_promoted_by_and_the_time(self, promotion, con, pair):
        second = clean_pair(promotion, con, pair)
        promotion.promote(con, second, PROMOTER, clock=lambda: NOON)
        assert con.execute("SELECT * FROM gate_promotion").fetchall() == [
            (1, second.plan_batch_id, second.ledger_batch_id, PROMOTER, "2026-11-05T12:00:00Z",
             None, None)]
        columns = con.execute("SELECT column_name, is_nullable FROM information_schema.columns "
                              "WHERE table_name = 'gate_promotion' ORDER BY ordinal_position"
                              ).fetchall()
        assert columns == [("number", "NO"), ("plan_batch_id", "NO"), ("ledger_batch_id", "NO"),
                           ("promoted_by", "NO"), ("promoted_at", "NO"),
                           ("last_acknowledged_by", "YES"), ("last_acknowledged_at", "YES")]
        assert [f.name for f in dataclasses.fields(promotion.Promotion)] == [
            "number", "pair", "promoted_by", "promoted_at", "last_acknowledged_by",
            "last_acknowledged_at"]


class TestPromoteRebuilds:
    """Item 5: promote records the promotion and rebuilds the facts, in one transaction."""

    def test_after_promote_the_facts_equal_a_build_from_that_pair(self, promotion,
                                                                  warehouse_copy, pair,
                                                                  tmp_path):
        import shutil

        from src import model, warehouse
        with warehouse.connect(warehouse_copy) as con:
            assert facts(con)["fact_plan"][0] == 0
            acknowledge_all(promotion, con, pair)
            promotion.promote(con, pair, PROMOTER, clock=lambda: NOON)
            promoted = facts(con)
        copy = tmp_path / "rebuilt.duckdb"
        shutil.copyfile(warehouse_copy, copy)
        with warehouse.connect(copy) as con:
            model.build_model(con)
            rebuilt = facts(con)
        assert promoted == rebuilt
        assert (promoted["fact_plan"][0], promoted["fact_actual_erp"][0]) == (1159, 162)

    def test_a_failed_rebuild_leaves_no_promotion_and_the_previous_facts(
            self, promotion, con, pair, monkeypatch):
        import duckdb

        from src import model
        acknowledge_all(promotion, con, pair)
        first = promotion.promote(con, pair, PROMOTER, clock=lambda: NOON)
        before = star(con)
        second = clean_pair(promotion, con, pair)
        real, seen = model.statements, []

        def failing(path):
            # One statement made to fail: fact_actual_erp's, after the
            # dimensions and fact_plan have been made anew in the transaction.
            seen.append(path.name)
            if path.name == "06_fact_actual_erp.sql":
                return ["CREATE OR REPLACE TABLE fact_actual_erp AS SELECT no_such_column "
                        "FROM raw_ledger_line"]
            return real(path)
        monkeypatch.setattr(model, "statements", failing)
        with pytest.raises(duckdb.Error):
            promotion.promote(con, second, "Q. Promoter", clock=lambda: NOON)
        assert seen == ["01_dim_version.sql", "02_dim_time.sql", "03_dim_entity.sql",
                        "04_dim_account.sql", "05_fact_plan.sql", "06_fact_actual_erp.sql"]
        assert promotion.promotions(con) == (first,)
        assert promotion.promoted_batch(con) == first
        assert star(con) == before
        # The connection is whole again, and the number was not taken.
        monkeypatch.undo()
        again = promotion.promote(con, second, "Q. Promoter", clock=lambda: NOON)
        assert again.number == 2 and star(con) != before

    def test_a_second_pair_replaces_the_facts(self, promotion, con, pair):
        acknowledge_all(promotion, con, pair)
        promotion.promote(con, pair, PROMOTER, clock=lambda: NOON)
        assert con.execute("SELECT DISTINCT batch_id FROM fact_plan").fetchall() == [
            (pair.plan_batch_id,)]
        second = clean_pair(promotion, con, pair)
        promotion.promote(con, second, "Q. Promoter", clock=lambda: NOON)
        assert con.execute("SELECT DISTINCT batch_id FROM fact_plan").fetchall() == [
            (second.plan_batch_id,)]
        assert con.execute("SELECT count(*) FROM fact_plan WHERE batch_id = ?",
                           [pair.plan_batch_id]).fetchone() == (0,)
        months = sum(1 for c in extraction(CLEAN).cells
                     if c.column_kind == "month" and c.amount is not None)
        assert facts(con)["fact_plan"][0] == months == 1170
        assert con.execute("SELECT DISTINCT batch_id FROM fact_actual_erp").fetchall() == [
            (pair.ledger_batch_id,)]
