# =============================================================================
# src/promotion.py: the promotion gate, acknowledgements and promotion
# =============================================================================
"""
The promotion gate (brief 6.3). What is promoted is a pair: a plan batch with
the ledger batch it was reconciled against. The facts of the star schema are
built from the promoted pair only (src/model.py).

- The pair's breaks are the plan batch's control breaks and the pair's
  reconcile breaks, the ledger's own row breaks included. The start-up build
  stores them once (store_breaks); storing the same pair again adds nothing.
- acknowledge() appends one row: the pair, the break, the person, the action
  and an optional note, with the time from an injected clock. The person is
  required, trimmed, and at most 60 characters; the action is accepted,
  dismissed or investigated, with no default; the note is at most 500
  characters. A repeat acknowledgement appends; nothing is ever overwritten.
- Acknowledgements belong to the pair. A break's id is the same in every file
  by design, so an acknowledgement given for one pair never counts for the
  same break of another.
- promote() is refused while any break of the pair has no acknowledgement. It
  appends a row with the person and time of the pair's last acknowledgement
  and the time of the promotion. promoted_batch() returns the latest
  promotion, or None: the previous promotion stays current until the next.

The breaks, the acknowledgements and the promotions are append-only tables
in the warehouse (gate_review, gate_break, gate_acknowledgement,
gate_promotion): this module only ever inserts into them, and
tests/test_warehouse.py scans for any statement that would change them. They
are lost when the app restarts, because the warehouse is rebuilt at start;
the audit trail of Phase 3 is the lasting record. At a fresh start nothing is
promoted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from src.batch import Pair
from src.controls import Break
from src.warehouse import insert_rows

__all__ = ["ACTIONS", "GATE_TABLES", "Acknowledgement", "AlreadyStored", "Pair", "Promotion",
           "PromotionError", "Stored", "acknowledge", "acknowledgements", "breaks_of",
           "create_indexes", "create_tables", "promote", "promoted_batch", "promotions",
           "store_breaks", "unacknowledged", "utc_now"]

ACTIONS = ("accepted", "dismissed", "investigated")
MAX_PERSON, MAX_NOTE = 60, 500
STAMP = "%Y-%m-%dT%H:%M:%SZ"

PAIR = (("plan_batch_id", "VARCHAR", "NOT NULL"), ("ledger_batch_id", "VARCHAR", "NOT NULL"))
COLUMNS = {
    "gate_review": PAIR + (("breaks", "INTEGER", "NOT NULL"),),
    "gate_break": PAIR + (
        ("number", "INTEGER", "NOT NULL"), ("break_id", "VARCHAR", "NOT NULL"),
        ("batch_id", "VARCHAR", "NOT NULL"), ("control", "VARCHAR", "NOT NULL"),
        ("kind", "VARCHAR", "NOT NULL"), ("reasons", "VARCHAR", "NOT NULL"),
        ("severity", "VARCHAR", "NOT NULL"), ("sheet", "VARCHAR", ""), ("block", "VARCHAR", ""),
        ("entity", "VARCHAR", ""), ("account", "VARCHAR", ""), ("period", "VARCHAR", ""),
        ("address", "VARCHAR", ""), ("message", "VARCHAR", "NOT NULL"),
        ("evidence", "VARCHAR", "NOT NULL")),
    "gate_acknowledgement": (("number", "INTEGER", "NOT NULL"),) + PAIR + (
        ("break_id", "VARCHAR", "NOT NULL"), ("person", "VARCHAR", "NOT NULL"),
        ("action", "VARCHAR", "NOT NULL"), ("note", "VARCHAR", ""),
        ("acknowledged_at", "VARCHAR", "NOT NULL")),
    "gate_promotion": (("number", "INTEGER", "NOT NULL"),) + PAIR + (
        ("person", "VARCHAR", ""), ("acknowledged_at", "VARCHAR", ""),
        ("promoted_at", "VARCHAR", "NOT NULL")),
}
GATE_TABLES = tuple(COLUMNS)
TABLES = tuple(
    f"CREATE TABLE IF NOT EXISTS {table} (\n"
    + ",\n".join(f"    {name} {kind}{' ' + rule if rule else ''}" for name, kind, rule in columns)
    + "\n)" for table, columns in COLUMNS.items())
# Created by the start-up build once the pair's breaks are stored.
INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS gate_review_pair ON gate_review "
    "(plan_batch_id, ledger_batch_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS gate_break_key ON gate_break "
    "(plan_batch_id, ledger_batch_id, break_id)",
)

# The fixed wording of each refusal.
REFUSALS = {
    "person_missing": "A person is required: enter a name.",
    "person_too_long": f"The name is longer than {MAX_PERSON} characters.",
    "action_unknown": "The action must be accepted, dismissed or investigated.",
    "note_too_long": f"The note is longer than {MAX_NOTE} characters.",
    "not_reviewed": "This pair of batches has not been reconciled, so it has no breaks to "
                    "acknowledge or promote.",
    "break_unknown": "That break is not one of this pair's breaks.",
}


class PromotionError(Exception):
    """A refused acknowledgement or promotion, told in one fixed sentence with a kind."""

    def __init__(self, kind: str, message: str, break_ids: tuple = ()):
        self.kind = kind
        self.break_ids = tuple(break_ids)
        super().__init__(message)


@dataclass(frozen=True)
class Stored:
    pair: Pair
    breaks: int


@dataclass(frozen=True)
class AlreadyStored:
    pair: Pair


@dataclass(frozen=True)
class Acknowledgement:
    number: int
    pair: Pair
    break_id: str
    person: str
    action: str
    note: Optional[str]
    acknowledged_at: str     # UTC, as text of the form 2026-11-05T12:00:00Z


@dataclass(frozen=True)
class Promotion:
    number: int
    pair: Pair
    person: Optional[str]           # of the last acknowledgement; None when the pair had no break
    acknowledged_at: Optional[str]  # its time
    promoted_at: str


def utc_now() -> datetime:
    """The clock acknowledge() and promote() use unless a test gives them another."""
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("the clock must give a time with its zone")
    return moment.astimezone(timezone.utc).strftime(STAMP)


def create_tables(con) -> None:
    for statement in TABLES:
        con.execute(statement)


def create_indexes(con) -> None:
    for statement in INDEXES:
        con.execute(statement)


# == The pair's breaks ====================================================================

def _reviewed(con, pair: Pair) -> bool:
    return con.execute("SELECT count(*) FROM gate_review WHERE plan_batch_id = ? AND "
                       "ledger_batch_id = ?", [pair.plan_batch_id, pair.ledger_batch_id]
                       ).fetchone()[0] > 0


def store_breaks(con, pair: Pair, breaks) -> Stored | AlreadyStored:
    """Stores the pair's breaks once, in one transaction; a pair already stored adds nothing."""
    if _reviewed(con, pair):
        return AlreadyStored(pair)
    rows = [(pair.plan_batch_id, pair.ledger_batch_id, number, b.break_id, b.batch_id,
             b.control, b.kind, json.dumps(list(b.reasons)), b.severity, b.sheet, b.block,
             b.entity, b.account, b.period, b.address, b.message,
             json.dumps([list(pair_) for pair_ in b.evidence], ensure_ascii=False))
            for number, b in enumerate(breaks, start=1)]
    con.begin()
    try:
        insert_rows(con, "gate_review", COLUMNS["gate_review"],
                    [(pair.plan_batch_id, pair.ledger_batch_id, len(rows))])
        insert_rows(con, "gate_break", COLUMNS["gate_break"], rows)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return Stored(pair, len(rows))


