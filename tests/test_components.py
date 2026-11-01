"""The theme kit, second slice: the rest of the components in brief section 5.4.

provenance_card, refusal_card, breaks_table and acknowledge_form,
evaluation_result, empty_state, and kpi_row (finding 2: tiles in a row are
one height). Every dynamic string goes through the kit's one escaping path.
The breaks table is the kit's own (finding 3): right-aligned tabular
figures, brackets for negatives, column headers with scope, and its own
scroll container, never the page's.
"""

import ast
import re
from decimal import Decimal as D
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from streamlit_app.lib import theme
from streamlit_app.lib.theme import BreakRow

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "streamlit_app"
POISON = "<script>alert(\"x\")</script> & Co's \"Q1\" plan"
ESCAPED = ("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; Co&#x27;s "
           "&quot;Q1&quot; plan")
HASH = "9f2c1e0a7b3d4c5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6"
ROWS = [
    BreakRow("high", "Revenue", D("2590000"), D("2477550"), "Actuals below plan by more than 4%",
             "open"),
    BreakRow("medium", "Cost of sales", D("1577000"), D("1611180"), "Over plan", "investigated"),
    BreakRow("low", "Opex", D("893000"), D("875000"), "Under plan", "dismissed"),
]


def _provenance(**changes):
    fields = dict(specification="Revenue for June 2026 against plan, by region",
                  sql="SELECT region, SUM(amount) AS revenue\nFROM actuals\nGROUP BY region",
                  sql_hash=HASH, row_count=1284, source_batch="batch-2026-06-30-01",
                  reconciliation="reconciled", registry_version="registry v3")
    fields.update(changes)
    return theme.provenance_card(**fields)


class TestFormatAmount:
    @pytest.mark.parametrize("value, shown", [(D("112450"), "112,450"), (D("-112450"), "(112,450)"),
                                              (0, "0"), (-5, "(5)"), (D("1234567.4"), "1,234,567")])
    def test_thousands_and_brackets_for_a_negative(self, value, shown):
        assert theme.format_amount(value) == shown


class TestProvenanceCard:
    def test_every_field_is_shown(self):
        out = _provenance()
        for text in ("Revenue for June 2026 against plan, by region", "1,284",
                     "batch-2026-06-30-01", "registry v3", "Reconciled"):
            assert text in out, text

    def test_the_sql_is_a_code_block_and_the_full_hash_a_copyable_line(self):
        out = _provenance()
        assert '<pre class="p7-code"><code>SELECT region, SUM(amount) AS revenue\nFROM actuals' in out
        assert f'<code class="p7-hash">sha256:{HASH}</code>' in out
        assert "title=" not in out                       # never on hover only

    @pytest.mark.parametrize("state, kind, label", [
        ("reconciled", "info", "Reconciled"), ("breaks", "warning", "Breaks open"),
        ("not_run", "neutral", "Not reconciled yet")])
    def test_the_reconciliation_status_is_a_labelled_chip(self, state, kind, label):
        assert theme.status_chip(kind, label) in _provenance(reconciliation=state)

    def test_an_unknown_reconciliation_status_is_refused(self):
        with pytest.raises(ValueError):
            _provenance(reconciliation="fine")


class TestRefusalCard:
    def test_a_code_its_wording_and_suggestions(self):
        out = theme.refusal_card("unknown_metric", "No metric of that name is defined.",
                                 ["Ask about revenue, cost of sales or opex."])
        assert '<code class="p7-code-inline">unknown_metric</code>' in out
        assert "No metric of that name is defined." in out
        assert "<li>Ask about revenue, cost of sales or opex.</li>" in out
        assert theme.status_chip("neutral", "Not answered") in out

    def test_never_in_error_red(self):
        out = theme.refusal_card("ambiguous_period", "Which month?", [])
        assert "adverse" not in out and "<ul" not in out

    @pytest.mark.parametrize("code", ["Unknown Metric", "", "drop table;"])
    def test_the_code_is_a_fixed_reason_code(self, code):
        with pytest.raises(ValueError):
            theme.refusal_card(code, "text", [])


class TestBreaksTable:
    def test_headers_have_scope_and_figures_are_right_aligned(self):
        out = theme.breaks_table(ROWS, caption="Figures in GBP")
        headers = re.findall(r'<th( class="num")? scope="col">([^<]+)</th>', out)
        assert [h for _, h in headers] == ["Severity", "Member", "Plan", "Actual", "Difference",
                                           "Break", "Status"]
        assert [bool(c) for c, _ in headers] == [False, False, True, True, True, False, False]
        assert out.count('<td class="num">') == 3 * len(ROWS)

    def test_negatives_are_in_brackets(self):
        out = theme.breaks_table(ROWS, caption="Figures in GBP")
        assert '<td class="num">(112,450)</td>' in out
        assert '<td class="num">34,180</td>' in out and '<td class="num">(18,000)</td>' in out

    def test_severity_and_status_are_labelled_chips(self):
        out = theme.breaks_table(ROWS, caption="Figures in GBP")
        for kind, label in (("adverse", "High"), ("warning", "Medium"), ("neutral", "Low"),
                            ("warning", "Open"), ("info", "Investigated"),
                            ("neutral", "Dismissed")):
            assert theme.status_chip(kind, label) in out

    def test_its_own_scroll_container_never_the_page(self):
        out = theme.breaks_table(ROWS, caption="Figures in GBP")
        assert out.startswith('<div class="p7-table-wrap" role="region" aria-label="Breaks" '
                              'tabindex="0"><table class="p7-table">')
        assert "<caption>Figures in GBP</caption>" in out

    def test_an_unknown_severity_or_status_is_refused(self):
        with pytest.raises(ValueError):
            theme.breaks_table([BreakRow("severe", "X", 1, 2, "t")], caption="c")
        with pytest.raises(ValueError):
            theme.breaks_table([BreakRow("low", "X", 1, 2, "t", "closed")], caption="c")


