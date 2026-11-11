# =============================================================================
# src/compiler.py: a validated specification becomes one SELECT
# =============================================================================
"""
The compiler (the reviewer's Phase 2 brief, 6.3). compile(validated,
registry, role) emits one SELECT from the fixed templates below and the
registry's structured formulas, checks it with the guard, and returns a
CompiledQuery: the SQL, its SHA-256 and its columns.

What enters the SQL. Fixed text, numbers the compiler counts itself, and
names passed through quote(), the one quoting function (single quotes
doubled): the canonical members of the validated specification (the
version, the comparator, the filter values, the months of the window), the
role's entities, and the parents and account codes of the registry's
formulas. Nothing of a specification is concatenated as it was given: a
dimension, a sort or a comparison selects a fixed fragment.

The role is injected. The query starts with two CTEs: plan, the only
reference to fact_plan, filtered to the role's entities and the versions
asked for; and permitted, the only reference to dim_entity, filtered to the
same entities (a region grouping, the gaps and the coverage need entity
names and regions, and an unfiltered dim_entity would show another entity's).
Every later step reads plan and permitted, never the tables.

The role is checked first: anything that is not a Role of the two shapes
raises RoleError (roles.checked), however it was made.

The finance rules (brief 4.2, with the reviewer's rulings of 8 November):
- the accounts a metric reads are resolved in the SQL from dim_account, by
  parent or by code, never a subtotal (so another Lists still works); a
  difference or a ratio reads the accounts of all its parts. The CTE leaves
  holds them all, whatever the filter;
- an account filter chooses which of those accounts are summed, and nothing
  else (the reviewer's verdict of 11 November, F22): it makes a second CTE,
  chosen, the leaves it names, and only the sums read it. Held, the held
  range, the gaps and the months held are judged on leaves, so the slices of
  a metric by account add up to its total and a gap is a gap with or
  without the filter. A query with no account filter has no chosen CTE;
- held: an entity-month is held for the metric in a version when it has a
  figure for every account the metric reads; the held range is the first to
  the last month in which any entity in scope has any figure on a line that
  is not a subtotal; a gap is an entity-month in scope, in the window and in
  the range that is not held (with a comparison, not held in both versions:
  months are matched by entity-month and per metric);
- flows sum over the held entity-months of the window; a stock takes its
  closing month, the last month of the window (or of each month or quarter
  bucket) in which any entity in scope holds it, and sums the entities that
  hold that month; an entity that does not is left out and is a gap;
- a ratio is the ratio of the sums, through integer_route, the one helper
  that divides: the exact quotient rounded once to twelve places, half away
  from zero; a zero denominator is NULL, "not meaningful";
- variance is base less comparator (in percentage points for a percentage),
  the variance percentage divides by the absolute comparator through the
  same helper, and the label follows the metric's polarity.

Rows are grouped by the dimensions asked for, in the specification's sort or
in list order (entities, regions, accounts) and time order (months,
quarters), and LIMIT is the limit plus one, so the caller knows when rows
were left out. The coverage (the months held, the gaps) comes from the same
query: every row carries it, and a query with no row returns one row of
coverage alone. The same specification, registry and role give the same SQL
byte for byte: nothing iterates a set, and nothing depends on the time.

This module imports neither the engine nor the query parser: it reaches the
parser through src/guard.py (tests/test_boundaries.py).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from src import guard, roles
from src.registry import Difference, Ratio, Sum

__all__ = ["ALLOWED_RELATIONS", "PERMITTED", "PLAN", "CompileError", "CompiledQuery", "compile",
           "integer_route", "quote"]

ALLOWED_RELATIONS = ("fact_plan", "dim_version", "dim_time", "dim_entity", "dim_account")
SCALE = "1000000000000"                       # 10 to the 12: the money type's twelve places
COMPARATOR = {"budget": "Budget", "forecast": "Forecast"}

# The two templates the role is injected through.
PLAN = ("SELECT version, entity, account, period, amount FROM fact_plan WHERE entity IN "
        "({entities}) AND version IN ({versions})")
PERMITTED = "SELECT entity, region, list_order FROM dim_entity WHERE entity IN ({entities})"

# Each dimension: what the sums select, what they group by, the key the rows are ordered by,
# and the column the later steps carry.
DIMENSIONS = {
    "entity": ("c.entity AS entity, c.list_order AS entity_order", "c.entity, c.list_order",
               "entity_order", ("entity", "entity_order")),
    "region": ("c.region AS region, g.region_order AS region_order",
               "c.region, g.region_order", "region_order", ("region", "region_order")),
    "account": ("p.account AS account, f.list_order AS account_order",
                "p.account, f.list_order", "account_order", ("account", "account_order")),
    "month": ("c.period AS month", "c.period", "month", ("month",)),
    "quarter": ("c.quarter AS quarter", "c.quarter", "quarter", ("quarter",)),
}
LABELS = {
    "favourable_when_higher": ("favourable", "adverse"),
    "favourable_when_lower": ("adverse", "favourable"),
}


class CompileError(Exception):
    """A query the compiler will not hand over: its reason, and one plain sentence."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    sql_sha256: str
    columns: tuple          # (name, kind, unit) of each column of a row, in order


