"""click_semantic_element must recover from sticky-overlay interception.

Observed on example-shop.pl (run 2026-07-09_11-29-52): Playwright's minimal
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
