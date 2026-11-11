# =============================================================================
# src/spec.py: the specification and its validation
# =============================================================================
"""
The specification a model will one day produce (the reviewer's Phase 2
brief, 6.1), and its validation.

A Specification is a frozen dataclass of eight fields, every one required,
with no default in Python: metric, version, dimensions (what the rows are
grouped by), filters (each a dimension and its values), period (a kind and
a value), comparison (none, budget, forecast or prior_year), sort (None, or
by value or a grouped dimension, ascending or descending) and limit (the
most rows). from_json takes a JSON text, its bytes or a mapping, with
exactly these keys and no others, and values of the right JSON types; any
other input is the outcome specification_invalid, which never echoes what
it was given. The JSON schema the model sees is Phase 3's.

The period's values: a month as 2026-06; a quarter as Q2; the year to date
through a month as that month, 2026-06; the fiscal year as FY2026.

validate(spec, registry, session, role) returns a Validated: the
specification with every member replaced by its canonical name from the
promoted pair's dimension tables (matched without regard to case, a member
by its code or its name), the role's entities, and the months of the
window. Or it returns a typed refusal (or, for a role of the wrong shape,
raises), checked in this order:

1. the role: a role that is not a Role of the two shapes raises RoleError
   (roles.checked), a programming fault and never an outcome, since a role
   is built by the page's own code, never from a visitor's words;
2. the types, by the rules from_json enforces (specification_invalid): a
   Specification built by hand is turned back into the JSON data it stands
   for (each of its dataclasses a mapping, each tuple a list, anything else
   as it is) and read by from_json, so a field of the wrong type is refused,
   never raised, and a limit of True is not a limit of 1; the rest of
   validation reads what from_json gave back;
3. nothing promoted (nothing_promoted); then an entity controller whose
   entity the promoted pair does not hold (entity_not_permitted), before
   anything else, so no query is ever built with an empty entity list;
4. the metric is in the registry (unknown_metric);
5. the version is a member of dim_version (unknown_member);
6. each dimension is one the metric may be grouped by, named once
   (dimension_not_allowed);
7. each filter is on a dimension the metric may be filtered by, named once
   (filter_not_allowed); then each value. An entity or region value is
   matched against the entities the role may see and their regions alone:
   for an entity controller, its own entity and its own regions (a shared
   region, Central Europe for AT, is its own), and any other value, another
   entity's code or name, a region only other entities hold, or a name that
   is no member at all, real or made up, is entity_not_permitted, one and
   the same outcome, so it never tells whether a code exists (the
   reviewer's verdict of 11 November, D3); for a group controller a name
   that is no member is unknown_member. An account value that is no member
   is unknown_member for every role; an account the metric does not sum, a
   subtotal or a statistic among them, is filter_not_allowed, because the
   member exists and every role's member lists show it;
8. the comparison: prior_year is prior_year_not_available; any other must be
   one the metric allows and not the version itself
   (comparison_not_allowed);
9. the period is a month, quarter, year to date or fiscal year of FY2026
   (period_invalid);
10. the limit is between 1 and 100 (limit_invalid);
11. the sort is by value or by a grouped dimension, asc or desc
    (sort_invalid).

Words of the schema (a dimension, a filter's dimension, the comparison, the
period's kind, the sort's field and direction) are matched exactly: they
are fixed words, never members. The metric id is matched without regard to
case. An unknown metric or member is refused, never guessed.

This module imports neither the database engine nor the query parser
(tests/test_boundaries.py): the session reads the dimension tables for it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Union

from src import outcomes, roles
from src.outcomes import Outcome

__all__ = ["COMPARISONS", "KEYS", "MAX_LIMIT", "PERIOD_KINDS", "Dimensions", "Filter", "Period",
           "Sort", "Specification", "SpecificationError", "Validated", "from_json", "validate"]

KEYS = ("metric", "version", "dimensions", "filters", "period", "comparison", "sort", "limit")
PERIOD_KINDS = ("month", "quarter", "ytd", "fy")
COMPARISONS = ("none", "budget", "forecast", "prior_year")
DIRECTIONS = ("asc", "desc")
MAX_LIMIT = 100


@dataclass(frozen=True)
class Filter:
    dimension: str
    values: tuple


@dataclass(frozen=True)
class Period:
    kind: str           # month, quarter, ytd or fy
    value: str          # 2026-06, Q2, 2026-06 or FY2026


@dataclass(frozen=True)
class Sort:
    by: str             # value, or a grouped dimension
    direction: str      # asc or desc


@dataclass(frozen=True)
class Specification:
    metric: str
    version: str
    dimensions: tuple
    filters: tuple
    period: Period
    comparison: str
    sort: Optional[Sort]
    limit: int


@dataclass(frozen=True)
class Validated:
    """A specification with canonical names, the role's entities and the window's months."""
    spec: Specification
    entities: tuple     # the entities the role may see, in list order: injected in the query
    months: tuple       # the months of the window, in order


@dataclass(frozen=True)
class Dimensions:
    """The promoted pair's members, as a session reads them from the dimension tables."""
    versions: tuple     # in version order
    entities: tuple     # (code, name, region), in list order
    accounts: tuple     # (code, name, parent, section, kind), in list order
    periods: tuple      # (period, quarter, fiscal year), in time order


