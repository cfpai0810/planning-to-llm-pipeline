# =============================================================================
# lib/stand_in.py: the stand-in figures the kit gallery sends in its test call
# =============================================================================
# Made up, like everything on the gallery. The call sends these names and
# formatted figures as text, and nothing else.
# =============================================================================

SYSTEM = "Reply with one short sentence that repeats the revenue figure you are given."
FIGURES = (("Revenue", "£2.48m"), ("Budget", "£2.59m"))


def prompt() -> str:
    """The text the model is sent: one line per name and formatted figure."""
    return "\n".join(f"{name}: {figure}" for name, figure in FIGURES)
