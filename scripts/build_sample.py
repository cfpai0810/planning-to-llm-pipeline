# =============================================================================
# scripts/build_sample.py: builds the sample workbooks (the maintainer's step)
# =============================================================================
r"""
Builds the committed sample of part 1b from src/sample_model.py:

    python scripts/build_sample.py
    python scripts/build_sample.py --only external_link extra_tab typed_subtotal

1. The clean control workbook first. It is written with openpyxl (formulas,
   no cached values), recalculated by LibreOffice headless, and checked before
   anything else: every cached value of every formula cell against the model,
   and every cell, formula, defined name, sheet and hidden state against what
   openpyxl wrote. Any difference stops the build.
2. The rest in one LibreOffice run: the sample (the clean workbook with T1 to
   T3 applied to its Actual block and D1 to D5 planted, so that every cached
   value, the Actual subtotals included, is computed by LibreOffice), the ten
   mutation variants and the fixtures, each checked the same way.
   The external_link fixture is hand-edited after its recalculation:
   LibreOffice rewrites its link's target against the output folder, so the
   one relationship file gets the plain file name back, and the formula and
   its cached value stay as LibreOffice wrote them. It is checked after the
   edit, and the manifest records the edit with the target before and after.
3. The shared_formula fixture (the recalculated small base with two groups of
   formulas stored as shared formulas, hand-edited too) and the no_cache
   fixture (written by openpyxl and never recalculated).
4. Only when every check has passed: the files are copied into data/ and
   tests/fixtures/, the ledger extract is written from the unmodified model in
   ledger sign, and data/sample_manifest.json records the planted truth, the
   checks, the file hashes, the hand edits and the LibreOffice version.

With --only, the build makes the named books and nothing else: step 1 still
recalculates and checks the clean workbook, but does not install it; only the
named books are converted, checked and installed; the ledger is not written;
and the manifest gains entries for the named books and their hand edits while
every other entry, and every top-level field, stays exactly as it was. A
partial build adds books and never replaces one: a book the manifest already
records is refused, so is a LibreOffice other than the one the manifest
records, and so is a manifest whose recorded files no longer match their hashes.

LibreOffice is found in this order: the SOFFICE environment variable, then
PATH, then C:\Program Files\LibreOffice\program\soffice.com. Where the path
found is soffice.exe, the soffice.com beside it is used: on Windows
soffice.exe hangs when run from a script. Every run gets a fresh profile
folder, passed as a file URI and deleted afterwards, even after a timeout; a
timeout kills the whole process tree, because a killed launcher leaves
soffice.bin running. The build converts into a fresh folder, never over its
input, and treats a missing or empty output as a failure. docs/rebuilding_the_sample.md
says the same for the maintainer. Only this step needs LibreOffice; the tests
and the app never do.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Font  # noqa: E402
from openpyxl.packaging.relationship import Relationship  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.workbook.defined_name import DefinedName  # noqa: E402
from openpyxl.workbook.external_link.external import (  # noqa: E402
    ExternalBook, ExternalCell, ExternalLink, ExternalRow, ExternalSheetData, ExternalSheetDataSet,
    ExternalSheetNames)
from openpyxl.worksheet.formula import ArrayFormula  # noqa: E402

from src import sample_model as model  # noqa: E402
from src.hashing import file_sha256  # noqa: E402

DEFAULT_SOFFICE = Path(r"C:\Program Files\LibreOffice\program\soffice.com")
TIMEOUT = 600
DATA = ROOT / "data"
FIXTURES = ROOT / "tests" / "fixtures"
MUTATIONS = FIXTURES / "mutations"
LEDGER = DATA / "erp_trial_balance.csv"
MANIFEST = DATA / "sample_manifest.json"


class BuildError(Exception):
    """A build failure, told in one plain line."""


# == Running LibreOffice ==========================================================

def find_soffice(environ=None) -> Optional[Path]:
    """The SOFFICE variable, then PATH, then the default install; soffice.com, never soffice.exe."""
    environ = os.environ if environ is None else environ
    found = None
    if environ.get("SOFFICE"):
        found = Path(environ["SOFFICE"])
    if found is None:
        on_path = shutil.which("soffice", path=environ.get("PATH", ""))
        found = Path(on_path) if on_path else None
    if found is None and Path(DEFAULT_SOFFICE).is_file():
        found = Path(DEFAULT_SOFFICE)
    if found is not None and found.name.lower() == "soffice.exe":
        launcher = found.with_name("soffice.com")
        if not launcher.is_file():
            raise BuildError(f"{found} hangs when run from a script, and there is no "
                             "soffice.com beside it")
        found = launcher
    return found


def profile_argument(folder: Path) -> str:
    """A fresh LibreOffice profile for one run, as a proper file URI."""
    return f"-env:UserInstallation={Path(folder).as_uri()}"


def conversion_arguments(outdir: Path, inputs) -> list[str]:
    return ["--headless", "--norestore", "--nolockcheck", "--convert-to", "xlsx",
            "--outdir", str(outdir), *map(str, inputs)]


def conversion_command(soffice: Path, profile: Path, outdir: Path, inputs) -> list[str]:
    """The whole command line of one conversion run."""
    return [str(soffice), profile_argument(profile), *conversion_arguments(outdir, inputs)]


def tree_kill_command(pid: int) -> list[str]:
    """Windows: end a process and every process it started."""
    return ["taskkill", "/T", "/F", "/PID", str(pid)]


def kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(tree_kill_command(process.pid), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=60, check=False)
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def run_tree(argv: list[str], timeout: float, env=None) -> subprocess.CompletedProcess:
    """Runs a command in its own process group; on a timeout kills the whole tree."""
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
               else {"start_new_session": True})
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8",
                               errors="replace", env=env, **options)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        try:
            process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        raise BuildError(f"LibreOffice timed out after {timeout} s; its process tree was killed")
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def process_running(pid: int) -> bool:
    if os.name == "nt":
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                                 capture_output=True, text=True, check=False).stdout
        return any(len(row) > 1 and row[1] == str(pid)
                   for row in csv.reader(listing.splitlines()))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat = Path(f"/proc/{pid}/stat")
    if stat.is_file():
        return stat.read_text().rsplit(")", 1)[-1].split()[0] != "Z"
    return True


def soffice_bin_pids() -> set[int]:
    """Every running soffice.bin, the process a LibreOffice launcher starts."""
    if os.name == "nt":
        listing = subprocess.run(["tasklist", "/FI", "IMAGENAME eq soffice.bin", "/NH", "/FO",
                                  "CSV"], capture_output=True, text=True, check=False).stdout
        return {int(row[1]) for row in csv.reader(listing.splitlines())
                if len(row) > 1 and row[0].lower() == "soffice.bin"}
    listing = subprocess.run(["ps", "-eo", "pid=,comm="], capture_output=True, text=True,
                             check=False).stdout
    return {int(line.split()[0]) for line in listing.splitlines()
            if line.split()[1:] and line.split()[1].startswith("soffice.bin")}


@contextmanager
def temporary_profile():
    folder = Path(tempfile.mkdtemp(prefix="lo_profile_"))
    try:
        yield folder
    finally:
        for _ in range(20):
            shutil.rmtree(folder, ignore_errors=True)
            if not folder.exists():
                break
            time.sleep(0.5)


SOFFICE_SECONDS: list[float] = []


def run_soffice(soffice: Path, args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """One LibreOffice run with a fresh profile folder, deleted afterwards whatever happens."""
    with temporary_profile() as profile:
        argv = [str(soffice), profile_argument(profile), *args]
        if "--convert-to" in args:
            print("  " + subprocess.list2cmdline(argv), flush=True)
        started = time.monotonic()
        try:
            return run_tree(argv, timeout)
        finally:
            SOFFICE_SECONDS.append(time.monotonic() - started)


def soffice_version(soffice: Path, timeout: float = 60) -> str:
    result = run_soffice(soffice, ["--version"], timeout)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not lines or not lines[0].startswith("LibreOffice"):
        raise BuildError(f"{soffice} --version did not print a LibreOffice version")
    return lines[0]


def convert(soffice: Path, inputs: list[Path], outdir: Path, timeout: float) -> list[Path]:
    """Recalculates workbooks into a fresh output folder; returns the outputs, in input order."""
    for book in inputs:
        if Path(outdir).resolve() == book.parent.resolve():
            raise BuildError(f"the output folder must not be the input's folder: {outdir}")
    if Path(outdir).exists() and any(Path(outdir).iterdir()):
        raise BuildError(f"the output folder must be fresh and empty: {outdir}")
    Path(outdir).mkdir(parents=True, exist_ok=True)
    result = run_soffice(soffice, conversion_arguments(outdir, inputs), timeout)
    outputs = []
    for book in inputs:
        output = Path(outdir) / book.name
        if not output.is_file() or output.stat().st_size == 0:
            raise BuildError(f"LibreOffice wrote no output for {book.name}")
        outputs.append(output)
    if result is not None and result.returncode != 0:
        raise BuildError(f"LibreOffice ended with code {result.returncode}")
    return outputs


# == The workbook layout ============================================================
# Every sheet's content starts at (row0, col0): (1, 1), or (4, 3) for the
# shifted fixture, whose first row holds a banner. Offsets below are from there.

FULL = tuple(entity.code for entity in model.ENTITIES)
LINE_ROW = {line.name: i for i, line in enumerate(model.LINES)}
BLOCK_TITLE = {"Actual": 4, "Budget": 20, "Forecast": 36}
OVERRIDE_ROW = 52                    # D3: one blank row below the Forecast block
# Part 2a: the extra_version_block fixture's block, a copy of the Forecast
# block's lines under the label Plan B, one blank row below the Forecast block.
EXTRA_VERSION = "Plan B"
EXTRA_TITLE = 52
ASSUMPTIONS_HEADER = 3
LISTS_ACCOUNTS = 2
CONTROL_SUMMARY = 11
CONTROL_CHECK = 20                   # the extra Control formula of two fixtures
MONEY, WHOLE, ONE_PLACE, PRICE, SHARE = "#,##0", "#,##0", "0.0", "0.00", "0.0%"
BOLD = Font(bold=True)
VERSION_HEADERS = {"Actual": "Actual FY2026 to date", "Budget": "Budget FY2026",
                   "Forecast": "Forecast FY2026"}


@dataclass(frozen=True)
class Shape:
    entities: tuple = FULL            # on Lists and Assumptions
    sheets: tuple = FULL              # the entities with a plan sheet
    row0: int = 1
    col0: int = 1
    banner: str = ""
    names: tuple = ()                 # (line name, the name the workbook shows)

    def name(self, line: str) -> str:
        return dict(self.names).get(line, line)


SMALL = Shape(entities=("ES",), sheets=("ES",))


def col(shape: Shape, offset: int) -> str:
    return get_column_letter(shape.col0 + offset)


def title_row(shape: Shape, version: str) -> int:
    if version == EXTRA_VERSION:
        return shape.row0 + EXTRA_TITLE
    return shape.row0 + BLOCK_TITLE[version]


def header_row(shape: Shape, version: str) -> int:
    return title_row(shape, version) + 1


def line_row(shape: Shape, version: str, line: str) -> int:
    return header_row(shape, version) + 1 + LINE_ROW[line]


def key_col(shape: Shape, key: str) -> str:
    """The column of a month, or of the FY2026 total."""
    if key == model.FY:
        return col(shape, 14)
    return col(shape, 2 + model.PERIODS.index(key))


def coordinate(shape: Shape, version: str, line: str, key: str) -> str:
    return f"{key_col(shape, key)}{line_row(shape, version, line)}"


def driver_row(shape: Shape, version: str, entity: str, driver: str) -> int:
    index = 0
    for v in ("Budget", "Forecast"):
        for e in shape.entities:
            for d in model.DRIVERS:
                if (v, e, d.name) == (version, entity, driver):
                    return shape.row0 + ASSUMPTIONS_HEADER + 1 + index
                index += 1
    raise KeyError((version, entity, driver))


def lookup(shape: Shape, version: str, period: str, driver: str) -> str:
    """SUMIFS fetching one driver for this block's version and entity, in this month."""
    first = shape.row0 + ASSUMPTIONS_HEADER + 1
    last = first + 2 * len(shape.entities) * len(model.DRIVERS) - 1
    month = col(shape, 4 + model.PERIODS.index(period))
    v, e, d = col(shape, 0), col(shape, 1), col(shape, 2)
    title = f"${col(shape, 0)}${title_row(shape, version)}"
    entity = f"${col(shape, 1)}${shape.row0 + 1}"
    return (f"SUMIFS(Assumptions!{month}${first}:{month}${last},"
            f"Assumptions!${v}${first}:${v}${last},{title},"
            f"Assumptions!${e}${first}:${e}${last},{entity},"
            f'Assumptions!${d}${first}:${d}${last},"{driver}")')


