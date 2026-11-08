# The governed layer

What stands between a question and the figures: the metric registry
(`docs/registry.md`), a specification of the question, its validation, the
compiler that turns it into one SELECT, the guard that checks every query,
the roles, and the session that holds one visitor's warehouse. No model is
called yet: in Phase 3 a model will write the specification, and nothing
else. A model never writes SQL and never calculates.

Said plainly: the read-only connection with external access off is the
control; the guard is a second line; the allow-list of names narrows the
injection route and does not close it. v1 does not query the ledger: Actual
is the workbook's Actual block, and the ledger serves the tie-out. v1 does
not answer prior-year questions: the plan holds one fiscal year.

## Sessions and promotion

At start the app builds one template warehouse (`src/startup.py`): both
pairs a visitor may review (the sample workbook and the clean control
workbook, each with the committed ledger, which lands once), each pair's
breaks stored apart, nothing promoted. The file is closed and left read-only
on disk.

A session (`src/session.py`) is one visitor's copy of the template, in a
folder of its own, opened read-only. Its acknowledgements, its promotion and
its facts live in its own file, so one visitor's promotion never changes
another's answers. DuckDB refuses a read-only and a read-write connection to
one file in one process, so a write is a cycle: the read-only connection is
closed, a read-write one opened for the work and closed, and the read-only
one reopened, under a lock that no query runs through. Acknowledging the
pair's breaks and promoting it (a named person, every break acknowledged, the
facts rebuilt in one transaction) each take one cycle. At most 20 sessions
are open in a process; one more closes the least recently used, and the
folders are removed on close, at exit, and, for a crashed process's, at the
next start.

A question runs on one cursor per request, taken from the session's
read-only connection and closed after. With nothing promoted, every question
is the outcome `nothing_promoted`.

### What the read-only connection does not stop

The read-only connection stops every change to the file, but not an object
in memory that lives as long as one cursor. On a read-only connection DuckDB
allows `CREATE TEMP TABLE`, `CREATE TEMP VIEW`, `CREATE TEMP MACRO`,
`CREATE TEMP SEQUENCE` and `CHECKPOINT`, and a TEMP table named `fact_plan`
hides the real one from the cursor that made it. Two things together close
that: each request has its own cursor, so a TEMP object never outlives the
request that made it and no other request sees it; and the guard lets
through one SELECT and nothing else, so no compiled query can make one. A
test makes such a table with a wrong figure on one cursor and shows that the
next answer is the real figure.

Nor does the connection stop reads: a SELECT of a raw or gate table, of the
ledger's facts, or of all of `fact_plan` runs on it. For reads the compiler
(which writes only the queries below) and the guard (which refuses any other
relation and any unfiltered `fact_plan`) are the controls.

## Roles

