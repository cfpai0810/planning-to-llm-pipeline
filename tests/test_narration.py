"""Narration (src/narration.py), part 3c (the reviewer's Phase 3 brief, sections 5 and 8).

figures_of(answer, registry, members) gives the checks' Figure for every number an answer
holds, under the exact words the template writes beside it: a name distinct for every row and
every column (the value, the comparator, the variance and the variance in per cent), so a
number is read against its own figure and two rows, or a value and its comparator, cannot swap
unseen. A percentage is the answer's fraction times 100; a variance of a percentage is in
points; units and FTE are counts; a "not meaningful" cell has no figure. The counts the
template writes (rows not shown, months asked for and held, gaps not listed) are count figures
with names of their own.

The template is built from the answer, the registry and the role's member lists alone. It
names the metric, the version, the period in words, the scope, the first ten rows (then how
many more), the comparison, each variance with its direction word from the computed sign and
the line's polarity, "not meaningful" where the rules give it, the months held against the
months asked, every gap up to five (then how many more) and a sentence when rows were cut. A
member is written by its name from the member lists, never as a code; a month in words. With
asks_for_a_cause it ends with the one fixed sentence. The checks give it no flag over a span
of more than 300 specifications, every metric, both pairs, the three roles, groupings,
comparisons and a gap; a changed number, a flipped direction word, two rows' values swapped
and a value swapped with its comparator are each flagged.

The reviewer's Stop 1 verdict (section 7, version 2) binds the template further, and the span
grows with it. A count is written in digits, a count figure with its name in the same clause
("the count of months held is 5"), and a changed count is flagged. The variance of a
percentage is written "percentage points", never "pp". The span covers every answer shape the
sample pair can give under every role, the group controller and the ES, AT and DE controllers:
each grouping of SHAPE_DIMENSIONS against each comparison and each kind of period, every
metric, every unit and both direction words; TestEveryShapeOfTheSamplePair names them. A
wrong number spelt out in words is not read by the checks: a known limit, shown by a test.

Model narration is one call with no schema and a fixed system text. Its user message holds the
figures as shown with their names, the coverage and the gaps, as data, and never the question;
for an entity controller nothing of another entity. Its text is shown when the checks give no
flag; otherwise the template is shown with the fixed notice, and the model's text and the flags
are kept for the record. A failed call shows the template with the other notice.
"""

import dataclasses
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import config
from tests import fakes

NOON = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
ENTITIES = {"ES": ("Calder Foods Spain", "Southern Europe"),
            "AT": ("Calder Foods Austria", "Central Europe"),
            "DE": ("Calder Foods Germany", "Central Europe")}
ROLE_NAMES = ("group", "ES", "AT", "DE")
METRICS = ("revenue", "cost_of_sales", "gross_profit", "operating_expenses", "ebit",
           "gross_margin_pct", "ebit_margin_pct", "marketing_pct_of_revenue", "volume",
           "headcount", "revenue_per_unit")
BY_ACCOUNT = ("revenue", "cost_of_sales", "operating_expenses")
QUESTION = "Which entity is furthest below budget, and why?"
# The answer shapes of the sample pair (the reviewer's Stop 1 verdict, section 7): each of these
# groupings against each comparison and each kind of period, under every role.
SHAPE_DIMENSIONS = ((), ("entity",), ("region",), ("account",), ("month",), ("quarter",),
                    ("entity", "month"), ("entity", "quarter"), ("region", "quarter"),
                    ("account", "quarter"))
SHAPE_COMPARISONS = ("none", "budget", "forecast")
SHAPE_PERIODS = (("month", "2026-03"), ("quarter", "Q2"), ("ytd", "2026-06"), ("fy", "FY2026"))


def spec(metric, version="Budget", period=("fy", "FY2026"), dimensions=(), comparison="none",
         filters=(), sort=None, limit=100) -> dict:
    return {"metric": metric, "version": version, "dimensions": list(dimensions),
            "filters": [{"dimension": d, "values": list(v)} for d, v in filters],
            "period": {"kind": period[0], "value": period[1]}, "comparison": comparison,
            "sort": None if sort is None else {"by": sort[0], "direction": sort[1]},
            "limit": limit}


