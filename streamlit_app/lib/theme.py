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
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Sequence

import streamlit as st

from src.stats import percent, wilson_interval

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
    Pair(INK, INFO_TINT, "text", "sidebar: the current page"),
    Pair(INK, NEUTRAL_TINT, "text", "sidebar link on hover; SQL and hashes"),
    Pair(RULE, SURFACE, "decorative", "card border, table rules"),
    Pair(RULE, PAPER, "decorative", "dividers on the page"),
)

# -- Type, spacing and shape ------------------------------------------------------------

SANS = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
SERIF = "'Source Serif 4', Georgia, 'Times New Roman', serif"
MONO = "ui-monospace, 'Cascadia Mono', Consolas, 'Liberation Mono', monospace"
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
CODE_PX = 14
TILE_MIN_PX = 160            # a KPI tile is at least this wide before the row wraps
TABLE_MIN_PX = 720           # a breaks table scrolls inside its frame below this width
# The closing bracket's width in the table's font (Inter 400, tabular figures),
# measured in the browser by scripts/shoot.py: 5.875 px at 15 px, so 0.3917 em.
# In em, it follows the cell's font size. Measure again if the font changes.
BRACKET_EM = 0.3917

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

/* Tables: 15 px. Tabular figures are set on figure cells only, below, so a
   caption and a break's text keep ordinary figures. Streamlit fades st.table
   headers to 60 per cent ink (4.0 on paper); they stay in ink. */
table, table th, table td {{
  font-size: {TABLE_PX}px !important;
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
  display: flex;
  flex-direction: column;
}}
/* Tiles in a row are one height, so a tile without a comparison line keeps
   that space; each line sits under its value, level with its neighbours'
   (finding 2). */
.p7-tile-row {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax({TILE_MIN_PX}px, 1fr));
  gap: {CARD_PAD}px;
  align-items: stretch;
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
.p7-notice {{ color: {INK}; margin: 0; }}

/* The sidebar's current page and hover, from tokens: Streamlit derives a pale
   olive from the page colour (finding 5). */
[data-testid="stSidebarNavLink"]:hover {{ background: {NEUTRAL_TINT}; }}
[data-testid="stSidebarNavLink"][aria-current="page"] {{ background: {INFO_TINT}; color: {INK}; }}

/* The kit's table: right-aligned tabular figures, and a frame of its own that
   scrolls sideways when the table is wider than the column, never the page
   (finding 3). */
.p7-table-wrap {{
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid {RULE};
  border-radius: {CARD_RADIUS}px;
  background: {SURFACE};
}}
.p7-table {{ border-collapse: collapse; width: 100%; min-width: {TABLE_MIN_PX}px; color: {INK}; }}
.p7-table caption {{
  caption-side: bottom;
  text-align: left;
  font-size: {CAPTION_PX}px !important;
  color: {MUTED};
  padding: 8px 12px;
}}
.p7-table th, .p7-table td {{
  padding: 8px 12px;
  text-align: left;
  vertical-align: top;
  border-bottom: 1px solid {RULE};
}}
.p7-table th {{ font-weight: 600; color: {INK}; }}
.p7-table td.num, .p7-table th.num {{
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}}
/* Hanging brackets: a non-negative figure is padded by the closing bracket's
   width, so a negative's bracket hangs in that space and every figure's
   digits end level (scripts/shoot.py measures both). */
.p7-table td.num:not(.neg) {{ padding-right: calc(12px + {BRACKET_EM}em); }}
.p7-table tbody tr:last-child td {{ border-bottom: none; }}

/* Provenance: every field in full; the SQL in a code block, the hash on one
   copyable line, never on hover only. */
.p7-provenance, .p7-refusal {{
  background: {SURFACE};
  border: 1px solid {RULE};
  border-radius: {CARD_RADIUS}px;
  padding: {CARD_PAD}px;
  color: {INK};
}}
.p7-card-title {{ font-weight: 600; color: {INK}; margin: 0 0 8px !important; }}
.p7-provenance dl {{
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 8px 16px;
  margin: 0 0 12px;
  padding: 0;
}}
.p7-provenance dt {{ color: {MUTED}; font-size: {CAPTION_PX}px; }}
.p7-provenance dd {{ margin: 0; min-width: 0; }}
.p7-code {{
  background: {NEUTRAL_TINT};
  color: {INK};
  border-radius: {CARD_RADIUS}px;
  padding: 12px;
  margin: 0;
  overflow-x: auto;
  white-space: pre;
  font-size: {CODE_PX}px;
  line-height: {BODY_LINE_HEIGHT};
}}
.p7-code, .p7-code code, .p7-hash, .p7-code-inline {{ font-family: {MONO} !important; }}
.p7-hash {{
  display: block;
  font-size: {CODE_PX}px;
  color: {INK};
  background: {NEUTRAL_TINT};
  padding: 4px 8px;
  border-radius: {CARD_RADIUS}px;
  word-break: break-all;
  user-select: all;
}}
.p7-code-inline {{ font-size: {CODE_PX}px; color: {INK}; background: {NEUTRAL_TINT}; padding: 0 4px; }}