def month_formula(shape: Shape, version: str, line: str, period: str, embedded: str = "") -> str:
    """The formula of a line in one month of a block (Actual: the subtotals only)."""
    x = key_col(shape, period)

    def at(name):
        return f"{x}{line_row(shape, version, name)}"

    def driven(base, driver):
        product = f"{at(base)}*" if base else ""
        return f"=ROUND({product}{lookup(shape, version, period, driver)}{embedded},0)"

    formulas = {
        "Product sales": lambda: driven("Volume", "Price"),
        "Co-packing income": lambda: driven(None, "Co-packing fees"),
        "Materials": lambda: driven("Volume", "Unit cost"),
        "Packaging": lambda: driven("Volume", "Packaging per unit"),
        "Freight out": lambda: driven("Volume", "Freight per unit"),
        "Gross profit": lambda: (f"=SUM({at('Product sales')}:{at('Co-packing income')})"
                                 f"-SUM({at('Materials')}:{at('Freight out')})"),
        "Salaries": lambda: driven("Headcount", "Cost per head"),
        "Marketing": lambda: (f"=ROUND(SUM({at('Product sales')}:{at('Co-packing income')})*"
                              f"{lookup(shape, version, period, 'Marketing share')},0)"),
        "Rent and facilities": lambda: driven(None, "Rent"),
        "IT and software": lambda: driven(None, "IT services"),
        "EBIT": lambda: f"={at('Gross profit')}-SUM({at('Salaries')}:{at('IT and software')})",
        "Volume": lambda: f"={lookup(shape, version, period, 'Volume')}",
        "Headcount": lambda: f"={lookup(shape, version, period, 'Headcount')}",
    }
    return formulas[line]()


def total_formula(shape: Shape, version: str, line: str, last: str = "2026-12") -> str:
    row = line_row(shape, version, line)
    if line == "Headcount":
        closing = model.CLOSED_PERIODS[-1] if version == "Actual" else model.PERIODS[-1]
        return f"={key_col(shape, closing)}{row}"
    return f"=SUM({key_col(shape, '2026-01')}{row}:{key_col(shape, last)}{row})"


def _number(value: Decimal):
    """A whole Decimal as an int, so the workbook holds 1490400, not 1490400.00."""
    return int(value) if value == value.to_integral_value() else value


def _format(line: str) -> str:
    return {"Volume": WHOLE, "Headcount": ONE_PLACE}.get(line, MONEY)


# == Writing the workbook ============================================================

def _put(ws, shape: Shape, row: int, offset: int, value, bold=False, number_format=None):
    cell = ws[f"{col(shape, offset)}{row}"]
    cell.value = value
    if bold:
        cell.font = BOLD
    if number_format:
        cell.number_format = number_format
    return cell


def _widths(ws, shape: Shape, widths: dict) -> None:
    for offset, width in widths.items():
        ws.column_dimensions[col(shape, offset)].width = width


def _write_control(ws, shape: Shape) -> None:
    r = shape.row0
    _put(ws, shape, r, 0, f"{model.COMPANY}: {model.FISCAL_YEAR} planning model", bold=True)
    facts = (("Company", model.COMPANY), ("Version label", "Budget FY2026 and June Forecast"),
             ("Preparer", "Group planning team"), ("As-of date", model.AS_OF),
             ("Currency", model.CURRENCY), ("Fiscal year", model.FISCAL_YEAR),
             ("Closed months", len(model.CLOSED_PERIODS)),
             ("Entities", ", ".join(shape.entities)))
    for i, (label, value) in enumerate(facts):
        _put(ws, shape, r + 2 + i, 0, label)
        _put(ws, shape, r + 2 + i, 1, value)
    top = r + CONTROL_SUMMARY
    _put(ws, shape, top, 0, "Group summary", bold=True)
    for i, text in enumerate(["Line item", "Unit", *VERSION_HEADERS.values()]):
        _put(ws, shape, top + 1, i, text, bold=True)
    fy = key_col(shape, model.FY)
    for i, line in enumerate(("Revenue", "Gross profit", "EBIT")):
        _put(ws, shape, top + 2 + i, 0, line)
        _put(ws, shape, top + 2 + i, 1, "EUR")
        for j, version in enumerate(model.VERSIONS):
            if line == "Revenue":
                first = line_row(shape, version, "Product sales")
                last = line_row(shape, version, "Co-packing income")
                parts = [f"SUM(Plan_{e}!{fy}{first}:{fy}{last})" for e in shape.sheets]
            else:
                parts = [f"Plan_{e}!{fy}{line_row(shape, version, line)}" for e in shape.sheets]
            _put(ws, shape, top + 2 + i, 2 + j, "=" + "+".join(parts), number_format=MONEY)
    _put(ws, shape, top + 6, 0, "Notes", bold=True)
    _put(ws, shape, top + 7, 0, "Actual is typed as loaded from the ledger at the June close. "
                                "Budget and Forecast are calculated from Assumptions.")
    _widths(ws, shape, {0: 30, 1: 34, 2: 22, 3: 16, 4: 18})


def _write_assumptions(ws, shape: Shape) -> None:
    r = shape.row0
    _put(ws, shape, r, 0, "Assumptions: drivers by version, entity and month", bold=True)
    _put(ws, shape, r + 1, 0, "Budget set in November 2025. Forecast at the June 2026 close: "
                              "January to June as they happened, July to December reforecast.")
    header = r + ASSUMPTIONS_HEADER
    for i, text in enumerate(["Version", "Entity", "Driver", "Unit", *model.PERIODS]):
        _put(ws, shape, header, i, text, bold=True)
    formats = {"Price": PRICE, "Unit cost": PRICE, "Packaging per unit": PRICE,
               "Freight per unit": PRICE, "Marketing share": SHARE, "Headcount": ONE_PLACE}
    for version in ("Budget", "Forecast"):
        for entity in shape.entities:
            values = model.driver_values(version, entity)
            for driver in model.DRIVERS:
                row = driver_row(shape, version, entity, driver.name)
                for i, text in enumerate((version, entity, driver.name, driver.unit)):
                    _put(ws, shape, row, i, text)
                for i, value in enumerate(values[driver.name]):
                    _put(ws, shape, row, 4 + i, _number(value),
                         number_format=formats.get(driver.name, MONEY))
    _widths(ws, shape, {0: 10, 1: 8, 2: 20, 3: 18} | {4 + i: 11 for i in range(12)})


def _write_plan(ws, shape: Shape, entity: str) -> None:
    r = shape.row0
    name = next(e.name for e in model.ENTITIES if e.code == entity)
    _put(ws, shape, r, 0, f"{name}: plan by month", bold=True)
    _put(ws, shape, r + 1, 0, "Entity")
    _put(ws, shape, r + 1, 1, entity)
    _put(ws, shape, r + 2, 0, "Currency")
    _put(ws, shape, r + 2, 1, model.CURRENCY)
    actual = model.block(entity, "Actual")
    for version in model.VERSIONS:
        _put(ws, shape, title_row(shape, version), 0, version, bold=True)
        for i, text in enumerate(["Line item", "Unit", *model.PERIODS, model.FY]):
            _put(ws, shape, header_row(shape, version), i, text, bold=True)
        for line in model.LINES:
            row = line_row(shape, version, line.name)
            _put(ws, shape, row, 0, shape.name(line.name))
            _put(ws, shape, row, 1, line.unit)
            for period in model.PERIODS:
                if version != "Actual":
                    value = month_formula(shape, version, line.name, period)
                elif period not in model.CLOSED_PERIODS:
                    continue
                elif line.name in model.SUBTOTALS:
                    value = month_formula(shape, version, line.name, period)
                else:
                    value = _number(actual[line.name][period])
                _put(ws, shape, row, 2 + model.PERIODS.index(period), value,
                     number_format=_format(line.name))
            _put(ws, shape, row, 14, total_formula(shape, version, line.name),
                 number_format=_format(line.name))
    _widths(ws, shape, {0: 34, 1: 7} | {2 + i: 12 for i in range(13)})


def _write_lists(ws, shape: Shape) -> None:
    r = shape.row0
    _put(ws, shape, r, 0, "Lists", bold=True)
    top = r + LISTS_ACCOUNTS
    for i, text in enumerate(("Code", "Name", "Parent", "Section", "Polarity", "Unit",
                              "Ledger sign", "Kind")):
        _put(ws, shape, top, i, text, bold=True)
    for j, line in enumerate(model.LINES):
        values = (line.code, shape.name(line.name), line.parent, line.section, line.polarity,
                  line.unit, line.ledger_sign, line.kind)
        for i, value in enumerate(values):
            if value:
                _put(ws, shape, top + 1 + j, i, value)
    entities_top = top + len(model.LINES) + 2
    for i, text in enumerate(("Code", "Name", "Region")):
        _put(ws, shape, entities_top, i, text, bold=True)
    listed = [e for e in model.ENTITIES if e.code in shape.entities]
    for j, entity in enumerate(listed):
        for i, value in enumerate((entity.code, entity.name, entity.region)):
            _put(ws, shape, entities_top + 1 + j, i, value)
    _widths(ws, shape, {0: 8, 1: 34, 2: 20, 3: 16, 4: 24, 5: 7, 6: 12, 7: 7})


def _name(wb, name: str, sheet: str, first: str, last: str) -> None:
    def absolute(ref):
        letters, digits = re.fullmatch(r"([A-Z]+)(\d+)", ref).groups()
        return f"${letters}${digits}"
    wb.defined_names[name] = DefinedName(name, attr_text=f"{sheet}!{absolute(first)}:"
                                                         f"{absolute(last)}")