def span_specs() -> list:
    """The span: for each metric, eight shapes of question, and more where a metric allows
    an account grouping; asked on both pairs under the three roles."""
    found = []
    for metric in METRICS:
        found += [
            spec(metric),
            spec(metric, "Actual", ("ytd", "2026-06"), ("entity",), "budget"),
            spec(metric, "Forecast", ("quarter", "Q2"), ("month",), "budget"),
            spec(metric, "Budget", ("month", "2026-03"), ("region",), "forecast"),
            spec(metric, "Budget", ("fy", "FY2026"), ("quarter",), "forecast",
                 sort=("value", "desc"), limit=2),
            spec(metric, "Budget", ("fy", "FY2026"), ("month",)),
            spec(metric, "Actual", ("ytd", "2026-06"), ("entity", "quarter"), "forecast"),
            spec(metric, "Actual", ("month", "2026-05"), comparison="budget",
                 filters=(("region", ("central europe",)),))]
        if metric in BY_ACCOUNT:
            found += [spec(metric, "Actual", ("ytd", "2026-06"), ("account",), "forecast"),
                      spec(metric, "Budget", ("quarter", "Q3"), ("entity",), "forecast")]
    found.append(spec("operating_expenses", "Actual", ("ytd", "2026-06"), ("entity",), "budget",
                      filters=(("account", ("6100",)),)))
    return found


def shape_specs() -> list:
    """One specification for each shape: each grouping of SHAPE_DIMENSIONS, each comparison and
    each kind of period, the metric turning through those that allow the grouping, the version
    Actual against a comparison and turning through the three without one."""
    found = []
    shapes = [(d, c, p) for d in SHAPE_DIMENSIONS for c in SHAPE_COMPARISONS
              for p in SHAPE_PERIODS]
    for i, (dimensions, comparison, period) in enumerate(shapes):
        allowed = BY_ACCOUNT if "account" in dimensions else METRICS
        version = "Actual" if comparison != "none" else ("Actual", "Budget", "Forecast")[i % 3]
        found.append(spec(allowed[i % len(allowed)], version, period, dimensions, comparison))
    return found


@pytest.fixture(scope="module")
def narration():
    from src import narration
    return narration


@pytest.fixture(scope="module")
def sessions(template, tmp_path_factory):
    """One session per pair, promoted, from the suite's one template."""
    from src import session
    pool = session.Sessions(template, root=tmp_path_factory.mktemp("narration"))
    clean = pool.open()
    clean.promote("clean", "P. Promoter", clock=lambda: NOON)
    sample = pool.open()
    sample.acknowledge_many("sample", [(b.break_id, "A. Reviewer", "accepted")
                                       for b in sample.breaks("sample")], clock=lambda: NOON)
    sample.promote("sample", "P. Promoter", clock=lambda: NOON)
    yield {"clean": clean, "sample": sample}
    pool.close_all()


def role_of(name):
    from src import roles
    return roles.group_controller if name == "group" else roles.entity_controller(name)


@pytest.fixture(scope="module")
def span(sessions):
    """Every answer of the span with figures: (pair, role name, specification, answer, members,
    registry), and the seconds the answers took."""
    from src import answer as answers
    from src import roles
    started = time.perf_counter()
    found = []
    for pair, s in sessions.items():
        loaded = s.registry()
        for role_name in ROLE_NAMES:
            role = role_of(role_name)
            members = roles.members_for(role, s)
            for question in span_specs() + (shape_specs() if pair == "sample" else []):
                result = s.answer(question, role)
                if isinstance(result, answers.Answer) and result.outcome is None:
                    found.append((pair, role_name, question, result, members, loaded))
    return found, time.perf_counter() - started


def made(narration, item, cause=False):
    pair, role_name, question, answer, members, loaded = item
    return (narration.template(answer, loaded, members, role_of(role_name),
                               asks_for_a_cause=cause),
            narration.figures_of(answer, loaded, members))


def check(narration, item, text):
    _, _, _, answer, members, loaded = item
    return narration.check(text, answer, loaded, members)


# -- The figures --------------------------------------------------------------------------------

