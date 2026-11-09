"""Google Places autocomplete: choose the suggestion that is the place typed.

select_places_suggestion pressed ArrowDown+Enter, always the FIRST suggestion.
On the live bnpparibas.pl branch finder "30-052 Kraków" is offered only streets
named after Kraków in other towns (Krakowskie Przedmieście in Warszawa,
Krakowiaków in Warszawa, Aleja Krakowska in Sękocin Nowy and in Piastów), and
choosing the first sent the search to Warsaw - while the report could not tell
that the site had offered no Kraków from the tool picking a bad suggestion.
"""
import json
import time

import pytest
from omegaconf import OmegaConf
from playwright.async_api import async_playwright

from simulated_web_agent.executor.env import (
    WebAgentEnv,
    choose_places_suggestion,
    places_advisory,
    select_places_suggestion,
)
from simulated_web_agent.main import experiment

WRONG_PLACES = [
    "Krakowskie Przedmieście Warszawa, Polska",
    "Krakowiaków Warszawa, Polska",
    "Aleja Krakowska Sękocin Nowy, Polska",
    "Aleja Krakowska Piastów, Polska",
]


# --- the choice -------------------------------------------------------------

def test_diacritics_do_not_matter():
    assert choose_places_suggestion("Krakow", ["Warszawa, Polska", "Kraków, Polska"]) == 1
    assert choose_places_suggestion("Łódź", ["Lodz, Polska"]) == 0
    assert choose_places_suggestion("KRAKÓW", ["kraków"]) == 0


def test_no_suggestion_is_the_city_when_only_streets_named_after_it_are_offered():
    assert choose_places_suggestion("30-052 Kraków", WRONG_PLACES) is None


def test_the_city_is_found_among_suggestions_that_merely_start_like_it():
    suggestions = ["Krakowskie Przedmieście, Warszawa", "Kraków, Polska"]
    assert choose_places_suggestion("Kraków", suggestions) == 1


def test_the_first_is_kept_when_it_is_the_place():
    assert choose_places_suggestion(
        "Kraków", ["Kraków", "Krakowskie Przedmieście, Warszawa"]
    ) == 0


def test_a_postcode_alone_does_not_make_a_wrong_town_the_place():
    assert choose_places_suggestion("30-052 Kraków", ["30-052 Warszawa", "30-052 Gdańsk"]) is None


def test_letters_outweigh_digits_and_the_better_overlap_wins():
    suggestions = ["30-052 Warszawa", "Kraków, Polska", "30-052 Kraków, Polska"]
    assert choose_places_suggestion("30-052 Kraków", suggestions) == 2


def test_of_equal_matches_the_shorter_then_the_earlier_wins():
    assert choose_places_suggestion(
        "Kraków", ["Aleja Krakowska Kraków, Polska", "Kraków, Polska"]
    ) == 1
    assert choose_places_suggestion("Kraków", ["Kraków, Polska", "Kraków, Polska"]) == 0


def test_a_filler_word_is_no_extra_word_in_the_tie_break():
    """'Plac' and 'Polska' are in every address; a suggestion that carries them
    has no more words than the place typed."""
    assert choose_places_suggestion(
        "Plac Zbawiciela", ["Plac Zbawiciela, Warszawa", "Zbawiciela, Warszawa"]
    ) == 0
    assert choose_places_suggestion(
        "Kraków", ["Kraków Główny, Kraków", "Kraków, Polska"]
    ) == 1


def test_a_street_prefix_or_the_country_is_not_a_match():
    assert choose_places_suggestion("ul. Długa Kraków", ["ul. Piękna, Warszawa"]) is None
    assert choose_places_suggestion("Warszawa Polska", ["Kraków, Polska"]) is None


def test_a_full_address_is_matched_on_its_street_and_town():
    assert choose_places_suggestion(
        "ul. Długa 14/3, 31-147 Kraków",
        ["Długa, Warszawa", "Długa, Kraków, Polska"],
    ) == 1


