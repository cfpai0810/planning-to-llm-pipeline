# =============================================================================
# src/registry.py: the metric registry, read and validated
# =============================================================================
"""
The finance-owned metric registry (brief 4.1 and 4.2): registry/metrics.toml,
read with the standard library's tomllib, validated, and given as frozen
dataclasses. Nothing here opens a database or reads a workbook.

The file holds a header (version, owner, unit system), a changelog (version,
date, change, approved_by) and one table per metric: id, label, definition,
kind (flow, stock, ratio), time_aggregation (sum for a flow, closing for a
stock; a ratio has none, because its parts carry theirs; average is reserved
and refused as not implemented), unit, polarity, dimensions, filters,
comparisons, owner and a formula. The formula is structured data, never a
query string:

- sum: the accounts on Lists with a parent, or one account by its code;
- difference: a list of metric ids, the first less the others;
- ratio: a numerator over a denominator, each a metric id or an inline sum
  (marketing_pct_of_revenue's numerator is account 6100, which is not a
  metric: the lean of part 2b, question 1 on the stop page).

At load: every field is checked against a closed schema; ids are unique and
in lower-case snake case; every metric a formula names exists; the formulas
form no cycle; a difference is of metrics with one unit, its own; a ratio
declares its unit; dimensions and filters come from the closed set entity,
region, account, month and quarter; comparisons from budget and forecast.
That every parent or account named is on the committed Lists, and that a
sum's units, kinds and polarity agree with the accounts it sums, are checked
by tests against the sample, never here.

A ratio's operands (part 2d1, the reviewer's verdict of 8 November, Q1 and
F18). A ratio with a stock operand is refused in v1, as average is: a sum
over the months against a closing value has no agreed meaning. A metric
operand is judged by its registry kind, at every load; a ratio of a ratio is
refused too. An inline sum is judged by the kind on Lists of the accounts it
sums, which the loader takes from the accounts it is given: plain data, each
account code with its parent and its kind (load(accounts=...)). Given them,
the loader refuses an inline sum that names no account or accounts of more
than one kind, and gives the inline Sum its kind (flow or stock). This module
still reads no workbook and opens no database: the tests pass the committed
Lists, and a session passes its promoted pair's dim_account.

The version lock. registry/registry.lock.json holds the registry version and
a SHA-256 of each metric's canonical definition: the JSON of its table with
sorted keys, separators "," and ":", ensure_ascii off, encoded in UTF-8.
lock_problems() says where a definition changed while the version stayed the
same; scripts/lock_registry.py rewrites the lock and refuses without a
version bump. The registry hash, the SHA-256 of the file's raw bytes (LF
endings), goes into every answer's provenance.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

__all__ = ["COMPARISONS", "DIMENSIONS", "FORMULAS", "KINDS", "LOCK", "POLARITIES", "REGISTRY",
           "RESERVED_AGGREGATIONS", "TIME_AGGREGATIONS", "Change", "Difference", "Metric",
           "Ratio", "Registry", "RegistryError", "Sum", "canonical", "definition_sha256",
           "from_data", "load", "lock_for", "lock_problems", "parse", "render_lock"]

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry" / "metrics.toml"
LOCK = ROOT / "registry" / "registry.lock.json"

KINDS = ("flow", "stock", "ratio")
TIME_AGGREGATIONS = ("sum", "closing")
RESERVED_AGGREGATIONS = ("average",)        # in the schema, and refused: not implemented
AGGREGATION_OF = {"flow": "sum", "stock": "closing"}
POLARITIES = ("favourable_when_higher", "favourable_when_lower", "ungraded")
DIMENSIONS = ("entity", "region", "account", "month", "quarter")
COMPARISONS = ("budget", "forecast")
FORMULAS = ("sum", "difference", "ratio")

TOP_FIELDS = ("version", "owner", "unit_system", "changelog", "metric")
CHANGE_FIELDS = ("version", "date", "change", "approved_by")
METRIC_FIELDS = ("id", "label", "definition", "kind", "time_aggregation", "unit", "polarity",
                 "dimensions", "filters", "comparisons", "owner", "formula")
OPTIONAL_METRIC_FIELDS = ("time_aggregation",)
ID = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")


class RegistryError(Exception):
    """A registry that cannot be used, told in one plain sentence."""


# == The dataclasses ===============================================================

@dataclass(frozen=True)
class Sum:
    """The accounts on Lists with this parent, or the one account with this code.

    kind is a ratio's inline operand's kind on Lists (flow or stock), set when the loader
    is given the accounts; None otherwise, and always None for a metric's own formula.
    """
    parent: Optional[str] = None
    account: Optional[str] = None
    kind: Optional[str] = None


@dataclass(frozen=True)
class Difference:
    """The first metric less the others."""
    metrics: tuple


@dataclass(frozen=True)
class Ratio:
    """A numerator over a denominator, each a metric id or an inline Sum, on the sums."""
    numerator: Union[str, Sum]
    denominator: Union[str, Sum]


@dataclass(frozen=True)
class Change:
    version: str
    date: date
    change: str
    approved_by: str


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    definition: str
    kind: str                         # flow, stock or ratio
    time_aggregation: Optional[str]   # sum or closing; None for a ratio
    unit: str
    polarity: str
    dimensions: tuple
    filters: tuple
    comparisons: tuple
    owner: str
    formula: Union[Sum, Difference, Ratio]


@dataclass(frozen=True)
class Registry:
    version: str
    owner: str
    unit_system: str
    changelog: tuple
    metrics: tuple
    sha256: str                       # of the file's raw bytes: the registry hash

    def ids(self) -> tuple:
        return tuple(m.id for m in self.metrics)

    def metric(self, metric_id: str) -> Metric:
        found = next((m for m in self.metrics if m.id == metric_id), None)
        if found is None:
            raise RegistryError("the registry has no metric of that id")
        return found


# == Reading ===========================================================================

def load(path: Path | str = REGISTRY, *, accounts: Optional[Mapping] = None) -> Registry:
    """The registry file, read and validated.

    accounts, when given, is plain data: {account code: (parent, kind)}, the kind Flow or
    Stock as Lists gives it (any case), the parent None for a line with none. The loader
    then checks each inline sum of a ratio against them and takes its kind from them.
    """
    return parse(Path(path).read_bytes(), accounts=accounts)


def parse(data: bytes, *, accounts: Optional[Mapping] = None) -> Registry:
    """A registry from the bytes of its file; its hash is the SHA-256 of those bytes."""
    try:
        tables = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise RegistryError(f"the registry is not valid TOML: {error}") from None
    return from_data(tables, sha256=hashlib.sha256(data).hexdigest(), accounts=accounts)


def _fields(table, allowed: tuple, required: tuple, what: str) -> None:
    if not isinstance(table, dict):
        raise RegistryError(f"{what} must be a table")
    for key in table:
        if key not in allowed:
            raise RegistryError(f"{what}: {key!r} is not a field of {what.split(':')[0]}")
    for key in required:
        if key not in table:
            raise RegistryError(f"{what}: the field {key!r} is missing")


def _text(table: dict, key: str, what: str) -> str:
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{what}: {key} must be text that is not empty")
    return value


def _one_of(value, allowed: tuple, what: str, field: str) -> str:
    if value not in allowed:
        raise RegistryError(f"{what}: the {field} {value!r} is not one of {', '.join(allowed)}")
    return value


def _closed_list(table: dict, key: str, allowed: tuple, what: str) -> tuple:
    values = table[key]
    if not isinstance(values, list):
        raise RegistryError(f"{what}: {key} must be a list")
    for value in values:
        _one_of(value, allowed, what, key.rstrip("s"))
    if len(set(values)) != len(values):
        raise RegistryError(f"{what}: {key} names a value more than once")
    return tuple(values)


def _sum(table: dict, what: str) -> Sum:
    _fields(table, ("type", "parent", "account"), ("type",), what)
    named = [k for k in ("parent", "account") if k in table]
    if len(named) != 1:
        raise RegistryError(f"{what}: a sum names exactly one of parent or account")
    return Sum(**{named[0]: _text(table, named[0], what)})


def _operand(value, what: str) -> Union[str, Sum]:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("type") == "sum":
        return _sum(value, f"{what}, an inline operand")
    raise RegistryError(f"{what}: an inline operand of a ratio must be a sum table, such as "
                        "{ type = \"sum\", account = \"6100\" }")


def _formula(value, what: str) -> Union[Sum, Difference, Ratio]:
    if not isinstance(value, dict):
        raise RegistryError(f"{what}: the formula must be structured data (a table), never a "
                            "string")
    kind = _one_of(value.get("type"), FORMULAS, what, "formula type")
    if kind == "sum":
        return _sum(value, what)
    if kind == "difference":
        _fields(value, ("type", "metrics"), ("type", "metrics"), what)
        metrics = value["metrics"]
        if not isinstance(metrics, list) or not all(isinstance(m, str) for m in metrics):
            raise RegistryError(f"{what}: a difference's metrics must be a list of metric ids")
        if len(metrics) < 2:
            raise RegistryError(f"{what}: a difference needs at least two metrics")
        return Difference(tuple(metrics))
    _fields(value, ("type", "numerator", "denominator"), ("type", "numerator", "denominator"),
            what)
    return Ratio(_operand(value["numerator"], what), _operand(value["denominator"], what))


def _metric(table, seen: set) -> Metric:
    what = f"metric {table.get('id', '(no id)')!r}" if isinstance(table, dict) else "a metric"
    _fields(table, METRIC_FIELDS, tuple(f for f in METRIC_FIELDS
                                        if f not in OPTIONAL_METRIC_FIELDS), what)
    metric_id = _text(table, "id", what)
    if not ID.match(metric_id):
        raise RegistryError(f"{what}: an id is lower-case snake case, such as gross_profit")
    if metric_id in seen:
        raise RegistryError(f"{what}: the id is used more than once")
    seen.add(metric_id)
    kind = _one_of(table["kind"], KINDS, what, "kind")
    aggregation = table.get("time_aggregation")
    if aggregation in RESERVED_AGGREGATIONS:
        raise RegistryError(f"{what}: the time aggregation {aggregation!r} is reserved and not "
                            "implemented")
    if kind == "ratio":
        if aggregation is not None:
            raise RegistryError(f"{what}: a ratio takes no time_aggregation; a ratio has none, "
                                "because its parts carry theirs")
    elif aggregation is None:
        raise RegistryError(f"{what}: a {kind} needs time_aggregation = "
                            f"{AGGREGATION_OF[kind]!r}")
    elif aggregation != AGGREGATION_OF[kind]:
        _one_of(aggregation, TIME_AGGREGATIONS, what, "time aggregation")
        raise RegistryError(f"{what}: a {kind}'s time_aggregation is {AGGREGATION_OF[kind]!r}; a "
                            "stock is never summed across months and takes its closing value")
    formula = _formula(table["formula"], what)
    if (kind == "ratio") != isinstance(formula, Ratio):
        raise RegistryError(f"{what}: only a ratio metric has a ratio formula, and a ratio "
                            "metric's formula is a ratio")
    return Metric(
        id=metric_id, label=_text(table, "label", what),
        definition=_text(table, "definition", what), kind=kind, time_aggregation=aggregation,
        unit=_text(table, "unit", what),
        polarity=_one_of(table["polarity"], POLARITIES, what, "polarity"),
        dimensions=_closed_list(table, "dimensions", DIMENSIONS, what),
        filters=_closed_list(table, "filters", DIMENSIONS, what),
        comparisons=_closed_list(table, "comparisons", COMPARISONS, what),
        owner=_text(table, "owner", what), formula=formula)


def _references(metric: Metric) -> list:
    if isinstance(metric.formula, Difference):
        return list(metric.formula.metrics)
    if isinstance(metric.formula, Ratio):
        return [o for o in (metric.formula.numerator, metric.formula.denominator)
                if isinstance(o, str)]
    return []


def _check_graph(metrics: tuple) -> None:
    """Every metric a formula names exists, there is no cycle, and a difference has one unit."""
    by_id = {m.id: m for m in metrics}
    for m in metrics:
        for ref in _references(m):
            if ref not in by_id:
                raise RegistryError(f"metric {m.id!r}: the formula names {ref!r}, which is not a "
                                    "metric of the registry")
    state: dict = {}

    def visit(metric_id: str, path: list) -> None:
        if state.get(metric_id) == "done":
            return
        if state.get(metric_id) == "open":
            cycle = path[path.index(metric_id):] + [metric_id]
            raise RegistryError(f"the formulas form a cycle: {', '.join(cycle)}")
        state[metric_id] = "open"
        for ref in _references(by_id[metric_id]):
            visit(ref, path + [metric_id])
        state[metric_id] = "done"

    for m in metrics:
        visit(m.id, [])
    for m in metrics:
        if isinstance(m.formula, Difference):
            units = [by_id[ref].unit for ref in m.formula.metrics]
            if set(units) != {m.unit}:
                raise RegistryError(f"metric {m.id!r}: a difference of metrics with different "
                                    f"units ({', '.join(dict.fromkeys(units + [m.unit]))})")
            kinds = {by_id[ref].kind for ref in m.formula.metrics}
            if kinds != {m.kind}:
                raise RegistryError(f"metric {m.id!r}: a difference of metrics of another kind")


NO_STOCK = ("a ratio with a stock operand is not in v1 (a sum over the months against a "
            "closing value has no agreed meaning)")


def _inline(operand: Sum) -> str:
    return (f"an inline sum of account {operand.account}" if operand.account is not None
            else f"an inline sum of parent {operand.parent}")


def _operand_kind(metric_id: str, side: str, operand: Sum, accounts: Mapping) -> Sum:
    """The inline sum with its kind on Lists; refused when it names no account, accounts of
    more than one kind, or a stock."""
    if operand.account is not None:
        kinds = [accounts[operand.account][1]] if operand.account in accounts else []
    else:
        kinds = [kind for code, (parent, kind) in accounts.items() if parent == operand.parent]
    what = f"metric {metric_id!r}: the {side}, {_inline(operand)},"
    if not kinds:
        raise RegistryError(f"{what} names no account on Lists")
    found = sorted({str(kind).strip().lower() for kind in kinds})
    if len(found) != 1:
        raise RegistryError(f"{what} sums accounts of more than one kind on Lists")
    if found[0] == "stock":
        raise RegistryError(f"{what} is a stock on Lists; {NO_STOCK}")
    return Sum(operand.parent, operand.account, found[0])


def _check_ratios(metrics: tuple, accounts: Optional[Mapping]) -> tuple:
    """Refuses a stock operand (F18) and a ratio of a ratio; with accounts, gives each inline
    sum its kind on Lists. The metrics, with their inline operands' kinds."""
    by_id = {m.id: m for m in metrics}
    checked = []
    for m in metrics:
        if not isinstance(m.formula, Ratio):
            checked.append(m)
            continue
        operands = []
        for side, operand in (("numerator", m.formula.numerator),
                              ("denominator", m.formula.denominator)):
            if isinstance(operand, str):
                kind = by_id[operand].kind
                if kind == "stock":
                    raise RegistryError(f"metric {m.id!r}: the {side} {operand!r} is a stock; "
                                        f"{NO_STOCK}")
                if kind == "ratio":
                    raise RegistryError(f"metric {m.id!r}: the {side} {operand!r} is a ratio; "
                                        "a ratio of a ratio is not in v1")
            elif accounts is not None:
                operand = _operand_kind(m.id, side, operand, accounts)
            operands.append(operand)
        checked.append(replace(m, formula=Ratio(*operands)))
    return tuple(checked)