class TestTheFigures:
    def test_each_number_of_the_answer_is_a_figure_with_a_distinct_name(self, narration, span):
        found, _ = span
        for item in found:
            _, _, _, answer, members, loaded = item
            figures = narration.figures_of(answer, loaded, members)
            names = [f.name for f in figures]
            assert len(names) == len(set(names)), names
            cells = sum(1 for row in answer.rows for (name, kind, _), cell in
                        zip(answer.columns, row) if kind not in ("dimension", "label")
                        and cell != "not meaningful")
            counts = [f for f in figures if f.unit == "count" and f.name in narration.COUNTS]
            assert len(figures) - len(counts) == cells

    def test_the_units_and_the_values(self, narration, span):
        from src import checks
        found, _ = span
        seen = set()
        for item in found:
            _, _, _, answer, members, loaded = item
            by_name = {f.name: f for f in narration.figures_of(answer, loaded, members)}
            for row in answer.rows:
                for (column, kind, unit), cell in zip(answer.columns, row):
                    if kind in ("dimension", "label") or cell == "not meaningful":
                        continue
                    name = narration.figure_name(answer, loaded, members, row, column)
                    figure = by_name[name]
                    expected = {"EUR": checks.MONEY, "EUR per unit": checks.MONEY,
                                "%": checks.PERCENT, "pp": checks.POINTS,
                                "units": checks.COUNT, "FTE": checks.COUNT}[unit]
                    assert figure.unit == expected, (column, unit)
                    times = 100 if unit == "%" else 1
                    assert figure.value == Decimal(cell) * times, (column, cell)
                    seen.add(unit)
        assert seen == {"EUR", "EUR per unit", "%", "pp", "units", "FTE"}

    def test_a_variance_carries_its_comparator_and_the_lines_polarity(self, narration, span):
        found, _ = span
        polarity = {"favourable_when_higher": True, "favourable_when_lower": False,
                    "ungraded": None}
        for item in found:
            _, _, question, answer, members, loaded = item
            if question["comparison"] == "none":
                continue
            higher = polarity[loaded.metric(answer.spec.metric).polarity]
            for f in narration.figures_of(answer, loaded, members):
                if f.against is not None:
                    assert f.against == question["comparison"]
                    assert f.higher_is_better is higher

    def test_a_figure_itself_has_its_versions_basis(self, narration, span):
        found, _ = span
        item = next(i for i in found if i[2]["comparison"] == "budget"
                    and i[2]["version"] == "Actual")
        _, _, _, answer, members, loaded = item
        bases = {f.basis for f in narration.figures_of(answer, loaded, members)
                 if f.against is None and f.unit != "count"}
        assert bases == {"actual", "budget"}


# -- The template --------------------------------------------------------------------------------