def breaks_of(con, pair: Pair) -> tuple:
    """The pair's stored breaks, in the order they were stored."""
    rows = con.execute(
        "SELECT break_id, batch_id, control, kind, reasons, severity, sheet, block, entity, "
        "account, period, address, message, evidence FROM gate_break WHERE plan_batch_id = ? "
        "AND ledger_batch_id = ? ORDER BY number",
        [pair.plan_batch_id, pair.ledger_batch_id]).fetchall()
    return tuple(Break(*row[:4], tuple(json.loads(row[4])), *row[5:13],
                       tuple(tuple(e) for e in json.loads(row[13]))) for row in rows)


def unacknowledged(con, pair: Pair) -> tuple:
    """The ids of the pair's breaks that have no acknowledgement, in stored order."""
    return tuple(r[0] for r in con.execute(
        "SELECT b.break_id FROM gate_break b WHERE b.plan_batch_id = ? AND b.ledger_batch_id = ? "
        "AND NOT EXISTS (SELECT 1 FROM gate_acknowledgement a WHERE a.plan_batch_id = "
        "b.plan_batch_id AND a.ledger_batch_id = b.ledger_batch_id AND a.break_id = b.break_id) "
        "ORDER BY b.number", [pair.plan_batch_id, pair.ledger_batch_id]).fetchall())


