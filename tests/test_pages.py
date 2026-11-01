"""The five pages: the structure of P2 to P5, each a stub in Phase 0.

st.navigation with st.Page(url_path=...), page files in views/ (never pages/),
a Material icon, Home first. Each page shows the banner and one plain line.
The pages run in process with Streamlit's AppTest, reached through Home's
navigation as a visitor reaches them.
"""

import ast
from pathlib import Path

from streamlit.testing.v1 import AppTest

from streamlit_app.lib import nav

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "streamlit_app"
LATER = "Built in a later phase"


def _home() -> AppTest:
    return AppTest.from_file(str(APP / "Home.py"), default_timeout=60).run()


def _text(at: AppTest) -> str:
    return "\n".join(str(e.value) for kind in ("title", "caption", "markdown")
                     for e in at.get(kind))


def _page_config_call() -> ast.Call:
    tree = ast.parse((APP / "Home.py").read_text(encoding="utf-8"))
    (call,) = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == "set_page_config"]
    return call


class TestPages:
    def test_the_navigation_order_titles_addresses_and_icons(self):
        pages = nav.pages({})
        assert [p.title for p in pages] == ["Home", "How the pipeline works", "Ask the plan",
                                           "Your own data", "How it's built and tested"]
        assert [p.url_path for p in pages] == [None, "How_the_pipeline_works", "Ask_the_plan",
                                              "Your_own_data", "How_its_built"]
        assert all(p.icon.startswith(":material/") and p.icon.endswith(":") for p in pages)

    def test_pages_live_in_views_never_in_pages(self):
        assert not (APP / "pages").exists()
        for page in nav.pages({}):
            assert (APP / "views" / page.file).is_file(), page.file

    def test_every_page_shows_the_banner_and_the_stub_line(self):
        for page in nav.pages({}):
            at = _home()
            if page.url_path:
                at.switch_page(f"views/{page.file}").run()
            assert not at.exception, (page.title, at.exception)
            text = _text(at)
            banner = nav.APP_TITLE if page.url_path is None else page.title
            assert banner in text, page.title
            assert LATER in text, page.title

    def test_a_phone_does_not_open_on_the_sidebar(self):
        # Finding 53: "expanded" covered most of a phone's first view.
        kwargs = {k.arg: k.value for k in _page_config_call().keywords}
        assert ast.literal_eval(kwargs["initial_sidebar_state"]) == "auto"

    def test_the_browser_tab_has_a_material_icon(self):
        kwargs = {k.arg: k.value for k in _page_config_call().keywords}
        assert ast.literal_eval(kwargs["page_icon"]).startswith(":material/")