def write_workbook(shape: Shape = Shape()) -> Workbook:
    """The clean workbook of a shape, as openpyxl writes it: formulas and no cached values."""
    wb = Workbook()
    wb.properties.creator = "Group planning team"
    wb.properties.lastModifiedBy = "Group planning team"
    wb.properties.title = f"{model.COMPANY} {model.FISCAL_YEAR} planning model"
    control = wb.active
    control.title = "Control"
    _write_control(control, shape)
    _write_assumptions(wb.create_sheet("Assumptions"), shape)
    for entity in shape.sheets:
        _write_plan(wb.create_sheet(f"Plan_{entity}"), shape, entity)
    _write_lists(wb.create_sheet("Lists"), shape)
    for entity in shape.sheets:
        for version in model.VERSIONS:
            _name(wb, f"{version}_{entity}", f"Plan_{entity}",
                  f"{col(shape, 0)}{header_row(shape, version)}",
                  f"{col(shape, 14)}{line_row(shape, version, 'Headcount')}")
    top = shape.row0 + LISTS_ACCOUNTS
    _name(wb, "Accounts", "Lists", f"{col(shape, 0)}{top}",
          f"{col(shape, 7)}{top + len(model.LINES)}")
    entities_top = top + len(model.LINES) + 2
    _name(wb, "Entities", "Lists", f"{col(shape, 0)}{entities_top}",
          f"{col(shape, 2)}{entities_top + len(shape.entities)}")
    first = shape.row0 + ASSUMPTIONS_HEADER
    _name(wb, "Drivers", "Assumptions", f"{col(shape, 0)}{first}",
          f"{col(shape, 15)}{first + 2 * len(shape.entities) * len(model.DRIVERS)}")
    if shape.banner:
        for ws in wb.worksheets:
            ws["A1"] = shape.banner
            ws["A1"].font = BOLD
    return wb


# == The planted faults: one function to plant and one to remove each ==============

def _cell(wb, shape: Shape, plant: model.Plant, key: Optional[str] = None):
    ws = wb[plant.sheet]
    return ws[coordinate(shape, plant.block, plant.line, key or plant.period)]


def plant_d1(wb, shape: Shape = Shape()) -> None:
    _cell(wb, shape, model.D1).value = _number(model.D1_TYPED)


def remove_d1(wb, shape: Shape = Shape()) -> None:
    d = model.D1
    _cell(wb, shape, d).value = month_formula(shape, d.block, d.line, d.period)


def plant_d2(wb, shape: Shape = Shape()) -> None:
    d = model.D2
    _cell(wb, shape, d).value = total_formula(shape, d.block, d.line, last=model.D2_LAST)


def remove_d2(wb, shape: Shape = Shape()) -> None:
    d = model.D2
    _cell(wb, shape, d).value = total_formula(shape, d.block, d.line)


def plant_d3(wb, shape: Shape = Shape()) -> None:
    ws, row = wb[model.D3.sheet], shape.row0 + OVERRIDE_ROW
    _put(ws, shape, row, 0, model.D3_LABEL)
    _put(ws, shape, row, 1, "EUR")
    _put(ws, shape, row, 2 + model.PERIODS.index(model.D3.period), _number(model.D3_VALUE),
         number_format=MONEY)
    ws.row_dimensions[row].hidden = True


def remove_d3(wb, shape: Shape = Shape()) -> None:
    ws, row = wb[model.D3.sheet], shape.row0 + OVERRIDE_ROW
    ws.delete_rows(row)
    ws.row_dimensions.pop(row, None)


def plant_d4(wb, shape: Shape = Shape()) -> None:
    d = model.D4
    _cell(wb, shape, d).value = month_formula(shape, d.block, d.line, d.period,
                                              embedded=f"*{model.D4_FACTOR}")


def remove_d4(wb, shape: Shape = Shape()) -> None:
    d = model.D4
    _cell(wb, shape, d).value = month_formula(shape, d.block, d.line, d.period)


SCRATCH = (("Scratch: price test, not used",),
           (),
           ("Price", Decimal("3.45"), Decimal("3.55"), Decimal("3.65")),
           ("Volume", 480000, 462000, 445000),
           ("Revenue", 1656000, 1640100, 1624250))


def plant_d5(wb, shape: Shape = Shape()) -> None:
    ws = wb.create_sheet(model.D5.sheet)
    for i, row in enumerate(SCRATCH, start=1):
        for j, value in enumerate(row, start=1):
            ws.cell(i, j).value = value
    ws.sheet_state = "hidden"


def remove_d5(wb, shape: Shape = Shape()) -> None:
    wb.remove(wb[model.D5.sheet])


PLANTERS: dict[str, tuple[Callable, Callable]] = {
    "D1": (plant_d1, remove_d1), "D2": (plant_d2, remove_d2), "D3": (plant_d3, remove_d3),
    "D4": (plant_d4, remove_d4), "D5": (plant_d5, remove_d5),
}


def apply_tieouts(wb, shape: Shape = Shape()) -> None:
    """T1 to T3 on the Actual block: the ledger is written from the model before them."""
    names = {line.code: line.name for line in model.LINES}
    t1, t2, t3 = model.T1, model.T2, model.T3
    cell = wb[f"Plan_{t1.entity}"][coordinate(shape, "Actual", names[t1.workbook_account],
                                              t1.period)]
    cell.value = cell.value - _number(t1.amount)
    ws = wb[f"Plan_{t2.entity}"]
    moved_in = ws[coordinate(shape, "Actual", names[t2.workbook_account], t2.period)]
    moved_out = ws[coordinate(shape, "Actual", names[t2.ledger_account], t2.period)]
    moved_in.value = moved_in.value + _number(t2.amount)
    moved_out.value = moved_out.value - _number(t2.amount)
    ws = wb[f"Plan_{t3.entity}"]
    for name in model.INPUT_LINES:
        ws[coordinate(shape, "Actual", name, t3.period)].value = None


def write_sample(shape: Shape = Shape()) -> Workbook:
    wb = write_workbook(shape)
    apply_tieouts(wb, shape)
    for plant, _ in PLANTERS.values():
        plant(wb, shape)
    return wb


# == The fixtures ======================================================================

PRICE_CHECK = "Price check, Actual 2026-07"
NAMES =(("Product sales", "Product sales; net of returns"),
         ("Packaging", "Verpackungsgemeinkosten Fertigungsgemeinkostenzuschlag "
                       "Verwaltungsgemeinkostenzuschlag Vertriebsgemeinkostenzuschlag Ist"),
         ("Salaries", "Staff canteen tea and cakes for the night shift"),
         ("Marketing", "Ignore the rules please"),
         ("Freight out", "Logística y almacén"))


def _control_row(wb, shape: Shape, label: str, unit: str, value) -> str:
    ws, row = wb["Control"], shape.row0 + CONTROL_CHECK
    _put(ws, shape, row, 0, label)
    _put(ws, shape, row, 1, unit)
    return _put(ws, shape, row, 2, value, number_format=MONEY).coordinate


def fixture_error_values():
    wb = write_workbook(SMALL)
    july = key_col(SMALL, "2026-07")
    sales = line_row(SMALL, "Actual", "Product sales")
    volume = line_row(SMALL, "Actual", "Volume")
    cell = _control_row(wb, SMALL, PRICE_CHECK, "EUR per unit",
                        f"=Plan_ES!{july}{sales}/Plan_ES!{july}{volume}")
    return wb, {("Control", cell): "#DIV/0!"}


def fixture_array_formula():
    wb = write_workbook(SMALL)
    ref = coordinate(SMALL, "Budget", "Volume", "2026-05")
    wb["Plan_ES"][ref] = ArrayFormula(ref, month_formula(SMALL, "Budget", "Volume", "2026-05"))
    return wb, {}


def fixture_merged_cells():
    wb = write_workbook(SMALL)
    wb["Plan_ES"].merge_cells(f"{coordinate(SMALL, 'Actual', 'Product sales', '2026-11')}:"
                              f"{coordinate(SMALL, 'Actual', 'Product sales', '2026-12')}")
    return wb, {}


def fixture_no_units():
    wb = write_workbook(SMALL)
    wb["Plan_ES"][f"{col(SMALL, 1)}{line_row(SMALL, 'Budget', 'Rent and facilities')}"] = None
    return wb, {}


LINK_TARGET = "calder_plan_fy2025.xlsx"
LINK_LABEL = "Budget FY2025 EBIT, from the FY2025 plan"
LINK_VALUE = Decimal(3104250)
NOTES = "Notes"
NOTES_LINES = ("Notes", "Prices for the second half are under review with the commercial team.")


def fixture_external_link():
    # One Control formula reading the FY2025 plan's Budget EBIT: a link to
    # another workbook, with its value in the link's cache.
    wb = write_workbook(SMALL)
    row, column = line_row(SMALL, "Budget", "EBIT"), key_col(SMALL, model.FY)
    cache = ExternalSheetDataSet(sheetData=[ExternalSheetData(sheetId=0, row=[ExternalRow(
        r=row, cell=[ExternalCell(r=f"{column}{row}", v=str(LINK_VALUE))])])])
    link = ExternalLink(externalBook=ExternalBook(
        sheetNames=ExternalSheetNames(sheetName=["Plan_ES"]), sheetDataSet=cache, id="rId1"))
    link.file_link = Relationship(type="externalLinkPath", Target=LINK_TARGET,
                                  TargetMode="External")
    wb._external_links.append(link)
    cell = _control_row(wb, SMALL, LINK_LABEL, "EUR", f"=[1]Plan_ES!${column}${row}")
    return wb, {("Control", cell): LINK_VALUE}


def fixture_extra_tab():
    # A visible tab outside the expected set, holding text and no formula.
    wb = write_workbook(SMALL)
    ws = wb.create_sheet(NOTES)
    for i, text in enumerate(NOTES_LINES, start=1):
        ws.cell(i, 1).value = text
    ws["A1"].font = BOLD
    return wb, {}


def fixture_typed_subtotal():
    # The June Actual EBIT typed as the number its formula gave: every total
    # still agrees, and only the formula row tells.
    wb = write_workbook(SMALL)
    figure = model.block("ES", "Actual")["EBIT"]["2026-06"]
    wb["Plan_ES"][coordinate(SMALL, "Actual", "EBIT", "2026-06")] = _number(figure)
    return wb, {}


def fixture_missing_tab():
    return write_workbook(Shape(entities=("ES", "AT"), sheets=("ES",))), {}


def fixture_missing_header():
    # The total column's header: every month still has its header, so no
    # figure goes unread, and with no total column found in the block the
    # totals control has nothing there to compare.
    wb = write_workbook(SMALL)
    wb["Plan_ES"][f"{key_col(SMALL, model.FY)}{header_row(SMALL, 'Budget')}"] = None
    return wb, {}


def fixture_duplicate_period():
    # In the Actual block's open months, whose cells are empty, so no figure
    # and no total depends on which of the two columns is read as 2026-07.
    wb = write_workbook(SMALL)
    wb["Plan_ES"][f"{key_col(SMALL, '2026-08')}{header_row(SMALL, 'Actual')}"] = "2026-07"
    return wb, {}


def fixture_unknown_account():
    # A line that feeds no subtotal, so that a totals control summing the
    # subtotals by their parents on Lists has nothing it cannot place.
    wb = write_workbook(SMALL)
    wb["Plan_ES"][f"{col(SMALL, 0)}{line_row(SMALL, 'Forecast', 'Volume')}"] = "Cases sold"
    return wb, {}


