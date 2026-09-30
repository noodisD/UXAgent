"""Defects found by verifying the 2026-09-27 hyundai live run, each of which put
something false or garbled in front of a client (see the commit for the run)."""
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from simulated_web_agent.agent.gpt import _parse_json_response
from simulated_web_agent.main import experiment, pagespeed, viewport_scan

SRC = Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent"


# --- perceive replies from 3.5 Flash-Lite -----------------------------------

def test_a_fenced_reply_parses_cleanly():
    reply = '```json\n{"observations": ["Skip to gnb menu", "Hyundai"]}\n```'
    assert json.loads(_parse_json_response(reply)) == {
        "observations": ["Skip to gnb menu", "Hyundai"]}


def test_an_escaped_closing_quote_does_not_leave_the_fence_in_the_page_text():
    """Two steps of the run were stored ending in '"]}```'."""
    reply = ('```{"observations": ["Menu", "Otwórz chat asystenta Hyundai\\" with an icon.\\"]}```')
    parsed = json.loads(_parse_json_response(reply))
    last = parsed["observations"][-1]
    assert "```" not in last and not last.endswith(']}"'), last
    assert last.endswith("with an icon.")


def test_ordinary_replies_are_untouched():
    reply = '{"observations": ["a \\"quoted\\" word", "b"]}'
    assert json.loads(_parse_json_response(reply))["observations"][0] == 'a "quoted" word'


# --- survey summary and PageSpeed -------------------------------------------

def test_the_survey_summary_may_not_claim_what_was_completed():
    text = (SRC / "main" / "reporter.py").read_text(encoding="utf-8")
    assert "Do NOT say whether the task was" in text


def test_pagespeed_waits_long_enough_for_a_mobile_run(monkeypatch):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(pagespeed.urllib.request, "urlopen", fake_urlopen)
    pagespeed._http_get("https://example.test/")
    assert seen["timeout"] >= 90, "Lighthouse sends nothing until the run ends"


# --- measurements in a real browser -----------------------------------------

async def _evaluate(js, html, viewport=(1024, 768)):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    try:
        page = await browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        await page.set_content(html)
        return await page.evaluate(js)
    finally:
        await browser.close()
        await pw.stop()


async def test_font_sizes_come_from_text_not_from_empty_icon_spans():
    html = ('<html><body><p style="font-size:16px">Treść strony</p>'
            '<span class="icon" style="font-size:1px;display:inline-block;width:10px;height:10px"></span>'
            '<i style="font-size:1px;display:block;width:10px;height:10px"><span></span></i>'
            '<small style="font-size:11px">drobny druk</small><div style="font-size:11px">stopka</div>'
            "</body></html>")
    sizes = await _evaluate(experiment._FONT_SIZE_JS, html)
    assert 1 not in sizes and 16 in sizes and 11 in sizes


async def test_text_over_a_photo_is_undetermined_not_a_contrast_failure():
    html = ('<html><body style="margin:0"><div style="position:relative;width:400px;height:200px">'
            '<img alt="" src="data:," style="position:absolute;inset:0;width:100%;height:100%;background:#333">'
            '<h2 style="position:relative;color:#fff;margin:0">Hero na zdjęciu</h2></div>'
            '<p style="color:#eee;background:#fff">Jasny szary na białym</p></body></html>')
    res = await _evaluate(viewport_scan._CONTRAST_JS, html)
    assert res["undetermined"] >= 1, "white on white canvas is our blind spot"
    assert res["below_threshold"] == 1, res  # the real low-contrast paragraph is still caught
    assert res["examples"][0]["text"].startswith("Jasny szary")


async def test_a_clipped_skip_link_is_not_a_tap_target():
    html = ('<html><head><style>.sr-navigator{position:absolute;width:100%;padding:4px;'
            "box-sizing:border-box;clip:rect(0,0,0,0);overflow:hidden}</style></head><body>"
            '<div><a class="sr-navigator" href="#main">Przejdź do treści</a></div>'
            '<div><a href="#x" style="display:inline-block;width:20px;height:20px">x</a></div></body></html>')
    res = await _evaluate(viewport_scan._TAP_TARGET_JS, html, viewport=(390, 844))
    assert res["elements_checked"] == 1, "only the visible link is a target"
    assert not any("Przejdź" in str(e) for e in res["examples"])