def test_a_postcode_only_is_matched_on_the_postcode():
    assert choose_places_suggestion("30-052", ["Kraków 30-052", "00-001 Warszawa"]) == 0
    assert choose_places_suggestion("30-052", ["00-001 Warszawa"]) is None


def test_no_suggestions_is_no_choice():
    assert choose_places_suggestion("Kraków", []) is None
    assert choose_places_suggestion("", []) is None


def test_nothing_typed_leaves_the_first():
    assert choose_places_suggestion("", ["A", "B"]) == 0


def test_the_advisory_names_what_was_typed_and_what_was_offered():
    text = places_advisory("30-052 Kraków", WRONG_PLACES)
    assert "30-052 Kraków" in text
    for suggestion in WRONG_PLACES:
        assert suggestion in text
    assert "first one was chosen" in text


def test_a_long_list_is_cut_and_says_so():
    text = places_advisory("x", [f"Miejsce {i}" for i in range(9)])
    assert "Miejsce 4" in text and "Miejsce 5" not in text
    assert "+4 more" in text


def test_the_advisory_blames_neither_the_site_nor_the_tool_and_is_no_executor_error():
    from simulated_web_agent.main.attribution import _EXECUTOR_ERROR_MARKERS

    for text in (
        places_advisory("Kraków", WRONG_PLACES),
        places_advisory("Nowy Sącz", ["Nowy Targ, Polska"], chosen="Nowy Targ, Polska"),
    ):
        text = text.lower()
        assert not any(marker in text for marker in _EXECUTOR_ERROR_MARKERS)
        assert "defect" not in text and "broken" not in text and "failed" not in text


def test_a_partial_match_is_still_the_best_of_what_is_offered():
    """The chooser answers with the closest suggestion; that it has only part of
    the place is for select_places_suggestion to say."""
    assert choose_places_suggestion("Nowy Sącz", ["Nowy Targ, Polska"]) == 0
    assert choose_places_suggestion("Zielona Góra", ["Góra Kalwaria, Polska"]) == 0


# --- the dropdown ------------------------------------------------------------

def _page(items, *, replace_after_ms=None, replace_on_down=None, replacement=()):
    """A stand-in for the Places dropdown; as Google's, its key handling lives on
    document, the highlighted row carries `pac-item-selected`, ArrowDown past the
    last row leaves the list and the next one starts again, and a new list has no
    highlight. `replacement` swaps the list in later, like a late response: after
    a delay, or while the `replace_on_down`-th ArrowDown is handled."""
    rows = "".join(f'<div class="pac-item">{t}</div>' for t in items)
    later = "".join(f'<div class="pac-item">{t}</div>' for t in replacement)
    swap = ""
    if replace_after_ms:
        swap = f"setTimeout(replace, {replace_after_ms});"
    return f"""<!DOCTYPE html><html><body style="margin:0">
      <input class="pac-target-input" parser-semantic-id="loc">
      <div class="pac-container" style="display:block">{rows}</div>
      <script>
        window.__selected = null; window.__downs = 0; let hi = -1;
        const items = () => Array.from(document.querySelectorAll('.pac-item'))
          .filter((el) => el.getClientRects().length);
        const mark = () => items().forEach((el, i) => el.classList.toggle('pac-item-selected', i === hi));
        const replace = () => {{
          document.querySelector('.pac-container').innerHTML = {json.dumps(later)}; hi = -1;
        }};
        document.addEventListener('keydown', (e) => {{
          if (e.key === 'ArrowDown') {{
            window.__downs++; hi = hi + 1 >= items().length ? -1 : hi + 1; mark();
            if (window.__downs === {json.dumps(replace_on_down)}) replace();
          }} else if (e.key === 'Enter' && hi >= 0) {{ window.__selected = items()[hi].innerText; }}
        }});
        {swap}
      </script></body></html>"""


@pytest.fixture
async def page():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    p = await browser.new_page(viewport={"width": 1024, "height": 768})
    try:
        yield p
    finally:
        await browser.close()
        await pw.stop()


