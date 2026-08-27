"""Persona names must be distinct — concurrent generation shares an empty
'previous' list, so the model repeats names (the 20-persona dm run had 4x 'Ewa').
`_ensure_unique_names` is the deterministic guarantee."""
from simulated_web_agent.main.persona import _ensure_unique_names, _persona_name


def _p(text):
    return {"persona": text, "intent": "x"}


def test_duplicate_names_are_made_unique():
    personas = [
        _p("Persona: Ewa\nTło: Ewa ma 30 lat. Ewa lubi zakupy."),
        _p("Persona: Ewa\nTło: Ewa ma 45 lat."),
        _p("Persona: Ewa\nTło: druga historia."),
        _p("Persona: Piotr\nTło: Piotr pracuje."),
    ]
    _ensure_unique_names(personas)
    names = [_persona_name(p["persona"]) for p in personas]
    assert len(set(n.lower() for n in names)) == 4, names
    assert names[0] == "Ewa"          # first occurrence keeps its name
    assert names[3] == "Piotr"        # a non-colliding name is untouched


def test_rename_rewrites_whole_backstory_for_coherence():
    personas = [_p("Persona: Ewa\nTło: Ewa mieszka w Krakowie."),
                _p("Persona: Ewa\nTło: Ewa pracuje w biurze.")]
    _ensure_unique_names(personas)
    second = personas[1]["persona"]
    # the renamed persona must not still contain the old first name as a word
    assert "Ewa" not in second.split()
    new_name = _persona_name(second)
    assert new_name in second  # backstory now refers to the new name


def test_missing_name_gets_assigned():
    personas = [_p("Tło: osoba bez nagłówka Persona.")]
    _ensure_unique_names(personas)
    assert _persona_name(personas[0]["persona"]) != ""


def test_pool_exhaustion_still_unique():
    personas = [_p("Persona: Ewa\nx")] * 0 + [_p("Persona: Ewa\nx") for _ in range(60)]
    _ensure_unique_names(personas)
    names = [_persona_name(p["persona"]) for p in personas]
    assert len(set(n.lower() for n in names)) == 60  # all distinct even past pool size