def _change(table, number: int) -> Change:
    what = f"changelog entry {number}"
    _fields(table, CHANGE_FIELDS, CHANGE_FIELDS, what)
    version = _text(table, "version", what)
    if not VERSION.match(version):
        raise RegistryError(f"{what}: the version must be of the form 1.0.0")
    when = table["date"]
    if not isinstance(when, date) or isinstance(when, datetime):
        raise RegistryError(f"{what}: the date must be a TOML date, such as 2026-11-07")
    return Change(version, when, _text(table, "change", what), _text(table, "approved_by", what))


def from_data(data: dict, sha256: str, *, accounts: Optional[Mapping] = None) -> Registry:
    """A registry from its parsed tables, validated; sha256 is the file's hash; accounts as
    load() takes them."""
    _fields(data, TOP_FIELDS, TOP_FIELDS, "the registry")
    version = _text(data, "version", "the registry")
    if not VERSION.match(version):
        raise RegistryError("the registry: the version must be of the form 1.0.0")
    if not isinstance(data["changelog"], list) or not isinstance(data["metric"], list):
        raise RegistryError("the registry: changelog and metric must be arrays of tables")
    changes = tuple(_change(t, n) for n, t in enumerate(data["changelog"], start=1))
    seen: set = set()
    metrics = tuple(_metric(t, seen) for t in data["metric"])
    if not metrics:
        raise RegistryError("the registry: it defines no metric")
    _check_graph(metrics)
    metrics = _check_ratios(metrics, accounts)
    return Registry(version, _text(data, "owner", "the registry"),
                    _text(data, "unit_system", "the registry"), changes, metrics, sha256)


