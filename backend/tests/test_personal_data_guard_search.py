"""A site search box is not a personal-data field.

On bnpparibas.pl the search in the mobile menu is
`<input type=search id=query_mobile name=query placeholder="Wpisz czego szukasz...">`.
The guard's wording check read "mobile" in its id as a phone field, refused the
typing as "Input blocked: personal data (id=query_mobile)", and told the agent to
treat reaching the search as the end of the path - a policy firing on a layout
word, and the audit losing the one way into the site's content.
"""
import json

import pytest
from omegaconf import OmegaConf
from playwright.async_api import async_playwright

from simulated_web_agent.executor.env import (
    INPUT_REFUSAL_PREFIX,
    WebAgentEnv,
    personal_field_reason,
)

BNP_SEARCH = {
    "type": "search", "id": "query_mobile", "name": "query",
    "placeholder": "Wpisz czego szukasz...",
}


def test_the_search_box_that_was_refused_on_the_live_site_is_left_alone():
    assert personal_field_reason(BNP_SEARCH) is None


@pytest.mark.parametrize("attrs", [
    {"type": "search", "name": "phone-search"},
    {"role": "search", "id": "mobile_box"},
    {"name": "query_mobile"},
    {"id": "siteSearch", "placeholder": "Telefon lub nazwa"},
    {"class": "header__search-input", "aria_label": "Szukaj telefonu"},
    {"name": "q"},
    {"id": "wyszukiwarka-mobile"},
    {"name": "szukaj"},
])
def test_a_search_or_query_box_skips_the_wording_check(attrs):
    assert personal_field_reason(attrs) is None


@pytest.mark.parametrize("attrs", [
    {"id": "mobile_menu"},
    {"name": "mobile-nav-toggle"},
    {"class": "mobile-only"},
    {"placeholder": "Wersja mobile"},
])
def test_mobile_alone_is_a_layout_word_not_a_phone(attrs):
    assert personal_field_reason(attrs) is None


@pytest.mark.parametrize("attrs", [
    {"name": "mobile_phone"},
    {"name": "mobile_number"},
    {"id": "mobileNumber"},
    {"name": "mobile-no"},
    {"name": "mobile_no_2"},
    {"name": "mobile_nr"},
    {"name": "number_mobile"},
    {"placeholder": "Mobile phone"},
    {"type": "tel"},
    {"type": "tel", "name": "query"},
    {"name": "telefon"},
    {"autocomplete": "email"},
    {"type": "email", "role": "search"},
    {"type": "password", "name": "search"},
    {"autocomplete": "tel", "type": "search"},
    {"name": "firstName"},
    {"name": "pesel"},
])
def test_phone_and_other_personal_fields_are_still_refused(attrs):
    assert personal_field_reason(attrs)


PAGE = """<!DOCTYPE html><html><body>
<input parser-semantic-id="search" type="search" id="query_mobile" name="query"
       placeholder="Wpisz czego szukasz...">
<input parser-semantic-id="phone" name="mobile_phone">
</body></html>"""


@pytest.fixture
async def env():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(PAGE)
    cfg = OmegaConf.create({"browser": {"launch_options": {"headless": True}, "sleep_after_action": 0}})
    e = WebAgentEnv(cfg)
    e.page = page
    e._allowed_domain = "example.com"

    async def observation():
        return {"html": ""}

    e.observation = observation
    try:
        yield e
    finally:
        await browser.close()
        await pw.stop()


async def _type(env, target, text):
    return await env.step(json.dumps({"action": "type", "target": target, "text": text}))


async def test_typing_a_query_into_the_search_box_goes_through(env):
    obs = await _type(env, "search", "kredyt hipoteczny")
    assert not obs.get("error")
    assert await env.page.input_value("#query_mobile") == "kredyt hipoteczny"


async def test_an_email_address_typed_into_the_search_box_is_still_refused(env):
    """The check on the text does not depend on which field it goes into."""
    obs = await _type(env, "search", "jan.kowalski@gmail.com")
    assert obs["error"].startswith(INPUT_REFUSAL_PREFIX)
    assert await env.page.input_value("#query_mobile") == ""


async def test_a_nine_digit_number_typed_into_the_search_box_is_still_refused(env):
    obs = await _type(env, "search", "600 100 200")
    assert obs["error"].startswith(INPUT_REFUSAL_PREFIX)


async def test_a_phone_field_on_the_same_page_is_still_refused(env):
    obs = await _type(env, "phone", "abc")
    assert obs["error"].startswith(INPUT_REFUSAL_PREFIX)