class TestTheSpanOfThreeHundred:
    def test_the_span_covers_what_the_brief_names(self, span):
        found, seconds = span
        assert len(found) >= 300
        assert {i[0] for i in found} == {"clean", "sample"}
        assert {i[1] for i in found} == set(ROLE_NAMES)
        assert {i[3].spec.metric for i in found} == set(METRICS)
        groupings = {tuple(i[3].spec.dimensions) for i in found}
        assert {(), ("entity",), ("region",), ("account",), ("month",), ("quarter",),
                ("entity", "quarter")} <= groupings
        assert {i[3].spec.comparison for i in found} == {"none", "budget", "forecast"}
        assert any(i[3].gaps for i in found)
        assert any(i[3].truncated for i in found) and any(i[3].row_count > 10 for i in found)
        assert any(i[3].spec.filters for i in found)

    def test_the_checks_flag_nothing_in_the_template(self, narration, span):
        found, _ = span
        flagged = []
        for item in found:
            for cause in (False, True):
                text, _ = made(narration, item, cause)
                flags = check(narration, item, text)
                if flags:
                    flagged.append((item[0], item[1], item[2], flags))
        assert flagged == []

    def test_every_figure_shown_is_written_as_answers_shown_gives_it(self, narration, span):
        # Changed on purpose by the Stop 1 verdict (version 2): a variance of a percentage is
        # written "percentage points", never "pp"; every other figure is answers.shown's.
        from src import answer as answers
        found, _ = span
        points = 0
        for item in found:
            text, _ = made(narration, item)
            answer = item[3]
            lines = text.splitlines()
            for i, row in enumerate(answer.rows[:narration.MAX_ROWS]):
                line = lines[1 + i]
                for (column, kind, unit), cell in zip(answer.columns, row):
                    if kind in ("dimension", "label") or cell == "not meaningful":
                        continue
                    shown = answers.shown(cell, unit)
                    if unit == "pp":
                        assert shown.endswith(" pp")
                        shown = shown[:-3] + " percentage points"
                        points += 1
                    assert shown in line, (column, cell, line)
                    assert narration.written(cell, unit) == shown
            assert not re.search(r"\bpp\b", text), text
        assert points > 100

    def test_the_direction_word_agrees_with_the_rows_own_label(self, narration, span):
        found, _ = span
        judged = 0
        for item in found:
            answer = item[3]
            if answer.spec.comparison == "none":
                continue
            text, _ = made(narration, item)
            lines = text.splitlines()
            label_at = [c[0] for c in answer.columns].index("label")
            for i, row in enumerate(answer.rows[:narration.MAX_ROWS]):
                label = row[label_at]
                words = re.findall(r"\b(favourable|adverse)\b", lines[1 + i])
                assert words == ([label] if label in ("favourable", "adverse") else []), (
                    label, lines[1 + i])
                judged += label in ("favourable", "adverse")
        assert judged > 100

    def test_it_says_what_the_brief_names(self, narration, span):
        found, _ = span
        for item in found:
            text, _ = made(narration, item)
            answer, loaded = item[3], item[5]
            first = text.splitlines()[0]
            assert first.startswith(loaded.metric(answer.spec.metric).label)
            assert answer.spec.version in first
            if answer.spec.comparison != "none":
                assert f"against {answer.coverage.compared_with}" in first
            shown_rows = min(answer.row_count, narration.MAX_ROWS)
            more = answer.row_count - shown_rows
            # Changed on purpose by the Stop 1 verdict (version 2): each count in digits, with
            # its name in the same clause.
            assert (f"The count of rows not shown is {more}." in text) == (more > 0)
            assert (narration.TRUNCATED in text) == answer.truncated
            assert (f"The count of months asked for is {len(answer.coverage.months_asked)} ("
                    in text)
            assert f"and the count of months held is {len(answer.coverage.months_held)} (" in text
            assert ("Gaps: none." in text) == (not answer.gaps)

    def test_members_are_written_by_name_and_months_in_words(self, narration, span):
        found, _ = span
        for item in found:
            text, _ = made(narration, item)
            assert not re.search(r"\b20\d\d-\d\d\b", text), text
            assert not re.search(r"\b(4000|4100|5000|5100|5200|6000|6100|6200|6300)\b", text)
            assert not re.search(r"\b(ES|AT|DE)\b", text), text

    def test_the_template_names_its_scope(self, narration, span):
        found, _ = span
        for item in found:
            text, _ = made(narration, item)
            first = text.splitlines()[0]
            if item[2]["filters"]:
                continue
            if item[1] == "group":
                assert "for the group" in first
            else:
                assert f"for {ENTITIES[item[1]][0]}" in first

    def test_a_filter_the_model_gave_in_another_case_is_written_as_the_member_list_writes_it(
            self, narration, span):
        found, _ = span
        item = next(i for i in found if i[2]["filters"]
                    and i[2]["filters"][0]["dimension"] == "region")
        text, _ = made(narration, item)
        assert "Central Europe" in text and "central europe" not in text

    def test_the_why_sentence_is_added_once_and_last(self, narration, span):
        found, _ = span
        for item in found[:40]:
            text, _ = made(narration, item, cause=True)
            plain, _ = made(narration, item, cause=False)
            assert text == plain + "\n" + narration.CAUSE
            assert text.count(narration.CAUSE) == 1
        assert narration.CAUSE == ("The plan holds no causes, so this shows what moved and by "
                                   "how much, not why.")

    def test_the_template_prose_holds_no_code_and_no_claim_word(self, narration):
        prose = " ".join([narration.TRUNCATED, narration.CAUSE, narration.NOTICE_WITHHELD,
                          narration.NOTICE_UNAVAILABLE, narration.SYSTEM])
        assert re.findall(r"\b(ES|AT|DE)\b", prose) == []
        assert re.findall(r"(?i)\b(es|at|de)\b", prose) == []