async def test_the_suggestion_that_is_the_place_is_chosen_not_the_first(page):
    await page.set_content(_page(["Krakowska, Warszawa", "Krakowiaków, Warszawa", "Kraków, Polska"]))
    told = []
    assert await select_places_suggestion(page, "Kraków", timeout=1500, advise=told.append) is True
    assert await page.evaluate("window.__selected") == "Kraków, Polska"
    assert told == []


async def test_when_no_suggestion_is_the_place_the_first_is_chosen_and_the_advisor_told(page):
    await page.set_content(_page(["Krakowskie Przedmieście Warszawa", "Krakowiaków Warszawa"]))
    told = []
    assert await select_places_suggestion(
        page, "30-052 Kraków", timeout=1500, advise=told.append, settle=0
    ) is True
    assert await page.evaluate("window.__selected") == "Krakowskie Przedmieście Warszawa"
    assert await page.evaluate("window.__downs") == 1
    assert len(told) == 1
    assert "30-052 Kraków" in told[0]
    assert "Krakowskie Przedmieście Warszawa" in told[0] and "Krakowiaków Warszawa" in told[0]


async def test_a_list_that_is_still_the_answer_to_a_shorter_prefix_is_given_time_to_settle(page):
    await page.set_content(_page(
        ["Krakowska, Warszawa"], replace_after_ms=500,
        replacement=["Krakowska, Warszawa", "Kraków, Polska"],
    ))
    told = []
    assert await select_places_suggestion(
        page, "Kraków", timeout=1500, advise=told.append, settle=3
    ) is True
    assert await page.evaluate("window.__selected") == "Kraków, Polska"
    assert told == []


async def test_a_hidden_dropdown_left_by_an_earlier_input_is_not_counted(page):
    """Google keeps one container per input, the old ones hidden: counting their
    items would shift the ArrowDown count onto the wrong suggestion."""
    await page.set_content(_page(["Krakowska, Warszawa", "Kraków, Polska", "Krakowiaków, Warszawa"]))
    await page.evaluate("""() => {
        const old = document.createElement('div');
        old.className = 'pac-container'; old.style.display = 'none';
        old.innerHTML = '<div class="pac-item">Gdańsk, Polska</div>'
                      + '<div class="pac-item">Poznań, Polska</div>';
        document.body.insertBefore(old, document.querySelector('.pac-container'));
    }""")
    assert await select_places_suggestion(page, "Kraków", timeout=1500, settle=0) is True
    assert await page.evaluate("window.__selected") == "Kraków, Polska"


async def test_a_suggestion_with_only_part_of_the_place_is_chosen_but_said_so(page):
    """Nowy Targ shares "Nowy" with Nowy Sącz and is the wrong town. It is the
    closest on offer, so it is chosen (the first would be no better), and the
    advisory is the only trace that the search may be for another place."""
    await page.set_content(_page(["Nowy Targ, Polska"]))
    told = []
    assert await select_places_suggestion(
        page, "Nowy Sącz", timeout=1500, advise=told.append, settle=0
    ) is True
    assert await page.evaluate("window.__selected") == "Nowy Targ, Polska"
    assert len(told) == 1
    assert "Nowy Sącz" in told[0] and "Nowy Targ, Polska" in told[0]
    assert "only part of what you typed" in told[0]


async def test_a_suggestion_with_the_whole_place_is_not_flagged_and_does_not_wait(page):
    await page.set_content(_page(["Nowy Targ, Polska", "Nowy Sącz, Polska"]))
    told = []
    started = time.monotonic()
    assert await select_places_suggestion(
        page, "Nowy Sącz", timeout=1500, advise=told.append, settle=5
    ) is True
    assert time.monotonic() - started < 2.5
    assert await page.evaluate("window.__selected") == "Nowy Sącz, Polska"
    assert told == []


async def test_a_list_that_matches_only_in_part_is_given_time_to_settle(page):
    """The answer to "Kraków" matches "Kraków Nowa Huta" in part; the list for
    the whole text comes a moment later."""
    await page.set_content(_page(
        ["Kraków, Polska"], replace_after_ms=500,
        replacement=["Kraków, Polska", "Nowa Huta, Kraków, Polska"],
    ))
    told = []
    assert await select_places_suggestion(
        page, "Kraków Nowa Huta", timeout=1500, advise=told.append, settle=3
    ) is True
    assert await page.evaluate("window.__selected") == "Nowa Huta, Kraków, Polska"
    assert told == []


