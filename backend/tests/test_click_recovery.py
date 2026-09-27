"""click_semantic_element must recover from sticky-overlay interception.

Observed on pepco.pl (run 2026-07-09_11-29-52): Playwright's minimal
scroll-into-view left a visible sidebar link underneath the site's sticky
header (`sticky top-0 z-9999`), so every click retried "subtree intercepts
pointer events" until timeout and the agent burned its whole session.
The recovery centers the element in the viewport and retries once.
Genuinely hidden elements (closed hover flyouts) must still fail.
"""

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from simulated_web_agent.executor.env import click_semantic_element

STICKY_PAGE = """
<!DOCTYPE html>
<html><body style="margin:0">
  <div style="position:sticky; top:0; height:80px; background:red; z-index:9999">
    sticky header
  </div>
  <div style="height:120px"></div>
  <a href="#" parser-semantic-id="target"
     onclick="window.__clicked = true; return false"
     style="display:block; height:20px">target link</a>
  <div style="height:4000px">spacer</div>
</body></html>
"""

FLYOUT_PAGE = """
<!DOCTYPE html>
<html><body style="margin:0">
  <div style="visibility:hidden">
    <a href="#" parser-semantic-id="flyout_link"
       onclick="window.__clicked = true; return false">hidden flyout link</a>
  </div>
</body></html>
"""


async def _with_page(html):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 1024, "height": 768})
    await page.set_content(html)
    return pw, browser, page


async def test_click_recovers_from_sticky_header_interception():
    pw, browser, page = await _with_page(STICKY_PAGE)
    try:
        # Scroll below the target so the minimal scroll-into-view puts it
        # at the very top of the viewport — under the sticky header.
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

        await click_semantic_element(page, "target", timeout=2000)

        assert await page.evaluate("window.__clicked === true")
    finally:
        await browser.close()
        await pw.stop()


async def test_click_on_hidden_flyout_element_still_fails():
    pw, browser, page = await _with_page(FLYOUT_PAGE)
    try:
        with pytest.raises(PlaywrightTimeoutError):
            await click_semantic_element(page, "flyout_link", timeout=500)
        assert not await page.evaluate("window.__clicked === true")
    finally:
        await browser.close()
        await pw.stop()


async def test_click_on_missing_element_raises_value_error():
    pw, browser, page = await _with_page("<html><body></body></html>")
    try:
        with pytest.raises(ValueError, match="not found"):
            await click_semantic_element(page, "nope")
    finally:
        await browser.close()
        await pw.stop()


# A step bar that keeps its active step centred, smoothly undoing any scroll
# of the strip. Playwright scrolls the target before each attempt, so after
# one step had been clicked the next target kept moving under its neighbours
# (as on the hyundai configurator at 390px, run 2026-09-26_19-54-51_72e8)
# although a person could tap it where it was.
_STEPS = ["Model", "Wersja", "Napęd", "Kolor", "Wnętrze", "Pakiety i opcje", "Podsumowanie"]
STRIP_PAGE = """
<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width"><style>
body { margin:0 }
header { position:fixed; top:0; left:0; right:0; height:56px; z-index:10; background:#fff }
.logo { position:absolute; left:0; top:0; width:90px; height:56px; z-index:12; background:#002c5f }
.strip { position:absolute; left:0; right:0; top:0; height:56px; overflow-x:auto;
         white-space:nowrap; scroll-behavior:smooth }
.strip a { display:inline-block; width:140px; line-height:56px; text-align:center;
           background:#eee; border-right:1px solid #ccc }
</style></head><body>
<header>
  <div class="strip">""" + "".join(
    f'<a href="#" parser-semantic-id="s{i}" '
    f'onclick="window.__clicked=(window.__clicked||[]).concat([{i}]);return false">{name}</a>'
    for i, name in enumerate(_STEPS)) + """</div>
  <a class="logo" href="#" onclick="window.__logo = true; return false"></a>
</header>
<script>
  const strip = document.querySelector('.strip');
  const active = document.querySelector('[parser-semantic-id="s3"]');
  const centre = () => strip.scrollTo({left: active.offsetLeft - (strip.clientWidth - active.offsetWidth) / 2});
  centre();
  let t; strip.addEventListener('scroll', () => { clearTimeout(t); t = setTimeout(centre, 30); });
</script>
</body></html>
"""

COVERED_PAGE = """
<!DOCTYPE html>
<html><body style="margin:0">
  <a href="#" parser-semantic-id="target" onclick="window.__clicked = true; return false"
     style="display:block; height:40px">under the sheet</a>
  <div onclick="window.__sheet = true" style="position:fixed; inset:0; z-index:5; background:rgba(0,0,0,.4)"></div>
</body></html>
"""


async def test_click_lands_on_a_step_bar_that_moves_back_when_scrolled():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    try:
        ctx = await browser.new_context(viewport={"width": 390, "height": 844},
                                        is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        await page.set_content(STRIP_PAGE)
        await page.wait_for_timeout(600)
        await page.locator('[parser-semantic-id="s4"]').click(timeout=1500)
        # The failure being fixed: Playwright alone cannot land this click.
        with pytest.raises(PlaywrightTimeoutError, match="intercepts pointer events"):
            await page.locator('[parser-semantic-id="s2"]').click(timeout=1500)
        await click_semantic_element(page, "s2", timeout=1500)
        assert await page.evaluate("window.__clicked") == [4, 2]
        assert not await page.evaluate("window.__logo === true"), "never the element on top"
    finally:
        await browser.close()
        await pw.stop()


async def test_an_element_covered_everywhere_is_not_clicked_through():
    pw, browser, page = await _with_page(COVERED_PAGE)
    try:
        with pytest.raises(PlaywrightTimeoutError):
            await click_semantic_element(page, "target", timeout=500)
        assert not await page.evaluate("window.__clicked === true")
        assert not await page.evaluate("window.__sheet === true")
    finally:
        await browser.close()
        await pw.stop()
