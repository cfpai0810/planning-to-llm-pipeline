# =============================================================================
# views/_gallery.py: the kit gallery, on stand-in data
# =============================================================================
# Every component of the theme kit in every state, for the screenshots and the
# AppTest. In the navigation only when P7_GALLERY=1 (lib/nav.py), so it never
# ships. Every name and figure here is made up. The page holds a table, so it
# takes the full width, as every table page will.
# =============================================================================

import streamlit as st

from streamlit_app.lib import theme

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

theme.full_width()
theme.show(theme.banner("Kit gallery", "Stand-in data: every component of the theme kit, "
                                       "in every state"))
st.caption("Stand-in data. Every name and figure on this page is made up.")

st.markdown("#### Status chips")
theme.show(theme.row(*(theme.status_chip(kind, label)
                       for kind, labels in CHIP_LABELS.items() for label in labels)))

st.markdown("#### KPI tiles")
tiles = [
    theme.kpi_tile("Revenue", "£2.48m"),
    theme.kpi_tile("Units shipped", "41,200", versus="1,800 above last year"),
    theme.kpi_tile("Opex", "£612k", versus="£18k below budget", kind="favourable"),
    theme.kpi_tile("Revenue", "£2.48m", versus="£112k below budget", kind="adverse"),
]
for column, tile in zip(st.columns(len(tiles)), tiles):
    with column:
        theme.show(tile)
st.caption("No comparison; a comparison with no status (its direction is not known); "
           "favourable; adverse. The figure is never coloured.")

st.markdown("#### Banners")
theme.show(theme.banner("A long page title that wraps on a phone without breaking a word",
                        "Planning-to-Warehouse-to-LLM Pipeline"))
theme.show(theme.banner("No subtitle", ""))

st.markdown("#### A poisoned name renders as text")
theme.show(theme.banner(POISON, POISON))
theme.show(theme.row(theme.kpi_tile(POISON, "£1k", versus=POISON, kind="adverse"),
                     theme.status_chip("warning", POISON)))

st.markdown("#### Controls and a table")
st.text_input("Member name", value="Revenue: stand-in")
st.text_area("Reviewer note", value="A stand-in note.")
st.selectbox("Scenario", ["Budget", "Forecast", "Actual"])
st.button("Run the stand-in check", type="primary")
st.button("A secondary action")
st.table([
    {"Member": "Revenue", "Plan": "2,590,000", "Actual": "2,477,550", "Variance": "(112,450)"},
    {"Member": "Cost of sales", "Plan": "1,577,000", "Actual": "1,611,180", "Variance": "(34,180)"},
    {"Member": "Opex", "Plan": "630,000", "Actual": "612,000", "Variance": "18,000"},
])
