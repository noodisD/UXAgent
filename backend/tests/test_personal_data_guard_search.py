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
    {"name": "site-search", "aria_label": "Szukaj telefonu"},
    {"role": "searchbox", "id": "mobile_phone"},
    {"name": "q"},
    {"id": "wyszukiwarka-mobile"},
    {"name": "szukaj"},
    {"type": "search", "placeholder": "Wpisz hasło"},
])
def test_a_search_or_query_box_skips_the_wording_check(attrs):
    assert personal_field_reason(attrs) is None


@pytest.mark.parametrize("attrs", [
    {"type": "text", "name": "email", "class": "form-control search-input"},
    {"type": "text", "name": "password", "class": "search-box"},
    {"class": "header__search-input", "aria_label": "Szukaj telefonu"},
    {"id": "mobile_phone", "class": "search-field"},
])
def test_a_search_class_alone_does_not_make_a_field_a_search_box(attrs):
    """`form-control search-input` styles ordinary text fields too."""
    assert personal_field_reason(attrs)


@pytest.mark.parametrize("attrs", [
    {"name": "q", "label": "First name"},
    {"type": "search", "name": "login", "placeholder": "E-mail"},
    {"role": "search", "aria_label": "Imię i nazwisko"},
    {"name": "query", "placeholder": "Nazwisko"},
    {"id": "siteSearch", "placeholder": "Numer karty"},
])
def test_a_search_box_whose_wording_asks_for_an_identity_is_still_refused(attrs):
    """What a visitor reads in the box is read for identity words."""
    assert personal_field_reason(attrs)


@pytest.mark.parametrize("attrs", [
    {"type": "text", "name": "email", "id": "query"},
    {"type": "text", "name": "first_name", "id": "search"},
    {"type": "text", "name": "imie", "id": "q"},
    {"type": "text", "name": "nazwisko", "id": "q"},
    {"type": "search", "name": "email"},
    {"type": "text", "name": "q", "id": "billing_email"},
])
def test_an_identity_word_in_a_search_boxs_identifier_is_still_refused(attrs):
    """One search-like identifier does not excuse the other one saying email."""
    assert personal_field_reason(attrs)


@pytest.mark.parametrize("attrs", [
    {"id": "mobile_menu"},
    {"name": "mobile-nav-toggle"},
    {"class": "mobile-only"},
    {"placeholder": "Wersja mobile"},
    {"id": "header_mobile_menu"},
    {"name": "immobile"},
])
def test_mobile_inside_a_layout_name_is_not_a_phone(attrs):
    assert personal_field_reason(attrs) is None


@pytest.mark.parametrize("attrs", [
    {"type": "text", "name": "mobile"},
    {"type": "text", "id": "mobile", "placeholder": "Enter"},
    {"type": "text", "placeholder": "Mobile"},
    {"type": "text", "placeholder": "Mobile *"},
    {"type": "text", "label": "Mobile:"},
    {"type": "text", "aria_label": "mobile"},
    {"type": "text", "name": "billing_mobile"},
    {"type": "text", "name": "billing[mobile]"},
    {"type": "text", "name": "userMobile"},
    {"type": "text", "name": "mobile_2"},
])
def test_a_field_that_is_just_mobile_is_a_phone_field(attrs):
    """In an English form a bare "mobile" is the phone field. It is a layout word
    only inside a longer name (mobile_menu) or phrase ("Wersja mobile")."""
    assert personal_field_reason(attrs)


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
    {"placeholder": "Hasło"},
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