class TestShapesTheSpanMayMiss:
    def _item(self, span, **where):
        found, _ = span
        return next(i for i in found if all(i[2][k] == v for k, v in where.items()))

    def test_more_than_five_gaps(self, narration, span):
        item = self._item(span, comparison="budget", dimensions=["entity"], version="Actual")
        answer = dataclasses.replace(item[3], gaps=tuple(
            ("AT", f"2026-0{m}") for m in range(1, 8)) if item[1] != "ES" else tuple(
            ("ES", f"2026-0{m}") for m in range(1, 8)))
        changed = (*item[:3], answer, *item[4:])
        text, _ = made(narration, changed)
        assert "; the count of gaps not listed is 2." in text
        assert check(narration, changed, text) == []

    def test_a_not_meaningful_cell(self, narration, span):
        item = self._item(span, comparison="budget", dimensions=["entity"], version="Actual",
                          metric="revenue")
        answer = item[3]
        at = [c[0] for c in answer.columns].index("variance_pct")
        rows = tuple(row[:at] + ("not meaningful",) + row[at + 1:] for row in answer.rows)
        changed = (*item[:3], dataclasses.replace(answer, rows=rows), *item[4:])
        text, figures = made(narration, changed)
        assert "was not meaningful" in text
        assert not [f for f in figures if "in per cent" in f.name]
        assert check(narration, changed, text) == []

    def test_a_member_name_with_digits_is_passed_as_a_label(self, narration, span):
        # The sample has no member name with digits; another planning export may. Written
        # outside a figure's name (here the filter in the first line), its digits would be read
        # as a number; narration passes every member name it writes as a label.
        from src import checks, roles
        found, _ = span
        item = next(i for i in found if i[2]["filters"]
                    and i[2]["filters"][0]["dimension"] == "account")
        members = item[4]
        renamed = roles.Members(members.entities, members.regions,
                                tuple((c, "Zone 9071 marketing" if c == "6100" else n)
                                      for c, n in members.accounts), members.metrics)
        changed = (*item[:4], renamed, item[5])
        text, figures = made(narration, changed)
        assert "Zone 9071 marketing" in text.splitlines()[0]
        assert check(narration, changed, text) == []
        assert checks.check_text(text, figures, currency="EUR")
        assert "Zone 9071 marketing" in narration.labels_of(item[3], renamed)