class SpecificationError(ValueError):
    """A text that is not a specification; its outcome is specification_invalid."""

    def __init__(self):
        self.outcome = outcomes.outcome("specification_invalid")
        super().__init__(self.outcome.sentence)


# == Reading a specification =======================================================================

def _keys(value, keys: tuple) -> dict:
    if not isinstance(value, dict) or tuple(sorted(value)) != tuple(sorted(keys)):
        raise SpecificationError()
    return value


def _text(value) -> str:
    if not isinstance(value, str):
        raise SpecificationError()
    return value


def _texts(value, *, empty: bool) -> tuple:
    if not isinstance(value, list) or (not empty and not value):
        raise SpecificationError()
    return tuple(_text(v) for v in value)


def from_json(given: Union[str, bytes, dict]) -> Specification:
    """A Specification from a JSON text, its bytes or a mapping: exactly the eight keys."""
    if isinstance(given, (str, bytes, bytearray)):
        try:
            given = json.loads(given)
        except (ValueError, UnicodeDecodeError):
            raise SpecificationError() from None
    data = _keys(given, KEYS)
    if not isinstance(data["filters"], list):
        raise SpecificationError()
    filters = tuple(Filter(_text(f["dimension"]), _texts(f["values"], empty=False))
                    for f in (_keys(f, ("dimension", "values")) for f in data["filters"]))
    period = _keys(data["period"], ("kind", "value"))
    sort = data["sort"]
    if sort is not None:
        sort = _keys(sort, ("by", "direction"))
        sort = Sort(_text(sort["by"]), _text(sort["direction"]))
    limit = data["limit"]
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise SpecificationError()
    return Specification(metric=_text(data["metric"]), version=_text(data["version"]),
                         dimensions=_texts(data["dimensions"], empty=True), filters=filters,
                         period=Period(_text(period["kind"]), _text(period["value"])),
                         comparison=_text(data["comparison"]), sort=sort, limit=limit)


def _as_data(given) -> dict:
    """A hand-built Specification as the JSON data from_json reads: each of its dataclasses a
    mapping, each tuple a list, anything else as it is, for from_json to judge. Anything that
    is not a Specification is refused."""
    if type(given) is not Specification:
        raise SpecificationError()

    def listed(value):
        return list(value) if isinstance(value, tuple) else value

    def mapping(value, kind, names: tuple):
        return {n: listed(getattr(value, n)) for n in names} if isinstance(value, kind) else value

    filters = listed(given.filters)
    if isinstance(filters, list):
        filters = [mapping(f, Filter, ("dimension", "values")) for f in filters]
    return {"metric": given.metric, "version": given.version,
            "dimensions": listed(given.dimensions), "filters": filters,
            "period": mapping(given.period, Period, ("kind", "value")),
            "comparison": given.comparison, "sort": mapping(given.sort, Sort, ("by", "direction")),
            "limit": given.limit}


# == Validation ======================================================================================

def _match(value: str, candidates) -> Optional[str]:
    """The canonical member whose code (or name) equals value without regard to case."""
    folded = value.casefold()
    for canonical, *names in candidates:
        if any(isinstance(n, str) and n.casefold() == folded for n in (canonical, *names)):
            return canonical
    return None


def _summed(metric, dims: Dimensions) -> tuple:
    """The accounts a sum metric reads, by its parent or its code, never a subtotal."""
    formula = metric.formula
    return tuple(a[0] for a in dims.accounts if a[3] != "Subtotal" and (
        (formula.parent is not None and a[2] == formula.parent)
        or (formula.account is not None and a[0] == formula.account)))


