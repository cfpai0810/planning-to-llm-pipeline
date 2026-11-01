# =============================================================================
# lib/stub.py: a page that is built in a later phase
# =============================================================================

import streamlit as st

LATER = "Built in a later phase"


def stub_page(title: str, subtitle: str) -> None:
    """The banner, then one plain line. The banner is provisional until the theme kit (0c)."""
    st.title(title)
    st.caption(subtitle)
    st.markdown(LATER)
