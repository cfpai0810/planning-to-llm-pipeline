# =============================================================================
# src/reconcile.py: reconcile, source against raw and the workbook against the ledger
# =============================================================================
"""
Two comparisons (brief 6.2), each giving the controls' typed breaks:

- Source against raw. For a batch, by version and period: the row count and
  the amount sum the reader computed in Python before landing, against the
  rows that landed, read back from the warehouse, and against the control
  totals landed with them. They must be equal; a difference is a break.
- The workbook's Actual block against the ledger, for a pair (a plan batch
  and the ledger batch it is reconciled against), by entity, account and
  period, read back from the warehouse. Each figure is rounded to the whole
  euro (half away from zero) and the ledger's is turned into presentation
  sign through its account's ledger sign on Lists (a Credit account's amount
  times -1, a Debit account's as it is), so revenue is positive. Only
  accounts with a ledger sign are compared: statistical lines and subtotals
  are not in a ledger. Members are judged against the Lists of the pair's
  plan batch, landed beside it.

The rules for the second comparison:

- an amount difference (a late journal is one) is one break per entity,
  account and period;
- two differences in one entity and period, in two accounts, that are equal
  and opposite in ledger sign, are one "possible reclassification" break: a
  reclassification moves one amount from one account to another, a debit and
  an equal credit, so in ledger sign the two differences cancel. Pairs are
  found in account order, each account in at most one pair;
- an entity-month that one side holds figures for and the other holds none
  is one break, not one per account;
- a key the ledger holds more than once is one break, and is not compared;
- a ledger entity or account code not on Lists is one break per code, and its
  lines are not compared;
- a ledger line with a row error is one break per line (its figure cannot be
  read); neither the line nor its key is compared;
- a workbook cell that holds something but no figure (no cached value, an
  error value, text), or a key the workbook holds twice, is not compared: the
  calculation-state and structure controls report those;
- an entity whose Actual block is not built or not read (its plan tab or its
  Actual block not found, a block that cannot be read, a block that shares a
  cell with another) is left out whole, and an account a built Actual block
  lacks, or holds on two lines, is left out for that entity (part 2a2: the
  reviewer's ruling of 8 November, and F19). The structure break that explains
  it says so (controls.actual_left_out); a built block with an empty month is
  compared as before.

Facts about the file (its times, the application that saved it, its creator
and its links to other workbooks) are never compared with anything here.
Severity follows the controls' rules (docs/controls.md): high when a figure as
read is wrong, or cannot be read or placed; medium when a figure may be wrong
or a member is left out.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from src.batch import Pair
from src.controls import (HIGH, MEDIUM, RUN, Break, ControlResult, _figure, _member, _plain,
                          actual_left_out)
from src.extract import Account, Entity, Lists, PlanCell
from src.ledger import LedgerExtraction, LedgerLine

__all__ = ["ID_PREFIX", "KINDS", "RECONCILE", "SEVERITY", "Break", "Pair", "compare",
           "reconcile", "source_against_raw"]

RECONCILE = "reconcile"
ID_PREFIX = "RC"
ACTUAL = "Actual"
MONTH = "month"
# Ledger sign to presentation sign: a Credit account's amount is turned round.
TO_PRESENTATION = {"Credit": -1, "Debit": 1}
WHOLE = Decimal(1)

# The kinds, in the order the result lists them, and the severity of each.
KINDS = ("source_against_raw", "missing_entity_month", "possible_reclassification",
         "amount_difference", "duplicate_key", "unknown_entity", "unknown_account",
         "row_error")
SEVERITY = {
    "source_against_raw": HIGH,          # what landed is not what was read
    "missing_entity_month": HIGH,        # a whole entity-month of figures is absent on one side
    "possible_reclassification": MEDIUM,  # the entity-month ties; two figures may sit wrongly
    "amount_difference": HIGH,           # a figure as read disagrees with the other source
    "duplicate_key": HIGH,               # a ledger figure cannot be placed
    "unknown_entity": MEDIUM,            # a member is left out
    "unknown_account": MEDIUM,
    "row_error": HIGH,                   # a ledger figure cannot be read
}

MESSAGES = {
    "source_against_raw": "For {what} in {period}, the source holds {read_rows} rows summing to "
                          "{read_sum} and the warehouse {raw_rows} rows summing to {raw_sum}; "
                          "the differences are {row_difference} rows and {sum_difference}.",
    "missing_from_workbook": "The workbook's Actual block holds no figure for {entity} in "
                             "{period}, and the ledger holds figures for {count} accounts, "
                             "netting to {net} in ledger sign: the entity-month is missing from "
                             "the workbook, one break for all its accounts.",
    "missing_from_ledger": "The ledger holds no line for {entity} in {period}, and the "
                           "workbook's Actual block holds figures for {count} accounts, netting "
                           "to {net} in ledger sign: the entity-month is missing from the "
                           "ledger, one break for all its accounts.",
    "possible_reclassification": "For {entity} in {period}, {line_a} is {workbook_a} in the "
                                 "workbook and {ledger_a} in the ledger (difference "
                                 "{difference_a}), and {line_b} is {workbook_b} in the workbook "
                                 "and {ledger_b} in the ledger (difference {difference_b}): the "
                                 "two differences are equal and opposite in ledger sign, a "
                                 "possible reclassification.",
    "amount_difference": "The Actual figure of {line} for {entity} in {period} is {workbook} in "
                         "the workbook and {ledger} in the ledger, in presentation sign and "
                         "whole euros; the difference is {difference}.",
    "duplicate_key": "The ledger holds {count} lines for {entity}, account {account}, in "
                     "{period} (lines {lines}): the key is duplicated, so it is not compared.",
    "unknown_entity": "The ledger's entity code {code} is not an entity on Lists (lines "
                      "{lines}): its lines are not compared and stay out of the star schema.",
    "unknown_account": "The ledger's account code {code} is not an account with a ledger sign "
                       "on Lists (lines {lines}): its lines are not compared and stay out of the "
                       "star schema.",
    "row_error": "Ledger line {line} has the row error {error}: its amount is not read, so the "
                 "line is not compared and stays out of the star schema.",
}


# == Shared helpers ======================================================================

def _id(anchors: tuple) -> str:
    """The controls' id scheme: the prefix, then 12 hex digits of SHA-256 of the anchors."""
    text = json.dumps([RECONCILE, *anchors], ensure_ascii=False, separators=(",", ":"),
                      default=str)
    return f"{ID_PREFIX}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


