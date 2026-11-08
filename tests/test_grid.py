"""The oracle grid against the compiler (part 2d, the reviewer's brief section 8 and 9.2).

Every question of the oracle's grid (tests/oracle.py: 11 metrics, 7 version and comparison
pairs, 4 entity sets, 18 periods, and 5 or 6 groupings; 29,232 questions a pair) is asked of
the compiler as the group controller, an entity set being an entity filter, and compared with
the oracle's committed figures (tests/generated/oracle_expected.csv): the clean pair promoted,
and the sample pair promoted after its 8 breaks are acknowledged. The SQL does not depend on
the pair, so each question is validated, compiled and guarded once, and run on one session of
each pair. Every figure must equal the oracle's exactly, as Decimal values: the largest
difference is zero. The outcome, the rows and their order, the months held and the gaps must
be the same too.

The whole grid runs in the default suite. It is shared among worker processes
(tests/grid_worker.py), as many as the machine has processors and at most eight, each
started with subprocess_env(); each opens its own sessions from the one template. This test
process compares every answer.
"""

import csv
import json
import os
import subprocess
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from tests import oracle
from tests.conftest import subprocess_env

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / "tests" / "generated" / "oracle_expected.csv"
MONTHS = tuple(f"2026-{m:02d}" for m in range(1, 13))
FIGURES = ("value", "comparator", "variance", "variance_pct")
WORKERS = max(1, min(8, os.cpu_count() or 1))
SUMMARY: dict = {"questions": 0, "figures": 0, "largest": Decimal(0), "pairs": set()}


def months_text(months) -> str:
    """The oracle's run text: 2026-01..2026-04 2026-06."""
    runs: list = []
    for m in months:
        if runs and MONTHS.index(m) == MONTHS.index(runs[-1][-1]) + 1:
            runs[-1].append(m)
        else:
            runs.append([m])
    return " ".join(r[0] if len(r) == 1 else f"{r[0]}..{r[-1]}" for r in runs)


@pytest.fixture(scope="module")
def expected():
    found = defaultdict(list)
    with EXPECTED.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            found[(row["pair"], row["metric"], row["version"], row["comparison"],
                   row["entities"], row["period"], row["group_by"])].append(row)
    return found


@pytest.fixture(scope="module")
def answered(template, tmp_path_factory):
    """Every question answered on both pairs: {question: {pair: the answer as data}}."""
    folder = tmp_path_factory.mktemp("grid")
    questions = list(oracle.questions())
    running = []
    for i in range(WORKERS):
        job, out = folder / f"job_{i}.json", folder / f"answers_{i}.json"
        job.write_text(json.dumps({
            "template": str(template.path),
            "pairs": {name: [p.plan_batch_id, p.ledger_batch_id]
                      for name, p in template.pairs.items()},
            "folder": str(folder / f"sessions_{i}"),
            "questions": [list(q) for q in questions[i::WORKERS]]}), encoding="utf-8")
        running.append((subprocess.Popen(
            [sys.executable, "-m", "tests.grid_worker", str(job), str(out)], cwd=str(ROOT),
            env=subprocess_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8"), out))
    for process, _ in running:
        _, stderr = process.communicate(timeout=3600)
        assert process.returncode == 0, stderr[-2000:]
    found = {}
    for _, out in running:
        for question, by_pair in json.loads(out.read_text(encoding="utf-8")):
            metric, version, comparison, entities, period, group_by = question
            found[(metric, version, comparison, entities, tuple(period), group_by)] = by_pair
    assert len(found) == len(questions)
    return found


def compare(answer: dict, rows: list) -> tuple:
    """(figures compared, the largest difference, what disagrees) of one answer."""
    wrong, count, largest = [], 0, Decimal(0)
    first = rows[0]
    if "refused" in answer:
        return count, largest, ["refused: " + answer["refused"]]
    if answer["outcome"] not in ("ok", "no_data"):
        return count, largest, ["outcome: " + answer["outcome"]]
    if (months_text(answer["months_held"]), "; ".join(f"{e} {p}" for e, p in answer["gaps"])) \
            != (first["months_held"], first["gaps"]):
        wrong.append("coverage")
    if first["outcome"] == "no_data":
        if answer["outcome"] != "no_data" or answer["rows"]:
            wrong.append("outcome")
        return count, largest, wrong
    if answer["outcome"] != "ok" or len(answer["rows"]) != len(rows):
        return count, largest, wrong + ["rows"]
    names = [c[0] for c in answer["columns"]]
    for got, row in zip(answer["rows"], rows):
        cells = dict(zip(names, got))
        group = " ".join(cells[n] for n, kind, _ in answer["columns"] if kind == "dimension")
        if group != row["group"] or cells.get("label", "") != row["label"]:
            wrong.append(("group or label", group, row["group"]))
        for name in FIGURES:
            mine, theirs = cells.get(name, ""), row[name]
            if (mine == "not meaningful") != (theirs == "not meaningful") or (
                    (mine == "") != (theirs == "")):
                wrong.append((name, mine, theirs))
            elif mine not in ("", "not meaningful"):
                difference = abs(Decimal(mine) - Decimal(theirs))
                largest = max(largest, difference)
                count += 1
                if difference != 0:
                    wrong.append((name, mine, theirs))
    return count, largest, wrong


@pytest.mark.parametrize("pair", ["clean", "sample"])
@pytest.mark.parametrize("metric", oracle.METRICS)
def test_every_question_of_the_grid_matches_the_oracle(answered, expected, pair, metric):
    disagreements = []
    for question, by_pair in answered.items():
        if question[0] != metric:
            continue
        metric_, version, comparison, entities, period, group_by = question
        key = (pair, metric_, version, comparison, entities, oracle._period_text(period),
               group_by)
        answer = by_pair.get(pair, by_pair)
        count, largest, wrong = compare(answer, expected[key])
        SUMMARY["questions"] += 1
        SUMMARY["figures"] += count
        SUMMARY["largest"] = max(SUMMARY["largest"], largest)
        SUMMARY["pairs"].add(pair)
        if wrong:
            disagreements.append((key, wrong[:3]))
    assert disagreements[:5] == [] and len(disagreements) == 0


def test_the_whole_grid_was_compared_on_both_pairs(expected):
    questions = {k[1:] for k in expected}
    print(f"\nThe grid: {SUMMARY['questions']:,} questions compared ({len(questions):,} a pair, "
          f"on {', '.join(sorted(SUMMARY['pairs']))}; {WORKERS} workers); "
          f"{SUMMARY['figures']:,} figures; the largest difference {SUMMARY['largest']}")
    assert SUMMARY["questions"] == 2 * len(questions) == 2 * 29232
    assert SUMMARY["pairs"] == {"clean", "sample"}
    assert SUMMARY["largest"] == 0