def quote(name: str) -> str:
    """A name as a SQL text literal: in single quotes, each single quote doubled."""
    if not isinstance(name, str):
        raise TypeError("only text is quoted")
    return "'" + name.replace("'", "''") + "'"


def _names(values) -> str:
    return ", ".join(quote(v) for v in values)


def integer_route(numerator: str, denominator: str) -> str:
    """The fragment that divides: numerator over denominator, both DECIMAL(38, 12), as the exact
    quotient rounded once to twelve places, half away from zero; NULL for a zero denominator.

    Both are scaled to HUGEINT by 10 to the 12; the quotient times 10 to the 12 is rounded by
    integer division of 2|n| times 10 to the 12 plus |d| by 2|d|, given the sign of n times d,
    and scaled back. DuckDB divides two DECIMAL(38, 12) values as a DOUBLE, so nothing else in
    a compiled query divides. A numerator of about 8.5 times 10 to the 13 or more overflows the
    scaling: the engine raises an error, never a wrong figure.
    """
    n = f"CAST(({numerator}) * {SCALE} AS HUGEINT)"
    d = f"CAST(({denominator}) * {SCALE} AS HUGEINT)"
    return (f"CASE WHEN ({denominator}) = 0 THEN NULL ELSE CAST(sign({n}) * sign({d}) * "
            f"((2 * abs({n}) * {SCALE} + abs({d})) // (2 * abs({d}))) AS DECIMAL(38, 0)) "
            "* CAST('0.000000000001' AS DECIMAL(13, 12)) END")


# == The formula as sums of accounts ===================================================================

def _terms(operand, registry, leaves: dict) -> list:
    """A flow or stock as signed sums of accounts: [(+1 or -1, leaf number)]. Each distinct sum
    (a parent or an account) is one leaf, numbered in the order it is met."""
    if isinstance(operand, str):
        return _terms(registry.metric(operand).formula, registry, leaves)
    if isinstance(operand, Sum):
        key = (operand.parent, operand.account)
        if key not in leaves:
            leaves[key] = len(leaves) + 1
        return [(1, leaves[key])]
    if isinstance(operand, Difference):
        first, *others = operand.metrics
        terms = _terms(first, registry, leaves)
        for other in others:
            terms += [(-sign, leaf) for sign, leaf in _terms(other, registry, leaves)]
        return terms
    raise CompileError("formula", "A ratio inside a ratio is not in v1.")


def _expression(terms: list, side: str) -> str:
    """The SQL of signed leaves for one version's side: b for the base, c for the comparator."""
    text = ""
    for i, (sign, leaf) in enumerate(terms):
        if i == 0:
            text = f"{'-' if sign < 0 else ''}{side}{leaf}"
        else:
            text += f" {'-' if sign < 0 else '+'} {side}{leaf}"
    return text


def _leaf_sql(number: int, key: tuple) -> str:
    """Every account of one leaf of the formula, whatever the filter."""
    parent, account = key
    where = f"parent = {quote(parent)}" if parent is not None else f"account = {quote(account)}"
    return f"SELECT {number} AS leaf, account, list_order FROM lines WHERE {where}"


# == Compiling ==========================================================================================

