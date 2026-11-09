"""A duplicate persona name is replaced from a pool matched to the persona's
gender. One mixed pool turned a man into 'Anna' while the backstory went on
with 'jako programista' and 'jego cel' (the report then described 'Anna' with
masculine forms)."""
from simulated_web_agent.main.persona import (
    _PL_FEMALE_NAMES,
    _PL_MALE_NAMES,
    _PL_NAME_POOL,
    _ensure_unique_names,
    _persona_gender,
    _persona_name,
)


def _p(text):
    return {"persona": text, "intent": "x"}


def _names(personas):
    return [_persona_name(p["persona"]) for p in personas]


def test_pools_are_big_distinct_and_disjoint():
    for pool in (_PL_FEMALE_NAMES, _PL_MALE_NAMES):
        assert len(pool) >= 25
        assert len({n.lower() for n in pool}) == len(pool)
    assert not {n.lower() for n in _PL_FEMALE_NAMES} & {n.lower() for n in _PL_MALE_NAMES}
    assert sorted(_PL_NAME_POOL) == sorted(_PL_FEMALE_NAMES + _PL_MALE_NAMES)


def test_colliding_man_gets_a_male_name_not_the_next_pool_entry():
    # 'Ewa' is taken, so a mixed pool would hand out 'Anna' next
    personas = [
        _p("Persona: Ewa\nPłeć: kobieta\nTło: Ewa pracuje w banku."),
        _p("Persona: Ewa\nPłeć: mężczyzna\nTło: Ewa jako programista szuka konta. Jego cel to lokata."),
    ]
    _ensure_unique_names(personas)
    first, second = _names(personas)
    assert first == "Ewa"
    assert second in _PL_MALE_NAMES, second
    assert "Ewa" not in personas[1]["persona"].split()
    assert second in personas[1]["persona"].split()  # backstory renamed, not just the header


def test_colliding_woman_gets_a_female_name():
    # 'Anna' is taken, so a mixed pool would hand out 'Piotr' next
    personas = [
        _p("Persona: Anna\nPłeć: kobieta\nTło: Anna jeździ ciężarówką."),
        _p("Persona: Anna\nPłeć: kobieta\nTło: Anna to kasjerka."),
    ]
    _ensure_unique_names(personas)
    assert _names(personas)[1] in _PL_FEMALE_NAMES


def test_stated_gender_wins_over_contrary_cues():
    personas = [
        _p("Persona: Ewa\nx"),
        _p("Persona: Ewa\nPłeć: kobieta\nTło: był, miał, pracował, jego cel, jego praca."),
    ]
    _ensure_unique_names(personas)
    assert _names(personas)[1] in _PL_FEMALE_NAMES


def test_gender_read_from_endings_when_there_is_no_line():
    man = _p("Persona: Ewa\nTło: Ewa był kierowcą, pracował nocami i miał dość. Jego cel to bilet.")
    woman = _p("Persona: Piotr\nTło: Piotr była nauczycielką, miała trudny dzień. Jej cel to bilet.")
    personas = [_p("Persona: Ewa\nx"), man, _p("Persona: Piotr\nx"), woman]
    _ensure_unique_names(personas)
    names = _names(personas)
    assert names[1] in _PL_MALE_NAMES, names
    assert names[3] in _PL_FEMALE_NAMES, names


def test_unknown_gender_gets_any_distinct_name():
    personas = [
        _p("Persona: Ewa\nx"),
        _p("Persona: Ewa\nTło: osoba bez żadnych oznak rodzaju."),
        _p("Persona: Ewa\nPłeć: niebinarna\nTło: y."),
    ]
    _ensure_unique_names(personas)
    names = _names(personas)
    assert len({n.lower() for n in names}) == 3
    assert all(n in _PL_NAME_POOL for n in names)


def test_missing_name_still_takes_the_persona_gender():
    personas = [_p("Płeć: mężczyzna\nTło: osoba bez nagłówka Persona.")]
    _ensure_unique_names(personas)
    assert _persona_name(personas[0]["persona"]) in _PL_MALE_NAMES


def test_pool_exhaustion_stays_distinct_and_does_not_cross_gender():
    personas = [_p("Persona: Jan\nPłeć: mężczyzna\nTło: x.") for _ in range(45)]
    _ensure_unique_names(personas)
    names = _names(personas)
    assert len({n.lower() for n in names}) == 45
    assert not set(names) & set(_PL_FEMALE_NAMES)
    assert all(n in _PL_MALE_NAMES or n.startswith("Jan-") for n in names)


def test_replacement_does_not_take_a_name_a_later_persona_has():
    personas = [
        _p("Persona: Ewa\nx"),
        _p("Persona: Ewa\nPłeć: mężczyzna\nx"),
        _p("Persona: Piotr\nPłeć: mężczyzna\nx"),
    ]
    _ensure_unique_names(personas)
    names = _names(personas)
    assert names[2] == "Piotr"
    assert names[1] in _PL_MALE_NAMES and names[1] != "Piotr"


def test_replacement_stays_whole_word():
    personas = [
        _p("Persona: Ewa\nx"),
        _p("Persona: Ewa\nPłeć: kobieta\nTło: Ewa czyta Ewangelia i lubi Ewa."),
    ]
    _ensure_unique_names(personas)
    second = personas[1]["persona"]
    assert "Ewangelia" in second
    assert "Ewa" not in second.split()


def test_gender_line_variants():
    assert _persona_gender("Persona: A\nPłeć: kobieta") == "f"
    assert _persona_gender("Persona: A\nPłeć: Mężczyzna") == "m"
    assert _persona_gender("Persona: A\n  Płeć:   mężczyzna (38 lat)") == "m"
    assert _persona_gender("Persona: A\nGender: Female") == "f"
    assert _persona_gender("Persona: A\nGender: Male") == "m"
    assert _persona_gender("Persona: A\nPłeć: niebinarna") is None
    # an empty value must not capture the next line
    assert _persona_gender("Persona: A\nPłeć:\nWiek: mężczyzna") is None


def test_gender_cues_do_not_fire_on_nouns_names_or_english():
    # mała/cała/szkoła agree with other nouns; Paweł/Rafał are names, not verbs
    assert _persona_gender("Persona: A\nTło: mała firma, cała rodzina, szkoła, Paweł i Rafał.") is None
    assert _persona_gender("Persona: A\nBackground: she is focusing on research on AI.") is None
    assert _persona_gender("Persona: A\nTło: był i była, jego i jej.") is None  # tie
