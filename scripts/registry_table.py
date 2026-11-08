# =============================================================================
# scripts/registry_table.py: the metric table of docs/registry.md
# =============================================================================
"""
Writes the metric table of docs/registry.md from registry/metrics.toml, so
the table is generated and never typed (part 2e):

    python scripts/registry_table.py

The table sits between two markers in the page; the script rewrites what is
between them and nothing else. tests/test_docs.py fails when the committed
table differs from the one this script writes from the registry. It reads
the registry and writes the one page; it makes no network call.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import registry  # noqa: E402

DOC = ROOT / "docs" / "registry.md"
START = ("<!-- metric-table: written by scripts/registry_table.py from registry/metrics.toml; "
         "never typed -->")
END = "<!-- /metric-table -->"
POLARITY = {"favourable_when_higher": "higher is favourable",
            "favourable_when_lower": "lower is favourable", "ungraded": "ungraded"}


def _operand(operand) -> str:
    if isinstance(operand, str):
        return f"`{operand}`"
    if operand.account is not None:
        return f"account {operand.account}"
    return f"the accounts whose parent is {operand.parent}"


def _formula(formula) -> str:
    if isinstance(formula, registry.Sum):
        return _operand(formula)
    if isinstance(formula, registry.Difference):
        return " less ".join(_operand(m) for m in formula.metrics)
    return f"{_operand(formula.numerator)} over {_operand(formula.denominator)}"


def table(loaded: registry.Registry) -> str:
    """The metric table, one row per metric in the registry's order."""
    lines = ["| Metric | Label | Formula | Kind | Over time | Unit | Polarity | Grouped by | "
             "Filtered by | Compared with |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for m in loaded.metrics:
        lines.append(" | ".join([
            f"| `{m.id}`", m.label, _formula(m.formula), m.kind,
            m.time_aggregation or "its parts' own", m.unit, POLARITY[m.polarity],
            ", ".join(m.dimensions), ", ".join(m.filters), ", ".join(m.comparisons)]) + " |")
    lines.append("")
    lines.append(f"Registry version {loaded.version}, owned by {loaded.owner}.")
    return "\n".join(lines)


def block_of(text: str) -> str:
    """What a page holds between the table's two markers."""
    start, end = text.index(START) + len(START), text.index(END)
    return text[start:end].strip("\n")


def main(argv: Optional[list] = None) -> int:
    text = DOC.read_text(encoding="utf-8")
    start, end = text.index(START) + len(START), text.index(END)
    new = text[:start] + "\n" + table(registry.load()) + "\n" + text[end:]
    if new == text:
        print(f"{DOC.name}: the metric table is up to date; nothing written.")
        return 0
    DOC.write_text(new, encoding="utf-8", newline="\n")
    print(f"{DOC.name}: the metric table was rewritten from registry/metrics.toml.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
