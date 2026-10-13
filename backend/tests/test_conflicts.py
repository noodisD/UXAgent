"""Contradicting accounts, and a recommendation that blames a route that worked.

Run 4429: Dominik and Kamil wrote that the test-drive form did not carry the
configured model; Anna's form did, after the summary-screen button. The report's
priority #2 asked for that route to be fixed."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from docx import Document

from simulated_web_agent.main.conflicts import (
    annotate_recommendations, clicked_names, conflict_text, find_conflicts, route_caveat,
    validate_conflicts,
)
from simulated_web_agent.main.report_docx import build_docx
from simulated_web_agent.main.reporter_html import generate_html_report
from simulated_web_agent.main.trace_store import TraceStore

KAMIL = {"persona": "Kamil", "clicks": ["Zapisz się na jazdę testową", "Jazda testowa"],
         "blockers": ["Brak zachowania kontekstu w formularzu jazdy testowej - system wybrał model i10"],
         "wins": ["Przejrzyste menu modeli"]}
ANNA = {"persona": "Anna", "clicks": ["Umów się na jazdę testową", "Następny krok"],
        "blockers": ["Intruzywna plansza powitalna na wejściu"],
        "wins": ["Płynne przejście z podsumowania do formularza z zachowaniem danych auta"]}
REFL = [KAMIL, ANNA]


def _raw(**over):
    c = {"topic": "Przenoszenie modelu do formularza jazdy testowej",
         "reported": [{"persona": "Kamil", "phrase": "Brak zachowania kontekstu w formularzu jazdy testowej",
                       "via": ["Zapisz się na jazdę testową"]}],
         "worked": [{"persona": "Anna", "phrase": "Płynne przejście z podsumowania do formularza",
                     "via": ["Umów się na jazdę testową"]}]}
    c.update(over)
    return {"conflicts": [c]}


def test_a_conflict_the_texts_bear_out_is_kept_with_the_routes():
    (c,) = validate_conflicts(_raw(), REFL)
    assert [r["persona"] for r in c["reported"]] == ["Kamil"]
    assert c["reported"][0]["via"] == ["Zapisz się na jazdę testową"]
    assert c["worked"][0]["via"] == ["Umów się na jazdę testową"] and c["routes_differ"] is True
    text = conflict_text(c)
    assert "zgłosili: Kamil (przez „Zapisz się na jazdę testową”)" in text and "Zadziałało u: Anna" in text
    assert "może wynikać z drogi" in text


@pytest.mark.parametrize("mutate", [
    lambda c: c["reported"][0].update(persona="Nikt"),                              # not a participant
    lambda c: c["reported"][0].update(phrase="Czegoś takiego nikt nie napisał"),     # not in their blockers
    lambda c: c["worked"][0].update(phrase="Intruzywna plansza powitalna na wejściu"),  # a blocker, not a win
    lambda c: c.update(reported=[]),                                                # one side only
    lambda c: c["worked"][0].update(persona="Kamil"),                                # same person on both sides
    lambda c: c["worked"][0].update(phrase="krótki"),                                # too short to be a quotation
])
def test_what_the_participants_did_not_write_is_dropped(mutate):
    raw = _raw()
    mutate(raw["conflicts"][0])
    assert validate_conflicts(raw, REFL) == []


def test_a_control_the_participant_never_clicked_is_not_shown():
    raw = _raw()
    raw["conflicts"][0]["worked"][0]["via"] = ["Przycisk którego nie klikał"]
    (c,) = validate_conflicts(raw, REFL)
    assert c["worked"][0]["via"] == [] and c["routes_differ"] is False
    assert "może wynikać z drogi" not in conflict_text(c)
    assert "nie wiadomo, czym" in conflict_text(c)


@pytest.mark.parametrize("raw", [None, "x", [], {"conflicts": "x"}, {"conflicts": [3, None]}, {"conflicts": [{}]}])
def test_garbage_from_the_model_gives_nothing(raw):
    assert validate_conflicts(raw, REFL) == []


def test_find_conflicts_never_raises_on_a_bad_reply():
    chat = AsyncMock(return_value="not json")
    assert asyncio.run(find_conflicts(REFL, chat)) == []
    assert asyncio.run(find_conflicts(REFL[:1], chat)) == []          # one participant: nothing to contrast
    ok = AsyncMock(return_value=json.dumps(_raw()))
    (c,) = asyncio.run(find_conflicts(REFL, ok))      # two passes, the same conflict, kept once
    assert c["topic"].startswith("Przenoszenie modelu")
    sent = json.loads(ok.call_args.args[0][1]["content"])
    assert sent[0]["klikniecia"] == ["Zapisz się na jazdę testową", "Jazda testowa"]


# --- a recommendation about a topic someone saw working ---------------------------------------

def _conflict():
    return validate_conflicts(_raw(), REFL)


def test_a_recommendation_about_the_topic_gets_the_caveat_and_others_do_not():
    narrative = {"priority_recommendations": [
        {"priority": 1, "area": "Ścieżka zakupu", "action": "Skonfigurować formularz jazdy testowej tak, "
         "aby przenosił skonfigurowany model z podsumowania."},
        {"priority": 2, "area": "Nawigacja", "action": "Zastąpić nakładkę powitalną banerem."}],
        "minor_issues": [{"heuristic_id": 5, "impact": "Model w formularzu jazdy testowej jest domyślny.",
                          "recommendation": "Przenosić skonfigurowany model do formularza jazdy testowej."}]}
    annotate_recommendations(narrative, _conflict())
    first, second = narrative["priority_recommendations"]
    assert "u Anna to zadziałało po wejściu przez „Umów się na jazdę testową”" in first["route_caveat"]
    assert "route_caveat" not in second
    assert "route_caveat" in narrative["minor_issues"][0]


def test_without_conflicts_nothing_is_added():
    narrative = {"priority_recommendations": [
        {"area": "Formularz", "action": "Skonfigurować formularz jazdy testowej model"}]}
    annotate_recommendations(narrative, [])
    assert "route_caveat" not in narrative["priority_recommendations"][0]


def test_the_caveat_says_so_when_the_route_is_unknown():
    c = _conflict()[0]
    c["worked"][0]["via"] = []
    assert "u części uczestników to zadziałało" in route_caveat(c)


# --- the document -----------------------------------------------------------------------

def _agg():
    c = _conflict()
    narrative = {"executive_summary": "S.", "overall_ux_score": 3, "critical_issues": [], "serious_issues": [],
                 "minor_issues": [], "priority_recommendations": [
                     {"priority": 1, "area": "Ścieżka zakupu", "action": "Skonfigurować formularz jazdy testowej, "
                      "aby przenosił skonfigurowany model.", "expected_impact": "Mniej pomyłek."}]}
    annotate_recommendations(narrative, c)
    return {"website": "x.pl", "task": "t", "narrative": narrative,
            "statistics": {"participant_count": 2, "route_conflicts": c, "heuristic": {"by_heuristic": []}}}


def test_the_html_shows_the_conflict_and_the_caveat():
    html = generate_html_report(_agg())
    assert "Rozbieżne relacje uczestników" in html
    assert "zgłosili: Kamil (przez „Zapisz się na jazdę testową”)" in html
    assert html.count("Uwaga: u Anna to zadziałało") == 1 and 'class="route-caveat"' in html


def test_the_word_file_says_the_same(tmp_path):
    doc = Document(build_docx(_agg(), tmp_path, tmp_path / "r.docx"))
    text = " | ".join(p.text for p in doc.paragraphs)
    assert "Rozbieżne relacje uczestników" in text and "zgłosili: Kamil" in text
    assert any("Uwaga: u Anna to zadziałało" in cell.text for t in doc.tables for row in t.rows for cell in row.cells)


def test_a_report_without_conflicts_has_neither():
    agg = _agg()
    agg["statistics"]["route_conflicts"] = []
    agg["narrative"]["priority_recommendations"][0].pop("route_caveat")
    html = generate_html_report(agg)
    assert "Rozbieżne relacje" not in html and "Uwaga: u Anna" not in html


# --- the clicks ---------------------------------------------------------------------------------

def test_only_successful_clicks_are_listed_in_order(tmp_path):
    store = TraceStore(tmp_path / "s")
    steps = [("Clicking 'Jazda testowa' in the menu", False), ("Clicking 'Zamknij'", True),
             ("Clicking 'Następny krok'", False)]
    store.write_action_trace([json.dumps({"action": "click", "target": "x", "description": d}) for d, _ in steps])
    store.append_observation({"tabs": [], "error": None})
    for _d, failed in steps:
        store.append_observation({"tabs": [], "error": "Error executing action: Timeout" if failed else None})
    assert clicked_names(store) == ["Jazda testowa", "Następny krok"]


def test_the_aggregate_prompt_gets_the_conflicts():
    prompt = (Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent" / "audit_prompts"
              / "aggregate_synthesize.txt").read_text(encoding="utf-8")
    assert "{route_conflicts}" in prompt and "ROZBIEŻNE RELACJE" in prompt
    assert "ani nie proś o wdrożenie czegoś, co u części uczestników zadziałało" in prompt


def test_one_person_cannot_be_on_both_sides_of_a_conflict():
    both = {"persona": "Ola", "clicks": [],
            "blockers": ["Formularz jazdy testowej nie zapamiętuje modelu"],
            "wins": ["Formularz jazdy testowej zapamiętuje model po wejściu z podsumowania"]}
    raw = _raw(reported=[{"persona": "Ola", "phrase": "Formularz jazdy testowej nie zapamiętuje modelu", "via": []}],
               worked=[{"persona": "Ola", "phrase": "Formularz jazdy testowej zapamiętuje model", "via": []}])
    assert validate_conflicts(raw, [both, KAMIL]) == []


def test_a_real_sentence_that_is_not_about_the_topic_is_dropped():
    """The model attached Dominik's remark about closing a dialog to a conflict about the price bar."""
    dom = {"persona": "Dominik", "clicks": [], "blockers": ["Drobne problemy z zamykaniem modala w konfiguratorze"],
           "wins": []}
    kat = {"persona": "Katarzyna", "clicks": [], "blockers": [],
           "wins": ["Stale widoczny pasek z aktualizowaną ceną podczas konfiguracji"]}
    raw = _raw(topic="Stale widoczny pasek podsumowania ceny",
               reported=[{"persona": "Dominik", "phrase": "Drobne problemy z zamykaniem modala", "via": []}],
               worked=[{"persona": "Katarzyna", "phrase": "Stale widoczny pasek z aktualizowaną ceną", "via": []}])
    assert validate_conflicts(raw, [dom, kat]) == []


def test_a_pass_that_missed_the_conflict_is_covered_by_the_other():
    replies = iter([json.dumps({"conflicts": []}), json.dumps(_raw())])
    chat = AsyncMock(side_effect=lambda *a, **k: next(replies))
    (c,) = asyncio.run(find_conflicts(REFL, chat))
    assert c["reported"][0]["persona"] == "Kamil"


def test_a_pass_that_fails_does_not_lose_the_other():
    replies = iter([RuntimeError("boom"), json.dumps(_raw())])

    async def chat(*a, **k):
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return r
    assert len(asyncio.run(find_conflicts(REFL, chat))) == 1


def test_a_control_unrelated_to_the_topic_is_not_offered_as_the_route():
    raw = _raw()
    raw["conflicts"][0]["worked"][0]["via"] = ["Następny krok"]          # clicked, but not about the form
    (c,) = validate_conflicts(raw, REFL)
    assert c["worked"][0]["via"] == [] and c["routes_differ"] is False
