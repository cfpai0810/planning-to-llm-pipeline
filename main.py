# =============================================================================
# main.py: the command line
# =============================================================================
"""
The pipeline's command line. Two commands:

    python main.py check <workbook> <ledger>

It runs the start-up build (src/startup.py) on the two files into a temporary
warehouse, which is deleted afterwards: the workbook and the ledger are read
and landed, the controls and reconcile run, and the pair's breaks are stored.
It prints each batch record's summary (names from the file shown as data,
JSON-encoded, external link targets included), then the breaks as a plain
table, and says how many there are. It never acknowledges a break and never
promotes. A file that is missing or cannot be read gives one plain line in
plain words (never the name of a Python class), no traceback, and the exit
code 1; a wrong command gives the usage and 2. With no arguments it prints
the usage and what exists so far, and exits 0.

    python main.py query <spec.json> --pair clean|sample
        --role group_controller|entity_controller [--entity ES]
        [--acknowledge-all-as "Name"] [--promote-as "Name"]

Part 2e (the reviewer's Phase 2 brief, section 7), a command to try the
governed layer with a specification written by hand. It builds the template
warehouse (both pairs, src/startup.py) in a temporary folder, opens a
session, optionally acknowledges every break of the pair (accepted) and
promotes it, each as the person named, asks the specification under the role
and prints the outcome's fixed sentence, or the answer: the SQL and its
hash, the figures (rounded once, where they are shown), the coverage and
gaps, and the provenance. A pair with breaks is refused for --promote-as
unless --acknowledge-all-as is given too. It writes nothing outside the
temporary folder, and removes it, the read-only template file included. A
wrong option is one plain line and the exit code 2; a spec file that is
missing or malformed, a refused promotion or a refused question is one plain
line and 1; an answer exits 0. No model is called.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Optional

USAGE = """Usage: python main.py check <workbook> <ledger>
       python main.py query <spec.json> --pair clean|sample --role group_controller|entity_controller [--entity ES] [--acknowledge-all-as "Name"] [--promote-as "Name"]

  check   Lands the workbook and the ledger in a temporary warehouse, runs the
          spreadsheet controls and reconcile, prints each batch record and every
          break, and deletes the warehouse. It never promotes.
  query   Builds the template warehouse in a temporary folder, may acknowledge
          every break of the pair and promote it as the people named, asks the
          specification under the role, prints the outcome or the answer (the
          SQL, the figures, the coverage and gaps, the provenance), and deletes
          the folder.