class TestMutationsAreFlagged:
    def _with(self, span, narration, rows=2, comparison=True):
        """Up to 25 answers of the span, spread across it, with at least that many rows and a
        comparison or none; with two rows or more, the first two values shown differently."""
        found, _ = span
        picked = []
        for item in found:
            answer = item[3]
            if answer.row_count < rows or (answer.spec.comparison != "none") != comparison:
                continue
            if rows >= 2 and self._shown(item, 0, "value") == self._shown(item, 1, "value"):
                continue
            picked.append(item)
        return picked[::max(1, len(picked) // 25)][:25]

    def _shown(self, item, row, column):
        from src import answer as answers
        answer = item[3]
        names = [c[0] for c in answer.columns]
        return answers.shown(answer.rows[row][names.index(column)],
                             answer.columns[names.index(column)][2])

    def test_a_changed_number(self, narration, span):
        cases = self._with(span, narration, rows=1, comparison=False)
        assert len(cases) >= 20
        for item in cases:
            text, _ = made(narration, item)
            written = self._shown(item, 0, "value")
            digits = re.search(r"\d", written)
            changed = written[:digits.start()] + str((int(digits.group(0)) + 1) % 10) + \
                written[digits.end():]
            mutated = text.replace(written, changed, 1)
            assert mutated != text and check(narration, item, mutated), (written, changed)

    def test_a_flipped_direction_word(self, narration, span):
        found, _ = span
        cases = [i for i in found if re.search(r"\b(favourable|adverse)\b", made(narration,
                                                                                  i)[0])][:40]
        assert len(cases) >= 20
        for item in cases:
            text, _ = made(narration, item)
            word = re.search(r"\b(favourable|adverse)\b", text)
            other = "adverse" if word.group(0) == "favourable" else "favourable"
            mutated = text[:word.start()] + other + text[word.end():]
            flags = check(narration, item, mutated)
            assert [f.kind for f in flags] == ["direction"], (word.group(0), flags)

    def test_two_rows_values_swapped(self, narration, span):
        cases = self._with(span, narration, rows=2, comparison=False)
        assert len(cases) >= 20
        for item in cases:
            text, _ = made(narration, item)
            first, second = self._shown(item, 0, "value"), self._shown(item, 1, "value")
            lines = text.splitlines()
            lines[1] = lines[1].replace(first, second)
            lines[2] = lines[2].replace(second, first)
            mutated = "\n".join(lines)
            assert mutated != text and check(narration, item, mutated), (first, second)

    def test_a_value_and_its_comparator_swapped(self, narration, span):
        found, _ = span
        differ = [i for i in found if i[3].spec.comparison != "none"
                  and self._shown(i, 0, "value") != self._shown(i, 0, "comparator")]
        cases = differ[::max(1, len(differ) // 25)][:25]
        assert len(cases) >= 20
        for item in cases:
            text, _ = made(narration, item)
            value, comparator = self._shown(item, 0, "value"), self._shown(item, 0, "comparator")
            lines = text.splitlines()
            lines[1] = lines[1].replace(value, "@V@").replace(comparator, value).replace(
                "@V@", comparator)
            mutated = "\n".join(lines)
            assert mutated != text and check(narration, item, mutated), (value, comparator)


# -- The narration input and leakage ---------------------------------------------------------------

def other_patterns(code: str) -> list:
    own = ENTITIES[code][1]
    found = []
    for other, (name, region) in ENTITIES.items():
        if other != code:
            found += [rf"\b{other}\b", rf"(?i){re.escape(name)}"]
            if region != own:
                found.append(rf"(?i){re.escape(region)}")
    return found


class TestTheNarrationInput:
    def test_it_holds_the_figures_as_shown_with_their_names_the_coverage_and_the_gaps(
            self, narration, span):
        found, _ = span
        item = next(i for i in found if i[3].gaps and i[3].spec.comparison != "none")
        _, role_name, _, answer, members, loaded = item
        message = narration.narration_input(answer, loaded, members, role_of(role_name))
        body = re.fullmatch(r'<data name="answer">\n(.*)\n</data>', message, flags=re.DOTALL)
        data = json.loads(body.group(1))
        text, figures = made(narration, item)
        assert {f["name"] for f in data["figures"]} <= {f.name for f in figures}
        for f in data["figures"]:
            assert f"{f['name']} was {f['shown']}" in text
        assert data["months_asked"] and data["months_held"] and data["gaps"]
        assert set(data) == {"metric", "version", "period", "scope", "comparison", "figures",
                             "not_meaningful", "rows_not_shown", "truncated", "months_asked",
                             "months_held", "gaps"}

    def test_it_never_holds_the_question(self, narration, span, sessions):
        from src import ask
        s = sessions["sample"]
        client = fakes.FakeClient(replies=[
            fakes.Reply(json.dumps({"refusal": None, "specification": spec(
                "revenue", "Actual", ("ytd", "2026-06"), ("entity",), "budget"),
                "asks_for_a_cause": True})),
            fakes.Reply("Ready.")])
        ask.ask(client, s, role_of("group"), QUESTION, audit=_Audit(), narrate="model")
        narration_call = client.calls[1]
        assert QUESTION not in json.dumps(narration_call)
        assert "why" not in narration_call["messages"][0]["content"].lower()

    @pytest.mark.parametrize("code", ["ES", "AT", "DE"])
    def test_no_other_entity_for_an_entity_controller(self, narration, sessions, code):
        from src import roles
        s = sessions["sample"]
        role = role_of(code)
        members = roles.members_for(role, s)
        for question in span_specs():
            answer = s.answer(question, role)
            if getattr(answer, "outcome", "x") is not None:
                continue
            message = narration.narration_input(answer, s.registry(), members, role)
            text = narration.template(answer, s.registry(), members, role)
            for checked in (message, text):
                assert [p for p in other_patterns(code) if re.search(p, checked)] == []


class _Audit:
    def __init__(self):
        self.records = []

    def write(self, record):
        json.dumps(record, ensure_ascii=True, allow_nan=False)
        self.records.append(record)


# -- Model narration with the fake -----------------------------------------------------------------

def _bump(text: str) -> str:
    """The text with its first digit changed to the next one."""
    digit = re.search(r"\d", text)
    return text[:digit.start()] + str((int(digit.group(0)) + 1) % 10) + text[digit.end():]


def _good_text(narration, item) -> str:
    """Wording a model might write: the first row's value and, with a comparison, its variance
    and direction word, under the names it was given."""
    from src import answer as answers
    answer, members, loaded = item[3], item[4], item[5]
    names = [c[0] for c in answer.columns]
    row = answer.rows[0]
    value = narration.figure_name(answer, loaded, members, row, "value")
    unit = answer.columns[names.index("value")][2]
    text = f"{value} was {answers.shown(row[names.index('value')], unit)}."
    if "variance" in names:
        variance = narration.figure_name(answer, loaded, members, row, "variance")
        label = row[names.index("label")]
        unit = answer.columns[names.index("variance")][2]
        text += (f" {variance} was {answers.shown(row[names.index('variance')], unit)}"
                 + (f", {label}." if label in ("favourable", "adverse") else "."))
    return text


class TestModelNarration:
    def _run(self, narration, item, *replies, cause=False):
        _, role_name, _, answer, members, loaded = item
        client = fakes.FakeClient(replies=list(replies))
        narrated = narration.narrate(client, answer, loaded, members, role_of(role_name),
                                     mode="model", asks_for_a_cause=cause)
        return narrated, client

    def _item(self, span):
        found, _ = span
        return next(i for i in found if i[3].spec.comparison == "budget"
                    and i[3].rows[0][-1] in ("favourable", "adverse") and i[1] == "group")

    def test_clean_text_is_shown(self, narration, span):
        item = self._item(span)
        text = _good_text(narration, item)
        narrated, client = self._run(narration, item, fakes.Reply(text))
        assert (narrated.mode_shown, narrated.text, narrated.flags, narrated.failure) == (
            "model", text, (), None)
        assert narrated.model_text is None and len(client.calls) == 1

    def test_the_call_is_the_fixed_system_text_and_the_input_with_no_schema(self, narration,
                                                                            span):
        item = self._item(span)
        narrated, client = self._run(narration, item, fakes.Reply("Ready."))
        (call,) = client.calls
        assert call["system"] == narration.SYSTEM
        assert call["messages"] == [{"role": "user", "content": narration.narration_input(
            item[3], item[5], item[4], role_of(item[1]))}]
        assert call["output_config"] == {"effort": config.EFFORT}
        assert call["model"] == config.MODEL and call["max_tokens"] == config.MAX_TOKENS

    def test_a_wrong_number_is_withheld(self, narration, span):
        item = self._item(span)
        text = _bump(_good_text(narration, item))
        narrated, _ = self._run(narration, item, fakes.Reply(text))
        template = narration.template(item[3], item[5], item[4], role_of(item[1]))
        assert narrated.mode_shown == "template"
        assert narrated.text == template + "\n" + narration.NOTICE_WITHHELD
        assert narrated.model_text == text and narrated.flags
        assert {f.kind for f in narrated.flags} == {"numeric"}

    def test_a_wrong_direction_is_withheld(self, narration, span):
        item = self._item(span)
        good = _good_text(narration, item)
        text = good.replace("favourable", "@").replace("adverse", "favourable").replace(
            "@", "adverse")
        assert text != good
        narrated, _ = self._run(narration, item, fakes.Reply(text))
        assert narrated.mode_shown == "template" and narrated.text.endswith(
            narration.NOTICE_WITHHELD)
        assert [f.kind for f in narrated.flags] == ["direction"]

    @pytest.mark.parametrize("reply, code", [
        (fakes.status_error(fakes.anthropic.RateLimitError, 429), "rate_limited"),
        (fakes.Reply("Revenue was", stop_reason="max_tokens"), "reply_cut_off"),
        (fakes.Reply("I would rather not.", stop_reason="refusal"), "model_refused"),
        (fakes.Reply(None), "unreadable_reply"),
        (fakes.Reply(blocks=({"type": "text", "text": "One."}, {"type": "text", "text": "Two."})),
         "unreadable_reply")])
    def test_a_failed_call_shows_the_template_with_the_other_notice(self, narration, span,
                                                                     reply, code):
        item = self._item(span)
        narrated, client = self._run(narration, item, reply)
        template = narration.template(item[3], item[5], item[4], role_of(item[1]))
        assert (narrated.mode_shown, narrated.failure, narrated.flags) == ("template", code, ())
        assert narrated.text == template + "\n" + narration.NOTICE_UNAVAILABLE
        assert len(client.calls) == 1

    def test_the_why_sentence_follows_the_text_shown_and_is_not_checked(self, narration, span):
        item = self._item(span)
        text = _good_text(narration, item)
        shown, _ = self._run(narration, item, fakes.Reply(text), cause=True)
        assert shown.text == text + "\n" + narration.CAUSE and shown.flags == ()
        withheld, _ = self._run(narration, item, fakes.Reply(_bump(text)), cause=True)
        template = narration.template(item[3], item[5], item[4], role_of(item[1]),
                                      asks_for_a_cause=True)
        assert withheld.text == template + "\n" + narration.NOTICE_WITHHELD
        assert withheld.text.count(narration.CAUSE) == 1

    def test_the_template_mode_makes_no_call(self, narration, span):
        item = self._item(span)
        client = fakes.FakeClient(replies=[])
        narrated = narration.narrate(client, item[3], item[5], item[4], role_of(item[1]),
                                     mode="template", asks_for_a_cause=False)
        assert client.calls == [] and narrated.mode_shown == "template"
        assert narrated.text == narration.template(item[3], item[5], item[4], role_of(item[1]))

    def test_the_record(self, narration, span):
        item = self._item(span)
        text = _bump(_good_text(narration, item))
        narrated, _ = self._run(narration, item, fakes.Reply(text))
        record = narrated.record()
        assert set(record) == {"mode_asked", "mode_shown", "text_shown", "model_text", "flags",
                               "failure"}
        assert (record["mode_asked"], record["mode_shown"], record["model_text"]) == (
            "model", "template", text)
        assert record["text_shown"] == narrated.text
        assert record["flags"] and all(set(f) == {"kind", "text", "figure", "reason"}
                                       for f in record["flags"])
        json.dumps(record, ensure_ascii=True, allow_nan=False)


# -- The Stop 1 verdict, section 7 (version 2) -------------------------------------------------

class TestEveryShapeOfTheSamplePair:
    """The template passes the checks for every answer shape the sample pair can give, under
    every role. The shapes covered: each grouping of SHAPE_DIMENSIONS (none; entity; region;
    account; month; quarter; entity and month; entity and quarter; region and quarter; account
    and quarter), against each comparison (none, budget, forecast), for each kind of period
    (month, quarter, year to date, fiscal year), under the group controller and the ES, AT and
    DE controllers; with every metric, every unit and both direction words under each role."""

    def test_every_shape_under_every_role(self, span):
        found, _ = span
        wanted = {(d, c, p[0]) for d in SHAPE_DIMENSIONS for c in SHAPE_COMPARISONS
                  for p in SHAPE_PERIODS}
        assert len(wanted) == 120
        for role_name in ROLE_NAMES:
            covered = {(tuple(i[3].spec.dimensions), i[3].spec.comparison, i[3].spec.period.kind)
                       for i in found if i[0] == "sample" and i[1] == role_name}
            assert wanted - covered == set(), role_name

    def test_every_metric_unit_and_direction_word_under_every_role(self, span):
        found, _ = span
        for role_name in ROLE_NAMES:
            mine = [i[3] for i in found if i[0] == "sample" and i[1] == role_name]
            assert {a.spec.metric for a in mine} == set(METRICS), role_name
            units = {unit for a in mine for _, kind, unit in a.columns
                     if kind not in ("dimension", "label")}
            assert units == {"EUR", "EUR per unit", "%", "pp", "units", "FTE"}, role_name
            labels = {row[-1] for a in mine if a.spec.comparison != "none" for row in a.rows}
            assert {"favourable", "adverse"} <= labels, role_name
            if role_name in ("group", "AT"):          # the sample's one gap is AT in May
                assert any(a.gaps for a in mine), role_name


class TestCountsInDigits:
    def test_each_count_is_a_count_figure_named_in_the_clause_of_its_number(self, narration,
                                                                           span):
        from src import checks
        found, _ = span
        for item in found:
            text, figures = made(narration, item)
            counts = [f for f in figures if f.name in narration.COUNTS]
            assert {f.name for f in counts} >= {"months asked for", "months held"}
            for f in counts:
                assert f.unit == checks.COUNT
                assert re.search(rf"\bthe count of {f.name} is {f.value}\b", text, re.I), (
                    f.name, text)

    def test_a_changed_count_is_flagged(self, narration, span):
        found, _ = span
        cases = 0
        for item in found[::20]:
            text, figures = made(narration, item)
            for f in figures:
                if f.name not in narration.COUNTS:
                    continue
                said = f"the count of {f.name} is {f.value}"
                start = text.lower().index(said)
                wrong = text[:start] + said[:-len(str(f.value))] + str(f.value + 1) + \
                    text[start + len(said):]
                flags = check(narration, item, wrong)
                assert [fl.figure for fl in flags] == [f.name], (f.name, flags)
                cases += 1
        assert cases >= 60


class TestKnownLimits:
    def test_a_wrong_number_spelt_out_in_words_is_not_read_by_the_checks(self, narration,
                                                                         span):
        # The Stop 1 verdict (section 7): the numeric check reads digits, not numbers spelt out,
        # so a wrong number in words gives no flag. A known limit; the narration's system text
        # asks for every number in digits.
        found, _ = span
        item = next(i for i in found if i[1] == "group" and i[2]["comparison"] == "none"
                    and not i[2]["dimensions"])
        answer, members, loaded = item[3], item[4], item[5]
        name = narration.figure_name(answer, loaded, members, answer.rows[0], "value")
        assert check(narration, item, f"{name} was 1,234 EUR.")
        assert check(narration, item, f"{name} was one thousand two hundred EUR.") == []

    def test_the_narration_system_text_asks_for_digits(self, narration):
        assert "Write every number in digits, exactly as given." in narration.SYSTEM
