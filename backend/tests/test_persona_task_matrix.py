"""Persona x task matrix (§6.1) — who did what, and how it went.

One row per persona: the task they were given, what happened, and the effort it
took. It is the section that stops a reader treating "3.2 / 5" as a property of
the site rather than an average over N synthetic people, and it is where a
reader notices that the panel was two 28-year-old men.

Every column is already computed elsewhere; this only arranges them. The point
is the labelling: effectiveness is an LLM's verdict (iso_metrics docstring), so
the matrix must not present it as observed.
"""
from src.simulated_web_agent.main.persona_matrix import build_persona_task_matrix


def _persona(name="Michał", outcome="success", steps=17, backtracks=0,
             seq=6.0, home="ul. Polna 3, 21-400 Łuków"):
    return {
        "name": name,
        "objective": "Kup kurtkę zimową",
        "homeLocation": home,
        "outcome": outcome,
        "steps": steps,
        "iso": {
            "effectiveness": {"task_success": outcome, "goal_state_reached": outcome == "success"},
            "efficiency": {"step_count": steps, "backtracks": backtracks},
            "satisfaction": {"synthetic_seq_1_7": seq, "label": "synthetic_self_report"},
        },
    }


def test_one_row_per_persona():
    rows = build_persona_task_matrix([_persona(name="A"), _persona(name="B")])
    assert [r["persona"] for r in rows] == ["A", "B"]


def test_row_carries_task_outcome_and_effort():
    r = build_persona_task_matrix([_persona()])[0]
    assert r["task"] == "Kup kurtkę zimową"
    assert r["outcome"] == "success"
    assert r["steps"] == 17
    assert r["backtracks"] == 0
    assert r["seq"] == 6.0


def test_outcome_is_labelled_as_declared_not_observed():
    """iso_metrics derives effectiveness from an LLM's verdict on its own run.
    The matrix is where a reader would most naturally read it as fact."""
    r = build_persona_task_matrix([_persona()])[0]
    assert r["outcome_source"] == "declared"


def test_home_location_is_shown():
    """The panel's geography is a scope fact: two personas from one city cannot
    tell you the store locator works nationally."""
    r = build_persona_task_matrix([_persona()])[0]
    assert "Łuków" in r["home_location"]


def test_missing_iso_degrades_to_none_not_zero():
    """A persona whose analysis failed has no metrics. Zeros would read as
    'ran perfectly, took no steps'."""
    p = _persona()
    p["iso"] = None
    r = build_persona_task_matrix([p])[0]
    assert r["backtracks"] is None
    assert r["seq"] is None


def test_empty_panel_is_an_empty_matrix():
    assert build_persona_task_matrix([]) == []


def test_non_dicts_are_skipped():
    assert build_persona_task_matrix([None, "junk", _persona()]) != []
