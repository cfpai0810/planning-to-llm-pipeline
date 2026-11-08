# =============================================================================
# src/startup.py: the start-up build, every step in order
# =============================================================================
"""
The one function the app runs at start (Phase 2 calls it once per process,
behind a visible message), and that python main.py check runs on the files it
is given. It runs every step of the reviewer's list, in order, and records
the time of each with an injected clock:

1. read workbook: extract the planning workbook (src/extract.py);
2. read ledger: read the ledger extract against the workbook's Lists;
3. land: open a fresh warehouse file, create the tables, land both batches,
   then create the raw tables' indexes;
4. controls: the five spreadsheet controls over the workbook's extraction;
5. reconcile: source against raw for both batches, the workbook's Actual
   block against the ledger, and the pair's breaks stored for the promotion
   gate (then the gate's indexes);
6. star schema: the SQL files of sql/, then the file is closed.

The clock is called once before the first step and once after each, so
scripts/time_build.py can pass a clock of its own and see what happened
between two calls. Nothing is promoted here: at start the star schema's facts
are empty until a named person acknowledges every break and promotes the pair.

Part 2c adds build_template(folder): the template warehouse every session
copies (src/session.py). It lands both pairs the Review page will offer, the
sample workbook and the clean control workbook, each with the one committed
ledger, which lands once (one file, one batch, two pairs); runs the controls
and reconcile for each pair and stores each pair's breaks apart; builds the
star schema with nothing promoted; closes the file and leaves it read-only on
disk, so no session can change it.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src import model, promotion, warehouse
from src.batch import DEFAULT_MANIFEST, Pair
from src.controls import ControlReport, ControlResult, run_controls
from src.extract import Extraction, extract_workbook
from src.ledger import LedgerExtraction, read_ledger
from src.reconcile import reconcile

STEPS = ("read workbook", "read ledger", "land", "controls", "reconcile", "star schema")


@dataclass(frozen=True)
class Built:
    path: Path
    pair: Pair
    landed: tuple            # Landed or AlreadyLanded, for the workbook and the ledger
    plan: Extraction
    ledger: LedgerExtraction
    report: ControlReport    # the five controls over the workbook
    reconciled: ControlResult
    breaks: tuple            # the pair's breaks, as stored: the controls' and reconcile's
    steps: tuple             # (step, seconds), in the order of STEPS


def build_warehouse(folder: Path | str, workbook: Path | str = warehouse.SAMPLE_WORKBOOK,
                    ledger: Path | str = warehouse.COMMITTED_LEDGER,
                    manifest=DEFAULT_MANIFEST,
                    clock: Callable[[], float] = time.perf_counter) -> Built:
    """Builds a fresh warehouse in folder from one workbook and one ledger, and closes it.

    At start that is the committed sample and ledger. The ledger's codes are
    read against the workbook's Lists: its entities and its accounts that
    carry a ledger sign.
    """
    path = Path(folder) / warehouse.DATABASE_NAME
    if path.exists():
        raise warehouse.WarehouseError(f"{path.name} already exists in that folder: a "
                                       "warehouse is built fresh, never on top of another")
    Path(folder).mkdir(parents=True, exist_ok=True)
    steps = []
    mark = clock()

    def done(step: str) -> None:
        nonlocal mark
        now = clock()
        steps.append((step, now - mark))
        mark = now

    plan = extract_workbook(workbook, manifest=manifest)
    done("read workbook")
    lines = read_ledger(ledger, entities=plan.lists.entity_codes(),
                        accounts=plan.lists.ledger_accounts())
    done("read ledger")
    con = warehouse.connect(path)
    try:
        warehouse.create_tables(con)
        promotion.create_tables(con)
        landed = (warehouse.land(con, plan), warehouse.land(con, lines))
        warehouse.create_indexes(con)
        done("land")
        report = run_controls(plan)
        done("controls")
        pair = Pair(plan.batch.batch_id, lines.batch.batch_id)
        reconciled = reconcile(con, plan, lines)
        breaks = report.breaks() + reconciled.breaks
        promotion.store_breaks(con, pair, breaks)
        promotion.create_indexes(con)
        done("reconcile")
        model.build_model(con)
    finally:
        con.close()
    done("star schema")
    return Built(path, pair, landed, plan, lines, report, reconciled, breaks, tuple(steps))


# == The template warehouse (part 2c) ===========================================================

TEMPLATE_NAME = "template.duckdb"
TEMPLATE_STEPS = ("read workbooks", "read ledger", "land", "controls", "reconcile", "star schema")


@dataclass(frozen=True)
class Template:
    path: Path
    pairs: dict              # {"sample": Pair, "clean": Pair}, in that order
    breaks: dict             # each pair's breaks, as stored, by the same names
    steps: tuple             # (step, seconds), in the order of TEMPLATE_STEPS


def build_template(folder: Path | str, sample: Path | str = warehouse.SAMPLE_WORKBOOK,
                   clean: Path | str = warehouse.CLEAN_WORKBOOK,
                   ledger: Path | str = warehouse.COMMITTED_LEDGER, manifest=DEFAULT_MANIFEST,
                   clock: Callable[[], float] = time.perf_counter) -> Template:
    """Builds the template warehouse in folder, closes it and leaves it read-only on disk.

    The ledger is read once, against the sample workbook's Lists (the two workbooks share
    one Lists), and lands once; each pair is reconciled against it apart, and reconcile
    and the promotion gate keep each pair's breaks to that pair. Nothing is promoted.
    """
    path = Path(folder) / TEMPLATE_NAME
    if path.exists():
        raise warehouse.WarehouseError(f"{path.name} already exists in that folder: a "
                                       "template is built fresh, never on top of another")
    Path(folder).mkdir(parents=True, exist_ok=True)
    steps = []
    mark = clock()

    def done(step: str) -> None:
        nonlocal mark
        now = clock()
        steps.append((step, now - mark))
        mark = now

    plans = {"sample": extract_workbook(sample, manifest=manifest),
             "clean": extract_workbook(clean, manifest=manifest)}
    done("read workbooks")
    lists = plans["sample"].lists
    lines = read_ledger(ledger, entities=lists.entity_codes(), accounts=lists.ledger_accounts())
    done("read ledger")
    con = warehouse.connect(path)
    try:
        warehouse.create_tables(con)
        promotion.create_tables(con)
        for plan in plans.values():
            warehouse.land(con, plan)
        warehouse.land(con, lines)
        warehouse.create_indexes(con)
        done("land")
        reports = {name: run_controls(plan) for name, plan in plans.items()}
        done("controls")
        pairs, breaks = {}, {}
        for name, plan in plans.items():
            pairs[name] = Pair(plan.batch.batch_id, lines.batch.batch_id)
            breaks[name] = reports[name].breaks() + reconcile(con, plan, lines).breaks
            promotion.store_breaks(con, pairs[name], breaks[name])
        promotion.create_indexes(con)
        done("reconcile")
        model.build_model(con)
    finally:
        con.close()
    os.chmod(path, stat.S_IREAD)
    done("star schema")
    return Template(path, pairs, breaks, tuple(steps))