async def test_a_list_replaced_while_the_keys_are_sent_is_chosen_from_again(page):
    """The response to the last keystroke lands between two ArrowDowns: the list
    is new and nothing is highlighted, so the count made for the old one selects
    the wrong row (here the second, Krakowska) unless the choice is made again."""
    await page.set_content(_page(
        ["Krakowska, Warszawa", "Krakowiaków, Warszawa", "Kraków, Małopolskie"],
        replace_on_down=1,
        replacement=["Kraków, Polska", "Krakowska, Warszawa", "Krakowiaków, Warszawa"],
    ))
    told = []
    assert await select_places_suggestion(
        page, "Kraków", timeout=1500, advise=told.append, settle=0
    ) is True
    assert await page.evaluate("window.__selected") == "Kraków, Polska"
    assert told == []


async def test_a_list_that_did_not_change_is_walked_once(page):
    await page.set_content(_page(["Krakowska, Warszawa", "Krakowiaków, Warszawa", "Kraków, Polska"]))
    assert await select_places_suggestion(page, "Kraków", timeout=1500, settle=0) is True
    assert await page.evaluate("window.__downs") == 3
    assert await page.evaluate("window.__selected") == "Kraków, Polska"


# --- how it reaches the persona and the trace --------------------------------

@pytest.fixture
async def env(page):
    cfg = OmegaConf.create({"browser": {"launch_options": {"headless": True}, "sleep_after_action": 0}})
    e = WebAgentEnv(cfg)
    e.page = page
    e._allowed_domain = "example.com"

    async def observation():
        return {"html": ""}

    e.observation = observation
    return e


async def _type(env, text):
    return await env.step(json.dumps({"action": "type", "target": "loc", "text": text}))


async def test_the_step_carries_the_advisory_beside_a_clean_error_channel(env, page):
    await page.set_content(_page(["Krakowskie Przedmieście Warszawa", "Krakowiaków Warszawa"]))
    obs = await _type(env, "30-052 Kraków")
    assert not obs.get("error"), "the action worked: the failure counters must not see it"
    assert "30-052 Kraków" in obs["advisory"] and "Krakowiaków Warszawa" in obs["advisory"]


async def test_the_step_carries_the_advisory_for_a_partial_match_too(env, page):
    await page.set_content(_page(["Nowy Targ, Polska"]))
    obs = await _type(env, "Nowy Sącz")
    assert not obs.get("error")
    assert "Nowy Targ, Polska" in obs["advisory"] and "only part" in obs["advisory"]


async def test_a_step_that_chose_the_place_has_nothing_to_advise(env, page):
    await page.set_content(_page(["Krakowska, Warszawa", "Kraków, Polska"]))
    obs = await _type(env, "Kraków")
    assert not obs.get("error") and "advisory" not in obs
    assert await page.evaluate("window.__selected") == "Kraków, Polska"


async def test_the_advisory_does_not_outlive_its_step(env, page):
    await page.set_content(_page(["Krakowskie Przedmieście Warszawa"]))
    assert (await _type(env, "Kraków"))["advisory"]
    obs = await env.step(json.dumps({"action": "scroll", "direction": "down", "amount": 1}))
    assert "advisory" not in obs


def test_the_loop_turns_it_into_an_advisory_after_the_breaker_has_seen_the_step():
    import inspect

    obs = {"html": "", "error": None, "advisory": "none of the place suggestions..."}
    error = experiment._executor_advisory(obs)
    assert error == f"{experiment.ADVISORY_PREFIX} none of the place suggestions..."
    assert "advisory" not in obs, "it must not be written to the trace a second time"
    assert experiment._executor_advisory({"html": ""}) is None

    src = inspect.getsource(experiment)
    assert src.index("breaker.record(action, obs.get(\"error\")") < src.index("_executor_advisory(obs)")
