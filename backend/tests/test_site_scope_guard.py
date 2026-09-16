"""A lidl.pl run in the first external review wandered onto reserved.pl and
audited that instead. Off-site was only ever flagged *after the fact*
(site_scope.flag_off_site, post-session), so the steps were still spent, the
screenshots still taken, and any finding written while off-domain described a
site nobody asked about.

The guard refuses the navigation instead. Two halves, because one cannot cover
the other: an explicit URL is checked before we load it, while a click or a
redirect is only detectable afterwards.
"""
import json
import types

import pytest

from simulated_web_agent.executor import env as env_mod
from simulated_web_agent.executor.env import SCOPE_REFUSAL_PREFIX, WebAgentEnv


class _FakePage:
    def __init__(self, url="https://lidl.pl/"):
        self.url = url
        self.goto_calls = []
        self.back_calls = 0

    async def go_back(self, **kw):
        self.back_calls += 1
        self.url = "https://lidl.pl/"

    async def goto(self, url, **kw):
        self.goto_calls.append(url)
        self.url = url


def _env(page_url="https://lidl.pl/", start="https://lidl.pl/"):
    e = WebAgentEnv.__new__(WebAgentEnv)          # no browser needed
    e.page = _FakePage(page_url)
    e.task_config = {"start_url": start}
    e.logger = types.SimpleNamespace(info=lambda *a, **k: None)
    from simulated_web_agent.main.site_scope import registrable_domain
    e._allowed_domain = registrable_domain(start)
    return e


# --- pre-check: an explicit off-site URL is never loaded -------------------

@pytest.mark.parametrize("url", [
    "https://reserved.pl/damska",
    "reserved.pl/damska",           # scheme-less; resolve_url would add https://
    "https://www.google.com/search?q=lidl",
])
def test_explicit_off_site_navigation_is_refused(url):
    e = _env()
    msg = e._off_site_refusal(url)
    assert msg and msg.startswith(SCOPE_REFUSAL_PREFIX)
    assert "reserved.pl" in msg or "google.com" in msg
    assert e.page.goto_calls == [], "the page must never be loaded"


@pytest.mark.parametrize("url", [
    "https://lidl.pl/oferta",
    "/oferta",                      # site-relative
    "https://www.lidl.pl/oferta",   # www is the same registrable domain
    "https://sklep.lidl.pl/x",      # subdomains belong to the site
])
def test_on_site_navigation_is_allowed(url):
    assert _env()._off_site_refusal(url) is None


def test_no_target_domain_means_no_restriction():
    """Without a start_url there is nothing to be outside of — the guard must
    not block every navigation."""
    e = _env(start="")
    assert e._off_site_refusal("https://anything.example/") is None


# --- post-check: a click or redirect that left the site is undone ----------

@pytest.mark.asyncio
async def test_a_click_that_left_the_site_is_reverted():
    e = _env(page_url="https://reserved.pl/damska")
    msg = await e._return_to_site_if_left()
    assert msg and msg.startswith(SCOPE_REFUSAL_PREFIX)
    assert "reserved.pl" in msg
    assert e.page.back_calls == 1
    assert e.page.url == "https://lidl.pl/"


@pytest.mark.asyncio
async def test_falls_back_to_the_start_url_when_back_does_not_help():
    """A hop that opened a fresh tab has no history to go back to."""
    e = _env(page_url="https://reserved.pl/damska")

    async def _useless_back(**kw):
        e.page.back_calls += 1            # still off-site afterwards

    e.page.go_back = _useless_back
    msg = await e._return_to_site_if_left()
    assert msg and e.page.goto_calls == ["https://lidl.pl/"]


@pytest.mark.asyncio
async def test_staying_on_site_reverts_nothing():
    e = _env(page_url="https://lidl.pl/oferta/x")
    assert await e._return_to_site_if_left() is None
    assert e.page.back_calls == 0


@pytest.mark.asyncio
async def test_about_blank_is_not_a_scope_breach():
    e = _env(page_url="about:blank")
    assert await e._return_to_site_if_left() is None


# --- the refusal must not be mistaken for a harness failure ---------------

def test_refusal_is_not_read_as_an_executor_error():
    """attribution.py suppresses every finding on a step whose observation
    carries an "Error executing action:" — a harness fault. A scope refusal is
    the guard working as designed, so it must not discredit that step."""
    from simulated_web_agent.main.attribution import _EXECUTOR_ERROR_MARKERS

    msg = _env()._off_site_refusal("https://reserved.pl/x").lower()
    assert not any(m in msg for m in _EXECUTOR_ERROR_MARKERS)
