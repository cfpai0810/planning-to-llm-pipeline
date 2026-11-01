# =============================================================================
# lib/nav.py: the pages in order, with their titles, addresses and icons
# =============================================================================
"""
One list of pages, read by Home.py's st.navigation and by the tests. Each
page's address (url_path) is pinned: links to the pages will be published, and
a renamed file must not change them. Home comes first and is the default page,
at the root address. The kit gallery joins the list only when the environment
sets P7_GALLERY=1, so it never ships.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

from config import APP_TITLE

__all__ = ["APP_TITLE", "GALLERY", "PageSpec", "PAGES", "pages"]


@dataclass(frozen=True)
class PageSpec:
    file: str                  # in streamlit_app/views/
    title: str
    url_path: Optional[str]    # None for Home, the default page at the root address
    icon: str                  # a Material Symbols icon


PAGES = (
    PageSpec("home.py", "Home", None, ":material/home:"),
    PageSpec("1_How_the_pipeline_works.py", "How the pipeline works", "How_the_pipeline_works",
             ":material/account_tree:"),
    PageSpec("2_Ask_the_plan.py", "Ask the plan", "Ask_the_plan", ":material/question_answer:"),
    PageSpec("3_Your_own_data.py", "Your own data", "Your_own_data", ":material/upload_file:"),
    PageSpec("4_How_its_built.py", "How it's built and tested", "How_its_built",
             ":material/build:"),
)


GALLERY = PageSpec("_gallery.py", "Kit gallery", "gallery", ":material/palette:")


def pages(environ: Mapping[str, str] = os.environ) -> tuple[PageSpec, ...]:
    """The pages the navigation shows, in order: the gallery last, and only with P7_GALLERY=1."""
    return PAGES + ((GALLERY,) if environ.get("P7_GALLERY") == "1" else ())