def compile(validated, registry, role) -> CompiledQuery:
    """One SELECT for the validated specification under the role, checked by the guard."""
    role = roles.checked(role)
    spec = validated.spec
    entities = tuple(validated.entities)
    if not entities or (role.entity is not None and entities != (role.entity,)):
        raise CompileError("role", "The role and the entities it may see disagree.")
    metric = registry.metric(spec.metric)
    comparator = COMPARATOR.get(spec.comparison)
    versions = (spec.version,) + ((comparator,) if comparator else ())
    filters = {f.dimension: f.values for f in spec.filters}
    dims = tuple(spec.dimensions)

    leaves: dict = {}
    if isinstance(metric.formula, Ratio):
        numerator = _terms(metric.formula.numerator, registry, leaves)
        denominator = _terms(metric.formula.denominator, registry, leaves)
    else:
        terms = _terms(metric.formula, registry, leaves)
    window = _names(validated.months)

    nv = len(versions)
    ctes = [("plan", PLAN.format(entities=_names(entities), versions=_names(versions))),
            ("permitted", PERMITTED.format(entities=_names(entities)))]
    narrowed = [f"{d} IN ({_names(filters[d])})" for d in ("entity", "region") if d in filters]
    scope = "permitted"
    if narrowed:
        scope = "scope"
        ctes.append(("scope", "SELECT entity, region, list_order FROM permitted WHERE "
                     + " AND ".join(narrowed)))
    ctes.append(("lines", "SELECT account, parent, list_order FROM dim_account WHERE "
                          "coalesce(section, '') <> 'Subtotal'"))
    ctes.append(("leaves", " UNION ALL ".join(
        _leaf_sql(number, key) for key, number in leaves.items())))
    # What is summed: every leaf, or with an account filter only the leaves it names.
    summed = "leaves"
    if "account" in filters:
        summed = "chosen"
        ctes.append(("chosen", "SELECT leaf, account, list_order FROM leaves WHERE account IN "
                               f"({_names(filters['account'])})"))
    # Each entity-month in scope with a figure on a line: in how many of the versions asked it
    # has any figure, and how many (version, account) figures it has of the accounts the
    # metric reads, beside how many those accounts are.
    ctes.append(("em", "SELECT p.entity, p.period, count(DISTINCT p.version) AS versions, "
                       "count(DISTINCT CASE WHEN f.account IS NOT NULL THEN p.version || ' ' || "
                       "p.account END) AS figures, max(n.needed) AS needed FROM plan AS p JOIN "
                       f"{scope} AS s ON s.entity = p.entity JOIN lines AS l ON l.account = "
                       "p.account LEFT JOIN (SELECT DISTINCT account FROM leaves) AS f ON "
                       "f.account = p.account CROSS JOIN (SELECT count(DISTINCT account) AS "
                       "needed FROM leaves) AS n GROUP BY p.entity, p.period"))
    # The held range: any figure, in every version asked (matched by entity-month).
    ctes.append(("bounds", "SELECT min(period) AS first_month, max(period) AS last_month FROM "
                           f"em WHERE versions = {nv}"))
    # Held for the metric, in every version asked, in the window.
    ctes.append(("cells", "SELECT e.entity, e.period, s.region, s.list_order, t.quarter FROM em "
                          f"AS e JOIN {scope} AS s ON s.entity = e.entity JOIN dim_time AS t ON "
                          f"t.period = e.period WHERE e.figures = {nv} * e.needed AND e.period "
                          f"IN ({window})"))
    if "region" in dims:
        ctes.append(("regions", "SELECT region, min(list_order) AS region_order FROM "
                                f"{scope} GROUP BY region"))
    source = "cells"
    if metric.kind == "stock" and "month" not in dims:
        # The closing month: the window's, or each quarter's.
        if "quarter" in dims:
            closing = ("(SELECT quarter, max(period) AS period FROM cells GROUP BY quarter) AS k "
                       "ON k.period = c.period AND k.quarter = c.quarter")
        else:
            closing = "(SELECT max(period) AS period FROM cells) AS k ON k.period = c.period"
        ctes.append(("closed", "SELECT c.entity, c.period, c.region, c.list_order, c.quarter "
                               f"FROM cells AS c JOIN {closing}"))
        source = "closed"
    sides = (("b", spec.version),) + ((("c", comparator),) if comparator else ())
    sums = ", ".join(f"coalesce(sum(p.amount) FILTER (WHERE p.version = {quote(version)} AND "
                     f"f.leaf = {number}), 0) AS {side}{number}"
                     for side, version in sides for number in leaves.values())
    selects = ", ".join(DIMENSIONS[d][0] for d in dims)
    groups = ", ".join(DIMENSIONS[d][1] for d in dims)
    carried = ", ".join(c for d in dims for c in DIMENSIONS[d][3])
    ctes.append(("sums", f"SELECT {selects + ', ' if selects else ''}{sums} FROM {source} AS c "
                         "JOIN plan AS p ON p.entity = c.entity AND p.period = c.period JOIN "
                         f"{summed} AS f ON f.account = p.account"
                         + (" JOIN regions AS g ON g.region = c.region" if "region" in dims
                            else "")
                         + (f" GROUP BY {groups}" if groups else "") + " HAVING count(*) > 0"))
    lead = carried + ", " if carried else ""
    if isinstance(metric.formula, Ratio):
        values = [integer_route(_expression(numerator, side), _expression(denominator, side))
                  for side, _ in sides]
    else:
        values = [_expression(terms, side) for side, _ in sides]
    ctes.append(("vals", f"SELECT {lead}" + ", ".join(
        f"{v} AS {name}" for v, name in zip(values, ("value", "comparator"))) + " FROM sums"))
    figures = ["value"]
    columns = [(d, "dimension", None) for d in dims] + [("value", metric.kind, metric.unit)]
    if comparator:
        difference = "value - comparator"
        variance = f"({difference}) * 100" if metric.unit == "%" else difference
        figures += ["comparator", f"{variance} AS variance"]
        columns += [("comparator", metric.kind, metric.unit),
                    ("variance", metric.kind, "pp" if metric.unit == "%" else metric.unit)]
        if metric.kind != "ratio":
            figures.append(f"{integer_route(difference, 'abs(comparator)')} AS variance_pct")
            columns.append(("variance_pct", "ratio", "%"))
        if metric.polarity in LABELS:
            up, down = LABELS[metric.polarity]
            label = (f"CASE WHEN {difference} > 0 THEN '{up}' WHEN {difference} < 0 THEN "
                     f"'{down}' WHEN {difference} = 0 THEN 'no variance' ELSE '' END")
        else:
            label = "''"
        figures.append(f"{label} AS label")
        columns.append(("label", "label", None))
    ctes.append(("answer", f"SELECT {lead}{', '.join(figures)}, TRUE AS found FROM vals"))
    # A gap: an entity-month in scope, in the window and in the held range, not held.
    ctes.append(("gaps", f"SELECT s.entity, t.period, s.list_order FROM {scope} AS s JOIN "
                         f"dim_time AS t ON t.period IN ({window}) JOIN bounds AS b ON t.period "
                         "BETWEEN b.first_month AND b.last_month LEFT JOIN cells AS m ON "
                         "m.entity = s.entity AND m.period = t.period WHERE m.entity IS NULL"))
    shown = [f"a.{name}" for name, _, _ in columns]
    order = _order(spec, dims)
    sql = ("WITH " + ",\n".join(f"{name} AS ({body})" for name, body in ctes)
           + f"\nSELECT {', '.join(shown)}, a.found, (SELECT list(DISTINCT period ORDER BY "
             "period) FROM cells) AS months_held, (SELECT list([entity, period] ORDER BY period, "
             "list_order) FROM gaps) AS gaps FROM bounds AS b LEFT JOIN answer AS a ON TRUE"
           + (f" ORDER BY {order}" if order else "") + f" LIMIT {spec.limit + 1}")
    reason = guard.check(sql, allowed_relations=ALLOWED_RELATIONS, role_entities=entities)
    if reason != guard.OK:
        raise CompileError(reason, "The compiled query did not pass the guard; it was not run.")
    return CompiledQuery(sql, hashlib.sha256(sql.encode("utf-8")).hexdigest(), tuple(columns))


def _order(spec, dims: tuple) -> str:
    """The specification's sort first, then each dimension in list or time order."""
    keys = [f"a.{DIMENSIONS[d][2]} ASC" for d in dims]
    if spec.sort is None:
        return ", ".join(keys)
    direction = "DESC" if spec.sort.direction == "desc" else "ASC"
    if spec.sort.by == "value":
        first = f"a.value {direction} NULLS LAST"
    else:
        first = f"a.{DIMENSIONS[spec.sort.by][2]} {direction}"
        keys = [k for d, k in zip(dims, keys) if d != spec.sort.by]
    return ", ".join([first] + keys)