# -- Part 1e: the anchors, the currency and a known blind spot -------------------------
# Three fixtures change one defined name of the small base and nothing else,
# so every cell and every cached value is the small base's. Two are made from
# the clean workbook, because the small base has no Plan_AT and reconcile
# needs every entity of the ledger.

def _rename_block(wb, shape: Shape, name: str, first_row: int, last_row: int) -> None:
    """Points a block's defined name at other rows of its own tab, columns unchanged."""
    entity = name.rsplit("_", 1)[1]
    _name(wb, name, f"Plan_{entity}", f"{col(shape, 0)}{first_row}",
          f"{col(shape, 14)}{last_row}")


def fixture_block_short():
    # The ES Budget name ends two rows early: the block loses Volume and
    # Headcount, the last two accounts on Lists. Nothing totals them.
    wb = write_workbook(SMALL)
    _rename_block(wb, SMALL, "Budget_ES", header_row(SMALL, "Budget"),
                  line_row(SMALL, "Budget", "EBIT"))
    return wb, {}


def fixture_name_shifted():
    # The ES Budget name one row down: its first row is the Product sales
    # line, so the block it names has no header row to read.
    wb = write_workbook(SMALL)
    _rename_block(wb, SMALL, "Budget_ES", header_row(SMALL, "Budget") + 1,
                  line_row(SMALL, "Budget", "Headcount") + 1)
    return wb, {}


def fixture_name_wrong_version():
    # The ES Forecast name placed on the Budget rows, where the Budget name
    # also points: the Budget figures would be read as Forecast.
    wb = write_workbook(SMALL)
    _rename_block(wb, SMALL, "Forecast_ES", header_row(SMALL, "Budget"),
                  line_row(SMALL, "Budget", "Headcount"))
    return wb, {}


def fixture_currency_changed():
    # Plan_AT's Currency cell (B3, beside its label) reads USD; no formula
    # reads it, so every figure is the clean workbook's.
    shape = Shape()
    wb = write_workbook(shape)
    wb["Plan_AT"][f"{col(shape, 1)}{shape.row0 + 2}"] = "USD"
    return wb, {}


WRONG_DRIVER = ("DE", "Budget", "Rent and facilities", "Rent", "IT services")


def fixture_wrong_driver_row():
    # The DE Budget Rent and facilities line fetches the IT services driver
    # in all twelve months: the same formula in every month, so no pattern
    # tells, and every total is computed from it, so every total agrees. The
    # line, the EBIT it feeds, their FY2026 totals and the Control tab's
    # Budget EBIT take new figures, given here as specials.
    shape = Shape()
    entity, version, line, _, wrong = WRONG_DRIVER
    wb = write_workbook(shape)
    ws = wb[f"Plan_{entity}"]
    figures = model.block(entity, version)
    specials, moved = {}, Decimal(0)
    for period in model.PERIODS:
        cell = ws[coordinate(shape, version, line, period)]
        cell.value = f"=ROUND({lookup(shape, version, period, wrong)},0)"
        fetched = model.round_whole(model.driver_values(version, entity)[wrong]
                                    [model.PERIODS.index(period)])
        change = figures[line][period] - fetched
        moved += change
        specials[(ws.title, cell.coordinate)] = fetched
        specials[(ws.title, coordinate(shape, version, "EBIT", period))] = \
            figures["EBIT"][period] + change
    specials[(ws.title, coordinate(shape, version, line, model.FY))] = \
        figures[line][model.FY] - moved
    specials[(ws.title, coordinate(shape, version, "EBIT", model.FY))] = \
        figures["EBIT"][model.FY] + moved
    summary = model.group_summary(shape.sheets)
    ebit_budget = f"{col(shape, 2 + model.VERSIONS.index(version))}" \
                  f"{shape.row0 + CONTROL_SUMMARY + 2 + 2}"
    specials[("Control", ebit_budget)] = summary[("EBIT", version)] + moved
    return wb, specials


def _summary_cells(shape: Shape, figures: dict, versions) -> dict:
    """The Control tab's group summary cells of some versions, given the summary's figures."""
    top = shape.row0 + CONTROL_SUMMARY
    return {("Control", f"{col(shape, 2 + model.VERSIONS.index(v))}{top + 2 + i}"):
            figures[(line, v)]
            for i, line in enumerate(("Revenue", "Gross profit", "EBIT")) for v in versions}


def fixture_entity_changed():
    # Plan_ES's Entity cell (B2, beside its label) reads AT. The Budget and
    # Forecast formulas fetch their drivers by that cell, so the tab's Budget
    # and Forecast blocks compute AT's figures; the typed Actual block and its
    # subtotals stay ES's. The changed cells are given as specials.
    shape = Shape()
    wb = write_workbook(shape)
    wb["Plan_ES"][f"{col(shape, 1)}{shape.row0 + 1}"] = "AT"
    specials = {}
    for version in ("Budget", "Forecast"):
        figures = model.block("AT", version)
        for line in model.LINES:
            for key in model.PERIODS + (model.FY,):
                specials[("Plan_ES", coordinate(shape, version, line.name, key))] = \
                    figures[line.name][key]
    specials.update(_summary_cells(shape, model.group_summary(("AT", "AT", "DE")),
                                   ("Budget", "Forecast")))
    return wb, specials


def fixture_name_other_tab():
    # The defined name Budget_AT points at the Budget rows of Plan_ES, another
    # entity's tab; Budget_ES still points there too. Every cell is the clean
    # workbook's.
    shape = Shape()
    wb = write_workbook(shape)
    _name(wb, "Budget_AT", "Plan_ES", f"{col(shape, 0)}{header_row(shape, 'Budget')}",
          f"{col(shape, 14)}{line_row(shape, 'Budget', 'Headcount')}")
    return wb, {}


VERSION_LABEL = "Plan B"
RENAMED_SALARIES = "Staff costs"


def fixture_version_label_changed():
    # The label above the ES Budget block's header row reads Plan B. The
    # Budget formulas fetch their drivers by that label, so every Budget cell
    # computes 0, and so does the Control tab's Budget column (ES alone).
    wb = write_workbook(SMALL)
    wb["Plan_ES"][f"{col(SMALL, 0)}{title_row(SMALL, 'Budget')}"] = VERSION_LABEL
    specials = {("Plan_ES", coordinate(SMALL, "Budget", line.name, key)): Decimal(0)
                for line in model.LINES for key in model.PERIODS + (model.FY,)}
    zero = {(line, "Budget"): Decimal(0) for line in ("Revenue", "Gross profit", "EBIT")}
    specials.update(_summary_cells(SMALL, zero, ("Budget",)))
    return wb, specials


def fixture_name_shifted_up():
    # The ES Budget name one row up: its first row is the block's title row,
    # and its header row is the second row of its range.
    wb = write_workbook(SMALL)
    _rename_block(wb, SMALL, "Budget_ES", title_row(SMALL, "Budget"),
                  line_row(SMALL, "Budget", "Headcount") - 1)
    return wb, {}


# -- Part 2a: one fault one break, three versions, case and spaces ---------------------------
# Every one is made from the full clean workbook and changes it in the cells
# named below and nowhere else. Only extra_version_block adds formulas, and
# they compute 0, given as specials.

def _label_cell(shape: Shape, version: str, line: str) -> str:
    return f"{col(shape, 0)}{line_row(shape, version, line)}"


def fixture_renamed_feeds_subtotal():
    # The ES Forecast Salaries line relabelled Staff costs, a label not on
    # Lists; Salaries feeds EBIT. No formula reads a label.
    shape = Shape()
    wb = write_workbook(shape)
    wb["Plan_ES"][_label_cell(shape, "Forecast", "Salaries")] = RENAMED_SALARIES
    return wb, {}


def fixture_duplicate_account():
    # The ES Budget Materials label overwritten by Salaries, so the block holds
    # Salaries twice and no Materials. No formula reads a label.
    shape = Shape()
    wb = write_workbook(shape)
    wb["Plan_ES"][_label_cell(shape, "Budget", "Materials")] = "Salaries"
    return wb, {}


def fixture_unit_column_empty():
    # The ES Budget block's Unit column emptied on all thirteen lines; its
    # "Unit" header stays.
    shape = Shape()
    wb = write_workbook(shape)
    for line in model.LINES:
        wb["Plan_ES"][f"{col(shape, 1)}{line_row(shape, 'Budget', line.name)}"] = None
    return wb, {}


def fixture_extra_version_block():
    # A copy of the ES Forecast block's lines, one blank row below it, under the
    # label Plan B and with no defined name: the labels, the units, and the
    # formulas written for the new block's own rows. They fetch their drivers
    # by the block's label, Plan B, which Assumptions does not hold, so every
    # cell computes 0.
    shape = Shape()
    wb = write_workbook(shape)
    ws = wb["Plan_ES"]
    version = EXTRA_VERSION
    _put(ws, shape, title_row(shape, version), 0, version, bold=True)
    for i, text in enumerate(["Line item", "Unit", *model.PERIODS, model.FY]):
        _put(ws, shape, header_row(shape, version), i, text, bold=True)
    specials = {}
    for line in model.LINES:
        row = line_row(shape, version, line.name)
        _put(ws, shape, row, 0, line.name)
        _put(ws, shape, row, 1, line.unit)
        for period in model.PERIODS:
            cell = _put(ws, shape, row, 2 + model.PERIODS.index(period),
                        month_formula(shape, version, line.name, period),
                        number_format=_format(line.name))
            specials[(ws.title, cell.coordinate)] = Decimal(0)
        cell = _put(ws, shape, row, 14, total_formula(shape, version, line.name),
                    number_format=_format(line.name))
        specials[(ws.title, cell.coordinate)] = Decimal(0)
    return wb, specials


def _currency(value: str):
    def make():
        # Plan_AT's Currency cell (B3, beside its label) differs from EUR in
        # case or spaces only; no formula reads it.
        shape = Shape()
        wb = write_workbook(shape)
        wb["Plan_AT"][f"{col(shape, 1)}{shape.row0 + 2}"] = value
        return wb, {}
    return make


def fixture_version_label_lowercase():
    # The label above the ES Budget block's header row reads budget. The
    # Budget formulas fetch their drivers by that label, and the lookup
    # ignores case, so every figure is the clean workbook's.
    shape = Shape()
    wb = write_workbook(shape)
    wb["Plan_ES"][f"{col(shape, 0)}{title_row(shape, 'Budget')}"] = "budget"
    return wb, {}


SHIFTED =Shape(row0=4, col0=3, banner="Calder Foods Group: draft for review, not for issue")

# A target written for this machine: a drive, a root, a URI, or a folder name
# that names the machine (with the user name and the work folder, checked below).
MACHINE_TARGET = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|file:)", re.IGNORECASE)
MACHINE_FOLDERS = {"users", "home", "appdata", "temp", "tmp"}
LINK_EDIT_WHY = ("LibreOffice rewrote the link's target against the build's output folder; it is "
                 "restored to the plain file name the workbook was written with, so no committed "
                 "file names a folder of the build machine. The formula and its cached value are "
                 "as LibreOffice wrote them.")


def _user_name() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or Path.home().name