def _break(kind: str, batch_id: str, anchors: tuple, message: str, evidence, sheet=None,
           block=None, entity=None, account=None, period=None, address=None) -> Break:
    return Break(break_id=_id(anchors), batch_id=batch_id, control=RECONCILE, kind=kind,
                 reasons=(kind,), severity=SEVERITY[kind], sheet=sheet, block=block,
                 entity=entity, account=account, period=period, address=address,
                 message=message, evidence=tuple(evidence))


def _whole(amount: Decimal) -> Decimal:
    return amount.quantize(WHOLE, rounding=ROUND_HALF_UP)


def _shown(amount: Decimal) -> str:
    """A figure in a message: with thousands separators and no trailing zeros."""
    return _figure(amount.normalize())


def _lines(lines) -> str:
    return ", ".join(str(line.line_number) for line in lines)


def _range(cells) -> Optional[str]:
    """The cells' addresses as a range from the first to the last, or one cell."""
    addresses = sorted((c.address for c in cells if c.address),
                       key=lambda a: (len(a.rstrip("0123456789")), a.rstrip("0123456789"),
                                      int(a[len(a.rstrip("0123456789")):])))
    if not addresses:
        return None
    return addresses[0] if len(addresses) == 1 else f"{addresses[0]}:{addresses[-1]}"


# == Source against raw ====================================================================

