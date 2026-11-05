# =============================================================================
# scripts/time_build.py: the warehouse build, timed step by step
# =============================================================================
"""
Times the start-up build the app runs (src/startup.py), from the committed
sample and ledger, step by step, and says what each number includes. The
steps are the reviewer's: read workbook, read ledger, land, controls,
reconcile, star schema. The maintainer runs it, and Phase 2 runs it on the
real host:

    python scripts/time_build.py               # every measure, five runs each
    python scripts/time_build.py --runs 3
    python scripts/time_build.py --first-run   # one fresh process and nothing else

Three measures:

- (a) in process: the build alone, in this process, with every module
  already imported and DuckDB's own first-use imports already done by one
  untimed build;
- (b) a fresh process: from launching Python to the end of the build, so
  the interpreter's start-up and every import are in it. Bytecode is already
  compiled: one untimed process runs first. The imports are also timed on
  their own (launch to src.startup and all it pulls in imported), and so is
  the interpreter's start-up alone;
- (c) the first run after a fresh install: --first-run, in a new
  environment, gives one fresh process with no untimed run before it.

DuckDB's Python binding imports numpy, pandas and pyarrow the first time a
statement is given a bound parameter, if they are installed (the lock has
them, because the web framework needs them). The pipeline never imports them
itself. The script records, at each step boundary, which of the three are
loaded, so the output says which step that import lands in, and it also times
that import alone, in a new process of its own.

At start nothing is promoted, so the start-up build's star schema step has
no facts to build. The script also times that step with facts: in a
throwaway copy of a built warehouse it acknowledges every break of the
sample pair, under a person named "Timing run (scripts/time_build.py)",
promotes the pair, and times the star schema's SQL files on it.

Nothing here is imported by the pipeline or the web app. The script builds
into a fresh temporary folder each time and deletes it afterwards; it reads
only the committed sources and makes no network call.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAZY = ("numpy", "pandas", "pyarrow")
END = "@@time_build: end"
TIMING_PERSON = "Timing run (scripts/time_build.py)"


# == Inside one process ==========================================================

class Recorder:
    """The build's clock: time.perf_counter, noting which of numpy, pandas and pyarrow are loaded."""

    def __init__(self):
        self.marks: list = []

    def __call__(self) -> float:
        now = time.perf_counter()
        self.marks.append((now, tuple(m for m in LAZY if m in sys.modules)))
        return now


def _build(folder: Path, recorder: Recorder) -> list:
    """[(step, seconds, modules first loaded in it)] for one start-up build into folder."""
    from src import startup
    built = startup.build_warehouse(folder, clock=recorder)
    steps = list(built.steps)
    marks = recorder.marks[-(len(steps) + 1):]
    out = []
    for (name, seconds), before, after in zip(steps, marks, marks[1:]):
        out.append((name, seconds, [m for m in after[1] if m not in before[1]]))
    return out


def _fresh_folder() -> Path:
    return Path(tempfile.mkdtemp(prefix="time_build_"))


def child(mode: str) -> None:
    """One fresh process: say when the mode's work ends, then print what was timed inside."""
    if mode == "start":
        print(END, flush=True)
        return
    sys.path.insert(0, str(ROOT))
    began = time.perf_counter()
    from src import startup, warehouse  # noqa: F401  (the imports are what is timed)
    imported = time.perf_counter() - began
    loaded_by_imports = [m for m in LAZY if m in sys.modules]
    if mode == "imports":
        print(END, flush=True)
        print(json.dumps({"imports": imported, "loaded": loaded_by_imports}))
        return
    if mode == "lazy":
        with warehouse.connect(":memory:") as con:
            first = time.perf_counter()
            con.execute("SELECT ?", ["a bound text"]).fetchall()
            first = time.perf_counter() - first
        print(END, flush=True)
        print(json.dumps({"first_bound_parameter": first,
                          "loaded": [m for m in LAZY if m in sys.modules]}))
        return
    folder = _fresh_folder()
    try:
        steps = _build(folder, Recorder())
        print(END, flush=True)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print(json.dumps({"imports": imported, "loaded_by_imports": loaded_by_imports,
                      "steps": steps}))


