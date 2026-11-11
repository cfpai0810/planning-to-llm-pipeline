# =============================================================================
# src/guard.py: every query is checked before it runs
# =============================================================================
"""
The guard (the reviewer's Phase 2 brief, 6.4): a second line behind the
compiler's fixed templates. The read-only connection, with external access
off, stops writes, files and settings; it does not stop reads; for reads the
controls are the compiler, which writes only fixed templates with canonical
members, and the guard (the reviewer's verdict of 11 November, F23). It
parses a query with sqlglot's DuckDB dialect, the one module that imports
sqlglot (tests/test_boundaries.py), and returns "ok" or one reason code,
checked in this order:

- parse_error: the text cannot be parsed;
- not_one_statement: it is not exactly one statement (a second one hidden
  behind a comment is a second one);
- not_select: the statement is not a SELECT or a UNION of SELECTs, or holds
  anything else anywhere (DDL, DML, COPY, ATTACH, PRAGMA, SET, DESCRIBE,
  SHOW, EXPLAIN, SUMMARIZE, PIVOT, a bare VALUES, INTERSECT, EXCEPT, INTO);
- comment: a comment anywhere (the compiler writes none, and a comment is
  where the parser and the engine could read the text differently);
- literal_not_plain: a literal that is not a plain string, number, boolean
  or NULL (a dollar-quoted string, a national string, a parameter);
- qualified_name: a schema- or catalog-qualified table or column;
- table_not_plain: a table that is not a plain identifier (a table function
  such as read_csv, glob, range, generate_series, duckdb_tables or
  query_table; UNNEST, LATERAL, VALUES, PIVOT; a quoted name, which is also
  how a file name such as 'plan.csv' is read);
- cte_shadows: a CTE named like a real relation of the warehouse, compared
  lower-cased;
- relation_not_allowed: a relation that is neither a CTE of the query nor in
  allowed_relations, compared lower-cased, so RAW_BATCH is raw_batch;
- function_not_allowed: a function the compiler never writes (current_setting,
  getenv, version, read_blob and every other);
- role_filter: given role_entities, fact_plan and dim_entity must each appear
  exactly once, each in a SELECT that reads that table alone and whose WHERE
  holds, among the conditions joined by AND, entity IN (...) for exactly the
  role's set.

Every check reaches every CTE body and subquery, a scalar subquery and an IN
subquery included: the guard walks the whole tree. CTE names are not
relations, but their bodies are checked like the rest.

What the guard cannot do: it reads the text as sqlglot reads it, and the
engine may read some text differently; the session therefore also asks the
engine's own parser that the text is one SELECT before it runs it
(warehouse.statement_types). The corpus of hostile statements
(tests/guard_corpus.txt) is a floor, not a proof.
"""

from __future__ import annotations

from typing import Iterable, Optional

import sqlglot
from sqlglot import exp

__all__ = ["KNOWN_RELATIONS", "OK", "REASONS", "check"]

OK = "ok"
REASONS = ("parse_error", "not_one_statement", "not_select", "comment", "literal_not_plain",
           "qualified_name", "table_not_plain", "cte_shadows", "relation_not_allowed",
           "function_not_allowed", "role_filter")

# Every table the warehouse holds: the raw tables, the gate's tables and the star schema
# (a test checks this list against src/warehouse.py, src/promotion.py and src/model.py).
KNOWN_RELATIONS = ("raw_batch", "raw_plan_cell", "raw_ledger_line", "raw_control_total",
                   "raw_list_account", "raw_list_entity", "gate_review", "gate_break",
                   "gate_acknowledgement", "gate_promotion", "dim_version", "dim_time",
                   "dim_entity", "dim_account", "fact_plan", "fact_actual_erp")

# The functions the compiler writes, by the parser's class name: nothing else may be called.
# (The parser counts AND and OR among its function classes; they are allowed as such.)
FUNCTIONS = frozenset({"Sum", "Count", "Min", "Max", "Abs", "Sign", "Coalesce", "Cast", "Case",
                       "If", "ArrayAgg", "Array", "Exists", "And", "Or"})

# The nodes a SELECT may never hold: statements of another kind, INTO, and set operations
# other than UNION. (A bare VALUES or PIVOT statement is not a SELECT; inside a SELECT they
# are tables that are not plain, below.)
NOT_SELECT = tuple(getattr(exp, name) for name in (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Copy", "Attach",
    "Detach", "Pragma", "Set", "Describe", "Show", "Command", "Summarize", "Use", "Into",
    "Intersect", "Except", "Transaction", "Commit", "Rollback", "Install", "LoadData", "Kill",
    "TruncateTable", "Grant", "Revoke", "Analyze") if hasattr(exp, name))