def source_against_raw(con, source) -> tuple:
    """The reader's row counts and sums, by version and period, against what landed."""
    batch = source.batch
    ledger = isinstance(source, LedgerExtraction)
    table = "raw_ledger_line" if ledger else "raw_plan_cell"
    version = "NULL" if ledger else "version"
    landed = {(v, p): (n, total) for v, p, n, total in con.execute(
        f"SELECT {version}, period, count(*), coalesce(sum(amount), 0) FROM {table} "
        "WHERE batch_id = ? GROUP BY ALL", [batch.batch_id]).fetchall()}
    stored = {(v, p): (n, total) for v, p, n, total in con.execute(
        "SELECT version, period, row_count, amount_sum FROM raw_control_total "
        "WHERE batch_id = ?", [batch.batch_id]).fetchall()}
    read = {(t.version, t.period): (t.row_count, t.amount_sum) for t in source.control_totals}
    breaks = []
    for key in sorted(set(read) | set(landed) | set(stored),
                      key=lambda k: (k[0] or "", k[1] or "")):
        version_of, period = key
        read_rows, read_sum = read.get(key, (0, Decimal(0)))
        raw_rows, raw_sum = landed.get(key, (0, Decimal(0)))
        stored_rows, stored_sum = stored.get(key, (None, None))
        if (read_rows, read_sum) == (raw_rows, raw_sum) == (stored_rows, stored_sum):
            continue
        what = "the ledger" if ledger else f"version {_member(version_of)}"
        breaks.append(_break(
            "source_against_raw", batch.batch_id, ("source", batch.source, version_of, period),
            MESSAGES["source_against_raw"].format(
                what=what, period=period, read_rows=read_rows, read_sum=_shown(read_sum),
                raw_rows=raw_rows, raw_sum=_shown(raw_sum),
                row_difference=read_rows - raw_rows, sum_difference=_shown(read_sum - raw_sum)),
            (("version", version_of or ""), ("period", period or ""),
             ("rows read", str(read_rows)), ("sum read", _plain(read_sum)),
             ("rows landed", str(raw_rows)), ("sum landed", _plain(raw_sum)),
             ("control total landed", "none" if stored_rows is None
              else f"{stored_rows} rows, {_plain(stored_sum)}")),
            period=period))
    return tuple(breaks)


# == The workbook's Actual block against the ledger ========================================

def _ledger_breaks(lines, entities: set, signs: dict, ledger_batch: str):
    """The ledger's own breaks, the lines left to compare, and the keys not to compare."""
    breaks, usable, skipped = [], [], set()
    unknown = {"entity": defaultdict(list), "account": defaultdict(list)}
    duplicates: dict = defaultdict(list)
    for line in lines:
        key = (line.entity, line.period, line.account)
        if line.row_error:
            breaks.append(_break(
                "row_error", ledger_batch, ("ledger_line", line.line_number),
                MESSAGES["row_error"].format(line=line.line_number, error=line.row_error),
                (("row error", line.row_error), ("amount as the file holds it",
                                                 line.amount_text or "")),
                entity=line.entity or None, account=line.account or None,
                period=line.period or None))
            if all(key):
                skipped.add(key)
        elif line.entity not in entities:
            unknown["entity"][line.entity].append(line)
        elif line.account not in signs:
            unknown["account"][line.account].append(line)
        elif line.duplicate:
            duplicates[key].append(line)
        else:
            usable.append(line)
    for member, codes in unknown.items():
        for code, found in codes.items():
            kind = f"unknown_{member}"
            breaks.append(_break(
                kind, ledger_batch, ("unknown", member, code),
                MESSAGES[kind].format(code=_member(code), lines=_lines(found)),
                ((f"{member} code", code), ("ledger lines", _lines(found))),
                entity=code if member == "entity" else None,
                account=code if member == "account" else None))
    for key, found in duplicates.items():
        entity, period, account = key
        skipped.add(key)
        breaks.append(_break(
            "duplicate_key", ledger_batch, ("ledger_key", entity, period, account),
            MESSAGES["duplicate_key"].format(count=len(found), entity=_member(entity),
                                             account=_member(account), period=period,
                                             lines=_lines(found)),
            (("ledger lines", _lines(found)),
             ("amounts as the file holds them", "; ".join(l.amount_text or "" for l in found))),
            entity=entity, account=account, period=period))
    return breaks, usable, skipped


