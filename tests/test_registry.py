"""The metric registry (registry/metrics.toml, src/registry.py), part 2b, brief 4.1 and 4.2.

The registry is a TOML file read with the standard library's tomllib: a header (version,
owner, unit system), a changelog, and one table per metric whose formula is structured
data, never a SQL string. The loader validates it and gives frozen dataclasses; it imports
no database engine. The eleven metrics, their kinds, units, polarities, dimensions, filters
and comparisons are the reviewer's table, written out by hand below and never read from
the file. Every account or parent a formula names is checked against the committed Lists,
and every sum's units, kinds and polarity against the accounts it sums.

The version lock (registry/registry.lock.json) holds the registry version and a SHA-256 of
each metric's canonical definition: a definition that changes while the version stays the
same fails a test, and scripts/lock_registry.py refuses to rewrite the lock without a
version bump. The registry hash is the SHA-256 of the file's raw bytes, with LF endings.
"""

import copy
import datetime
import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from tests.extractions import SAMPLE, extraction

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry" / "metrics.toml"
LOCK = ROOT / "registry" / "registry.lock.json"
SCRIPT = ROOT / "scripts" / "lock_registry.py"

# The reviewer's table (brief 4.2), by hand: id -> (kind, time aggregation, unit, polarity,
# group by).
ALL = ("entity", "region", "account", "month", "quarter")
NO_ACCOUNT = ("entity", "region", "month", "quarter")
HIGHER, LOWER, UNGRADED = "favourable_when_higher", "favourable_when_lower", "ungraded"
TABLE = {
    "revenue": ("flow", "sum", "EUR", HIGHER, ALL),
    "cost_of_sales": ("flow", "sum", "EUR", LOWER, ALL),
    "gross_profit": ("flow", "sum", "EUR", HIGHER, NO_ACCOUNT),
    "operating_expenses": ("flow", "sum", "EUR", LOWER, ALL),
    "ebit": ("flow", "sum", "EUR", HIGHER, NO_ACCOUNT),
    "gross_margin_pct": ("ratio", None, "%", HIGHER, NO_ACCOUNT),
    "ebit_margin_pct": ("ratio", None, "%", HIGHER, NO_ACCOUNT),
    "marketing_pct_of_revenue": ("ratio", None, "%", LOWER, NO_ACCOUNT),
    "volume": ("flow", "sum", "units", HIGHER, NO_ACCOUNT),
    "headcount": ("stock", "closing", "FTE", UNGRADED, NO_ACCOUNT),
    "revenue_per_unit": ("ratio", None, "EUR per unit", HIGHER, NO_ACCOUNT),
}
# The three that sum several accounts may also be filtered by account.
SEVERAL = ("revenue", "cost_of_sales", "operating_expenses")
LISTS_POLARITY = {"Favourable when higher": HIGHER, "Favourable when lower": LOWER}


@pytest.fixture(scope="module")
def registry():
    from src import registry
    return registry


@pytest.fixture(scope="module")
def loaded(registry):
    return registry.load()


@pytest.fixture(scope="module")
def lists():
    return extraction(SAMPLE).lists


def raw() -> dict:
    return tomllib.loads(REGISTRY.read_text(encoding="utf-8"))


def _m(data: dict, metric_id: str) -> dict:
    """One metric's table in the parsed registry."""
    return next(t for t in data["metric"] if t["id"] == metric_id)


