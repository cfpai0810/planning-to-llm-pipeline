# =============================================================================
# lib/theme.py: the theme kit: tokens, CSS and components
# =============================================================================
"""
One visual system, proven here before Step 8 applies it to P1 to P6. Every
colour, font and size is a named token below; the CSS, the components and
.streamlit/config.toml take only tokens (the tests check every hex value), so
a colour changes in one place.

Colour carries status only, and a number is never coloured: the chip beside
it carries the status, and every chip carries a word, so colour is never
alone. Source Serif 4 sets the headings; Inter sets the body, the controls and
the data, with tabular figures only where numbers line up. The CSS has no
motion of any kind, so a reduced-motion preference needs no special case.

Every component takes plain data and returns markup built by _element, the
one place a dynamic string is HTML-escaped: workbook member names will reach
these pages, and a poisoned name must render as text.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional

import streamlit as st

# -- Colour tokens ----------------------------------------------------------------

INK = "#1E2A2E"              # text: 14.1 on paper
PAPER = "#FBFAF7"            # the page
SURFACE = "#FFFFFF"          # cards, tables, inputs, the sidebar
NAVY = "#1A3A5C"             # banner, primary controls, focus ring: 11.2 on paper
NAVY_HOVER = "#122940"       # a primary control on hover
BANNER_SUB = "#AACCEE"       # the subtitle on the banner: 7.0 on navy
MUTED = "#5B666B"            # secondary text: 5.7 on paper, 5.9 on white
RULE = "#D5D9DB"             # decorative lines only
CONTROL_BORDER = "#6A7378"   # input and button borders: 4.6 on paper
ON_DARK = "#FFFFFF"          # text on the navy banner and on the solid chips

FAVOURABLE_TEXT, FAVOURABLE_TINT = "#2E6B3F", "#E7F2EA"
ADVERSE_TEXT, ADVERSE_TINT = "#9B2C2C", "#FBEAEA"
NEUTRAL_TEXT, NEUTRAL_TINT = "#4A5560", "#EEF1F4"
WARNING_TEXT, WARNING_TINT = "#7A4200", "#FBF0E3"
INFO_TEXT, INFO_TINT = "#1A3A5C", "#E6EDF5"
DRAFT_FILL = "#B45F06"       # the PDF's own DRAFT colour
FINAL_FILL = "#2E6B3F"       # the PDF's own FINAL colour


@dataclass(frozen=True)
class Status:
    text: str
    background: str


STATUS = {
    "favourable": Status(FAVOURABLE_TEXT, FAVOURABLE_TINT),
    "adverse": Status(ADVERSE_TEXT, ADVERSE_TINT),
    "neutral": Status(NEUTRAL_TEXT, NEUTRAL_TINT),
    "warning": Status(WARNING_TEXT, WARNING_TINT),
    "info": Status(INFO_TEXT, INFO_TINT),
    "draft": Status(ON_DARK, DRAFT_FILL),
    "final": Status(ON_DARK, FINAL_FILL),
}

# A KPI tile's comparison carries its Fav or (Adv) word in a chip.
TILE_WORDS = {"favourable": "Fav", "adverse": "(Adv)"}

TOKEN_VALUES = {v.upper() for v in (
    INK, PAPER, SURFACE, NAVY, NAVY_HOVER, BANNER_SUB, MUTED, RULE, CONTROL_BORDER, ON_DARK,
    *(c for s in STATUS.values() for c in (s.text, s.background)))}


@dataclass(frozen=True)
class Pair:
    foreground: str
    background: str
    kind: str        # "text" needs 4.5, "ui" (boundaries, the focus ring) needs 3
    use: str


# Every foreground and background the CSS and the components put together.
PAIRS = (
    Pair(INK, PAPER, "text", "body text on the page"),
    Pair(INK, SURFACE, "text", "text on a card, input or table"),
    Pair(MUTED, PAPER, "text", "captions on the page"),
    Pair(MUTED, SURFACE, "text", "KPI tile label on its card"),
    Pair(NAVY, PAPER, "text", "links, secondary button on hover"),
    Pair(NAVY, SURFACE, "text", "links on a card or in the sidebar"),
    Pair(ON_DARK, NAVY, "text", "banner title, primary button"),
    Pair(ON_DARK, NAVY_HOVER, "text", "primary button on hover"),
    Pair(BANNER_SUB, NAVY, "text", "banner subtitle"),
    Pair(CONTROL_BORDER, PAPER, "ui", "input and button border on the page"),
    Pair(CONTROL_BORDER, SURFACE, "ui", "input border against its fill"),
    Pair(NAVY, PAPER, "ui", "focus ring on the page"),
    Pair(NAVY, SURFACE, "ui", "focus ring on a card or input"),
    *(Pair(s.text, s.background, "text", f"{kind} chip") for kind, s in STATUS.items()),
    Pair(RULE, SURFACE, "decorative", "card border, table rules"),
    Pair(RULE, PAPER, "decorative", "dividers on the page"),
)

# -- Type, spacing and shape ------------------------------------------------------------

SANS = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
SERIF = "'Source Serif 4', Georgia, 'Times New Roman', serif"
FONTS_URL = ("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700"
             "&family=Source+Serif+4:wght@400;600;700&display=swap")

BODY_PX = 16
BODY_LINE_HEIGHT = 1.5
HEADING_LINE_HEIGHT = 1.25
HEADING_REM = {"h1": 2, "h2": 1.5, "h3": 1.25, "h4": 1.0625}
CAPTION_PX = 14              # captions and labels: 13 px or more, never smaller
CHIP_PX = 13
TABLE_PX = 15
TILE_VALUE_REM = 1.75

SPACING = (4, 8, 12, 16, 24, 32, 48)   # the only spacing values
CARD_PAD, CARD_PAD_WIDE = 16, 24
BLOCK_GAP = 24
PHONE_GUTTER = 16
CARD_RADIUS = 6
CHIP_RADIUS = 999                      # fully rounded
FOCUS_WIDTH, FOCUS_OFFSET = 2, 2

PHONE_MAX_PX = 640
WIDE_MIN_PX = 1024


def _status_rules() -> str:
    return "\n".join(f".p7-chip-{kind} {{ color: {s.text}; background: {s.background}; }}"
                     for kind, s in STATUS.items())


def inject_css() -> str:
    """The theme's CSS, from the tokens only, as one style element."""
    ring = (f"outline: {FOCUS_WIDTH}px solid {NAVY} !important; "
            f"outline-offset: {FOCUS_OFFSET}px !important;")
    heading_sizes = "\n".join(f"{h} {{ font-size: {rem}rem; }}" for h, rem in HEADING_REM.items())
    return f"""<style>
@import url('{FONTS_URL}');

html, body, .stApp, p, li, label, caption {{ font-family: {SANS}; }}
body {{ font-size: {BODY_PX}px; line-height: {BODY_LINE_HEIGHT}; }}
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li {{
  line-height: {BODY_LINE_HEIGHT};
}}

/* Controls and table cells: Streamlit's own classes set fonts too, so these win.
   In Streamlit 1.60 a selectbox is a combobox input, so the input rule covers it. */
input, textarea, select, button, th, td, [role="option"] {{
  font-family: {SANS} !important;
}}
h1, h2, h3, h4, h5, h6 {{
  font-family: {SERIF} !important;
  line-height: {HEADING_LINE_HEIGHT};
  color: {INK};
}}
{heading_sizes}
/* Streamlit fades captions with opacity; opaque MUTED keeps 5.7 on paper. */
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{
  font-size: {CAPTION_PX}px;
  color: {MUTED};
  opacity: 1;
}}

/* Tables: 15 px, tabular figures, so numbers line up. Streamlit fades st.table
   headers to 60 per cent ink (4.0 on paper); they stay in ink. */
table, table th, table td {{
  font-size: {TABLE_PX}px !important;
  font-variant-numeric: tabular-nums;
}}
[data-testid="stTable"] th, [data-testid="stTable"] th p {{ color: {INK}; }}

/* Every control shows a 2 px navy ring when focused from the keyboard. A text
   box rings its whole field rather than the bare input inside it. */
*:focus-visible {{ {ring} }}
[data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stSelectbox"] [role="group"]:focus-within {{ {ring} }}
[data-testid="stTextInputRootElement"] input:focus-visible,
[data-testid="stTextAreaRootElement"] textarea:focus-visible,
[data-testid="stSelectbox"] input:focus-visible {{ outline: none !important; }}

/* Buttons: navy fill (primaryColor), darker on hover; secondary in ink. */
[data-testid="stBaseButton-primary"]:hover {{
  background: {NAVY_HOVER}; border-color: {NAVY_HOVER}; color: {ON_DARK};
}}
[data-testid="stBaseButton-secondary"] {{ border-color: {CONTROL_BORDER}; color: {INK}; }}
[data-testid="stBaseButton-secondary"]:hover {{ border-color: {NAVY}; color: {NAVY}; }}

/* 24 px between blocks. */
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] {{ gap: {BLOCK_GAP}px; }}

/* The banner */
.p7-banner {{
  background: {NAVY};
  border-radius: {CARD_RADIUS}px;
  padding: {CARD_PAD_WIDE}px;
  margin: 0;
}}
.p7-banner h1 {{
  color: {ON_DARK};
  font-size: {HEADING_REM["h1"]}rem;
  font-weight: 600;
  margin: 0;
  padding: 0;
}}
.p7-banner p {{
  color: {BANNER_SUB};
  font-size: {BODY_PX}px;
  line-height: {BODY_LINE_HEIGHT};
  margin: 8px 0 0;
}}

/* KPI tile: the value in ink and tabular figures; the chip carries the status. */
.p7-tile {{
  background: {SURFACE};
  border: 1px solid {RULE};
  border-radius: {CARD_RADIUS}px;
  padding: {CARD_PAD}px;
}}
.p7-tile-label {{ color: {MUTED}; font-size: {CAPTION_PX}px; font-weight: 500; margin: 0 0 4px; }}
.p7-tile-value {{ color: {INK}; font-size: {TILE_VALUE_REM}rem; font-weight: 600; line-height: {HEADING_LINE_HEIGHT}; font-variant-numeric: tabular-nums; }}
.p7-tile-versus {{
  color: {INK};
  font-size: {CAPTION_PX}px;
  margin: 8px 0 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}}

/* Status chip: a word on its tint, never colour alone. */
.p7-chip {{
  display: inline-block;
  border-radius: {CHIP_RADIUS}px;
  padding: 4px 12px;
  font-size: {CHIP_PX}px;
  font-weight: 600;
  line-height: {HEADING_LINE_HEIGHT};
  white-space: nowrap;
}}
{_status_rules()}
.p7-row {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }}

@media (min-width: {WIDE_MIN_PX}px) {{
  .p7-tile {{ padding: {CARD_PAD_WIDE}px; }}
}}
@media (max-width: {PHONE_MAX_PX}px) {{
  [data-testid="stMainBlockContainer"] {{ padding-left: {PHONE_GUTTER}px; padding-right: {PHONE_GUTTER}px; }}
  .p7-banner {{ padding: {CARD_PAD}px; }}
  .p7-banner h1 {{ font-size: {HEADING_REM["h2"]}rem; }}
}}
</style>"""


