"""Persona abandon threshold (§2.2)."""
from simulated_web_agent.main.abort import abandon_threshold


def test_patience_maps_to_steps():
    assert abandon_threshold({"behavioral_profile": {"patience": "low"}}) == 3
    assert abandon_threshold({"behavioral_profile": {"patience": "medium"}}) == 5
    assert abandon_threshold({"behavioral_profile": {"patience": "high"}}) == 8


def test_explicit_override_wins():
    assert abandon_threshold({"abort_max_confusing_steps": 2,
                              "behavioral_profile": {"patience": "high"}}) == 2


def test_default_when_unknown():
    assert abandon_threshold({}) == 4
    assert abandon_threshold(None) == 4
    assert abandon_threshold({"behavioral_profile": {"patience": "???"}}) == 4