/* A refusal is a designed outcome: neutral, never error red. */
.p7-refusal p {{ margin: 0; }}
.p7-refusal ul {{ margin: 8px 0 0; padding-left: 24px; }}

/* An evaluation result: the rate, its interval and n, on one line. */
.p7-eval {{ color: {INK}; margin: 0; }}
.p7-eval-figures {{ font-variant-numeric: tabular-nums; }}

/* An empty state: calm, and no colour. */
.p7-empty {{
  border: 1px solid {RULE};
  border-radius: {CARD_RADIUS}px;
  padding: {CARD_PAD_WIDE}px;
  color: {MUTED};
}}
.p7-empty p {{ margin: 0; }}
.p7-empty-title {{ font-family: {SERIF}; font-size: {HEADING_REM["h3"]}rem; color: {INK}; margin: 0 0 8px !important; }}
.p7-empty-action {{ color: {INK}; font-weight: 500; margin: 8px 0 0 !important; }}

@media (min-width: {WIDE_MIN_PX}px) {{
  .p7-tile {{ padding: {CARD_PAD_WIDE}px; }}
}}
@media (max-width: {PHONE_MAX_PX}px) {{
  [data-testid="stMainBlockContainer"] {{ padding-left: {PHONE_GUTTER}px; padding-right: {PHONE_GUTTER}px; }}
  .p7-banner {{ padding: {CARD_PAD}px; }}
  .p7-banner h1 {{ font-size: {HEADING_REM["h2"]}rem; }}
  .p7-provenance dl {{ grid-template-columns: minmax(0, 1fr); gap: 4px; }}
  .p7-provenance dd {{ margin: 0 0 8px; }}
}}
</style>"""


# -- Components ------------------------------------------------------------------------

class _Markup(str):
    """Markup that _element built: safe as it stands, never escaped twice."""


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _element(tag: str, css_class: str, *content,
             attrs: Sequence[tuple[str, str]] = ()) -> _Markup:
    """
    The one place markup is built. A plain string, as content or as an
    attribute's value, is escaped here; markup this module built is kept.
    """
    inner = "".join(part if isinstance(part, _Markup) else _esc(part) for part in content)
    attributes = (f' class="{_esc(css_class)}"' if css_class else "") + "".join(
        f' {name}="{_esc(value)}"' for name, value in attrs)
    return _Markup(f"<{tag}{attributes}>{inner}</{tag}>")


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


def kpi_row(*tiles: str) -> str:
    """KPI tiles in one grid row: one height each, wrapping to the next row on a phone."""
    return _element("div", "p7-tile-row", *tiles)


def notice(kind: str, label: str, text: str) -> str:
    """A labelled chip, then one line of text."""
    return _element("p", "p7-notice", status_chip(kind, label), " ", text)


# -- Figures --------------------------------------------------------------------------

def format_amount(value) -> str:
    """A whole amount with thousands separators, a negative in brackets: (112,450)."""
    whole = Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    text = f"{abs(whole):,}"
    return f"({text})" if whole < 0 else text


# -- Provenance and refusal -----------------------------------------------------------

RECONCILIATION = {"reconciled": ("info", "Reconciled"), "breaks": ("warning", "Breaks open"),
                  "not_run": ("neutral", "Not reconciled yet")}
_HASH = re.compile(r"[0-9a-f]{64}")
_REASON_CODE = re.compile(r"[a-z][a-z0-9_]*")


def provenance_card(*, specification: str, sql: str, sql_hash: str, row_count: int,
                    source_batch: str, reconciliation: str, registry_version: str) -> str:
    """Where a figure came from: every field in full, the SQL as code, the whole hash."""
    if reconciliation not in RECONCILIATION:
        raise ValueError(f"reconciliation is one of {', '.join(RECONCILIATION)}")
    if not _HASH.fullmatch(sql_hash):
        raise ValueError("the SQL hash is 64 lowercase hex characters (SHA-256)")
    kind, label = RECONCILIATION[reconciliation]
    fields = (("Specification", specification), ("Row count", format_amount(row_count)),
              ("Source batch", source_batch), ("Reconciliation", status_chip(kind, label)),
              ("Registry version", registry_version),
              ("SQL hash", _element("code", "p7-hash", f"sha256:{sql_hash}")))
    listing = _element("dl", "", *(part for name, value in fields
                                   for part in (_element("dt", "", name),
                                                _element("dd", "", value))))
    return _element("div", "p7-provenance", _element("p", "p7-card-title", "Provenance"),
                    listing, _element("pre", "p7-code", _element("code", "", sql)))


def refusal_card(code: str, text: str, suggestions: Sequence[str] = ()) -> str:
    """A question not answered, by design: its fixed reason code and wording, in neutral."""
    if not _REASON_CODE.fullmatch(code or ""):
        raise ValueError("a refusal carries a fixed reason code, such as unknown_metric")
    parts = [_element("div", "p7-row", status_chip("neutral", "Not answered"),
                      _element("code", "p7-code-inline", code)),
             _element("p", "", text)]
    if suggestions:
        parts.append(_element("ul", "", *(_element("li", "", s) for s in suggestions)))
    return _element("div", "p7-refusal", *parts)


# -- Breaks and their acknowledgement --------------------------------------------------

SEVERITIES = {"high": ("adverse", "High"), "medium": ("warning", "Medium"),
              "low": ("neutral", "Low")}
BREAK_STATUSES = {"open": ("warning", "Open"), "accepted": ("info", "Accepted"),
                  "dismissed": ("neutral", "Dismissed"), "investigated": ("info", "Investigated")}
ACTIONS = ("Accepted", "Dismissed", "Investigated")
ACKNOWLEDGE_NOTICE = "Enter your name and choose an action."
_BREAK_COLUMNS = (("Severity", False), ("Member", False), ("Plan", True), ("Actual", True),
                  ("Difference", True), ("Break", False), ("Status", False))


@dataclass(frozen=True)
class BreakRow:
    """One break: its severity, the member, plan and actual, what is wrong, and its status."""
    severity: str
    member: str
    plan: object
    actual: object
    text: str
    status: str = "open"

    @property
    def difference(self) -> Decimal:
        return Decimal(str(self.actual)) - Decimal(str(self.plan))


def _figure_cell(value) -> str:
    """A figure's cell: right-aligned digits; a negative's, in brackets, is marked for its hang."""
    shown = format_amount(value)
    return _element("td", "num neg" if shown.startswith("(") else "num", shown)


def breaks_table(rows: Sequence[BreakRow], caption: str) -> str:
    """
    The breaks as a table of the kit's own: headers with scope, figures right-
    aligned in tabular figures with negatives in brackets, and a frame that
    scrolls sideways inside itself when the table is wider than the column.
    A negative figure's cell is marked "neg", so its closing bracket hangs
    and its digits end level with the others'.
    """
    head = _element("thead", "", _element("tr", "", *(
        _element("th", "num" if numeric else "", name, attrs=(("scope", "col"),))
        for name, numeric in _BREAK_COLUMNS)))
    body = []
    for r in rows:
        if r.severity not in SEVERITIES or r.status not in BREAK_STATUSES:
            raise ValueError(f"severity is one of {', '.join(SEVERITIES)} and status one of "
                             f"{', '.join(BREAK_STATUSES)}")
        body.append(_element(
            "tr", "",
            _element("td", "", status_chip(*SEVERITIES[r.severity])),
            _element("td", "", r.member),
            *(_figure_cell(v) for v in (r.plan, r.actual, r.difference)),
            _element("td", "", r.text),
            _element("td", "", status_chip(*BREAK_STATUSES[r.status]))))
    table = _element("table", "p7-table", _element("caption", "", caption), head,
                     _element("tbody", "", *body))
    return _element("div", "p7-table-wrap", table,
                    attrs=(("role", "region"), ("aria-label", "Breaks"), ("tabindex", "0")))


def acknowledge_form(break_label: str, key: str) -> Optional[dict]:
    """
    The acknowledgement of one break: the person's name, their action and a
    note. No action is chosen until the person chooses one. It returns what
    was entered, with the name trimmed, or None: until it is submitted, and
    when it is submitted with no action or a blank name, which shows a plain
    notice instead. It records nothing.
    """
    with st.form(key):
        show(_element("p", "p7-card-title", f"Acknowledge: {break_label}"))
        name = st.text_input("Your name", key=f"{key}_name")
        action = st.radio("Action", ACTIONS, key=f"{key}_action", horizontal=True, index=None)
        note = st.text_area("Note", key=f"{key}_note")
        submitted = st.form_submit_button("Record acknowledgement", key=f"{key}_submit")
    if not submitted:
        return None
    if action is None or not name.strip():
        st.warning(ACKNOWLEDGE_NOTICE)
        return None
    return {"name": name.strip(), "action": action, "note": note}


# -- Evaluation and empty states ---------------------------------------------------------

def evaluation_result(label: str, k: int, n: int) -> str:
    """A rate with its Wilson 95% interval and n, on one line. k and n are required."""
    lower, upper = wilson_interval(k, n)
    figures = (f"{percent(k / n)}% ({k} of {n}); 95% interval {percent(lower)}% to "
               f"{percent(upper)}%")
    return _element("p", "p7-eval", _element("span", "p7-eval-label", label), ": ",
                    _element("span", "p7-eval-figures", figures))


def empty_state(title: str, text: str, action: Optional[str] = None) -> str:
    """A calm placeholder: a title, a line of text, and the next step if there is one."""
    parts = [_element("p", "p7-empty-title", title), _element("p", "", text)]
    if action:
        parts.append(_element("p", "p7-empty-action", f"Next: {action}"))
    return _element("div", "p7-empty", *parts)


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
