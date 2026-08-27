"""Bootstrap offcanvas handling in the executor.

Observed on diabetyk24.pl (run 2026-07-20_14-50-35): the visible "Szukaj
produktu" box is a DECOY — `<input maxlength="0" data-bs-toggle="offcanvas"
data-bs-target="#offcanvasSearch">`. It cannot hold text; clicking it opens an
offcanvas panel whose real `<input name="search[box][query]">` the user actually
types into. Agents typed into the decoy (nothing landed), retried, and gave up
(5 personas produced 18 "element didn't work" fallbacks, most on search). Once
open, the panel also intercepts clicks until dismissed.

These mirror test_places_suggestion.py: real Playwright, headless, inline
`<script>` standing in for Bootstrap's offcanvas JS (trigger click adds `.show`
+ makes the panel visible; Escape / the dismiss control removes `.show`).
"""
from playwright.async_api import async_playwright

from simulated_web_agent.executor.env import (
    dismiss_open_offcanvas,
    locate_offcanvas_input,
)

# A decoy trigger (maxlength=0 + data-bs-toggle) and the panel it opens, holding
# the real input. The inline script simulates Bootstrap: click the trigger →
# panel gets `.show` and becomes visible; Escape / dismiss button → panel closes.
DECOY_PAGE = """
<!DOCTYPE html>
<html><body style="margin:0">
  <input parser-semantic-id="szukaj_produktu" placeholder="Szukaj produktu"
         maxlength="0" data-bs-toggle="offcanvas" data-bs-target="#offcanvasSearch">
  <div class="offcanvas offcanvas-top" id="offcanvasSearch" style="visibility:hidden">
    <button data-bs-dismiss="offcanvas" aria-label="Close">x</button>
    <input name="search[box][query]" type="text" placeholder="Szukaj produktu">
  </div>
  <script>
    window.__typed = "";
    const panel = document.getElementById('offcanvasSearch');
    panel.querySelector('input[type=text]')
         .addEventListener('input', e => { window.__typed = e.target.value; });
    const open = () => { panel.classList.add('show'); panel.style.visibility = 'visible'; };
    const close = () => { panel.classList.remove('show'); panel.style.visibility = 'hidden'; };
    document.querySelector('[data-bs-toggle=offcanvas]').addEventListener('click', open);
    panel.querySelector('[data-bs-dismiss=offcanvas]').addEventListener('click', close);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  </script>
</body></html>
"""

NORMAL_INPUT_PAGE = """
<!DOCTYPE html>
<html><body><input parser-semantic-id="q" type="text" placeholder="Search"></body></html>
"""

MAXLEN_INPUT_PAGE = """
<!DOCTYPE html>
<html><body>
  <input parser-semantic-id="q" type="text" maxlength="50" placeholder="Search">
</body></html>
"""

OPEN_PANEL_PAGE = """
<!DOCTYPE html>
<html><body style="margin:0">
  <button parser-semantic-id="behind" onclick="window.__clicked=true">buy</button>
  <div class="offcanvas offcanvas-top show" id="p" style="visibility:visible">
    <button data-bs-dismiss="offcanvas" aria-label="Close">x</button>
  </div>
  <script>
    const panel = document.getElementById('p');
    const close = () => { panel.classList.remove('show'); panel.style.visibility='hidden'; };
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
    panel.querySelector('[data-bs-dismiss=offcanvas]').addEventListener('click', close);
  </script>
</body></html>
"""

# An open panel whose Escape handler is deliberately absent, so the Escape path
# fails and the dismiss-button fallback must be used.
OPEN_PANEL_NO_ESCAPE_PAGE = """
<!DOCTYPE html>
<html><body style="margin:0">
  <div class="offcanvas offcanvas-top show" id="p" style="visibility:visible">
    <button data-bs-dismiss="offcanvas" aria-label="Close">x</button>
  </div>
  <script>
    const panel = document.getElementById('p');
    panel.querySelector('[data-bs-dismiss=offcanvas]')
         .addEventListener('click', () => { panel.classList.remove('show'); panel.style.visibility='hidden'; });
  </script>
</body></html>
"""

NO_OFFCANVAS_PAGE = """
<!DOCTYPE html>
<html><body><div>just a page</div></body></html>
"""


async def _with_page(html):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 1024, "height": 768})
    await page.set_content(html)
    return pw, browser, page


# --- locate_offcanvas_input ------------------------------------------------

async def test_decoy_redirects_to_the_real_panel_input():
    pw, browser, page = await _with_page(DECOY_PAGE)
    try:
        trigger = page.locator("[parser-semantic-id='szukaj_produktu']")
        inp = await locate_offcanvas_input(page, trigger, timeout=2000)
        assert inp is not None, "should return the real input inside the panel"
        # It must be the panel input, not the maxlength=0 decoy — typing lands.
        await inp.fill("FreeStyle Libre 2")
        assert await page.evaluate("window.__typed") == "FreeStyle Libre 2"
    finally:
        await browser.close()
        await pw.stop()


async def test_normal_input_is_not_treated_as_a_decoy():
    pw, browser, page = await _with_page(NORMAL_INPUT_PAGE)
    try:
        trigger = page.locator("[parser-semantic-id='q']")
        assert await locate_offcanvas_input(page, trigger, timeout=300) is None
    finally:
        await browser.close()
        await pw.stop()


async def test_ordinary_maxlength_is_not_a_decoy():
    """maxlength='50' is a normal constraint, not the maxlength='0' decoy."""
    pw, browser, page = await _with_page(MAXLEN_INPUT_PAGE)
    try:
        trigger = page.locator("[parser-semantic-id='q']")
        assert await locate_offcanvas_input(page, trigger, timeout=300) is None
    finally:
        await browser.close()
        await pw.stop()


# --- dismiss_open_offcanvas ------------------------------------------------

async def test_dismiss_closes_open_panel_via_escape():
    pw, browser, page = await _with_page(OPEN_PANEL_PAGE)
    try:
        assert await dismiss_open_offcanvas(page) is True
        assert await page.evaluate("!document.querySelector('.offcanvas.show')")
    finally:
        await browser.close()
        await pw.stop()


async def test_dismiss_falls_back_to_the_close_control():
    pw, browser, page = await _with_page(OPEN_PANEL_NO_ESCAPE_PAGE)
    try:
        assert await dismiss_open_offcanvas(page) is True
        assert await page.evaluate("!document.querySelector('.offcanvas.show')")
    finally:
        await browser.close()
        await pw.stop()


async def test_dismiss_returns_false_when_nothing_open():
    pw, browser, page = await _with_page(NO_OFFCANVAS_PAGE)
    try:
        assert await dismiss_open_offcanvas(page) is False
    finally:
        await browser.close()
        await pw.stop()