class TestEvaluationResult:
    def test_the_rate_never_comes_without_its_interval_and_n(self):
        with pytest.raises(TypeError):
            theme.evaluation_result("Golden set", 38)       # no n
        with pytest.raises(TypeError):
            theme.evaluation_result("Golden set")            # no k, no n

    def test_one_line_with_the_interval(self):
        out = theme.evaluation_result("Golden set", 38, 40)
        text = re.sub(r"<[^>]+>", "", out)
        assert text == "Golden set: 95.0% (38 of 40); 95% interval 83.5% to 98.6%"
        assert '<span class="p7-eval-figures">' in out

    def test_impossible_counts_are_refused(self):
        with pytest.raises(ValueError):
            theme.evaluation_result("Golden set", 41, 40)


class TestEmptyStateAndKpiRow:
    def test_a_calm_placeholder_with_or_without_an_action(self):
        plain = theme.empty_state("No questions yet", "Ask a question to see the figures.")
        assert plain == ('<div class="p7-empty"><p class="p7-empty-title">No questions yet</p>'
                         '<p>Ask a question to see the figures.</p></div>')
        with_action = theme.empty_state("No data", "Nothing is loaded.", action="Load the sample")
        assert '<p class="p7-empty-action">Next: Load the sample</p>' in with_action

    def test_tiles_in_a_row_share_one_grid(self):
        row = theme.kpi_row(theme.kpi_tile("Revenue", "£2.48m"),
                            theme.kpi_tile("Opex", "£875k", versus="£18k below budget",
                                           kind="favourable"))
        assert row.startswith('<div class="p7-tile-row"><div class="p7-tile">')
        assert row.count('<div class="p7-tile">') == 2


class TestNotice:
    def test_a_labelled_chip_then_its_text(self):
        assert theme.notice("info", "Stand-in", "Nothing is recorded.") == (
            '<p class="p7-notice"><span class="p7-chip p7-chip-info">Stand-in</span> '
            'Nothing is recorded.</p>')


class TestEscapingInEveryNewComponent:
    RENDERS = {
        "provenance specification": lambda s: _provenance(specification=s),
        "provenance sql": lambda s: _provenance(sql=s),
        "provenance batch": lambda s: _provenance(source_batch=s),
        "provenance registry": lambda s: _provenance(registry_version=s),
        "refusal text": lambda s: theme.refusal_card("unknown_metric", s, []),
        "refusal suggestion": lambda s: theme.refusal_card("unknown_metric", "t", [s]),
        "break member": lambda s: theme.breaks_table([BreakRow("low", s, 1, 2, "t")], "c"),
        "break text": lambda s: theme.breaks_table([BreakRow("low", "m", 1, 2, s)], "c"),
        "break caption": lambda s: theme.breaks_table(ROWS, caption=s),
        "evaluation label": lambda s: theme.evaluation_result(s, 1, 2),
        "empty title": lambda s: theme.empty_state(s, "t"),
        "empty text": lambda s: theme.empty_state("t", s),
        "empty action": lambda s: theme.empty_state("t", "t", action=s),
        "notice text": lambda s: theme.notice("info", "Stand-in", s),
    }

    @pytest.mark.parametrize("where", list(RENDERS))
    def test_a_poisoned_name_renders_as_text(self, where):
        out = self.RENDERS[where](POISON)
        assert "<script>" not in out and ESCAPED in out

    def test_escaping_happens_in_one_place(self):
        tree = ast.parse((APP / "lib" / "theme.py").read_text(encoding="utf-8"))
        functions = {f.name: f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}
        escapes = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute) and n.func.attr == "escape"]
        assert len(escapes) == 1 and escapes[0] in ast.walk(functions["_esc"])
        callers = {name for name, f in functions.items() if name != "_esc"
                   for n in ast.walk(f) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name) and n.func.id == "_esc"}
        assert callers == {"_element"}


class TestAcknowledgeForm:
    def test_name_action_and_note_then_a_stand_in_confirmation(self, monkeypatch):
        monkeypatch.setenv("P7_GALLERY", "1")
        at = AppTest.from_file(str(APP / "Home.py"), default_timeout=60).run()
        at.switch_page("views/_gallery.py").run()
        at.text_input(key="ack_gallery_name").input("Robin Sample").run()
        at.radio(key="ack_gallery_action").set_value("Investigated").run()
        at.text_area(key="ack_gallery_note").input(POISON).run()
        assert at.radio(key="ack_gallery_action").options == ["Accepted", "Dismissed",
                                                             "Investigated"]
        at.button(key="ack_gallery_submit").click().run()
        assert not at.exception, at.exception
        shown = "\n".join(e.proto.body for e in at.get("html"))
        assert "Stand-in data: nothing is recorded" in shown
        assert "Robin Sample" in shown and "Investigated" in shown and ESCAPED in shown
