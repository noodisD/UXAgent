"""A step records its evidence even when its action never ran.

58f59ae made every action handler enter ElementHighlight, but a step could
still end before any handler did: a refused input or off-site navigation
returned early, a malformed action raised, and a target in the DOM but hidden
made the scroll-into-view raise before either hook call. Such steps had no
screenshot, scroll position or consent check.
"""
import json

import pytest
from omegaconf import OmegaConf
from playwright.async_api import async_playwright

from simulated_web_agent.executor.env import WebAgentEnv

PAGE = """<!DOCTYPE html><html><body>
<a href="#" parser-semantic-id="shown" onclick="return false">shown</a>
<div style="display:none"><a href="#" parser-semantic-id="hidden">hidden</a></div>
<input parser-semantic-id="email" name="email" type="email">
<div style="height:3000px"></div>
</body></html>"""


@pytest.fixture
async def env():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(PAGE)
    calls = []

    async def hook():
        calls.append(1)

    cfg = OmegaConf.create({"browser": {"launch_options": {"headless": True}, "sleep_after_action": 0}})
    e = WebAgentEnv(cfg, before_action_hook=hook)
    e.page = page
    e._allowed_domain = "example.com"
    e.hook_calls = calls
    e.observation = _fake_observation
    try:
        yield e
    finally:
        await browser.close()
        await pw.stop()


async def _fake_observation():
    return {"html": ""}


@pytest.mark.parametrize("action", [
    {"action": "type", "target": "email", "text": "jan@example.com"},   # personal data refused
    {"action": "goto_url", "url": "https://competitor.pl/"},           # off-site refused
    {"action": "click"},                                                # missing target
    {"action": "wait"},                                                 # unknown action
    {"action": "scroll", "direction": "diagonal", "amount": 1},        # invalid direction
    {"action": "click", "target": "hidden"},                            # hidden target
])
async def test_a_step_that_never_acted_still_records_evidence(env, action):
    await env.step(json.dumps(action))
    assert len(env.hook_calls) == 1


async def test_a_normal_step_records_it_once(env):
    await env.step(json.dumps({"action": "click", "target": "shown"}))
    assert len(env.hook_calls) == 1, "a second recording would overwrite the pre-action frame"
