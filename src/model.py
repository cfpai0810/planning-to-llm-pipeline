# =============================================================================
# src/model.py: the star schema, built from plain SQL files
# =============================================================================
"""
Builds the star schema (brief 6.4) by running the plain SQL files in sql/ in
the order of their names, on a connection the caller opened with
warehouse.connect (this module never opens DuckDB itself):

- dim_version, dim_time (the FY2026 fiscal calendar), dim_entity and
  dim_account (section, sign convention, polarity, parent, unit, and the kind
  of line, flow or stock);
- fact_plan and fact_actual_erp, kept apart so the tie-out stays visible;
- then the indexes, each unique, once every table is loaded.

The facts come from the promoted pair only (src/promotion.py): fact_plan from
its plan batch, fact_actual_erp from its ledger batch, turned into
presentation sign. The dimensions come from the promoted plan batch's Lists.
At a fresh start nothing is promoted, so every table but dim_time is empty.
Members whose names fail the allow-list, and unknown entities and accounts,
are left out. Nothing here reads a fact about a file (raw_batch), and no
statement changes or removes a raw row: each table is made anew from the raw
tables, all in one transaction, so a failed build leaves the last one whole.

Two ways in (part 1e): build_model() opens and commits a transaction of its
own, for the start-up build; run_statements() runs the same statements inside
a transaction the caller has opened, so promote() can record a promotion and
rebuild the facts from it in one transaction (src/promotion.py). DuckDB rolls
back a CREATE OR REPLACE TABLE with the rest of its transaction.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL = ROOT / "sql"
STAR_TABLES = ("dim_version", "dim_time", "dim_entity", "dim_account", "fact_plan",
               "fact_actual_erp")


def sql_files() -> list[Path]:
    """The star schema's SQL files, in the order they run."""
    return sorted(SQL.glob("*.sql"))


def statements(path: Path) -> list[str]:
    """The statements of one SQL file, without their comment lines.

    The files hold no text literal with a semicolon in it, so a statement ends
    at each semicolon.
    """
    text = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("--"))
    return [s.strip() for s in text.split(";") if s.strip()]


def run_statements(con) -> None:
    """Runs every SQL file's statements in order, inside a transaction the caller opened."""
    for path in sql_files():
        for statement in statements(path):
            con.execute(statement)


def counts(con) -> dict:
    """{table: rows} of the star schema."""
    return {table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in STAR_TABLES}


def build_model(con) -> dict:
    """Runs every SQL file in order, in a transaction of its own; {table: rows}."""
    con.begin()
    try:
        run_statements(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return counts(con)
