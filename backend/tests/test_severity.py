"""NN/g 0–4 severity mapping (§4.4), incl. harness-suspect → 0."""
from simulated_web_agent.main.severity import severity_score


def test_label_mapping():
    assert severity_score({"severity": "krytyczny"}) == 4
    assert severity_score({"severity": "poważny"}) == 3
    assert severity_score({"severity": "drobny"}) == 2


def test_harness_suspect_collapses_to_zero():
    assert severity_score({"severity": "krytyczny", "harness_suspect": True}) == 0
    assert severity_score({"severity": "poważny", "requires_human_check": True}) == 0


def test_unknown_label_is_cosmetic():
    assert severity_score({"severity": "???"}) == 1
    assert severity_score({}) == 1