Phase 2: the sample workbook and its ledger extract, the extractor, the
spreadsheet controls, reconcile and promotion, the metric registry, and the
governed layer that answers a structured question exist.
No model is called yet."""

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
    except OSError:
        print("check: the check could not finish: a file could not be read or written.")
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


# == query (part 2e) ===================================================================================

PAIRS = ("clean", "sample")
ROLES = ("group_controller", "entity_controller")
OPTIONS = {"--pair": "pair", "--role": "role", "--entity": "entity",
           "--acknowledge-all-as": "acknowledge_as", "--promote-as": "promote_as"}
NAMES = {"value": "value", "comparator": "comparator", "variance": "variance",
         "variance_pct": "variance %"}


class Refused(Exception):
    """One plain line for the person, and the exit code."""

    def __init__(self, line: str, code: int):
        self.code = code
        super().__init__(line)


def remove_folder(folder: Path) -> None:
    """Removes a folder and everything in it, read-only files included: on Windows shutil
    cannot delete a read-only file, and with ignore_errors it would leave it silently."""
    for path in sorted(Path(folder).rglob("*"), reverse=True):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    shutil.rmtree(folder)


def _options(argv: list[str]) -> dict:
    """The spec file and the options, or Refused with a plain line and the exit code 2."""
    if not argv or argv[0].startswith("--"):
        raise Refused("query: give the spec file first, then --pair and --role.", 2)
    found: dict = {"spec": Path(argv[0])}
    rest = argv[1:]
    while rest:
        option = rest.pop(0)
        if option not in OPTIONS or not rest or OPTIONS[option] in found:
            raise Refused("query: an option was not understood; run python main.py for the "
                          "usage.", 2)
        found[OPTIONS[option]] = rest.pop(0)
    if found.get("pair") not in PAIRS:
        raise Refused("query: the pair must be clean or sample.", 2)
    if found.get("role") not in ROLES:
        raise Refused("query: the role must be group_controller or entity_controller.", 2)
    if found["role"] == "entity_controller" and "entity" not in found:
        raise Refused("query: an entity controller needs --entity.", 2)
    if found["role"] == "group_controller" and "entity" in found:
        raise Refused("query: --entity is for an entity controller alone.", 2)
    return found


def _specification(path: Path):
    from src import spec
    if not path.is_file():
        raise Refused(f"query: {path.name}: there is no such file.", 1)
    try:
        return spec.from_json(path.read_bytes())
    except spec.SpecificationError as refused:
        raise Refused(f"query: {path.name}: {refused.outcome.sentence}", 1) from None
    except OSError:
        raise Refused(f"query: {path.name}: the file could not be read.", 1) from None


def _runs(months) -> str:
    """Months as runs of consecutive months: 2026-01..2026-04 2026-06; none when empty."""
    runs: list = []
    for m in months:
        if runs and int(m[-2:]) == int(runs[-1][-1][-2:]) + 1 and m[:4] == runs[-1][-1][:4]:
            runs[-1].append(m)
        else:
            runs.append([m])
    return " ".join(r[0] if len(r) == 1 else f"{r[0]}..{r[-1]}" for r in runs) or "none"


def _period(period) -> str:
    return f"YTD {period.value}" if period.kind == "ytd" else period.value


def answer_lines(result, role) -> list[str]:
    """What query prints for an Answer."""
    from src import answer
    s = result.spec
    compared = "no comparison" if s.comparison == "none" else \
        f"against {result.coverage.compared_with}"
    lines = [f"Question: {s.metric}, {s.version}, {_period(s.period)}, {compared}; "
             f"{role.view()}",
             f"SQL (SHA-256 {result.sql_sha256}):", *result.sql.splitlines()]
    if result.outcome is not None:
        lines.append(f"No figures: {result.outcome.sentence}")
    else:
        more = "; more rows than the limit, left out" if result.truncated else ""
        lines.append(f"Figures ({result.row_count} row{'' if result.row_count == 1 else 's'}"
                     f"{more}):")
        for row in result.rows:
            parts, dims, label = [], [], ""
            for (name, kind, unit), cell in zip(result.columns, row):
                if kind == "dimension":
                    dims.append(f"{name} {cell}")
                elif kind == "label":
                    label = cell
                else:
                    parts.append(f"{NAMES[name]} {answer.shown(cell, unit)}")
            lines.append("  " + (", ".join(dims) + ": " if dims else "") + "; ".join(parts)
                         + (f"; {label}" if label else ""))
    held = f"Months asked: {_runs(result.coverage.months_asked)}; months held: " \
           f"{_runs(result.coverage.months_held)}"
    if result.coverage.compared_with:
        held += f"; compared with {result.coverage.compared_with}"
    lines.append(held)
    lines.append("Gaps: " + ("; ".join(f"{e} {m}" for e, m in result.gaps) or "none"))
    lines.append(f"Result SHA-256: {result.result_sha256}")
    p = result.provenance
    lines += ["Provenance:",
              f"  plan: {p['plan_file_name']} (batch {p['plan_batch_id']}, SHA-256 "
              f"{p['plan_file_sha256']})",
              f"  ledger: {p['ledger_file_name']} (batch {p['ledger_batch_id']}, SHA-256 "
              f"{p['ledger_file_sha256']})",
              f"  promotion {p['promotion_number']}, promoted by {p['promoted_by']} at "
              f"{p['promoted_at']}",
              "  breaks: " + ", ".join(f"{k} {v}" for k, v in p["breaks"].items())
              + f"; acknowledged {p['acknowledged']} (accepted {p['accepted']}, dismissed "
                f"{p['dismissed']}, investigated {p['investigated']})",
              f"  registry {p['registry_version']} (SHA-256 {p['registry_hash']}); engine "
              f"{p['engine']} {p['engine_version']}; {p['view']}"]
    return lines


def query(argv: list[str]) -> int:
    from src import outcomes, promotion, roles, session, startup, warehouse
    from src.batch import SourceError
    try:
        options = _options(argv)
        role = roles.group_controller if options["role"] == "group_controller" else \
            roles.entity_controller(options["entity"])
        question = _specification(options["spec"])
    except Refused as refused:
        print(refused)
        return refused.code
    except roles.RoleError as refused:
        print(f"query: {refused}")
        return 2
    folder = Path(tempfile.mkdtemp(prefix="p7_query_"))
    try:
        template = startup.build_template(folder / "template")
        pair = options["pair"]
        waiting = len(template.breaks[pair])
        if "promote_as" in options and waiting and "acknowledge_as" not in options:
            print(f"query: the {pair} pair has {waiting} break{'' if waiting == 1 else 's'}, "
                  "so --promote-as needs --acknowledge-all-as as well; nothing was promoted.")
            return 1
        pool = session.Sessions(template, root=folder / "sessions")
        try:
            s = pool.open()
            if "acknowledge_as" in options:
                s.acknowledge_many(pair, [(b.break_id, options["acknowledge_as"], "accepted")
                                          for b in s.breaks(pair)])
            if "promote_as" in options:
                s.promote(pair, options["promote_as"])
            result = s.answer(question, role)
        finally:
            pool.close_all()
    except promotion.PromotionError as refused:
        print(f"query: {refused}")
        return 1
    except (SourceError, warehouse.WarehouseError) as error:
        print(f"query: {error}")
        return 1
    except OSError:
        print("query: the query could not finish: a file could not be read or written.")
        return 1
    except Exception:              # the compiler refusing its own output, or a fault of mine
        print("query: the question could not be answered.")
        return 1
    finally:
        remove_folder(folder)
    if isinstance(result, outcomes.Outcome):
        print(f"query: {result.sentence}")
        return 1
    print("\n".join(answer_lines(result, role)))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(USAGE)
        return 0
    if argv[0] == "query":
        return query(argv[1:])
    if argv[0] != "check" or len(argv) != 3:
        print(USAGE)
        return 2
    return check(Path(argv[1]), Path(argv[2]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
