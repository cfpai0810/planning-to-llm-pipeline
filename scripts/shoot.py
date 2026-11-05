# =============================================================================
# scripts/shoot.py: browser evidence for the theme kit (not part of the suite)
# =============================================================================
"""
Loads the five pages and the kit gallery at 1280, 768 and 390 px in a real
browser and records, for the reviewer:

- no sideways scroll: neither the document nor the main column is wider than
  the viewport;
- the computed font-family of an input, a select, a button, a table cell, an
  h1 and an h4 (each must begin with Inter or Source Serif 4), and whether
  the two fonts actually loaded;
- the reading width: the main column of a text page and of a table page, and
  again after moving between them in one session;
- the keyboard focus ring on each control of the gallery;
- that a poisoned name on the gallery renders as text;
- KPI tiles in a row are one height (finding 2); the breaks table's figures
  are right-aligned in tabular figures, and a table wider than the column
  scrolls inside its own frame, never the page (finding 3); the sidebar's
  current page takes its colour from a token (finding 5);
- hanging brackets (Phase 0 verdict, finding 4): in the breaks table's
  Difference column, the right edge of the digits of "(112,450)" and of
  "34,180", each measured with a DOM Range over the digit characters only,
  must be equal within DIGIT_EDGE_TOLERANCE_PX; the closing bracket's width
  is measured too, in px and in em of the cell's font, which is where
  theme.BRACKET_EM comes from;
- figure column headers (Phase 1 stop 1 verdict, finding 1): the right edge
  of each figure column's header text, measured with a DOM Range, against the
  right edge of the digits below it: in the Difference column, of both
  "(112,450)" and "34,180"; in Plan and Actual, of the first figure. Each must
  be equal within DIGIT_EDGE_TOLERANCE_PX;
- tabular figures only where figures are (Phase 0 verdict, finding 5): the
  computed font-variant-numeric of a figure cell and a KPI tile value
  (tabular-nums), and of the table caption and a break's text (normal);
- a failed model call on screen: a stand-in key typed into the gate, the
  gallery's call made through the real SDK to a local stand-in API that
  answers "model not found", and the plain line and code it shows;
- a screenshot of every page at every width, labelled "stand-in data".

It is acceptance evidence, not a CI test: the suite does not need a browser.

    python scripts/shoot.py --out shots
    python scripts/shoot.py --url http://localhost:8770 --out shots   (a server already running)

Without --url it starts Streamlit itself on --port (8770), with P7_GALLERY=1,
an empty ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL pointing at the stand-in
API on --api-port (8771, loopback only), and stops both at the end. With
--url the model call is skipped, since that server's settings are not known.
It needs Playwright (--playwright-path names a folder that holds it) and a
Chromium browser (--channel, msedge by default; "chromium" for Playwright's
own). No real key is used and nothing leaves the machine but the font files.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src import errors  # noqa: E402
from streamlit_app.lib import key_gate, nav, theme  # noqa: E402

# A stand-in key, built from parts so the whole key is written in no file.
SHOT_KEY = "-".join(["sk", "ant", "api03", "SHOOT", "standin", "not", "a", "real", "key"])
HEIGHTS = {1280: 900, 768: 1024, 390: 844}
# The gallery's last element: once it is drawn, every section above it is.
GALLERY_READY = 'button:has-text("Run a stand-in call")'
LABEL = "Stand-in data"
POISON = "<script>alert(\"x\")</script> & Co's \"Q1\" plan"
FONT_TARGETS = {
    "input": '[data-testid="stTextInput"] input',
    "select": '[data-testid="stSelectbox"] input[role="combobox"]',
    "button": '[data-testid="stBaseButton-primary"]',
    "table cell": ".p7-table td",
    "h1": ".p7-banner h1",
    "h4": '[data-testid="stMarkdownContainer"] h4',
}
EXPECTED_FONT = {"h1": "Source Serif 4", "h4": "Source Serif 4"}
# Phase 0 verdict, finding 4: the digits of a negative and a positive figure
# end level. The browser lays text out in 1/64 px steps, so the tolerance is
# a few of those steps and far below one pixel.
DIGIT_EDGE_TOLERANCE_PX = 0.05
# Phase 0 verdict, finding 5: the computed font-variant-numeric each kind of
# element must have.
EXPECTED_NUMERALS = {"figure cell": "tabular-nums", "KPI tile value": "tabular-nums",
                     "table caption": "normal", "break text": "normal"}

MEASURE = """(targets) => {
  const de = document.documentElement;
  const main = document.querySelector('[data-testid="stMain"]');
  const block = document.querySelector('[data-testid="stMainBlockContainer"]');
  const sidebar = document.querySelector('[data-testid="stSidebar"]');
  const caption = document.querySelector('[data-testid="stMain"] [data-testid="stCaptionContainer"]');
  const fonts = {};
  for (const [name, selector] of Object.entries(targets)) {
    const el = document.querySelector(selector);
    fonts[name] = el ? getComputedStyle(el).fontFamily : null;
  }
  const sideRect = sidebar ? sidebar.getBoundingClientRect() : null;
  return {
    viewport: window.innerWidth,
    doc_scroll_width: de.scrollWidth,
    doc_client_width: de.clientWidth,
    main_scroll_width: main ? main.scrollWidth : null,
    main_client_width: main ? main.clientWidth : null,
    column_width: block ? Math.round(block.getBoundingClientRect().width) : null,
    column_padding: block ? getComputedStyle(block).paddingLeft : null,
    sidebar_open: sideRect ? (sidebar.getAttribute('aria-expanded') === 'true'
                              && sideRect.right > 0 && sideRect.width > 0) : null,
    caption: caption ? [getComputedStyle(caption).color, getComputedStyle(caption).opacity] : null,
    table_header: (() => { const th = document.querySelector('.p7-table th');
                           return th ? getComputedStyle(th).color : null; })(),
    tile_rows: [...document.querySelectorAll('.p7-tile-row')].map(row =>
      [...row.children].map(t => { const r = t.getBoundingClientRect();
                                   return [Math.round(r.top), Math.round(r.height)]; })),
    figures: (() => { const cells = [...document.querySelectorAll('.p7-table td.num, .p7-table th.num')];
                      return {count: cells.length,
                              right: cells.filter(c => getComputedStyle(c).textAlign === 'right').length,
                              tabular: cells.filter(c => getComputedStyle(c).fontVariantNumeric.includes('tabular-nums')).length}; })(),
    table_frame: (() => { const w = document.querySelector('.p7-table-wrap');
                          if (!w) return null; const s = getComputedStyle(w);
                          return {scroll: w.scrollWidth, client: w.clientWidth, overflow: s.overflowX,
                                  border: s.borderTopWidth + ' ' + s.borderTopStyle + ' ' + s.borderTopColor}; })(),
    current_page: (() => { const a = document.querySelector('[data-testid="stSidebarNavLink"][aria-current="page"]');
                           return a ? getComputedStyle(a).backgroundColor : null; })(),
    hash_fits: (() => { const h = document.querySelector('.p7-hash');
                        return h ? h.scrollWidth <= h.clientWidth + 1 : null; })(),
    // Phase 0 verdict, finding 4: in the first breaks table's Difference column, the first
    // negative and the first non-negative figure. Each right edge comes from a
    // DOM Range over the digit characters only, never the brackets.
    brackets: (() => {
      const table = document.querySelector('.p7-table');
      if (!table) return null;
      const col = [...table.querySelectorAll('thead th')]
        .findIndex(th => th.textContent.trim() === 'Difference');
      const cells = [...table.querySelectorAll('tbody tr')].map(tr => tr.children[col]);
      const neg = cells.find(c => c.textContent.trim().startsWith('('));
      const pos = cells.find(c => !c.textContent.trim().startsWith('('));
      const range = (node, start, end) => { const r = document.createRange();
                                            r.setStart(node, start); r.setEnd(node, end);
                                            return r.getBoundingClientRect(); };
      const digits = (cell) => { const node = cell.firstChild, text = node.textContent;
                                 const at = [...text].map((ch, i) => /\\d/.test(ch) ? i : -1).filter(i => i >= 0);
                                 return range(node, at[0], at[at.length - 1] + 1).right; };
      const negText = neg.firstChild.textContent;
      const close = range(neg.firstChild, negText.lastIndexOf(')'), negText.lastIndexOf(')') + 1);
      const s = getComputedStyle(neg);
      const contentRight = neg.getBoundingClientRect().right - parseFloat(s.paddingRight)
                           - parseFloat(s.borderRightWidth);
      // Phase 1 stop 1 verdict, finding 1: each figure column's header text against
      // the digits below it (the Difference column's negative and non-negative,
      // and the first figure of every other figure column).
      const headers = [...table.querySelectorAll('thead th')].map((th, i) => [th, i])
        .filter(([th]) => th.classList.contains('num'))
        .map(([th, i]) => {
          const column = [...table.querySelectorAll('tbody tr')].map(tr => tr.children[i]);
          const below = i === col ? [neg, pos] : [column[0]];
          const node = th.firstChild;
          return {header: th.textContent.trim(),
                  header_right: range(node, 0, node.textContent.length).right,
                  padding_right: getComputedStyle(th).paddingRight,
                  digits: below.map(c => ({text: c.textContent.trim(), right: digits(c)}))};
        });
      return {negative: neg.textContent.trim(), positive: pos.textContent.trim(), headers,
              negative_digits_right: digits(neg), positive_digits_right: digits(pos),
              bracket_px: close.width, bracket_hang_px: contentRight - digits(neg),
              font_size_px: parseFloat(s.fontSize), font: s.fontFamily.split(',')[0] + ' '
                + s.fontWeight + ' ' + s.fontVariantNumeric,
              padding_right: {negative: s.paddingRight, positive: getComputedStyle(pos).paddingRight}};
    })(),
    // Phase 0 verdict, finding 5: tabular figures on figures only.
    numerals: (() => {
      const table = document.querySelector('.p7-table');
      const tile = document.querySelector('.p7-tile-value');
      if (!table || !tile) return null;
      const col = [...table.querySelectorAll('thead th')]
        .findIndex(th => th.textContent.trim() === 'Break');
      const fvn = el => el ? getComputedStyle(el).fontVariantNumeric : null;
      return {'figure cell': fvn(table.querySelector('tbody td.num')),
              'KPI tile value': fvn(tile),
              'table caption': fvn(table.querySelector('caption')),
              'break text': fvn(table.querySelector('tbody tr').children[col])};
    })(),
    fonts,
    // The weights of each family that actually loaded. document.fonts.check() is
    // not used: the CSS and Streamlit's config both declare the fonts, and check()
    // is false while either copy of a face stays unloaded.
    loaded: (() => {
      const seen = {};
      for (const face of document.fonts) {
        const family = face.family.replace(/["']/g, '');
        if ((family === 'Inter' || family === 'Source Serif 4') && face.status === 'loaded') {
          (seen[family] = seen[family] || new Set()).add(face.weight);
        }
      }
      return Object.fromEntries(Object.entries(seen).map(([k, v]) => [k, [...v].sort()]));
    })(),
  };
}"""

POISON_CHECK = """(poison) => {
  const heads = [...document.querySelectorAll('.p7-banner h1')].map(h => h.textContent);
  const scripts = [...document.querySelectorAll('[data-testid="stMain"] script')]
    .filter(s => s.textContent.includes('alert')).length;
  return {shown_as_text: heads.includes(poison), injected_scripts: scripts};
}"""

FOCUS_STEP = """() => {
  const a = document.activeElement;
  if (!a) return null;
  let el = a;
  for (let k = 0; k < 3 && el; k++, el = el.parentElement) {
    const s = getComputedStyle(el);
    if (s.outlineStyle !== 'none' && s.outlineWidth !== '0px') {
      return {control: a.getAttribute('data-testid') || a.getAttribute('role') || a.tagName.toLowerCase(),
              ring_on: el === a ? 'the control' : 'its field',
              colour: s.outlineColor, width: s.outlineWidth, offset: s.outlineOffset};
    }
  }
  return {control: a.getAttribute('data-testid') || a.tagName.toLowerCase(), ring_on: null};
}"""

ADD_LABEL = """([text, fg, bg, font]) => {
  const d = document.createElement('div');
  d.textContent = text;
  d.style.cssText = 'position:fixed;top:8px;right:64px;z-index:999999;padding:4px 12px;'
    + 'border-radius:999px;font-size:13px;font-weight:600;color:' + fg + ';background:' + bg
    + ';font-family:' + font;
  document.body.appendChild(d);
}"""


def _child_env(api_port: int) -> dict:
    keep = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env.update({"ANTHROPIC_API_KEY": "", "P7_GALLERY": "1", "PYTHONIOENCODING": "utf-8",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{api_port}"})
    return env


class _StandInAPI(BaseHTTPRequestHandler):
    """A loopback stand-in for the API: every call is answered 404, model not found."""
    received: list = []

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        headers = {k.lower(): v for k, v in self.headers.items()}
        type(self).received.append({"path": self.path, "key": headers.get("x-api-key"),
                                    "messages": body.get("messages"),
                                    "sampling": sorted({"temperature", "top_p", "top_k"} & set(body))})
        reply = json.dumps({"type": "error", "error": {
            "type": "not_found_error", "message": f"model: {config.MODEL}"}}).encode()
        self.send_response(404)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


def _model_call(browser, url: str, width: int, out: Path) -> dict:
    """The gallery's call with a stand-in key, through the stand-in API: what the page shows."""
    context = browser.new_context(viewport={"width": width, "height": HEIGHTS[width]})
    page = context.new_page()
    page.goto(f"{url}/gallery")
    frame = _app_frame(page, GALLERY_READY)
    _settle(page, frame)
    opened = False
    if not frame.evaluate(MEASURE, {})["sidebar_open"]:
        frame.click('[data-testid="stExpandSidebarButton"]')
        opened = True
    frame.click('[data-testid="stSidebar"] [data-testid="stExpander"] summary')
    field = '[data-testid="stSidebar"] input[type="password"]'
    frame.fill(field, SHOT_KEY)
    frame.press(field, "Enter")
    frame.wait_for_function(
        "(text) => document.querySelector('[data-testid=\"stSidebar\"]').innerText.includes(text)",
        arg=key_gate.LIVE_ON)
    if opened:
        frame.click('[data-testid="stSidebarCollapseButton"]')
    frame.get_by_role("button", name="Run a stand-in call").click()
    alert = '[data-testid="stAlert"] [data-testid="stMarkdownContainer"]'
    frame.wait_for_function(
        "(sel) => [...document.querySelectorAll(sel)].some(e => e.innerText.includes('Code:'))",
        arg=alert)
    page.wait_for_timeout(500)
    shown = [t for t in frame.eval_on_selector_all(alert, "els => els.map(e => e.innerText)")
             if "Code:" in t]
    text = frame.evaluate("() => document.body.innerText")
    colours = frame.evaluate("""(sel) => {
      const p = [...document.querySelectorAll(sel)].find(e => e.innerText.includes('Code:'));
      let box = p;
      while (box && getComputedStyle(box).backgroundColor === 'rgba(0, 0, 0, 0)') box = box.parentElement;
      return {text: getComputedStyle(p).color, background: box ? getComputedStyle(box).backgroundColor : null};
    }""", alert)
    shot = out / f"gallery_model_not_found_{width}_stand-in-data.png"
    _shoot(page, frame, shot)
    context.close()
    return {"width": width, "line": shown[0] if shown else None,
            "line_is_the_mapping": bool(shown) and shown[0] == errors.line("model_not_found"),
            "key_on_page": SHOT_KEY in text, "class_name_on_page": "NotFoundError" in text,
            "colours": colours,
            "screenshot": str(shot)}


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/_stcore/health", timeout=2) as reply:
            return reply.status == 200
    except OSError:
        return False


def _wait_healthy(url: str, seconds: int = 60) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _healthy(url):
            return
        time.sleep(0.5)
    raise SystemExit(f"Streamlit did not answer at {url}")


def _stop(server: subprocess.Popen) -> None:
    """Stop the server and every process it started (a venv's python.exe starts a child)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        os.killpg(server.pid, signal.SIGTERM)
    try:
        server.wait(timeout=15)
    except subprocess.TimeoutExpired:
        server.kill()


def _app_frame(page, ready: str, seconds: int = 60):
    """The frame that holds the Streamlit app, once `ready` is on it."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        for frame in page.frames:
            try:
                if frame.query_selector('[data-testid="stApp"]') and frame.query_selector(ready):
                    return frame
            except Exception:  # a frame can go away while it is read
                pass
        page.wait_for_timeout(250)
    raise SystemExit(f"the app never showed {ready!r} at {page.url}")


def _settle(page, frame) -> None:
    frame.wait_for_function("document.fonts.status === 'loaded'")
    page.wait_for_timeout(800)


def _shoot(page, frame, path: Path) -> None:
    frame.evaluate(ADD_LABEL, [LABEL, theme.WARNING_TEXT, theme.WARNING_TINT, theme.SANS])
    width = page.viewport_size["width"]
    height = frame.evaluate(
        "() => { const m = document.querySelector('[data-testid=\"stMain\"]');"
        " return m ? m.scrollHeight : document.documentElement.scrollHeight; }")
    page.set_viewport_size({"width": width, "height": min(max(height, HEIGHTS[width]), 9000)})
    page.wait_for_timeout(600)
    page.screenshot(path=str(path))
    page.set_viewport_size({"width": width, "height": HEIGHTS[width]})


FOCUS_CONTROLS = ("input", "textarea", "combobox", "stBaseButton-primary",
                  "stBaseButton-secondary", "stSidebarNavLink")


def _focus_ring(page, frame) -> list[dict]:
    """Tab through the page from the caption; the ring on the first of each kind of control."""
    frame.click('[data-testid="stMain"] [data-testid="stCaptionContainer"]')
    rings = {}
    for _ in range(40):
        page.keyboard.press("Tab")
        step = frame.evaluate(FOCUS_STEP)
        if step and step["control"] in FOCUS_CONTROLS and step["control"] not in rings:
            rings[step["control"]] = step
        if len(rings) == len(FOCUS_CONTROLS):
            break
    return [rings.get(c, {"control": c, "ring_on": None}) for c in FOCUS_CONTROLS]


def _layout_sequence(browser, url: str) -> list[dict]:
    """The column width on a text page and a table page, moving between them in one session."""
    context = browser.new_context(viewport={"width": 1280, "height": HEIGHTS[1280]})
    page = context.new_page()
    steps = []
    page.goto(f"{url}/")
    frame = _app_frame(page, ".p7-banner")
    _settle(page, frame)
    steps.append(("Home, loaded", frame.evaluate(MEASURE, {})["column_width"]))
    frame.locator('[data-testid="stSidebarNavLink"]', has_text="Kit gallery").click()
    frame = _app_frame(page, GALLERY_READY)
    _settle(page, frame)
    steps.append(("Kit gallery, by the sidebar", frame.evaluate(MEASURE, {})["column_width"]))
    frame.locator('[data-testid="stSidebarNavLink"]', has_text="Home").click()
    frame.wait_for_function("() => !document.querySelector('.p7-table')")
    frame = _app_frame(page, ".p7-banner")
    _settle(page, frame)
    steps.append(("Home again, by the sidebar", frame.evaluate(MEASURE, {})["column_width"]))
    frame.locator('[data-testid="stSidebarNavLink"]', has_text="Ask the plan").click()
    page.wait_for_url(f"{url}/Ask_the_plan")
    frame = _app_frame(page, ".p7-banner")
    _settle(page, frame)
    steps.append(("Ask the plan, by the sidebar", frame.evaluate(MEASURE, {})["column_width"]))
    context.close()
    return [{"step": s, "column_width": w} for s, w in steps]


def run(url: str, out: Path, widths: list[int], channel: str | None,
        model_call: bool) -> dict:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    pages = nav.pages({"P7_GALLERY": "1"})
    results = {"pages": [], "focus": [], "layout": [], "poison": None, "model_call": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=channel, headless=True)
        try:
            for width in widths:
                context = browser.new_context(viewport={"width": width, "height": HEIGHTS[width]})
                for spec in pages:
                    page = context.new_page()
                    page.goto(f"{url}/{spec.url_path or ''}")
                    ready = GALLERY_READY if spec is nav.GALLERY else ".p7-banner"
                    frame = _app_frame(page, ready)
                    _settle(page, frame)
                    row = {"page": spec.title, "width": width,
                           **frame.evaluate(MEASURE, FONT_TARGETS)}
                    if spec is nav.GALLERY:
                        if width == widths[0]:
                            results["poison"] = frame.evaluate(POISON_CHECK, POISON)
                            results["focus"] = _focus_ring(page, frame)
                            page.goto(f"{url}/{spec.url_path}")
                            frame = _app_frame(page, ready)
                            _settle(page, frame)
                    name = (spec.url_path or "Home").lower()
                    shot = out / f"{name}_{width}_stand-in-data.png"
                    _shoot(page, frame, shot)
                    row["screenshot"] = str(shot)
                    results["pages"].append(row)
                    page.close()
                context.close()
            results["layout"] = _layout_sequence(browser, url)
            if model_call:
                results["model_call"] = [_model_call(browser, url, w, out) for w in widths]
        finally:
            browser.close()
    return results


def _rgb(token: str) -> str:
    return "rgb({}, {}, {})".format(*(int(token[i:i + 2], 16) for i in (1, 3, 5)))


def _report(results: dict, widths: list[int]) -> bool:
    ok = True
    print("\nNo sideways scroll (document and main column within the viewport)")
    print(f"{'page':28} {'width':>5} {'document':>9} {'main':>11} {'column':>7} {'padding':>8} sidebar")
    for r in results["pages"]:
        flat = (r["doc_scroll_width"] <= r["doc_client_width"]
                and (r["main_scroll_width"] or 0) <= (r["main_client_width"] or 0))
        ok &= flat
        print(f"{r['page']:28} {r['width']:>5} {r['doc_scroll_width']:>4}/{r['viewport']:<4} "
              f"{r['main_scroll_width']:>5}/{r['main_client_width']:<5} {r['column_width']:>7} "
              f"{r['column_padding']:>8} {'open' if r['sidebar_open'] else 'closed'}"
              f"{'' if flat else '  SIDEWAYS SCROLL'}")

    print("\nComputed font-family on the kit gallery")
    gallery = {r["width"]: r for r in results["pages"] if r["page"] == nav.GALLERY.title}
    print(f"{'element':11} " + " ".join(f"{w:>5} px: font-family".ljust(46) for w in widths))
    for name in FONT_TARGETS:
        cells = []
        for w in widths:
            family = gallery[w]["fonts"][name] or "(missing)"
            want = EXPECTED_FONT.get(name, "Inter")
            good = family.strip("'\"").startswith(want)
            ok &= good
            cells.append((family[:40] + ("" if good else "  WRONG")).ljust(46))
        print(f"{name:11} " + " ".join(cells))
    for w in widths:
        loaded = gallery[w]["loaded"]
        good = "400" in loaded.get("Inter", []) and "600" in loaded.get("Source Serif 4", [])
        ok &= good
        ink = "rgb({}, {}, {})".format(*(int(theme.INK[i:i + 2], 16) for i in (1, 3, 5)))
        good_header = gallery[w]["table_header"] == ink
        ok &= good_header
        print(f"  {w} px: weights loaded {loaded}{'' if good else '  MISSING'}; "
              f"caption colour and opacity {gallery[w]['caption']}; table header "
              f"{gallery[w]['table_header']}{'' if good_header else '  FADED'}")

    print("\nKPI tiles in a row, breaks table and sidebar, on the kit gallery")
    for w in widths:
        g = gallery[w]
        rows = {}
        for tiles in g["tile_rows"]:
            for top, height in tiles:
                rows.setdefault(top, []).append(height)
        equal = all(max(h) - min(h) <= 1 for h in rows.values())
        f, frame_ = g["figures"], g["table_frame"]
        right = f["count"] > 0 and f["right"] == f["count"] == f["tabular"]
        inside = frame_["overflow"] == "auto"      # the document itself is checked above
        current = g["current_page"] == _rgb(theme.INFO_TINT)
        ok &= equal and right and inside and current and bool(g["hash_fits"])
        print(f"  {w:>5} px: tile heights by row {sorted(rows.values())} equal {equal}; "
              f"figure cells {f['count']}, right-aligned {f['right']}, tabular {f['tabular']}; "
              f"table frame {frame_['scroll']}/{frame_['client']} px (scrolls inside "
              f"{frame_['scroll'] > frame_['client']}), overflow "
              f"{frame_['overflow']}, edge {frame_['border']}; current page "
              f"{g['current_page']} (INFO_TINT {current}); hash fits {g['hash_fits']}")

    print(f"\nHanging brackets in the breaks table (Phase 0 verdict, finding 4): the right edge "
          f"of the digits, tolerance {DIGIT_EDGE_TOLERANCE_PX} px")
    for w in widths:
        b = gallery[w]["brackets"]
        gap = abs(b["negative_digits_right"] - b["positive_digits_right"])
        level = gap <= DIGIT_EDGE_TOLERANCE_PX
        ok &= level
        print(f"  {w:>5} px: {b['negative']} digits end at {b['negative_digits_right']:.4f}, "
              f"{b['positive']} at {b['positive_digits_right']:.4f}; difference {gap:.4f} px"
              f"{'' if level else '  NOT LEVEL'}")
        print(f"          closing bracket {b['bracket_px']:.4f} px wide, hangs "
              f"{b['bracket_hang_px']:.4f} px past the digits; "
              f"{b['bracket_px'] / b['font_size_px']:.4f} em of {b['font']} at "
              f"{b['font_size_px']:g} px; padding-right negative {b['padding_right']['negative']}, "
              f"non-negative {b['padding_right']['positive']}")

    print(f"\nFigure column headers (Phase 1 stop 1 verdict, finding 1): the right edge of the "
          f"header text against the digits below it, tolerance {DIGIT_EDGE_TOLERANCE_PX} px")
    for w in widths:
        for h in gallery[w]["brackets"]["headers"]:
            for d in h["digits"]:
                gap = h["header_right"] - d["right"]
                level = abs(gap) <= DIGIT_EDGE_TOLERANCE_PX
                ok &= level
                print(f"  {w:>5} px: header {h['header']!r} ends at {h['header_right']:.4f}, "
                      f"the digits of {d['text']} at {d['right']:.4f}; header minus digits "
                      f"{gap:+.4f} px (padding-right {h['padding_right']})"
                      f"{'' if level else '  NOT LEVEL'}")

    print("\nTabular figures only where figures are (Phase 0 verdict, finding 5): computed "
          "font-variant-numeric")
    for w in widths:
        n = gallery[w]["numerals"]
        ok &= n == EXPECTED_NUMERALS
        print(f"  {w:>5} px: " + "; ".join(
            f"{kind} {n[kind]}{'' if n[kind] == want else ' (WRONG, want ' + want + ')'}"
            for kind, want in EXPECTED_NUMERALS.items()))

    print("\nBanner h1 on every page")
    every_h1 = True
    for r in results["pages"]:
        good = (r["fonts"]["h1"] or "").strip("'\"").startswith("Source Serif 4")
        every_h1 &= good
        if not good:
            print(f"  {r['page']} at {r['width']}: {r['fonts']['h1']}  WRONG")
    ok &= every_h1
    if every_h1:
        print("  every page: Source Serif 4")

    print("\nReading width at 1280 px, one session")
    for step in results["layout"]:
        print(f"  {step['step']:32} column {step['column_width']} px")

    print("\nKeyboard focus ring on the gallery's controls (1280 px)")
    for ring in results["focus"]:
        print(f"  {ring}")
        ok &= ring.get("ring_on") is not None

    print(f"\nPoisoned name: {results['poison']}")
    ok &= bool(results["poison"] and results["poison"]["shown_as_text"]
               and results["poison"]["injected_scripts"] == 0)

    calls = results["model_call"]
    if calls:
        received = results["api_received"]
        print("\nA failed model call on screen (real SDK, local stand-in API, stand-in key)")
        for c in calls:
            good = (c["line_is_the_mapping"] and not c["key_on_page"]
                    and not c["class_name_on_page"]
                    and c["colours"] == {"text": _rgb(theme.WARNING_TEXT),
                                         "background": _rgb(theme.WARNING_TINT)})
            ok &= good
            print(f"  {c['width']:>5} px: {c['line']!r}; key on the page {c['key_on_page']}; "
                  f"class name on the page {c['class_name_on_page']}; colours {c['colours']}"
                  f"{'' if good else '  WRONG'}")
        sent_ok = (len(received) == len(calls)
                   and all(r["key"] == SHOT_KEY and not r["sampling"] for r in received))
        ok &= sent_ok
        print(f"  the stand-in API received {len(received)} calls, each with the typed key and "
              f"no sampling parameter: {sent_ok}")
    else:
        print("\nA failed model call on screen: skipped (--url given)")

    print("\nScreenshots:")
    for r in results["pages"] + calls:
        print(f"  {r['screenshot']}")
    return ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="folder for screenshots and results")
    parser.add_argument("--url", help="a running app; without it, Streamlit is started here")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--api-port", type=int, default=8771, help="the stand-in API, loopback")
    parser.add_argument("--widths", default="1280,768,390")
    parser.add_argument("--channel", default="msedge", help='browser channel, or "chromium"')
    parser.add_argument("--playwright-path", help="a folder to put on sys.path for Playwright")
    args = parser.parse_args(argv)
    if args.playwright_path:
        sys.path.insert(0, args.playwright_path)
    widths = [int(w) for w in args.widths.split(",")]
    channel = None if args.channel == "chromium" else args.channel

    server, log, api = None, None, None
    url = args.url
    if not url:
        url = f"http://localhost:{args.port}"
        if _healthy(url):
            raise SystemExit(f"Something already answers at {url}: stop it, or pass --url to "
                             "measure it on purpose.")
        _StandInAPI.received = []
        api = ThreadingHTTPServer(("127.0.0.1", args.api_port), _StandInAPI)
        threading.Thread(target=api.serve_forever, daemon=True).start()
        args.out.mkdir(parents=True, exist_ok=True)
        log = open(args.out / "streamlit.log", "w", encoding="utf-8")
        server = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "streamlit_app/Home.py",
             "--server.headless", "true", "--server.port", str(args.port),
             "--server.address", "127.0.0.1"],
            cwd=ROOT, env=_child_env(args.api_port), stdout=log, stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt")
    try:
        _wait_healthy(url)
        results = run(url, args.out, widths, channel, model_call=api is not None)
        results["api_received"] = list(_StandInAPI.received)
    finally:
        if server is not None:
            _stop(server)
            log.close()
            print(f"Streamlit stopped; {url} answers: {_healthy(url)}")
        if api is not None:
            api.shutdown()
            api.server_close()
            print("The stand-in API is stopped.")
    (args.out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = _report(results, widths)
    print("\nAll checks passed." if ok else "\nSome checks FAILED.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
