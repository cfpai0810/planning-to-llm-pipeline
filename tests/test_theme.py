"""The theme kit, first slice: tokens, CSS, config, banner, KPI tile, status chip, gallery.

Colour carries status only and a number is never coloured. Every status chip
carries a word, so colour is never alone. Every dynamic string is escaped in
one place. The contrast test recomputes the WCAG ratio of every pair the CSS
and the components use and prints the table (run with -s or -rP to see it).
"""

import ast
import re
import tomllib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from streamlit_app.lib import nav, theme

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "streamlit_app"
CONFIG = ROOT / ".streamlit" / "config.toml"
POISON = "<script>alert(\"x\")</script> & Co's \"Q1\" plan"
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# The brief's tokens (section 5.2), and the ratios it quotes for comparison.
BRIEF_TOKENS = {"INK": "#1E2A2E", "PAPER": "#FBFAF7", "SURFACE": "#FFFFFF", "NAVY": "#1A3A5C",
                "NAVY_HOVER": "#122940", "BANNER_SUB": "#AACCEE", "MUTED": "#5B666B",
                "RULE": "#D5D9DB", "CONTROL_BORDER": "#6A7378"}
BRIEF_STATUS = {"favourable": ("#2E6B3F", "#E7F2EA"), "adverse": ("#9B2C2C", "#FBEAEA"),
                "neutral": ("#4A5560", "#EEF1F4"), "warning": ("#7A4200", "#FBF0E3"),
                "info": ("#1A3A5C", "#E6EDF5"), "draft": ("#FFFFFF", "#B45F06"),
                "final": ("#FFFFFF", "#2E6B3F")}
MINIMUM = {"text": 4.5, "ui": 3.0, "decorative": None}