def recorded_target(target: str, work: Path, user: Optional[str] = None) -> str:
    """The target as it may be recorded: a folder of this machine becomes <work folder>."""
    user = _user_name() if user is None else user
    *folders, name = re.split(r"[\\/]", target)
    machine = (MACHINE_TARGET.match(target) or Path(work).name in folders
               or any(f.lower() in MACHINE_FOLDERS for f in folders) or (user and user in folders)
               or any(f.startswith(("calder_build_", "lo_profile_")) for f in folders))
    return f"<work folder>/{name}" if machine else target


def restore_link_target(source: Path, target: Path, work: Path, user: Optional[str] = None) -> dict:
    """Copies the workbook with its one external link's target set back to the plain file name.

    Every other part is copied byte for byte. A file name other than the one
    written stops the build: only the folder LibreOffice prefixed is undone.
    """
    with zipfile.ZipFile(source) as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    parts = {info.filename: data for info, data in entries}
    rels = parts.get("xl/_rels/workbook.xml.rels", b"").decode("utf-8")
    links = re.findall(r'<Relationship [^>]*Type="[^"]*/externalLink"[^>]*>', rels)
    if len(links) != 1:
        raise BuildError(f"external_link: {len(links)} links to another workbook, not one")
    link = re.search(r'Target="([^"]+)"', links[0])[1]
    link = link.lstrip("/") if link.startswith("/") else posixpath.normpath(f"xl/{link}")
    part = posixpath.join(posixpath.dirname(link), "_rels", posixpath.basename(link) + ".rels")
    xml = parts[part].decode("utf-8")
    paths = re.findall(r'<Relationship [^>]*Type="[^"]*/externalLinkPath"[^>]*>', xml)
    if len(paths) != 1:
        raise BuildError(f"external_link: {len(paths)} link targets, not one")
    before = re.search(r'Target="([^"]*)"', paths[0])[1]
    if re.split(r"[\\/]", before)[-1] != LINK_TARGET:
        raise BuildError(f"external_link: LibreOffice changed the linked file to {before}")
    if before != LINK_TARGET:
        restored = paths[0].replace(f'Target="{before}"', f'Target="{LINK_TARGET}"', 1)
        parts[part] = xml.replace(paths[0], restored, 1).encode("utf-8")
    with zipfile.ZipFile(target, "w") as out:
        for info, _ in entries:
            out.writestr(info, parts[info.filename], compress_type=zipfile.ZIP_DEFLATED)
    return {"part": part, "attribute": "Target", "before": recorded_target(before, work, user),
            "after": LINK_TARGET}


@dataclass
class Book:
    """One workbook the build writes: where it goes and what the model expects in it."""
    dest: Path
    make: Callable
    shape: Shape = Shape()
    plants: frozenset = frozenset()
    tieouts: bool = False
    purpose: str = ""
    expected: str = ""
    specials: dict = field(default_factory=dict)
    edit: Optional[Callable] = None     # a hand edit after the recalculation, recorded
    base: str = ""                      # what it is made from, recorded in the manifest (1e)

    @property
    def name(self) -> str:
        return self.dest.name

    @property
    def rel(self) -> str:
        return self.dest.relative_to(ROOT).as_posix()


def _simple(make_wb):
    return lambda: (make_wb(), {})


def _clean_plus(defect):
    def make():
        wb = write_workbook()
        PLANTERS[defect][0](wb)
        return wb, {}
    return make


def _sample_minus(defect):
    def make():
        wb = write_sample()
        PLANTERS[defect][1](wb)
        return wb, {}
    return make


ALL = frozenset(model.PLANTS)
CLEAN = Book(DATA / "planning_model_clean.xlsx", _simple(write_workbook),
             purpose="The clean control workbook: the same structure, none of the planted "
                     "faults, and an Actual block that matches the ledger exactly.",
             expected="No spreadsheet finding and no tie-out break against the same ledger.")
SAMPLE = Book(DATA / "planning_model.xlsx", _simple(write_sample), plants=ALL, tieouts=True,
              purpose="The sample: the clean workbook with T1 to T3 in its Actual block and "
                      "D1 to D5 planted.",
              expected="Exactly D1 to D5 (D4 once, with two reasons); against the ledger, "
                       "exactly T1 to T3.")
MUTATION_BOOKS = (
    [Book(MUTATIONS / f"clean_plus_{d.lower()}.xlsx", _clean_plus(d), plants=frozenset({d}),
          purpose=f"The clean workbook plus {d} only.", expected=f"Exactly the break of {d}.")
     for d in model.PLANTS]
    + [Book(MUTATIONS / f"sample_minus_{d.lower()}.xlsx", _sample_minus(d), plants=ALL - {d},
            tieouts=True, purpose=f"The sample without {d}.",
            expected=f"The sample's breaks less the break of {d}.")
       for d in model.PLANTS])
SMALL_NOTE = "The small base (the clean workbook with ES alone on Lists, Assumptions and Plan)"
FIXTURE_BOOKS = [
    Book(FIXTURES / "shifted.xlsx", _simple(lambda: write_workbook(SHIFTED)), SHIFTED,
         purpose="The clean workbook with every sheet's content moved three rows down and two "
                 "columns right, under a banner row in A1.",
         expected="No finding; it extracts to exactly the clean workbook's rows."),
    Book(FIXTURES / "error_values.xlsx", fixture_error_values, SMALL,
         purpose=f"{SMALL_NOTE}, plus one Control formula dividing two empty Actual cells of "
                 "2026-07, cached as #DIV/0!.",
         expected="Calculation state: one error-value break, at that cell."),
    Book(FIXTURES / "array_formula.xlsx", fixture_array_formula, SMALL,
         purpose=f"{SMALL_NOTE}, with ES Budget Volume for 2026-05 written as a one-cell array "
                 "formula of the row's own formula.",
         expected="No break; the cell is recorded as an array formula, never as a constant."),
    Book(FIXTURES / "merged_cells.xlsx", fixture_merged_cells, SMALL,
         purpose=f"{SMALL_NOTE}, with two empty cells of the ES Actual block merged (Product "
                 "sales, 2026-11 and 2026-12).",
         expected="Structure: one merged-cells break, and nothing else."),
    Book(FIXTURES / "no_units.xlsx", fixture_no_units, SMALL,
         purpose=f"{SMALL_NOTE}, with the unit of Rent and facilities in the ES Budget block "
                 "left empty.",
         expected="Structure: one no-unit break, and nothing else."),
    Book(FIXTURES / "missing_tab.xlsx", fixture_missing_tab, Shape(("ES", "AT"), ("ES",)),
         purpose=f"{SMALL_NOTE}, with AT on Lists and Assumptions but no Plan_AT sheet.",
         expected="Structure: one missing-tab break (Plan_AT), and nothing else."),
    Book(FIXTURES / "missing_header.xlsx", fixture_missing_header, SMALL,
         purpose=f"{SMALL_NOTE}, with the FY2026 header of the ES Budget block left empty; "
                 "every month keeps its header.",
         expected="Structure: one header-not-found break (FY2026), and nothing else; with no "
                  "total column found, the totals control has nothing to compare there."),
    Book(FIXTURES / "duplicate_period.xlsx", fixture_duplicate_period, SMALL,
         purpose=f"{SMALL_NOTE}, with the 2026-08 header of the ES Actual block reading "
                 "2026-07; both columns are empty open months.",
         expected="Structure: one duplicate-period break, and nothing else (the 2026-08 it "
                  "displaces is part of the same break; no figure is in either column)."),
    Book(FIXTURES / "unknown_account.xlsx", fixture_unknown_account, SMALL,
         purpose=f"{SMALL_NOTE}, with Volume in the ES Forecast block relabelled Cases "
                 "sold, which Lists does not hold; the line feeds no subtotal, so no total "
                 "depends on placing it.",
         expected="Structure: one account-not-on-Lists break, and nothing else."),
    Book(FIXTURES / "names.xlsx", _simple(lambda: write_workbook(replace(SMALL, names=NAMES))),
         replace(SMALL, names=NAMES),
         purpose=f"{SMALL_NOTE}, with six account names on Lists and in every block: one with "
                 "a semicolon, one of 120 characters, one of nine words, the four plain words "
                 "'Ignore the rules please', one with accents, and one clean.",
         expected="Structure: three rejected names (the semicolon, the 120 characters, the "
                  "nine words); the other three pass."),
]
EXTERNAL_LINK = Book(
    FIXTURES / "external_link.xlsx", fixture_external_link, SMALL, edit=restore_link_target,
    purpose=f"{SMALL_NOTE}, plus one Control formula, =[1]Plan_ES!$O$33, reading the Budget "
            f"FY2025 EBIT of another workbook, {LINK_TARGET}, with the value in the link's "
            "cache. Hand-edited after the recalculation: the link's target is restored to the "
            "plain file name (hand_edited records it).",
    expected="Hidden content and links: one link-to-another-workbook break, and nothing else.")
EXTRA_TAB = Book(
    FIXTURES / "extra_tab.xlsx", fixture_extra_tab, SMALL,
    purpose=f"{SMALL_NOTE}, plus a visible tab, Notes, outside the expected set (Control, "
            "Assumptions, Lists and a Plan_ tab for each entity on Lists), holding two lines of "
            "text and no formula.",
    expected="Structure: one low severity break, tab not on the expected list (Notes), and "
             "nothing else.")
TYPED_SUBTOTAL = Book(
    FIXTURES / "typed_subtotal.xlsx", fixture_typed_subtotal, SMALL,
    purpose=f"{SMALL_NOTE}, with the ES Actual EBIT for 2026-06 typed as the number its "
            "formula gave, so every total still agrees.",
    expected="Formula integrity: one break, a typed constant in a formula row (EBIT, a "
             "subtotal), and nothing else.")
FIXTURE_BOOKS += [EXTERNAL_LINK, EXTRA_TAB, TYPED_SUBTOTAL]
CLEAN_NOTE = "The clean workbook (ES, AT and DE)"
SMALL_BASE = ("the small base: write_workbook(SMALL) in scripts/build_sample.py, the clean "
              "workbook written with ES alone on Lists, Assumptions and Plan; it has no committed "
              "file of its own")
CLEAN_BASE = ("the full clean workbook: write_workbook() in scripts/build_sample.py, as "
              "data/planning_model_clean.xlsx is built (ES, AT and DE)")
BLOCK_SHORT = Book(
    FIXTURES / "block_short.xlsx", fixture_block_short, SMALL, base=SMALL_BASE,
    purpose=f"{SMALL_NOTE}, with the defined name Budget_ES ending two rows early, at the EBIT "
            "row, so the block it names lacks Volume and Headcount, the last two accounts on "
            "Lists. Every cell is the small base's.",
    expected="Structure: one medium break, accounts not in the block (Budget_ES lacks VOL "
             "Volume and HC Headcount), and nothing else.")
NAME_SHIFTED = Book(
    FIXTURES / "name_shifted.xlsx", fixture_name_shifted, SMALL, base=SMALL_BASE,
    purpose=f"{SMALL_NOTE}, with the defined name Budget_ES moved one row down, so its first "
            "row is the Product sales line and not the header row. Every cell is the small "
            "base's.",
    expected="Structure: one high break, the name's block cannot be read (its first row has no "
             "Line item header and no month header); no no-unit or header-not-found break, and "
             "nothing else.")
