"""A reflection is marked self-reported when the tool failed in that session and
the account describes interactions not working.

In the 2026-09-30 hyundai run two of six reflections carried the warning. Two
more did not, and published "divy blokujące klikanie… naprawa struktury warstw
(z-index)" and "zapętlający się konfigurator, błędy skryptów" as site facts:
echoes of Playwright's "<div> intercepts pointer events" and a retried click,
in words none of the harness vocabulary contained."""
import json

import pytest

from simulated_web_agent.main.redaction import describes_interaction_trouble
from simulated_web_agent.main.reporter import aggregate_statistics
from simulated_web_agent.main.trace_store import TraceStore


@pytest.mark.parametrize("text, expected", [
    ("Nakładające się warstwy interfejsu (divy) blokujące klikanie w przyciski, w tym CTA", True),
    ("Naprawa struktury warstw (z-index) interfejsu, aby zapobiec nakładaniu się elementów", True),
    ("Zapętlający się konfigurator i błędy skryptów przy próbie przejścia do podsumowania.", True),
    ("Nakładki modalne, które uniemożliwiały kliknięcia i cofały stronę do poprzednich kroków.", True),
    ("Niedziałający przycisk umówienia jazdy testowej z poziomu konfiguratora.", True),
    # ordinary remarks about the site are not interaction trouble
    ("Czytelne karty wersji wyposażenia, które ułatwiły szybkie porównanie.", False),
    # the words sit inside ordinary ones, or the remark is about content
    ("Usprawnienie działania filtrów produktów.", False),
    ("Naprawienie działania zakładek w konfiguratorze.", False),
    ("Jedynie działające strzałki nawigacyjne.", False),
    ("Brak informacji o racie uniemożliwia porównanie ofert.", False),
    ("Brak etykiet z nazwami kolorów, co uniemożliwia pewny wybór.", False),
    ("Baner uniemożliwia przejście do podsumowania.", True),
    ("Brak kalkulatora rat w podsumowaniu konfiguracji.", False),
    ("Przyciski w wyszukiwarce dealerów nie posiadają etykiet tekstowych.", False),
    ("Brak automatycznego przeniesienia modelu do formularza jazdy testowej.", False),
    ("", False),
])
def test_the_vocabulary_of_interactions_that_did_not_work(text, expected):
    assert describes_interaction_trouble(text) is expected


BLOCKED = ("Error executing action: Locator.click: Timeout 5000ms exceeded. "
           "<div> subtree intercepts pointer events")


def _session(tmp_path, name, *, tool_error, blockers):
    store = TraceStore(tmp_path / name)
    store.write_action_trace([json.dumps({"action": "click", "target": "a"}),
                              json.dumps({"action": "click", "target": "b"})])
    for error in (None, BLOCKED if tool_error else None):
        store.append_observation({"tabs": [{"url": "https://x.pl/", "is_active": True}], "error": error})
    store.write_objective_reflection({"outcome": "partial", "summary": "Częściowo.",
                                      "blockers": blockers, "wins": [], "priorityFix": "Poprawić."})
    report = {"trace_dir": str(store.trace_dir), "persona": f"Persona: {name}", "first_impression": {},
              "heuristic_findings": [], "accessibility": {"findings": []},
              "survey_result": {"answers": []}}
    return store, report


def _flag(tmp_path, name, **kw):
    store, report = _session(tmp_path, name, **kw)
    (row,) = aggregate_statistics([report], stores=[store])["objective_reflections"]
    return row["self_reported_only"]


def test_flagged_when_the_tool_failed_and_the_account_describes_it(tmp_path):
    assert _flag(tmp_path, "Natalia", tool_error=True,
                 blockers=["Nakładające się warstwy interfejsu (divy) blokujące klikanie w przyciski"]) is True


def test_not_flagged_when_the_session_had_no_tool_failure(tmp_path):
    """The same sentence from a clean session may be a real defect."""
    assert _flag(tmp_path, "Ola", tool_error=False,
                 blockers=["Nakładające się warstwy interfejsu (divy) blokujące klikanie w przyciski"]) is False


def test_not_flagged_when_the_account_is_about_something_else(tmp_path):
    assert _flag(tmp_path, "Jan", tool_error=True,
                 blockers=["Brak kalkulatora rat w podsumowaniu konfiguracji."]) is False


# --- the two prompt rules that go with it ----------------------------------------

def test_the_survey_summary_may_not_state_malfunctions_as_fact():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent" / "main"
           / "reporter.py").read_text(encoding="utf-8")
    assert "Do NOT state as fact that buttons" in src


def test_a_mean_belongs_to_the_question_it_came_from():
    from pathlib import Path
    prompt = (Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent"
              / "audit_prompts" / "aggregate_synthesize.txt").read_text(encoding="utf-8")
    assert "przypisuj WYŁĄCZNIE do pytania lub wymiaru, z którego pochodzi" in prompt


def test_a_session_whose_only_trouble_is_a_consent_banner_is_not_a_tool_failure(tmp_path):
    """Only executor errors, fallbacks and repeats mean the tool failed while it
    acted; a banner left open says the page was covered, not that the account of
    the page is an echo of the tool."""
    store = TraceStore(tmp_path / "Ewa")
    store.write_action_trace([json.dumps({"action": "click", "target": "a"}),
                              json.dumps({"action": "click", "target": "b"})])
    for _ in range(2):
        store.append_observation({"tabs": [{"url": "https://x.pl/", "is_active": True}], "error": None})
    store.write_objective_reflection({"outcome": "partial", "summary": "Częściowo.",
                                      "blockers": ["Nakładające się warstwy interfejsu (divy) blokujące klikanie"],
                                      "wins": [], "priorityFix": "Poprawić."})
    report = {"trace_dir": str(store.trace_dir), "persona": "Persona: Ewa", "first_impression": {},
              "heuristic_findings": [], "accessibility": {"findings": []}, "survey_result": {"answers": []}}
    (row,) = aggregate_statistics([report], stores=[store])["objective_reflections"]
    assert row["self_reported_only"] is False
