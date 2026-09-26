"""Runtime safety of the executor, on real local pages.

From the runtime review (its verifiers never ran, so each case was reproduced
here before the fix):
- a site whose CSS disables transitions hung every targeted action for good:
  the highlight waited for a transitionend that never fired;
- a targeted key_press always raised (Locator.press takes no force), and the
  one in the archive reached a report as "Enter does not search";
- key_press typed digits into a phone field one by one, past the guard that
  refuses the same text through type;
- goto_url to file:// was not treated as leaving the site;
- a tab the page opened on another domain stayed open and in the trace;
- closing the only tab crashed the session.
"""
import asyncio
import json

import pytest
from omegaconf import OmegaConf
from playwright.async_api import async_playwright

from simulated_web_agent.executor.env import WebAgentEnv

SHOP = "https://www.shop-test.pl/"
COMPETITOR = "https://www.competitor-test.pl/"


@pytest.fixture
async def env():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context()
    pages = {
        SHOP: """<html><body>
            <button parser-semantic-id="btn" onclick="window.__clicked=1">Kup</button>
            <input parser-semantic-id="q" name="q">
            <input parser-semantic-id="tel" name="phone" type="tel">
            <a parser-semantic-id="out" href="https://www.competitor-test.pl/" target="_blank">Promo</a>
            </body></html>""",
        COMPETITOR: "<html><body><h1>Konkurencja</h1></body></html>",
    }

    async def route(r):
        body = pages.get(r.request.url.split("?")[0])
        await (r.fulfill(body=body, content_type="text/html") if body else r.abort())

    await context.route("**/*", route)
    page = await context.new_page()
    await page.goto(SHOP)
    cfg = OmegaConf.create({"browser": {"launch_options": {"headless": True}, "sleep_after_action": 0}})
    e = WebAgentEnv(cfg)
    e.browser, e.context, e.page = browser, context, page
    e._allowed_domain = "shop-test.pl"

    async def obs():
        return {"html": await e.page.content(), "tabs": [{"url": p.url} for p in e.context.pages]}

    e.observation = obs
    try:
        yield e
    finally:
        await browser.close()
        await pw.stop()


async def _step(env, action, timeout=20):
    return await asyncio.wait_for(env.step(json.dumps(action)), timeout)


async def test_a_site_that_disables_transitions_does_not_hang_actions(env):
    await env.page.add_style_tag(content="*{transition:none !important}")
    await _step(env, {"action": "click", "target": "btn"})
    assert await env.page.evaluate("window.__clicked") == 1


async def test_a_targeted_key_press_works(env):
    obs = await _step(env, {"action": "key_press", "key": "Enter", "target": "q"})
    assert not obs.get("error")


async def test_key_press_cannot_spell_a_phone_number(env):
    await env.page.focus("input[name=phone]")
    obs = await _step(env, {"action": "key_press", "key": "6"})
    assert (obs.get("error") or "").startswith("Input blocked")
    assert await env.page.input_value("input[name=phone]") == ""


async def test_key_press_of_a_named_key_is_still_allowed(env):
    await env.page.focus("input[name=phone]")
    obs = await _step(env, {"action": "key_press", "key": "Tab"})
    assert not obs.get("error")


async def test_a_local_file_is_off_site(env, tmp_path):
    secret = tmp_path / "canary.html"
    secret.write_text("<h1>SECRET-CANARY-42</h1>", encoding="utf-8")
    obs = await _step(env, {"action": "goto_url", "url": secret.as_uri()})
    assert obs.get("error")
    assert "SECRET-CANARY-42" not in await env.page.content()


async def test_a_tab_the_page_opens_on_another_site_is_closed(env):
    obs = await _step(env, {"action": "click", "target": "out"})
    await asyncio.sleep(1.0)
    assert [p.url for p in env.context.pages] == [SHOP]
    # Reported on this step, or on the next if the tab loaded after it ended.
    if not obs.get("error"):
        obs = await _step(env, {"action": "scroll", "direction": "down", "amount": 1})
    assert "competitor-test.pl" in (obs.get("error") or "")


async def test_closing_the_only_tab_is_refused_not_fatal(env):
    obs = await _step(env, {"action": "close_tab", "tab_id": 0})
    assert obs.get("error")
    obs = await _step(env, {"action": "scroll", "direction": "down", "amount": 1})
    assert env.page and not env.page.is_closed()


def test_com_pl_sites_are_separate_sites():
    from simulated_web_agent.main.site_scope import registrable_domain, same_site
    assert registrable_domain("https://www.euro.com.pl/tv") == "euro.com.pl"
    assert not same_site("https://www.komputronik.com.pl/x", "https://www.euro.com.pl/")
    assert same_site("https://sklep.euro.com.pl/x", "https://www.euro.com.pl/")
    assert registrable_domain("https://signin.dm.pl/x") == "dm.pl"
    assert registrable_domain("https://www.hyundai.com/pl/pl") == "hyundai.com"
