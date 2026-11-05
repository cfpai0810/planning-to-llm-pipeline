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
"""

from __future__ import annotations

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