NOT_PLAIN_LITERALS = tuple(getattr(exp, name) for name in (
    "RawString", "National", "ByteString", "HexString", "BitString", "UnicodeString",
    "Placeholder", "Parameter", "SessionParameter") if hasattr(exp, name))
NOT_PLAIN_TABLES = tuple(getattr(exp, name) for name in (
    "Unnest", "Lateral", "Values", "Pivot", "TableSample", "TableFromRows")
    if hasattr(exp, name))
TABLE_NAME = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


def _is_select(node) -> bool:
    if isinstance(node, exp.Select):
        return True
    if isinstance(node, exp.Union):
        return _is_select(node.this) and _is_select(node.expression)
    if isinstance(node, exp.Subquery):
        return _is_select(node.this)
    return False


def _conjuncts(condition) -> list:
    """The conditions a WHERE joins by AND, at its top level."""
    while isinstance(condition, exp.Paren):
        condition = condition.this
    if isinstance(condition, exp.And):
        return _conjuncts(condition.this) + _conjuncts(condition.expression)
    return [condition]


def _filtered(table: exp.Table, entities: frozenset) -> bool:
    """The table is the only source of its SELECT, whose WHERE holds entity IN (the set)."""
    select = table.parent.parent if isinstance(table.parent, exp.From) else None
    if not isinstance(select, exp.Select) or select.args.get("joins"):
        return False
    where = select.args.get("where")
    if where is None:
        return False
    names = {table.name.lower(), (table.alias or "").lower()} - {""}
    for condition in _conjuncts(where.this):
        if not isinstance(condition, exp.In) or condition.args.get("query") is not None:
            continue
        column = condition.this
        if not isinstance(column, exp.Column) or column.name.lower() != "entity":
            continue
        if column.table and column.table.lower() not in names:
            continue
        values = condition.expressions
        if values and all(isinstance(v, exp.Literal) and v.is_string for v in values) and \
                {v.this for v in values} == entities:
            return True
    return False


def check(sql: str, *, allowed_relations: Iterable[str],
          role_entities: Optional[Iterable[str]] = None) -> str:
    """"ok", or the reason code of the first rule the query breaks (see the module's text)."""
    if not isinstance(sql, str):
        raise TypeError("the guard checks the text of a query")
    allowed = {name.lower() for name in allowed_relations}
    try:
        trees = sqlglot.parse(sql, read="duckdb")
    except Exception:               # sqlglot raises several kinds; any of them is a refusal
        return "parse_error"
    if len(trees) != 1 or trees[0] is None:
        return "not_one_statement"
    tree = trees[0]
    # One walk of the whole tree (every CTE body and subquery included); the rules below are
    # then checked in their order on what it found.
    nodes = list(tree.walk())
    tables = [n for n in nodes if isinstance(n, exp.Table)]
    if not _is_select(tree) or any(isinstance(n, NOT_SELECT) for n in nodes):
        return "not_select"
    if any(getattr(n, "comments", None) for n in nodes):
        return "comment"
    if any(isinstance(n, NOT_PLAIN_LITERALS) for n in nodes):
        return "literal_not_plain"
    if any(isinstance(n, (exp.Table, exp.Column)) and (n.args.get("db") or n.args.get("catalog"))
           for n in nodes):
        return "qualified_name"
    for t in tables:
        name = t.this
        if (not isinstance(name, exp.Identifier) or name.quoted or t.args.get("pivots")
                or not set(name.name.lower()) <= TABLE_NAME):
            return "table_not_plain"
    if any(isinstance(n, NOT_PLAIN_TABLES) for n in nodes):
        return "table_not_plain"
    ctes = {n.alias_or_name.lower() for n in nodes if isinstance(n, exp.CTE)}
    if ctes & (set(KNOWN_RELATIONS) | allowed):
        return "cte_shadows"
    if any(t.name.lower() not in ctes and t.name.lower() not in allowed for t in tables):
        return "relation_not_allowed"
    if any(isinstance(n, exp.Func) and type(n).__name__ not in FUNCTIONS for n in nodes):
        return "function_not_allowed"
    if role_entities is not None:
        entities = frozenset(role_entities)
        for relation in ("fact_plan", "dim_entity"):
            found = [t for t in tables if t.name.lower() == relation]
            if len(found) != 1 or not _filtered(found[0], entities):
                return "role_filter"
    return OK
