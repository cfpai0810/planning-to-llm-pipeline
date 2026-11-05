# =============================================================================
# main.py: the command line
# =============================================================================
"""
The pipeline's command line. Phase 1 has one command:

    python main.py check <workbook> <ledger>

It runs the start-up build (src/startup.py) on the two files into a temporary
warehouse, which is deleted afterwards: the workbook and the ledger are read
and landed, the controls and reconcile run, and the pair's breaks are stored.
It prints each batch record's summary (names from the file shown as data,
JSON-encoded, external link targets included), then the breaks as a plain
table, and says how many there are. It never acknowledges a break and never
promotes. A file that is missing or cannot be read gives one plain line, no
traceback, and the exit code 1; a wrong command gives the usage and 2. With
no arguments it prints the usage and what exists so far, and exits 0.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Optional

USAGE = """Usage: python main.py check <workbook> <ledger>

  check   Lands the workbook and the ledger in a temporary warehouse, runs the
          spreadsheet controls and reconcile, prints each batch record and every
          break, and deletes the warehouse. It never promotes.

Phase 1: the sample workbook and its ledger extract, the extractor, the
spreadsheet controls, reconcile and promotion exist.
Nothing answers a question yet."""

COLUMNS = (("id", 16), ("control", 18), ("kind", 26), ("severity", 9), ("where", 22))


def _data(text: Optional[str]) -> str:
    """A name or text from a file, shown as data: JSON-encoded, or none."""
    return "none" if text is None else json.dumps(text, ensure_ascii=False)


def batch_summary(batch) -> list[str]:
    """The lines that summarise one batch record."""
    label = {"workbook": "Workbook", "ledger": "Ledger"}.get(batch.source, "Source")
    lines = [f"{label}: {batch.file_name}",
             f"  batch id: {batch.batch_id}",
             f"  SHA-256: {batch.file_sha256}",
             f"  size: {batch.file_bytes:,} bytes"]
    if batch.source == "workbook":
        lines += [
            "  sheets: " + ", ".join(f"{_data(s.name)} ({s.visibility})" for s in batch.sheets),
            f"  creator: {_data(batch.creator)}",
            f"  last modified by: {_data(batch.last_modified_by)}",
            f"  created: {_data(batch.created)}",
            f"  modified: {_data(batch.modified)}",
            f"  application: {_data(batch.application)}"]
    lines.append(f"  calculation: {batch.calculation}")
    if batch.source == "workbook":
        links = json.dumps(list(batch.external_links), ensure_ascii=False)
        lines.append(f"  external links: {links if batch.external_links else 'none'}")
    return lines


def _where(b) -> str:
    if b.sheet and b.address:
        return f"{b.sheet}!{b.address}"
    if b.sheet:
        return b.sheet
    return " ".join(part for part in (b.entity, b.account, b.period) if part) or "the file"


def breaks_table(breaks) -> list[str]:
    """The breaks as a plain table: one line each, the message last and in full."""
    header = "  ".join(name.ljust(width) for name, width in COLUMNS) + "  message"
    rows = [header]
    for b in breaks:
        cells = (b.break_id, b.control, b.kind, b.severity, _where(b))
        rows.append("  ".join(str(cell).ljust(width) for cell, (_, width) in zip(cells, COLUMNS))
                    + "  " + b.message)
    return rows


def check(workbook: Path, ledger: Path) -> int:
    for path in (workbook, ledger):
        if not path.is_file():
            print(f"check: {path.name}: there is no such file.")
            return 1
    from src import startup, warehouse
    from src.batch import SourceError
    try:
        with tempfile.TemporaryDirectory(prefix="check_") as folder:
            built = startup.build_warehouse(Path(folder), workbook=workbook, ledger=ledger)
    except (SourceError, warehouse.WarehouseError) as error:
        print(f"check: {error}")
        return 1
    except OSError as error:
        print(f"check: the check could not finish ({type(error).__name__}).")
        return 1
    print("\n".join(report_lines(built)))
    return 0


def report_lines(built) -> list[str]:
    """What check prints for a start-up build: both batch records, the breaks, their count."""
    out = batch_summary(built.plan.batch)
    out += batch_summary(built.ledger.batch)
    out.insert(len(out) - 1, f"  lines: {len(built.ledger.lines)}")
    controls, reconciled = len(built.report.breaks()), len(built.reconciled.breaks)
    out.append("")
    if built.breaks:
        out += breaks_table(built.breaks)
    else:
        out.append("No break was found.")
    out += ["", f"{len(built.breaks)} break{'' if len(built.breaks) == 1 else 's'}: "
                f"{controls} from the controls, {reconciled} from reconcile.",
            "Nothing was promoted: the check never promotes."]
    return out


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(USAGE)
        return 0
    if argv[0] != "check" or len(argv) != 3:
        print(USAGE)
        return 2
    return check(Path(argv[1]), Path(argv[2]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