`src/roles.py` has two roles. A group controller sees every entity of the
promoted pair. An entity controller sees one: every total, ratio and "group"
figure is over that entity alone, and the provenance says so ("entity view:
AT"). The role, never the specification, decides the entities a question may
see: the compiler injects them.

- A specification that names another entity, by its code or its name, is
  refused with `entity_not_permitted`, in fixed wording that names no
  entity: "That entity is not available to your role."
- Regions: AT and DE share Central Europe. For an AT controller, Central
  Europe is AT's own member: a filter on it is allowed, and a region grouping
  gives one row, Central Europe, with AT's figures alone. A region only other
  entities hold (Southern Europe, for AT) is refused like another entity,
  with `entity_not_permitted`.
- An entity controller whose entity the promoted pair does not hold is
  refused with `entity_not_permitted` before anything is compiled, so no
  query is ever built for an empty set of entities.
- The member lists a prompt may use (`members_for`) hold the role's own
  entity, its region, every account and every metric, and nothing of another
  entity.
- A limit, kept as the brief designed it: a name that is no member at all is
  `unknown_member`, and another entity's code is `entity_not_permitted`, so an
  entity controller can tell that a code exists without seeing anything of
  it.

## The specification and its validation

A specification (`src/spec.py`) has eight fields, every one required:
metric, version, dimensions, filters, period, comparison, sort and limit.
`from_json` takes exactly those keys; anything else is
`specification_invalid`, which never echoes what it was given. Validation
checks, in this order: something is promoted; the role's entity is in the
pair; the metric is in the registry; the version is a member; each dimension
is one the metric may be grouped by; each filter is one it may be filtered
by, and each value a member the role may see; the comparison; the period;
the limit (1 to 100); the sort (by the value or a grouped dimension). Members
are matched without regard to case, by code or by name, and replaced by
their canonical names; an unknown metric or member is refused, never
guessed. An account the metric does not sum (a subtotal, a statistic, an
account of another parent) is `filter_not_allowed`: the member exists, and
this metric cannot be filtered by it.

## The compiler

`src/compiler.py` turns a validated specification into one SELECT, from
fixed templates and the registry's structured formulas. Its guarantees:

- **The role is injected.** The query starts with the CTE `plan`, the only
  reference to `fact_plan`, filtered to the role's entities and the versions
  asked for, and the CTE `permitted`, the only reference to `dim_entity`,
  filtered to the same entities. Each appears exactly once, and every later
  step reads them, never the tables.
- **Nothing of a specification is concatenated as given.** Members enter only
  as canonical names through one quoting function (single quotes doubled); a
  dimension, a sort or a comparison selects a fixed fragment.
- **The finance rules of `docs/registry.md`**: the accounts a metric reads,
  resolved from `dim_account` in the query; held per metric; the held range;
  gaps; matched months; the closing value; the ratio of sums; variance, its
  percentage and its label. The coverage (months held, gaps) comes from the
  same query.
- **One helper divides.** `integer_route` gives the only fragment that
  divides, so every ratio and variance percentage is the exact quotient
  rounded once to twelve places. Nothing else in a compiled query divides.
- **The same SQL, byte for byte**, for the same specification, registry and
  role, in another process and under another hash seed.
- **It checks itself.** The compiler runs the guard on its own output and
  refuses to hand over a query that fails.

`session.answer` then asks DuckDB's own parser whether the text is one
SELECT, runs it on its own cursor, and returns an answer: the rows as exact
decimals, the coverage and gaps, the result hash and the provenance. Any
error of the engine is `engine_error`, with no SQL echoed. Every metric of
the grid of `tests/oracle.py` (29,232 questions on each pair) equals the
independent oracle's figures exactly.

## The guard

`src/guard.py` parses a query with sqlglot's DuckDB dialect, the only module
that imports sqlglot, and returns "ok" or one reason: a parse error; not
exactly one statement; a statement that is not a SELECT or a UNION of
SELECTs (DDL, DML, COPY, ATTACH, PRAGMA, SET, DESCRIBE, SHOW, EXPLAIN,
SUMMARIZE, PIVOT, a bare VALUES, INTERSECT, EXCEPT, INTO); a comment; a
literal that is not a plain string or number; a schema- or catalog-qualified
name; a table that is not a plain identifier (a table function, UNNEST,
LATERAL, VALUES, PIVOT, a quoted name or a file name); a CTE named like a
real relation; a relation not allowed, compared lower-cased; a function the
compiler never writes; and, given the role's entities, `fact_plan` and
`dim_entity` each exactly once, each in a SELECT that reads it alone and
whose WHERE holds `entity IN (...)` for exactly the role's set. Every check
reaches every CTE body and subquery. For a compiled query the relations
allowed are `fact_plan` and the four dimensions; the ledger's facts, the raw
tables and the gate's tables never are.

Its limits. The guard reads the text as sqlglot reads it, and the engine
could read some text differently; the session therefore also asks the
engine's own parser for one SELECT before it runs anything. The hostile
corpus, `tests/guard_corpus.txt`, holds 103 statements the guard refuses and
18 it passes, each also run on a session's read-only connection; it is a
floor, not a proof.

## Outcomes

A refusal is an outcome (`src/outcomes.py`): a code and one fixed sentence,
filled with nothing, so it never echoes a name.

| Code | Sentence |
|---|---|
| `nothing_promoted` | Nothing has been promoted yet. Review a batch first. |
| `specification_invalid` | The question could not be read: it is not a specification of the expected form. |
| `unknown_metric` | That metric is not in the registry. |
| `unknown_member` | A name in the question is not a member of the plan. |
| `dimension_not_allowed` | That metric cannot be grouped that way. |
| `filter_not_allowed` | That metric cannot be filtered that way. |
| `comparison_not_allowed` | That comparison is not available for that metric and version. |
| `prior_year_not_available` | Prior-year comparisons are not available: the plan holds one fiscal year. |
| `period_invalid` | That period is not valid: ask for a month, a quarter, the year to date or the fiscal year, within FY2026. |
| `limit_invalid` | The number of rows must be between 1 and 100. |
| `sort_invalid` | The rows can be sorted only by the value or by a dimension they are grouped by. |
| `entity_not_permitted` | That entity is not available to your role. |
| `no_data` | The version holds no figures for the period asked for. |
| `engine_error` | The figures could not be computed: the engine stopped the query. |

## Trying it

`python main.py query <spec.json> --pair clean|sample --role
group_controller|entity_controller [--entity ES] [--acknowledge-all-as
"Name"] [--promote-as "Name"]` builds the template in a temporary folder,
may acknowledge every break of the pair and promote it as the people named,
asks the specification under the role, and prints the outcome or the answer:
the SQL and its hash, the figures, the coverage and gaps, and the
provenance. It removes the folder after.