def _luminance(colour: str) -> float:
    channels = []
    for i in (1, 3, 5):
        c = int(colour[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(foreground: str, background: str) -> float:
    high, low = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _rules(css: str) -> list[tuple[list[str], str]]:
    """(selectors, declarations) for every innermost rule, inside @media blocks too."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return [([s.strip() for s in selectors.split(",")], body)
            for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def _bodies(at: AppTest) -> str:
    return "\n".join(e.proto.body for e in at.get("html"))


class TestTokens:
    def test_every_token_of_the_brief_is_a_named_constant(self):
        assert {name: getattr(theme, name) for name in BRIEF_TOKENS} == BRIEF_TOKENS
        assert {k: (s.text, s.background) for k, s in theme.STATUS.items()} == BRIEF_STATUS

    def test_type_spacing_and_shape(self):
        assert (theme.BODY_PX, theme.BODY_LINE_HEIGHT, theme.HEADING_LINE_HEIGHT) == (16, 1.5, 1.25)
        assert theme.HEADING_REM == {"h1": 2, "h2": 1.5, "h3": 1.25, "h4": 1.0625}
        assert min(theme.CAPTION_PX, theme.CHIP_PX) >= 13 and theme.TABLE_PX == 15
        assert theme.SPACING == (4, 8, 12, 16, 24, 32, 48)
        assert (theme.CARD_RADIUS, theme.FOCUS_WIDTH, theme.FOCUS_OFFSET) == (6, 2, 2)
        assert theme.SANS.startswith("'Inter'") and theme.SERIF.startswith("'Source Serif 4'")


class TestContrast:
    def test_every_pair_meets_wcag(self):
        rows, failures = [], []
        for pair in theme.PAIRS:
            ratio = contrast(pair.foreground, pair.background)
            need = MINIMUM[pair.kind]
            verdict = "exempt" if need is None else ("pass" if ratio >= need else "FAIL")
            rows.append((pair.use, pair.foreground, pair.background, pair.kind, ratio, need, verdict))
            if verdict == "FAIL":
                failures.append(pair.use)
        print("\nWCAG contrast, recomputed from theme.PAIRS")
        print(f"{'use':44} {'fg':8} {'bg':8} {'kind':10} {'ratio':>6} {'need':>5}  verdict")
        for use, fg, bg, kind, ratio, need, verdict in rows:
            print(f"{use:44} {fg:8} {bg:8} {kind:10} {ratio:6.2f} {need or '-':>5}  {verdict}")
        assert failures == []

    def test_the_pairs_cover_every_status_and_the_focus_ring(self):
        pairs = {(p.foreground, p.background) for p in theme.PAIRS}
        for status in theme.STATUS.values():
            assert (status.text, status.background) in pairs
        assert any(p.foreground == theme.NAVY and p.kind == "ui" for p in theme.PAIRS)
        assert (theme.CONTROL_BORDER, theme.PAPER) in pairs


class TestCss:
    def test_every_hex_in_the_css_is_a_token(self):
        found = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{3,8}\b", theme.inject_css())}
        assert found and found <= theme.TOKEN_VALUES, found - theme.TOKEN_VALUES

    def test_no_motion(self):
        css = theme.inject_css().lower()
        for word in ("transition", "animation", "@keyframes"):
            assert word not in css, word

    def test_fonts_reach_inputs_selects_buttons_tables_and_headings(self):
        rules = _rules(theme.inject_css())

        def font_of(element):
            return [body for selectors, body in rules if element in selectors
                    and "font-family" in body]

        for element in ("input", "textarea", "select", "button", "td", "th"):
            assert any(f"font-family: {theme.SANS}" in b for b in font_of(element)), element
        for element in HEADINGS:
            assert any(f"font-family: {theme.SERIF}" in b for b in font_of(element)), element

    def test_a_visible_focus_ring_on_every_control(self):
        rules = _rules(theme.inject_css())
        ring = (f"outline: {theme.FOCUS_WIDTH}px solid {theme.NAVY} !important; "
                f"outline-offset: {theme.FOCUS_OFFSET}px !important;")
        assert any("*:focus-visible" in selectors and ring in body for selectors, body in rules)

    def test_spacing_only_from_the_scale(self):
        for selectors, body in _rules(theme.inject_css()):
            for prop, value in re.findall(r"(margin|padding|gap)[a-z-]*:\s*([^;]+);", body):
                for px in re.findall(r"(\d+)px", value):
                    assert int(px) in theme.SPACING, (selectors, prop, value)

    def test_no_text_smaller_than_13px(self):
        for size in re.findall(r"font-size:\s*([\d.]+)(px|rem)", theme.inject_css()):
            value = float(size[0]) * (16 if size[1] == "rem" else 1)
            assert value >= 13, size

    def test_captions_are_opaque_so_their_contrast_holds(self):
        # Streamlit 1.60 fades captions with opacity 0.6, which would take MUTED
        # below 4.5 on paper; the pairs above assume opaque text.
        rules = _rules(theme.inject_css())
        assert any('[data-testid="stCaptionContainer"]' in selectors and "opacity: 1;" in body
                   and f"color: {theme.MUTED};" in body for selectors, body in rules)

    def test_table_headers_are_ink_not_faded(self):
        # Streamlit 1.60 draws st.table headers in ink at 60 per cent, 4.0 on paper.
        rules = _rules(theme.inject_css())
        assert any('[data-testid="stTable"] th' in selectors and f"color: {theme.INK};" in body
                   for selectors, body in rules)

    def test_numbers_are_never_coloured(self):
        rules = {tuple(s): b for s, b in _rules(theme.inject_css())}
        assert f"color: {theme.INK};" in rules[(".p7-tile-value",)]
        assert "tabular-nums" in rules[(".p7-tile-value",)]


class TestConfig:
    def test_the_config_takes_the_tokens(self):
        config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
        t = config["theme"]
        assert (t["primaryColor"], t["backgroundColor"], t["secondaryBackgroundColor"],
                t["textColor"], t["linkColor"], t["borderColor"], t["dataframeBorderColor"]) == (
            theme.NAVY, theme.PAPER, theme.SURFACE, theme.INK, theme.NAVY,
            theme.CONTROL_BORDER, theme.RULE)
        assert t["font"].startswith("Inter:https://fonts.googleapis.com/")
        assert t["headingFont"].startswith("Source Serif 4:https://fonts.googleapis.com/")
        assert t["headingFontSizes"] == [f"{theme.HEADING_REM[h]}rem" for h in HEADINGS[:4]]
        assert t["showWidgetBorder"] is True
        assert config["client"]["toolbarMode"] == "minimal"
        assert config["browser"]["gatherUsageStats"] is False

    def test_every_hex_in_the_config_is_a_token(self):
        found = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}\b", CONFIG.read_text(encoding="utf-8"))}
        assert found <= theme.TOKEN_VALUES, found - theme.TOKEN_VALUES


class TestComponents:
    def test_the_banner(self):
        out = theme.banner("Ask the plan", "Planning-to-Warehouse-to-LLM Pipeline")
        assert out == ('<div class="p7-banner"><h1>Ask the plan</h1>'
                       '<p>Planning-to-Warehouse-to-LLM Pipeline</p></div>')

    def test_the_kpi_tile_in_each_state(self):
        plain = theme.kpi_tile("Revenue", "£2.48m")
        assert "p7-tile-versus" not in plain and "p7-chip" not in plain
        versus = theme.kpi_tile("Revenue", "£2.48m", versus="£112k below budget")
        assert "£112k below budget" in versus and "p7-chip" not in versus
        fav = theme.kpi_tile("Opex", "£612k", versus="£18k below budget", kind="favourable")
        assert '<span class="p7-chip p7-chip-favourable">Fav</span>' in fav
        adv = theme.kpi_tile("Revenue", "£2.48m", versus="£112k below budget", kind="adverse")
        assert '<span class="p7-chip p7-chip-adverse">(Adv)</span>' in adv
        # The value sits in its own element, in ink, whatever the status.
        for out in (plain, versus, fav, adv):
            assert re.search(r'<div class="p7-tile-value">£[\d.]+[mk]</div>', out)

    def test_a_status_needs_its_comparison_and_a_known_kind(self):
        with pytest.raises(ValueError):
            theme.kpi_tile("Revenue", "£2.48m", kind="favourable")
        with pytest.raises(ValueError):
            theme.kpi_tile("Revenue", "£2.48m", versus="£1k below budget", kind="draft")

    def test_a_chip_is_never_colour_alone(self):
        with pytest.raises(TypeError):
            theme.status_chip("adverse")
        for label in ("", "   "):
            with pytest.raises(ValueError):
                theme.status_chip("adverse", label)
        with pytest.raises(ValueError):
            theme.status_chip("red", "Check")

    def test_every_status_kind_renders_with_its_label(self):
        for kind in theme.STATUS:
            assert theme.status_chip(kind, "Label") == (
                f'<span class="p7-chip p7-chip-{kind}">Label</span>')


class TestEscaping:
    RENDERS = {
        "banner title": lambda s: theme.banner(s, "subtitle"),
        "banner subtitle": lambda s: theme.banner("Title", s),
        "tile label": lambda s: theme.kpi_tile(s, "£1k"),
        "tile value": lambda s: theme.kpi_tile("Label", s),
        "tile comparison": lambda s: theme.kpi_tile("Label", "£1k", versus=s, kind="adverse"),
        "chip label": lambda s: theme.status_chip("warning", s),
    }

    @pytest.mark.parametrize("where", list(RENDERS))
    def test_a_poisoned_name_renders_as_text(self, where):
        out = self.RENDERS[where](POISON)
        assert "<script>" not in out
        assert ("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; Co&#x27;s "
                "&quot;Q1&quot; plan") in out

    def test_escaping_happens_in_one_place(self):
        tree = ast.parse((APP / "lib" / "theme.py").read_text(encoding="utf-8"))
        escapes = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute) and n.func.attr == "escape"]
        assert len(escapes) == 1
        (builder,) = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                      and escapes[0] in ast.walk(f)]
        assert builder.name == "_element"


class TestGallery:
    def test_the_gallery_is_in_the_navigation_only_with_the_flag(self):
        assert "_gallery.py" not in [p.file for p in nav.pages({})]
        assert "_gallery.py" not in [p.file for p in nav.pages({"P7_GALLERY": "0"})]
        assert nav.pages({"P7_GALLERY": "1"})[-1].file == "_gallery.py"
        assert nav.pages({"P7_GALLERY": "1"})[:-1] == nav.pages({})

    def test_the_gallery_renders_every_component_in_every_state(self, monkeypatch):
        monkeypatch.setenv("P7_GALLERY", "1")
        at = AppTest.from_file(str(APP / "Home.py"), default_timeout=60).run()
        at.switch_page("views/_gallery.py").run()
        assert not at.exception, at.exception
        bodies = _bodies(at)
        for kind in theme.STATUS:
            assert f"p7-chip-{kind}" in bodies, kind
        assert bodies.count('class="p7-tile"') >= 4
        assert bodies.count('class="p7-banner"') >= 2
        assert "<script>" not in bodies and "&lt;script&gt;" in bodies
        text = bodies + "\n".join(str(e.value) for e in at.get("caption"))
        assert "stand-in data" in text.lower()
        # The controls the browser font check measures.
        assert at.text_input and at.text_area and at.selectbox and at.button and at.table

    def test_every_page_shows_the_kit_banner(self):
        for page in nav.pages({}):
            at = AppTest.from_file(str(APP / "Home.py"), default_timeout=60).run()
            if page.url_path:
                at.switch_page(f"views/{page.file}").run()
            subtitle = "Phase 0, not yet usable" if page.url_path is None else nav.APP_TITLE
            title = nav.APP_TITLE if page.url_path is None else page.title
            assert theme.banner(title, subtitle) in _bodies(at), page.title
            assert "font-family" in _bodies(at), "the theme CSS is applied on every page"


class TestLayout:
    """Text pages keep the centred reading width; a page with tables widens itself.

    Streamlit 1.60 lets st.set_page_config run more than once in a script run,
    each call overriding the last, so Home sets "centered" on every run and a
    table page calls theme.full_width() for its own run only (scripts/shoot.py
    measures 736 px, then 980 px, then 736 px again in one session at 1280 px).
    """

    @staticmethod
    def _calls(path: Path) -> list[ast.Call]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)]

    def test_home_sets_the_centred_reading_width_on_every_run(self):
        (call,) = [c for c in self._calls(APP / "Home.py") if c.func.attr == "set_page_config"]
        assert {k.arg: ast.literal_eval(k.value) for k in call.keywords}["layout"] == "centered"

    def test_the_table_page_widens_itself_and_the_text_pages_do_not(self):
        widens = {page.file: any(c.func.attr == "full_width"
                                 for c in self._calls(APP / "views" / page.file))
                  for page in nav.pages({"P7_GALLERY": "1"})}
        assert widens == {**{p.file: False for p in nav.pages({})}, "_gallery.py": True}
        (call,) = [c for c in self._calls(APP / "lib" / "theme.py")
                   if c.func.attr == "set_page_config"]
        assert {k.arg: ast.literal_eval(k.value) for k in call.keywords} == {"layout": "wide"}