# -- Components ------------------------------------------------------------------------

class _Markup(str):
    """Markup that _element built: safe as it stands, never escaped twice."""


def _element(tag: str, css_class: str, *content) -> _Markup:
    """The one place markup is built: a plain string is escaped here; built markup is kept."""
    inner = "".join(part if isinstance(part, _Markup) else html.escape(str(part), quote=True)
                    for part in content)
    attribute = f' class="{css_class}"' if css_class else ""
    return _Markup(f"<{tag}{attribute}>{inner}</{tag}>")


def banner(title: str, subtitle: str) -> str:
    """The navy band at the top of a page: a serif title and a subtitle."""
    parts = [_element("h1", "", title)]
    if subtitle:
        parts.append(_element("p", "", subtitle))
    return _element("div", "p7-banner", *parts)


def status_chip(kind: str, label: str) -> str:
    """A chip of one status kind; the label is required, so a chip is never colour alone."""
    if kind not in STATUS:
        raise ValueError(f"unknown status kind: {kind!r}")
    if not str(label).strip():
        raise ValueError("a status chip needs a label")
    return _element("span", f"p7-chip p7-chip-{kind}", label)


def kpi_tile(label: str, value: str, versus: Optional[str] = None,
             kind: Optional[str] = None) -> str:
    """A label, a value in tabular figures, and an optional comparison with its Fav or (Adv) chip."""
    if kind is not None and kind not in TILE_WORDS:
        raise ValueError(f"a KPI tile's kind is favourable or adverse, not {kind!r}")
    if kind is not None and versus is None:
        raise ValueError("a status needs the comparison it describes")
    parts = [_element("div", "p7-tile-label", label), _element("div", "p7-tile-value", value)]
    if versus is not None:
        line = [_element("span", "p7-tile-versus-text", versus)]
        if kind is not None:
            line.append(status_chip(kind, TILE_WORDS[kind]))
        parts.append(_element("div", "p7-tile-versus", *line))
    return _element("div", "p7-tile", *parts)


def row(*items: str) -> str:
    """Components side by side, wrapping on a narrow screen."""
    return _element("div", "p7-row", *items)


# -- Rendering ------------------------------------------------------------------------

def apply() -> None:
    """The theme's CSS for this run. A style-only st.html takes no space on the page."""
    st.html(inject_css())


def show(markup: str) -> None:
    """Render a component's markup."""
    st.html(markup)


def full_width() -> None:
    """For a page whose tables need the width. Streamlit 1.60 lets a page override the
    shell's centred layout for its own run; text pages keep the centred reading width."""
    st.set_page_config(layout="wide")
