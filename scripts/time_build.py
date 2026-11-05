# =============================================================================
# scripts/time_build.py: the warehouse build, timed step by step
# =============================================================================
"""
Times the warehouse build the app runs at start, from the committed sample
and ledger, step by step, and says what each number includes. The
maintainer runs it, and Phase 2 runs it on the real host:

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
  their own (launch to src.warehouse and all it pulls in imported), and so is
  the interpreter's start-up alone;
- (c) the first run after a fresh install: --first-run, in a new
  environment, gives one fresh process with no untimed run before it.

DuckDB's Python binding imports numpy, pandas and pyarrow the first time a
statement is given a bound parameter, if they are installed (the lock has
them, because the web framework needs them). The pipeline never imports them
itself. The script records, at each step boundary, which of the three are
loaded, so the output says which step that import lands in, and it also times
that import alone, in a new process of its own.

Nothing here is imported by the pipeline or the web app. The script builds
into a fresh temporary folder each time and deletes it afterwards; it reads
only the committed sources and makes no network call.
"""

from __future__ import annotations

import argparse
import inspect
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
NOT_BUILT = ("reconcile", "star schema")


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
    """[(step, seconds, modules first loaded in it)] for one build into folder."""
    from src import warehouse
    if "clock" in inspect.signature(warehouse.build_warehouse).parameters:
        built = warehouse.build_warehouse(folder, clock=recorder)
        steps = list(built.steps)
    else:
        steps = _steps_by_hand(folder, recorder)
    marks = recorder.marks[-(len(steps) + 1):]
    out = []
    for (name, seconds), before, after in zip(steps, marks, marks[1:]):
        out.append((name, seconds, [m for m in after[1] if m not in before[1]]))
    return out


def _steps_by_hand(folder: Path, clock) -> list:
    """The build's steps timed from outside, for a tree whose build records no steps (9741b0e).

    The steps are the build's own, in its order: every workbook it lands,
    the ledger, then the landing (open, create the tables, land, close).
    """
    from src import warehouse
    from src.extract import extract_workbook
    from src.ledger import read_ledger
    Path(folder).mkdir(parents=True, exist_ok=True)
    steps, mark = [], clock()
    extractions = []
    for book in warehouse.COMMITTED_WORKBOOKS:          # 9741b0e's constant
        extractions.append(extract_workbook(book))
        now = clock()
        steps.append((f"read workbook ({Path(book).name})", now - mark))
        mark = now
    lists = extractions[0].lists
    lines = read_ledger(warehouse.COMMITTED_LEDGER, entities=lists.entity_codes(),
                        accounts=lists.ledger_accounts())
    now = clock()
    steps.append(("read ledger", now - mark))
    mark = now
    with warehouse.connect(Path(folder) / warehouse.DATABASE_NAME) as con:
        warehouse.create_tables(con)
        for source in (*extractions, lines):
            warehouse.land(con, source)
    now = clock()
    steps.append(("land", now - mark))
    return steps


def _fresh_folder() -> Path:
    return Path(tempfile.mkdtemp(prefix="time_build_"))


def child(mode: str) -> None:
    """One fresh process: say when the mode's work ends, then print what was timed inside."""
    if mode == "start":
        print(END, flush=True)
        return
    sys.path.insert(0, str(ROOT))
    began = time.perf_counter()
    from src import warehouse  # noqa: F401  (the imports are what is timed)
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
    for name in NOT_BUILT:
        if not any(name in n for n in names):
            lines.append(f"{indent}{name:34s} not built yet")
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
           f"    imports (src.warehouse and all)  {_spread([printed['imports']])}"]
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
    out.append(f"    imports (src.warehouse and all)  {_spread([p['imports'] for p in inside])}")
    out += _step_table([p["steps"] for p in inside], indent="    ")
    out.append(f"  DuckDB's first bound parameter, alone in a fresh process: "
               f"{_spread([p['first_bound_parameter'] for p in lazy])}; it imported "
               f"{', '.join(lazy[0]['loaded']) or 'nothing'}")
    imported_by_us = sorted({m for p in inside for m in p["loaded_by_imports"]})
    out.append(f"  of the three, loaded while importing src.warehouse: "
               f"{', '.join(imported_by_us) or 'none'} (by the libraries: openpyxl imports "
               f"numpy when it is installed; the pipeline's code imports none of them)")
    return out


def in_process(runs: int) -> list[str]:
    sys.path.insert(0, str(ROOT))
    from src import warehouse  # noqa: F401
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
        out += _controls(runs)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return out


def _controls(runs: int) -> list[str]:
    """The controls, timed on the sample's extraction when the build does not run them."""
    from src.controls import run_controls
    from src.extract import extract_workbook
    sample = extract_workbook(ROOT / "data" / "planning_model.xlsx")
    times = []
    for _ in range(runs):
        began = time.perf_counter()
        run_controls(sample)
        times.append(time.perf_counter() - began)
    return [f"  controls (not a step of this build) {_spread(times)}, on the sample's extraction"]


INCLUDES = """What each number includes:
- interpreter start alone: launching python until its first line runs; no import of ours.
- start to imports done: start-up plus importing src.warehouse and all it pulls in
  (openpyxl, DuckDB, the readers), with bytecode already compiled.
- start to build end: start-up, those imports and the whole build, DuckDB's own import of
  numpy, pandas and pyarrow included; the process's exit and the deleting of the folder are not.
- inside the build process: the same run, timed from inside; the imports line starts after
  the interpreter has started.
- (a) in process: the build alone; every module already imported, and DuckDB's own imports
  already done by an untimed build. No start-up and no import is in it.
- each step: read workbook = extracting the workbook (openpyxl reads it twice);
  read ledger = reading the CSV; land = open the database, create the tables, land every
  batch, create the indexes where the build has them, close (closing writes the file)."""


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
