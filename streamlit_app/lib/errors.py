# =============================================================================
# lib/errors.py: the pages' thin wrapper around the one error mapping
# =============================================================================
# A visitor never sees a traceback or an exception's name. A failed model
# call is shown as the line src/errors.py words for its code, the same line
# the PDF and the audit will use.
# =============================================================================

import streamlit as st

from src import errors


def failure_line(code: str) -> str:
    """The plain line for a failed call, ending with its code."""
    return errors.line(code)


def show_failure(code: str) -> None:
    """A failed call, on screen, in its plain words and its code."""
    st.warning(failure_line(code), icon=":material/error:")
