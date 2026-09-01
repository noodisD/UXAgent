"""parser.js must not present hidden elements as clickable, and must mark
elements inside hover-triggered containers so the agent knows a hover may
reveal more (run 2026-07-09_11-29-52: agent clicked list entries it could
not act on and looped for 20 steps).
"""

from pathlib import Path

from playwright.async_api import async_playwright

PARSER_JS = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "simulated_web_agent"
    / "executor"
    / "parser"
    / "parser.js"
).read_text(encoding="utf-8")

PAGE = """
<!DOCTYPE html>
<html><body style="margin:0">
  <a href="/visible" style="display:block">Visible Link</a>

  <div style="visibility:hidden">
    <a href="/vis-hidden">VisHidden Link</a>
  </div>

  <div style="display:none">
    <a href="/disp-none">DispNone Link</a>
  </div>

  <!-- ancestor opacity:0 — the child's own computed opacity stays 1 -->
  <div style="opacity:0">
    <a href="/opacity-hidden">OpacityHidden Link</a>
  </div>

  <!-- small nav item that registers hover listeners (tagged by initscript) -->
  <nav data-maybe-hoverable="true" style="width:200px; height:40px">
    <a href="/menu">Menu Trigger</a>
  </nav>

  <!-- page-sized container tagged hoverable (framework event delegation
       on the app root) must NOT mark its whole subtree as hoverable -->
  <div data-maybe-hoverable="true" style="width:100vw; height:150vh">
    <a href="/inside-root">Root Child Link</a>
  </div>
</body></html>
"""


async def _parse(html):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 1024, "height": 768})
    await page.set_content(html)
    try:
        return await page.evaluate(PARSER_JS)
    finally:
        await browser.close()
        await pw.stop()


async def test_hidden_elements_are_not_clickable():
    result = await _parse(PAGE)
    clickables = result["clickable_elements"]

    assert "visible_link" in clickables
    assert not any("vishidden" in c for c in clickables)
    assert not any("dispnone" in c for c in clickables)
    assert not any("opacityhidden" in c for c in clickables)


async def test_hover_container_children_are_marked_hoverable():
    result = await _parse(PAGE)
    hoverables = result["hoverable_elements"]

    assert "menu_trigger" in hoverables
    # ids in hoverable_elements must be real targets, never null
    assert all(h for h in hoverables)


async def test_page_sized_hover_container_is_ignored():
    result = await _parse(PAGE)
    hoverables = result["hoverable_elements"]

    assert "root_child_link" not in hoverables