def compare(pair: Pair, cells, lines, lists: Lists, left_out=(), accounts_left_out=()) -> tuple:
    """The workbook's Actual block against the ledger, for one pair, as typed breaks.

    cells are the plan batch's rows (PlanCell), lines the ledger batch's
    (LedgerLine), lists the plan batch's Lists. Nothing is read from a file or
    the warehouse here, so the rules can be shown on rows changed in memory.
    left_out names entities, and accounts_left_out (entity, account) pairs,
    whose figures are compared on neither side (part 2a2).
    """
    signs = {a.code: TO_PRESENTATION[a.ledger_sign] for a in lists.accounts
             if a.code and a.ledger_sign in TO_PRESENTATION}
    names = {a.code: a.name for a in lists.accounts if a.code}
    entities = set(lists.entity_codes())
    breaks, usable, skipped = _ledger_breaks(lines, entities, signs, pair.ledger_batch_id)
    left_out, accounts_left_out = set(left_out), set(accounts_left_out)

    def kept(entity, account) -> bool:
        return entity not in left_out and (entity, account) not in accounts_left_out
    usable = [line for line in usable if kept(line.entity, line.account)]

    workbook: dict = {}
    twice = set()
    for c in cells:
        if (c.version == ACTUAL and c.column_kind == MONTH and c.account in signs
                and c.entity in entities and kept(c.entity, c.account)):
            key = (c.entity, c.period, c.account)
            if key in workbook or c.duplicate:
                twice.add(key)
            workbook[key] = c
    unreadable = {key for key, c in workbook.items() if c.kind != "empty" and c.amount is None}
    skipped |= twice | unreadable

    by_month_workbook: dict = defaultdict(dict)
    for (entity, period, account), c in workbook.items():
        by_month_workbook[(entity, period)][account] = c
    by_month_ledger: dict = defaultdict(dict)
    for line in usable:
        by_month_ledger[(line.entity, line.period)][line.account] = line

    for month in sorted(set(by_month_workbook) | set(by_month_ledger)):
        entity, period = month
        in_book = by_month_workbook.get(month, {})
        in_ledger = by_month_ledger.get(month, {})
        book_has = {a: c for a, c in in_book.items() if c.amount is not None}
        if in_ledger and not book_has:
            breaks.append(_missing(pair, month, "missing_from_workbook", in_book, in_ledger,
                                   signs))
            continue
        if book_has and not in_ledger:
            breaks.append(_missing(pair, month, "missing_from_ledger", in_book, book_has, signs))
            continue
        if not in_ledger:
            continue
        differences = []
        for account in sorted(set(in_book) | set(in_ledger)):
            if (entity, period, account) in skipped:
                continue
            c, line = in_book.get(account), in_ledger.get(account)
            book = _whole(c.amount) if c is not None and c.amount is not None else Decimal(0)
            ledger = _whole(line.amount * signs[account]) if line is not None else Decimal(0)
            if book != ledger:
                differences.append((account, c, line, book, ledger, book - ledger))
        breaks += _differences(pair, month, differences, signs, names)
    return tuple(breaks)


def _missing(pair, month, which, in_book, holding, signs) -> Break:
    """One break for an entity-month that one side holds figures for and the other does not."""
    entity, period = month
    if which == "missing_from_workbook":
        net = sum((line.amount for line in holding.values()), Decimal(0))
        figures = "; ".join(f"{a}: {_plain(_whole(l.amount * signs[a]))}"
                            for a, l in sorted(holding.items()))
        evidence = (("ledger figures, in presentation sign", figures),
                    ("ledger lines", _lines(sorted(holding.values(),
                                                   key=lambda l: l.line_number))),
                    ("net in ledger sign", _plain(net)))
    else:
        net = sum((c.amount * signs[a] for a, c in holding.items()), Decimal(0))
        figures = "; ".join(f"{a}: {_plain(_whole(c.amount))}" for a, c in sorted(holding.items()))
        evidence = (("workbook figures, in presentation sign", figures),
                    ("net in ledger sign", _plain(net)))
    cells = list(in_book.values())
    first = cells[0] if cells else None
    return _break(
        "missing_entity_month", pair.plan_batch_id, ("entity_month", entity, period),
        MESSAGES[which].format(entity=_member(entity), period=period, count=len(holding),
                               net=_shown(_whole(net))),
        evidence, sheet=first.sheet if first else None, block=first.block if first else None,
        entity=entity, period=period, address=_range(cells))


