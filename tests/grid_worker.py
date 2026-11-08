"""A worker of the oracle grid (tests/test_grid.py), run as a child process.

    python -m tests.grid_worker <job.json> <answers.json>

The job names the template warehouse (read-only; each session copies it), the pairs' batch
ids, a folder for the sessions, and a share of the grid's questions. The worker opens one
session of each pair and promotes it (the sample's 8 breaks acknowledged first), then, for
each question: builds its specification, validates it, compiles it once (the compiler runs
the guard on its own output), and runs the compiled query on the session of each pair. It
writes each answer's outcome, columns, rows, months held and gaps as JSON, for the test to
compare with the oracle's committed figures. It is a test file, not a test: it holds no
assertion, and pytest does not collect it.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

NOON = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)


def spec_of(metric, version, comparison, entities, period, group_by) -> dict:
    """A grid question as a specification: an entity set other than the group is an entity
    filter, asked by the group controller."""
    return {"metric": metric, "version": version,
            "dimensions": [] if group_by == "none" else [group_by],
            "filters": [] if entities == "group" else [{"dimension": "entity",
                                                         "values": [entities]}],
            "period": {"kind": period[0], "value": period[1]}, "comparison": comparison,
            "sort": None, "limit": 100}


def as_data(answer) -> dict:
    from src import outcomes
    if isinstance(answer, outcomes.Outcome):
        return {"outcome": answer.code}
    return {"outcome": answer.outcome.code if answer.outcome else "ok",
            "columns": [list(c) for c in answer.columns], "rows": [list(r) for r in answer.rows],
            "months_held": list(answer.coverage.months_held),
            "gaps": [list(g) for g in answer.gaps]}


def main(argv) -> int:
    job = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    from src import compiler, outcomes, roles, session, spec
    from src.batch import Pair
    from src.startup import Template
    template = Template(Path(job["template"]),
                        {name: Pair(*ids) for name, ids in job["pairs"].items()}, {}, ())
    pool = session.Sessions(template, root=Path(job["folder"]))
    try:
        sessions = {}
        for name in ("clean", "sample"):
            s = pool.open()
            breaks = s.breaks(name)
            if breaks:
                s.acknowledge_many(name, [(b.break_id, "A. Reviewer", "accepted")
                                          for b in breaks], clock=lambda: NOON)
            s.promote(name, "P. Promoter", clock=lambda: NOON)
            sessions[name] = s
        role = roles.group_controller
        loaded = sessions["clean"].registry()
        found = []
        for metric, version, comparison, entities, period, group_by in job["questions"]:
            question = (metric, version, comparison, entities, tuple(period), group_by)
            validated = spec.validate(spec.from_json(spec_of(*question)), loaded,
                                      sessions["clean"], role)
            if isinstance(validated, outcomes.Outcome):
                found.append([list(question), {"refused": validated.code}])
                continue
            query = compiler.compile(validated, loaded, role)           # once, for both pairs
            found.append([list(question), {name: as_data(s.run(query, validated, role))
                                           for name, s in sessions.items()}])
    finally:
        pool.close_all()
    Path(argv[1]).write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
