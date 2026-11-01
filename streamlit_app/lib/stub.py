# =============================================================================
# lib/stub.py: a page that is built in a later phase
# =============================================================================

import streamlit as st

from streamlit_app.lib import theme

LATER = "Built in a later phase"


def stub_page(title: str, subtitle: str) -> None:
    """The kit's banner, then one plain line."""
    theme.show(theme.banner(title, subtitle))
    st.markdown(LATER)
