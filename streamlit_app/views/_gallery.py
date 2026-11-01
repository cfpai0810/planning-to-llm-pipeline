# =============================================================================
# views/_gallery.py: the kit gallery, on stand-in data
# =============================================================================
# Every component of the theme kit in every state, for the screenshots and the
# AppTest. In the navigation only when P7_GALLERY=1 (lib/nav.py), so it never
# ships. Every name and figure here is made up. The page holds a table, so it
# takes the full width, as every table page will. Its model call is the one
# place in Phase 0 a call can be made, with the key in the sidebar: it proves
# a failed call is shown in plain words with its code (brief 4.1).
# =============================================================================

from decimal import Decimal

import streamlit as st

from src import model_call
from streamlit_app.lib import errors, key_gate, stand_in, theme
from streamlit_app.lib.theme import BreakRow

POISON = "<script>alert(\"x\")</script> & Co's \"Q1\" plan"
CHIP_LABELS = {
    "favourable": ["Favourable"],
    "adverse": ["(Adverse)"],
    "neutral": ["Neutral", "Not meaningful"],
    "warning": ["Check", "Awaiting acknowledgement"],
    "info": ["Promoted", "Source"],
    "draft": ["DRAFT"],
    "final": ["FINAL"],
}
SQL = ("SELECT region, SUM(amount) AS revenue\nFROM actuals\n"
       "WHERE period = '2026-06' AND scenario = 'actual'\nGROUP BY region\nORDER BY region")
SQL_HASH = "9f2c1e0a7b3d4c5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6"
BREAKS = [
    BreakRow("high", "Revenue", Decimal("2590000"), Decimal("2477550"),
             "Actuals are below plan by more than the 4% tolerance.", "open"),
    BreakRow("medium", "Cost of sales", Decimal("1577000"), Decimal("1611180"),
             "Over plan; the second source agrees.", "investigated"),
    BreakRow("low", "Opex", Decimal("893000"), Decimal("875000"),
             "Under plan, within tolerance.", "dismissed"),
    BreakRow("low", "Other income", Decimal("12000"), Decimal("12450"),
             "A rounding difference between the two sources.", "accepted"),
]

theme.full_width()
theme.show(theme.banner("Kit gallery", "Stand-in data: every component of the theme kit, "
                                       "in every state"))
st.caption("Stand-in data. Every name and figure on this page is made up.")

st.markdown("#### Status chips")
theme.show(theme.row(*(theme.status_chip(kind, label)
                       for kind, labels in CHIP_LABELS.items() for label in labels)))

st.markdown("#### KPI tiles")
theme.show(theme.kpi_row(
    theme.kpi_tile("Revenue", "£2.48m"),
    theme.kpi_tile("Units shipped", "41,200", versus="1,800 above last year"),
    theme.kpi_tile("Opex", "£875k", versus="£18k below budget", kind="favourable"),
    theme.kpi_tile("Revenue", "£2.48m", versus="£112k below budget", kind="adverse"),
))
st.caption("No comparison; a comparison with no status (its direction is not known); "
           "favourable; adverse. The figure is never coloured, and tiles in a row are one "
           "height.")

st.markdown("#### Banners")
theme.show(theme.banner("A long page title that wraps on a phone without breaking a word",
                        "Planning-to-Warehouse-to-LLM Pipeline"))
theme.show(theme.banner("No subtitle", ""))

st.markdown("#### Provenance")
for state in ("reconciled", "breaks", "not_run"):
    theme.show(theme.provenance_card(
        specification="Revenue for June 2026 against plan, by region",
        sql=SQL, sql_hash=SQL_HASH, row_count=1284, source_batch="batch-2026-06-30-01",
        reconciliation=state, registry_version="registry v3"))

st.markdown("#### Refusals")
theme.show(theme.refusal_card("unknown_metric", "No metric of that name is defined in the "
                              "registry, so the question was not answered.",
                              ["Ask about revenue, cost of sales or opex.",
                               "See the metric list on How the pipeline works."]))
theme.show(theme.refusal_card("ambiguous_period", "The question names no period, and the plan "
                              "holds twelve.", []))

st.markdown("#### Breaks")
theme.show(theme.breaks_table(BREAKS, caption="Stand-in data. Figures in GBP; a negative "
                              "difference is in brackets."))
acknowledged = theme.acknowledge_form("Revenue, actuals below plan", key="ack_gallery")
if acknowledged:
    theme.show(theme.notice(
        "info", "Stand-in",
        f"Stand-in data: nothing is recorded. {acknowledged['name'] or 'No name'} chose "
        f"{acknowledged['action']}. Note: {acknowledged['note'] or 'none'}."))

st.markdown("#### Evaluation results")
for label, k, n in (("All 40 answered", 40, 40), ("Golden set", 38, 40),
                    ("Comparison model", 36, 40), ("A weaker run", 32, 40)):
    theme.show(theme.evaluation_result(label, k, n))

st.markdown("#### Empty states")
theme.show(theme.empty_state("No questions yet", "Ask a question to see the figures and "
                             "where they came from."))
theme.show(theme.empty_state("No data loaded", "Nothing is loaded for this session.",
                             action="Load the stand-in workbook"))

st.markdown("#### A poisoned name renders as text")
theme.show(theme.banner(POISON, POISON))
theme.show(theme.row(theme.kpi_tile(POISON, "£1k", versus=POISON, kind="adverse"),
                     theme.status_chip("warning", POISON)))
theme.show(theme.refusal_card("unknown_metric", POISON, [POISON]))
theme.show(theme.breaks_table([BreakRow("low", POISON, 1, 2, POISON)], caption=POISON))
theme.show(theme.evaluation_result(POISON, 1, 2))
theme.show(theme.empty_state(POISON, POISON, action=POISON))

st.markdown("#### Controls")
st.text_input("Member name", value="Revenue: stand-in")
st.text_area("Reviewer note", value="A stand-in note.")
st.selectbox("Scenario", ["Budget", "Forecast", "Actual"])
st.button("Run the stand-in check", type="primary")
st.button("A secondary action")

st.markdown("#### A model call")
st.caption("Sends the stand-in figures as text, with the key held in the sidebar. A failed "
           "call shows its plain line and its code.")
if st.button("Run a stand-in call", key="gallery_call", disabled=not key_gate.live()):
    result = model_call.call_model(key_gate.session_client(), system=stand_in.SYSTEM,
                                   user=stand_in.prompt())
    if result.ok:
        st.text(result.text)
    else:
        errors.show_failure(result.code)
