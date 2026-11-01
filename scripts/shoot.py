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
- a screenshot of every page at every width, labelled "stand-in data".

It is acceptance evidence, not a CI test: the suite does not need a browser.

    python scripts/shoot.py --out shots
    python scripts/shoot.py --url http://localhost:8770 --out shots   (a server already running)

Without --url it starts Streamlit itself on --port (8770), with P7_GALLERY=1
and an empty ANTHROPIC_API_KEY, and stops it at the end. It needs Playwright
(--playwright-path names a folder that holds it) and a Chromium browser
(--channel, msedge by default; "chromium" for Playwright's own).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib import nav, theme  # noqa: E402

HEIGHTS = {1280: 900, 768: 1024, 390: 844}
LABEL = "Stand-in data"
POISON = "<script>alert(\"x\")</script> & Co's \"Q1\" plan"
FONT_TARGETS = {
    "input": '[data-testid="stTextInput"] input',
    "select": '[data-testid="stSelectbox"] input[role="combobox"]',
    "button": '[data-testid="stBaseButton-primary"]',
    "table cell": '[data-testid="stTable"] td',
    "h1": ".p7-banner h1",
    "h4": '[data-testid="stMarkdownContainer"] h4',
}
EXPECTED_FONT = {"h1": "Source Serif 4", "h4": "Source Serif 4"}

MEASURE = """(targets) => {
  const de = document.documentElement;
  const main = document.querySelector('[data-testid="stMain"]');
  const block = document.querySelector('[data-testid="stMainBlockContainer"]');
  const sidebar = document.querySelector('[data-testid="stSidebar"]');
  const caption = document.querySelector('[data-testid="stCaptionContainer"]');
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
    table_header: (() => { const th = document.querySelector('[data-testid="stTable"] th p')
                             || document.querySelector('[data-testid="stTable"] th');
                           return th ? getComputedStyle(th).color : null; })(),
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


def _child_env(port: int) -> dict:
    keep = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env.update({"ANTHROPIC_API_KEY": "", "P7_GALLERY": "1", "PYTHONIOENCODING": "utf-8"})
    return env


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
    frame.click('[data-testid="stCaptionContainer"]')
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
    frame = _app_frame(page, '[data-testid="stTable"]')
    _settle(page, frame)
    steps.append(("Kit gallery, by the sidebar", frame.evaluate(MEASURE, {})["column_width"]))
    frame.locator('[data-testid="stSidebarNavLink"]', has_text="Home").click()
    frame.wait_for_function("() => !document.querySelector('[data-testid=\"stTable\"]')")
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


def run(url: str, out: Path, widths: list[int], channel: str | None) -> dict:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    pages = nav.pages({"P7_GALLERY": "1"})
    results = {"pages": [], "focus": [], "layout": [], "poison": None}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=channel, headless=True)
        try:
            for width in widths:
                context = browser.new_context(viewport={"width": width, "height": HEIGHTS[width]})
                for spec in pages:
                    page = context.new_page()
                    page.goto(f"{url}/{spec.url_path or ''}")
                    ready = '[data-testid="stTable"]' if spec is nav.GALLERY else ".p7-banner"
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
        finally:
            browser.close()
    return results


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

    print("\nBanner h1 on every page")
    for r in results["pages"]:
        good = (r["fonts"]["h1"] or "").strip("'\"").startswith("Source Serif 4")
        ok &= good
        if not good:
            print(f"  {r['page']} at {r['width']}: {r['fonts']['h1']}  WRONG")
    print("  every page: Source Serif 4" if ok else "")

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
    print("\nScreenshots:")
    for r in results["pages"]:
        print(f"  {r['screenshot']}")
    return ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="folder for screenshots and results")
    parser.add_argument("--url", help="a running app; without it, Streamlit is started here")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--widths", default="1280,768,390")
    parser.add_argument("--channel", default="msedge", help='browser channel, or "chromium"')
    parser.add_argument("--playwright-path", help="a folder to put on sys.path for Playwright")
    args = parser.parse_args(argv)
    if args.playwright_path:
        sys.path.insert(0, args.playwright_path)
    widths = [int(w) for w in args.widths.split(",")]
    channel = None if args.channel == "chromium" else args.channel

    server, log = None, None
    url = args.url
    if not url:
        url = f"http://localhost:{args.port}"
        if _healthy(url):
            raise SystemExit(f"Something already answers at {url}: stop it, or pass --url to "
                             "measure it on purpose.")
        args.out.mkdir(parents=True, exist_ok=True)
        log = open(args.out / "streamlit.log", "w", encoding="utf-8")
        server = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "streamlit_app/Home.py",
             "--server.headless", "true", "--server.port", str(args.port)],
            cwd=ROOT, env=_child_env(args.port), stdout=log, stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt")
    try:
        _wait_healthy(url)
        results = run(url, args.out, widths, channel)
    finally:
        if server is not None:
            _stop(server)
            log.close()
            print(f"Streamlit stopped; {url} answers: {_healthy(url)}")
    (args.out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = _report(results, widths)
    print("\nAll checks passed." if ok else "\nSome checks FAILED.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
