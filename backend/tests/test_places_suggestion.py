"""select_places_suggestion must drive Google Places Autocomplete.

Observed on pepco.pl's store locator (run 2026-07-09_11-49-36 and its re-run):
the location field is a Google Places input (class "pac-target-input"). Typing
a city — even with Enter — never filtered the store list; the agent retyped
"Warszawa" up to four times, saw no change, emitted the "element didn't work"
fallback, and falsely reported the locator as broken. The list only updates
once a *suggestion* is selected (ArrowDown+Enter fires place_changed).
"""
from playwright.async_api import async_playwright

from simulated_web_agent.executor.env import select_places_suggestion

# Fixture: a minimal stand-in for the Google Places dropdown. Real key handling
# lives on document (as Google's does), so page-level keystrokes must reach it.
PLACES_PAGE = """
<!DOCTYPE html>
<html><body style="margin:0">
  <input class="pac-target-input" parser-semantic-id="loc" placeholder="city">
  <div class="pac-container" style="display:block">
    <div class="pac-item">Warszawa, Poland</div>
    <div class="pac-item">Warszawa Wola, Poland</div>
  </div>
  <script>
    window.__selected = null;
    let hi = -1;
    const items = () => Array.from(document.querySelectorAll('.pac-item'));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { hi = Math.min(hi + 1, items().length - 1); }
      else if (e.key === 'Enter' && hi >= 0) { window.__selected = items()[hi].innerText; }
    });
  </script>
</body></html>
"""

NO_DROPDOWN_PAGE = """
<!DOCTYPE html>
<html><body><input class="pac-target-input" parser-semantic-id="loc"></body></html>
"""

HIDDEN_DROPDOWN_PAGE = """
<!DOCTYPE html>
<html><body>
  <input class="pac-target-input" parser-semantic-id="loc">
  <div class="pac-container" style="display:none"><div class="pac-item">X</div></div>
</body></html>
"""


async def _with_page(html):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 1024, "height": 768})
    await page.set_content(html)
    return pw, browser, page


async def test_selects_first_visible_suggestion():
    pw, browser, page = await _with_page(PLACES_PAGE)
    try:
        selected = await select_places_suggestion(page, timeout=1500)
        assert selected is True
        assert await page.evaluate("window.__selected") == "Warszawa, Poland"
    finally:
        await browser.close()
        await pw.stop()


async def test_returns_false_when_no_dropdown():
    pw, browser, page = await _with_page(NO_DROPDOWN_PAGE)
    try:
        assert await select_places_suggestion(page, timeout=500) is False
    finally:
        await browser.close()
        await pw.stop()


async def test_returns_false_when_dropdown_hidden():
    pw, browser, page = await _with_page(HIDDEN_DROPDOWN_PAGE)
    try:
        assert await select_places_suggestion(page, timeout=500) is False
    finally:
        await browser.close()
        await pw.stop()