# == The version lock ======================================================================

def canonical(table: dict) -> str:
    """A metric's canonical definition: the JSON of its table, keys sorted, no spaces."""
    return json.dumps(table, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def definition_sha256(table: dict) -> str:
    return hashlib.sha256(canonical(table).encode("utf-8")).hexdigest()


def lock_for(data: bytes) -> dict:
    """The lock a registry file should have: its version and each definition's SHA-256."""
    registry = parse(data)
    tables = tomllib.loads(data.decode("utf-8"))["metric"]
    return {"registry_version": registry.version,
            "metrics": {t["id"]: definition_sha256(t)
                        for t in sorted(tables, key=lambda t: t["id"])}}


def render_lock(lock: dict) -> str:
    return json.dumps(lock, indent=2, ensure_ascii=False) + "\n"


def lock_problems(data: bytes, lock: dict) -> list:
    """What disagrees between a registry file and a lock; [] when the lock holds.

    A definition that changed, a metric added or a metric removed while the
    version stayed the lock's is a problem, and so is a version the lock has
    not seen: scripts/lock_registry.py rewrites the lock after a bump.
    """
    current = lock_for(data)
    version = current["registry_version"]
    if lock.get("registry_version") != version:
        return [f"the lock is for {lock.get('registry_version')} and the registry is {version}: "
                "run scripts/lock_registry.py"]
    problems = []
    old, new = lock.get("metrics", {}), current["metrics"]
    for metric_id in sorted(set(old) | set(new)):
        if metric_id not in old:
            problems.append(f"{metric_id}: a metric added while the version stayed {version}")
        elif metric_id not in new:
            problems.append(f"{metric_id}: a metric removed while the version stayed {version}")
        elif old[metric_id] != new[metric_id]:
            problems.append(f"{metric_id}: its definition changed while the version stayed "
                            f"{version}")
    return problems