NAME_WRONG_VERSION = Book(
    FIXTURES / "name_wrong_version.xlsx", fixture_name_wrong_version, SMALL, base=SMALL_BASE,
    purpose=f"{SMALL_NOTE}, with the defined name Forecast_ES placed on the Budget rows, where "
            "Budget_ES also points. Every cell is the small base's.",
    expected="Structure: one high break naming Forecast_ES and the sheet's label Budget above "
             "the block's header row, and nothing else.")
CURRENCY_CHANGED = Book(
    FIXTURES / "currency_changed.xlsx", fixture_currency_changed, base=CLEAN_BASE,
    purpose=f"{CLEAN_NOTE}, with Plan_AT's Currency cell, B3, reading USD while every money "
            "account on Lists is in EUR. No formula reads the cell, so every figure is the "
            "clean workbook's. Made from the clean workbook because the small base has no "
            "Plan_AT.",
    expected="Structure: one high break, the tab's currency (USD in B3) is not the unit of the "
             "money accounts on Lists (EUR); against the committed ledger, nothing else.")
WRONG_DRIVER_ROW = Book(
    FIXTURES / "wrong_driver_row.xlsx", fixture_wrong_driver_row, base=CLEAN_BASE,
    purpose=f"{CLEAN_NOTE}, with the DE Budget Rent and facilities line fetching the IT "
            "services driver instead of Rent in all twelve months: the same formula in every "
            "month, and every total computed from it. A known blind spot of the controls.",
    expected="No break, from the controls or against the committed ledger: a row that is wrong "
             "in the same way in every month has no pattern to break (docs/controls.md, What "
             "the controls do not see).")
ENTITY_CHANGED = Book(
    FIXTURES / "entity_changed.xlsx", fixture_entity_changed, base=CLEAN_BASE,
    purpose=f"{CLEAN_NOTE}, with Plan_ES's Entity cell, B2, reading AT. The Budget and Forecast "
            "formulas fetch their drivers by that cell, so Plan_ES's Budget and Forecast blocks "
            "compute AT's figures (and the Control tab's Budget and Forecast totals move); the "
            "typed Actual block stays ES's.",
    expected="Structure: one high break for the tab, the Entity cell (AT in B2) against the "
             "defined names of its blocks (ES); against the committed ledger, nothing else.")
NAME_OTHER_TAB = Book(
    FIXTURES / "name_other_tab.xlsx", fixture_name_other_tab, base=CLEAN_BASE,
    purpose=f"{CLEAN_NOTE}, with the defined name Budget_AT pointing at the Budget rows of "
            "Plan_ES, another entity's tab. Every cell is the clean workbook's. Made from the "
            "clean workbook because the small base has no Plan_AT.",
    expected="Structure: one high break naming Budget_AT and where it points; Plan_AT's Budget "
             "block is read by its labels, as before; against the committed ledger, nothing "
             "else.")
VERSION_LABEL_CHANGED = Book(
    FIXTURES / "version_label_changed.xlsx", fixture_version_label_changed, SMALL,
    base=SMALL_BASE,
    purpose=f"{SMALL_NOTE}, with the label above the ES Budget block's header row reading "
            f"{VERSION_LABEL}. The Budget formulas fetch their drivers by that label, so every "
            "Budget cell computes 0, and so does the Control tab's Budget column.",
    expected="Structure: one high break, the defined name Budget_ES against the sheet's label "
             f"{VERSION_LABEL}, and nothing else.")
NAME_SHIFTED_UP = Book(
    FIXTURES / "name_shifted_up.xlsx", fixture_name_shifted_up, SMALL, base=SMALL_BASE,
    purpose=f"{SMALL_NOTE}, with the defined name Budget_ES moved one row up, so its first row "
            "is the block's title row (Budget) and its header row is the second. Every cell is "
            "the small base's.",
    expected="Structure: one high break, the name's block cannot be read (its first row has no "
             "Line item header and no month header); no no-unit or header-not-found break, and "
             "nothing else.")
FIXTURE_BOOKS += [BLOCK_SHORT, NAME_SHIFTED, NAME_WRONG_VERSION, CURRENCY_CHANGED,
                  WRONG_DRIVER_ROW, ENTITY_CHANGED, NAME_OTHER_TAB, VERSION_LABEL_CHANGED,
                  NAME_SHIFTED_UP]
# Part 2a: the reviewer's Phase 2 brief, section 3. Each is made from the full
# clean workbook and records it as its base.
FIXTURE_BOOKS_2A = [
    Book(FIXTURES / "renamed_feeds_subtotal.xlsx", fixture_renamed_feeds_subtotal,
         base=CLEAN_BASE,
         purpose=f"{CLEAN_NOTE}, with the ES Forecast Salaries line relabelled "
                 f"{RENAMED_SALARIES}, a label not on Lists. Salaries feeds EBIT. Every figure "
                 "is the clean workbook's.",
         expected="Structure: one medium break, the line item not on Lists, naming Salaries, the "
                  "account it displaced, and the block's subtotals it leaves unchecked (Gross "
                  "profit, EBIT); no subtotal break, and nothing else."),
    Book(FIXTURES / "duplicate_account.xlsx", fixture_duplicate_account, base=CLEAN_BASE,
         purpose=f"{CLEAN_NOTE}, with the ES Budget Materials label overwritten by Salaries, so "
                 "the block holds Salaries twice and no Materials. Every figure is the clean "
                 "workbook's.",
         expected="Structure: one medium break, the account twice in the block, naming both rows "
                  "and Materials, the account the second takes the place of, and the block's "
                  "subtotals it leaves unchecked; no accounts-not-in-block break, no subtotal "
                  "break, and nothing else."),
    Book(FIXTURES / "unit_column_empty.xlsx", fixture_unit_column_empty, base=CLEAN_BASE,
         purpose=f"{CLEAN_NOTE}, with the Unit column of the ES Budget block emptied on all "
                 "thirteen lines; the Unit header stays. Every figure is the clean workbook's.",
         expected="Structure: one medium break, the block without units, and no no-unit break "
                  "for any line."),
    Book(FIXTURES / "extra_version_block.xlsx", fixture_extra_version_block, base=CLEAN_BASE,
         purpose=f"{CLEAN_NOTE}, plus a block on Plan_ES one blank row below the Forecast block, "
                 f"labelled {EXTRA_VERSION}, with no defined name: a copy of the Forecast "
                 "block's labels and units, and formulas written for its own rows. They fetch "
                 f"their drivers by its label, {EXTRA_VERSION}, which Assumptions does not hold, "
                 "so every cell of the block computes 0. Every other figure is the clean "
                 "workbook's.",
         expected="Structure: one medium break, the block's version is not one of Actual, "
                  "Budget, Forecast, so it is landed and not built; nothing else."),
    Book(FIXTURES / "currency_lowercase.xlsx", _currency("eur"), base=CLEAN_BASE,
         purpose=f"{CLEAN_NOTE}, with Plan_AT's Currency cell, B3, reading eur. No formula reads "
                 "the cell, so every figure is the clean workbook's.",
         expected="No break: the currency is compared after trimming and ignoring case."),
    Book(FIXTURES / "currency_trailing_space.xlsx", _currency("EUR "), base=CLEAN_BASE,
         purpose=f"{CLEAN_NOTE}, with Plan_AT's Currency cell, B3, reading EUR followed by a "
                 "space. No formula reads the cell, so every figure is the clean workbook's.",
         expected="No break: the currency is compared after trimming and ignoring case."),
    Book(FIXTURES / "version_label_lowercase.xlsx", fixture_version_label_lowercase,
         base=CLEAN_BASE,
         purpose=f"{CLEAN_NOTE}, with the label above the ES Budget block's header row reading "
                 "budget. The Budget formulas fetch their drivers by that label, and the lookup "
                 "ignores case, so every figure is the clean workbook's.",
         expected="No break: the version label is compared with the defined name's version "
                  "after trimming and ignoring case, and its rows are read once."),
]
BASE = Book(FIXTURES / "shared_formula.xlsx", _simple(lambda: write_workbook(SMALL)), SMALL,
            purpose=f"{SMALL_NOTE}, recalculated, then the ES Budget Product sales months and "
                    "the ES Budget FY2026 column from Product sales to Volume stored as shared "
                    "formulas by editing the sheet's XML: the one hand-edited fixture.",
            expected="No break; openpyxl expands the shared formulas to the base's, cell for "
                     "cell.")
NO_CACHE = Book(FIXTURES / "no_cache.xlsx", _simple(lambda: write_workbook(SMALL)), SMALL,
                purpose=f"{SMALL_NOTE}, as openpyxl wrote it and never recalculated: no "
                        "formula cell has a cached value.",
                expected="Calculation state: one break for every formula cell.")
STEP_2 = [SAMPLE, *MUTATION_BOOKS, *FIXTURE_BOOKS, *FIXTURE_BOOKS_2A, BASE]
BOOKS = {book.dest.stem: book for book in (CLEAN, *STEP_2, NO_CACHE)}


# == Checking a recalculated workbook =================================================

def _plain(value):
    if isinstance(value, ArrayFormula):
        return ("array formula", value.ref, value.text)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    return value


def snapshot(path: Path) -> dict:
    """Everything the recalculation must keep: cells, formulas, names, sheets, hidden states."""
    wb = load_workbook(path)
    cells, names, rows, cols, merged = {}, {}, set(), set(), set()
    for name, defined in wb.defined_names.items():
        names[name] = defined.attr_text
    for ws in wb.worksheets:
        for name, defined in ws.defined_names.items():
            names[f"{ws.title}!{name}"] = defined.attr_text
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is not None:
                    cells[(ws.title, cell.coordinate)] = _plain(cell.value)
        rows |= {(ws.title, r) for r, d in ws.row_dimensions.items() if d.hidden}
        cols |= {(ws.title, c) for c, d in ws.column_dimensions.items() if d.hidden}
        merged |= {(ws.title, str(r)) for r in ws.merged_cells.ranges}
    return {"sheets": [(ws.title, ws.sheet_state) for ws in wb.worksheets],
            "names": names, "cells": cells, "hidden rows": rows, "hidden columns": cols,
            "merged": merged,
            "links": [link.file_link.Target for link in wb._external_links]}


def _is_formula(value) -> bool:
    return isinstance(value, tuple) or (isinstance(value, str) and value.startswith("="))


def compare(before: dict, after: dict) -> list[tuple]:
    """(what, where, before, after) for everything LibreOffice changed."""
    changes = []
    if before["sheets"] != after["sheets"]:
        changes.append(("sheets", "", before["sheets"], after["sheets"]))
    for name in sorted(set(before["names"]) | set(after["names"])):
        if before["names"].get(name) != after["names"].get(name):
            changes.append(("defined name", name, before["names"].get(name),
                            after["names"].get(name)))
    for key in sorted(set(before["cells"]) | set(after["cells"])):
        old, new = before["cells"].get(key), after["cells"].get(key)
        if old != new:
            kind = "formula" if _is_formula(old) or _is_formula(new) else "cell"
            changes.append((kind, f"{key[0]}!{key[1]}", old, new))
    for what in ("hidden rows", "hidden columns", "merged", "links"):
        if sorted(before[what]) != sorted(after[what]):
            changes.append((what, "", sorted(before[what]), sorted(after[what])))
    return changes