# == The measures ==================================================================

def _launch(mode: str) -> tuple[float, dict]:
    """Seconds from launching a fresh Python to its end line, and what it printed after."""
    started = time.perf_counter()
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child", mode],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", cwd=str(ROOT))
    elapsed = None
    for line in process.stdout:
        if line.rstrip("\n") == END:
            elapsed = time.perf_counter() - started
            break
    rest = process.stdout.read()
    error = process.stderr.read()
    process.wait()
    if elapsed is None or process.returncode != 0:
        raise SystemExit(f"the {mode} process failed (exit {process.returncode}): "
                         f"{error.strip().splitlines()[-1] if error.strip() else 'no message'}")
    return elapsed, (json.loads(rest) if rest.strip() else {})


def _spread(values: list) -> str:
    if len(values) == 1:
        return f"{values[0]:6.2f} s"
    return (f"min {min(values):6.2f} s, median {statistics.median(values):6.2f} s, "
            f"max {max(values):6.2f} s")


def _step_table(runs: list, indent: str = "  ") -> list[str]:
    """One line per step: its times over the runs, and what it imported first."""
    lines = []
    names = [name for name, _, _ in runs[0]]
    for i, name in enumerate(names):
        times = [run[i][1] for run in runs]
        loaded = sorted({m for run in runs for m in run[i][2]})
        note = f"  (numpy, pandas and pyarrow first imported here, by DuckDB)" \
            if set(LAZY) <= set(loaded) else (f"  (first imported here: {', '.join(loaded)})"
                                             if loaded else "")
        lines.append(f"{indent}{name:34s} {_spread(times)}{note}")
    return lines


def _header() -> list[str]:
    import importlib.metadata as metadata
    versions = []
    for package in ("duckdb", "openpyxl"):
        try:
            versions.append(f"{package} {metadata.version(package)}")
        except metadata.PackageNotFoundError:
            versions.append(f"{package} not installed")
    return [f"Python {platform.python_version()} ({sys.executable})",
            f"{platform.platform()}; {', '.join(versions)}",
            "Sources: the committed files the build lands at start (the read steps name them)"]


def first_run() -> list[str]:
    """(c): one build in a fresh process, the first Python process run in this environment."""
    elapsed, printed = _launch("build")
    out = ["(c) the first run after an install, one run, nothing run before it:",
           f"  start to build end (the number)    {_spread([elapsed])}",
           "  inside the build process:",
           f"    imports (src.startup and all)    {_spread([printed['imports']])}"]
    out += _step_table([printed["steps"]], indent="    ")
    return out


def fresh_process(runs: int) -> list[str]:
    """(b): fresh processes after one untimed one, so bytecode is compiled."""
    out = []
    _launch("build")
    starts = [_launch("start")[0] for _ in range(runs)]
    imports = [_launch("imports")[0] for _ in range(runs)]
    builds, inside = [], []
    for _ in range(runs):
        elapsed, printed = _launch("build")
        builds.append(elapsed)
        inside.append(printed)
    lazy = [_launch("lazy")[1] for _ in range(runs)]
    out.append(f"(b) a fresh process, {runs} run{'s' if runs > 1 else ''}, "
               "after one untimed process:")
    out.append(f"  interpreter start alone            {_spread(starts)}")
    out.append(f"  start to imports done              {_spread(imports)}")
    out.append(f"  start to build end (the number)    {_spread(builds)}")
    out.append("  inside the build process:")
    out.append(f"    imports (src.startup and all)    {_spread([p['imports'] for p in inside])}")
    out += _step_table([p["steps"] for p in inside], indent="    ")
    out.append(f"  DuckDB's first bound parameter, alone in a fresh process: "
               f"{_spread([p['first_bound_parameter'] for p in lazy])}; it imported "
               f"{', '.join(lazy[0]['loaded']) or 'nothing'}")
    imported_by_us = sorted({m for p in inside for m in p["loaded_by_imports"]})
    out.append(f"  of the three, loaded while importing src.startup: "
               f"{', '.join(imported_by_us) or 'none'} (by the libraries: openpyxl imports "
               f"numpy when it is installed; the pipeline's code imports none of them)")
    return out


