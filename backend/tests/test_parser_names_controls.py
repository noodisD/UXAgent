"""The parser hands the persona every control a person can see, by name.

The first live run on the current code published three false findings built on
controls the parser had removed:
- every colour chip is a button wrapping <picture><img alt="Abyss Black">; with
  <picture> discarded they reached the persona as "button", "button1"…, it
  picked colours blind, and the report said a "0 zł" colour cost 3000 zł;
- the package checkbox is <label role=button aria-label="checkbox Pakiet
  COMFORT"> wrapping an <svg>, inside a clickable card: it got no id, lost its
  icon and was pruned — "no way to add a package";
- a modal's portal sits below the fold of a page the modal scroll-locked, so
  the "below the fold, cannot scroll" rule dropped the whole dialog.
Replayed offline on all 70 recorded pages: anonymous controls 234 -> 21, the
dialog and the checkbox present, output size +15% at the median.
"""
from pathlib import Path

from playwright.async_api import async_playwright

PARSER_JS = (Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent"
             / "executor" / "parser" / "parser.js").read_text(encoding="utf-8")

CHIPS = """<html><body>
<h3>Perłowe</h3>
<ul><li><button tabindex="0"><div><div><picture><source srcset="a.webp">
  <img alt="Abyss Black" src="data:," style="width:40px;height:40px"></picture></div></div></button></li>
<li><div role="button" tabindex="0"><picture><img alt="Atlas White" src="data:," style="width:40px;height:40px"></picture></div></li></ul>
</body></html>"""

PACKAGE = """<html><body><ul>
<li style="cursor:pointer"><h3>Pakiet COMFORT</h3><span>4200,00 zł</span>
  <button title="info"><svg width="4" height="17"></svg></button>
  <label aria-label="checkbox Pakiet COMFORT" role="button" tabindex="0" style="display:inline-block;width:20px;height:20px">
    <svg width="17" height="17"></svg></label></li>
</ul></body></html>"""

MODAL = """<html style="position:fixed;top:0;overflow:hidden;height:100%"><body style="margin:0">
<div style="height:100vh">Wybierz kolor zewnętrzny</div>
<div style="height:3000px"></div>
<div><div style="position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:1050">
  <div role="dialog"><h2>Czy zapisać konfigurację na później?</h2>
  <button>Wróć do konfiguracji</button></div></div></div>
</body></html>"""


async def _parse(html, viewport=(1440, 900)):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    try:
        page = await browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        await page.set_content(html)
        return await page.evaluate(PARSER_JS)
    finally:
        await browser.close()
        await pw.stop()


async def test_image_only_chips_are_named_by_their_image():
    res = await _parse(CHIPS)
    assert "abyss_black" in res["clickable_elements"]
    assert "atlas_white" in res["clickable_elements"]
    assert 'alt="Abyss Black"' in res["html"]


async def test_a_checkbox_inside_a_clickable_card_is_its_own_control():
    res = await _parse(PACKAGE)
    ids = res["clickable_elements"]
    assert any("checkbox_pakiet" in i for i in ids), ids
    assert any(i.endswith("info") for i in ids), "the info button too"
    assert 'aria-label="checkbox Pakiet COMFORT"' in res["html"]


async def test_a_modal_on_a_scroll_locked_page_is_seen():
    res = await _parse(MODAL, viewport=(390, 844))
    assert "Czy zapisać konfigurację na później?" in res["html"]
    assert any("wr_do_konfiguracji" in i or "konfiguracji" in i for i in res["clickable_elements"])


async def test_content_really_below_an_unscrollable_page_is_still_dropped():
    html = """<html style="overflow:hidden;height:100%"><body style="margin:0;height:100%;overflow:hidden">
    <div style="height:100vh">Na górze</div><div style="position:absolute;top:3000px">Poza ekranem</div>
    </body></html>"""
    res = await _parse(html)
    assert "Na górze" in res["html"] and "Poza ekranem" not in res["html"]