# == Acknowledge ===========================================================================

def _refuse(kind: str) -> PromotionError:
    return PromotionError(kind, REFUSALS[kind])


def _next_number(con, table: str) -> int:
    return con.execute(f"SELECT coalesce(max(number), 0) + 1 FROM {table}").fetchone()[0]


def acknowledge(con, pair: Pair, break_id: str, person, action, note: Optional[str] = None, *,
                clock: Callable[[], datetime] = utc_now) -> Acknowledgement:
    """Appends one acknowledgement of one break of the pair; refused with a fixed sentence."""
    if not isinstance(person, str) or not person.strip():
        raise _refuse("person_missing")
    person = person.strip()
    if len(person) > MAX_PERSON:
        raise _refuse("person_too_long")
    if action not in ACTIONS:
        raise _refuse("action_unknown")
    if note is not None:
        note = note.strip() or None
        if note is not None and len(note) > MAX_NOTE:
            raise _refuse("note_too_long")
    if not _reviewed(con, pair):
        raise _refuse("not_reviewed")
    if not con.execute("SELECT count(*) FROM gate_break WHERE plan_batch_id = ? AND "
                       "ledger_batch_id = ? AND break_id = ?",
                       [pair.plan_batch_id, pair.ledger_batch_id, break_id]).fetchone()[0]:
        raise _refuse("break_unknown")
    at = _stamp(clock())
    con.begin()
    try:
        number = _next_number(con, "gate_acknowledgement")
        insert_rows(con, "gate_acknowledgement", COLUMNS["gate_acknowledgement"],
                    [(number, pair.plan_batch_id, pair.ledger_batch_id, break_id, person,
                      action, note, at)])
        con.commit()
    except Exception:
        con.rollback()
        raise
    return Acknowledgement(number, pair, break_id, person, action, note, at)


def acknowledgements(con, pair: Pair) -> tuple:
    """The pair's acknowledgements, in the order they were given."""
    rows = con.execute("SELECT number, break_id, person, action, note, acknowledged_at FROM "
                       "gate_acknowledgement WHERE plan_batch_id = ? AND ledger_batch_id = ? "
                       "ORDER BY number", [pair.plan_batch_id, pair.ledger_batch_id]).fetchall()
    return tuple(Acknowledgement(n, pair, b, p, a, note, at) for n, b, p, a, note, at in rows)


# == Promote =================================================================================

def promote(con, pair: Pair, *, clock: Callable[[], datetime] = utc_now) -> Promotion:
    """Promotes the pair once every break has an acknowledgement; refused otherwise."""
    if not _reviewed(con, pair):
        raise _refuse("not_reviewed")
    waiting = unacknowledged(con, pair)
    if waiting:
        count = len(waiting)
        raise PromotionError(
            "unacknowledged", f"{count} break{' has' if count == 1 else 's have'} no "
                              "acknowledgement, so the batch is not promoted.", waiting)
    given = acknowledgements(con, pair)
    last = given[-1] if given else None
    at = _stamp(clock())
    con.begin()
    try:
        number = _next_number(con, "gate_promotion")
        insert_rows(con, "gate_promotion", COLUMNS["gate_promotion"],
                    [(number, pair.plan_batch_id, pair.ledger_batch_id,
                      last.person if last else None, last.acknowledged_at if last else None,
                      at)])
        con.commit()
    except Exception:
        con.rollback()
        raise
    return Promotion(number, pair, last.person if last else None,
                     last.acknowledged_at if last else None, at)


def promotions(con) -> tuple:
    """Every promotion, in order."""
    rows = con.execute("SELECT number, plan_batch_id, ledger_batch_id, person, acknowledged_at, "
                       "promoted_at FROM gate_promotion ORDER BY number").fetchall()
    return tuple(Promotion(n, Pair(plan, ledger), person, acknowledged, promoted)
                 for n, plan, ledger, person, acknowledged, promoted in rows)


def promoted_batch(con) -> Optional[Promotion]:
    """The latest promotion, or None: the previous one stays current until the next."""
    found = promotions(con)
    return found[-1] if found else None
