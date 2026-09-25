"""Every action records its evidence, not only the ones aimed at an element.

The before-action hook takes the step's screenshots, its scroll position and
the consent check, and it runs inside ElementHighlight. scroll(), mouse_click()
and raw_type() never entered ElementHighlight, so none of the 71 scroll steps
archived by 2026-09-24 had a screenshot or a scroll position — and a hyundai
session whose first action was a scroll had no step-0 frame, so its
first-impression pass failed with "screenshot_missing".
"""
import inspect
import re

import pytest
from omegaconf import OmegaConf
from playwright.async_api import async_playwright

from simulated_web_agent.executor.env import WebAgentEnv

PAGE = """
<!DOCTYPE html>
<html><body style="margin:0">
  <input id="q">
  <div style="height:4000px">spacer</div>
</body></html>
"""


def _dispatched_actions():
    return re.findall(r'action_name\s*==\s*"(\w+)"', inspect.getsource(WebAgentEnv.step))


def test_the_sweep_sees_the_whole_dispatcher():
    names = _dispatched_actions()
    assert {"click", "scroll", "mouse_click", "raw_type", "goto_url", "terminate"} <= set(names)


@pytest.mark.parametrize("action", _dispatched_actions())
def test_every_dispatched_action_runs_the_evidence_hook(action):
    src = inspect.getsource(getattr(WebAgentEnv, action))
    assert "ElementHighlight(" in src, f"{action} would leave its step without evidence"


@pytest.fixture
async def env():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 1024, "height": 768})
    await page.set_content(PAGE)
    calls = []

    async def hook():
        calls.append(await page.evaluate("window.scrollY"))

    e = WebAgentEnv(OmegaConf.create({"browser": {"launch_options": {"headless": True}}}),
                    before_action_hook=hook)
    e.page = page
    e.hook_calls = calls
    try:
        yield e
    finally:
        await browser.close()
        await pw.stop()


async def test_scroll_records_the_frame_before_it_moves(env):
    await env.scroll("down", 2)
    assert env.hook_calls == [0], "evidence is the page as the step found it"
    assert await env.page.evaluate("window.scrollY") > 0, "and the scroll still happens"


async def test_mouse_click_records_evidence(env):
    await env.mouse_click(10, 10)
    assert len(env.hook_calls) == 1


async def test_raw_type_records_evidence_and_still_types(env):
    await env.page.focus("#q")
    await env.raw_type("lampka")
    assert len(env.hook_calls) == 1
    assert await env.page.input_value("#q") == "lampka"