def _window(period: Period, dims: Dimensions) -> Optional[tuple]:
    """(the canonical period, its months), or None when it is not a period of the year."""
    months = tuple(p for p, _, _ in dims.periods)
    years = sorted({f"FY{year}" for _, _, year in dims.periods})
    if period.kind in ("month", "ytd"):
        if period.value not in months:
            return None
        window = (period.value,) if period.kind == "month" else months[
            :months.index(period.value) + 1]
        return Period(period.kind, period.value), window
    if period.kind == "quarter":
        quarter = period.value.upper()
        window = tuple(p for p, q, _ in dims.periods if q == quarter)
        return (Period("quarter", quarter), window) if window else None
    if period.kind == "fy":
        year = period.value.upper()
        return (Period("fy", year), months) if [year] == years else None
    return None


def validate(spec: Specification, registry, session, role) -> Union[Validated, Outcome]:
    """The specification with canonical names, or a typed refusal (in the order above); a role
    of the wrong shape raises RoleError."""
    role = roles.checked(role)
    try:
        spec = from_json(_as_data(spec))
    except SpecificationError as refused:
        return refused.outcome
    dims = session.dimensions()
    if isinstance(dims, Outcome):
        return dims
    codes = tuple(e[0] for e in dims.entities)
    if role.entity is not None and role.entity not in codes:
        return outcomes.outcome("entity_not_permitted")
    permitted = codes if role.entity is None else (role.entity,)
    entity_view = role.name == roles.ENTITY_CONTROLLER
    if not permitted:
        return outcomes.NO_DATA
    metric_id = _match(spec.metric, ((m,) for m in registry.ids()))
    if metric_id is None:
        return outcomes.outcome("unknown_metric")
    metric = registry.metric(metric_id)
    version = _match(spec.version, ((v,) for v in dims.versions))
    if version is None:
        return outcomes.outcome("unknown_member")
    if (any(d not in metric.dimensions for d in spec.dimensions)
            or len(set(spec.dimensions)) != len(spec.dimensions)):
        return outcomes.outcome("dimension_not_allowed")
    filters = []
    for f in spec.filters:
        if f.dimension not in metric.filters or f.dimension in [g.dimension for g in filters]:
            return outcomes.outcome("filter_not_allowed")
        values = []
        for value in f.values:
            found = _value(f.dimension, value, metric, dims, permitted, entity_view)
            if isinstance(found, Outcome):
                return found
            if found not in values:
                values.append(found)
        filters.append(Filter(f.dimension, tuple(values)))
    if spec.comparison == "prior_year":
        return outcomes.outcome("prior_year_not_available")
    if spec.comparison != "none":
        # The registry's comparisons are budget and forecast: each names a version.
        comparator = _match(spec.comparison, ((v,) for v in dims.versions))
        if (spec.comparison not in metric.comparisons or comparator is None
                or comparator == version):
            return outcomes.outcome("comparison_not_allowed")
    window = _window(spec.period, dims)
    if window is None:
        return outcomes.outcome("period_invalid")
    if not 1 <= spec.limit <= MAX_LIMIT:
        return outcomes.outcome("limit_invalid")
    if spec.sort is not None and (spec.sort.direction not in DIRECTIONS or spec.sort.by not in
                                  ("value",) + spec.dimensions):
        return outcomes.outcome("sort_invalid")
    canonical = Specification(metric=metric_id, version=version, dimensions=spec.dimensions,
                              filters=tuple(filters), period=window[0],
                              comparison=spec.comparison, sort=spec.sort, limit=spec.limit)
    return Validated(spec=canonical, entities=permitted, months=window[1])


def _value(dimension: str, value: str, metric, dims: Dimensions, permitted: tuple,
           entity_view: bool) -> Union[str, Outcome]:
    """A filter value's canonical member, or why it is refused. An entity or region value is
    matched against the permitted entities and their regions alone; for an entity controller
    (entity_view) anything else, real or made up, is entity_not_permitted, and for a group
    controller, who is permitted every entity, it is no member: unknown_member."""
    if dimension in ("entity", "region"):
        seen = tuple(e for e in dims.entities if e[0] in permitted)
        if dimension == "entity":
            found = _match(value, ((code, name) for code, name, _ in seen))
        else:
            regions = tuple(dict.fromkeys(region for _, _, region in seen if region))
            found = _match(value, ((r,) for r in regions))
        if found is not None:
            return found
        return outcomes.outcome("entity_not_permitted" if entity_view else "unknown_member")
    found = _match(value, ((code, name) for code, name, *_ in dims.accounts))
    if found is None:
        return outcomes.outcome("unknown_member")
    return found if found in _summed(metric, dims) else outcomes.outcome("filter_not_allowed")