def expected_values(book: Book) -> dict:
    """(sheet, coordinate) -> the model's figure for every block cell and Control formula."""
    shape, expected = book.shape, {}
    for entity in shape.sheets:
        for version in model.VERSIONS:
            figures = model.block(entity, version, book.plants, book.tieouts)
            for line in model.LINES:
                for key in model.PERIODS + (model.FY,):
                    expected[(f"Plan_{entity}", coordinate(shape, version, line.name, key))] = \
                        figures[line.name][key]
    summary = model.group_summary(shape.sheets, book.plants, book.tieouts)
    top = shape.row0 + CONTROL_SUMMARY
    for i, line in enumerate(("Revenue", "Gross profit", "EBIT")):
        for j, version in enumerate(model.VERSIONS):
            expected[("Control", f"{col(shape, 2 + j)}{top + 2 + i}")] = summary[(line, version)]
    expected.update(book.specials)
    return expected


@dataclass
class Check:
    formula_cells: int = 0
    largest: Decimal = Decimal(0)
    problems: list = field(default_factory=list)


def check_values(path: Path, before: dict, expected: dict) -> Check:
    """Every formula cell's cached value against the model's figure."""
    cached = load_workbook(path, data_only=True)
    check = Check()
    for (sheet, ref), value in sorted(before["cells"].items()):
        if not _is_formula(value):
            continue
        check.formula_cells += 1
        if (sheet, ref) not in expected:
            check.problems.append(("no figure in the model for this formula", f"{sheet}!{ref}",
                                   value, None))
            continue
        want, got = expected[(sheet, ref)], cached[sheet][ref].value
        if got is None:
            check.problems.append(("no cached value", f"{sheet}!{ref}", want, None))
        elif isinstance(want, str) or isinstance(got, str):
            if got != want:
                check.problems.append(("cached value", f"{sheet}!{ref}", want, got))
        else:
            difference = abs(Decimal(str(got)) - want)
            check.largest = max(check.largest, difference)
            if difference:
                check.problems.append(("cached value", f"{sheet}!{ref}", want, got))
    return check


def check_book(book: Book, written: Path, output: Path) -> Check:
    before, after = snapshot(written), snapshot(output)
    check = check_values(output, before, expected_values(book))
    check.problems = compare(before, after) + check.problems
    return check


def report(book: Book, check: Check) -> None:
    print(f"  {book.rel}: {check.formula_cells} formula cells checked, largest absolute "
          f"difference {check.largest}; formulas, defined names, sheets and hidden states "
          f"{'unchanged' if not check.problems else 'CHANGED'}")
    for what, where, old, new in check.problems[:200]:
        print(f"    {what} {where}: before {old!r}, after {new!r}")
    if len(check.problems) > 200:
        print(f"    and {len(check.problems) - 200} more")


# == The shared formulas (the one hand-edited fixture) ==================================

def _sheet_part(archive: zipfile.ZipFile, title: str) -> str:
    """The XML part of a sheet, found through the workbook and its relationships."""
    workbook = archive.read("xl/workbook.xml").decode("utf-8")
    rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rid = re.search(rf'<sheet [^>]*name="{re.escape(title)}"[^>]*r:id="([^"]+)"', workbook)[1]
    target = re.search(rf'<Relationship [^>]*Id="{rid}"[^>]*Target="([^"]+)"', rels) or \
        re.search(rf'<Relationship [^>]*Target="([^"]+)"[^>]*Id="{rid}"', rels)
    target = target[1].lstrip("/")
    return target if target.startswith("xl/") else f"xl/{target}"


def share_formulas(xml: str, groups: list[list[str]]) -> str:
    """Stores each group of cells as one shared formula: the formula in the first, a reference in the rest."""
    for si, cells in enumerate(groups):
        for i, ref in enumerate(cells):
            pattern = re.compile(rf'(<c r="{ref}"[^>]*>)<f([^>]*)>([^<]*)</f>')
            match = pattern.search(xml)
            if not match:
                raise BuildError(f"shared_formula: no formula found at {ref}")
            if i == 0:
                new = f'{match[1]}<f t="shared" ref="{cells[0]}:{cells[-1]}" si="{si}">{match[3]}</f>'
            else:
                new = f'{match[1]}<f t="shared" si="{si}"/>'
            xml = xml[:match.start()] + new + xml[match.end():]
    return xml


def write_shared_formula(source: Path, target: Path) -> None:
    shape = SMALL
    months = [coordinate(shape, "Budget", "Product sales", p) for p in model.PERIODS]
    lines = [line.name for line in model.LINES if line.name != "Headcount"]
    totals = [coordinate(shape, "Budget", name, model.FY) for name in lines]
    with zipfile.ZipFile(source) as archive:
        part = _sheet_part(archive, "Plan_ES")
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    with zipfile.ZipFile(target, "w") as out:
        for info, data in entries:
            if info.filename == part:
                data = share_formulas(data.decode("utf-8"), [months, totals]).encode("utf-8")
            out.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)


# == The ledger and the manifest =======================================================

def write_ledger(path: Path) -> None:
    text = io.StringIO()
    text.write("entity,period,account,amount\n")
    for entity, period, code, amount in model.ledger_lines():
        text.write(f"{entity},{period},{code},{amount:.2f}\n")
    path.write_bytes(text.getvalue().encode("utf-8"))


def _money(value: Decimal) -> str:
    return f"{value:,.0f}"


def planted_truth() -> list[dict]:
    """The planted faults and tie-out breaks, with anchors, amounts and the control meant to find each."""
    shape, items = Shape(), []
    d1, d2, d3, d4, d5 = (model.PLANTS[k] for k in ("D1", "D2", "D3", "D4", "D5"))
    formula_d1 = model.block("AT", "Budget")["Marketing"]["2026-03"]
    december = model.block("DE", "Budget")["Salaries"]["2026-12"]
    d4_clean = model.block("ES", "Forecast")["Product sales"]["2026-09"]
    d4_planted = model.block("ES", "Forecast", {"D4"})["Product sales"]["2026-09"]
    codes = {line.name: line.code for line in model.LINES}
    items.append({
        "id": "D1", "fault": d1.fault, "sheet": d1.sheet, "block": d1.block, "entity": d1.entity,
        "line": d1.line, "account": codes[d1.line], "period": d1.period,
        "cell": coordinate(shape, d1.block, d1.line, d1.period),
        "typed": str(model.D1_TYPED), "formula_would_give": str(formula_d1),
        "control": "Formula integrity: a typed constant in a row that is otherwise formulas.",
        "why_only_this_control": "Labels, units and headers are unchanged (structure); every "
        "formula keeps its cached value (calculation state); the EBIT and FY2026 formulas use "
        "the typed figure, so totals recomputed from the extracted cells agree (totals); "
        "nothing is hidden or linked."})
    items.append({
        "id": "D2", "fault": d2.fault, "sheet": d2.sheet, "block": d2.block, "entity": d2.entity,
        "line": d2.line, "account": codes[d2.line], "period": d2.period,
        "cell": coordinate(shape, d2.block, d2.line, d2.period),
        "formula": total_formula(shape, d2.block, d2.line, last=model.D2_LAST),
        "range_stops_at": model.D2_LAST, "short_by": str(december),
        "control": f"Totals: the FY2026 cell is {_money(december)} short of the sum of its "
                   "twelve months.",
        "why_only_this_control": "The FY2026 column is not part of the month pattern and its "
        "formula holds no numeric constant (formula integrity); EBIT's FY2026 cell sums EBIT's "
        "own months, so no other total moves; nothing else changes."})
    items.append({
        "id": "D3", "fault": d3.fault, "sheet": d3.sheet, "entity": d3.entity,
        "row": shape.row0 + OVERRIDE_ROW, "label": model.D3_LABEL, "period": d3.period,
        "cell": f"{key_col(shape, d3.period)}{shape.row0 + OVERRIDE_ROW}",
        "typed": str(model.D3_VALUE),
        "control": "Hidden content and links: a hidden row.",
        "why_only_this_control": "The row is outside every block's defined name, so the "
        "extractor never reads it (structure, formula integrity, totals); it holds a typed "
        "number and no formula refers to it (calculation state)."})
    items.append({
        "id": "D4", "fault": d4.fault, "sheet": d4.sheet, "block": d4.block, "entity": d4.entity,
        "line": d4.line, "account": codes[d4.line], "period": d4.period,
        "cell": coordinate(shape, d4.block, d4.line, d4.period),
        "constant": f"*{model.D4_FACTOR}", "pattern_would_give": str(d4_clean),
        "planted_gives": str(d4_planted),
        "control": "Formula integrity: one break with two reasons, an embedded constant and a "
                   "difference from the pattern of its row.",
        "why_only_this_control": "The cells that depend on it (Marketing, Gross profit, EBIT "
        "and the FY2026 totals) keep their own patterns and use its cached value, so totals "
        "agree; nothing is hidden, linked or out of structure."})
    items.append({
        "id": "D5", "fault": d5.fault, "sheet": d5.sheet, "state": "hidden",
        "control": "Hidden content and links: a hidden sheet.",
        "why_only_this_control": "It is not a plan sheet (structure checks the required tabs, "
        "and a Plan_ sheet for each entity on Lists); it holds typed numbers only and nothing "
        "refers to it."})
    t1, t2, t3 = model.T1, model.T2, model.T3
    names = {line.code: line.name for line in model.LINES}
    ledger_t1 = model.block(t1.entity, "Actual")[names[t1.workbook_account]][t1.period]
    items.append({
        "id": "T1", "fault": t1.fault, "entity": t1.entity, "period": t1.period,
        "account": t1.workbook_account, "line": names[t1.workbook_account],
        "cell": f"Plan_{t1.entity}!{coordinate(shape, 'Actual', names[t1.workbook_account], t1.period)}",
        "amount": str(t1.amount), "workbook": str(ledger_t1 - t1.amount),
        "ledger": str(ledger_t1),
        "control": "Reconcile: one amount difference.",
        "why_only_this_control": "The Actual block's subtotals and FY2026 totals are formulas, "
        "so the workbook still agrees with itself: no spreadsheet control fires."})
    items.append({
        "id": "T2", "fault": t2.fault, "entity": t2.entity, "period": t2.period,
        "amount": str(t2.amount), "workbook_account": t2.workbook_account,
        "ledger_account": t2.ledger_account,
        "cells": [f"Plan_{t2.entity}!{coordinate(shape, 'Actual', names[c], t2.period)}"
                  for c in (t2.workbook_account, t2.ledger_account)],
        "control": "Reconcile: one possible reclassification (two equal and opposite "
                   "differences in one entity and month).",
        "why_only_this_control": "Both accounts are operating expenses, so the formula "
        "subtotals move together and the workbook agrees with itself."})
    items.append({
        "id": "T3", "fault": t3.fault, "entity": t3.entity, "period": t3.period,
        "column": f"Plan_{t3.entity}!{key_col(shape, t3.period)}",
        "control": "Reconcile: one missing entity-month, not one break per account.",
        "why_only_this_control": "The header for the month stays, so there is no gap in "
        "periods; the subtotal formulas give zero and the totals agree."})
    return items


def fixture_entry(book: Book) -> dict:
    """A fixture's manifest entry; a fixture of part 1e also records its base."""
    entry = {"file": book.rel, "purpose": book.purpose, "expected_in_1d": book.expected}
    if book.base:
        entry["base"] = book.base
    return entry


