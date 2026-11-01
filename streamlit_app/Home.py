# =============================================================================
# Home.py: the app shell: page settings and navigation (Streamlit entry point)
# =============================================================================
# Run locally: streamlit run streamlit_app/Home.py
# st.navigation with st.Page, the page structure of P2 to P5. The pages live
# in views/, never pages/: a folder named pages/ beside this script would let
# Streamlit run a page without this shell (its older multipage mode). Paths
# resolve from this file, so the app runs from any working directory.
# =============================================================================

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib import key_gate, nav, theme  # noqa: E402

VIEWS = Path(__file__).resolve().parent / "views"

# "auto" keeps the sidebar closed on a phone, so the page itself is the first
# view (finding 53); a desktop still opens with it shown. "centered" gives text
# pages their reading width (46rem); a page with tables widens itself for its
# own run with theme.full_width().
st.set_page_config(
    page_icon=":material/account_tree:",
    layout="centered",
    initial_sidebar_state="auto",
)
theme.apply()

pg = st.navigation([
    st.Page(str(VIEWS / page.file), title=page.title, icon=page.icon,
            url_path=page.url_path, default=page.url_path is None)
    for page in nav.pages()
])
# Once per app, before the page runs: every page has the gate, and no page
# renders its own, so a key entered anywhere holds on every page (item 4).
key_gate.render_key_gate()
pg.run()
