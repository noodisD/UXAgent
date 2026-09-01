""""Miejsce zamieszkania" demographic holds a city; the LLM expands it to a
specific street address at generation time.

Full hand-typed addresses in the weighted-choice UI ("ul. Marszałkowska 84/92,
00-514 Warszawa") are unreadable and nobody wants to type one to add an option.
But a bare city is useless for "find the nearest store" — extract_home_location
already needs a real address (see test_home_location.py), so the specificity has
to come from somewhere. Moving it into generation-time inference means the UI
only ever holds a city name; the LLM invents the street+postal code, once, per
persona, the same way it already invents the rest of the backstory.

Backward compatible: a demographic value that already looks like a full address
(contains "ul." or a postal code) is passed through unchanged, so saved wizard
forms and any run using the old full-address defaults keep working.
"""
from unittest.mock import AsyncMock

import pytest

from simulated_web_agent.main.persona import _generate_one


@pytest.mark.asyncio
async def test_city_only_value_is_expanded_by_the_llm():
    demographics_cum = [("Miejsce zamieszkania", [(1.0, "Kraków")])]
    mock = AsyncMock(return_value="Persona: X\nMiejsce zamieszkania: ul. Testowa 1, 30-001 Kraków")
    await _generate_one(demographics_cum, "kup produkt", "", [], chat_fn=mock, rng_seed=1)
    sent = mock.await_args.kwargs["messages"][1]["content"]
    assert "Kraków" in sent
    assert "invent" in sent.lower() or "wymyśl" in sent.lower()
    # the instruction must not just repeat "have the X of Kraków" verbatim —
    # that would tell the model the address IS "Kraków", not that it must
    # invent a street within it.
    assert "have the Miejsce zamieszkania of Kraków" not in sent


@pytest.mark.asyncio
async def test_full_address_value_passes_through_unchanged():
    """Backward compat: existing saved forms and old defaults still work."""
    demographics_cum = [
        ("Miejsce zamieszkania", [(1.0, "ul. Długa 14/3, 31-147 Kraków")]),
    ]
    mock = AsyncMock(return_value="Persona: X")
    await _generate_one(demographics_cum, "kup produkt", "", [], chat_fn=mock, rng_seed=1)
    sent = mock.await_args.kwargs["messages"][1]["content"]
    assert "have the Miejsce zamieszkania of ul. Długa 14/3, 31-147 Kraków" in sent


@pytest.mark.asyncio
async def test_other_dimensions_are_unaffected():
    demographics_cum = [("Age", [(1.0, "34")]), ("Miejsce zamieszkania", [(1.0, "Poznań")])]
    mock = AsyncMock(return_value="Persona: X")
    await _generate_one(demographics_cum, "kup produkt", "", [], chat_fn=mock, rng_seed=1)
    sent = mock.await_args.kwargs["messages"][1]["content"]
    assert "have the Age of 34" in sent


@pytest.mark.asyncio
async def test_no_residence_dimension_still_gets_a_fallback_address_instruction():
    """If the user removed the residence dimension entirely, the agent still
    needs an address for store-locator tasks — fall back to inventing one
    anywhere, as before."""
    demographics_cum = [("Age", [(1.0, "34")])]
    mock = AsyncMock(return_value="Persona: X")
    await _generate_one(demographics_cum, "kup produkt", "", [], chat_fn=mock, rng_seed=1)
    sent = mock.await_args.kwargs["messages"][1]["content"]
    assert "Miejsce zamieszkania" in sent
    assert "SPECIFIC" in sent


@pytest.mark.asyncio
async def test_recognizes_the_dimension_case_insensitively():
    demographics_cum = [("miejsce zamieszkania", [(1.0, "Gdańsk")])]
    mock = AsyncMock(return_value="Persona: X")
    await _generate_one(demographics_cum, "kup produkt", "", [], chat_fn=mock, rng_seed=1)
    sent = mock.await_args.kwargs["messages"][1]["content"]
    assert "Gdańsk" in sent
    assert "have the miejsce zamieszkania of Gdańsk" not in sent
