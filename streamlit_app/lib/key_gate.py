# =============================================================================
# lib/key_gate.py: the bring-your-own-key gate, rendered once per app
# =============================================================================
# Home.py renders the gate before the page runs, so every page has it and no
# page renders its own. It sits in a closed sidebar expander, and one line
# below it says whether live mode is on. Nothing in Phase 0 needs a key.
#
# The key is held in this session's state (the server's memory) under a
# non-widget key, KEPT_KEY, so it survives a page change (item 4); the
# widget's own state is restored from it on every run. It is passed to the
# client built with it, and nowhere else: never to a file, a log, the page
# or a message. Every sentence the gate says about the key is tested
# (tests/test_key_gate.py, items 40 and 45).
# =============================================================================

import streamlit as st

from config import MODEL
from streamlit_app.lib import claude_client

GATE_LABEL = "Run it live with your own key (optional)"
WIDGET_KEY = "p7_api_key_input"
KEPT_KEY = "p7_api_key"          # a non-widget key: survives a page change

KEY_SENTENCES = (
    "Your key is held in the server's memory for this session only.",
    "It is used to make the call, and it is never stored or logged.",
    "Only the formatted figures and names go to the model, never your files.",
    "A model run uses your own key, so your Anthropic account is billed for it.",
)
LIVE_ON = "Live mode is on for this session."
LIVE_OFF = "Live mode is off: no key is held for this session."
MISTAKEN_PASTE = ("That does not look like a complete Anthropic key. It should start with "
                  "sk-ant- and be the full string.")
KEY_LOOKS_COMPLETE = "Key looks complete. It is checked when you first run the model."


def _keep() -> None:
    st.session_state[KEPT_KEY] = st.session_state.get(WIDGET_KEY, "")


def held_key() -> str:
    """The key this session holds, or an empty string."""
    return st.session_state.get(KEPT_KEY, "")


def live() -> bool:
    return claude_client.looks_like_anthropic_key(held_key())


def render_key_gate() -> None:
    """The gate in a closed sidebar expander, then the live-mode line. Home.py only."""
    if KEPT_KEY in st.session_state:
        st.session_state[WIDGET_KEY] = st.session_state[KEPT_KEY]
    with st.sidebar.expander(GATE_LABEL, expanded=False):
        st.caption("Nothing in Phase 0 needs a key. With your own key, the model (Claude, via "
                   f"the Anthropic API) runs as {MODEL}.")
        st.text_input("Anthropic API key", type="password", placeholder="sk-ant-...",
                      key=WIDGET_KEY, on_change=_keep)
        st.caption(" ".join(KEY_SENTENCES))
        if held_key() and not live():
            st.warning(MISTAKEN_PASTE)
        elif live():
            st.success(KEY_LOOKS_COMPLETE)
    st.sidebar.caption(LIVE_ON if live() else LIVE_OFF)


def session_client():
    """A client built from this session's key, or None when no complete key is held."""
    return claude_client.build_client(held_key()) if live() else None