def in_process(runs: int) -> list[str]:
    sys.path.insert(0, str(ROOT))
    from src import startup  # noqa: F401
    folder = _fresh_folder()
    try:
        _build(folder / "untimed", Recorder())
        all_runs = []
        for number in range(runs):
            all_runs.append(_build(folder / f"run{number}", Recorder()))
        totals = [sum(seconds for _, seconds, _ in run) for run in all_runs]
        out = [f"(a) in process, {runs} runs, after one untimed build:",
               f"  the build                          {_spread(totals)}"]
        out += _step_table(all_runs)
        out += _star_schema_with_facts(folder / "untimed", folder / "promoted", runs)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return out


def _star_schema_with_facts(built: Path, copy: Path, runs: int) -> list[str]:
    """The star schema step on a throwaway copy in which the sample pair is promoted."""
    from datetime import datetime, timezone

    from src import model, promotion, warehouse
    copy.mkdir(parents=True)
    shutil.copyfile(built / warehouse.DATABASE_NAME, copy / warehouse.DATABASE_NAME)
    times = []
    with warehouse.connect(copy / warehouse.DATABASE_NAME) as con:
        (pair,) = [promotion.Pair(p, l) for p, l in con.execute(
            "SELECT plan_batch_id, ledger_batch_id FROM gate_review").fetchall()]
        stamp = datetime.now(timezone.utc)
        for break_id in promotion.unacknowledged(con, pair):
            promotion.acknowledge(con, pair, break_id, TIMING_PERSON, "accepted",
                                  "Acknowledged only to time the star schema with facts.",
                                  clock=lambda: stamp)
        promotion.promote(con, pair, clock=lambda: stamp)
        for _ in range(runs):
            began = time.perf_counter()
            counts = model.build_model(con)
            times.append(time.perf_counter() - began)
    return [f"  star schema with facts to build    {_spread(times)}, in a throwaway copy where",
            f"    every break of the sample pair was acknowledged by \"{TIMING_PERSON}\" and the",
            f"    pair promoted: fact_plan {counts['fact_plan']} rows, fact_actual_erp "
            f"{counts['fact_actual_erp']} rows (at start both are empty)"]


INCLUDES = """What each number includes:
- interpreter start alone: launching python until its first line runs; no import of ours.
- start to imports done: start-up plus importing src.startup and all it pulls in
  (openpyxl, DuckDB, the readers, the controls, reconcile, promotion and the star
  schema's module), with bytecode already compiled.
- start to build end: start-up, those imports and the whole build, DuckDB's own import of
  numpy, pandas and pyarrow included; the process's exit and the deleting of the folder are not.
- inside the build process: the same run, timed from inside; the imports line starts after
  the interpreter has started.
- (a) in process: the build alone; every module already imported, and DuckDB's own imports
  already done by an untimed build. No start-up and no import is in it.
- each step: read workbook = extracting the workbook (openpyxl reads it twice);
  read ledger = reading the CSV; land = open the database, create the tables, land both
  batches, create the raw tables' indexes; controls = the five spreadsheet controls;
  reconcile = source against raw, the Actual block against the ledger, and storing the
  pair's breaks; star schema = the SQL files of sql/ (with nothing promoted, the facts
  are empty), then closing the file (closing writes it)."""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--first-run", action="store_true",
                        help="one fresh process with nothing run before it, and nothing else")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        child(args.child)
        return 0
    out = ["Warehouse build, timed step by step (scripts/time_build.py)"] + _header() + [""]
    if args.first_run:
        out += first_run()
    else:
        out += fresh_process(args.runs) + [""] + in_process(args.runs)
    out += ["", INCLUDES]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
