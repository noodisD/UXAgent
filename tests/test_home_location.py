"""Persona home_location (§2.1 context).

Without a home location, "sprawdź dostępność w pobliskim sklepie" has no anchor
and the agent invents a city — observed in run 2026-07-09_11-49-36 where the
pepco personas carried only age / online-shopping habit / payment preference,
so Julia typed "Warszawa" and, in the re-run, "Kraków" arbitrarily.
"""
from simulated_web_agent.main.experiment import (
    build_persona_card,
    extract_home_location,
    persona_with_location,
)


def test_explicit_field_wins():
    assert extract_home_location({"home_location": "Gdańsk, Wrzeszcz"}) == "Gdańsk, Wrzeszcz"


def test_reads_location_demographic():
    assert extract_home_location({"Miejsce zamieszkania": "Kraków"}) == "Kraków"
    assert extract_home_location({"Miasto": "Poznań"}) == "Poznań"
    assert extract_home_location({"City": "Warszawa"}) == "Warszawa"


def test_falls_back_to_persona_text():
    persona = "Persona: Julia\nWiek: 24\nMiejsce zamieszkania: Rzeszów\nTło: studentka"
    assert extract_home_location({"persona": persona}) == "Rzeszów"


def test_returns_none_when_unknown_rather_than_guessing():
    # the actual pepco persona shape — no location anywhere
    info = {
        "persona": "Persona: Julia\nTło: studentka sztuki",
        "intent": "kup dekoracje",
        "Wiek": "18–24",
        "Preferencja dotycząca płatności": "Odbiór w sklepie",
    }
    assert extract_home_location(info) is None


def test_reserved_keys_are_not_treated_as_location():
    # site_context mentions cities but must never be used as home location
    info = {"persona": "Persona: X", "site_context": "Sklepy w Warszawie i Krakowie"}
    assert extract_home_location(info) is None


def test_card_carries_home_location():
    card = build_persona_card({"persona": "Persona: Ala", "intent": "x", "Miasto": "Łódź"})
    assert card["home_location"] == "Łódź"


def test_prompt_states_location_and_forbids_inventing_a_city():
    out = persona_with_location("Persona: Julia\nTło: studentka", "ul. Długa 14/3, 31-147 Kraków")
    assert "Persona: Julia" in out                      # original persona preserved
    assert "ul. Długa 14/3, 31-147 Kraków" in out       # full address, not just city
    assert "kod pocztowy" in out                        # told how to use it in a locator
    assert "Nie wymyślaj innego miasta" in out


def test_full_address_demographic_is_extracted_whole():
    info = {"persona": "Persona: X", "Miejsce zamieszkania": "ul. Polna 3, 21-400 Łuków"}
    assert extract_home_location(info) == "ul. Polna 3, 21-400 Łuków"


def test_address_with_postal_code_parsed_from_persona_text():
    persona = "Persona: Ala\nMiejsce zamieszkania: ul. Marszałkowska 84/92, 00-514 Warszawa\nTło: x"
    assert extract_home_location({"persona": persona}) == "ul. Marszałkowska 84/92, 00-514 Warszawa"


def test_prompt_unchanged_when_location_unknown():
    text = "Persona: Julia\nTło: studentka"
    assert persona_with_location(text, None) == text
    assert persona_with_location(text, "   ") == text
