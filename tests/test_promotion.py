"""The promotion gate (src/promotion.py), part 1d, brief 6.3.

What is promoted is a pair: a plan batch with the ledger batch it was
reconciled against. Its breaks are the plan batch's control breaks and the
pair's reconcile breaks, the ledger's own row breaks included; the start-up
build stores them in the warehouse. A named person acknowledges each break
(accepted, dismissed or investigated, never a default), and the pair is
promoted only once every break has an acknowledgement. Acknowledgements
belong to the pair: the break ids are the same in every file by design, so an
acknowledgement given for one pair never counts for another.

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
            promotion.promote(con, other, clock=lambda: NOON)


class TestPromote:
    def test_promote_is_refused_while_a_break_has_no_acknowledgement(self, promotion, con,
                                                                     pair):
        acknowledge_all(promotion, con, pair, but=("HC-d72689858228",))
        with pytest.raises(promotion.PromotionError) as refused:
            promotion.promote(con, pair, clock=lambda: NOON)
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
        done = promotion.promote(con, pair, clock=lambda: NOON + timedelta(hours=1))
        assert (done.pair, done.person, done.acknowledged_at, done.promoted_at) == (
            pair, "C. Reviewer", last.strftime("%Y-%m-%dT%H:%M:%SZ"), "2026-11-05T13:00:00Z")
        assert promotion.promoted_batch(con) == done

    def test_the_previous_promotion_stays_current_until_the_next(self, promotion, con, pair):
        from src import controls, reconcile, warehouse
        acknowledge_all(promotion, con, pair)
        first = promotion.promote(con, pair, clock=lambda: NOON)
        # A second plan batch, the clean workbook, against the same ledger.
        clean = extraction(CLEAN)
        warehouse.land(con, clean)
        second = promotion.Pair(clean.batch.batch_id, pair.ledger_batch_id)
        found = controls.run_controls(clean).breaks() + \
            reconcile.reconcile(con, clean, ledger()).breaks
        assert found == ()
        promotion.store_breaks(con, second, found)
        assert promotion.promoted_batch(con) == first
        later = promotion.promote(con, second, clock=lambda: NOON + timedelta(days=1))
        assert promotion.promoted_batch(con) == later
        assert (later.pair, later.person, later.acknowledged_at) == (second, None, None)
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
            promotion.promote(con, second, clock=lambda: NOON)


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
