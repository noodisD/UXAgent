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


# "Mandatory Field" sits beside the "Model" label of the test-drive form as
# visually-hidden text. The persona read it as an untranslated English label
# and the report's only serious finding was built on it.
SR_ONLY = """<html><head><style>
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden;
           clip:rect(0,0,0,0); white-space:nowrap; border:0 }
.clip-path { position:absolute; clip-path:inset(50%); width:1px; height:1px; overflow:hidden }
.styled-radio input { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0) }
</style></head><body>
<label>Model <span class="sr-only">Mandatory Field</span></label>
<p>Cena od 89 900 zł <span class="clip-path">cena brutto</span></p>
<button><span class="sr-only">Zamknij</span><i></i></button>
<a class="sr-only" href="#main">Przejdź do treści</a>
<div class="styled-radio"><label><input type="radio" name="c" value="b"><span class="sr-only">Czarny</span> Czarny</label></div>
</body></html>"""


async def test_screen_reader_only_text_is_not_page_text_for_the_persona():
    res = await _parse(SR_ONLY)
    assert "Mandatory Field" not in res["html"]
    assert "cena brutto" not in res["html"]
    assert ">Model" in res["html"].replace("> ", ">") or "Model" in res["html"]
    assert "Cena od 89 900" in res["html"], "visible text stays"


async def test_controls_made_of_screen_reader_text_keep_their_names_and_reach():
    res = await _parse(SR_ONLY)
    ids = res["clickable_elements"]
    assert any(i.startswith("zamknij") for i in ids), ids  # named from its own text
    assert any("przejd" in i for i in ids), "a focusable skip link stays a control"
    assert 'type="radio"' in res["html"], "a hidden radio behind a styled label stays"