def render_manifest(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def _check_entry(check: "Check") -> dict:
    return {"formula_cells": check.formula_cells, "largest_difference": str(check.largest)}


def write_manifest(version: str, checks: dict, books: list[Book], timing: dict,
                   edits: list[dict]) -> None:
    files = {}
    for path in sorted([LEDGER] + [b.dest for b in books]):
        rel = path.relative_to(ROOT).as_posix()
        files[rel] = {"sha256": file_sha256(path), "bytes": path.stat().st_size}
    manifest = {
        "written_by": "scripts/build_sample.py",
        "company": model.COMPANY, "currency": model.CURRENCY,
        "fiscal_year": model.FISCAL_YEAR, "as_of": model.AS_OF,
        "libreoffice": version,
        "decisions": [
            "T1 to T3 are applied to the sample's Actual block before the recalculation, so "
            "LibreOffice computes every cached value, the Actual subtotals included; the "
            "ledger extract is written from the unmodified model in ledger sign (debit "
            "positive).",
            "The clean workbook carries none of the planted faults, T1 to T3 included: its "
            "Actual block matches the ledger exactly.",
        ],
        "planted": planted_truth(),
        "checks": {rel: _check_entry(c) for rel, c in checks.items()},
        "fixtures": [fixture_entry(b) for b in books if b.dest.parent != DATA],
        "timing_seconds": timing,
        "files": files,
        "hand_edited": edits,
    }
    MANIFEST.write_bytes(render_manifest(manifest).encode("utf-8"))


def select_books(names: list[str]) -> list[Book]:
    """The books a partial build makes, by file name without .xlsx, in the order named."""
    unknown = [n for n in names if n not in BOOKS]
    if unknown:
        raise BuildError(f"no book is called {', '.join(unknown)}; the books are "
                         f"{', '.join(BOOKS)}")
    return [BOOKS[n] for n in dict.fromkeys(names)]


def check_partial(manifest: dict, books: list[Book], version: str) -> None:
    """A partial build adds books with the LibreOffice the manifest records; it replaces none."""
    recorded = [b.rel for b in books if b.rel in manifest.get("files", {})]
    if recorded:
        raise BuildError(f"already built and recorded in the manifest: {', '.join(recorded)}; a "
                         "partial build adds books and never replaces one")
    if version != manifest.get("libreoffice"):
        raise BuildError(f"the recorded files were built with {manifest.get('libreoffice')} and "
                         f"this is {version}; a partial build needs the same LibreOffice")


def partial_manifest(manifest: dict, checks: dict, books: list[Book], files: dict,
                     edits: list[dict]) -> dict:
    """The manifest with the new books added: every recorded entry and field kept as it is."""
    new = dict(manifest)
    entries = dict(manifest["files"])
    for book in books:
        path = Path(files[book.rel])
        entries[book.rel] = {"sha256": file_sha256(path), "bytes": path.stat().st_size}
    new["files"] = dict(sorted(entries.items()))
    new["checks"] = {**manifest["checks"],
                     **{b.rel: _check_entry(checks[b.rel]) for b in books if b.rel in checks}}
    new["fixtures"] = list(manifest["fixtures"]) + [
        fixture_entry(b) for b in books if b.dest.parent != DATA]
    new["hand_edited"] = list(manifest.get("hand_edited", [])) + list(edits)
    return new


# == The build =========================================================================

def _save(book: Book, folder: Path) -> Path:
    wb, specials = book.make()
    book.specials = specials
    path = folder / book.name
    wb.save(path)
    return path


def _install(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)


def build(soffice: Path, version: str, work: Path, timeout: float,
          only: Optional[list[Book]] = None) -> dict:
    """The full build, or with only, the named books (the clean workbook is still checked first)."""
    chosen = None if only is None else {book.rel for book in only}

    def wanted(book: Book) -> bool:
        return chosen is None or book.rel in chosen

    checks, failed, edits = {}, [], []

    def checked(book, written, output):
        check = check_book(book, written, output)
        report(book, check)
        checks[book.rel] = check
        if check.problems:
            failed.append(book.rel)

    # 1. The clean workbook, alone, before anything else.
    print("Step 1: the clean workbook" + ("" if wanted(CLEAN) else " (checked, not installed)"))
    written = work / "written_1"
    written.mkdir()
    clean_in = _save(CLEAN, written)
    clean_out, = convert(soffice, [clean_in], work / "converted_1", timeout)
    checked(CLEAN, clean_in, clean_out)
    if failed:
        raise BuildError("the clean workbook changed in the recalculation; nothing was written")

    # 2. Everything else in one run; a hand edit is made, then checked.
    books = [book for book in STEP_2 if wanted(book)]
    results = {}
    if books:
        print("Step 2: " + ("the sample, the mutation variants and the fixtures" if only is None
                            else ", ".join(book.rel for book in books)))
        written = work / "written_2"
        written.mkdir()
        inputs = [_save(book, written) for book in books]
        outputs = convert(soffice, inputs, work / "converted_2", timeout)
        for book, book_in, book_out in zip(books, inputs, outputs):
            if book.edit is not None:
                edited = work / "edited" / book.name
                edited.parent.mkdir(exist_ok=True)
                record = book.edit(book_out, edited, work)
                edits.append({"file": book.rel, **record, "why": LINK_EDIT_WHY})
                print(f"  {book.rel}: hand edit of {record['part']}: {record['attribute']} "
                      f"{record['before']!r} became {record['after']!r}")
                book_out = edited
            results[book.rel] = (book_in, book_out)
            checked(book, book_in, book_out)
        if failed:
            raise BuildError(f"LibreOffice changed {len(failed)} workbook(s): "
                             f"{', '.join(failed)}; nothing was written")

    # 3. The hand-edited shared formulas and the workbook never recalculated.
    shared = work / "shared" / BASE.name
    no_cache = None
    if wanted(BASE) or wanted(NO_CACHE):
        print("Step 3: " + " and ".join(b.dest.stem for b in (BASE, NO_CACHE) if wanted(b)))
        shared.parent.mkdir()
    if wanted(BASE):
        base_in, base_out = results[BASE.rel]
        write_shared_formula(base_out, shared)
        check = check_values(shared, snapshot(base_in), expected_values(BASE))
        check.problems = compare(snapshot(base_in), snapshot(shared)) + check.problems
        report(BASE, check)
        checks[BASE.rel] = check
        with zipfile.ZipFile(shared) as archive:
            xml = archive.read(_sheet_part(archive, "Plan_ES")).decode("utf-8")
        if xml.count('t="shared"') != 24 or check.problems:
            raise BuildError("shared_formula: the shared formulas did not expand to the base")
    if wanted(NO_CACHE):
        no_cache = _save(NO_CACHE, work / "shared")
        cached = load_workbook(no_cache, data_only=True)
        count = 0
        for (sheet, ref), value in snapshot(no_cache)["cells"].items():
            if _is_formula(value):
                count += 1
                if cached[sheet][ref].value is not None:
                    raise BuildError(f"no_cache: {sheet}!{ref} has a cached value")
        print(f"  {NO_CACHE.rel}: {count} formula cells, none with a cached value")

    # 4. Install, then the ledger (full build only).
    print("Step 4: the files" + (", the ledger and the manifest" if only is None
                                 else " and the manifest's new entries"))
    installed = []
    if wanted(CLEAN):
        _install(clean_out, CLEAN.dest)
        installed.append(CLEAN)
    for book in books:
        if book is not BASE:
            _install(results[book.rel][1], book.dest)
            installed.append(book)
    if wanted(BASE):
        _install(shared, BASE.dest)
        installed.append(BASE)
    if no_cache is not None:
        _install(no_cache, NO_CACHE.dest)
        installed.append(NO_CACHE)
    if only is None:
        write_ledger(LEDGER)
    return {"checks": checks, "books": installed, "edits": edits}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the sample workbooks (needs LibreOffice).")
    parser.add_argument("--timeout", type=float, default=TIMEOUT,
                        help="seconds allowed for one LibreOffice run (default %(default)s)")
    parser.add_argument("--keep", action="store_true", help="keep the work folder")
    parser.add_argument("--only", nargs="+", metavar="NAME",
                        help="build only these books (file names without .xlsx) and add them to "
                             "the manifest; every other file and manifest entry stays as it is")
    return parser.parse_args(argv)


def _changed_files(manifest: dict) -> list[str]:
    return [rel for rel, entry in manifest["files"].items()
            if not (ROOT / rel).is_file() or file_sha256(ROOT / rel) != entry["sha256"]]


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    started = time.monotonic()
    SOFFICE_SECONDS.clear()
    before = soffice_bin_pids()
    work = Path(tempfile.mkdtemp(prefix="calder_build_"))
    status = 1
    try:
        soffice = find_soffice()
        if soffice is None:
            raise BuildError("LibreOffice was not found: set SOFFICE, put soffice on PATH, or "
                             f"install it at {DEFAULT_SOFFICE}")
        only = select_books(args.only) if args.only else None
        recorded = json.loads(MANIFEST.read_text(encoding="utf-8")) if only else None
        if only and _changed_files(recorded):
            raise BuildError("these files no longer match the manifest's hashes: "
                             f"{', '.join(_changed_files(recorded))}")
        version = soffice_version(soffice)
        print(f"LibreOffice: {version}")
        print(f"Launcher: {soffice}")
        if only:
            check_partial(recorded, only, version)
            print(f"Partial build: {', '.join(b.dest.stem for b in only)}; the "
                  f"{len(recorded['files'])} recorded files and their manifest entries stay as "
                  "they are")
        built = build(soffice, version, work, args.timeout, only)
        timing = {"total": round(time.monotonic() - started, 1),
                  "libreoffice": round(sum(SOFFICE_SECONDS), 1),
                  "libreoffice_runs": len(SOFFICE_SECONDS)}
        if only:
            manifest = partial_manifest(recorded, built["checks"], built["books"],
                                        {b.rel: b.dest for b in built["books"]}, built["edits"])
            MANIFEST.write_bytes(render_manifest(manifest).encode("utf-8"))
            changed = _changed_files(recorded)
            if changed:
                raise BuildError(f"recorded files changed during the build: {', '.join(changed)}")
            print(f"The {len(recorded['files'])} recorded files still match their hashes")
            print(f"Wrote {len(built['books'])} workbooks and the manifest's new entries in "
                  f"{MANIFEST.relative_to(ROOT).as_posix()}; the ledger was not written")
        else:
            write_manifest(version, {rel: c for rel, c in built["checks"].items()},
                           built["books"], timing, built["edits"])
            print(f"Wrote {len(built['books'])} workbooks, {LEDGER.relative_to(ROOT).as_posix()} "
                  f"and {MANIFEST.relative_to(ROOT).as_posix()}")
        print(f"Time: {timing['total']} s in all, of which LibreOffice {timing['libreoffice']} s "
              f"in {timing['libreoffice_runs']} runs")
        status = 0
    except BuildError as error:
        print(f"build failed: {error}", file=sys.stderr)
    finally:
        left = soffice_bin_pids() - before
        if left:
            print(f"soffice.bin still running after the build: {sorted(left)}; ending them")
            for pid in left:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)] if os.name == "nt"
                               else ["kill", "-9", str(pid)], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            status = 1
        else:
            print("No soffice.bin started by this build is still running.")
        if args.keep or status:
            print(f"Work folder kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