def _differences(pair, month, differences, signs, names) -> list:
    """Equal and opposite pairs (in ledger sign) as reclassifications; the rest one by one."""
    entity, period = month
    in_ledger_sign = {d[0]: d[5] * signs[d[0]] for d in differences}
    paired, breaks = set(), []
    for i, a in enumerate(differences):
        if a[0] in paired:
            continue
        partner = next((b for b in differences[i + 1:] if b[0] not in paired
                        and in_ledger_sign[b[0]] == -in_ledger_sign[a[0]]), None)
        if partner is None:
            continue
        paired |= {a[0], partner[0]}
        cells = [d[1] for d in (a, partner) if d[1] is not None]
        breaks.append(_break(
            "possible_reclassification", pair.plan_batch_id,
            ("reclassification", entity, period, a[0], partner[0]),
            MESSAGES["possible_reclassification"].format(
                entity=_member(entity), period=period, line_a=_member(names.get(a[0])),
                workbook_a=_shown(a[3]), ledger_a=_shown(a[4]), difference_a=_shown(a[5]),
                line_b=_member(names.get(partner[0])), workbook_b=_shown(partner[3]),
                ledger_b=_shown(partner[4]), difference_b=_shown(partner[5])),
            (("workbook figures", f"{a[0]}: {_plain(a[3])}; {partner[0]}: {_plain(partner[3])}"),
             ("ledger figures, in presentation sign",
              f"{a[0]}: {_plain(a[4])}; {partner[0]}: {_plain(partner[4])}"),
             ("differences in ledger sign", f"{a[0]}: {_plain(in_ledger_sign[a[0]])}; "
                                            f"{partner[0]}: {_plain(in_ledger_sign[partner[0]])}")),
            sheet=cells[0].sheet if cells else None, block=cells[0].block if cells else None,
            entity=entity, account=f"{a[0]},{partner[0]}", period=period,
            address=",".join(c.address for c in cells) or None))
    for account, c, line, book, ledger, difference in differences:
        if account in paired:
            continue
        sign = next(k for k, v in TO_PRESENTATION.items() if v == signs[account])
        breaks.append(_break(
            "amount_difference", pair.plan_batch_id, ("amount", entity, account, period),
            MESSAGES["amount_difference"].format(
                line=_member(names.get(account)), entity=_member(entity), period=period,
                workbook=_shown(book), ledger=_shown(ledger), difference=_shown(difference)),
            (("workbook", _plain(book)), ("ledger in presentation sign", _plain(ledger)),
             ("ledger as landed", (line.amount_text or _plain(line.amount)) if line else "none"),
             ("ledger sign", sign), ("difference", _plain(difference)),
             ("ledger line", str(line.line_number) if line else "none")),
            sheet=c.sheet if c else None, block=c.block if c else None, entity=entity,
            account=account, period=period, address=c.address if c else None))
    return breaks


# == Reading what landed ======================================================================

def _cells(con, batch_id: str) -> tuple:
    rows = con.execute("SELECT * EXCLUDE (row_number) FROM raw_plan_cell WHERE batch_id = ? "
                       "AND version = ? AND column_kind = ? ORDER BY row_number",
                       [batch_id, ACTUAL, MONTH]).fetchall()
    return tuple(PlanCell(*row) for row in rows)


def _ledger_lines(con, batch_id: str) -> tuple:
    rows = con.execute("SELECT * FROM raw_ledger_line WHERE batch_id = ? ORDER BY line_number",
                       [batch_id]).fetchall()
    return tuple(LedgerLine(*row) for row in rows)


def _lists(con, batch_id: str) -> Lists:
    accounts = con.execute("SELECT code, name, parent, section, polarity, unit, ledger_sign, "
                           "kind, address FROM raw_list_account WHERE batch_id = ? "
                           "ORDER BY list_row", [batch_id]).fetchall()
    entities = con.execute("SELECT code, name, region, address FROM raw_list_entity "
                           "WHERE batch_id = ? ORDER BY list_row", [batch_id]).fetchall()
    return Lists(accounts=tuple(Account(*row) for row in accounts),
                 entities=tuple(Entity(*row) for row in entities), found_by="the warehouse")


def reconcile(con, plan, ledger: LedgerExtraction) -> ControlResult:
    """Source against raw for both batches, then the pair's Actual block against its ledger.

    plan and ledger are what the readers returned, already landed in con; the
    comparison reads the plan batch's Actual cells, its Lists and the
    ledger's lines back from the warehouse. What it leaves out is read from the
    plan batch's extraction, as the structure control reads it (part 2a2).
    """
    pair = Pair(plan.batch.batch_id, ledger.batch.batch_id)
    left_out, accounts_left_out = actual_left_out(plan)
    found = source_against_raw(con, plan) + source_against_raw(con, ledger) + compare(
        pair, _cells(con, pair.plan_batch_id), _ledger_lines(con, pair.ledger_batch_id),
        _lists(con, pair.plan_batch_id), left_out, accounts_left_out)
    order = {kind: i for i, kind in enumerate(KINDS)}
    return ControlResult(RECONCILE, RUN, None,
                         tuple(sorted(found, key=lambda b: order[b.kind])), ())