def changed(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


# -- The file ----------------------------------------------------------------------------------

class TestTheFile:
    def test_the_header(self, loaded):
        assert (loaded.version, loaded.owner, loaded.unit_system) == (
            "1.0.0", "Group Finance", "EUR")
        assert re.fullmatch(r"\d+\.\d+\.\d+", loaded.version)

    def test_the_metrics_are_the_reviewers_eleven_in_order(self, loaded):
        assert [m.id for m in loaded.metrics] == list(TABLE)

    @pytest.mark.parametrize("metric_id", list(TABLE))
    def test_each_metric_is_the_reviewers(self, loaded, metric_id):
        kind, aggregation, unit, polarity, dimensions = TABLE[metric_id]
        m = loaded.metric(metric_id)
        assert (m.kind, m.time_aggregation, m.unit, m.polarity) == (
            kind, aggregation, unit, polarity)
        assert m.dimensions == dimensions
        assert m.filters == (("entity", "region", "account") if metric_id in SEVERAL
                             else ("entity", "region"))
        assert m.comparisons == ("budget", "forecast")
        assert m.owner == "Group Finance"
        assert m.label and re.fullmatch(r"[a-z][a-z0-9_]*", m.id)

    def test_the_formulas(self, loaded, registry):
        f = {m.id: m.formula for m in loaded.metrics}
        assert f["revenue"] == registry.Sum(parent="Revenue")
        assert f["cost_of_sales"] == registry.Sum(parent="Cost of sales")
        assert f["operating_expenses"] == registry.Sum(parent="Operating expenses")
        assert f["volume"] == registry.Sum(account="VOL")
        assert f["headcount"] == registry.Sum(account="HC")
        assert f["gross_profit"] == registry.Difference(("revenue", "cost_of_sales"))
        assert f["ebit"] == registry.Difference(("gross_profit", "operating_expenses"))
        assert f["gross_margin_pct"] == registry.Ratio("gross_profit", "revenue")
        assert f["ebit_margin_pct"] == registry.Ratio("ebit", "revenue")
        # The lean, question 1 on the stop page: an operand of a ratio may be an inline
        # sum, so the public list stays at eleven.
        assert f["marketing_pct_of_revenue"] == registry.Ratio(registry.Sum(account="6100"),
                                                               "revenue")
        assert f["revenue_per_unit"] == registry.Ratio("revenue", "volume")

    def test_every_definition_is_one_or_two_sentences_of_words(self, loaded):
        for m in loaded.metrics:
            sentences = [s for s in re.split(r"(?<=\.)\s+", m.definition.strip()) if s]
            assert 1 <= len(sentences) <= 2 and m.definition.endswith("."), m.id
            assert not re.search(r"\bSELECT\b|\bFROM\b|\bSUM\(", m.definition), m.id

    def test_the_brief_example_reads_as_written(self, loaded):
        assert loaded.metric("gross_margin_pct").definition == (
            "Gross profit as a share of revenue, computed on the sums for the group and "
            "period asked for.")

    def test_the_changelog_names_a_person_and_a_date_for_the_current_version(self, loaded):
        entries = [c for c in loaded.changelog if c.version == loaded.version]
        assert len(entries) == 1
        (entry,) = entries
        assert isinstance(entry.date, datetime.date) and entry.date == datetime.date(2026, 11, 7)
        assert entry.change and entry.approved_by.strip()

    def test_the_approver_is_the_fictional_one(self, loaded):
        # Question 3 on the stop page: a fictional person of the sample's company,
        # never a real one.
        (entry,) = loaded.changelog
        assert entry.approved_by == "E. Marsh, Group Financial Controller, Calder Foods Group"

    def test_no_formula_is_a_sql_string(self):
        text = REGISTRY.read_text(encoding="utf-8")
        assert not re.search(r"\b(SELECT|FROM|WHERE|JOIN|GROUP BY)\b", text)
        for table in raw()["metric"]:
            assert isinstance(table["formula"], dict), table["id"]

    def test_the_file_holds_no_cr_byte(self):
        assert b"\r" not in REGISTRY.read_bytes()

    def test_the_registry_hash_is_the_sha256_of_the_raw_bytes(self, loaded):
        assert loaded.sha256 == hashlib.sha256(REGISTRY.read_bytes()).hexdigest()
        assert re.fullmatch(r"[0-9a-f]{64}", loaded.sha256)


# -- The loader --------------------------------------------------------------------------------

class TestTheLoader:
    def test_it_reads_with_tomllib_and_imports_no_engine(self):
        source = (ROOT / "src" / "registry.py").read_text(encoding="utf-8")
        assert "import tomllib" in source
        assert not re.search(r"^\s*(import|from)\s+(duckdb|sqlglot|pandas)\b", source,
                             flags=re.MULTILINE)

    def test_the_dataclasses_are_frozen(self, loaded, registry):
        import dataclasses
        for value in (loaded, loaded.metrics[0], loaded.changelog[0],
                      registry.Sum(parent="Revenue")):
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(value, "version" if value is loaded else "id", "x")

    def test_the_closed_sets(self, registry):
        assert registry.DIMENSIONS == ("entity", "region", "account", "month", "quarter")
        assert registry.KINDS == ("flow", "stock", "ratio")
        assert registry.TIME_AGGREGATIONS == ("sum", "closing")
        assert registry.RESERVED_AGGREGATIONS == ("average",)
        assert registry.POLARITIES == (HIGHER, LOWER, UNGRADED)
        assert registry.COMPARISONS == ("budget", "forecast")
        assert registry.FORMULAS == ("sum", "difference", "ratio")

    # Each case changes the committed registry's tables in one place, then hands them to the
    # validator: (what, the change, words of the refusal).
    BAD = [
        ("an id twice", lambda d: _m(d, "cost_of_sales").update(id="revenue"), "more than once"),
        ("an id not in snake case", lambda d: _m(d, "volume").update(id="Volume"), "lower-case"),
        ("a metric not defined",
         lambda d: _m(d, "gross_margin_pct")["formula"].update(numerator="gross_profits"),
         "not a metric"),
        ("the reserved aggregation", lambda d: _m(d, "headcount").update(
            time_aggregation="average"), "not implemented"),
        ("a ratio with an aggregation", lambda d: _m(d, "revenue_per_unit").update(
            time_aggregation="sum"), "a ratio has none"),
        ("a flow with none", lambda d: _m(d, "volume").pop("time_aggregation"),
         "time_aggregation"),
        ("a stock that sums", lambda d: _m(d, "headcount").update(time_aggregation="sum"),
         "closing"),
        ("a dimension not in the set", lambda d: _m(d, "ebit").update(
            dimensions=["entity", "scenario"]), "not one of"),
        ("a filter not in the set", lambda d: _m(d, "ebit").update(filters=["entity", "version"]),
         "not one of"),
        ("a comparison not in the set", lambda d: _m(d, "ebit").update(
            comparisons=["budget", "prior_year"]), "not one of"),
        ("a polarity not in the set", lambda d: _m(d, "headcount").update(polarity="neutral"),
         "not one of"),
        ("a kind not in the set", lambda d: _m(d, "volume").update(kind="balance"), "not one of"),
        ("a field not in the schema", lambda d: _m(d, "volume").update(extra="x"), "not a field"),
        ("a field missing", lambda d: _m(d, "volume").pop("owner"), "owner"),
        ("parent and account both", lambda d: _m(d, "revenue")["formula"].update(account="4000"),
         "exactly one of"),
        ("neither parent nor account", lambda d: _m(d, "revenue")["formula"].pop("parent"),
         "exactly one of"),
        ("a formula type not in the set", lambda d: _m(d, "ebit")["formula"].update(
            type="product"), "not one of"),
        ("a difference of different units", lambda d: _m(d, "gross_profit")["formula"].update(
            metrics=["revenue", "volume"]), "different units"),
        ("a difference of one metric", lambda d: _m(d, "gross_profit")["formula"].update(
            metrics=["revenue"]), "at least two"),
        ("a formula as a string", lambda d: _m(d, "volume").update(formula="SELECT 1"),
         "structured"),
        ("a ratio with no unit", lambda d: _m(d, "revenue_per_unit").pop("unit"), "unit"),
        ("a cycle", lambda d: _m(d, "gross_profit")["formula"].update(
            metrics=["ebit", "cost_of_sales"]), "cycle"),
        ("an inline operand that is not a sum",
         lambda d: _m(d, "marketing_pct_of_revenue")["formula"].update(
             numerator={"type": "difference", "metrics": ["revenue", "cost_of_sales"]}),
         "inline"),
        ("a ratio metric with a sum formula", lambda d: _m(d, "revenue_per_unit").update(
            formula={"type": "sum", "parent": "Revenue"}), "a ratio"),
        ("a flow metric with a ratio formula", lambda d: _m(d, "volume").update(
            formula={"type": "ratio", "numerator": "revenue", "denominator": "ebit"}), "a ratio"),
        ("a version not of the form 1.0.0", lambda d: d.update(version="one"), "version"),
        ("a top-level field not in the schema", lambda d: d.update(currency="EUR"),
         "not a field"),
        ("a changelog entry with no approver", lambda d: d["changelog"][0].pop("approved_by"),
         "approved_by"),
        ("a changelog date that is text", lambda d: d["changelog"][0].update(date="7 Nov"),
         "date"),
    ]

    @pytest.mark.parametrize("what, change, words", BAD, ids=[case[0] for case in BAD])
    def test_a_bad_registry_is_refused(self, registry, what, change, words):
        data = copy.deepcopy(raw())
        change(data)
        with pytest.raises(registry.RegistryError, match=words):
            registry.from_data(data, sha256="0" * 64)

    def test_the_committed_tables_pass_the_same_validator(self, registry, loaded):
        assert registry.from_data(copy.deepcopy(raw()), sha256=loaded.sha256) == loaded

    def test_a_ratio_of_different_units_is_allowed_because_it_declares_its_unit(self, loaded):
        rpu = loaded.metric("revenue_per_unit")
        assert (loaded.metric("revenue").unit, loaded.metric("volume").unit, rpu.unit) == (
            "EUR", "units", "EUR per unit")

    def test_a_file_that_is_not_toml_is_one_plain_error(self, registry):
        with pytest.raises(registry.RegistryError, match="not valid TOML"):
            registry.parse(b"version = \n")

    def test_an_unknown_metric_is_a_plain_error(self, loaded, registry):
        with pytest.raises(registry.RegistryError, match="no metric"):
            loaded.metric("revenue_growth")


# -- Against the committed Lists -----------------------------------------------------------------

def summed(lists, formula) -> list:
    """The accounts on Lists a sum formula reads: by parent, or the one account."""
    if formula.parent is not None:
        return [a for a in lists.accounts if a.code and a.parent == formula.parent]
    return [a for a in lists.accounts if a.code == formula.account]


class TestAgainstTheLists:
    def test_every_parent_and_account_named_is_on_the_committed_lists(self, loaded, lists,
                                                                      registry):
        named = []
        for m in loaded.metrics:
            operands = [m.formula] if isinstance(m.formula, registry.Sum) else (
                [m.formula.numerator, m.formula.denominator]
                if isinstance(m.formula, registry.Ratio) else [])
            named += [o for o in operands if isinstance(o, registry.Sum)]
        assert len(named) == 6
        for s in named:
            assert summed(lists, s), s

    def test_a_sum_reads_leaf_accounts_only(self, loaded, lists, registry):
        # Measures on additive lines only: never a subtotal row (Phase 1 verdict, f).
        for m in loaded.metrics:
            if isinstance(m.formula, registry.Sum):
                assert all(a.section != "Subtotal" for a in summed(lists, m.formula)), m.id

    def test_the_sums_read_the_accounts_the_brief_names(self, loaded, lists):
        codes = {m.id: [a.code for a in summed(lists, m.formula)] for m in loaded.metrics
                 if m.kind != "ratio" and m.formula.__class__.__name__ == "Sum"}
        assert codes == {"revenue": ["4000", "4100"], "cost_of_sales": ["5000", "5100", "5200"],
                         "operating_expenses": ["6000", "6100", "6200", "6300"],
                         "volume": ["VOL"], "headcount": ["HC"]}

    def test_units_and_kinds_agree_with_lists(self, loaded, lists, registry):
        for m in loaded.metrics:
            if isinstance(m.formula, registry.Sum):
                accounts = summed(lists, m.formula)
                assert {a.unit for a in accounts} == {m.unit}, m.id
                assert {a.kind.lower() for a in accounts} == {m.kind}, m.id
        hc = loaded.metric("headcount")
        assert (hc.unit, hc.kind, hc.time_aggregation) == ("FTE", "stock", "closing")
        vol = loaded.metric("volume")
        assert (vol.unit, vol.kind, vol.time_aggregation) == ("units", "flow", "sum")

    def test_polarity_agrees_with_lists_with_one_named_exception(self, loaded, lists, registry):
        # A sum against the polarity of every account it sums; a difference against the
        # subtotal line of the same name on Lists (Gross profit, EBIT). Ratios are not
        # tested against Lists: no line on Lists is a ratio. Headcount is the one
        # exception: Lists grades HC "lower is favourable" for the workbook's own variance
        # column, and the registry never grades a number of people.
        by_name = {a.name: a for a in lists.accounts if a.code}
        checked = []
        for m in loaded.metrics:
            if isinstance(m.formula, registry.Sum):
                polarities = {LISTS_POLARITY[a.polarity] for a in summed(lists, m.formula)}
                if m.id == "headcount":
                    assert (polarities, m.polarity) == ({LOWER}, UNGRADED)
                else:
                    assert polarities == {m.polarity}, m.id
                checked.append(m.id)
            elif isinstance(m.formula, registry.Difference):
                line = by_name[m.label]
                assert line.section == "Subtotal"
                assert LISTS_POLARITY[line.polarity] == m.polarity, m.id
                checked.append(m.id)
        assert checked == ["revenue", "cost_of_sales", "gross_profit", "operating_expenses",
                           "ebit", "volume", "headcount"]


# -- The version lock ------------------------------------------------------------------------------

class TestTheLock:
    def test_the_canonical_definition(self, registry):
        table = {"id": "x", "b": [1, 2], "a": {"z": "é", "y": 1}}
        assert registry.canonical(table) == '{"a":{"y":1,"z":"é"},"b":[1,2],"id":"x"}'
        assert registry.definition_sha256(table) == hashlib.sha256(
            registry.canonical(table).encode("utf-8")).hexdigest()

    def test_the_lock_is_the_current_version_and_definitions(self, registry):
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        assert list(lock) == ["registry_version", "metrics"]
        assert lock["registry_version"] == "1.0.0"
        assert lock["metrics"] == {t["id"]: registry.definition_sha256(t)
                                   for t in raw()["metric"]}

    def test_no_definition_changed_while_the_version_stayed_the_same(self, registry):
        problems = registry.lock_problems(REGISTRY.read_bytes(),
                                          json.loads(LOCK.read_text(encoding="utf-8")))
        assert problems == []

    def test_a_changed_definition_without_a_bump_is_found(self, registry):
        text = changed(REGISTRY.read_text(encoding="utf-8"), 'label = "Revenue"',
                       'label = "Sales"')
        problems = registry.lock_problems(text.encode("utf-8"),
                                          json.loads(LOCK.read_text(encoding="utf-8")))
        assert problems == ["revenue: its definition changed while the version stayed 1.0.0"]

    def test_a_bump_the_lock_has_not_seen_is_found(self, registry):
        text = changed(REGISTRY.read_text(encoding="utf-8"), 'version = "1.0.0"\nowner',
                       'version = "1.1.0"\nowner')
        problems = registry.lock_problems(text.encode("utf-8"),
                                          json.loads(LOCK.read_text(encoding="utf-8")))
        assert problems == ["the lock is for 1.0.0 and the registry is 1.1.0: run "
                            "scripts/lock_registry.py"]

    def test_the_lock_file_is_lf_and_sorted(self):
        data = LOCK.read_bytes()
        assert b"\r" not in data and data.endswith(b"\n")
        lock = json.loads(data)
        assert list(lock["metrics"]) == sorted(lock["metrics"])


class TestTheLockScript:
    @pytest.fixture
    def script(self):
        from scripts import lock_registry
        return lock_registry

    @pytest.fixture
    def copies(self, tmp_path):
        registry_copy, lock_copy = tmp_path / "metrics.toml", tmp_path / "registry.lock.json"
        registry_copy.write_bytes(REGISTRY.read_bytes())
        lock_copy.write_bytes(LOCK.read_bytes())
        return registry_copy, lock_copy

    def run(self, script, copies, capsys):
        status = script.main(["--registry", str(copies[0]), "--lock", str(copies[1])])
        return status, capsys.readouterr().out

    def test_nothing_changed_does_nothing(self, script, copies, capsys):
        before = copies[1].read_bytes()
        status, out = self.run(script, copies, capsys)
        assert status == 0 and "nothing changed" in out
        assert copies[1].read_bytes() == before

    def test_a_definition_changed_without_a_bump_is_refused(self, script, copies, capsys):
        copies[0].write_text(changed(copies[0].read_text(encoding="utf-8"), 'label = "Revenue"',
                                     'label = "Sales"'), encoding="utf-8", newline="\n")
        before = copies[1].read_bytes()
        status, out = self.run(script, copies, capsys)
        assert status == 1 and "refused" in out and "revenue" in out
        assert copies[1].read_bytes() == before

    def test_a_bump_with_a_changelog_entry_rewrites_the_lock(self, script, copies, capsys,
                                                              registry):
        text = changed(copies[0].read_text(encoding="utf-8"), 'label = "Revenue"',
                       'label = "Sales"')
        text = changed(text, 'version = "1.0.0"\nowner', 'version = "1.1.0"\nowner')
        # A second changelog entry, before the first metric.
        text = text.replace("[[metric]]", '[[changelog]]\nversion = "1.1.0"\n'
                            'date = 2026-11-08\nchange = "Revenue relabelled."\n'
                            'approved_by = "E. Marsh, Group Financial Controller, Calder Foods '
                            'Group"\n\n[[metric]]', 1)
        copies[0].write_text(text, encoding="utf-8", newline="\n")
        status, out = self.run(script, copies, capsys)
        assert status == 0 and "1.1.0" in out
        lock = json.loads(copies[1].read_text(encoding="utf-8"))
        assert lock["registry_version"] == "1.1.0"
        assert registry.lock_problems(copies[0].read_bytes(), lock) == []

    def test_a_bump_with_no_changelog_entry_is_refused(self, script, copies, capsys):
        copies[0].write_text(changed(copies[0].read_text(encoding="utf-8"),
                                     'version = "1.0.0"\nowner', 'version = "1.1.0"\nowner'),
                             encoding="utf-8", newline="\n")
        before = copies[1].read_bytes()
        status, out = self.run(script, copies, capsys)
        assert status == 1 and "changelog" in out
        assert copies[1].read_bytes() == before

    def test_a_version_moved_back_is_refused(self, script, copies, capsys):
        lock = json.loads(copies[1].read_text(encoding="utf-8"))
        lock["registry_version"] = "2.0.0"
        copies[1].write_text(json.dumps(lock), encoding="utf-8", newline="\n")
        status, out = self.run(script, copies, capsys)
        assert status == 1 and "refused" in out
